#!/usr/bin/env python3
"""Agent Router: classifies an incoming message and dispatches it to the
right agent's inbox, then waits for the reply on its outbox.

This module is channel-agnostic — it takes a plain-text message plus who
sent it and where from, and returns the reply text(s) to send back. Actual
channel I/O (e.g. a Telegram bot reading updates and sending replies) is
the caller's job; this only does classify -> write inbox -> poll outbox.

Usage:
    python router.py dispatch --message "..." --from-user USER --channel telegram
    python router.py heartbeat          Touch this router's heartbeat file.
                                         Call this from your bot's idle loop
                                         so agent-dashboard can see the
                                         router itself as "ok" even between
                                         messages, not just while dispatching.

See README.md for the dispatch table, the inbox/outbox file formats, and
how this fits with the sibling agent-dashboard project (same ~/agents tree).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

AGENTS_HOME = Path(os.environ.get("AGENTS_HOME", Path.home() / "agents"))
UNCLASSIFIED_LOG = AGENTS_HOME / "router" / "unclassified.log"
HEARTBEAT_PATH = AGENTS_HOME / "router" / "heartbeat"

# Order matters only for the "which two agents?" clarifying question below.
DISPATCH_TABLE: dict[str, list[str]] = {
    "email": ["email", "correo", "bandeja", "inbox", "redactar correo", "responder"],
    "company": ["empresa", "productos", "precios internos", "contratos", "política", "equipo"],
    "ecommerce": [
        "shopify", "store", "tienda", "pedidos", "inventario",
        "competencia", "reviews", "reseñas",
    ],
}

# Splits a compound message like "revisa mi inbox y avísame de Shopify" into turns.
SPLIT_PATTERN = re.compile(r"\s+y\s+|,\s*y\s+|\s+and\s+", re.IGNORECASE)

DEFAULT_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 10
NO_AGENT_REPLY = "No tengo un agente para eso todavía. Lo registro."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def classify(text: str) -> list[str]:
    """Return every agent (in DISPATCH_TABLE order) whose keywords appear in text."""
    lowered = text.lower()
    return [
        agent
        for agent, keywords in DISPATCH_TABLE.items()
        if any(kw in lowered for kw in keywords)
    ]


def split_segments(text: str) -> list[str]:
    """Split a compound message on "y"/"and" into candidate single-topic turns."""
    parts = [p.strip() for p in SPLIT_PATTERN.split(text) if p.strip()]
    return parts if len(parts) > 1 else [text]


def touch_heartbeat() -> None:
    """Mark the router itself as alive for agent-dashboard's heartbeat_file check.

    Called on every handled message. A persistent bot loop (Telegram polling,
    etc.) should also call this on its own idle timer so the router still
    reads "ok" between messages, not just while actively dispatching.
    """
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_PATH.touch()


def log_unclassified(text: str, from_user: str, channel: str) -> None:
    UNCLASSIFIED_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": to_iso(now_utc()),
        "from_user": from_user,
        "channel": channel,
        "message": text,
    }
    with UNCLASSIFIED_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_inbox(agent: str, text: str, from_user: str, channel: str) -> str:
    """Write ~/agents/<agent>/inbox/<token>.md; returns the token (also the outbox filename)."""
    ts = now_utc()
    token = ts.strftime("%Y%m%dT%H%M%S%f")
    inbox_dir = AGENTS_HOME / agent / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"from_user: {from_user}\n"
        f"channel: {channel}\n"
        f"timestamp: {to_iso(ts)}\n"
        "---\n\n"
        f"{text}\n"
    )
    (inbox_dir / f"{token}.md").write_text(body, encoding="utf-8")
    return token


def wait_for_outbox(
    agent: str,
    token: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> str | None:
    """Poll ~/agents/<agent>/outbox/<token>.md until it exists or timeout elapses."""
    outbox_path = AGENTS_HOME / agent / "outbox" / f"{token}.md"
    deadline = time.monotonic() + timeout
    while True:
        if outbox_path.exists():
            return outbox_path.read_text(encoding="utf-8")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(poll_interval, remaining))


def handle_turn(
    text: str,
    from_user: str,
    channel: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> str:
    """Classify + dispatch a single (non-compound) message; returns the reply text."""
    agents = classify(text)
    if not agents:
        log_unclassified(text, from_user, channel)
        return NO_AGENT_REPLY
    if len(agents) > 1:
        return f"¿Esto va para {agents[0]} o para {agents[1]}?"

    agent = agents[0]
    token = write_inbox(agent, text, from_user, channel)
    reply = wait_for_outbox(agent, token, timeout=timeout, poll_interval=poll_interval)
    if reply is None:
        return f"El agente {agent} está tardando. Te aviso cuando conteste."
    return reply


def handle_message(
    text: str,
    from_user: str,
    channel: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> list[str]:
    """Entry point. Splits a compound, multi-agent message into sequential turns
    (never broadcasts to more than one agent per turn) and dispatches each in
    order. Returns one reply per turn, in the order to forward to the user.
    """
    touch_heartbeat()
    agents = classify(text)
    if len(agents) > 1:
        segments = split_segments(text)
        if len(segments) > 1:
            return [
                handle_turn(seg, from_user, channel, timeout=timeout, poll_interval=poll_interval)
                for seg in segments
            ]
        return [f"¿Esto va para {agents[0]} o para {agents[1]}?"]
    return [handle_turn(text, from_user, channel, timeout=timeout, poll_interval=poll_interval)]


def cmd_heartbeat(_args: argparse.Namespace) -> None:
    touch_heartbeat()


def cmd_dispatch(args: argparse.Namespace) -> None:
    replies = handle_message(
        args.message,
        args.from_user,
        args.channel,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
    )
    for reply in replies:
        print(reply)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Router dispatcher")
    sub = parser.add_subparsers(dest="command", required=True)

    dispatch_parser = sub.add_parser("dispatch", help="Classify and dispatch one message")
    dispatch_parser.add_argument("--message", required=True)
    dispatch_parser.add_argument("--from-user", required=True)
    dispatch_parser.add_argument("--channel", required=True)
    dispatch_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    dispatch_parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL_SECONDS)
    dispatch_parser.set_defaults(func=cmd_dispatch)

    heartbeat_parser = sub.add_parser("heartbeat", help="Touch the router's heartbeat file")
    heartbeat_parser.set_defaults(func=cmd_heartbeat)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

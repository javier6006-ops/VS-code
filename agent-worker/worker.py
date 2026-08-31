#!/usr/bin/env python3
"""Generic worker runner: polls one agent's inbox, calls a handler function
per message, writes the reply to its outbox, and touches its heartbeat so
agent-dashboard sees it as alive.

This is what sits on the other end of agent-router's inbox/outbox
convention — before this existed, router.py could write to
~/agents/<agent>/inbox but nothing ever answered, so every dispatch timed
out after 5 minutes.

Usage:
    python worker.py run --name email --handler handlers.email:handle
    python worker.py run --name email --handler handlers.email:handle --once

Handler contract: "<module>:<function>" resolves to
    def handle(message: str, metadata: dict) -> str
metadata has from_user / channel / timestamp (all str, from the inbox
file's frontmatter). Return the reply text to send back to the user. Raise
any exception to have it logged to logs/error.log (agent-dashboard reads
this) and a generic apology sent back instead of hanging the router.

See README.md and handlers/email.py for a worked (stub) example.
"""
from __future__ import annotations

import argparse
import importlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path

AGENTS_HOME = Path(os.environ.get("AGENTS_HOME", Path.home() / "agents"))

DEFAULT_POLL_INTERVAL = 2.0
GENERIC_FAILURE_REPLY = "Tuve un problema procesando tu mensaje. Ya quedó registrado el error."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_handler(spec: str):
    module_name, func_name = spec.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def parse_inbox_message(path: Path) -> tuple[str, dict]:
    """Parse the "---\\nkey: val\\n---\\n\\n<body>" format agent-router writes."""
    raw = path.read_text(encoding="utf-8")
    metadata: dict[str, str] = {}
    body = raw
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end != -1:
            frontmatter = raw[4:end]
            body = raw[end + 5 :]
            for line in frontmatter.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    metadata[key.strip()] = value.strip()
    return body.strip(), metadata


def touch_heartbeat(name: str) -> None:
    path = AGENTS_HOME / name / "heartbeat"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def log_error(name: str, message: str) -> None:
    log_path = AGENTS_HOME / name / "logs" / "error.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{to_iso(now_utc())} {message}\n")


def write_outbox(name: str, token: str, reply: str) -> None:
    outbox_dir = AGENTS_HOME / name / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / f"{token}.md").write_text(reply, encoding="utf-8")


def process_one(name: str, path: Path, handler) -> None:
    token = path.stem
    body, metadata = parse_inbox_message(path)
    try:
        reply = handler(body, metadata)
    except Exception as exc:  # noqa: BLE001 - a bad handler must not crash the worker loop
        log_error(name, f"handler failed on {token}: {exc!r}")
        reply = GENERIC_FAILURE_REPLY
    write_outbox(name, token, reply)

    processed_dir = AGENTS_HOME / name / "inbox" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    path.rename(processed_dir / path.name)


def run_once(name: str, handler) -> int:
    """Process every pending inbox message once. Returns how many were processed."""
    inbox_dir = AGENTS_HOME / name / "inbox"
    processed = 0
    if inbox_dir.exists():
        pending = sorted(p for p in inbox_dir.glob("*.md") if p.is_file())
        for path in pending:
            process_one(name, path, handler)
            processed += 1
    touch_heartbeat(name)
    return processed


def run_loop(name: str, handler, poll_interval: float) -> None:
    while True:
        run_once(name, handler)
        time.sleep(poll_interval)


def cmd_run(args: argparse.Namespace) -> None:
    handler = resolve_handler(args.handler)
    if args.once:
        run_once(args.name, handler)
    else:
        run_loop(args.name, handler, args.poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generic agent worker runner")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Poll an agent's inbox and process messages")
    run_parser.add_argument("--name", required=True, help="Agent name (matches ~/agents/<name>)")
    run_parser.add_argument(
        "--handler", required=True, help="module:function, e.g. handlers.email:handle"
    )
    run_parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    run_parser.add_argument(
        "--once", action="store_true", help="Process pending messages once and exit"
    )
    run_parser.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

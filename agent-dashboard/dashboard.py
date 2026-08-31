#!/usr/bin/env python3
"""Agent Dashboard: health-checks registered agents and reports their status.

Usage:
    python dashboard.py check          Run one health-check pass, print the
                                        status JSON to stdout, and exit.
    python dashboard.py loop           Run health-checks forever, once per
                                        hour, writing last-status.json and
                                        appending to alerts.log on failures.
    python dashboard.py loop --once    Run a single loop iteration (writes
                                        the files) and exit. Useful for cron.
    python dashboard.py serve          Serve a live web viewer that updates
                                        the moment an agent's status changes.

See README.md for the registry.json and per-agent log-file formats.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENTS_HOME = Path(os.environ.get("AGENTS_HOME", Path.home() / "agents"))
REGISTRY_PATH = AGENTS_HOME / "registry.json"
DASHBOARD_DIR = AGENTS_HOME / "dashboard"
STATE_PATH = DASHBOARD_DIR / "state.json"
LAST_STATUS_PATH = DASHBOARD_DIR / "last-status.json"
ALERTS_LOG_PATH = DASHBOARD_DIR / "alerts.log"

DOWN_AFTER = timedelta(minutes=5)
SLOW_AFTER_SECONDS = 30.0
ERROR_WINDOW = timedelta(hours=24)
DEFAULT_PING_TIMEOUT = 5.0
CHECK_INTERVAL_SECONDS = 3600


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        v = value.strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def load_registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return []
    with REGISTRY_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("agents", [])


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def ping_agent(agent: dict) -> tuple[bool, float | None]:
    """Attempt to reach the agent. Returns (reachable, latency_seconds)."""
    url = agent.get("ping_url")
    if not url:
        return False, None
    timeout = float(agent.get("timeout_seconds", DEFAULT_PING_TIMEOUT))
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            ok = 200 <= resp.status < 300
            return ok, time.monotonic() - started
    except (urllib.error.URLError, TimeoutError, OSError):
        return False, None


def read_last_error(name: str) -> tuple[str | None, datetime | None]:
    """Return (last_error_line, last_error_timestamp) for an agent's error.log."""
    log_path = AGENTS_HOME / name / "logs" / "error.log"
    if not log_path.exists():
        return None, None
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f if line.strip()]
    except OSError:
        return None, None
    if not lines:
        return None, None
    last_line = lines[-1]
    ts_token = last_line.split(" ", 1)[0]
    ts = parse_iso(ts_token)
    return last_line, ts


def compute_status(name: str, agent: dict, state: dict) -> dict:
    agent_state = state.setdefault(name, {})
    reachable, latency = ping_agent(agent)

    check_time = now_utc()
    if reachable:
        agent_state["last_ping"] = to_iso(check_time)
        agent_state["last_latency_seconds"] = latency
    last_ping_dt = parse_iso(agent_state.get("last_ping", ""))

    last_error_line, last_error_ts = read_last_error(name)
    error_unresolved = last_error_line is not None and (
        last_error_ts is None or check_time - last_error_ts <= ERROR_WINDOW
    )

    if last_ping_dt is None or check_time - last_ping_dt > DOWN_AFTER:
        status = "caido"
    elif latency is not None and latency > SLOW_AFTER_SECONDS:
        status = "lento"
    elif error_unresolved:
        status = "ok-con-error"
    else:
        status = "ok"

    return {
        "name": name,
        "status": {
            "caido": "caído",
            "lento": "lento",
            "ok-con-error": "ok-con-error",
            "ok": "ok",
        }[status],
        "last_ping": agent_state.get("last_ping"),
        "last_error": last_error_line if error_unresolved else None,
    }


def check_all() -> dict:
    state = load_state()
    registry = load_registry()
    results = [compute_status(agent["name"], agent, state) for agent in registry]
    save_state(state)
    return {
        "checked_at": to_iso(now_utc()),
        "agents": results,
    }


def append_alerts(report: dict) -> None:
    down_agents = [a for a in report["agents"] if a["status"] == "caído"]
    if not down_agents:
        return
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    with ALERTS_LOG_PATH.open("a", encoding="utf-8") as f:
        for agent in down_agents:
            f.write(
                f"{report['checked_at']} caído: {agent['name']} "
                f"(last_ping={agent['last_ping']})\n"
            )


def write_last_status(report: dict) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    with LAST_STATUS_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


def run_check_and_persist() -> dict:
    report = check_all()
    write_last_status(report)
    append_alerts(report)
    return report


def cmd_check(_args: argparse.Namespace) -> None:
    print(json.dumps(check_all(), indent=2, ensure_ascii=False))


def cmd_loop(args: argparse.Namespace) -> None:
    while True:
        run_check_and_persist()
        if args.once:
            return
        time.sleep(CHECK_INTERVAL_SECONDS)


def cmd_serve(args: argparse.Namespace) -> None:
    import server  # local import: avoids a circular import at module load time

    server.serve(port=args.port, interval=args.interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Dashboard health checker")
    sub = parser.add_subparsers(dest="command", required=True)

    check_parser = sub.add_parser("check", help="Run one check and print JSON")
    check_parser.set_defaults(func=cmd_check)

    loop_parser = sub.add_parser("loop", help="Run hourly checks forever")
    loop_parser.add_argument(
        "--once", action="store_true", help="Run a single iteration and exit"
    )
    loop_parser.set_defaults(func=cmd_loop)

    serve_parser = sub.add_parser("serve", help="Serve a live web viewer")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Seconds between checks while serving (default: 10)",
    )
    serve_parser.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

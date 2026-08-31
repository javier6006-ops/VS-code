#!/usr/bin/env python3
"""CLI: run an Apify Actor and save its results as a deduplicated leads file.

Usage:
    export APIFY_API_TOKEN=...
    python fetch_leads.py --actor compass~crawler-google-places \\
        --input search_input.json --out leads.csv

See README.md for how to pick an Actor and shape its input for client
acquisition (Apify Store: https://apify.com/store).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from apify_client import ApifyError, run_actor_sync
from leads import dedupe_leads, normalize_leads, save_leads_csv, save_leads_json


def cmd_fetch(args: argparse.Namespace) -> None:
    run_input = json.loads(Path(args.input).read_text(encoding="utf-8"))

    try:
        items = run_actor_sync(args.actor, run_input, timeout=args.timeout)
    except ApifyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    leads = normalize_leads(items)
    if args.dedupe_key:
        leads = dedupe_leads(leads, key=args.dedupe_key)

    out_path = Path(args.out)
    if out_path.suffix == ".json":
        save_leads_json(leads, out_path)
    else:
        save_leads_csv(leads, out_path)

    print(f"{len(leads)} leads -> {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch leads via an Apify Actor")
    parser.add_argument("--actor", required=True, help='Actor slug, e.g. "username~actor-name"')
    parser.add_argument("--input", required=True, help="Path to the Actor's input JSON file")
    parser.add_argument("--out", default="leads.csv", help="Output path (.csv or .json)")
    parser.add_argument(
        "--dedupe-key",
        default="email",
        help='Field to dedupe on, e.g. "email" or "website". Empty string disables dedupe.',
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.set_defaults(func=cmd_fetch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

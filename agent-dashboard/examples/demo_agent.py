#!/usr/bin/env python3
"""Minimal HTTP agent for exercising the dashboard end-to-end.

    python examples/demo_agent.py --port 8001
    python examples/demo_agent.py --port 8002 --fail   # always returns 500
    python examples/demo_agent.py --port 8003 --delay 31  # simulate "lento"
"""
from __future__ import annotations

import argparse
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def make_handler(fail: bool, delay: float):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if delay:
                time.sleep(delay)
            if fail:
                self.send_response(500)
            else:
                self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo agent health endpoint")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--fail", action="store_true", help="Always respond 500")
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds to sleep before responding")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), make_handler(args.fail, args.delay))
    print(f"demo agent listening on http://127.0.0.1:{args.port}/health")
    server.serve_forever()


if __name__ == "__main__":
    main()

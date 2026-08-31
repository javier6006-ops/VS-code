"""Live web viewer for the Agent Dashboard.

Runs a background loop that re-checks every agent on a short interval and
pushes each new result to connected browsers over Server-Sent Events, so a
failing agent shows up in the page the moment it's detected instead of on
the next manual refresh. Independent of `dashboard.py loop`'s hourly cadence
(which is what persists last-status.json / alerts.log for production).
"""
from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import dashboard

STATIC_DIR = Path(__file__).parent / "static"


class Broadcaster:
    """Fan-out of the latest status report to every connected /events client."""

    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.latest: dict | None = None

    def publish(self, report: dict) -> None:
        self.latest = report
        self._emit("status", report)

    def publish_transitions(self, events: list[dict]) -> None:
        for event in events:
            self._emit("transition", event)

    def _emit(self, kind: str, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put((kind, data))

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)


broadcaster = Broadcaster()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._serve_json(broadcaster.latest or {"checked_at": None, "agents": []})
        elif self.path.startswith("/api/history"):
            self._serve_json({"events": dashboard.read_history(limit=50)})
        elif self.path == "/events":
            self._serve_sse()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = broadcaster.subscribe()
        try:
            if broadcaster.latest is not None:
                self._write_event("status", json.dumps(broadcaster.latest, ensure_ascii=False))
            while True:
                try:
                    kind, data = q.get(timeout=15)
                    self._write_event(kind, data)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            broadcaster.unsubscribe(q)

    def _write_event(self, kind: str, data: str) -> None:
        self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


def check_loop(interval: float, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        report = dashboard.run_check_and_persist()
        transitions = report.pop("transitions", [])
        broadcaster.publish(report)
        if transitions:
            broadcaster.publish_transitions(transitions)
        stop_event.wait(interval)


def serve(port: int = 8080, interval: float = 10.0) -> None:
    stop_event = threading.Event()
    checker = threading.Thread(target=check_loop, args=(interval, stop_event), daemon=True)
    checker.start()

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Agent Dashboard viewer: http://localhost:{port}  (checking every {interval:.0f}s)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        httpd.shutdown()

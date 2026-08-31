import importlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "agent-router"))


def load_worker(agents_home: Path):
    import os

    os.environ["AGENTS_HOME"] = str(agents_home)
    if "worker" in sys.modules:
        return importlib.reload(sys.modules["worker"])
    return importlib.import_module("worker")


def load_router(agents_home: Path):
    import os

    os.environ["AGENTS_HOME"] = str(agents_home)
    if "router" in sys.modules:
        return importlib.reload(sys.modules["router"])
    return importlib.import_module("router")


def echo_handler(message: str, metadata: dict) -> str:
    return f"echo: {message} (from {metadata.get('from_user')})"


def broken_handler(message: str, metadata: dict) -> str:
    raise RuntimeError("boom")


class WorkerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.agents_home = Path(self.tmpdir.name)
        self.wk = load_worker(self.agents_home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def write_inbox(self, name: str, token: str, from_user: str, channel: str, body: str):
        inbox = self.agents_home / name / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        content = (
            "---\n"
            f"from_user: {from_user}\n"
            f"channel: {channel}\n"
            "timestamp: 2026-08-31T12:00:00Z\n"
            "---\n\n"
            f"{body}\n"
        )
        (inbox / f"{token}.md").write_text(content, encoding="utf-8")

    def test_parse_inbox_message_splits_frontmatter_and_body(self):
        self.write_inbox("email", "tok1", "ale", "telegram", "responde este correo")
        body, meta = self.wk.parse_inbox_message(self.agents_home / "email" / "inbox" / "tok1.md")
        self.assertEqual(body, "responde este correo")
        self.assertEqual(meta["from_user"], "ale")
        self.assertEqual(meta["channel"], "telegram")

    def test_run_once_writes_outbox_and_archives_inbox(self):
        self.write_inbox("email", "tok1", "ale", "telegram", "hola")
        processed = self.wk.run_once("email", echo_handler)
        self.assertEqual(processed, 1)

        outbox_file = self.agents_home / "email" / "outbox" / "tok1.md"
        self.assertTrue(outbox_file.exists())
        self.assertIn("echo: hola (from ale)", outbox_file.read_text())

        self.assertFalse((self.agents_home / "email" / "inbox" / "tok1.md").exists())
        self.assertTrue((self.agents_home / "email" / "inbox" / "processed" / "tok1.md").exists())

    def test_run_once_touches_heartbeat_even_with_no_messages(self):
        self.wk.run_once("email", echo_handler)
        self.assertTrue((self.agents_home / "email" / "heartbeat").exists())

    def test_run_once_processes_multiple_messages_in_order(self):
        self.write_inbox("email", "20260101T000000000001", "ale", "telegram", "primero")
        self.write_inbox("email", "20260101T000000000002", "ale", "telegram", "segundo")
        processed = self.wk.run_once("email", echo_handler)
        self.assertEqual(processed, 2)
        self.assertIn("primero", (self.agents_home / "email" / "outbox" / "20260101T000000000001.md").read_text())
        self.assertIn("segundo", (self.agents_home / "email" / "outbox" / "20260101T000000000002.md").read_text())

    def test_handler_exception_logs_error_and_still_replies(self):
        self.write_inbox("email", "tok1", "ale", "telegram", "hola")
        self.wk.run_once("email", broken_handler)

        outbox_file = self.agents_home / "email" / "outbox" / "tok1.md"
        self.assertEqual(outbox_file.read_text(), self.wk.GENERIC_FAILURE_REPLY)

        error_log = self.agents_home / "email" / "logs" / "error.log"
        self.assertTrue(error_log.exists())
        self.assertIn("boom", error_log.read_text())

    def test_resolve_handler_imports_email_stub(self):
        handler = self.wk.resolve_handler("handlers.email:handle")
        reply = handler("responde este correo", {"from_user": "ale"})
        self.assertIn("ale", reply)


class RouterWorkerIntegrationTestCase(unittest.TestCase):
    """Confirms router.write_inbox()'s token and worker.run_once()'s outbox
    line up, so a full router -> inbox -> worker -> outbox -> router round
    trip actually works end-to-end (not just each half in isolation)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.agents_home = Path(self.tmpdir.name)
        self.wk = load_worker(self.agents_home)
        self.rt = load_router(self.agents_home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_full_round_trip(self):
        token = self.rt.write_inbox("email", "responde este correo", "ale", "telegram")
        processed = self.wk.run_once("email", echo_handler)
        self.assertEqual(processed, 1)

        reply = self.rt.wait_for_outbox("email", token, timeout=1, poll_interval=0.05)
        self.assertIsNotNone(reply)
        self.assertIn("responde este correo", reply)
        self.assertIn("ale", reply)


if __name__ == "__main__":
    unittest.main()

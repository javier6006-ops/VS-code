import importlib
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_router(agents_home: Path):
    import os

    os.environ["AGENTS_HOME"] = str(agents_home)
    if "router" in sys.modules:
        return importlib.reload(sys.modules["router"])
    return importlib.import_module("router")


class RouterTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.agents_home = Path(self.tmpdir.name)
        self.rt = load_router(self.agents_home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_touch_heartbeat_creates_file(self):
        self.assertFalse(self.rt.HEARTBEAT_PATH.exists())
        self.rt.touch_heartbeat()
        self.assertTrue(self.rt.HEARTBEAT_PATH.exists())

    def test_handle_message_touches_heartbeat_even_when_unclassified(self):
        self.rt.handle_message("cuál es la capital de Francia", "ale", "telegram")
        self.assertTrue(self.rt.HEARTBEAT_PATH.exists())

    def test_classify_single_agent(self):
        self.assertEqual(self.rt.classify("revisa mi bandeja de email"), ["email"])
        self.assertEqual(self.rt.classify("¿cuáles son los precios internos?"), ["company"])
        self.assertEqual(self.rt.classify("hay pedidos atrasados en Shopify"), ["ecommerce"])

    def test_classify_no_match(self):
        self.assertEqual(self.rt.classify("cuál es la capital de Francia"), [])

    def test_unclassified_message_is_logged_and_replied(self):
        reply = self.rt.handle_turn("cuál es la capital de Francia", "ale", "telegram")
        self.assertEqual(reply, self.rt.NO_AGENT_REPLY)
        entries = self.rt.UNCLASSIFIED_LOG.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(entries), 1)
        logged = json.loads(entries[0])
        self.assertEqual(logged["from_user"], "ale")
        self.assertEqual(logged["channel"], "telegram")

    def test_ambiguous_message_asks_for_clarification(self):
        # matches both "company" (empresa) and "ecommerce" (inventario), no "y" to split on.
        reply = self.rt.handle_turn("dime el inventario de la empresa", "ale", "telegram")
        self.assertIn("¿Esto va para", reply)
        self.assertIn("company", reply)
        self.assertIn("ecommerce", reply)

    def test_dispatch_writes_inbox_with_metadata(self):
        token = self.rt.write_inbox("email", "responde este correo", "ale", "telegram")
        inbox_path = self.agents_home / "email" / "inbox" / f"{token}.md"
        content = inbox_path.read_text(encoding="utf-8")
        self.assertIn("from_user: ale", content)
        self.assertIn("channel: telegram", content)
        self.assertIn("responde este correo", content)

    def test_dispatch_returns_agent_reply(self):
        # Prime a background writer that will answer whatever token gets generated
        # by racing on the outbox directory instead of a known token.
        def worker():
            outbox = self.agents_home / "email" / "outbox"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                inbox = self.agents_home / "email" / "inbox"
                if inbox.exists():
                    files = list(inbox.glob("*.md"))
                    if files:
                        token = files[0].stem
                        outbox.mkdir(parents=True, exist_ok=True)
                        (outbox / f"{token}.md").write_text("ya respondí el correo", encoding="utf-8")
                        return
                time.sleep(0.05)

        threading.Thread(target=worker, daemon=True).start()
        reply = self.rt.handle_turn(
            "responde este correo", "ale", "telegram", timeout=5, poll_interval=0.05,
        )
        self.assertEqual(reply, "ya respondí el correo")

    def test_dispatch_times_out_when_agent_never_answers(self):
        reply = self.rt.handle_turn(
            "responde este correo", "ale", "telegram", timeout=0.2, poll_interval=0.05
        )
        self.assertIn("está tardando", reply)
        self.assertIn("email", reply)

    def test_compound_message_splits_into_two_sequential_turns(self):
        seen_order = []

        def worker():
            deadline = time.monotonic() + 5
            handled = set()
            while time.monotonic() < deadline and len(handled) < 2:
                for agent in ("email", "ecommerce"):
                    inbox = self.agents_home / agent / "inbox"
                    if not inbox.exists():
                        continue
                    for f in inbox.glob("*.md"):
                        if f.stem in handled:
                            continue
                        seen_order.append(agent)
                        outbox = self.agents_home / agent / "outbox"
                        outbox.mkdir(parents=True, exist_ok=True)
                        (outbox / f"{f.stem}.md").write_text(f"ok de {agent}", encoding="utf-8")
                        handled.add(f.stem)
                time.sleep(0.05)

        threading.Thread(target=worker, daemon=True).start()
        replies = self.rt.handle_message(
            "revisa mi inbox y avísame de Shopify", "ale", "telegram",
            timeout=5, poll_interval=0.05,
        )
        self.assertEqual(replies, ["ok de email", "ok de ecommerce"])
        self.assertEqual(seen_order, ["email", "ecommerce"])


if __name__ == "__main__":
    unittest.main()

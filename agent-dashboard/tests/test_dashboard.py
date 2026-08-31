import importlib
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_dashboard(agents_home: Path):
    """(Re)import dashboard.py with AGENTS_HOME pointed at a fresh tempdir."""
    import os

    os.environ["AGENTS_HOME"] = str(agents_home)
    if "dashboard" in sys.modules:
        return importlib.reload(sys.modules["dashboard"])
    return importlib.import_module("dashboard")


class DashboardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.agents_home = Path(self.tmpdir.name)
        self.dash = load_dashboard(self.agents_home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def write_registry(self, agents):
        self.agents_home.mkdir(parents=True, exist_ok=True)
        with (self.agents_home / "registry.json").open("w") as f:
            json.dump({"agents": agents}, f)

    def write_error_log(self, name, line):
        log_dir = self.agents_home / name / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "error.log").open("w") as f:
            f.write(line + "\n")

    def test_ok_when_reachable_fast_and_no_error(self):
        self.write_registry([{"name": "router", "ping_url": "http://x/health"}])
        with patch.object(self.dash, "ping_agent", return_value=(True, 0.05)):
            report = self.dash.check_all()
        agent = report["agents"][0]
        self.assertEqual(agent["status"], "ok")
        self.assertIsNone(agent["last_error"])
        self.assertIsNotNone(agent["last_ping"])

    def test_caido_when_never_pinged_successfully(self):
        self.write_registry([{"name": "router", "ping_url": "http://x/health"}])
        with patch.object(self.dash, "ping_agent", return_value=(False, None)):
            report = self.dash.check_all()
        agent = report["agents"][0]
        self.assertEqual(agent["status"], "caído")
        self.assertIsNone(agent["last_ping"])

    def test_caido_when_last_success_older_than_five_minutes(self):
        self.write_registry([{"name": "router", "ping_url": "http://x/health"}])
        stale = self.dash.to_iso(self.dash.now_utc() - timedelta(minutes=10))
        state = {"router": {"last_ping": stale}}
        self.dash.save_state(state)
        with patch.object(self.dash, "ping_agent", return_value=(False, None)):
            report = self.dash.check_all()
        agent = report["agents"][0]
        self.assertEqual(agent["status"], "caído")
        self.assertEqual(agent["last_ping"], stale)

    def test_lento_when_latency_over_threshold(self):
        self.write_registry([{"name": "router", "ping_url": "http://x/health"}])
        with patch.object(self.dash, "ping_agent", return_value=(True, 31.0)):
            report = self.dash.check_all()
        agent = report["agents"][0]
        self.assertEqual(agent["status"], "lento")

    def test_ok_con_error_when_recent_unresolved_error(self):
        self.write_registry([{"name": "email", "ping_url": "http://x/health"}])
        recent = self.dash.to_iso(self.dash.now_utc() - timedelta(hours=1))
        self.write_error_log("email", f"{recent} SMTP timeout")
        with patch.object(self.dash, "ping_agent", return_value=(True, 0.05)):
            report = self.dash.check_all()
        agent = report["agents"][0]
        self.assertEqual(agent["status"], "ok-con-error")
        self.assertIn("SMTP timeout", agent["last_error"])

    def test_ok_when_error_older_than_24h(self):
        self.write_registry([{"name": "email", "ping_url": "http://x/health"}])
        old = self.dash.to_iso(self.dash.now_utc() - timedelta(hours=48))
        self.write_error_log("email", f"{old} SMTP timeout")
        with patch.object(self.dash, "ping_agent", return_value=(True, 0.05)):
            report = self.dash.check_all()
        agent = report["agents"][0]
        self.assertEqual(agent["status"], "ok")
        self.assertIsNone(agent["last_error"])

    def test_check_all_shape_and_alerts_written_on_caido(self):
        self.write_registry(
            [
                {"name": "router", "ping_url": "http://x/health"},
                {"name": "email", "ping_url": "http://y/health"},
            ]
        )
        with patch.object(
            self.dash,
            "ping_agent",
            side_effect=[(True, 0.05), (False, None)],
        ):
            report = self.dash.run_check_and_persist()

        self.assertIn("checked_at", report)
        self.assertEqual(len(report["agents"]), 2)
        self.assertTrue(self.dash.LAST_STATUS_PATH.exists())
        self.assertTrue(self.dash.ALERTS_LOG_PATH.exists())
        alerts = self.dash.ALERTS_LOG_PATH.read_text()
        self.assertIn("caído: email", alerts)
        self.assertNotIn("caído: router", alerts)

    def test_history_records_transition_once_then_stays_quiet(self):
        self.write_registry([{"name": "router", "ping_url": "http://x/health"}])

        with patch.object(self.dash, "ping_agent", return_value=(True, 0.05)):
            self.dash.check_all()  # first sighting: null -> ok
            self.dash.check_all()  # unchanged: no new event

        events = self.dash.read_history(limit=10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "router")
        self.assertIsNone(events[0]["from"])
        self.assertEqual(events[0]["to"], "ok")

    def test_history_records_ok_to_caido_transition(self):
        self.write_registry([{"name": "router", "ping_url": "http://x/health"}])

        with patch.object(self.dash, "ping_agent", return_value=(True, 0.05)):
            self.dash.check_all()
        stale = self.dash.to_iso(self.dash.now_utc() - timedelta(minutes=10))
        state = self.dash.load_state()
        state["router"]["last_ping"] = stale
        self.dash.save_state(state)
        with patch.object(self.dash, "ping_agent", return_value=(False, None)):
            self.dash.check_all()

        events = self.dash.read_history(limit=10)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["from"], "ok")
        self.assertEqual(events[0]["to"], "caído")

    def test_run_check_and_persist_exposes_transitions_but_not_in_last_status_file(self):
        self.write_registry([{"name": "router", "ping_url": "http://x/health"}])
        with patch.object(self.dash, "ping_agent", return_value=(True, 0.05)):
            report = self.dash.run_check_and_persist()

        self.assertEqual(len(report["transitions"]), 1)
        persisted = json.loads(self.dash.LAST_STATUS_PATH.read_text())
        self.assertNotIn("transitions", persisted)

    def test_heartbeat_file_fresh_is_reachable(self):
        heartbeat = self.agents_home / "email" / "heartbeat"
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.touch()

        reachable, latency = self.dash.ping_agent({"heartbeat_file": str(heartbeat)})
        self.assertTrue(reachable)
        self.assertIsNone(latency)

    def test_heartbeat_file_stale_is_not_reachable(self):
        import os

        heartbeat = self.agents_home / "email" / "heartbeat"
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.touch()
        stale_time = time.time() - timedelta(minutes=10).total_seconds()
        os.utime(heartbeat, (stale_time, stale_time))

        reachable, _ = self.dash.ping_agent({"heartbeat_file": str(heartbeat)})
        self.assertFalse(reachable)

    def test_heartbeat_file_missing_is_not_reachable(self):
        missing = self.agents_home / "email" / "heartbeat"
        reachable, _ = self.dash.ping_agent({"heartbeat_file": str(missing)})
        self.assertFalse(reachable)

    def test_agent_with_no_liveness_mechanism_is_unreachable(self):
        reachable, _ = self.dash.ping_agent({"name": "mystery"})
        self.assertFalse(reachable)

    def test_check_all_reports_ok_for_fresh_heartbeat_agent(self):
        heartbeat = self.agents_home / "email" / "heartbeat"
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.touch()
        self.write_registry([{"name": "email", "heartbeat_file": str(heartbeat)}])

        report = self.dash.check_all()
        agent = report["agents"][0]
        self.assertEqual(agent["status"], "ok")


if __name__ == "__main__":
    unittest.main()

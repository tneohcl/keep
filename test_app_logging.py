import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import app_logging

class SessionLogTests(unittest.TestCase):
    def test_rotation_retention_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "backup-important.log").write_text("keep")
            for _ in range(4):
                log = app_logging.SessionLog(root, max_bytes=256, keep_sessions=2)
                for index in range(20):
                    log.event("operation.finished", count=index)
                log.close()
            self.assertEqual(len(list(root.glob("session-*.log"))), 2)
            self.assertLessEqual(len(list(root.glob("session-*"))), 6)
            self.assertEqual((root / "backup-important.log").read_text(), "keep")
            if os.name != "nt":
                self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in root.glob("session-*")))

    def test_exception_does_not_record_secret_messages_or_locals(self):
        with tempfile.TemporaryDirectory() as directory:
            log = app_logging.SessionLog(directory)
            try:
                raise ValueError("SECRET-PASSPHRASE")
            except ValueError as error:
                log.exception(type(error), error, error.__traceback__)
            log.event("operation.finished", password="SECRET-KEY", count=2)
            log.close()
            text = log.path.read_text()
            self.assertNotIn("SECRET", text)
            records = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(records[0]["error_type"], "ValueError")
            self.assertTrue(records[0]["frames"])

    def test_launch_writes_start_ready_and_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            import consumer
            settings = consumer.default_config(directory)
            settings["setup_complete"] = True
            config.write_text(json.dumps(settings))
            env = dict(os.environ, XDG_STATE_HOME=directory, KEEP_CONFIG_PATH=str(config), QT_QPA_PLATFORM="offscreen")
            script = "from PySide6.QtWidgets import QApplication; QApplication.exec=lambda self: 0; import runpy; runpy.run_path('main.py',run_name='__main__')"
            result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            logs = list((Path(directory) / "keep/logs").glob("session-*.log"))
            self.assertEqual(len(logs), 1)
            events = [json.loads(line)["event"] for line in logs[0].read_text().splitlines()]
            for expected in ("session.started", "application.ready", "application.exited", "session.closed"):
                self.assertIn(expected, events)

"""Real Borg lock contention in disposable repositories (Linux only)."""
import io
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import keep_backup


@unittest.skipUnless(sys.platform == "linux" and shutil.which("borg"), "Requires Linux and Borg")
class BackupLockingTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        root = Path(self.scratch.name)
        self.repo = str(root / "repo")
        self.env = dict(os.environ, HOME=str(root), BORG_CACHE_DIR=str(root / "cache"),
                        BORG_CONFIG_DIR=str(root / "config"), BORG_SECURITY_DIR=str(root / "security"),
                        BORG_PASSPHRASE="", BORG_EXIT_CODES="legacy")
        subprocess.run(["borg", "init", "--encryption=none", self.repo], env=self.env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        marker = root / "locked"
        self.holder = subprocess.Popen(["borg", "with-lock", self.repo, sys.executable, "-c",
                                       "from pathlib import Path; import sys,time; Path(sys.argv[1]).touch(); time.sleep(60)",
                                       str(marker)], env=self.env, start_new_session=True,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.release)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue(marker.exists(), "Holder did not acquire repository lock")

    def release(self):
        if self.holder.poll() is None:
            os.killpg(self.holder.pid, signal.SIGTERM)
        self.holder.wait(timeout=10)

    def test_preflight_waits_until_lock_released(self):
        timer = threading.Timer(2, self.release)
        timer.start()
        try:
            with patch.object(keep_backup, "REPOSITORY_LOCK_WAIT_SECONDS", 8):
                self.assertTrue(keep_backup._check_repo_access(self.repo, self.env, io.StringIO()))
        finally:
            timer.join()

    def test_preflight_reports_exhausted_wait(self):
        log = io.StringIO()
        with patch.object(keep_backup, "REPOSITORY_LOCK_WAIT_SECONDS", 1):
            self.assertFalse(keep_backup._check_repo_access(self.repo, self.env, log))
        self.assertIn("wait up to 1 seconds", log.getvalue())
        self.assertIn("access check failed", log.getvalue())
        self.assertIsNone(self.holder.poll(), "Active lock holder must not be killed")

    def test_waiting_process_group_can_be_cancelled(self):
        script = ("import io,os,sys,keep_backup; "
                  "keep_backup._check_repo_access(sys.argv[1],dict(os.environ),sys.stdout)")
        waiter = subprocess.Popen([sys.executable, "-u", "-c", script, self.repo], env=self.env,
                                  start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(2)
            self.assertIsNone(waiter.poll())
            os.killpg(waiter.pid, signal.SIGTERM)
            self.assertNotEqual(waiter.wait(timeout=10), 0)
            self.assertIsNone(self.holder.poll())
        finally:
            if waiter.poll() is None:
                os.killpg(waiter.pid, signal.SIGKILL)
                waiter.wait(timeout=10)
        self.release()
        self.assertTrue(keep_backup._check_repo_access(self.repo, self.env, io.StringIO()))


if __name__ == "__main__":
    unittest.main()

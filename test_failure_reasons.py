"""Plain-language reasons for a failed backup: the engine logs one, and the
Status page shows it (or says the run stopped before finishing)."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_STATE = tempfile.TemporaryDirectory()
os.environ["KEEP_CONFIG_PATH"] = str(Path(_STATE.name) / "config.json")
os.environ["XDG_STATE_HOME"] = str(Path(_STATE.name) / "state")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import consumer  # noqa: E402
import main  # noqa: E402
from operation_lock import RepositoryLock  # noqa: E402

REPO = "/mnt/nas/backups/repo"


class BorgFailureReason(unittest.TestCase):
    """The destination is only blamed when Borg's output ties the error to the
    repository: a path inside it (with a real path boundary) or Borg's own
    repository messages. Otherwise the reason is about this computer, about a
    file being backed up, or neutral when the device can't be told."""

    NEUTRAL = "either on this computer or"

    def reason(self, output):
        return consumer.borg_failure_reason(output, REPO)

    def test_destination_full(self):
        for output in ("No space left on device, cleaning up partial transaction to free space.",
                       "Insufficient free space to complete transaction (required: 1.00 GB, available: 12.3 MB).",
                       f"OSError: [Errno 28] No space left on device: '{REPO}/data/0/5'"):
            with self.subTest(output=output):
                self.assertIn("backup destination is full", self.reason(output))

    def test_local_cache_full_is_this_computer(self):
        reason = self.reason("OSError: [Errno 28] No space left on device: '/home/me/.cache/borg/abc/chunks.tmp'")
        self.assertIn("This computer", reason)
        self.assertNotIn("destination", reason)

    def test_full_without_a_path_is_neutral(self):
        self.assertIn(self.NEUTRAL, self.reason("OSError: [Errno 28] No space left on device"))

    def test_destination_disconnected(self):
        for output in (f"FileNotFoundError: [Errno 2] No such file or directory: '{REPO}/data/0/12'",
                       f"OSError: [Errno 5] Input/output error: '{REPO}/data/0/3'",
                       f"OSError: [Errno 107] Transport endpoint is not connected: '{REPO}/index.12'",
                       f"Repository {REPO} does not exist."):
            with self.subTest(output=output):
                self.assertIn("backup destination disconnected", self.reason(output))

    def test_connection_errors_without_a_path_are_neutral(self):
        for output in ("OSError: [Errno 107] Transport endpoint is not connected",
                       "OSError: [Errno 5] Input/output error",
                       "OSError: [Errno 116] Stale file handle"):
            with self.subTest(output=output):
                reason = self.reason(output)
                self.assertIn(self.NEUTRAL, reason)
                self.assertNotIn("destination disconnected", reason)

    def test_source_read_error_is_not_the_destination(self):
        reason = self.reason("OSError: [Errno 5] Input/output error: '/home/me/Documents/broken-file'")
        self.assertIn("couldn't be read", reason)
        self.assertNotIn("destination", reason)

    def test_a_missing_source_file_is_not_a_disconnected_destination(self):
        self.assertIsNone(self.reason(
            "FileNotFoundError: [Errno 2] No such file or directory: '/home/me/Documents/gone.txt'"))

    def test_sibling_paths_sharing_the_prefix_are_not_the_repository(self):
        for output in ("PermissionError: [Errno 13] Permission denied: '/mnt/nas/backups/repository-source/file'",
                       "FileNotFoundError: [Errno 2] No such file or directory: '/mnt/nas/backups/repo-old/data/0'",
                       "Repository /mnt/nas/backups/repo2 does not exist."):
            with self.subTest(output=output):
                self.assertNotIn("destination", self.reason(output) or "")

    def test_permission_passphrase_and_lock(self):
        self.assertIn("permission", self.reason(f"PermissionError: [Errno 13] Permission denied: '{REPO}/config'"))
        self.assertIn("passphrase", self.reason(
            "passphrase supplied in BORG_PASSPHRASE, by BORG_PASSCOMMAND or via BORG_PASSPHRASE_FD is incorrect."))
        self.assertIn("another backup or check", self.reason(
            f"Failed to create/acquire the lock {REPO}/lock.exclusive (timeout).").lower())

    def test_unrecognised_output_has_no_reason(self):
        self.assertIsNone(self.reason("Something unexpected happened."))


class StatusPageReason(unittest.TestCase):
    def setUp(self):
        self.logdir = Path(tempfile.mkdtemp())
        patcher = patch.object(main, "LOGDIR", str(self.logdir))
        patcher.start()
        self.addCleanup(patcher.stop)

    def log(self, *lines, age=0):
        path = self.logdir / "backup-20260926-040000.log"
        body = [f"Repository: {REPO}", *lines]
        path.write_text("\n".join(f"2026-09-26T04:00:01+08:00 {line}" for line in body) + "\n")
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
        return path

    def test_the_logged_reason_is_shown(self):
        self.log("ERROR borg create failed (rc=2)", "Reason: The backup destination is full.",
                 "Keep backup finished with exit code 3")
        self.assertEqual(main.backup_failure_reason(REPO, None), "The backup destination is full.")

    def test_no_reason_for_a_finished_backup(self):
        self.log("backup completed successfully", "Keep backup finished with exit code 0", age=3600)
        self.assertIsNone(main.backup_failure_reason(REPO, None))

    def test_a_run_that_stopped_writing_and_holds_no_lock_was_interrupted(self):
        self.log("running borg create", age=600)
        self.assertIn("stopped before finishing", main.backup_failure_reason(REPO, None))

    def test_a_run_still_writing_is_not_called_interrupted(self):
        self.log("running borg create")
        self.assertIsNone(main.backup_failure_reason(REPO, None))

    def test_a_run_holding_the_repository_lock_is_not_called_interrupted(self):
        self.log("running borg create", age=600)
        with RepositoryLock(REPO):
            self.assertIsNone(main.backup_failure_reason(REPO, None))


if __name__ == "__main__":
    unittest.main()

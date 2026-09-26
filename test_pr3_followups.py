"""Regression tests for the PR #3 follow-ups: unknown repository identity,
records written before repository IDs were stored, cancelled restores and
unsupported special files. Each case failed on the code PR #3 merged."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_STATE = tempfile.TemporaryDirectory()
os.environ["KEEP_CONFIG_PATH"] = str(Path(_STATE.name) / "config.json")
os.environ["XDG_STATE_HOME"] = str(Path(_STATE.name) / "state")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

_app = QApplication.instance() or QApplication([])

import main  # noqa: E402
import recovery_test  # noqa: E402
from keep_ui.safe_copy import restore_entries  # noqa: E402
from keep_ui.status_icons import SuccessBadge  # noqa: E402

REPO = "/mnt/backups/repo"
REPO_ID = "a" * 64
OTHER_ID = "b" * 64


def write_log(logdir, stamp, body, repository=REPO, repository_id=REPO_ID):
    lines = [f"2026-09-{stamp[6:8]}T04:00:00+08:00 Keep backup started"]
    if repository:
        lines.append(f"2026-09-{stamp[6:8]}T04:00:00+08:00 Repository: {repository}")
    if repository_id:
        lines.append(f"2026-09-{stamp[6:8]}T04:00:01+08:00 Repository ID: {repository_id}")
    lines.append(f"2026-09-{stamp[6:8]}T04:00:20+08:00 {body}")
    Path(logdir, f"backup-{stamp}-040000.log").write_text("\n".join(lines) + "\n")


class UnknownIdentityHistory(unittest.TestCase):
    """Issue 1: an unknown repository ID must not read as an empty history."""

    def setUp(self):
        self.logdir = tempfile.mkdtemp()
        patcher = patch.object(main, "LOGDIR", self.logdir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unknown_id_still_finds_this_locations_history(self):
        write_log(self.logdir, "20260926", "backup completed successfully")
        verdict, ts = main.last_backup_attempt_status(REPO, None)
        self.assertEqual(verdict, "ok")
        self.assertTrue(ts)
        self.assertEqual(main.backup_history(REPO, None), ("ok", ts, False))
        self.assertEqual(main.backup_history(REPO, REPO_ID), ("ok", ts, True))

    def test_log_for_another_repository_is_never_used(self):
        write_log(self.logdir, "20260926", "backup completed successfully", repository_id=OTHER_ID)
        self.assertEqual(main.backup_history(REPO, REPO_ID)[0], "never run")
        self.assertEqual(main.backup_history("/elsewhere", None)[0], "never run")

    def test_log_without_an_id_is_unverified_history(self):
        write_log(self.logdir, "20260925", "backup completed successfully", repository_id=None)
        self.assertEqual(main.backup_history(REPO, REPO_ID)[::2], ("ok", False))


class Headline(QLabel):
    def __init__(self):
        super().__init__()
        self.lbl_headline = QLabel()
        self.headline_icon = SuccessBadge("never", 48)


class UnknownIdentityHeadline(unittest.TestCase):
    def headline(self, *args, **kwargs):
        probe = Headline()
        main.MainWindow._apply_status_headline(probe, *args, **kwargs)
        return probe.lbl_headline.text(), probe.headline_icon.state()

    def test_unverified_success_is_not_claimed(self):
        text, state = self.headline(True, "ok", "2026-09-26T04:00:00", verified=False)
        self.assertEqual(text, "Unable to verify backup history")
        self.assertNotEqual(state, "ok")

    def test_unknown_identity_is_not_an_empty_repository(self):
        text, _ = self.headline(True, "never run", None, verified=False)
        self.assertNotEqual(text, "No backups yet")
        self.assertEqual(self.headline(True, "never run", None, verified=False, checking=True)[0],
                         "Checking backup history…")

    def test_unverified_failure_is_still_reported(self):
        self.assertEqual(self.headline(True, "FAILED", "2026-09-26T04:00:00", verified=False),
                         ("Last backup failed", "error"))

    def test_verified_states_are_unchanged(self):
        self.assertEqual(self.headline(True, "ok", "2026-09-26T04:00:00")[0], "Last backup completed successfully")
        self.assertEqual(self.headline(True, "never run", None)[0], "No backups yet")
        self.assertEqual(self.headline(False, "ok", "2026-09-26T04:00:00")[0], "Backup destination unavailable")


class LegacyRecords(unittest.TestCase):
    """Issue 2: records written before Keep stored repository IDs."""

    OLDEST_ARCHIVE = "2026-09-25T06:14:48.000000"

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def legacy_test(self, finished):
        record = {"result": "passed", "reason": "passed", "repository": REPO,
                  "archive": "keep-a", "finished": finished}
        Path(self.root, recovery_test.STATE_FILE).write_text(json.dumps(record))
        return record

    def test_record_newer_than_the_oldest_archive_is_carried_forward(self):
        self.legacy_test("2026-09-25T21:37:48+08:00")
        recovery_test.adopt_legacy_records(REPO, REPO_ID, self.OLDEST_ARCHIVE, self.root)
        record = recovery_test.load(self.root)
        self.assertEqual(record["repository_id"], REPO_ID)
        self.assertEqual(recovery_test.status(record, REPO, repository_id=REPO_ID)[0], "ok")

    def test_record_older_than_the_oldest_archive_stays_historical(self):
        self.legacy_test("2026-09-20T10:00:00+08:00")
        recovery_test.adopt_legacy_records(REPO, REPO_ID, self.OLDEST_ARCHIVE, self.root)
        record = recovery_test.load(self.root)
        self.assertNotIn("repository_id", record)          # retained, not rewritten or deleted
        state, when, advice = recovery_test.status(record, REPO, repository_id=REPO_ID)
        self.assertEqual(state, "never")
        self.assertIn("earlier", advice)

    def test_unverified_record_is_shown_as_history_not_as_never(self):
        record = self.legacy_test("2026-09-25T21:37:48+08:00")
        state, when, advice = recovery_test.status(record, REPO, repository_id=None)
        self.assertEqual((state, when), ("info", "2026-09-25T21:37:48+08:00"))
        stamped = dict(record, repository_id=REPO_ID)
        self.assertEqual(recovery_test.status(stamped, REPO)[0], "info")    # ID not known yet

    def test_access_confirmations_are_carried_forward_by_date(self):
        path = Path(self.root, recovery_test.ACCESS_FILE)
        path.write_text(json.dumps({REPO: {"passphrase_saved": "2026-09-25T08:30:00+08:00",
                                           "key_exported": "2026-09-24T10:00:00+08:00"}}))
        recovery_test.adopt_legacy_records(REPO, REPO_ID, self.OLDEST_ARCHIVE, self.root)
        entry = recovery_test.load_access(REPO, self.root, repository_id=REPO_ID)
        self.assertEqual(entry, {"passphrase_saved": "2026-09-25T08:30:00+08:00"})
        self.assertIn(REPO, json.loads(path.read_text()))   # the legacy entry is retained

    def test_integrity_check_record_is_carried_forward(self):
        check = Path(self.root, "last-check.json")
        check.write_text(json.dumps({"repository": REPO, "deep": False, "result": "success",
                                     "started": "2026-09-25T12:00:00+08:00",
                                     "finished": "2026-09-25T12:03:00+08:00"}))
        recovery_test.adopt_legacy_records(REPO, REPO_ID, self.OLDEST_ARCHIVE, self.root)
        self.assertEqual(json.loads(check.read_text())["repository_id"], REPO_ID)


class IncompleteRestore(unittest.TestCase):
    """Issue 3: cancelled or failed restores must be recognisable as such."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.src = self.root / "big.bin"
        self.src.write_bytes(os.urandom(3 * 1024 * 1024))

    def test_cancel_mid_file_leaves_no_file_under_the_final_name(self):
        calls = []

        def cancel():
            calls.append(1)
            return len(calls) >= 4          # after the first 1 MB block
        dest = self.root / "restore"
        done, failed, skipped = restore_entries([(self.src, "big.bin", "Big")], dest, cancel)
        self.assertFalse(done)
        self.assertFalse((dest / "big.bin").exists())
        self.assertEqual([p.name for p in dest.iterdir()], ["RESTORE-INCOMPLETE.txt"])

    def test_failed_entry_marks_the_folder_incomplete(self):
        dest = self.root / "restore"
        done, failed, skipped = restore_entries(
            [(self.src, "big.bin", "Big"), (self.root / "missing", "missing", "Missing")], dest)
        self.assertEqual((len(done), len(failed)), (1, 1))
        self.assertEqual((dest / "big.bin").read_bytes(), self.src.read_bytes())
        self.assertTrue((dest / "RESTORE-INCOMPLETE.txt").exists())

    def test_complete_restore_has_no_marker(self):
        dest = self.root / "restore"
        done, failed, skipped = restore_entries([(self.src, "big.bin", "Big")], dest)
        self.assertEqual((len(done), failed, skipped), (1, [], []))
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["big.bin"])


@unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
class SpecialFiles(unittest.TestCase):
    """Issue 4: a FIFO inside a folder must not fail the whole folder."""

    def test_special_file_is_skipped_and_the_rest_restored(self):
        root = Path(tempfile.mkdtemp())
        app = root / "app"
        (app / "data").mkdir(parents=True)
        (app / "data" / "settings.ini").write_text("x=1")
        os.mkfifo(app / "data" / "control.fifo")
        dest = root / "restore"
        done, failed, skipped = restore_entries([(app, "app", "App")], dest)
        self.assertEqual((len(done), failed), (1, []))
        self.assertEqual((dest / "app" / "data" / "settings.ini").read_text(), "x=1")
        self.assertFalse(os.path.lexists(dest / "app" / "data" / "control.fifo"))
        self.assertEqual(len(skipped), 1)
        self.assertIn("control.fifo", skipped[0])
        self.assertFalse((dest / "RESTORE-INCOMPLETE.txt").exists())


if __name__ == "__main__":
    unittest.main()

"""Regression tests for the PR #3 follow-ups: unknown repository identity,
records written before repository IDs were stored, cancelled restores and
unsupported special files. Each case failed on the code PR #3 merged."""
from datetime import datetime
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
    """Issue 2 (and review of #4): records written before Keep stored
    repository IDs are history. Nothing about their dates can verify or
    disqualify them (retention pruning moves the oldest archive forward), so
    they are kept as they are and shown as unverified until a new test,
    check or confirmation records the ID."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        patcher = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = self.root / "keep"
        self.state.mkdir()

    def write(self, name, data):
        path = self.state / name
        path.write_text(json.dumps(data))
        return path

    def legacy_test(self, finished="2026-09-25T21:37:48+08:00", result="passed"):
        return {"result": result, "reason": result, "repository": REPO, "archive": "keep-a", "finished": finished}

    def test_legacy_record_is_unverified_history(self):
        for finished in ("2026-09-25T21:37:48+08:00", "2020-01-01T00:00:00+00:00"):
            state, when, _ = recovery_test.status(self.legacy_test(finished), REPO, repository_id=REPO_ID)
            self.assertEqual((state, when), ("info", finished))
        self.assertEqual(recovery_test.status(self.legacy_test(result="failed"), REPO, repository_id=REPO_ID)[0], "error")
        self.assertEqual(recovery_test.status(self.legacy_test(), "/elsewhere", repository_id=REPO_ID)[0], "never")

    def test_repository_query_leaves_legacy_records_untouched(self):
        # The oldest surviving archive is newer than the (valid) test: pruning.
        test = self.write(recovery_test.STATE_FILE, self.legacy_test())
        access = self.write(recovery_test.ACCESS_FILE, {REPO: {"passphrase_saved": "2026-09-25T08:30:00+08:00"}})
        check = self.write("last-check.json", {"repository": REPO, "result": "success",
                                               "finished": "2026-09-25T12:03:00+08:00"})
        before = {path: path.read_text() for path in (test, access, check)}
        with patch.object(main.MainWindow, "refresh_status", lambda self: None):
            window = main.MainWindow()
        self.addCleanup(window.deleteLater)
        window._pending_select_latest = False       # set by a real query start
        with patch.object(main, "REPO", REPO), patch.object(main, "DEST_STATUS", {"available": True, "repo": REPO}):
            window._on_status_query_finished(REPO, {"repository": {"id": REPO_ID}},
                                             {"archives": [{"name": "newest", "start": "2027-04-01T04:00:00"}]}, False)
        self.assertEqual({path: path.read_text() for path in before}, before)
        self.assertEqual(window.status_facts.facts["recovery"]["icon"].state(), "info")
        self.assertEqual(window.status_facts.facts["check"]["icon"].state(), "info")

    def test_legacy_confirmations_are_history_not_confirmations(self):
        self.write(recovery_test.ACCESS_FILE, {REPO: {"passphrase_saved": "2026-09-25T08:30:00+08:00"}})
        self.assertEqual(recovery_test.load_access(REPO, repository_id=REPO_ID), {})
        self.assertEqual(recovery_test.legacy_access(REPO), {"passphrase_saved": "2026-09-25T08:30:00+08:00"})
        recovery_test.save_access(REPO, {"kit_offsite": "2026-09-26T09:00:00+08:00"}, repository_id=REPO_ID)
        saved = json.loads((self.state / recovery_test.ACCESS_FILE).read_text())
        self.assertEqual(saved[REPO], {"passphrase_saved": "2026-09-25T08:30:00+08:00"})    # kept as is
        self.assertEqual(saved[REPO_ID], {"kit_offsite": "2026-09-26T09:00:00+08:00"})

    def test_unknown_current_id_shows_history(self):
        stamped = dict(self.legacy_test(), repository_id=REPO_ID)
        self.assertEqual(recovery_test.status(stamped, REPO)[0], "info")
        self.assertEqual(recovery_test.status(stamped, REPO, repository_id=REPO_ID)[0], "ok")
        self.assertEqual(recovery_test.status(stamped, REPO, repository_id=OTHER_ID)[0], "never")


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


class RestoreNameCollisions(unittest.TestCase):
    """Review of #4: Keep's own temporary files and marker must never touch
    restored content, whatever names the backup contains."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def source(self, name, text):
        path = self.root / "src" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        return path

    def test_temporary_name_in_the_backup_is_never_deleted(self):
        partial = self.source(".report.keep-partial", "a real file")
        report = self.source("report", "the report")
        dest = self.root / "restore"
        done, failed, skipped = restore_entries(
            [(partial, ".report.keep-partial", "A"), (report, "report", "B")], dest)
        self.assertEqual((len(done), failed), (2, []))
        self.assertEqual((dest / ".report.keep-partial").read_text(), "a real file")
        self.assertEqual((dest / "report").read_text(), "the report")
        self.assertEqual(sorted(p.name for p in dest.iterdir()), [".report.keep-partial", "report"])

    def test_marker_never_overwrites_a_restored_file(self):
        mine = self.source("RESTORE-INCOMPLETE.txt", "my own notes")
        dest = self.root / "restore"
        done, failed, skipped = restore_entries(
            [(mine, "RESTORE-INCOMPLETE.txt", "Notes"), (self.root / "missing", "missing", "Missing")], dest)
        self.assertEqual((len(done), len(failed)), (1, 1))
        self.assertEqual((dest / "RESTORE-INCOMPLETE.txt").read_text(), "my own notes")
        markers = [p for p in dest.iterdir() if p.name != "RESTORE-INCOMPLETE.txt"]
        self.assertEqual(len(markers), 1)
        self.assertTrue(markers[0].name.startswith("RESTORE-INCOMPLETE"))
        self.assertIn("did not finish", markers[0].read_text())

    @unittest.skipIf(os.name == "nt", "Windows symlink creation requires privileges")
    def test_marker_never_follows_a_restored_symlink(self):
        outside = self.root / "outside.txt"
        outside.write_text("untouched")
        link = self.root / "src" / "RESTORE-INCOMPLETE.txt"
        link.parent.mkdir()
        link.symlink_to(outside)
        dest = self.root / "restore"
        restore_entries([(link, "RESTORE-INCOMPLETE.txt", "Link"), (self.root / "missing", "missing", "Missing")], dest)
        self.assertEqual(outside.read_text(), "untouched")
        self.assertTrue((dest / "RESTORE-INCOMPLETE.txt").is_symlink())


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

"""Recorded recovery test against real throwaway Borg repositories."""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import recovery_test

PASSPHRASE = "recovery-test-passphrase-41"


def borg(*arguments, keys_dir, cwd=None):
    env = dict(os.environ, BORG_PASSPHRASE=PASSPHRASE, BORG_KEYS_DIR=str(keys_dir),
               BORG_SECURITY_DIR=str(Path(keys_dir).parent / "security"), BORG_CACHE_DIR=str(Path(keys_dir).parent / "cache"))
    subprocess.run(["borg", *arguments], env=env, cwd=cwd, check=True, capture_output=True)


@unittest.skipUnless(shutil.which("borg"), "borg is not installed")
class RecoveryTestRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="keep-rt-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.keys = self.tmp / "keys"
        self.keys.mkdir()
        self.source = self.tmp / "src"
        (self.source / "docs").mkdir(parents=True)
        (self.source / "docs" / "note.txt").write_text("the files you care about\n")
        (self.source / "empty.txt").write_text("")

    def make_repo(self, encryption, archive=True):
        repo = str(self.tmp / f"repo-{encryption}")
        borg("init", "--encryption", encryption, repo, keys_dir=self.keys)
        if archive:
            borg("create", f"{repo}::first", "src", keys_dir=self.keys, cwd=self.tmp)
        return repo

    def test_repokey_passes_with_only_the_passphrase_and_leaves_nothing_behind(self):
        repo = self.make_repo("repokey-blake2")
        before = set(Path(tempfile.gettempdir()).glob("keep-recovery-test-*"))
        result = recovery_test.run(repo, PASSPHRASE)
        self.assertEqual((result["result"], result["reason"]), ("passed", "passed"), result)
        self.assertEqual(result["archive"], "first")
        self.assertEqual(result["bytes"], len("the files you care about\n"))  # empty files are never the sample
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("keep-recovery-test-*")), before)

    def test_wrong_passphrase_fails_plainly(self):
        repo = self.make_repo("repokey-blake2")
        result = recovery_test.run(repo, "not-the-passphrase")
        self.assertEqual((result["result"], result["reason"]), ("failed", "wrong_passphrase"), result)

    def test_keyfile_repository_fails_because_the_key_is_not_in_the_backup(self):
        # The test deliberately runs with an empty keys directory: a keyfile
        # repo can't be recovered from the passphrase alone.
        repo = self.make_repo("keyfile-blake2")
        result = recovery_test.run(repo, PASSPHRASE)
        self.assertEqual((result["result"], result["reason"]), ("failed", "key_missing"), result)

    def test_empty_repository_says_back_up_first(self):
        repo = self.make_repo("repokey-blake2", archive=False)
        self.assertEqual(recovery_test.run(repo, PASSPHRASE)["reason"], "no_archives")

    def test_cancel_records_nothing(self):
        repo = self.make_repo("repokey-blake2")
        result = recovery_test.run(repo, PASSPHRASE, cancelled=lambda: True)
        self.assertEqual(result["result"], "cancelled")
        self.assertIsNone(recovery_test.record(result, self.tmp / "state"))
        self.assertIsNone(recovery_test.record({"result": "failed", "reason": "busy"}, self.tmp / "state"))
        self.assertFalse((self.tmp / "state" / recovery_test.STATE_FILE).exists())


class RecordAndStatus(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="keep-rt-state-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_record_keeps_only_outcome_and_date(self):
        kept = recovery_test.record({"result": "passed", "reason": "passed", "repository": "/r", "archive": "a",
                                     "finished": "2026-09-25T10:00:00+08:00", "bytes": 12, "message": "x", "started": "y"},
                                    self.root)
        stored = json.loads((self.root / recovery_test.STATE_FILE).read_text())
        self.assertEqual(stored, kept)
        self.assertEqual(set(stored), {"result", "reason", "repository", "archive", "finished"})
        self.assertEqual((self.root / recovery_test.STATE_FILE).stat().st_mode & 0o777, 0o600)
        self.assertEqual(recovery_test.load(self.root), stored)

    def test_status_never_ok_stale_and_failed(self):
        now = datetime.datetime(2026, 9, 25, 10, tzinfo=datetime.timezone.utc)
        passed = {"result": "passed", "repository": "/r", "finished": "2026-09-01T10:00:00+00:00"}
        self.assertEqual(recovery_test.status(None, "/r")[0], "never")
        self.assertEqual(recovery_test.status(passed, "/other")[0], "never")  # a test of another repo doesn't count
        self.assertEqual(recovery_test.status(passed, "/r", now)[0], "ok")
        stale = dict(passed, finished="2026-02-01T10:00:00+00:00")
        self.assertEqual(recovery_test.status(stale, "/r", now)[0], "warning")
        failed = dict(passed, result="failed", reason="wrong_passphrase")
        state, _, advice = recovery_test.status(failed, "/r", now)
        self.assertEqual(state, "error")
        self.assertIn("passphrase", advice)


if __name__ == "__main__":
    unittest.main()

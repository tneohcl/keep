"""Regression cases for review-folder safety and repository identity."""
import datetime
import os
from pathlib import Path
import tempfile
import unittest
import consumer
import recovery_test
from keep_ui.safe_copy import restore_entries


class SafeRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / 'source'
        self.src.write_text('archived content')

    def test_existing_destination_is_never_overwritten(self):
        dest = self.root / 'existing'
        dest.mkdir()
        (dest / 'file').write_text('live content')
        done, failed, skipped = restore_entries([(self.src, 'file', 'File')], dest)
        self.assertFalse(done)
        self.assertTrue(failed)
        self.assertEqual((dest / 'file').read_text(), 'live content')

    def test_duplicate_targets_report_failure_without_overwriting(self):
        other = self.root / 'other'
        other.write_text('different')
        dest = self.root / 'restore'
        done, failed, skipped = restore_entries([(self.src, 'file', 'A'), (other, 'file', 'B')], dest)
        self.assertEqual((len(done), len(failed)), (1, 1))
        self.assertEqual((dest / 'file').read_text(), 'archived content')

    def test_missing_requested_source_is_failure(self):
        done, failed, skipped = restore_entries([(self.root / 'missing', 'file', 'Missing')], self.root / 'restore')
        self.assertFalse(done)
        self.assertIn('missing', failed[0].lower())

    def test_archive_paths_preserve_equal_basenames(self):
        done, failed, skipped = restore_entries([(self.src, 'one/file', 'One'), (self.src, 'two/file', 'Two')], self.root / 'restore')
        self.assertEqual(len(done), 2)
        self.assertFalse(failed)

    def test_cancel_during_file_reports_incomplete(self):
        calls = []
        def cancel():
            calls.append(1)
            return len(calls) >= 3
        done, failed, skipped = restore_entries([(self.src, 'file', 'File')], self.root / 'restore', cancel)
        self.assertFalse(done)
        self.assertIn('cancelled', failed[0])

    def test_traversal_rejected(self):
        done, failed, skipped = restore_entries([(self.src, '../escape', 'File')], self.root / 'restore')
        self.assertFalse(done)
        self.assertTrue(failed)
        self.assertFalse((self.root / 'escape').exists())

    @unittest.skipIf(os.name == 'nt', 'Windows symlink creation requires privileges')
    def test_links_preserved_but_never_followed_at_destination(self):
        link = self.root / 'link'
        link.symlink_to(self.root / 'missing')
        dest = self.root / 'restore'
        done, failed, skipped = restore_entries([(link, 'link', 'Link'), (self.src, 'link/file', 'File')], dest)
        self.assertEqual((len(done), len(failed)), (1, 1))
        self.assertTrue((dest / 'link').is_symlink())
        self.assertFalse((self.root / 'missing').exists())


class RepositoryIdentityTests(unittest.TestCase):
    def test_recreated_repository_does_not_inherit_success(self):
        record = {'repository': '/same/path', 'repository_id': 'old', 'result': 'passed',
                  'finished': datetime.datetime.now().astimezone().isoformat()}
        self.assertFalse(consumer.matches_repository(record, '/same/path', 'new'))
        self.assertEqual(recovery_test.status(record, '/same/path', repository_id='new')[0], 'never')
        self.assertEqual(recovery_test.status(record, '/same/path', repository_id='old')[0], 'ok')
        # Unknown current ID: shown as unverified history, not as 'never' (PR #3 follow-up).
        self.assertEqual(recovery_test.status(record, '/same/path')[0], 'info')

    def test_confirmations_do_not_transfer_to_new_repository(self):
        with tempfile.TemporaryDirectory() as root:
            recovery_test.save_access('/r', {'key_exported': 'date'}, root, repository_id='old')
            self.assertEqual(recovery_test.load_access('/r', root, repository_id='new'), {})
            self.assertEqual(recovery_test.load_access('/r', root), {})
            self.assertEqual(recovery_test.load_access('/r', root, repository_id='old'), {'key_exported': 'date'})


if __name__ == '__main__':
    unittest.main()

"""Portable regression tests for configuration and release safety."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import consumer
import keep_backup
import destination


class ReleaseSafetyTests(unittest.TestCase):
    def test_migration_and_update_preserve_user_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            app.mkdir()
            legacy = {"destination": {"type": "other", "repo": "/backup"}}
            (app / "config.json").write_text(json.dumps(legacy))
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "xdg")}, clear=True):
                target = consumer.config_path(str(app))
                self.assertEqual(json.loads(target.read_text()), legacy)
                consumer.write_config(target, {"new": True})
                self.assertEqual(consumer.config_path(str(app)), target)
                self.assertEqual(json.loads(target.read_text()), {"new": True})
                self.assertEqual(json.loads((app / "config.json").read_text()), legacy)

    def test_override_does_not_migrate(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "override.json"
            with patch.dict(os.environ, {"KEEP_CONFIG_PATH": str(target)}):
                self.assertEqual(consumer.config_path(directory), target)
                self.assertFalse(target.exists())

    def test_prune_rejects_unbounded_selectors(self):
        for prefix in ("*", "keep-*-", "keep-[abc]-", "unrelated-", "keep-?-", "keep-"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                consumer.validate_retention({"archive_prefix": prefix})
        self.assertEqual(consumer.validate_retention({"archive_prefix": "keep-test-"}), "keep-test-")
        with self.assertRaises(ValueError):
            consumer.validate_retention({"retention": {"daily": 0, "weekly": 0, "monthly": 0}})

    def test_missing_source_stops_before_borg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = consumer.default_config(directory)
            config["backup_sources"] = [{"path": str(root / "missing")}]
            path = root / "config.json"
            consumer.write_config(path, config)
            with patch("keep_backup.shutil.which", return_value="borg"), patch("keep_backup._check_repo_access") as access:
                import io
                log = io.StringIO()
                self.assertEqual(keep_backup._run_with_log(str(path), log), 2)
                access.assert_not_called()
                self.assertIn("selected backup source(s) unavailable", log.getvalue())

    def test_summary_excludes_paths_and_diagnostics(self):
        summary = consumer.support_summary("2026-09-15T10:00:00+00:00 Keep backup started\nRepository: /private/name\nERROR secret diagnostic\n2026-09-15T10:00:00+00:00 backup completed with warnings")
        self.assertEqual(summary, "Keep backup started\nbackup completed with warnings")

    def test_systemd_literal_expansion(self):
        self.assertEqual(consumer._systemd_quote('/a%h/$HOME'), '"/a%%h/$$HOME"')

    def test_activity_uses_recorded_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(consumer.recent_activity(directory), [])
            for index, text in enumerate(("backup completed successfully", "backup completed with warnings", "STOPPED BY USER", "still running")):
                (root / f"backup-{index}.log").write_text("2026-09-15T09:30:00+08:00 start\n" + text)
            rows = consumer.recent_activity(directory)
            self.assertEqual([row[1] for row in rows], ["No final result recorded", "Backup stopped", "Completed with warnings"])

    def test_activity_only_lists_this_destinations_backups(self):
        # A repository deleted and re-created at the same path is a different
        # backup: its runs must not appear once the new one has an ID on record.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def log(name, repo, repo_id, text):
                header = f"2026-09-25T06:00:00+08:00 Keep backup started\n2026-09-25T06:00:00+08:00 Repository: {repo}\n"
                if repo_id:
                    header += f"2026-09-25T06:00:01+08:00 Repository ID: {repo_id}\n"
                (root / f"backup-{name}.log").write_text(header + text)
            log("1", "/nas/repo", "old-id", "backup completed successfully")
            log("2", "/other/repo", "other-id", "backup completed successfully")
            log("3", "/nas/repo", "new-id", "backup completed with warnings")
            log("4", "/nas/repo", None, "STOPPED BY USER")  # older log, no ID recorded
            rows = consumer.recent_activity(directory, "/nas/repo", "new-id")
            self.assertEqual([row[1] for row in rows], ["Backup stopped", "Completed with warnings"])
            self.assertEqual(len(consumer.recent_activity(directory)), 3)  # unfiltered: newest three

    def test_drive_subpath_cannot_escape_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = Path(directory) / "drive"
            mount.mkdir()
            with patch("destination._findmnt_target_for_uuid", return_value=str(mount)):
                result = destination.resolve_destination({"type": "removable", "uuid": "test", "repo_subpath": "../elsewhere"})
            self.assertFalse(result["available"])
            self.assertIn("escapes", result["reason"])

    def test_custom_borg_key_material_is_excluded(self):
        with patch.dict(os.environ, {"BORG_KEYS_DIR": "/private/keys", "BORG_KEY_FILE": "/private/key"}):
            protected = keep_backup._protected_paths()
            self.assertIn("/private/keys", protected)
            self.assertIn("/private/key", protected)



class DebianPackageContents(unittest.TestCase):
    def test_every_module_the_package_imports_is_installed(self):
        # Review of #8: host.py was imported by main/cli/consumer but not
        # installed by build-deb.sh, so the packaged app, CLI and scheduled
        # engine failed to start.
        import re
        root = Path(__file__).resolve().parent
        script = (root / "packaging" / "build-deb.sh").read_text()
        installed = set(re.findall(r"\b(\w+)\.py\b", script.split('"$stage/usr/lib/keep/"')[0]))
        local = {p.stem for p in root.glob("*.py") if not p.stem.startswith("test_")}
        packaged = [root / f"{name}.py" for name in installed if (root / f"{name}.py").exists()]
        packaged += sorted((root / "keep_ui").glob("*.py"))
        missing = set()
        for source in packaged:
            for name in re.findall(r"^\s*(?:from|import)\s+(\w+)", source.read_text(), re.M):
                if name in local and name not in installed:
                    missing.add(f"{name}.py (imported by {source.name})")
        self.assertEqual(sorted(missing), [])

if __name__ == "__main__":
    unittest.main()

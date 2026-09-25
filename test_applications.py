import tempfile
import unittest
from pathlib import Path
import applications
import consumer


class AppSelectionTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.home = Path(self.scratch.name)
        for path in (".mozilla", ".var/app/org.mozilla.firefox", ".config/kritarc", ".config/unrecognized", ".config/borg", "Documents", ".ssh"):
            (self.home / path).mkdir(parents=True)
        self.config = consumer.default_config(str(self.home))
        # Isolate from the host's system-wide Flatpaks (/var/lib/flatpak/app).
        from unittest.mock import patch
        system_flatpaks = patch.object(applications, "SYSTEM_FLATPAK_APP_DIR", self.home / "system-flatpak-app")
        system_flatpaks.start()
        self.addCleanup(system_flatpaks.stop)

    def test_residual_data_does_not_prove_installation(self):
        from unittest.mock import patch
        with patch("applications.os.environ", {"XDG_DATA_DIRS": str(self.home / "empty")}), patch("shutil.which", return_value=None):
            installed = applications.InstalledApps(self.home)
        self.assertFalse(installed.contains("Firefox"))
        self.assertFalse(installed.contains("Krita"))

    def test_flatpak_data_requires_active_deployment(self):
        from unittest.mock import patch
        appid = "org.example.Leftover"
        (self.home / ".var/app" / appid).mkdir()
        with patch("applications.os.environ", {"XDG_DATA_DIRS": str(self.home / "empty")}), patch("shutil.which", return_value=None):
            self.assertFalse(applications.InstalledApps(self.home).contains(appid))
            (self.home / ".local/share/flatpak/app" / appid / "current/active").mkdir(parents=True)
            self.assertTrue(applications.InstalledApps(self.home).contains(appid))

    def test_desktop_entry_requires_executable(self):
        from unittest.mock import patch
        desktop = self.home / ".local/share/applications"
        desktop.mkdir(parents=True)
        (desktop / "notes.desktop").write_text("[Desktop Entry]\nType=Application\nName=Notes\nExec=notes-app %U\n")
        with patch("applications.os.environ", {"XDG_DATA_DIRS": str(self.home / "empty")}), patch("shutil.which", side_effect=lambda name: "/bin/notes-app" if name == "notes-app" else None):
            self.assertTrue(applications.InstalledApps(self.home).contains("Notes"))
        with patch("applications.os.environ", {"XDG_DATA_DIRS": str(self.home / "empty")}), patch("shutil.which", return_value=None):
            self.assertFalse(applications.InstalledApps(self.home).contains("Notes"))

    def test_groups_native_and_flatpak_without_borg_state(self):
        catalog = applications.catalog(self.config, self.home)
        firefox = next(e for e in catalog if e["id"] == "firefox")
        self.assertEqual(firefox["formats"], "Native + Flatpak")
        self.assertEqual(len(firefox["paths"]), 2)
        self.assertFalse(any(".config/borg" in e["paths"] for e in catalog))

    def test_selected_apps_control_sources(self):
        self.config.update(app_selection_mode="selected", selected_applications=["firefox"])
        sources = consumer.app_data_sources(self.config, str(self.home))
        self.assertEqual(set(sources), {str(self.home / ".mozilla"), str(self.home / ".var/app/org.mozilla.firefox")})
        self.assertNotIn(str(self.home / ".config/kritarc"), sources)

    def test_app_data_appearing_after_the_choice_is_flagged_for_review(self):
        # "selected" mode backs up only what was chosen; a folder created
        # later (e.g. a new app's config) must not be skipped silently.
        catalog = applications.catalog(self.config, self.home, include_unrecognized=True)
        ids = [entry["id"] for entry in catalog]
        self.config.update(app_selection_mode="selected", selected_applications=["firefox"], known_applications=ids)
        self.assertEqual(applications.unreviewed(self.config, catalog), [])
        (self.home / ".config/NewApp").mkdir()
        catalog = applications.catalog(self.config, self.home, include_unrecognized=True)
        self.assertEqual([entry["id"] for entry in applications.unreviewed(self.config, catalog)], ["path:.config/NewApp"])
        self.config["app_selection_mode"] = "all"  # everything is backed up anyway
        self.assertEqual(applications.unreviewed(self.config, catalog), [])

    def test_without_a_record_every_unselected_entry_is_reviewed_once(self):
        catalog = applications.catalog(self.config, self.home, include_unrecognized=True)
        self.config.update(app_selection_mode="selected", selected_applications=["firefox"])
        pending = {entry["id"] for entry in applications.unreviewed(self.config, catalog)}
        self.assertEqual(pending, {entry["id"] for entry in catalog} - {"firefox"})

    def test_legacy_keeps_all_coverage(self):
        self.assertIn(str(self.home / ".config"), consumer.app_data_sources(self.config, str(self.home)))

    def test_broad_folder_cannot_bypass_app_selection(self):
        self.config.update(app_selection_mode="selected", selected_applications=["firefox"])
        for folder in (self.home, self.home / ".config", self.home / ".config/kritarc"):
            self.assertIsNotNone(applications.selection_conflict(self.config, [str(folder)], self.home))
        self.assertIsNone(applications.selection_conflict(self.config, [str(self.home / "Documents")], self.home))

    def test_system_and_unknown_data_are_explicit(self):
        self.config.update(app_selection_mode="selected", selected_applications=["curated:SSH Keys", "path:.config/unrecognized"])
        self.assertEqual(set(consumer.app_data_sources(self.config, str(self.home))), {str(self.home / ".ssh"), str(self.home / ".config/unrecognized")})

    def test_default_catalog_does_not_promote_settings_files_to_apps(self):
        for index in range(50):
            (self.home / f".config/setting-{index}").write_text("setting")
        default = applications.catalog(self.config, self.home)
        self.assertTrue(all(entry["recognized"] for entry in default))
        self.assertFalse(any(entry["id"].startswith("path:.config/") for entry in default))
        advanced = applications.catalog(self.config, self.home, include_unrecognized=True)
        self.assertGreater(len(advanced), len(default) + 49)

    def test_app_ids_are_not_display_names(self):
        appid = "com.example.SimpleNotes"
        (self.home / f".var/app/{appid}").mkdir(parents=True)
        desktop = self.home / ".local/share/applications"
        desktop.mkdir(parents=True, exist_ok=True)
        (desktop / f"{appid}.desktop").write_text("[Desktop Entry]\nName=Simple Notes\n")
        entry = next(e for e in applications.catalog(self.config, self.home) if e["id"] == "path:.var/app/" + appid)
        self.assertEqual(entry["label"], "Simple Notes")
        self.assertEqual(applications.display_name("org.example.photo_viewer", self.home), "Photo Viewer")


if __name__ == "__main__":
    unittest.main()

"""The product is called Keep Backup wherever its name is shown: the
launcher, the store listing, window and dialog titles, the toolbar and
About. The app ID, the `keep` command and archive names stay as they are."""
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_STATE = tempfile.TemporaryDirectory()
os.environ["KEEP_CONFIG_PATH"] = str(Path(_STATE.name) / "config.json")
os.environ["XDG_STATE_HOME"] = str(Path(_STATE.name) / "state")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

APP = QApplication.instance() or QApplication([])

import main  # noqa: E402
import version  # noqa: E402

ROOT = Path(__file__).resolve().parent
NAME = "Keep Backup"


class AppName(unittest.TestCase):
    def test_one_name(self):
        self.assertEqual(version.APP_NAME, NAME)

    def test_launchers(self):
        for desktop in (ROOT / "packaging/keep.desktop", ROOT / "packaging/flatpak/io.github.tneohcl.Keep.desktop"):
            with self.subTest(desktop=desktop.name):
                self.assertIn(f"\nName={NAME}\n", desktop.read_text(encoding="utf-8"))

    def test_store_listing(self):
        metainfo = ET.parse(ROOT / "packaging/flatpak/io.github.tneohcl.Keep.metainfo.xml").getroot()
        self.assertEqual(metainfo.findtext("name"), NAME)
        self.assertEqual(metainfo.findtext("id"), "io.github.tneohcl.Keep")  # the ID does not change

    def test_listing_screenshots_are_in_the_repository(self):
        # Flathub fetches them from main; packaging/screenshots.sh makes them.
        metainfo = ET.parse(ROOT / "packaging/flatpak/io.github.tneohcl.Keep.metainfo.xml").getroot()
        urls = [image.text for image in metainfo.iter("image")]
        self.assertTrue(urls)
        prefix = "https://raw.githubusercontent.com/tneohcl/keep/main/"
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(url.startswith(prefix))
                self.assertTrue((ROOT / url[len(prefix):]).is_file())

    def test_window_toolbar_and_about(self):
        with patch.object(main.MainWindow, "refresh_status", lambda self: None):
            window = main.MainWindow()
        self.addCleanup(window.close)
        self.assertEqual(window.windowTitle(), NAME)
        self.assertEqual(window.findChild(QLabel, "keepAppTitle").text(), NAME)
        about = main.AboutDialog(window)
        self.addCleanup(about.close)
        self.assertEqual(about.windowTitle(), f"About {NAME}")
        self.assertIn(NAME, [label.text() for label in about.findChildren(QLabel)])

    def test_no_dialog_is_titled_with_the_short_name(self):
        pattern = re.compile(r'''(setWindowTitle\(|QMessageBox\.\w+\(\s*self,\s*|getText\(\s*self,\s*)"Keep[" ]''')
        for path in [ROOT / "main.py", *sorted((ROOT / "keep_ui").glob("*.py"))]:
            with self.subTest(path=path.name):
                self.assertEqual(pattern.findall(path.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()

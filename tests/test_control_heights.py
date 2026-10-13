"""Buttons and fields are never shorter than their own text needs.
Regression: style.qss's min-height is a hard minimum in px, so where a
window was short on room Qt squeezed buttons to 36 px around 22 pt text
that needs 49 (Cancel and Save selection in Choose Applications)."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_STATE = tempfile.TemporaryDirectory()
os.environ["KEEP_CONFIG_PATH"] = str(Path(_STATE.name) / "config.json")
os.environ["XDG_STATE_HOME"] = str(Path(_STATE.name) / "state")

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton  # noqa: E402

APP = QApplication.instance() or QApplication([])

from keep_backup.core import applications  # noqa: E402
from keep_backup.ui import main_window, recovery_access  # noqa: E402

CONTROLS = (QPushButton, QLineEdit, QComboBox)


def fake_catalog(config, home=None, include_unrecognized=False):
    return [{"id": name.lower(), "label": name, "category": "applications", "formats": "Flatpak",
             "recognized": True, "paths": [f".var/app/{name}"]} for name in ("Firefox", "Thunderbird", "Krita")]


class ControlHeights(unittest.TestCase):
    POINTS = 22

    @classmethod
    def setUpClass(cls):
        cls._font = QFont(APP.font())
        font = APP.font()
        font.setPointSize(cls.POINTS)
        APP.setFont(font)

    @classmethod
    def tearDownClass(cls):
        APP.setFont(cls._font)

    def setUp(self):
        with patch.object(main_window.MainWindow, "refresh_status", lambda self: None):
            self.window = main_window.MainWindow()
        self.addCleanup(self.window.close)

    def settle(self):
        for _ in range(4):
            APP.processEvents()

    def assert_not_squeezed(self, top):
        checked = 0
        for kind in CONTROLS:
            for control in top.findChildren(kind):
                if not control.isVisible() or control.maximumHeight() < 16777215:
                    continue  # hidden, or a height set on purpose (SameHeightRow)
                checked += 1
                needed = (control.heightForWidth(control.width()) if control.hasHeightForWidth()
                          else control.sizeHint().height())  # a settings row's height follows its wrapped text
                with self.subTest(control=f"{kind.__name__} {getattr(control, 'text', lambda: '')()!r}"):
                    self.assertGreaterEqual(control.height(), needed)
        self.assertGreater(checked, 0)

    def test_main_window_at_its_smallest(self):
        for page in (0, 1):
            self.window.pages.setCurrentIndex(page)
            self.window.resize(100, 100)  # as small as it allows
            self.window.show()
            self.settle()
            with self.subTest(page=page):
                self.assert_not_squeezed(self.window)

    def test_applications_dialog(self):
        with patch.object(applications, "catalog", fake_catalog), \
                patch.object(applications.InstalledApps, "entry_installed", lambda self, entry: True):
            dialog = main_window.ApplicationSelectionDialog({"app_selection_mode": "selected"},
                                                            Path(_STATE.name), self.window)
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.resize(100, 100)
        self.settle()
        self.assert_not_squeezed(dialog)

    def test_recovery_access_dialog(self):
        dialog = recovery_access.RecoveryAccessDialog(
            "/fixture/repo", {"available": True, "label": "Backup Drive", "type": "local"},
            {"encryption": {"mode": "repokey-blake2"}, "repository": {"id": "fixture-id"}}, 7,
            "Today at 9:00 AM", main_window.borg_env, lambda: None, self.window)
        self.addCleanup(dialog.close)
        dialog.show()
        self.settle()
        self.assert_not_squeezed(dialog)


class ControlHeightsAt14pt(ControlHeights):
    POINTS = 14


class ControlHeightsAtDefaultSize(unittest.TestCase):
    def test_default_sizes_are_unchanged(self):
        with patch.object(main_window.MainWindow, "refresh_status", lambda self: None):
            window = main_window.MainWindow()
        self.addCleanup(window.close)
        window.show()
        for _ in range(4):
            APP.processEvents()
        self.assertEqual(window.archive_actions_button.height(), 36)  # Manage, as before


if __name__ == "__main__":
    unittest.main()

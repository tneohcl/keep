"""Choose Applications at large text sizes. Regression: at 22 pt names
were cut off in their tiles ("Thunder" for Thunderbird), since the tiles
were sized before the apps were added, and the dialog opened at 760 x 560
with room for only a sliver of the first row."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_STATE = tempfile.TemporaryDirectory()
os.environ["KEEP_CONFIG_PATH"] = str(Path(_STATE.name) / "config.json")
os.environ["XDG_STATE_HOME"] = str(Path(_STATE.name) / "state")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from keep_backup.core import applications  # noqa: E402
from keep_backup.ui import main_window  # noqa: E402

NAMES = ["Firefox", "Thunderbird", "Krita", "GNU Image Manipulation Program", "LibreOffice", "Visual Studio Code"]


def fake_catalog(config, home=None, include_unrecognized=False):
    return [{"id": name.lower(), "label": name, "category": "applications", "formats": "Flatpak",
             "recognized": True, "paths": [f".var/app/{name}"]} for name in NAMES]


class ApplicationsDialog(unittest.TestCase):
    POINTS = 0  # the platform's default font size

    @classmethod
    def setUpClass(cls):
        cls._font = QFont(APP.font())
        if cls.POINTS:
            font = APP.font()
            font.setPointSize(cls.POINTS)
            APP.setFont(font)

    @classmethod
    def tearDownClass(cls):
        APP.setFont(cls._font)

    def setUp(self):
        with patch.object(main_window.MainWindow, "refresh_status", lambda self: None):
            self.window = main_window.MainWindow()  # loads the app's stylesheet, as in use
        self.addCleanup(self.window.close)
        with patch.object(applications, "catalog", fake_catalog), \
                patch.object(applications.InstalledApps, "entry_installed", lambda self, entry: True):
            self.dialog = main_window.ApplicationSelectionDialog({"app_selection_mode": "selected"},
                                                                 Path(_STATE.name), self.window)
        self.addCleanup(self.dialog.close)
        self.dialog.show()  # at the size it opens with
        for _ in range(3):
            APP.processEvents()

    def test_names_fit_their_tiles(self):
        items = self.dialog.items
        delegate = items.itemDelegate()
        metrics = items.fontMetrics()
        self.assertEqual(items.count(), len(NAMES))
        for row in range(items.count()):
            index = items.model().index(row, 0)
            _tile, _check, _icon, text = delegate.layout(items.visualRect(index))
            label, subtitle = delegate._texts(index)
            with self.subTest(label=label):
                for word in label.split():
                    self.assertLessEqual(metrics.horizontalAdvance(word), text.width(), word)
                height = sum(metrics.boundingRect(QRect(0, 0, text.width(), 10000), Qt.TextWordWrap, t).height()
                             for t in (label, subtitle) if t)
                self.assertLessEqual(height, text.height())

    def test_opens_with_room_for_a_row_of_tiles(self):
        items = self.dialog.items
        self.assertGreaterEqual(items.viewport().height(), items.gridSize().height())


    def test_nothing_overlaps(self):
        # At 22 pt the wrapped note needed more height than the dialog's
        # minimum allowed for, and the checkboxes were drawn over the tiles.
        layout = self.dialog.layout()
        previous = None
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is not None and not widget.isVisible():
                continue
            rect = item.geometry()
            if rect.height() == 0:
                continue
            if previous is not None:
                self.assertGreaterEqual(rect.top(), previous.bottom(), f"item {index} overlaps the one above")
            previous = rect


class ApplicationsDialogAt14pt(ApplicationsDialog):
    POINTS = 14


class ApplicationsDialogAt22pt(ApplicationsDialog):
    POINTS = 22


class ApplicationsDialogAtDefaultSize(ApplicationsDialog):
    def test_opens_at_its_usual_size(self):
        self.assertEqual(self.dialog.size().toTuple(), (760, 560))


if __name__ == "__main__":
    unittest.main()

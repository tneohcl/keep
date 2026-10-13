"""The search row above Restore's catalog and the Applications dialog's
list: Find…, Grid | List, Select all and Clear share one height (they were
40, 28 and 36 px), at any text size."""
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
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from keep_backup.ui import main_window  # noqa: E402


class SearchRowHeights(unittest.TestCase):
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

    def settle(self):
        for _ in range(3):
            APP.processEvents()

    def assert_one_height(self, search, switch, buttons):
        controls = [search, switch, *buttons]
        heights = {control.height() for control in controls}
        self.assertEqual(len(heights), 1, [(type(c).__name__, c.height()) for c in controls])
        for control in (search, *buttons):
            self.assertGreaterEqual(control.height(), control.sizeHint().height())  # never squeezed
        margins = switch.layout().contentsMargins()
        for segment in switch.buttons():  # the segments fill the switch, an even inset all round
            self.assertEqual(segment.height(), switch.height() - margins.top() - margins.bottom())

    def test_restore_catalog_row(self):
        with patch.object(main_window.MainWindow, "refresh_status", lambda self: None):
            window = main_window.MainWindow()
        self.addCleanup(window.close)
        window.pages.setCurrentIndex(1)
        window.resize(1200, 800)
        window.show()
        self.settle()
        for tab, picker in enumerate((window.apps_picker, window.folders_picker)):
            window.tabs.setCurrentIndex(tab)
            self.settle()
            with self.subTest(picker=picker.search.placeholderText()):
                self.assert_one_height(picker.search, picker.view_mode,
                                       (picker.select_all_button, picker.clear_selection_button))

    def test_applications_dialog_row(self):
        with patch.object(main_window.MainWindow, "refresh_status", lambda self: None):
            window = main_window.MainWindow()  # loads the app's stylesheet, as in use
        self.addCleanup(window.close)
        dialog = main_window.ApplicationSelectionDialog({}, Path(_STATE.name), window)
        self.addCleanup(dialog.close)
        dialog.show()
        self.settle()
        search = next(w for w in dialog.findChildren(main_window.QLineEdit) if w.isClearButtonEnabled())
        self.assert_one_height(search, dialog.view_mode, (dialog.select_all_button, dialog.clear_selection_button))


class SearchRowHeightsAt14pt(SearchRowHeights):
    POINTS = 14


class SearchRowHeightsAt22pt(SearchRowHeights):
    POINTS = 22


if __name__ == "__main__":
    unittest.main()

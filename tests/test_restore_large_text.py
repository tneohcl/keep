"""The Restore view at large text sizes. Regression: at 22 pt the app grid
collapsed to a sliver, the text under it ran into More options…, and the
backup names in the sidebar were cut off ("Backup Drive · ne")."""
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
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

APP = QApplication.instance() or QApplication([])

from keep_backup.ui import main_window  # noqa: E402
from keep_backup.ui.backup_list import DETAIL_ROLE  # noqa: E402

BACKUPS = [
    ("Today at 8:03 AM", "Backup Drive · newest", "keep-1"),
    ("Yesterday at 4:00 AM", "Backup Drive", "keep-2"),
    ("Saturday at 4:00 AM", "Backup Drive", "keep-3"),
    ("Wednesday at 4:00 AM", "Backup Drive", "keep-4"),
]
CATALOG = [
    main_window.CatalogEntry("Flatseal", None, [("a", "/a")], "flatseal", "applications"),
    main_window.CatalogEntry("GNU Image Manipulation Program", None, [("b", "/b")], "gimp", "applications"),
    main_window.CatalogEntry("Krita", None, [("c", "/c")], "krita", "applications"),
    main_window.CatalogEntry("mpv", None, [("d", "/d")], "mpv", "applications"),
    main_window.CatalogEntry("Spotify", None, [("e", "/e")], "spotify", "applications"),
    main_window.CatalogEntry("Desktop Look & Feel", None, [("f", "/f")], "preferences", "system"),
]


class RestoreView(unittest.TestCase):
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
            self.window = main_window.MainWindow()
        self.addCleanup(self.window.close)
        self.window.backup_list_panel.set_backups(BACKUPS, 0)
        self.window.apps_picker.populate(CATALOG)
        self.window.pages.setCurrentIndex(1)
        self.window.resize(1200, 800)
        self.window.show()
        for _ in range(3):
            APP.processEvents()

    def test_the_catalog_has_room_for_a_row_of_tiles(self):
        picker = self.window.apps_picker
        tile = next(iter(picker._sections.values())).gridSize().height()
        self.assertGreaterEqual(picker._scroll.viewport().height(), tile)

    def test_text_under_the_catalog_is_not_squeezed(self):
        picker = self.window.apps_picker
        labels = [label for label in picker.findChildren(QLabel)
                  if label.isVisible() and label.wordWrap() and not picker._scroll.isAncestorOf(label)]
        self.assertTrue(labels)
        for label in labels:
            with self.subTest(label=label.text()):
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        more = next(b for b in picker.findChildren(QPushButton) if b.text() == "More options…")
        bottom = max(label.mapTo(picker, label.rect().bottomLeft()).y() for label in labels)
        self.assertLess(bottom, more.mapTo(picker, more.rect().topLeft()).y())

    def test_no_sideways_scrolling(self):
        page = self.window.restore_page
        self.assertLessEqual(page.width(), page.parentWidget().width())

    def test_tile_names_fit_their_tiles(self):
        # "GNU Image Manipulation Program": at 14 pt "Manipulation" was
        # wider than the fixed 210 px tile ("Manipulatior"); at 22 pt the
        # name ran out of the tile.
        for section in self.window.apps_picker._sections.values():
            delegate = section.itemDelegate()
            for row in range(section.count()):
                index = section.model().index(row, 0)
                _tile, _check, _icon, text = delegate.layout(section.visualRect(index))
                metrics = section.fontMetrics()
                label, subtitle = delegate._texts(index)
                with self.subTest(label=label):
                    for word in label.split():
                        self.assertLessEqual(metrics.horizontalAdvance(word), text.width(), word)
                    height = sum(metrics.boundingRect(QRect(0, 0, text.width(), 10000), Qt.TextWordWrap, t).height()
                                 for t in (label, subtitle) if t)
                    self.assertLessEqual(height, text.height())

    def test_backup_names_are_never_cut_off(self):
        backups = self.window.backup_list_panel.list
        metrics = backups.fontMetrics()
        for row in range(backups.count()):
            index = backups.model().index(row, 0)
            rect = backups.visualRect(index)
            width = rect.width() - 24  # the row's text inset
            needed = sum(metrics.boundingRect(QRect(0, 0, width, 10000), Qt.TextWordWrap, text).height()
                         for text in (index.data(Qt.DisplayRole), index.data(DETAIL_ROLE)))
            with self.subTest(row=index.data(Qt.DisplayRole)):
                self.assertGreaterEqual(rect.height(), needed + 14)


class TextSizeChangeWhileOpen(unittest.TestCase):
    def test_the_catalog_follows_a_text_size_change(self):
        original = QFont(APP.font())
        self.addCleanup(APP.setFont, original)
        with patch.object(main_window.MainWindow, "refresh_status", lambda self: None):
            window = main_window.MainWindow()
        self.addCleanup(window.close)
        window.apps_picker.populate(CATALOG)
        before = window.apps_picker._scroll.minimumHeight()
        font = APP.font()
        font.setPointSize(22)
        APP.setFont(font)
        APP.processEvents()
        self.assertGreater(window.apps_picker._scroll.minimumHeight(), before)


class WindowMinimumSize(unittest.TestCase):
    def test_the_restore_page_sets_the_minimum_from_the_start(self):
        # The page measured itself unstyled while hidden in its scroll area,
        # so until Restore was first shown the window could be made smaller
        # than the page (758 x 473 instead of 882 x 509 at the default size).
        with patch.object(main_window.MainWindow, "refresh_status", lambda self: None):
            window = main_window.MainWindow()
        self.addCleanup(window.close)
        window.show()
        for _ in range(3):
            APP.processEvents()
        first = window.minimumSizeHint()
        for page in (1, 0):  # Restore, then back to Status
            window.pages.setCurrentIndex(page)
            for _ in range(3):
                APP.processEvents()
        self.assertEqual(first, window.minimumSizeHint())


class RestoreViewAt14pt(RestoreView):
    POINTS = 14


class RestoreViewAt22pt(RestoreView):
    POINTS = 22


class RestoreViewAtDefaultSize(RestoreView):
    def test_tiles_keep_their_width(self):
        for section in self.window.apps_picker._sections.values():
            self.assertEqual(section.gridSize().width(), 210)

    def test_rows_stay_two_lines_tall(self):
        backups = self.window.backup_list_panel.list
        line = backups.fontMetrics().height()
        for row in range(backups.count()):
            self.assertEqual(backups.visualRect(backups.model().index(row, 0)).height(), line * 2 + 16)

    def test_the_page_does_not_scroll(self):
        scroll = self.window.restore_page.parentWidget().parentWidget()
        bar = getattr(scroll, "verticalScrollBar", None)
        self.assertFalse(bar is not None and bar().isVisible())


if __name__ == "__main__":
    unittest.main()

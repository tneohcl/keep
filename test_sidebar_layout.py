"""The Status | Restore switch heads the sidebar, as wide as the cards under
it, the Restore sidebar keeps its heading at the top when there are no
backups yet, and a short window scrolls the Status sidebar rather than clip
its rows."""
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
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget  # noqa: E402

APP = QApplication.instance() or QApplication([])

import main  # noqa: E402
from keep_ui.backup_list import BackupListPanel  # noqa: E402


def span(widget, window):
    left = widget.mapTo(window, widget.rect().topLeft()).x()
    return left, left + widget.width()


class SidebarSwitch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(main.MainWindow, "refresh_status", lambda self: None):
            cls.window = main.MainWindow()
        cls.window.resize(1100, 720)
        cls.window.show()
        APP.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def test_switch_heads_the_sidebar_not_the_toolbar(self):
        toolbar = self.window.findChild(QWidget, "keepToolbar")
        self.assertFalse(toolbar.isAncestorOf(self.window.view_switch))

    def test_switch_is_as_wide_as_the_settings_card(self):
        self.window.pages.setCurrentIndex(0)
        APP.processEvents()
        card = self.window.backup_panel.findChild(QFrame, "odcsSettingsList")
        self.assertEqual(span(self.window.view_switch, self.window), span(card, self.window))
        first, second = self.window.view_switch.buttons()
        self.assertLessEqual(abs(first.width() - second.width()), 1)

    def test_switch_is_as_wide_as_the_backup_list(self):
        self.window.backup_list_panel.set_backups([("Today at 4:00 AM", "TITAN-i · newest", "keep-a")], 0)
        self.window.pages.setCurrentIndex(1)
        APP.processEvents()
        backups = self.window.backup_list_panel.list
        self.assertEqual(span(self.window.view_switch, self.window), span(backups, self.window))
        self.window.pages.setCurrentIndex(0)


class EmptyRestoreSidebar(unittest.TestCase):
    def panel(self, rows):
        holder = QWidget()
        holder.resize(300, 600)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        panel = BackupListPanel()
        layout.addWidget(panel)
        panel.set_backups(rows, 0 if rows else -1)
        holder.show()
        APP.processEvents()
        self.addCleanup(holder.close)
        return panel

    def test_no_backups_keeps_the_heading_at_the_top(self):
        panel = self.panel([])
        heading = panel.layout().itemAt(0).widget()
        # Each label only as tall as its text: with the list hidden they used to
        # share the whole height (~200 px each), text centred, a gap on top.
        self.assertEqual(heading.y(), panel.layout().contentsMargins().top())
        self.assertEqual(heading.height(), heading.sizeHint().height())
        self.assertLess(panel.empty.y(), heading.y() + heading.height() + 20)   # the note right under it

    def test_backups_fill_the_height(self):
        panel = self.panel([(f"Backup {n}", "detail", f"keep-{n}") for n in range(3)])
        self.assertGreater(panel.list.height(), 400)


class ShortStatusSidebar(unittest.TestCase):
    """Large text or a short screen: the rows keep their full height, wrapped
    values included, and the sidebar scrolls. Regression: the rows were
    squeezed and the second line of Recovery access was clipped; then, at
    14 pt on Windows, a long host name widened the cards past the column."""

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
        with patch.object(main.MainWindow, "refresh_status", lambda self: None):
            self.window = main.MainWindow()
        self.addCleanup(self.window.close)
        panel = self.window.backup_panel
        panel.destination_choice.setValue("NAS share · Synology-DS920plus-Living-Room · /volume1/backups")
        panel.recovery_access_row.setValue("Tested · not yet verified · 2 to confirm on this computer")
        self.window.pages.setCurrentIndex(0)
        self.window.resize(1000, 200)  # as short as the window allows
        self.window.show()
        APP.processEvents()

    def rows(self):
        panel = self.window.backup_panel
        return panel.plan_panel.rows + panel.recovery_panel.rows

    def test_rows_keep_their_full_height(self):
        for row in self.rows():
            with self.subTest(row=row.label()):
                self.assertGreaterEqual(row.height(), row.heightForWidth(row.width()))
                self.assertGreaterEqual(row._value.height(), row._value.heightForWidth(row._value.width()))

    def test_a_wrapped_value_really_wraps(self):
        value = self.window.backup_panel.recovery_access_row._value
        self.assertGreater(value.heightForWidth(value.width()), 1.5 * value.fontMetrics().lineSpacing())

    def test_the_sidebar_scrolls_to_its_last_row(self):
        scroll = self.window.status_sidebar
        self.assertTrue(scroll.verticalScrollBar().isVisible())
        self.assertTrue(scroll.isAncestorOf(self.window.backup_panel))
        last = self.window.backup_panel.recovery_access_row
        scroll.ensureWidgetVisible(last, 0, 0)
        APP.processEvents()
        bottom = last.mapTo(scroll.viewport(), last.rect().bottomLeft()).y()
        self.assertLessEqual(bottom, scroll.viewport().height())

    def test_cards_stay_as_wide_as_the_switch_beside_a_scroll_bar(self):
        card = self.window.backup_panel.findChild(QFrame, "odcsSettingsList")
        self.assertEqual(span(card, self.window), span(self.window.view_switch, self.window))
        bar = self.window.status_sidebar.verticalScrollBar()
        self.assertGreaterEqual(bar.mapTo(self.window, bar.rect().topLeft()).x(), span(card, self.window)[1])

    def test_margin_returns_when_the_window_grows(self):
        bar = self.window.status_sidebar.verticalScrollBar()
        self.window.resize(1000, self.window.height() + bar.maximum() + 100)  # room for all of it
        APP.processEvents()
        card = self.window.backup_panel.findChild(QFrame, "odcsSettingsList")
        self.assertFalse(self.window.status_sidebar.verticalScrollBar().isVisible())
        self.assertEqual(self.window.backup_panel.layout().contentsMargins().right(), 16)
        self.assertEqual(span(card, self.window), span(self.window.view_switch, self.window))

    def test_no_empty_space_below_the_last_row(self):
        panel = self.window.backup_panel
        self.assertLessEqual(panel.height(), max(panel.heightForWidth(panel.width()),
                                                 self.window.status_sidebar.viewport().height()))

    def test_no_sideways_scrolling(self):
        scroll = self.window.status_sidebar
        self.assertFalse(scroll.horizontalScrollBar().isVisible())
        self.assertLessEqual(self.window.backup_panel.width(), scroll.viewport().width())


class ShortStatusSidebarAt14pt(ShortStatusSidebar):
    POINTS = 14


class ShortStatusSidebarAt22pt(ShortStatusSidebar):
    POINTS = 22


if __name__ == "__main__":
    unittest.main()

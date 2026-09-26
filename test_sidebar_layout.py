"""The Status | Restore switch heads the sidebar, as wide as the cards under
it, and the Restore sidebar keeps its heading at the top when there are no
backups yet."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_STATE = tempfile.TemporaryDirectory()
os.environ["KEEP_CONFIG_PATH"] = str(Path(_STATE.name) / "config.json")
os.environ["XDG_STATE_HOME"] = str(Path(_STATE.name) / "state")

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


if __name__ == "__main__":
    unittest.main()

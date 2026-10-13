"""Recovery access at large text sizes. Regression: at 22 pt the dialog
opened 600 px wide around ~820 px of content and scrolled sideways, since
a check box can't wrap its text."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_STATE = tempfile.TemporaryDirectory()
os.environ["KEEP_CONFIG_PATH"] = str(Path(_STATE.name) / "config.json")
os.environ["XDG_STATE_HOME"] = str(Path(_STATE.name) / "state")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QFont, QMouseEvent  # noqa: E402
from PySide6.QtCore import QEvent, QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea  # noqa: E402

APP = QApplication.instance() or QApplication([])

from keep_backup.ui import main_window, recovery_access  # noqa: E402


class RecoveryAccess(unittest.TestCase):
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
        self.dialog = recovery_access.RecoveryAccessDialog(
            "/fixture/repo", {"available": True, "label": "Backup Drive", "type": "local"},
            {"encryption": {"mode": "repokey-blake2"}, "repository": {"id": "fixture-id"}}, 7,
            "Today at 9:00 AM", main_window.borg_env, lambda: None, self.window)
        self.addCleanup(self.dialog.close)
        self.dialog.show()  # at the size it opens with
        for _ in range(3):
            APP.processEvents()
        self.scroll = self.dialog.findChild(QScrollArea)

    def test_never_scrolls_sideways(self):
        content = self.scroll.widget()
        self.assertFalse(self.scroll.horizontalScrollBar().isVisible())
        self.assertLessEqual(content.width(), self.scroll.viewport().width())

    def test_wrapped_text_gets_its_full_height(self):
        content = self.scroll.widget()
        for label in content.findChildren(QLabel):
            if label.isVisible() and label.wordWrap():
                with self.subTest(text=label.text()[:40]):
                    self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        for key, box in self.dialog.confirm_boxes.items():
            with self.subTest(box=key):
                self.assertGreaterEqual(box.height(), box.heightForWidth(box.width()))

    def test_check_boxes_keep_their_text_and_toggle_from_it(self):
        box = self.dialog.confirm_boxes["passphrase_saved"]
        self.assertEqual(box.text(), recovery_access.CONFIRM_TEXT["passphrase_saved"])
        before = box.isChecked()
        point = QPointF(box.width() - 4, box.height() / 2)  # on the text, far from the box
        for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            APP.sendEvent(box, QMouseEvent(kind, point, box.mapToGlobal(point), Qt.LeftButton,
                                           Qt.LeftButton if kind == QEvent.MouseButtonPress else Qt.NoButton,
                                           Qt.NoModifier))
        self.assertNotEqual(box.isChecked(), before)
        box.setChecked(before)


class RecoveryAccessAt14pt(RecoveryAccess):
    POINTS = 14


class RecoveryAccessAt22pt(RecoveryAccess):
    POINTS = 22


if __name__ == "__main__":
    unittest.main()

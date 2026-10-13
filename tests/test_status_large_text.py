"""The Status page's "Can you get your files back?" facts at large text
sizes. Regression: at 22 pt "Stored data is readable and consistent"
needed three lines and its row had room for two (odcs-ui 0.4.9)."""
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


class StatusFacts(unittest.TestCase):
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

    def test_descriptions_are_never_clipped(self):
        with patch.object(main_window.MainWindow, "refresh_status", lambda self: None):
            window = main_window.MainWindow()
        self.addCleanup(window.close)
        window.pages.setCurrentIndex(0)
        window.show()
        for width in (1000, 1100, 1200, 1400):
            window.resize(width, 800)
            for _ in range(3):
                APP.processEvents()
            for key, fact in window.status_facts.facts.items():
                label = fact["description"]
                with self.subTest(width=width, fact=key):
                    self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))


class StatusFactsAt14pt(StatusFacts):
    POINTS = 14


class StatusFactsAt22pt(StatusFacts):
    POINTS = 22


if __name__ == "__main__":
    unittest.main()

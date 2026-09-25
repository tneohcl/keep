"""Shared theme resolution, contrast and live switching regression checks."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QPushButton
import theming

class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_text_tokens_meet_wcag_aa(self):
        # 2026-09-25 UI audit: every readable TEXT_* token >= 4.5:1 on the
        # surfaces text actually sits on (TEXT_DISABLED is WCAG-exempt).
        import themes
        def ratio(a, b):
            la, lb = theming.luminance(QColor(a)), theming.luminance(QColor(b))
            return (max(la, lb) + .05) / (min(la, lb) + .05)
        for name, theme in themes.THEMES.items():
            for fg in ("TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_TERTIARY", "TEXT_READONLY", "TEXT_CAPTION"):
                for bg in ("BG_WINDOW", "BG_PANEL", "BG_FIELD"):
                    with self.subTest(theme=name, fg=fg, bg=bg):
                        self.assertGreaterEqual(ratio(theme[fg], theme[bg]), 4.5)

    def test_manual_theme_choice_overrides_desktop(self):
        light_desktop = QPalette(self.app.palette())
        light_desktop.setColor(QPalette.Window, QColor("#eeeeee"))
        self.assertTrue(theming.resolve_dark(light_desktop, "dark"))
        self.assertFalse(theming.resolve_dark(light_desktop, "light"))
        self.assertFalse(theming.resolve_dark(light_desktop, "system"))
        self.assertFalse(theming.resolve_dark(light_desktop, "nonsense"))
        self.assertNotEqual(theming.stylesheet(light_desktop, "dark"), theming.stylesheet(light_desktop, "light"))

    def test_theme_round_trip(self):
        original = self.app.palette()
        results = []
        try:
            theming.install(self.app)
            for background in ("#eeeeee", "#202020", "#eeeeee"):
                palette = QPalette(original)
                palette.setColor(QPalette.Window, QColor(background))
                self.app.setPalette(palette)
                self.app.processEvents()
                results.append(self.app.styleSheet())
                self.assertNotIn("$", results[-1])
                self.assertEqual(results[-1], theming.stylesheet(palette))
            self.assertNotEqual(results[0], results[1])
            self.assertEqual(results[0], results[2])
        finally:
            self.app.setPalette(original)

    def test_accent_contrast(self):
        for color in ("#3daee9", "#ffff00", "#123456"):
            palette = QPalette()
            palette.setColor(QPalette.Accent, QColor(color))
            values = theming.tokens(palette)
            a, b = sorted((theming.luminance(QColor(values["ACCENT"])), theming.luminance(QColor(values["TEXT_ON_ACCENT"]))))
            self.assertGreaterEqual((b + .05) / (a + .05), 4.5)

    def test_initial_install_survives_reentrant_palette_event(self):
        # Isolate first installation from the QApplication used by other tests.
        import subprocess
        import sys
        script = """
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget
import theming
app = QApplication([])
class Probe(QWidget):
    def changeEvent(self, event):
        if event.type() == QEvent.PaletteChange:
            theming.install(app)
        super().changeEvent(event)
probe = Probe()
probe.show()
theming.install(app)
assert app.styleSheet()
assert len(app.findChildren(theming.ThemeController)) == 1
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_roles_leave_style_centralized(self):
        button = QPushButton("Save")
        theming.role(button, "primary")
        self.assertEqual(button.property("role"), "primary")
        self.assertEqual(button.styleSheet(), "")
        theming.role(button, "")
        self.assertEqual(button.property("role"), "")

if __name__ == "__main__":
    unittest.main()

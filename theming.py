"""Shared QSS loading and live system-palette integration for Keep."""
from pathlib import Path
from string import Template
from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QColor, QPalette
import themes


def luminance(color):
    values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in (color.redF(), color.greenF(), color.blueF())]
    return sum(a * b for a, b in zip(values, (.2126, .7152, .0722)))


# Same choices and labels as VeloCoder's theme setting (ODCS apps share one
# vocabulary). "system" follows the desktop palette live, as before.
THEME_CHOICES = [("dark", "Dark"), ("light", "Light"), ("system", "Match System")]


def resolve_dark(palette, choice="system"):
    if choice == "dark":
        return True
    if choice == "light":
        return False
    return luminance(palette.color(QPalette.Window)) < .5


def tokens(palette, choice="system"):
    dark = resolve_dark(palette, choice)
    values = dict(themes.DARK if dark else themes.LIGHT)
    accent = palette.color(getattr(QPalette, "Accent", QPalette.Highlight))
    if not accent.isValid():
        accent = QColor(values["ACCENT"])
    lightness = luminance(accent)
    white_contrast = 1.05 / (lightness + .05)
    black_contrast = (lightness + .05) / .05
    values["CHEVRON_PATH"] = Path(__file__).with_name("chevron-down.svg").as_posix()
    values["CHECK_PATH"] = Path(__file__).with_name("check.svg").as_posix()
    values.update(ACCENT=accent.name(), ACCENT_HOVER=accent.lighter(118).name(),
                  ACCENT_PRESSED=accent.darker(115).name(),
                  TEXT_ON_ACCENT="#ffffff" if white_contrast >= black_contrast else "#000000",
                  ERROR="#ff9a8e" if dark else "#b42318")
    return values


def stylesheet(palette, choice="system"):
    return Template(Path(__file__).with_name("style.qss").read_text(encoding="utf-8")).substitute(tokens(palette, choice))


class ThemeController(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.busy = False
        self.current = None
        self.choice = "system"
        app.installEventFilter(self)

    def set_choice(self, choice):
        self.choice = choice if choice in dict(THEME_CHOICES) else "system"
        self.apply()

    def apply(self):
        if self.busy:
            return
        self.busy = True
        try:
            qss = stylesheet(self.app.palette(), self.choice)
            if qss != self.current:
                self.current = qss
                self.app.setStyleSheet(qss)
        finally:
            self.busy = False

    def eventFilter(self, obj, event):
        if obj is self.app and event.type() in (QEvent.ApplicationPaletteChange, QEvent.PaletteChange):
            self.apply()
        return False


def install(app, choice=None):
    if not hasattr(app, "_keep_theme"):
        app._keep_theme = ThemeController(app)
    if choice is not None:
        app._keep_theme.set_choice(choice)
    else:
        app._keep_theme.apply()


def role(widget, value):
    if widget.property("role") != value:
        widget.setProperty("role", value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

"""Keep's theming: a thin adapter over the bundled odcs-ui (_vendor/odcs_ui).

odcs-ui owns the tokens, the desktop-accent logic, $TOKEN rendering and the
shared base stylesheet; this module keeps the small API the rest of Keep and
its tests already use (install, role, tokens, stylesheet, resolve_dark, ...).
Stylesheets load in order: odcs-ui base.qss, then Keep's style.qss on top.
"""
from pathlib import Path

from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QPalette

from keep_backup.ui._vendor.odcs_ui import color as odcs_color
from keep_backup.ui._vendor.odcs_ui import theming as odcs_theming

ASSETS = Path(__file__).with_name("assets")
KEEP_QSS = ASSETS / "style.qss"

# Same choices and labels in every ODCS app (odcs_ui.theming.THEME_CHOICES).
THEME_CHOICES = odcs_theming.THEME_CHOICES


def luminance(color):
    """WCAG relative luminance of a QColor."""
    return odcs_color.luminance(color.name())


def resolve_dark(palette, choice="system"):
    if choice == "dark":
        return True
    if choice == "light":
        return False
    return luminance(palette.color(QPalette.Window)) < .5


def tokens(palette, choice="system"):
    """odcs-ui tokens for the resolved theme and desktop accent, plus Keep's icon paths."""
    name = "dark" if resolve_dark(palette, choice) else "light"
    return odcs_theming.build_tokens(name, palette, {
        "CHEVRON_PATH": (ASSETS / "chevron-down.svg").as_posix(),
        "CHECK_PATH": (ASSETS / "check.svg").as_posix(),
    })


def stylesheet(palette, choice="system"):
    qss = odcs_theming.BASE_QSS.read_text(encoding="utf-8") + "\n" + KEEP_QSS.read_text(encoding="utf-8")
    return odcs_theming.render(qss, tokens(palette, choice))


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
            values = tokens(self.app.palette(), self.choice)
            # odcs widgets that paint their own colours (status icons) read these.
            odcs_theming._CURRENT.clear()
            odcs_theming._CURRENT.update(values)
            # Light or dark variant of the desktop's icon theme, before the
            # stylesheet below makes widgets redraw.
            odcs_theming.match_icon_theme("dark" if resolve_dark(self.app.palette(), self.choice) else "light")
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
        # Keyboard-only focus ring: style.qss matches [focusVisible="true"].
        odcs_theming.install_focus_visible(app)
    if choice is not None:
        app._keep_theme.set_choice(choice)
    else:
        app._keep_theme.apply()


def role(widget, value):
    odcs_theming.set_role(widget, value)

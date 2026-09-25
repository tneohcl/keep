"""Keep's theming: a thin adapter over the bundled odcs-ui (vendor/odcs_ui).

odcs-ui owns the tokens, the desktop-accent logic, $TOKEN rendering and the
shared base stylesheet; this module keeps the small API the rest of Keep and
its tests already use (install, role, tokens, stylesheet, resolve_dark, ...).
Stylesheets load in order: odcs-ui base.qss, then Keep's style.qss on top.
"""
import sys
from pathlib import Path

_VENDOR = Path(__file__).with_name("vendor")
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from PySide6.QtCore import QObject, QEvent  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402

from odcs_ui import color as odcs_color  # noqa: E402
from odcs_ui import theming as odcs_theming  # noqa: E402

KEEP_QSS = Path(__file__).with_name("style.qss")

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
        "CHEVRON_PATH": Path(__file__).with_name("chevron-down.svg").as_posix(),
        "CHECK_PATH": Path(__file__).with_name("check.svg").as_posix(),
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
    odcs_theming.set_role(widget, value)

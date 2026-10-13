"""Keep's theming: a thin adapter over the bundled odcs-ui (_vendor/odcs_ui).

odcs-ui owns the tokens, the desktop-accent logic, $TOKEN rendering and the
shared base stylesheet; this module keeps the small API the rest of Keep and
its tests already use (install, role, tokens, stylesheet, resolve_dark, ...).
Stylesheets load in order: odcs-ui base.qss, then Keep's style.qss on top.
"""
from pathlib import Path

import weakref

from PySide6.QtCore import QObject, QEvent, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QPushButton

from keep_backup.ui._vendor.odcs_ui import color as odcs_color
from keep_backup.ui._vendor.odcs_ui import theming as odcs_theming

ASSETS = Path(__file__).with_name("assets")
KEEP_QSS = ASSETS / "style.qss"

QWIDGETSIZE_MAX = (1 << 24) - 1  # Qt's "no maximum"; PySide6 doesn't export it

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


class ControlHeights(QObject):
    """Buttons and fields never shorter than their own text needs.

    style.qss's min-height is in px and Qt treats it as a hard minimum, so a
    window short on room squeezed buttons to 36 px around 22 pt text that
    needs 49. After the stylesheet styles a control, and when its font
    changes, its minimum height becomes its size hint: the same 36 px (40
    for fields) at normal sizes, more with larger text. Controls with a
    height set on purpose (SameHeightRow) or one that follows their wrapped
    text (settings rows) are left alone."""

    KINDS = (QPushButton, QLineEdit, QComboBox, QAbstractSpinBox)

    def __init__(self, app):
        super().__init__(app)
        self._pending = weakref.WeakSet()
        app.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Polish, QEvent.FontChange, QEvent.StyleChange) and isinstance(obj, self.KINDS):
            if not self._pending:
                QTimer.singleShot(0, self._fit)  # after the control has taken the change in
            self._pending.add(obj)
        return False

    def _fit(self):
        controls, self._pending = list(self._pending), weakref.WeakSet()
        for control in controls:
            try:
                if control.maximumHeight() < QWIDGETSIZE_MAX or control.hasHeightForWidth():
                    continue
                height = control.sizeHint().height()
                if control.minimumHeight() != height:
                    control.setMinimumHeight(height)
            except RuntimeError:  # deleted since
                continue


def install(app, choice=None):
    if not hasattr(app, "_keep_control_heights"):
        app._keep_control_heights = ControlHeights(app)
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

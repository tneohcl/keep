"""Keep status glyphs: filled success badges, distinct non-success states."""
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from odcs_ui.widgets import StatusIcon, StatusFacts
from odcs_ui.theming import current_tokens


class SuccessBadge(StatusIcon):
    def paintEvent(self, event):
        if self.state() != "ok":
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        size = float(self.width())
        green = QColor(current_tokens()["SUCCESS"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(green)
        painter.drawEllipse(QRectF(1, 1, size - 2, size - 2))
        # Choose the tick with the stronger contrast against the theme green.
        channels = [green.redF(), green.greenF(), green.blueF()]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        luminance = sum(c * w for c, w in zip(linear, (.2126, .7152, .0722)))
        ink = QColor("#ffffff" if luminance < .179 else "#102019")
        pen = QPen(ink, max(1.8, size * .075), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([QPointF(size * .28, size * .52), QPointF(size * .44, size * .67), QPointF(size * .73, size * .35)])
        painter.end()


class BadgeFacts(StatusFacts):
    def addFact(self, key, *args, **kwargs):
        super().addFact(key, *args, **kwargs)
        fact = self.facts[key]
        old = fact["icon"]
        badge = SuccessBadge(old.state(), 20)
        old.parentWidget().layout().replaceWidget(old, badge)
        old.hide()
        old.deleteLater()
        fact["icon"] = badge

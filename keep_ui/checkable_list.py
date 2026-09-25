"""Native checkable list with a full-cell mouse target, drawn as tiles.

Each item is a tile: [checkbox] [icon] [name / subtitle]. The checkbox sits
inside the tile beside what it controls, a checked tile is tinted, and the
whole tile toggles on click (Space toggles the current tile from the
keyboard). Geometry is identical in every state; only colours change
(odcs-ui DESIGN.md, Component states).
"""
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QStyle, QStyledItemDelegate

import theming  # noqa: F401  (puts the bundled vendor/odcs_ui on sys.path)
from odcs_ui.theming import current_tokens

LABEL_ROLE = Qt.UserRole + 3      # plain name, when the display text carries a 2nd line
SUBTITLE_ROLE = Qt.UserRole + 4

_FALLBACK = {
    "BG_FIELD": "#ffffff", "BG_PANEL": "#ffffff", "HOVER_BG": "#f0f2f5", "SELECTION_BG": "#d3e6f5",
    "BORDER": "#d1d5db", "ACCENT": "#1c72c4", "TEXT_ON_ACCENT": "#ffffff", "TEXT_PRIMARY": "#1a1d23",
    "TEXT_SECONDARY": "#565d6b", "TEXT_DISABLED": "#9ca3af",
}


def _colors():
    values = dict(_FALLBACK)
    values.update(current_tokens() or {})
    return values


class CheckTileDelegate(QStyledItemDelegate):
    PAD = 10
    CHECK = 16
    GAP = 10

    def __init__(self, view, icon_size=32, framed=True):
        super().__init__(view)
        self.view = view
        self.icon_size = icon_size
        self.framed = framed

    def layout(self, rect):
        """(tile, checkbox, icon, text) rects for a cell; the same in every state."""
        tile = QRect(rect).adjusted(2, 2, -2, -2)
        middle = tile.center().y()
        check = QRect(tile.left() + self.PAD, middle - self.CHECK // 2, self.CHECK, self.CHECK)
        icon = QRect(check.right() + 1 + self.GAP, middle - self.icon_size // 2, self.icon_size, self.icon_size)
        text = QRect(icon.right() + 1 + self.GAP, tile.top() + 4, 0, tile.height() - 8)
        text.setRight(tile.right() - self.PAD)
        return tile, check, icon, text

    @staticmethod
    def _texts(index):
        label = index.data(LABEL_ROLE)
        display = index.data(Qt.DisplayRole) or ""
        subtitle = index.data(SUBTITLE_ROLE)
        if not label:
            label, _, rest = display.partition("\n")
            subtitle = subtitle or rest
        return label, subtitle or ""

    def paint(self, painter, option, index):
        c = _colors()
        tile, check, icon_rect, text_rect = self.layout(option.rect)
        enabled = bool(option.state & QStyle.State_Enabled)
        checked = index.data(Qt.CheckStateRole) in (Qt.Checked, Qt.CheckState.Checked, 2)
        hover = bool(option.state & QStyle.State_MouseOver) and enabled
        focus = (bool(option.state & QStyle.State_HasFocus) and self.view.hasFocus()
                 and bool(self.view.property("focusVisible")))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        fill = c["SELECTION_BG"] if checked else (c["HOVER_BG"] if hover else (c["BG_FIELD"] if self.framed else None))
        edge = c["ACCENT"] if focus else (c["BORDER"] if self.framed else None)
        painter.setPen(QPen(QColor(edge), 1) if edge else Qt.NoPen)
        painter.setBrush(QColor(fill) if fill else Qt.NoBrush)
        painter.drawRoundedRect(QRectF(tile).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

        # Checkbox: accent fill + check when on; a 1 px outline when off.
        box = QRectF(check).adjusted(0.5, 0.5, -0.5, -0.5)
        if checked:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(c["ACCENT"] if enabled else c["TEXT_DISABLED"]))
            painter.drawRoundedRect(box, 3, 3)
            pen = QPen(QColor(c["TEXT_ON_ACCENT"]), 2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            s, x, y = box.width(), box.left(), box.top()
            path = QPainterPath(QPointF(x + s * .24, y + s * .52))
            path.lineTo(x + s * .43, y + s * .70)
            path.lineTo(x + s * .77, y + s * .32)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        else:
            painter.setPen(QPen(QColor(c["TEXT_SECONDARY"] if enabled else c["TEXT_DISABLED"]), 1))
            painter.setBrush(QColor(c["BG_FIELD"]))
            painter.drawRoundedRect(box, 3, 3)

        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(painter, icon_rect, Qt.AlignCenter, QIcon.Normal if enabled else QIcon.Disabled)

        label, subtitle = self._texts(index)
        painter.setPen(QColor(c["TEXT_PRIMARY"] if enabled else c["TEXT_DISABLED"]))
        painter.setFont(option.font)
        flags = Qt.AlignLeft | Qt.TextWordWrap
        if subtitle:
            # Stack name and subtitle as one block, centred: a name that
            # wraps to two lines pushes the subtitle down, never out.
            metrics = option.fontMetrics
            label_h = metrics.boundingRect(QRect(0, 0, text_rect.width(), 1000), flags, label).height()
            sub_h = metrics.boundingRect(QRect(0, 0, text_rect.width(), 1000), flags, subtitle).height()
            top = text_rect.top() + max(0, (text_rect.height() - label_h - 2 - sub_h) // 2)
            painter.drawText(QRect(text_rect.left(), top, text_rect.width(), label_h), flags, label)
            painter.setPen(QColor(c["TEXT_SECONDARY"] if enabled else c["TEXT_DISABLED"]))
            painter.drawText(QRect(text_rect.left(), top + label_h + 2, text_rect.width(), sub_h), flags, subtitle)
        else:
            painter.drawText(text_rect, flags | Qt.AlignVCenter, label)
        painter.restore()

    def sizeHint(self, option, index):
        grid = self.view.gridSize()
        return QSize(max(1, grid.width() - 4), max(1, grid.height() - 4)) if grid.isValid() else super().sizeHint(option, index)

    def editorEvent(self, event, model, option, index):
        # Mouse toggling is the list's (whole tile, CheckableListWidget);
        # the default delegate would also toggle where it *thinks* the box is.
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
            return True
        return super().editorEvent(event, model, option, index)  # Space / Select toggle


def apply_tile_mode(view, grid, font_metrics):
    """Grid: framed tiles in wrapping columns. List: compact rows."""
    view.setViewMode(QListWidget.ListMode)
    view.setFlow(QListWidget.LeftToRight)
    view.setWrapping(True)
    view.setResizeMode(QListWidget.Adjust)
    view.setUniformItemSizes(True)
    view.setMouseTracking(True)
    line = font_metrics.height()
    if grid:
        view.setIconSize(QSize(32, 32))
        view.setGridSize(QSize(210, max(64, line * 3 + 16)))  # room for a 2-line name + subtitle
        view.setItemDelegate(CheckTileDelegate(view, 32, framed=True))
    else:
        view.setIconSize(QSize(20, 20))
        view.setGridSize(QSize(280, max(36, line * 2 + 10)))
        view.setItemDelegate(CheckTileDelegate(view, 20, framed=False))
    view.setSpacing(0)
    view.update_cell_sizes()


class CheckableListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.setTextElideMode(Qt.ElideNone)
        self.setMouseTracking(True)
        self._pressed_item = None

    def update_cell_sizes(self):
        # Give the delegate the whole cell for word wrapping; uniform item
        # sizing could otherwise reuse the first short label's width.
        grid = self.gridSize()
        for index in range(self.count()):
            self.item(index).setSizeHint(QSize(max(1, grid.width() - 4), max(1, grid.height() - 4)))

    def mousePressEvent(self, event):
        self._pressed_item = self.itemAt(event.position().toPoint()) if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        toggle = (event.button() == Qt.LeftButton and item is not None
                  and item is self._pressed_item and bool(item.flags() & Qt.ItemIsEnabled)
                  and bool(item.flags() & Qt.ItemIsUserCheckable))
        before = item.checkState() if toggle else None
        super().mouseReleaseEvent(event)
        # The tile delegate leaves mouse toggling to us, so a click anywhere
        # on the tile (checkbox, icon or name) toggles it exactly once.
        if toggle and item.checkState() == before:
            item.setCheckState(Qt.Unchecked if before == Qt.Checked else Qt.Checked)
        self._pressed_item = None

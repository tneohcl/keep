"""Native checkable list with a full-cell mouse target."""
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QListWidget, QAbstractItemView


class CheckableListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.setTextElideMode(Qt.ElideNone)
        self._pressed_item = None

    def update_cell_sizes(self):
        # Give the native delegate the whole cell width for word wrapping.
        # Otherwise uniform item sizing can reuse the first short label's width.
        grid = self.gridSize()
        for index in range(self.count()):
            self.item(index).setSizeHint(QSize(max(1, grid.width() - 8), max(1, grid.height() - 4)))

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
        # The native delegate handles checkbox clicks; only toggle ourselves
        # when it didn't, so icon/label clicks work without a double toggle.
        if toggle and item.checkState() == before:
            item.setCheckState(Qt.Unchecked if before == Qt.Checked else Qt.Checked)
        self._pressed_item = None

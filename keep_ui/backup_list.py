"""Restore view sidebar: the stored backups, newest first, by friendly name.

Selecting a row chooses the backup to restore from. The row shows when it
was made ("Today at 8:51 AM") and where ("TITAN-i · newest"); the Borg
archive name stays out of sight (tooltip), since it is an identifier, not
something a person reads. Selection is colour-only (odcs-ui DESIGN.md).
"""
from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QLabel, QListWidget, QListWidgetItem, QStyle,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

import theming  # noqa: F401  (puts the bundled vendor/odcs_ui on sys.path)
from odcs_ui.theming import current_tokens, set_role, set_surface

DETAIL_ROLE = Qt.UserRole + 1
ARCHIVE_ROLE = Qt.UserRole + 2


class _BackupRowDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        c = current_tokens()
        rect = QRect(option.rect)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        view = self.parent()
        focus = bool(option.state & QStyle.State_HasFocus) and view.hasFocus() and bool(view.property("focusVisible"))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        fill = c.get("SELECTION_BG") if selected else (c.get("HOVER_BG") if hover else None)
        if fill:
            painter.fillRect(rect, QColor(fill))
        if focus:
            painter.setPen(QPen(QColor(c.get("ACCENT", "#1c72c4")), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(rect).adjusted(1, 1, -1, -1))
        if index.row() < index.model().rowCount() - 1:  # separators between rows only
            painter.setPen(QPen(QColor(c.get("BORDER", "#d1d5db")), 1))
            painter.drawLine(rect.left() + 12, rect.bottom(), rect.right(), rect.bottom())
        text = rect.adjusted(12, 7, -12, -7)
        metrics = option.fontMetrics
        painter.setFont(option.font)
        painter.setPen(QColor(c.get("TEXT_PRIMARY", "#1a1d23")))
        painter.drawText(QRect(text.left(), text.top(), text.width(), metrics.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, index.data(Qt.DisplayRole) or "")
        painter.setPen(QColor(c.get("TEXT_SECONDARY", "#565d6b")))
        painter.drawText(QRect(text.left(), text.top() + metrics.height() + 2, text.width(), metrics.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, index.data(DETAIL_ROLE) or "")
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(200, option.fontMetrics.height() * 2 + 16)


class BackupListPanel(QWidget):
    """Sidebar for the Restore view. `chosen(row)` fires on a person's choice."""

    chosen = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("keepSidebar")
        set_surface(self, "window")
        self.setFixedWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(6)
        heading = QLabel("Restore from")
        heading.setObjectName("odcsGroupHeading")
        layout.addWidget(heading)
        self.list = QListWidget()
        self.list.setObjectName("keepBackupList")
        self.list.setAccessibleName("Stored backups")
        self.list.setItemDelegate(_BackupRowDelegate(self.list))
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self.list, 1)
        self.empty = QLabel("No backups yet. Back up now, then come back here to restore.")
        self.empty.setWordWrap(True)
        set_role(self.empty, "secondary")
        layout.addWidget(self.empty)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        set_role(self.caption, "caption")
        layout.addWidget(self.caption)
        self._syncing = False
        self.set_backups([], -1)

    def set_backups(self, rows, current):
        """rows: [(label, detail, archive_name)], newest first."""
        self._syncing = True
        try:
            self.list.clear()
            for label, detail, archive in rows:
                item = QListWidgetItem(label)
                item.setData(DETAIL_ROLE, detail)
                item.setData(ARCHIVE_ROLE, archive)
                item.setToolTip(archive)
                item.setData(Qt.AccessibleTextRole, f"{label}, {detail}")
                self.list.addItem(item)
            if 0 <= current < self.list.count():
                self.list.setCurrentRow(current)
        finally:
            self._syncing = False
        self.list.setVisible(bool(rows))
        self.empty.setVisible(not rows)

    def select_row(self, row):
        self._syncing = True
        try:
            self.list.setCurrentRow(row)
        finally:
            self._syncing = False

    def set_caption(self, text):
        self.caption.setText(text)
        self.caption.setVisible(bool(text))

    def _on_row_changed(self, row):
        if not self._syncing and row >= 0:
            self.chosen.emit(row)

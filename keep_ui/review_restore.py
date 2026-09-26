"""Review restore…: shows exactly what will be written and where, before
anything is. The default is always a new folder, so nothing is overwritten;
replacing live app data stays a separate, explicit step."""
import os
import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QScrollArea)

import theming  # noqa: F401  (puts the bundled vendor/odcs_ui on sys.path)
from odcs_ui.theming import set_role
from odcs_ui.widgets import StatusIcon

SHOWN_ITEMS = 10


def display_path(path, home=None):
    home = str(home or Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


class ReviewRestoreDialog(QDialog):
    def __init__(self, items, archive, destination, home=None, parent=None):
        super().__init__(parent)
        self.destination = destination
        self.home = home
        self.setWindowTitle("Review restore")
        self.resize(min(560, int(self.screen().availableGeometry().width() * .9)), 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(10)
        title = QLabel(f"Restore {len(items)} item{'s' if len(items) != 1 else ''} from {archive}")
        font = title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 3)
        title.setFont(font)
        title.setWordWrap(True)
        layout.addWidget(title)

        listing = QFrame()
        listing.setObjectName("recoveryTable")
        listing.setAttribute(Qt.WA_StyledBackground, True)
        rows = QVBoxLayout(listing)
        rows.setContentsMargins(12, 8, 12, 8)
        rows.setSpacing(4)
        for name in items:
            label = QLabel(name)
            label.setWordWrap(True)
            rows.addWidget(label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(listing)
        scroll.setMinimumHeight(80)
        layout.addWidget(scroll, 1)

        where = QHBoxLayout()
        where.setSpacing(8)
        where_label = QLabel("Restore into")
        set_role(where_label, "secondary")
        self.destination_label = QLabel()
        self.destination_label.setWordWrap(True)
        self.destination_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        change = QPushButton("Change folder…")
        change.setAutoDefault(False)
        change.clicked.connect(self._change_folder)
        where.addWidget(where_label)
        where.addWidget(self.destination_label, 1)
        where.addWidget(change)
        layout.addLayout(where)

        note = QFrame()
        note.setObjectName("recoveryTestNote")
        note.setAttribute(Qt.WA_StyledBackground, True)
        note_row = QHBoxLayout(note)
        note_row.setContentsMargins(12, 10, 12, 10)
        note_row.setSpacing(10)
        note_text = QLabel("Keep copies these into a new folder. Nothing is overwritten, and your current files "
                           "and app data stay unchanged. To replace live app data instead, close the app and use "
                           "More options ▸ Restore to original location…")
        note_text.setWordWrap(True)
        note_row.addWidget(StatusIcon("info", 16))
        note_row.addWidget(note_text, 1)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 8, 0, 0)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self.restore_button = QPushButton("Restore")
        set_role(self.restore_button, "primary")
        self.restore_button.setDefault(True)
        self.restore_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.restore_button)
        layout.addLayout(buttons)
        self._show_destination()

    def _show_destination(self):
        self.destination_label.setText(display_path(self.destination, self.home))
        self.destination_label.setToolTip(self.destination)
        self.layout().activate()
        self.resize(self.width(), min(560, int(self.screen().availableGeometry().height() * .85)))

    def _change_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, "Restore into folder", str(Path(self.destination).parent))
        if chosen:
            self.destination = str(Path(chosen) / ("Keep-Restored-" + uuid.uuid4().hex[:12]))
            self._show_destination()

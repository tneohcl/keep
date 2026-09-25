from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QCheckBox, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QComboBox, QStyle, QScrollArea, QFrame, QMenu, QAbstractItemView

from .checkable_list import CheckableListWidget

SECTION_ORDER = ["personal", "applications", "system", "folders"]
SECTION_HEADERS = {
    "personal": "Files & folders",
    "applications": "Applications",
    "system": "System & settings",
    "folders": "Folders",
}


class SectionListWidget(CheckableListWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_height()

    def fit_height(self):
        grid = self.gridSize()
        if not self.count() or grid.width() <= 0 or grid.height() <= 0:
            self.setFixedHeight(0)
            return
        columns = max(1, self.viewport().width() // grid.width())
        self.setFixedHeight(((self.count() + columns - 1) // columns) * grid.height() + 4)

class RestorePicker(QWidget):
    """Checkable restore catalog, shared by Apps and Folders; no backend imports."""

    def __init__(self, hint_text, parent=None, ensure_mounted_cb=None, touch_activity_cb=None, backup_now_cb=None, process_names=None, installed_apps_provider=None):
        super().__init__(parent)
        self._ensure_mounted_cb = ensure_mounted_cb or (lambda: None)
        self._touch_activity_cb = touch_activity_cb or (lambda: None)
        self._backup_now_cb = backup_now_cb or (lambda: None)
        self._placeholder_mode = "startup"
        self._icon_mode = True
        self._sections = {}  # Rebuilt for each archive.
        self.process_names = process_names or {}
        self._installed_apps_provider = installed_apps_provider
        self._catalog = []
        self._installed_apps = None

        layout = QVBoxLayout(self)

        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("View:"))
        self.view_mode = QComboBox()
        self.view_mode.addItems(["Compact icons", "List"])
        self.view_mode.setAccessibleName("Restore view mode")
        self.view_mode.currentIndexChanged.connect(lambda index: self.set_icon_view() if index == 0 else self.set_list_view())
        view_row.addWidget(self.view_mode)
        self.show_uninstalled = QCheckBox("Show data for uninstalled apps")
        self.show_uninstalled.hide()
        self.show_uninstalled.toggled.connect(self._render_catalog)
        view_row.addWidget(self.show_uninstalled)
        view_row.addStretch()
        self.select_all_button = QPushButton("Select all")
        self.clear_selection_button = QPushButton("Clear selection")
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.clear_selection_button.clicked.connect(lambda: self._set_all_checked(False))
        view_row.addWidget(self.select_all_button)
        view_row.addWidget(self.clear_selection_button)
        self.select_all_button.setEnabled(False)
        self.clear_selection_button.setEnabled(False)
        layout.addLayout(view_row)

        self._placeholder = QWidget()
        ph_layout = QVBoxLayout(self._placeholder)
        ph_layout.addStretch()
        self.ph_label = QLabel("Choose a backup to see its contents.")
        self.ph_label.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(self.ph_label)
        browse_row = QHBoxLayout()
        browse_row.addStretch()
        self.btn_browse = QPushButton("Open backup…")
        self.btn_browse.clicked.connect(self._on_placeholder_button)
        browse_row.addWidget(self.btn_browse)
        browse_row.addStretch()
        ph_layout.addLayout(browse_row)
        ph_layout.addStretch()
        layout.addWidget(self._placeholder, stretch=1)

        self._sections_container = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_container)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.setSpacing(4)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setWidget(self._sections_container)
        self._scroll.hide()
        layout.addWidget(self._scroll, stretch=1)

        hint = QLabel(hint_text)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.selection_summary = QLabel("No items selected")
        layout.addWidget(self.selection_summary)
        restore_row = QHBoxLayout()
        self.btn_restore_safe = QPushButton("Restore safely")
        self.btn_restore_safe.clicked.connect(self.restore_checked_safe)
        explanation = QLabel("Your current files stay unchanged. Review restore copies what you check into a new folder in Keep-Restored.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        more = QPushButton("More options…")
        more_menu = QMenu(more)
        self.action_restore_direct = more_menu.addAction("Restore to original location…", self.restore_checked_direct)
        more.setMenu(more_menu)
        restore_row.addWidget(more)
        restore_row.addStretch()
        self.btn_restore_safe.setMinimumHeight(36)
        restore_row.addWidget(self.btn_restore_safe)
        layout.addLayout(restore_row)

    def _new_section_widget(self):
        section = SectionListWidget()
        section.setObjectName("applicationChoices")
        section.setMovement(QListWidget.Static)
        section.setSelectionMode(QAbstractItemView.NoSelection)
        section.setDragEnabled(False)
        section.setDragDropMode(QAbstractItemView.NoDragDrop)
        section.setUniformItemSizes(True)
        section.setWordWrap(True)
        section.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        section.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        section.itemChanged.connect(self._update_selection_summary)
        section.itemChanged.connect(self._touch_activity_cb)
        section.itemSelectionChanged.connect(self._touch_activity_cb)
        section.itemSelectionChanged.connect(self._update_selection_summary)
        return section

    def _set_all_checked(self, checked):
        for section in self._sections.values():
            section.blockSignals(True)
            for i in range(section.count()):
                item = section.item(i)
                if not item.isHidden():
                    item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            section.blockSignals(False)
        self._touch_activity_cb()
        self._update_selection_summary()

    def _update_selection_summary(self):
        count = len(self._all_items())
        total = sum(section.count() for section in self._sections.values())
        self.select_all_button.setEnabled(total > count)
        self.clear_selection_button.setEnabled(count > 0)
        self.selection_summary.setText(f"Selected: {count} item(s)" if count else "No items selected")

    def _apply_view_mode(self):
        for section in self._sections.values():
            if self._icon_mode:
                section.setViewMode(QListWidget.IconMode)
                section.setFlow(QListWidget.LeftToRight)
                section.setWrapping(True)
                section.setResizeMode(QListWidget.Adjust)
                section.setIconSize(QSize(32, 32))
                section.setGridSize(QSize(160, max(96, self.fontMetrics().height() * 3 + 40)))
                section.setSpacing(4)
            else:
                section.setViewMode(QListWidget.ListMode)
                section.setFlow(QListWidget.TopToBottom)
                section.setWrapping(True)
                section.setResizeMode(QListWidget.Adjust)
                section.setIconSize(QSize(24, 24))
                section.setGridSize(QSize(220, max(48, self.fontMetrics().height() * 2 + 12)))  # tall enough for a 2nd (subtitle) line
                section.setSpacing(2)
            section.update_cell_sizes()
            section.fit_height()
        self.view_mode.blockSignals(True)
        self.view_mode.setCurrentIndex(0 if self._icon_mode else 1)
        self.view_mode.blockSignals(False)

    def set_icon_view(self):
        self._icon_mode = True
        self._apply_view_mode()

    def set_list_view(self):
        self._icon_mode = False
        self._apply_view_mode()

    def _on_placeholder_button(self):
        if self._placeholder_mode == "empty_repo":
            self._backup_now_cb()
        else:
            self._ensure_mounted_cb()

    def show_empty_repo_message(self):
        """Called when the repo was confirmed reachable and genuinely has
        zero archives - distinct from the generic "haven't tried browsing
        yet" placeholder, since clicking Browse again here can't produce
        anything (there's nothing to browse); the useful next action is
        running a first backup instead."""
        self._placeholder_mode = "empty_repo"
        self.ph_label.setText(
            "No backups yet\n\n"
            "This backup destination is working, but it doesn't contain "
            "any backup snapshots yet."
        )
        self.btn_browse.setText("Back up now")

    def reset_placeholder_mode(self):
        self._placeholder_mode = "startup"
        self.ph_label.setText("Choose a backup to see its contents.")
        self.btn_browse.setText("Open backup…")

    def reset_to_placeholder(self):
        """Back to the "nothing browsed here" state, tiles and all - called
        whenever the underlying mount goes away for any reason (idle-
        unmount, archive change, delete, destination switch) so a tile
        describing a DIFFERENT archive than what's actually mounted can
        never sit there clickable in the meantime. A tile tapped after
        this always describes what's actually mounted, never a leftover
        from before - found via a reviewer's whole-app sweep: without
        this, idle-unmount followed by a Backup Now (or a delete, or a
        destination switch) could leave the picker showing archive A's
        tiles while the combo had already moved to archive B, and
        restoring would silently read B's mount using A's item paths."""
        self.reset_placeholder_mode()
        self._scroll.hide()
        self._placeholder.show()
        while self._sections_layout.count():
            child = self._sections_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._sections.clear()
        self._catalog = []
        self.show_uninstalled.hide()
        self._update_selection_summary()

    def populate(self, catalog):
        self._catalog = list(catalog)
        self._installed_apps = self._installed_apps_provider() if self._installed_apps_provider else None
        self.show_uninstalled.setVisible(self._installed_apps is not None and any(e.category == "applications" for e in self._catalog))
        self._render_catalog(preserve=False)

    def _render_catalog(self, _checked=False, preserve=True):
        selected = {tuple(tuple(pair) for pair in item.data(Qt.UserRole)) for item in self._all_items()} if preserve else set()
        catalog = [entry for entry in self._catalog if entry.category != "applications" or self._installed_apps is None or self.show_uninstalled.isChecked() or self._installed_apps.contains(entry.label, entry.icon)]
        self.selection_summary.setText("No items selected")
        self.reset_placeholder_mode()
        self._placeholder.hide()
        self._scroll.show()
        while self._sections_layout.count():
            child = self._sections_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._sections.clear()

        if not catalog:
            message = "No data for installed apps in this backup. Enable ‘Show data for uninstalled apps’ to view older app data." if self._catalog and self._installed_apps else "No matching data in this backup."
            empty = QLabel(message)
            empty.setWordWrap(True)
            self._sections_layout.addWidget(empty)
        by_category = {}
        for entry in catalog:
            by_category.setdefault(entry.category, []).append(entry)
        show_headers = len(by_category) > 1  # Projects' single category never gets a header

        fallback = QIcon.fromTheme("application-x-executable", self.style().standardIcon(QStyle.SP_FileIcon))
        for category in SECTION_ORDER:
            entries = by_category.get(category)
            if not entries:
                continue
            if show_headers:
                header = QLabel(SECTION_HEADERS.get(category, category.replace("_", " ").title()))
                header.setStyleSheet("font-weight: 500; margin-top: 6px;")
                self._sections_layout.addWidget(header)
            section = self._new_section_widget()
            for entry in entries:
                icon = QIcon.fromTheme(entry.icon, fallback)
                text = entry.label if not entry.subtitle else f"{entry.label}\n{entry.subtitle}"
                item = QListWidgetItem(icon, text)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if tuple(entry.rel_paths) in selected else Qt.Unchecked)
                item.setToolTip(text + "\n" + "\n".join(source for source, _ in entry.rel_paths))
                item.setData(Qt.UserRole, entry.rel_paths)
                identity = entry.label if not entry.subtitle else f"{entry.label} ({entry.subtitle})"
                item.setData(Qt.UserRole + 1, identity)
                item.setData(Qt.UserRole + 2, self.process_names.get(entry.label, []))
                item.setData(Qt.UserRole + 3, entry.label)
                item.setData(Qt.AccessibleTextRole, entry.label)
                item.setData(Qt.AccessibleDescriptionRole, entry.subtitle or "Backup item")
                item.setData(Qt.UserRole + 4, entry.subtitle)
                section.addItem(item)
            self._sections[category] = section
            self._sections_layout.addWidget(section)
        self._sections_layout.addStretch()
        self._apply_view_mode()
        self._update_selection_summary()

    def _all_items(self):
        items = []
        for section in self._sections.values():
            items.extend(section.item(i) for i in range(section.count()) if section.item(i).checkState() == Qt.Checked)
        return items

    def restore_checked_safe(self):
        raise NotImplementedError("Connect a restore controller")

    def restore_checked_direct(self):
        raise NotImplementedError("Connect a restore controller")


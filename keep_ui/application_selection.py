from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QStyle
from .common import DisclosureSection
from .checkable_list import CheckableListWidget, apply_tile_mode
from .restore_picker import view_switch

class ApplicationSelectionDialog(QDialog):
    """Explicit application choices with inspectable source paths."""
    def __init__(self, config, home, parent=None, category="applications"):
        super().__init__(parent)
        import applications
        self.category = category
        self.setWindowTitle("Choose Applications" if category == "applications" else "Choose System Settings")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        self.all_data = QCheckBox("Include all application data and settings (current broad coverage)")
        self.all_data.setChecked(config.get("app_selection_mode", "all") == "all" and config.get("include_app_data", True))
        initial_all = self.all_data.isChecked()
        if category == "system":
            self.all_data.setChecked(False)
            self.all_data.hide()
        layout.addWidget(self.all_data)
        search = QLineEdit()
        search.setPlaceholderText("Find an application" if category == "applications" else "Find a setting")
        search.setAccessibleName("Filter applications and settings")
        search.setClearButtonEnabled(True)
        self.items = CheckableListWidget()
        self.items.setObjectName("applicationChoices")
        self.items.setAccessibleName("Applications to back up")
        self.items.setMovement(QListWidget.Static)
        self.items.setResizeMode(QListWidget.Adjust)
        self.items.setIconSize(QSize(32, 32))
        self.items.setWordWrap(True)
        view_row = QHBoxLayout()
        view_row.addWidget(search, 1)
        self.view_mode = view_switch("Application view mode")
        view_row.addWidget(self.view_mode)
        self.select_all_button = QPushButton("Select all")
        self.clear_selection_button = QPushButton("Clear")
        def set_checked(checked):
            self.all_data.setChecked(False)
            for i in range(self.items.count()):
                item = self.items.item(i)
                if not item.isHidden():
                    item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.select_all_button.clicked.connect(lambda: set_checked(True))
        self.clear_selection_button.clicked.connect(lambda: set_checked(False))
        view_row.addWidget(self.select_all_button)
        view_row.addWidget(self.clear_selection_button)
        layout.addLayout(view_row)
        def set_mode(index):
            apply_tile_mode(self.items, index == 0, self.fontMetrics())
        self.view_mode.currentChanged.connect(set_mode)
        set_mode(0)
        layout.addWidget(self.items, 1)
        installed = applications.InstalledApps(home)
        selected = set(config.get("selected_applications", []))
        for entry in applications.catalog(config, home=home, include_unrecognized=True):
            if entry["category"] != category:
                continue
            kind = "System settings" if entry["category"] == "system" else entry["formats"]
            if not entry["recognized"]:
                kind = "Unrecognized data folder"
            item = QListWidgetItem(entry['label'])
            flatpak_id = next((p.split("/")[-1] for p in entry["paths"] if p.startswith(".var/app/")), entry["id"])
            fallback = self.style().standardIcon(QStyle.SP_ComputerIcon if entry["category"] == "system" else QStyle.SP_FileIcon)
            item.setIcon(QIcon.fromTheme(flatpak_id, QIcon.fromTheme(entry["id"], fallback)))
            entry["installed"] = installed.entry_installed(entry)
            item.setData(Qt.UserRole, entry)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if (initial_all and entry["recognized"] and entry["installed"]) or entry["id"] in selected else Qt.Unchecked)
            item.setToolTip(kind + "\n" + "\n".join(str(Path(home) / p) for p in entry["paths"]))
            self.items.addItem(item)
        self.items.update_cell_sizes()
        show_uninstalled = QCheckBox("Show data for uninstalled apps")
        show_uninstalled.setVisible(category == "applications")
        layout.addWidget(show_uninstalled)
        show_other = QCheckBox("Show unrecognized data folders (advanced)")
        layout.addWidget(show_other)
        def filter_items():
            text = search.text().casefold()
            for i in range(self.items.count()):
                item = self.items.item(i)
                entry = item.data(Qt.UserRole)
                # Preserve saved choices; the uninstalled-data toggle reveals old apps.
                recognized = entry["recognized"] or entry["id"] in selected
                item.setHidden(text not in item.text().casefold() or (not recognized and not show_other.isChecked()) or (not entry["installed"] and not show_uninstalled.isChecked()))
        show_uninstalled.toggled.connect(filter_items)
        show_other.toggled.connect(filter_items)
        search.textChanged.connect(filter_items)
        filter_items()
        self.path_details = QPlainTextEdit()
        self.path_details.setReadOnly(True)
        self.path_details.setMaximumHeight(100)
        self.path_details.setAccessibleName("Included application paths")
        path_section = DisclosureSection("Included paths", "Hide paths", self.path_details)
        layout.addWidget(path_section)
        self.items.currentItemChanged.connect(lambda item, previous: self.path_details.setPlainText(item.toolTip() if item else ""))
        self.all_data.toggled.connect(lambda checked: self.items.setEnabled(not checked))
        self.items.setEnabled(not self.all_data.isChecked())
        note = QLabel("Known apps include the data paths shown below. Unrecognized folders are optional. Broad home/app-data folder backups must be removed when choosing individual apps.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save selection")
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def selection(self):
        return [self.items.item(i).data(Qt.UserRole)["id"] for i in range(self.items.count()) if self.items.item(i).checkState() == Qt.Checked]


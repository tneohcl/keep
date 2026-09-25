from PySide6.QtCore import Qt, QDir
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QProgressBar, QMenu, QTabWidget, QFileSystemModel, QTreeView, QAbstractItemView
import consumer

class RestorePage(QWidget):
    """Restore controls; signals are connected after tab construction to avoid startup mounts."""
    CONTROL_NAMES = ('apps_picker', 'archive_actions_button', 'archive_combo', 'btn_compare_archives', 'btn_delete_archive', 'btn_restore', 'delete_progress', 'folders_picker', 'fs_model', 'lbl_delete_status', 'section_combo', 'tabs', 'tree')

    def __init__(self, controller, config, mountpoint, home_in_archive, picker_factory):
        super().__init__()
        browse_box = self
        browse_box.setObjectName("restoreSurface")
        browse_box.setAttribute(Qt.WA_StyledBackground, True)
        browse_layout = QVBoxLayout(browse_box)
        browse_layout.setContentsMargins(24, 16, 24, 16)
        browse_layout.setSpacing(14)
        restore_intro = QLabel("Restore from a backup")
        restore_font = restore_intro.font()
        restore_font.setPointSize(restore_font.pointSize() + 5)
        restore_font.setBold(True)
        restore_intro.setFont(restore_font)
        browse_layout.addWidget(restore_intro)

        archive_row = QHBoxLayout()
        archive_row.addWidget(QLabel("Backup:"))
        self.archive_combo = QComboBox()
        self.archive_combo.setAccessibleName("Backup archive to restore")
        self.archive_combo.currentIndexChanged.connect(controller.on_archive_changed)
        archive_row.addWidget(self.archive_combo, stretch=1)
        self.archive_actions_button = QPushButton("Manage backup")
        archive_menu = QMenu(self.archive_actions_button)
        self.btn_compare_archives = archive_menu.addAction("Compare Backups…", controller.compare_archives)
        archive_menu.addSeparator()
        self.btn_delete_archive = archive_menu.addAction("Delete This Backup…", controller.delete_current_archive)
        self.archive_actions_button.setMenu(archive_menu)
        archive_row.addWidget(self.archive_actions_button)
        browse_layout.addLayout(archive_row)

        self.delete_progress = QProgressBar()
        self.delete_progress.setTextVisible(False)
        self.delete_progress.setFixedHeight(18)
        self.delete_progress.hide()
        browse_layout.addWidget(self.delete_progress)
        self.lbl_delete_status = QLabel()
        self.lbl_delete_status.hide()
        browse_layout.addWidget(self.lbl_delete_status)

        self.tabs = QTabWidget()
        browse_layout.addWidget(self.tabs, stretch=1)

        self.apps_picker = picker_factory(
            "Check the items you want to restore.", filter_installed=True,
            ensure_mounted_cb=controller.ensure_mounted, touch_activity_cb=controller._touch_activity,
            backup_now_cb=controller.start_backup,
        )
        self.tabs.addTab(self.apps_picker, "Apps && data")

        self.folders_picker = picker_factory(
            "Check the folders you want to restore.",
            ensure_mounted_cb=controller.ensure_mounted, touch_activity_cb=controller._touch_activity,
            backup_now_cb=controller.start_backup,
        )
        self.tabs.addTab(self.folders_picker, "Folders")

        adv_tab = QWidget()
        adv_layout = QVBoxLayout(adv_tab)
        section_row = QHBoxLayout()
        section_row.addWidget(QLabel("Show:"))
        self.section_combo = QComboBox()
        self.section_combo.setAccessibleName("Archive folder")
        self.section_combo.addItem("My Files", f"{mountpoint}/{home_in_archive}")
        for source in consumer.backup_source_entries(config):
            rel = consumer.archive_path_for_source(source["path"])
            self.section_combo.addItem(source["label"], f"{mountpoint}/{rel}")
        self.section_combo.currentIndexChanged.connect(controller.on_section_changed)
        section_row.addWidget(self.section_combo)
        section_row.addStretch()
        adv_layout.addLayout(section_row)

        self.fs_model = QFileSystemModel()
        self.fs_model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden | QDir.System)
        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        for col in (1, 2, 3):
            self.tree.hideColumn(col)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.selectionModel().selectionChanged.connect(controller._touch_activity)
        adv_layout.addWidget(self.tree, stretch=1)
        adv_layout.addWidget(QLabel("Ctrl+click or Shift+click to pick several files/folders at once."))
        adv_restore_row = QHBoxLayout()
        self.btn_restore = QPushButton("Restore selected to…")
        self.btn_restore.clicked.connect(controller.restore_selected)
        adv_restore_row.addWidget(self.btn_restore)
        adv_restore_row.addStretch()
        adv_layout.addLayout(adv_restore_row)
        self.tabs.addTab(adv_tab, "Advanced (browse all files)")
        self.tabs.currentChanged.connect(controller.on_tab_changed)


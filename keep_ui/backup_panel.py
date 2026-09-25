"""Sidebar of backup settings plus the backup/stop actions; no filesystem or
repository operations.

ODCS layout: the sidebar is one grouped odcs SettingsList (each row a real
button whose value wraps), sitting on the window colour. btn_backup and
btn_stop are created here for the controller but placed by MainWindow at the
trailing end of the toolbar.
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QSizePolicy

import theming  # noqa: F401  (puts the bundled vendor/odcs_ui on sys.path)
from odcs_ui.theming import set_surface
from odcs_ui.widgets import SettingsList


class BackupPanel(QWidget):
    CONTROL_NAMES = ('apps_choice', 'btn_backup', 'btn_stop', 'destination_choice', 'plan_panel', 'schedule_button', 'settings_choice', 'source_choice')

    def __init__(self, controller):
        super().__init__()
        self.setObjectName("keepSidebar")
        set_surface(self, "window")
        self.setFixedWidth(300)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(16, 20, 16, 20)
        shell.setSpacing(16)
        self.plan_panel = SettingsList("What's backed up")
        controls = (
            ("Folders", "source_choice", controller.configure_backup_sources),
            ("Applications", "apps_choice", controller.configure_applications),
            ("System settings", "settings_choice", lambda: controller.configure_applications("system")),
            ("Destination", "destination_choice", controller.change_backup_destination),
            ("Schedule", "schedule_button", controller.configure_schedule),
        )
        for label, name, callback in controls:
            row = self.plan_panel.addRow(label, "", lambda checked=False, callback=callback: callback())
            setattr(self, name, row)
        shell.addWidget(self.plan_panel)
        self.recovery_slot = QVBoxLayout()  # Recovery group (phase D) goes here
        shell.addLayout(self.recovery_slot)
        shell.addStretch(1)

        self.btn_backup = QPushButton("Back up now")
        self.btn_backup.clicked.connect(controller.start_backup)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(controller.stop_backup)
        self.btn_stop.setEnabled(False)
        self.btn_stop.hide()
        for button in (self.btn_backup, self.btn_stop):
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

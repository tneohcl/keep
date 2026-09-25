"""Persistent backup controls; no filesystem or repository operations."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QSizePolicy

class BackupPanel(QWidget):
    CONTROL_NAMES = ('apps_choice', 'btn_backup', 'btn_stop', 'destination_choice', 'plan_panel', 'schedule_button', 'settings_choice', 'source_choice')

    def __init__(self, controller):
        super().__init__()
        self.setFixedWidth(290)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(12)
        title = QLabel("Your backup")
        font = title.font()
        font.setPointSize(font.pointSize() + 5)
        font.setBold(True)
        title.setFont(font)
        title.setContentsMargins(0, 8, 0, 0)
        shell.addWidget(title)
        self.plan_panel = QWidget()
        self.plan_panel.setObjectName("planPanel")
        self.plan_panel.setAttribute(Qt.WA_StyledBackground, True)
        plan = QVBoxLayout(self.plan_panel)
        plan.setContentsMargins(20, 24, 20, 20)
        plan.setSpacing(12)
        controls = (
            ("Folders", "source_choice", "Choose folders…", controller.configure_backup_sources),
            ("Applications", "apps_choice", "Choose applications…", controller.configure_applications),
            ("System settings", "settings_choice", "Choose settings…", lambda: controller.configure_applications("system")),
            ("Destination", "destination_choice", "Choose backup location…", controller.change_backup_destination),
            ("Automatic backups", "schedule_button", "Schedule…", controller.configure_schedule),
        )
        for caption, name, text, callback in controls:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, callback=callback: callback())
            button.setMinimumHeight(36)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            setattr(self, name, button)
            plan.addWidget(QLabel(caption))
            plan.addWidget(button)
            plan.addSpacing(10)
        plan.addStretch()
        self.btn_backup = QPushButton("Back up now")
        self.btn_backup.clicked.connect(controller.start_backup)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(controller.stop_backup)
        self.btn_stop.setEnabled(False)
        self.btn_stop.hide()
        for button in (self.btn_backup, self.btn_stop):
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setMinimumSize(180, 40)
            plan.addWidget(button)
        shell.addWidget(self.plan_panel, 1)

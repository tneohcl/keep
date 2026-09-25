"""Test recovery… dialog: the person types their passphrase, Keep restores one
file with only that passphrase (recovery_test.py) and records the outcome."""
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QVBoxLayout)

import theming  # noqa: F401  (puts the bundled vendor/odcs_ui on sys.path)
from odcs_ui.theming import set_role
from odcs_ui.widgets import StatusIcon
from operation_lock import RepositoryBusy, RepositoryLock
import recovery_test


class RecoveryTestWorker(QThread):
    finished_result = Signal(dict)

    def __init__(self, repository, passphrase):
        super().__init__()
        self.repository = repository
        self._passphrase = passphrase
        self.cancel_event = threading.Event()

    def run(self):
        # finished_result must fire exactly once, whatever happens, or the
        # dialog would sit in its busy state forever.
        try:
            with RepositoryLock(self.repository):
                result = recovery_test.run(self.repository, self._passphrase, cancelled=self.cancel_event.is_set)
        except RepositoryBusy:
            result = {"result": "failed", "reason": "busy", "message": recovery_test.MESSAGES["busy"]}
        except Exception:
            result = {"result": "failed", "reason": "unavailable", "message": recovery_test.MESSAGES["unavailable"]}
        finally:
            self._passphrase = ""
        self.finished_result.emit(result)


class RecoveryTestDialog(QDialog):
    recorded = Signal(dict)

    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.worker = None
        self.setWindowTitle("Test recovery")
        self.setFixedWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)
        heading = QLabel("Can you get your files back without this computer?")
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        explanation = QLabel(
            "Keep will restore one file from your latest backup into a temporary folder using only "
            "the passphrase you type here. The passphrase and key files saved on this computer are "
            "not used. Keep checks the file matches the backup, then deletes it. Your backup is not changed.")
        explanation.setWordWrap(True)
        set_role(explanation, "secondary")
        layout.addWidget(explanation)
        self.passphrase = QLineEdit()
        self.passphrase.setEchoMode(QLineEdit.Password)
        self.passphrase.setPlaceholderText("Backup passphrase")
        self.passphrase.setAccessibleName("Backup passphrase")
        layout.addWidget(self.passphrase)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        layout.addWidget(self.progress)
        outcome = QHBoxLayout()
        outcome.setSpacing(10)
        self.outcome_icon = StatusIcon("info", 20)
        self.outcome_text = QLabel()
        self.outcome_text.setWordWrap(True)
        self.outcome_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outcome.addWidget(self.outcome_icon)
        outcome.addWidget(self.outcome_text, 1)
        layout.addLayout(outcome)
        self._show_outcome(None)
        self.buttons = QDialogButtonBox()
        self.start_button = QPushButton("Test recovery")
        set_role(self.start_button, "primary")
        self.start_button.setDefault(True)
        self.start_button.setEnabled(False)
        self.close_button = self.buttons.addButton(QDialogButtonBox.Cancel)
        self.buttons.addButton(self.start_button, QDialogButtonBox.AcceptRole)
        self.start_button.clicked.connect(self.start)
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.buttons)
        self.passphrase.textChanged.connect(lambda text: self.start_button.setEnabled(bool(text) and self.worker is None))
        self.passphrase.returnPressed.connect(self.start)
        self._fit()

    def _show_outcome(self, state, text=""):
        self.outcome_icon.setVisible(state is not None)
        self.outcome_text.setVisible(bool(text))
        if state is not None:
            self.outcome_icon.setState(state)
        self.outcome_text.setText(text)
        self._fit()

    def _fit(self):
        # A top-level dialog sizes wrapped labels at a guessed width and
        # spreads the surplus as gaps; size it from the real width instead.
        self.layout().activate()
        self.setFixedHeight(self.layout().heightForWidth(self.width()))

    def start(self):
        if self.worker is not None or not self.passphrase.text():
            return
        self.worker = RecoveryTestWorker(self.repository, self.passphrase.text())
        self.passphrase.clear()
        self.passphrase.setEnabled(False)
        self.start_button.setEnabled(False)
        self.progress.show()
        self._show_outcome("info", "Testing recovery… This usually takes under a minute.")
        self.worker.finished_result.connect(self._finished)
        self.worker.start()

    def _finished(self, result):
        self.worker.wait()
        self.worker = None
        self.progress.hide()
        kept = recovery_test.record(dict(result, repository=self.repository))
        state = {"passed": "ok", "cancelled": "info"}.get(result.get("result"), "error")
        self._show_outcome(state, result.get("message", ""))
        if kept:
            self.recorded.emit(kept)
        if result.get("result") == "passed":
            self.start_button.hide()
            self.close_button.setText("Done")
            self.close_button.setFocus()
        else:
            self.start_button.setText("Try again")
            self.passphrase.setEnabled(True)
            self.passphrase.setFocus()
            self.close_button.setText("Close")

    def reject(self):
        if self.worker is not None:
            # Cancel stops borg and removes the temporary folder before closing.
            self.close_button.setEnabled(False)
            self._show_outcome("info", "Cancelling…")
            self.worker.finished_result.disconnect(self._finished)
            self.worker.cancel_event.set()
            self.worker.wait()
            self.worker = None
        super().reject()

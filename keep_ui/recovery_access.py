"""Recovery access: what you need to restore this backup on a new computer.

Two kinds of facts, kept visibly apart: what Keep verified itself (from the
last status query) and what only the person can confirm (dated, re-asked
after a year). Export key file… and Test recovery… live here too.
"""
import os
from pathlib import Path
import subprocess

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget, QScrollArea)

import theming  # noqa: F401  (puts the bundled vendor/odcs_ui on sys.path)
from odcs_ui.theming import set_role
from odcs_ui.widgets import StatusIcon
import recovery_test

CONFIRM_TEXT = {
    "passphrase_saved": "The passphrase is saved in a password manager",
    "kit_offsite": "A printed recovery kit is stored away from this computer",
}
DESTINATION_ACCESS_TEXT = {
    "network": "I can sign in to {label} without this computer",
    "removable": "I can find the backup drive and connect it to another computer",
}


def encryption_fact(info):
    """(state, text) for the repository's encryption mode from `borg info --json`."""
    if not info:
        return "never", "Not checked: the backup couldn't be read. Connect the destination and refresh."
    mode = (info.get("encryption") or {}).get("mode", "")
    if mode.startswith("repokey"):
        return "ok", "The key is stored inside the backup (repokey). You need the passphrase, not a separate key file."
    if mode.startswith("keyfile"):
        return "warning", ("The key is stored only on this computer (keyfile). On a new computer you need "
                           "the exported key file and the passphrase. Export it now.")
    return "warning", "This backup is not encrypted. Anyone who can reach the destination can read it."


def is_encrypted(info):
    mode = ((info or {}).get("encryption") or {}).get("mode", "")
    return mode.startswith(("repokey", "keyfile"))


def summary(repository, now=None, repository_id=None):
    """(value, state) for the sidebar's Recovery access row."""
    state, when, _ = recovery_test.status(recovery_test.load(), repository, now, repository_id=repository_id)
    entry = recovery_test.load_access(repository, repository_id=repository_id)
    unconfirmed = sum(recovery_test.confirmation(entry, key, now)[0] != "confirmed"
                      for key in recovery_test.CONFIRMATIONS)
    if state == "error":
        return "Last test failed", "error"
    if state == "never":
        return "Not yet tested", "warning"
    if state == "warning":
        return "Test again", "warning"
    if state == "info":
        return "Tested · not yet verified", ""
    return ("Tested" if not unconfirmed else f"Tested · {unconfirmed} to confirm"), ""


class KeyExportWorker(QThread):
    finished_result = Signal(bool, str)

    def __init__(self, repository, path, env):
        super().__init__()
        self.repository, self.path, self.env = repository, path, env

    def run(self):
        try:
            result = subprocess.run(["borg", "key", "export", self.repository, self.path], env=self.env,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                os.chmod(self.path, 0o600)
            lines = (result.stderr or "").strip().splitlines()
            self.finished_result.emit(result.returncode == 0, lines[-1] if result.returncode and lines else "")
        except Exception as exc:  # always answer, or the button stays busy
            self.finished_result.emit(False, str(exc))


class RecoveryAccessDialog(QDialog):
    changed = Signal()

    def __init__(self, repository, destination, info, archive_count, verified_when, borg_env,
                 test_recovery, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.destination = destination
        self.info = info
        self.repository_id = ((info or {}).get("repository") or {}).get("id")
        self.borg_env = borg_env
        self.test_recovery = test_recovery
        self.worker = None
        self.setWindowTitle("Recovery access")
        self.resize(min(600, int(self.screen().availableGeometry().width() * .9)), 650)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(8)
        title = QLabel("Recovery access")
        font = title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 3)
        title.setFont(font)
        layout.addWidget(title)
        intro = QLabel("What you need to restore this backup on a new computer. Keep checks what it can; "
                       "the rest only you can confirm.")
        intro.setWordWrap(True)
        set_role(intro, "secondary")
        layout.addWidget(intro)

        layout.addSpacing(8)
        layout.addWidget(self._heading(f"Verified by Keep · {verified_when}" if verified_when else "Verified by Keep"))
        self.verified = self._table()
        label = destination.get("label") or "The destination"
        if destination.get("available"):
            count = f", {archive_count} backup{'s' if archive_count != 1 else ''} found" if archive_count is not None else ""
            self._verified_row("Destination", "ok", f"{label} is reachable{count}.")
        else:
            self._verified_row("Destination", "error", f"{label} isn't reachable right now. Connect it, then open this again.")
        self._verified_row("Encryption", *encryption_fact(info))
        if is_encrypted(info):
            self._verified_row("Passphrase", "ok", "The copy saved on this computer opens the backup. "
                                                   "You'll need your own copy on a new computer.")
        elif not info:
            self._verified_row("Passphrase", "never", "Not checked: the backup couldn't be read.")
        exported = recovery_test.load_access(repository, repository_id=self.repository_id).get("key_exported")
        if is_encrypted(info):
            if exported:
                self._verified_row("Key file", "ok", f"Exported with Keep {self._friendly(exported)}.")
            elif encryption_fact(info)[0] == "warning":
                self._verified_row("Key file", "error", "Not exported yet. Without it this backup can't be opened on a new computer.")
            else:
                self._verified_row("Key file", "info", "Optional: a spare copy helps if the backup's own copy is ever damaged.")
        layout.addWidget(self.verified)

        layout.addSpacing(8)
        layout.addWidget(self._heading("Only you can confirm"))
        self.confirm_boxes = {}
        confirms = self._table()
        texts = dict(CONFIRM_TEXT)
        texts["destination_access"] = DESTINATION_ACCESS_TEXT.get(
            destination.get("type"), "I can reach the backup folder from another computer").format(label=label)
        entry = recovery_test.load_access(repository, repository_id=self.repository_id)
        for key in recovery_test.CONFIRMATIONS:
            state, when = recovery_test.confirmation(entry, key)
            box = QCheckBox(texts[key])
            box.setChecked(state == "confirmed")
            note = QLabel(f"Confirmed {self._friendly(when)}" if state == "confirmed"
                          else f"Last confirmed {self._friendly(when)}. Check again." if state == "stale" and when
                          else "")
            set_role(note, "caption" if state == "confirmed" else "warning" if state == "stale" else "caption")
            note.setWordWrap(True)
            note.setVisible(bool(note.text()))
            box.toggled.connect(lambda checked, key=key, note=note: self._confirm(key, checked, note))
            box.setEnabled(bool(self.repository_id))
            self.confirm_boxes[key] = box
            item = QWidget()
            stack = QVBoxLayout(item)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(2)
            stack.addWidget(box)
            note.setContentsMargins(box.style().pixelMetric(box.style().PixelMetric.PM_IndicatorWidth) + 8, 0, 0, 0)
            stack.addWidget(note)
            self._row(confirms, [item])
        layout.addWidget(confirms)
        caption = QLabel("Your answers are saved with a date so Keep can remind you to check them again. "
                         "Keep never stores where your copies are.")
        caption.setWordWrap(True)
        set_role(caption, "caption")
        layout.addWidget(caption)

        layout.addSpacing(8)
        layout.addWidget(self._heading("Recovery test"))
        test_row = QFrame()
        test_row.setObjectName("recoveryTestNote")
        test_row.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(test_row)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)
        self.test_icon = StatusIcon("never", 18)
        self.test_text = QLabel()
        self.test_text.setWordWrap(True)
        self.test_text.setTextFormat(Qt.RichText)
        row.addWidget(self.test_icon)
        row.addWidget(self.test_text, 1)
        layout.addWidget(test_row)
        self._refresh_test()

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 12, 0, 0)
        self.export_button = QPushButton("Export key file…")
        self.export_button.setAutoDefault(False)
        self.export_button.setEnabled(bool(destination.get("available")) and is_encrypted(info))
        self.export_button.clicked.connect(self.export_key)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        self.test_button = QPushButton("Test recovery…")
        set_role(self.test_button, "primary")
        self.test_button.setDefault(True)
        self.test_button.setEnabled(bool(destination.get("available")))
        self.test_button.clicked.connect(self._test)
        buttons.addWidget(self.export_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(self.test_button)
        layout.addLayout(buttons)
        self._fit()

    # ---- layout helpers ------------------------------------------------
    def _fit(self):
        # Word-wrapped labels in a top-level dialog: size from the real width.
        self.layout().activate()
        self.resize(self.width(), min(720, int(self.screen().availableGeometry().height() * .85)))

    @staticmethod
    def _friendly(iso):
        from odcs_ui.timefmt import friendly_datetime
        import datetime
        try:
            return friendly_datetime(datetime.datetime.fromisoformat(iso))
        except (TypeError, ValueError):
            return iso or ""

    @staticmethod
    def _heading(text):
        label = QLabel(text)
        label.setObjectName("odcsGroupHeading")
        label.setStyleSheet("padding: 0;")
        return label

    @staticmethod
    def _table():
        frame = QFrame()
        frame.setObjectName("recoveryTable")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)
        return frame

    @staticmethod
    def _row(table, widgets, stretch_column=None):
        grid = table.layout()
        row = grid.rowCount() if grid.count() else 0
        for column, widget in enumerate(widgets):
            cell = QFrame()
            cell.setObjectName("odcsFactCell")
            lay = QHBoxLayout(cell)
            lay.setContentsMargins(12 if column == 0 else 0, 9, 12, 9)
            lay.addWidget(widget)
            grid.addWidget(cell, row, column)
        grid.setColumnStretch(stretch_column if stretch_column is not None else len(widgets) - 1, 1)

    def _verified_row(self, name, state, text):
        title = QLabel(name)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        title.setMinimumWidth(110)
        value = QLabel(text)
        value.setWordWrap(True)
        value.setAccessibleName(f"{name}: {text}")
        self._row(self.verified, [StatusIcon(state, 16), title, value], stretch_column=2)

    # ---- behaviour -----------------------------------------------------
    def _confirm(self, key, checked, note):
        when = recovery_test.now_iso() if checked else None
        recovery_test.save_access(self.repository, {key: when}, repository_id=self.repository_id)
        note.setText(f"Confirmed {self._friendly(when)}" if checked else "")
        note.setVisible(checked)
        set_role(note, "caption")
        self._fit()
        self.changed.emit()

    def _refresh_test(self):
        state, when, advice = recovery_test.status(recovery_test.load(), self.repository, repository_id=self.repository_id)
        lead = {"never": "Not yet recorded.", "ok": f"Tested {self._friendly(when)}.",
                "warning": f"Last tested {self._friendly(when)}.", "error": "The last test failed.",
                "info": f"Tested {self._friendly(when)}, not yet verified for this backup."}[state]
        self.test_icon.setState({"never": "warning"}.get(state, state))
        self.test_text.setText(f"<b>{lead}</b> {advice}")

    def _test(self):
        self.test_recovery()
        self._refresh_test()
        self._fit()
        self.changed.emit()

    def export_key(self):
        label = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (self.destination.get("label") or "backup"))
        suggested = str(Path.home() / f"keep-{label}-key.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Export key file", suggested, "Key file (*.txt);;All files (*)")
        if not path:
            return
        self.export_button.setEnabled(False)
        self.export_button.setText("Exporting…")
        self.worker = KeyExportWorker(self.repository, path, self.borg_env())
        self.worker.finished_result.connect(lambda ok, message, path=path: self._exported(ok, message, path))
        self.worker.start()

    def _exported(self, ok, message, path):
        self.worker.wait()
        self.worker = None
        self.export_button.setText("Export key file…")
        self.export_button.setEnabled(True)
        if ok:
            recovery_test.save_access(self.repository, {"key_exported": recovery_test.now_iso()}, repository_id=self.repository_id)
            self.changed.emit()
            QMessageBox.information(
                self, "Keep",
                f"Key file saved to:\n{path}\n\nIt only works together with your passphrase. Keep a copy with "
                "your recovery kit, away from this computer, then delete this one if it's on this computer.")
        else:
            QMessageBox.warning(self, "Keep", f"The key file couldn't be exported.\n\n{message}".strip())

    def done(self, result):
        if self.worker is not None and self.worker.isRunning():
            self.export_button.setText("Finishing export…")
            return
        super().done(result)

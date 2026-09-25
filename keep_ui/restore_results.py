"""Bounded restore summary with scrollable, copyable diagnostics."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QDialogButtonBox


class RestoreResultsDialog(QDialog):
    def __init__(self, parent, done, failed, directory, direct=False):
        super().__init__(parent)
        self.setWindowTitle("Restore results")
        layout = QVBoxLayout(self)
        summary = QLabel(f"Restored: {len(done)} · Failed: {len(failed)}")
        layout.addWidget(summary)
        location = QLabel("Previous content saved to:" if direct else "Restored files saved to:")
        layout.addWidget(location)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Restore results and file paths")
        sections = [str(directory)]
        if failed:
            sections.append("Failed (some files may have been copied):\n" + "\n\n".join(failed))
        if done:
            sections.append("Restored:\n" + "\n\n".join(done))
        self.details.setPlainText("\n\n".join(sections))
        layout.addWidget(self.details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)
        available = self.screen().availableGeometry()
        self.resize(min(760, int(available.width() * .9)), min(520, int(available.height() * .85)))


def show_restore_results(parent, done, failed, directory, direct=False):
    RestoreResultsDialog(parent, done, failed, directory, direct).exec()

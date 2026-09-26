"""Bounded restore summary with scrollable, copyable diagnostics."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QDialogButtonBox


class RestoreResultsDialog(QDialog):
    def __init__(self, parent, done, failed, directory, direct=False, skipped=()):
        super().__init__(parent)
        self.setWindowTitle("Restore results")
        layout = QVBoxLayout(self)
        counts = f"Restored: {len(done)} · Failed: {len(failed)}"
        if skipped:
            counts += f" · Skipped: {len(skipped)}"
        summary = QLabel(counts)
        layout.addWidget(summary)
        location = QLabel("Previous content saved to:" if direct else "Restored files saved to:")
        layout.addWidget(location)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Restore results and file paths")
        sections = [str(directory)]
        if failed:
            heading = "Failed (some files may have been copied):"
            if not direct:
                heading = ("Not finished. Files already copied are complete, and the folder "
                           "is marked with RESTORE-INCOMPLETE.txt:")
            sections.append(heading + "\n" + "\n\n".join(failed))
        if skipped:
            sections.append("Skipped (sockets, pipes and device files can't be restored as files):\n"
                            + "\n".join(skipped))
        if done:
            sections.append("Restored:\n" + "\n\n".join(done))
        self.details.setPlainText("\n\n".join(sections))
        layout.addWidget(self.details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)
        available = self.screen().availableGeometry()
        self.resize(min(760, int(available.width() * .9)), min(520, int(available.height() * .85)))


def show_restore_results(parent, done, failed, directory, direct=False, skipped=()):
    RestoreResultsDialog(parent, done, failed, directory, direct, skipped).exec()

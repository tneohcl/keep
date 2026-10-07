"""Responsive modal copy operation; the caller retains the archive mount."""
import threading
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton
from .safe_copy import restore_entries


class RestoreWorker(QThread):
    progress = Signal(int, int)
    def __init__(self, entries, destination):
        super().__init__()
        self.entries, self.destination = tuple(entries), destination
        self.cancelled = threading.Event()
        self.result = ([], ["Restore did not finish"], [])

    def run(self):
        try:
            self.result = restore_entries(self.entries, self.destination, self.cancelled.is_set, self.progress.emit)
        except Exception as exc:
            self.result = ([], [str(exc)], [])


class RestoreProgress(QDialog):
    def __init__(self, parent, worker):
        super().__init__(parent)
        self.worker = worker
        self.setWindowTitle("Restoring copies")
        layout = QVBoxLayout(self)
        self.label = QLabel("Copying into a new folder…")
        layout.addWidget(self.label)
        bar = QProgressBar()
        bar.setRange(0, len(worker.entries))
        layout.addWidget(bar)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.reject)
        layout.addWidget(self.cancel)
        worker.progress.connect(lambda count, total: bar.setValue(count))
        worker.finished.connect(self.accept)

    def reject(self):
        # Keep the dialog and mount alive until the worker acknowledges cancel.
        self.worker.cancelled.set()
        self.label.setText("Cancelling… Partial copies will be kept for inspection.")
        self.cancel.setEnabled(False)


def run_restore(parent, entries, destination):
    worker = RestoreWorker(entries, destination)
    dialog = RestoreProgress(parent, worker)
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, worker.start)
    dialog.exec()
    worker.wait()
    return worker.result

"""Qt adapter for bounded background Borg mounts."""
import subprocess
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QProgressDialog

class ArchiveOpenProgress(QProgressDialog):
    """Mount cannot be cancelled safely halfway through establishing FUSE."""
    def reject(self):
        pass

    def closeEvent(self, event):
        event.ignore()


class ArchiveMountWorker(QThread):
    """Run the bounded Borg mount away from the GUI thread."""
    def __init__(self, command, environment, parent=None):
        super().__init__(parent)
        self.command, self.environment = command, environment
        self.result = (False, "Could not open backup.")

    def run(self):
        try:
            result = subprocess.run(self.command, stdin=subprocess.DEVNULL,
                                    env=self.environment, capture_output=True, text=True, timeout=60)
            self.result = (result.returncode == 0, result.stderr)
        except subprocess.TimeoutExpired:
            self.result = (False, "Timed out while opening this backup.")
        except OSError as error:
            self.result = (False, str(error))


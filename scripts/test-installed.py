"""Smoke-test installed files, without importing from the source checkout."""
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
os.environ["QT_QPA_PLATFORM"] = "offscreen"
with tempfile.TemporaryDirectory() as directory:
    os.environ["KEEP_CONFIG_PATH"] = str(Path(directory) / "config.json")
    from keep_backup.ui import main_window
    INSTALLED = Path("/usr/lib/python3/dist-packages/keep_backup")
    assert Path(main_window.__file__).resolve() == INSTALLED / "ui" / "main_window.py"
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import QCoreApplication, QEvent
    app = QApplication([])
    main_window.CONFIG["setup_complete"] = True
    with patch.object(main_window.MainWindow, "refresh_status", lambda self: None):
        window = main_window.MainWindow()
    window.show()
    app.processEvents()
    assert window.pages.count() == 2
    assert app.styleSheet() and "$" not in app.styleSheet()
    assert not QPixmap(str(INSTALLED / "ui" / "assets" / "chevron-down.svg")).isNull()
    assert (INSTALLED / "BUILD_INFO").is_file()
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
print("Installed Debian package imports, constructs both pages and resolves theme assets")

# The commands the package installs, as the menu and a timer run them.
subprocess.run(["/usr/bin/keep-cli", "--version"], check=True)
subprocess.run(["/usr/bin/keep-backup", "--help"], check=True, capture_output=True)
# The old path that timer units from the previous layout still run.
subprocess.run(["/usr/bin/python3", "/usr/lib/keep/keep_backup.py", "--help"], check=True, capture_output=True)
for name in ("applications/io.github.tneohcl.Keep.desktop", "icons/hicolor/scalable/apps/io.github.tneohcl.Keep.svg",
             "metainfo/io.github.tneohcl.Keep.metainfo.xml"):
    assert Path("/usr/share", name).is_file(), name

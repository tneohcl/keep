"""Smoke-test installed files, without importing from the source checkout."""
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
os.environ["QT_QPA_PLATFORM"] = "offscreen"
with tempfile.TemporaryDirectory() as directory:
    os.environ["KEEP_CONFIG_PATH"] = str(Path(directory) / "config.json")
    import main
    assert Path(main.__file__).resolve() == Path("/usr/lib/keep/main.py")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import QCoreApplication, QEvent
    app = QApplication([])
    main.CONFIG["setup_complete"] = True
    with patch.object(main.MainWindow, "refresh_status", lambda self: None):
        window = main.MainWindow()
    window.show()
    app.processEvents()
    assert window.pages.count() == 2
    assert app.styleSheet() and "$" not in app.styleSheet()
    assert not QPixmap("/usr/lib/keep/chevron-down.svg").isNull()
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
print("Installed Debian package imports, constructs both pages and resolves theme assets")

subprocess.run(["/usr/bin/python3", "-S", "/usr/lib/keep/cli.py", "--version"], check=True)

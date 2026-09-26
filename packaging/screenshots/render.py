"""Render Keep Backup's screenshots from the demo user (packaging/screenshots.sh
runs it after build.py, in the same namespace). The real app, real data,
offscreen: render.py APP OUT THEME SHOTS."""
import os
from pathlib import Path
import sys
import time

APP, OUT, THEME = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
SHOTS = sys.argv[4].split(",")
sys.path.insert(0, str(APP))
os.chdir(APP)

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtGui import QFont, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
app.setFont(QFont("Noto Sans", 10))
# what a desktop session gives Qt (offscreen has no platform theme to do it)
QIcon.setThemeSearchPaths(QIcon.themeSearchPaths() + [f"{root}/icons" for root in os.environ["XDG_DATA_DIRS"].split(":")])
QIcon.setThemeName("breeze-dark" if THEME == "dark" else "breeze")

import main  # noqa: E402

main.CONFIG["theme"] = THEME
main.extend_icon_search_paths()


def settle(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def save(widget, name):
    image = widget.grab()
    image.save(str(OUT / f"{name}.png"))
    print("saved", name, image.size().toTuple(), flush=True)


window = main.MainWindow()
window.resize(1200, 800)
window.show()
settle(6)
print("headline:", window.lbl_headline.text(), "| last:", window.lbl_last.text(), "| next:", window.lbl_next.text(), flush=True)
if "status" in SHOTS:
    save(window, f"status-{THEME}")
if "restore" in SHOTS:
    window.pages.setCurrentIndex(1)
    settle(10)
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidget
    for listing in window.pages.widget(1).findChildren(QListWidget):
        for i in range(listing.count()):
            item = listing.item(i)
            if item.text().split("\n")[0] in ("GNU Image Manipulation Program", "Krita"):
                item.setCheckState(Qt.Checked)
    settle(1)
    save(window, f"restore-{THEME}")
    window.pages.setCurrentIndex(0)
    settle(1)
if "applications" in SHOTS:
    dialog = main.ApplicationSelectionDialog(main.CONFIG, Path.home(), window)
    dialog.resize(760, 560)
    dialog.show()
    settle(1)
    dialog.all_data.setChecked(False)  # choosing apps one by one
    from PySide6.QtCore import Qt
    for i in range(dialog.items.count()):
        item = dialog.items.item(i)
        item.setCheckState(Qt.Unchecked if item.text() in ("Spotify", "Flatseal") else Qt.Checked)
    settle(1)
    save(dialog, f"applications-{THEME}")
    dialog.close()
if "recovery" in SHOTS:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    def grab_dialog():
        for widget in app.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                widget.resize(max(widget.width(), 700), 820)
                settle(1)
                save(widget, f"recovery-{THEME}")
                widget.reject()
                return
        print("no recovery dialog", flush=True)

    QTimer.singleShot(3000, grab_dialog)
    window.show_recovery_access()
window.close()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
os._exit(0)

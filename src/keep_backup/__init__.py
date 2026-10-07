"""Keep Backup: a desktop app around BorgBackup, by ODCS App Studio.

core/ holds the engine logic and has no Qt; ui/ holds the Qt application;
engine.py (`keep-backup`, what the timer runs) and cli.py (`keep-cli`) are
the headless entry points. `python -m keep_backup` opens the window.
"""
from pathlib import Path

VERSION = "0.9.2"
# The product name wherever it is shown. The app ID, the `keep` command and
# archive names stay "keep".
APP_NAME = "Keep Backup"

PACKAGE_DIR = Path(__file__).resolve().parent
# A source checkout's root: it holds the keep_backup.py launcher (which old
# timer units and "Back up now" run) and, on an old install, a legacy
# config.json. None when Keep is installed as a package (Flatpak, .deb).
_root = PACKAGE_DIR.parent.parent
SOURCE_ROOT = _root if (_root / "keep_backup.py").is_file() else None

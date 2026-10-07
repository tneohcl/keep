#!/usr/bin/env python3
"""The checkout's launcher for the backup engine, and a stand-in for the package.

Kept at this path on purpose: timer units written by older versions of Keep
run `python3 <checkout>/keep_backup.py --config ...`, and "Back up now" in a
source checkout runs it too. Because it sits at the checkout root, Python also
finds it as "keep_backup" whenever the root is on the import path (anything
started from the root): then it loads the real package from src/ in its place.
Installed copies use the `keep-backup` command instead.
"""
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

if __name__ == "__main__" and __spec__ is None:
    # Run as a script: one backup.
    from keep_backup.engine import main
    raise SystemExit(main())
elif __name__ == "__main__":
    # `python -m keep_backup` from the root: the window, as from anywhere else.
    import runpy
    del sys.modules["__main__"]
    runpy.run_module("keep_backup", run_name="__main__", alter_sys=True)
else:
    # Imported as "keep_backup": a module may replace itself in sys.modules.
    import importlib
    del sys.modules[__name__]
    sys.modules[__name__] = importlib.import_module(__name__)

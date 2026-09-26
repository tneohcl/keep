"""Keep's version, and where this copy was built from."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

VERSION = "0.9.2"
# The product name wherever it is shown. The app ID, the `keep` command and
# archive names stay "keep".
APP_NAME = "Keep Backup"
_HERE = Path(__file__).resolve().parent
_EARLIEST = datetime(2020, 1, 1, tzinfo=timezone.utc)


def build_info(root=None):
    """A short build line such as '2026-09-26 15:28 · Flatpak', or None.

    Packages record it in BUILD_INFO when they're built (packaging/); a
    source checkout reports its git commit. File timestamps are never used:
    packagers such as Flatpak reset them to 1970."""
    root = Path(root or _HERE)
    try:
        record = json.loads((root / "BUILD_INFO").read_text(encoding="utf-8"))
        built = datetime.fromisoformat(record["built"])
        if built >= _EARLIEST:
            parts = [built.astimezone().strftime("%Y-%m-%d %H:%M"), record.get("by"), record.get("commit")]
            return " · ".join(part for part in parts if part)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cd · %h", "--date=format-local:%Y-%m-%d %H:%M"],
                             cwd=root, capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return f"{out.stdout.strip()} · source"
    except (OSError, subprocess.SubprocessError):
        pass
    return None

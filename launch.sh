#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Keep source/preview launcher.
# Prefer an already-working interpreter, but make a self-contained local
# environment on first run when PySide6 is not installed system-wide.

has_pyside6() {
    local py="$1"
    [ -x "$py" ] && "$py" -c 'import PySide6' >/dev/null 2>&1
}

try_exec() {
    local py="$1"
    if has_pyside6 "$py"; then
        exec "$py" main.py
    fi
}

# 1) Explicit override for testers/packagers.
if [ -n "${KEEP_PYTHON:-}" ]; then
    try_exec "$KEEP_PYTHON"
fi

# 2) Existing-install compatibility: honor the interpreter already recorded in
#    config.json, but only if it can actually import PySide6.
VENV_PYTHON=""
if [ -f config.json ]; then
    VENV_PYTHON="$(python3 - <<'PY' 2>/dev/null || true
import json
try:
    with open('config.json', encoding='utf-8') as f:
        print(json.load(f).get('venv_python', ''))
except Exception:
    print('')
PY
)"
fi
if [ -n "$VENV_PYTHON" ]; then
    try_exec "$VENV_PYTHON"
fi

# 3) Local environment created by this launcher.
if [ -x .venv/bin/python ]; then
    try_exec "$PWD/.venv/bin/python"
fi

# 4) System Python may already have PySide6.
if command -v python3 >/dev/null 2>&1; then
    SYSTEM_PYTHON="$(command -v python3)"
    try_exec "$SYSTEM_PYTHON"
else
    echo "Keep could not find Python 3." >&2
    echo "Install Python 3, then run ./launch.sh again." >&2
    exit 1
fi

# 5) Preview/source convenience: bootstrap only the GUI dependency into a
#    private .venv. This does not modify the system Python installation.
echo "Keep: PySide6 is not installed for the available Python interpreter."
echo "Keep: creating a private environment in $PWD/.venv ..."

if ! "$SYSTEM_PYTHON" -m venv .venv >/dev/null 2>&1; then
    cat >&2 <<'MSG'
Keep could not create its private Python environment.

On Debian/Ubuntu-based systems, install the venv support package first:

    sudo apt install python3-venv

Then run:

    ./launch.sh
MSG
    exit 1
fi

if ! .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt; then
    cat >&2 <<'MSG'

Keep could not install its GUI dependency (PySide6).
Check your internet connection and the pip error above, then run ./launch.sh again.
The failed install is isolated inside this Keep folder's .venv directory.
MSG
    exit 1
fi

if ! has_pyside6 "$PWD/.venv/bin/python"; then
    echo "Keep installed dependencies, but PySide6 still cannot be imported." >&2
    exit 1
fi

exec "$PWD/.venv/bin/python" main.py

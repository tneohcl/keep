#!/bin/bash
# Failure-scenario acceptance checks (see packaging/acceptance.py). Runs in an
# unprivileged user + mount namespace with a throwaway HOME: your real
# config, passphrase, logs and backups are never touched.
#   packaging/acceptance.sh [python-with-PySide6]
set -euo pipefail
cd "$(dirname "$0")/.."
python="${1:-${KEEP_PYTHON:-.venv/bin/python}}"
[ -x "$python" ] || python="$(command -v python3)"
"$python" -c 'import PySide6' 2>/dev/null || { echo "Needs a Python with PySide6 (pass it as the first argument)." >&2; exit 2; }
command -v borg >/dev/null || { echo "Needs borg." >&2; exit 2; }
exec unshare --user --map-root-user --mount env KEEP_ACCEPTANCE_NS=1 "$python" packaging/acceptance.py

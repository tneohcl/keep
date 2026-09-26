#!/bin/bash
# Regenerate docs/screenshots (README and the Flathub listing) from the real
# app and real Borg backups of a demo user, "alex". Runs in an unprivileged
# user + mount + UTS namespace: /home and /run/media are empty tmpfs mounts,
# the hostname is "workstation", and a stand-in systemctl answers for the
# demo's timer, so your own files, backups and schedule are never touched.
#   packaging/screenshots.sh [python-with-PySide6]
set -euo pipefail
cd "$(dirname "$0")/.."
python="${1:-${KEEP_PYTHON:-.venv/bin/python}}"
[ -x "$python" ] || python="$(command -v python3)"
"$python" -c 'import PySide6' 2>/dev/null || { echo "Needs a Python with PySide6 (pass it as the first argument)." >&2; exit 2; }
command -v borg >/dev/null || { echo "Needs borg." >&2; exit 2; }
export KEEP_SHOTS_PYTHON="$python" KEEP_SHOTS_APP="$(pwd)" KEEP_SHOTS_OUT="$(pwd)/docs/screenshots"
export KEEP_SHOTS_TZ="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)"
exec unshare --user --map-root-user --mount --uts bash -c '
  set -euo pipefail
  here="$KEEP_SHOTS_APP/packaging/screenshots"
  mount -t tmpfs tmpfs /home && mkdir -p /home/alex && mount -t tmpfs tmpfs /run/media && hostname workstation
  "$KEEP_SHOTS_PYTHON" "$here/build.py" "$KEEP_SHOTS_APP" > /home/build.log 2>&1 || { cat /home/build.log >&2; exit 1; }
  mkdir -p "$KEEP_SHOTS_OUT"
  render() {  # render THEME SHOTS: the real app, offscreen at 2x, as a desktop session starts it
    env -i PATH="$here/bin:/usr/bin:/bin" HOME=/home/alex USER=alex LOGNAME=alex LANG=en_US.UTF-8 TZ="$KEEP_SHOTS_TZ" \
        XDG_DATA_DIRS=/home/alex/.local/share/flatpak/exports/share:/var/lib/flatpak/exports/share:/usr/local/share:/usr/share \
        QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2 \
        "$KEEP_SHOTS_PYTHON" "$here/render.py" "$KEEP_SHOTS_APP" "$KEEP_SHOTS_OUT" "$1" "$2"
  }
  render light status,restore,applications,recovery
  render dark status,restore
'

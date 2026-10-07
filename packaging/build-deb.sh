#!/bin/bash
# Target Debian 13. Runtime dependencies are resolved by apt at install.
set -euo pipefail
cd "$(dirname "$0")/.."
version=${1:-$(PYTHONPATH=src python3 -c 'import keep_backup; print(keep_backup.VERSION)')}
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Expected numeric X.Y.Z version' >&2; exit 2; }
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
id=io.github.tneohcl.Keep
package="$stage/usr/lib/python3/dist-packages/keep_backup"
mkdir -p "$stage/DEBIAN" "$stage/usr/bin" dist
cat > "$stage/DEBIAN/control" <<CONTROL
Package: keep-backup
Version: $version
Section: utils
Priority: optional
Architecture: all
Maintainer: Keep maintainers
Depends: python3 (>= 3.10), python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets, qt6-svg-plugins, borgbackup (>= 1.2), borgbackup (<< 2), fuse, python3-llfuse
Description: Linux desktop backup and recovery with BorgBackup
 Versioned encrypted backups, automatic schedules and safe file recovery.
CONTROL
# The whole package: code, stylesheet, icons and the bundled odcs-ui (pinned
# by scripts/sync-odcs-ui.sh; not available from apt). Into dist-packages, so
# `python3 -m keep_backup.engine` (what the timer runs) works without any
# path setup. User configuration lives outside src/, so it can't be packaged.
(cd src && find keep_backup -type f ! -path '*/__pycache__/*' ! -name '*.pyc') | while read -r file; do
    install -Dm644 "src/$file" "$stage/usr/lib/python3/dist-packages/$file"
done
# What About shows as the build (version.build_info); never a file's mtime.
printf '{"built": "%s", "by": "Debian package %s", "commit": "%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" "$version" "$(git rev-parse --short HEAD 2>/dev/null || true)" > "$package/BUILD_INFO"
for command in keep:keep_backup keep-cli:keep_backup.cli keep-backup:keep_backup.engine; do
    printf '#!/bin/sh\nexec /usr/bin/python3 -m %s "$@"\n' "${command#*:}" > "$stage/usr/bin/${command%%:*}"
    chmod 755 "$stage/usr/bin/${command%%:*}"
done
# Timer units written by the old layout run /usr/lib/keep/keep_backup.py.
mkdir -p "$stage/usr/lib/keep"
cat > "$stage/usr/lib/keep/keep_backup.py" <<'LAUNCHER'
"""Older Keep timer units run this path; the engine is now keep_backup.engine."""
import sys
del sys.path[0]  # this folder: here "keep_backup" would be this file, not the package
from keep_backup.engine import main
raise SystemExit(main())
LAUNCHER
install -Dm644 "data/$id.desktop" "$stage/usr/share/applications/$id.desktop"
install -Dm644 "data/$id.svg" "$stage/usr/share/icons/hicolor/scalable/apps/$id.svg"
install -Dm644 "data/$id.metainfo.xml" "$stage/usr/share/metainfo/$id.metainfo.xml"
dpkg-deb --root-owner-group --build "$stage" "dist/keep-backup_${version}_all.deb"

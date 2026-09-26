#!/bin/bash
# Target Debian 13. Runtime dependencies are resolved by apt at install.
set -euo pipefail
cd "$(dirname "$0")/.."
version=${1:-$(python3 -c 'import version; print(version.VERSION)')}
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Expected numeric X.Y.Z version' >&2; exit 2; }
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
mkdir -p "$stage/usr/lib/keep/keep_ui" "$stage/DEBIAN" "$stage/usr/lib/keep" "$stage/usr/bin" "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/scalable/apps" dist
cat > "$stage/DEBIAN/control" <<EOF
Package: keep-backup
Version: $version
Section: utils
Priority: optional
Architecture: all
Maintainer: Keep maintainers
Depends: python3 (>= 3.10), python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets, qt6-svg-plugins, borgbackup (>= 1.2), borgbackup (<< 2), fuse, python3-llfuse
Description: Linux desktop backup and recovery with BorgBackup
 Versioned encrypted backups, automatic schedules and safe file recovery.
EOF
# Explicit allowlist: never package user configuration or credentials.
install -m 644 main.py cli.py host.py version.py borg_ops.py operation_lock.py recovery_test.py app_logging.py mount_service.py consumer.py destination.py keep_backup.py applications.py themes.py theming.py style.qss chevron-down.svg check.svg "$stage/usr/lib/keep/"
install -m 644 keep_ui/*.py "$stage/usr/lib/keep/keep_ui/"
# What About shows as the build (version.build_info); never a file's mtime.
printf '{"built": "%s", "by": "Debian package %s", "commit": "%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" "$version" "$(git rev-parse --short HEAD 2>/dev/null || true)" > "$stage/usr/lib/keep/BUILD_INFO"
# Bundled odcs-ui (pinned by packaging/sync-odcs-ui.sh; not available from apt).
install -d "$stage/usr/lib/keep/vendor/odcs_ui"
install -m 644 vendor/ODCS_UI_VERSION "$stage/usr/lib/keep/vendor/"
install -m 644 vendor/odcs_ui/*.py vendor/odcs_ui/*.json vendor/odcs_ui/*.qss vendor/odcs_ui/LICENSE "$stage/usr/lib/keep/vendor/odcs_ui/"
printf '#!/bin/sh\nexec /usr/bin/python3 /usr/lib/keep/main.py "$@"\n' > "$stage/usr/bin/keep"
chmod 755 "$stage/usr/bin/keep"
printf '#!/bin/sh\nexec /usr/bin/python3 /usr/lib/keep/cli.py "$@"\n' > "$stage/usr/bin/keep-cli"
chmod 755 "$stage/usr/bin/keep-cli"
install -m 644 packaging/keep.desktop "$stage/usr/share/applications/"
install -m 644 packaging/keep.svg "$stage/usr/share/icons/hicolor/scalable/apps/"
dpkg-deb --root-owner-group --build "$stage" "dist/keep-backup_${version}_all.deb"

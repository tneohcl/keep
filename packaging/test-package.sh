#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash packaging/build-deb.sh 0.9.2
dpkg -i dist/keep-backup_0.9.2_all.deb
# Copy the probe outside the checkout so sys.path cannot hide missing files.
probe_dir=$(mktemp -d)
trap 'rm -rf -- "$probe_dir"' EXIT
cp packaging/test-installed.py "$probe_dir/probe.py"
cd "$probe_dir"
PYTHONPATH=/usr/lib/keep python3 probe.py

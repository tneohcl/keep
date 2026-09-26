#!/bin/bash
set -euo pipefail
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
export HOME="$scratch" KEEP_CONFIG_PATH="$scratch/config.json" QT_QPA_PLATFORM=offscreen
python3 -m unittest -v test_release_safety
python3 -m unittest -v test_applications test_theming test_mount_service test_app_logging test_restore_copy test_backup_locking test_cli test_destination_mounts test_vendor test_recovery_test test_review_fixes test_pr3_followups test_failure_reasons test_acceptance_harness test_host
python3 test_gui_smoke.py
python3 test_consumer_features.py
python3 test_unlock_recovery.py

bash packaging/test-package.sh

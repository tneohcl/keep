#!/bin/bash
# The full local suite, as CI runs it. Tests live in tests/ and run from the
# repository root, where the app modules are imported from.
set -euo pipefail
cd "$(dirname "$0")/.."
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
export HOME="$scratch" KEEP_CONFIG_PATH="$scratch/config.json" QT_QPA_PLATFORM=offscreen

# Checks written as scripts (they run their checks at import), not unittest modules.
scripts=(test_gui_smoke test_consumer_features test_unlock_recovery)
# Every other tests/test_*.py is a unittest module, found here so none is forgotten.
modules=()
for path in tests/test_*.py; do
    name=$(basename "$path" .py)
    [[ " ${scripts[*]} test_release_safety " == *" $name "* ]] || modules+=("tests.$name")
done

python3 -m unittest -v tests.test_release_safety
python3 -m unittest -v "${modules[@]}"
for name in "${scripts[@]}"; do
    python3 -m "tests.$name"
done

bash packaging/test-package.sh

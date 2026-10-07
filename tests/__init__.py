"""Keep Backup tests. Run from the repository root (scripts/test-linux.sh);
this puts src/ first on the import path, so the checkout's package is tested."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

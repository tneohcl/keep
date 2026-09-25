"""Keep's colour tokens now come from odcs-ui (vendor/odcs_ui/tokens.json).

Kept as a module so existing imports (`import themes`) keep working.
"""
import sys
from pathlib import Path

_VENDOR = Path(__file__).with_name("vendor")
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from odcs_ui.tokens import DARK, LIGHT, THEMES  # noqa: E402,F401

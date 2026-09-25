"""The bundled odcs-ui must be exactly the pinned release (packaging/sync-odcs-ui.sh)."""
import unittest
from pathlib import Path

import theming  # noqa: F401  (puts vendor/ on sys.path)
import odcs_ui

ROOT = Path(__file__).resolve().parent


class VendoredOdcsUi(unittest.TestCase):
    def test_bundled_version_matches_the_pin(self):
        tag, commit = (ROOT / "vendor" / "ODCS_UI_VERSION").read_text().split()
        self.assertEqual("v" + odcs_ui.__version__, tag)
        self.assertEqual(len(commit), 40)

    def test_keep_imports_the_bundled_copy(self):
        self.assertEqual(Path(odcs_ui.__file__).resolve().parent, ROOT / "vendor" / "odcs_ui")

    def test_bundled_copy_is_complete(self):
        names = {p.name for p in (ROOT / "vendor" / "odcs_ui").iterdir()}
        for required in ("tokens.json", "base.qss", "theming.py", "widgets.py", "timefmt.py", "LICENSE"):
            self.assertIn(required, names)


if __name__ == "__main__":
    unittest.main()

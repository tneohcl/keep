"""Keep's version and build line (the Flatpak's About said 'Build: 1970-01-01')."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_STATE = tempfile.TemporaryDirectory()
os.environ["KEEP_CONFIG_PATH"] = str(Path(_STATE.name) / "config.json")
os.environ["XDG_STATE_HOME"] = str(Path(_STATE.name) / "state")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

_app = QApplication.instance() or QApplication([])

import version  # noqa: E402


class BuildInfo(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_packaged_build_info_is_shown_in_local_time(self):
        (self.root / "BUILD_INFO").write_text(json.dumps({"built": "2026-09-26T07:28:49+00:00", "by": "Flatpak"}))
        import time
        self.addCleanup(time.tzset)             # after patch.dict restores TZ
        with patch.dict(os.environ, {"TZ": "Asia/Singapore"}):
            time.tzset()
            self.assertEqual(version.build_info(self.root), "2026-09-26 15:28 · Flatpak")

    @unittest.skipUnless(shutil.which("git"), "needs git (the CI image has none)")
    def test_source_checkout_shows_its_commit(self):
        run = lambda *args: subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True)
        run("init", "-q")
        (self.root / "main.py").write_text("")
        run("add", "main.py")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.root,
                                capture_output=True, text=True).stdout.strip()
        self.assertIn(commit, version.build_info(self.root))

    def test_nothing_trustworthy_means_no_build_line(self):
        (self.root / "BUILD_INFO").write_text(json.dumps({"built": "1970-01-01T00:00:00+00:00", "by": "Flatpak"}))
        self.assertIsNone(version.build_info(self.root))
        self.assertIsNone(version.build_info(Path(tempfile.mkdtemp())))


class AboutDialog(unittest.TestCase):
    def test_about_shows_the_version_and_never_1970(self):
        import main
        with patch("os.path.getmtime", return_value=0), patch.object(version, "build_info", return_value=None):
            about = main.AboutDialog()
        text = " ".join(label.text() for label in about.findChildren(QLabel))
        self.assertIn(f"Version {version.VERSION}", text)
        self.assertNotIn("1970", text)


if __name__ == "__main__":
    unittest.main()

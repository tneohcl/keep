"""Restore real filesystem fixtures without accessing user configuration."""
import os
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch

with tempfile.TemporaryDirectory() as config_dir:
    with patch.dict(os.environ, {"KEEP_CONFIG_PATH": str(Path(config_dir) / "config.json")}):
        import main


@unittest.skipUnless(os.name == "posix", "Requires Linux symlinks and Unix sockets")
class RestoreCopyTests(unittest.TestCase):
    def test_nested_runtime_links_preserved_by_both_copy_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "archive" / "config" / "discord"
            source.mkdir(parents=True)
            (source / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
            external = root / "external"
            external.mkdir()
            (external / "untouched").write_text("original", encoding="utf-8")
            with socket.socket(socket.AF_UNIX) as runtime_socket:
                socket_path = root / "runtime.sock"
                runtime_socket.bind(str(socket_path))
                targets = {
                    "SingletonLock": "old-host-12345",
                    "SingletonCookie": "/missing/runtime/cookie",
                    "SingletonSocket": str(socket_path),
                    "linked-directory": str(external),
                }
                for name, target in targets.items():
                    (source / name).symlink_to(target)
                for mode in ("safe-and-undo", "direct"):
                    with self.subTest(mode=mode):
                        dest = root / mode
                        if mode == "direct":
                            dest.mkdir()
                            (dest / "obsolete").write_text("old", encoding="utf-8")
                            main.ItemPicker._replace_live_path(None, str(source.parent.parent), str(dest))
                            self.assertFalse((dest / "obsolete").exists())
                        else:
                            main.copy_item(str(source.parent.parent), str(dest))
                        restored = dest / "config" / "discord"
                        self.assertEqual((restored / "settings.json").read_bytes(), (source / "settings.json").read_bytes())
                        for name, target in targets.items():
                            self.assertTrue((restored / name).is_symlink())
                            self.assertEqual(os.readlink(restored / name), target)
                self.assertEqual((external / "untouched").read_text(), "original")

    def test_real_copy_errors_are_not_suppressed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "archive"
            source.mkdir()
            with socket.socket(socket.AF_UNIX) as runtime_socket:
                runtime_socket.bind(str(source / "actual-socket"))
                with self.assertRaises(shutil.Error):
                    main.copy_item(str(source), str(root / "restored"))


if __name__ == "__main__":
    unittest.main()

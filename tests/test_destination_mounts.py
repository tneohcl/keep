import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import destination


class MountParsingTests(unittest.TestCase):
    def test_stacked_autofs_and_cifs(self):
        rows = [{"target": "/mnt/neonas/home", "fstype": kind} for kind in ("autofs", "cifs")]
        with patch.object(destination.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps({"filesystems": rows}))):
            self.assertEqual(destination.mount_details("/mnt/neonas/home"), ("/mnt/neonas/home", "cifs"))

    def test_autofs_alone_is_not_connected_storage(self):
        with patch.object(destination.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout='{"filesystems":[{"target":"/mnt/nas","fstype":"autofs"}]}')):
            self.assertIsNone(destination.mount_details("/mnt/nas"))

    def test_legacy_setting_requires_real_matching_filesystem(self):
        config = {"type": "network", "repo": "/mnt/neonas/home/backups/test", "mount_check": "/mnt/neonas/home",
                  "fstype": "autofs\n/mnt/neonas/home cifs"}
        with patch.object(destination.os.path, "ismount", return_value=True), patch.object(destination, "_looks_like_borg_repo", return_value=True):
            for actual, expected in (("cifs", True), ("ext4", False), (None, False)):
                with patch.object(destination, "_fstype_of", return_value=actual):
                    self.assertEqual(destination.resolve_destination(config)["available"], expected)


if __name__ == "__main__":
    unittest.main()

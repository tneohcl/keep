"""Host tools, the shared mount path and the timer command inside a Flatpak."""
import os
import unittest
from unittest.mock import patch

import consumer
import host

APP_ID = "io.github.tneohcl.Keep"


class OutsideFlatpak(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(host, "flatpak_id", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_commands_run_unchanged(self):
        self.assertEqual(host.command(["systemctl", "--user", "daemon-reload"]), ["systemctl", "--user", "daemon-reload"])
        self.assertEqual(host.shared_path("borg-keep-mount"), "/tmp/borg-keep-mount")
        self.assertIsNone(host.backup_command("/home/me/.config/keep/config.json"))

    def test_timer_runs_keep_backup_directly(self):
        cmd = consumer.backup_command({"backup_engine": "builtin"}, "/c.json", "/opt/keep", "/usr/bin/python3")
        self.assertEqual(cmd, ["/usr/bin/python3", "/opt/keep/keep_backup.py", "--config", "/c.json"])


class InsideFlatpak(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(host, "flatpak_id", return_value=APP_ID)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_host_tools_go_through_flatpak_spawn(self):
        self.assertEqual(host.command(["findmnt", "-T", "/mnt/nas"]),
                         ["flatpak-spawn", "--host", "findmnt", "-T", "/mnt/nas"])

    def test_mount_point_is_shared_with_the_host(self):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}):
            self.assertEqual(host.shared_path("borg-keep-mount"), f"/run/user/1000/app/{APP_ID}/borg-keep-mount")

    def test_timer_runs_the_flatpak(self):
        config = {"backup_engine": "builtin", "schedule": {"time": "04:00"}}
        service, _ = consumer.render_systemd_units(config, "/home/me/.config/keep/config.json", "/app/share/keep",
                                                   "/usr/bin/python3")
        self.assertIn(f'ExecStart="/usr/bin/flatpak" "run" "--command=keep-backup" "{APP_ID}" "--config" '
                      '"/home/me/.config/keep/config.json"', service)


class Detection(unittest.TestCase):
    def test_needs_both_the_app_id_and_the_sandbox_marker(self):
        with patch.dict(os.environ, {"FLATPAK_ID": APP_ID}), patch("os.path.exists", return_value=False):
            self.assertIsNone(host.flatpak_id())
        with patch.dict(os.environ, {"FLATPAK_ID": APP_ID}), patch("os.path.exists", return_value=True):
            self.assertEqual(host.flatpak_id(), APP_ID)
        with patch.dict(os.environ, {}, clear=True), patch("os.path.exists", return_value=True):
            self.assertIsNone(host.flatpak_id())


if __name__ == "__main__":
    unittest.main()

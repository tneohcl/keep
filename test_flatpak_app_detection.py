"""Inside the Flatpak, Keep must see the host's installed apps: its /usr is the
runtime's, so native launchers and executables are under /run/host (host-os),
and system Flatpaks under /var/lib/flatpak. (Reported: the app chooser showed
only Keep and Builder, the two per-user Flatpaks.)"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import applications
import host

APP_ID = "io.github.tneohcl.Keep"


def desktop(path, name, exec_line, extra=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\n{extra}")


class HostAppsFromInsideTheFlatpak(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.home, self.host_root, self.sandbox = tmp / "home", tmp / "run-host", tmp / "sandbox"
        # The host: a native app with its launcher and executable in /usr...
        desktop(self.host_root / "usr/share/applications/org.inkscape.Inkscape.desktop", "Inkscape", "inkscape %F")
        binary = self.host_root / "usr/bin/inkscape"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        # ...and a system-wide Flatpak with its exported launcher.
        system = tmp / "var-lib-flatpak"
        (system / "app/org.mozilla.firefox/current/active").mkdir(parents=True)
        desktop(system / "exports/share/applications/org.mozilla.firefox.desktop", "Firefox Web Browser",
                "/usr/bin/flatpak run org.mozilla.firefox", "X-Flatpak=org.mozilla.firefox\n")
        # Their data in home.
        (self.home / ".config/inkscape").mkdir(parents=True)
        (self.home / ".var/app/org.mozilla.firefox").mkdir(parents=True)
        # The sandbox's own /usr and PATH know nothing about them.
        (self.sandbox / "share/applications").mkdir(parents=True)
        for patcher in (patch.object(host, "flatpak_id", return_value=APP_ID),
                        patch.object(host, "HOST_ROOT", str(self.host_root), create=True),
                        patch.object(host, "SYSTEM_FLATPAK", str(system), create=True),
                        patch.object(applications, "SYSTEM_FLATPAK_APP_DIR", system / "app"),
                        patch.dict(os.environ, {"XDG_DATA_DIRS": str(self.sandbox / "share"),
                                                "PATH": str(self.sandbox / "bin")})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_native_and_system_flatpak_apps_are_installed(self):
        installed = applications.InstalledApps(self.home)
        self.assertTrue(installed.contains("inkscape"), "native app via the host's /usr")
        self.assertTrue(installed.contains("org.mozilla.firefox"), "system-wide Flatpak")

    def test_the_chooser_lists_them_as_installed(self):
        installed = applications.InstalledApps(self.home)
        entries = {e["id"]: e for e in applications.catalog({}, self.home, include_unrecognized=True)}
        self.assertTrue(installed.entry_installed(entries["firefox"]))
        self.assertTrue(installed.entry_installed(entries["path:.config/inkscape"]))

    def test_absolute_symlinked_executables_resolve_on_the_host(self):
        # Like RustDesk: /usr/bin/rustdesk -> /usr/share/rustdesk/rustdesk. Followed
        # inside the sandbox, that absolute target is the runtime's /usr.
        real = self.host_root / "usr/share/keepprobeapp/keepprobeapp"
        real.parent.mkdir(parents=True)
        real.write_text("#!/bin/sh\n")
        real.chmod(0o755)
        (self.host_root / "usr/bin/keepprobeapp").symlink_to("/usr/share/keepprobeapp/keepprobeapp")
        desktop(self.host_root / "usr/share/applications/keepprobeapp.desktop", "Keep Probe App", "keepprobeapp %u")
        self.assertTrue(applications.InstalledApps(self.home).contains("keepprobeapp"))

    def test_flatpak_names_come_from_the_system_exports(self):
        # A distinctive name: display_name otherwise falls back to guessing from the ID.
        self.assertEqual(applications.display_name("org.mozilla.firefox", self.home), "Firefox Web Browser")


class OutsideTheFlatpakUnchanged(unittest.TestCase):
    def test_paths_and_lookups_are_the_host_itself(self):
        with patch.object(host, "flatpak_id", return_value=None):
            self.assertEqual(host.system_path("/usr/share/applications"), "/usr/share/applications")
            self.assertEqual(host.system_path("/var/lib/flatpak/app"), "/var/lib/flatpak/app")
            with patch.dict(os.environ, {"XDG_DATA_DIRS": "/a:/b"}):
                self.assertEqual(host.data_dirs(Path("/home/me")), [Path("/a"), Path("/b")])


if __name__ == "__main__":
    unittest.main()

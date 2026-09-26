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

    def test_links_through_etc_alternatives_resolve_on_the_host(self):
        # Review of #9: /usr/bin/X -> /etc/alternatives/X -> /usr/lib/X/X.
        # host-os shows the host's /etc/alternatives under /run/host too.
        real = self.host_root / "usr/lib/keepaltprobe/keepaltprobe"
        real.parent.mkdir(parents=True)
        real.write_text("#!/bin/sh\n")
        real.chmod(0o755)
        (self.host_root / "etc/alternatives").mkdir(parents=True)
        (self.host_root / "etc/alternatives/keepaltprobe").symlink_to("/usr/lib/keepaltprobe/keepaltprobe")
        (self.host_root / "usr/bin/keepaltprobe").symlink_to("/etc/alternatives/keepaltprobe")
        desktop(self.host_root / "usr/share/applications/keepaltprobe.desktop", "Keep Alt Probe", "keepaltprobe")
        self.assertFalse(os.path.exists("/etc/alternatives/keepaltprobe"))   # the host can't make it pass
        self.assertTrue(applications.InstalledApps(self.home).contains("keepaltprobe"))
        self.assertEqual(host.system_path("/etc/alternatives/x"), f"{self.host_root}/etc/alternatives/x")
        self.assertEqual(host.system_path("/etc/passwd"), "/etc/passwd")      # only that subtree

    def test_flatpak_names_come_from_the_system_exports(self):
        # A distinctive name: display_name otherwise falls back to guessing from the ID.
        self.assertEqual(applications.display_name("org.mozilla.firefox", self.home), "Firefox Web Browser")


class AppIcons(unittest.TestCase):
    """Reported: in the Flatpak the chooser showed generic icons. Other
    Flatpaks export their icons to .../flatpak/exports/share/icons, which the
    sandbox's icon search path leaves out."""

    def test_flatpak_icon_exports_are_searched_inside_the_flatpak(self):
        home = Path("/home/me")
        with patch.object(host, "flatpak_id", return_value=APP_ID), \
                patch.object(host, "SYSTEM_FLATPAK", "/var/lib/flatpak"):
            self.assertEqual(host.icon_dirs(home), [home / ".local/share/flatpak/exports/share/icons",
                                                    Path("/var/lib/flatpak/exports/share/icons")])
        with patch.object(host, "flatpak_id", return_value=None):
            self.assertEqual(host.icon_dirs(home), [])      # the host's own search path has them

    def test_keep_adds_them_to_qts_search_path_once(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import main
        extra = tempfile.mkdtemp()
        before = QIcon.themeSearchPaths()
        self.addCleanup(QIcon.setThemeSearchPaths, before)
        with patch.object(host, "icon_dirs", return_value=[Path(extra)]):
            main.extend_icon_search_paths()
            main.extend_icon_search_paths()
        self.assertEqual(QIcon.themeSearchPaths().count(extra), 1)

    def test_data_folder_entries_try_the_folder_name_as_their_icon(self):
        # path:.local/share/dolphin looked up an icon named "path:.local/share/dolphin".
        entry = {"id": "path:.local/share/dolphin", "paths": [".local/share/dolphin"]}
        self.assertIn("dolphin", applications.icon_names(entry))
        flatpak = {"id": "path:.var/app/org.mozilla.firefox", "paths": [".var/app/org.mozilla.firefox"]}
        self.assertEqual(applications.icon_names(flatpak)[0], "org.mozilla.firefox")


class RealIconNames(unittest.TestCase):
    """The chooser guessed icon names from IDs and folders, so Dolphin
    (Icon=system-file-manager), curated items and launchers with an icon file
    all showed the generic icon, in the source copy too."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.home = tmp / "home"
        (self.home / ".local/share/dolphin").mkdir(parents=True)
        apps = tmp / "share/applications"
        desktop(apps / "org.kde.dolphin.desktop", "Dolphin", "dolphin %u", "Icon=system-file-manager\n")
        desktop(apps / "velocoder.desktop", "VeloCoder", "velocoder", "Icon=/opt/velocoder/icon.svg\n")
        binaries = tmp / "bin"
        binaries.mkdir()
        for name in ("dolphin", "velocoder"):
            (binaries / name).write_text("#!/bin/sh\n")
            (binaries / name).chmod(0o755)
        for patcher in (patch.object(host, "flatpak_id", return_value=None),
                        patch.object(applications, "SYSTEM_FLATPAK_APP_DIR", tmp / "none"),
                        patch.dict(os.environ, {"XDG_DATA_DIRS": str(tmp / "share"), "PATH": str(binaries)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.installed = applications.InstalledApps(self.home)

    def test_launcher_icon_is_used(self):
        entry = {"id": "path:.local/share/dolphin", "label": "dolphin", "paths": [".local/share/dolphin"]}
        self.assertEqual(applications.icon_names(entry, self.installed)[0], "system-file-manager")

    def test_launcher_icon_file_path_is_kept(self):
        entry = {"id": "path:.config/VeloCoder", "label": "VeloCoder", "paths": [".config/VeloCoder"]}
        self.assertEqual(applications.icon_names(entry, self.installed)[0], "/opt/velocoder/icon.svg")

    def test_curated_items_use_their_configured_icon(self):
        config = {"curated_items": [["SSH Keys", [".ssh"], "dialog-password", "system"]]}
        (self.home / ".ssh").mkdir()
        entry = next(e for e in applications.catalog(config, self.home) if e["id"] == "curated:SSH Keys")
        self.assertEqual(applications.icon_names(entry, self.installed)[0], "dialog-password")


class RunningAppCheck(unittest.TestCase):
    """Before restoring an app's data over the live copy, Keep checks the app
    isn't running. Inside the Flatpak, pgrep sees only the sandbox's own
    processes, so every app looked closed (found in the parity audit)."""

    def test_pgrep_asks_the_host_inside_the_flatpak(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import main
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return type("Done", (), {"returncode": 0})()
        with patch.object(host, "flatpak_id", return_value=APP_ID), patch.object(main.subprocess, "run", fake_run):
            self.assertTrue(main.is_native_process_running(["krita"]))
        self.assertEqual(calls, [["flatpak-spawn", "--host", "--directory=/", "pgrep", "-x", "krita"]])


class OutsideTheFlatpakUnchanged(unittest.TestCase):
    def test_paths_and_lookups_are_the_host_itself(self):
        with patch.object(host, "flatpak_id", return_value=None):
            self.assertEqual(host.system_path("/usr/share/applications"), "/usr/share/applications")
            self.assertEqual(host.system_path("/var/lib/flatpak/app"), "/var/lib/flatpak/app")
            with patch.dict(os.environ, {"XDG_DATA_DIRS": "/a:/b"}):
                self.assertEqual(host.data_dirs(Path("/home/me")), [Path("/a"), Path("/b")])


if __name__ == "__main__":
    unittest.main()

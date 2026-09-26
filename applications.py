"""Explicit app-data mappings shared by selection, backup and archive browsing."""
import os
import configparser
import re
from pathlib import Path

import host

# These are data locations, not cross-install restore conversion rules.
KNOWN = {
    "firefox": ("Firefox", [".mozilla", ".config/mozilla"], "org.mozilla.firefox"),
    "thunderbird": ("Thunderbird", [".thunderbird", ".config/thunderbird"], "org.mozilla.Thunderbird"),
    "krita": ("Krita", [".config/kritarc", ".config/kritadisplayrc", ".local/share/krita"], "org.kde.krita"),
    "vlc": ("VLC", [".config/vlc", ".local/share/vlc"], "org.videolan.VLC"),
    "gimp": ("GIMP", [".config/GIMP"], "org.gimp.GIMP"),
    "obs": ("OBS Studio", [".config/obs-studio"], "com.obsproject.Studio"),
}


def display_name(appid, home=None):
    """Use installed desktop metadata when present, otherwise a readable name."""
    home = Path(home or Path.home())
    roots = [home / ".local/share/flatpak/exports/share/applications",
             home / ".local/share/applications",
             Path(host.system_path("/var/lib/flatpak/exports/share/applications")),
             Path(host.system_path("/usr/share/applications"))]
    for root in roots:
        try:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(root / f"{appid}.desktop", encoding="utf-8")
            name = parser.get("Desktop Entry", "Name", fallback="").strip()
            if name and name != appid:
                return name
        except (OSError, configparser.Error, UnicodeError):
            continue
    for label, _native, known_id in KNOWN.values():
        if appid == known_id:
            return label
    segment = appid.rsplit(".", 1)[-1]
    return re.sub(r"[_-]+", " ", segment).strip().title() or "Application"


def catalog(config, home=None, include_unrecognized=False):
    home = Path(home or Path.home())
    entries = []
    claimed = set()
    def add(key, label, paths, category, recognized=True, icon=None):
        paths = [p for p in paths if os.path.lexists(home / p)]
        if not paths:
            return
        claimed.update(paths)
        formats = []
        if any(not p.startswith(".var/app/") for p in paths):
            formats.append("Native")
        if any(p.startswith(".var/app/") for p in paths):
            formats.append("Flatpak")
        entries.append(dict(id=key, label=label, paths=paths, category=category,
                            formats=" + ".join(formats), recognized=recognized, icon=icon))
    for key, (label, native, flatpak) in KNOWN.items():
        add(key, label, native + [f".var/app/{flatpak}"], "applications")
    for label, paths, icon, category in config.get("curated_items", []):
        if category in ("system", "applications"):
            add("curated:" + label, label, paths, category, icon=icon)
    for root in (".config", ".local/share", ".var/app"):
        try:
            children = sorted((home / root).iterdir())
        except OSError:
            continue
        for child in children:
            rel = f"{root}/{child.name}"
            if child.name in ("borg", "keep") or any(p == rel or p.startswith(rel + "/") for p in claimed):
                continue
            flatpak = root == ".var/app"
            if not flatpak and not include_unrecognized:
                continue
            add("path:" + rel, display_name(child.name, home) if flatpak else child.name, [rel], "applications", flatpak)
    return entries


def selected_paths(config, home=None):
    home = Path(home or Path.home())
    selected = set(config.get("selected_applications", []))
    # Previously selected advanced paths remain backed up even when hidden
    # from the default app list.
    return [str(home / p) for entry in catalog(config, home, include_unrecognized=True) if entry["id"] in selected for p in entry["paths"]]


def unreviewed(config, entries):
    """App data the person hasn't decided about: not selected, and not on
    screen the last time they chose apps (known_applications). Only
    meaningful when choosing individual apps; "all" already includes it.
    Without a record yet (older configs) every unselected entry counts once."""
    if not config.get("include_app_data", True) or config.get("app_selection_mode", "all") != "selected":
        return []
    reviewed = set(config.get("known_applications", [])) | set(config.get("selected_applications", []))
    return [entry for entry in entries if entry["id"] not in reviewed]


def selection_conflict(config, sources, home=None):
    """Reject broad folder sources that would bypass explicit app selection."""
    if not config.get("include_app_data", True) or config.get("app_selection_mode", "all") != "selected":
        return None
    home = Path(home or Path.home())
    protected = [str(home / root) for root in (".config", ".local/share", ".var/app")]
    protected += [str(home / p) for e in catalog(config, home) for p in e["paths"]]
    for source in sources:
        for app_path in protected:
            source_real, app_real = os.path.realpath(source), os.path.realpath(app_path)
            try:
                common = os.path.commonpath([source_real, app_real])
            except ValueError:
                continue
            if common in (source_real, app_real):
                return source
    return None


# System-wide Flatpak deployments. A module constant so tests can point it at
# an empty directory - otherwise whatever the test machine has installed
# (e.g. Firefox or Krita as system Flatpaks) leaks into the results.
def icon_names(entry, installed=None):
    """Icons to try for a catalog entry, most specific first: a curated
    item's own icon, the icon its app's launcher names (Icon=, a theme name
    or a file path), then guesses from the Flatpak ID, the entry's ID and
    each data folder's name (path:.local/share/dolphin -> "dolphin")."""
    folders = [p.rstrip("/").split("/")[-1].lstrip(".") for p in entry["paths"]]
    flatpaks = [p.split("/")[-1] for p in entry["paths"] if p.startswith(".var/app/")]
    names = [entry.get("icon")]
    if installed is not None:
        names.append(installed.icon(*flatpaks, entry["id"], entry.get("label"), *folders))
    names += flatpaks
    if ":" not in entry["id"]:
        names.append(entry["id"])
    for folder in folders:
        names += [folder, folder.lower()]
    return list(dict.fromkeys(name for name in names if name))


SYSTEM_FLATPAK_APP_DIR = Path(host.system_path("/var/lib/flatpak/app"))


class InstalledApps:
    """One filesystem snapshot of installed launchers, not app-data directories.

    Flatpak deployments and executable-backed desktop entries are evidence of
    installation. Residual ~/.config and ~/.var/app directories are not.
    """
    def __init__(self, home=None):
        import shlex
        self.home = Path(home or Path.home())
        self.names = set()
        self.flatpak_ids = set()
        self.icons = {}                 # _key(name/file/command) -> the launcher's Icon=
        for root in (self.home / ".local/share/flatpak/app", SYSTEM_FLATPAK_APP_DIR):
            try:
                for app in root.iterdir():
                    if (app / "current/active").exists():
                        self.flatpak_ids.add(app.name)
            except OSError:
                pass
        roots = [self.home / ".local/share/applications"]
        roots += [root / "applications" for root in host.data_dirs(self.home)]
        for root in roots:
            try:
                files = root.glob("*.desktop")
                for desktop in files:
                    parser = configparser.ConfigParser(interpolation=None, strict=False)
                    try:
                        parser.read(desktop, encoding="utf-8")
                        entry = parser["Desktop Entry"]
                        if entry.get("Type", "Application") != "Application" or entry.get("Hidden", "false").lower() == "true":
                            continue
                        flatpak = entry.get("X-Flatpak")
                        if flatpak:
                            if flatpak not in self.flatpak_ids:
                                continue
                        else:
                            command = shlex.split(entry.get("TryExec") or entry.get("Exec", ""))
                            if not command:
                                continue
                            if command[0] == "env":
                                command = [part for part in command[1:] if "=" not in part and not part.startswith("-")]
                            if not command or not host.which(command[0], self.home):
                                continue
                        keys = [self._key(value) for value in (desktop.stem, entry.get("Name", ""), flatpak or "") if value]
                        if not flatpak:
                            keys.append(self._key(Path(command[0]).name))
                        self.names.update(keys)
                        if entry.get("Icon"):
                            for key in keys:
                                self.icons.setdefault(key, entry["Icon"])
                    except (OSError, configparser.Error, UnicodeError, ValueError, KeyError):
                        continue
            except OSError:
                continue
        for key, (label, _paths, appid) in KNOWN.items():
            executable = "obs" if key == "obs" else key
            if host.which(executable, self.home) or appid in self.flatpak_ids:
                self.names.update(self._key(value) for value in (key, label, appid))
        self.names.update(self._key(value) for value in self.flatpak_ids)

    @staticmethod
    def _key(value):
        value = value.removesuffix(" (not installed)")
        return re.sub(r"[^a-z0-9]", "", value.casefold())

    def icon(self, *identifiers):
        """The Icon= of the first identifier with a launcher, or None."""
        return next((self.icons[self._key(value)] for value in identifiers
                     if value and self._key(value) in self.icons), None)

    def contains(self, *identifiers):
        return any(self._key(value) in self.names for value in identifiers if value)

    def entry_installed(self, entry):
        if entry["category"] != "applications":
            return True
        ids = [entry["id"], entry["label"]]
        ids.extend(Path(path).name for path in entry["paths"] if path.startswith(".var/app/"))
        return self.contains(*ids)

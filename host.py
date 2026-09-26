"""Running Keep inside a Flatpak.

A few programs Keep runs belong to the host session, not the sandbox:
systemctl (the user timer), flatpak (installed apps), findmnt (the host's
mounts and drive UUIDs) and fusermount (archives are mounted by the host's
fusermount, see packaging/flatpak). Inside a Flatpak, command() runs them
through flatpak-spawn --host; anywhere else it leaves them unchanged.
"""
import os
from pathlib import Path
import posixpath
import shutil

FLATPAK_BIN = "/usr/bin/flatpak"
# Inside the Flatpak the sandbox's /usr is the runtime's. --filesystem=host-os
# shows the host's /usr (and /bin, /lib...) under HOST_ROOT, and
# --filesystem=/var/lib/flatpak:ro shows system Flatpaks at their own path.
HOST_ROOT = "/run/host"
SYSTEM_FLATPAK = "/var/lib/flatpak"
HOST_PATH = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/local/sbin", "/usr/sbin", "/sbin")


def flatpak_id():
    """The app ID when running inside a Flatpak, else None."""
    app_id = os.environ.get("FLATPAK_ID")
    return app_id if app_id and os.path.exists("/.flatpak-info") else None


def command(args):
    """argv for a host tool: through flatpak-spawn --host inside a Flatpak."""
    return ["flatpak-spawn", "--host", *args] if flatpak_id() else list(args)


def shared_path(name):
    """A path the host and the app see alike. /tmp is private to a Flatpak,
    so a FUSE mount made by the host's fusermount there would be invisible;
    $XDG_RUNTIME_DIR/app/<app id> is shared at the same path."""
    app_id = flatpak_id()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if app_id and runtime:
        return posixpath.join(runtime, "app", app_id, name)
    return posixpath.join("/tmp", name)


def backup_command(config_path):
    """How the host's systemd timer starts a Flatpak Keep backup, or None
    outside a Flatpak (then the timer runs keep_backup.py directly)."""
    app_id = flatpak_id()
    if not app_id:
        return None
    return [FLATPAK_BIN, "run", "--command=keep-backup", app_id, "--config", config_path]


def system_path(path):
    """Where a host system path (/usr/..., /etc/alternatives/...,
    /var/lib/flatpak/...) is found from here: inside a Flatpak, the host's /usr
    family and /etc/alternatives are under HOST_ROOT."""
    if flatpak_id():
        if path.startswith("/var/lib/flatpak"):
            return SYSTEM_FLATPAK + path[len("/var/lib/flatpak"):]
        if path.split("/")[1:2] and path.split("/")[1] in ("usr", "bin", "sbin", "lib", "lib64", "lib32"):
            return HOST_ROOT + path
        # host-os also shows the host's /etc/alternatives (and only that part
        # of /etc), where executables often link through.
        if path == "/etc/alternatives" or path.startswith("/etc/alternatives/"):
            return HOST_ROOT + path
    return path


def which(name, home):
    """The host's executable for `name`, as a path readable from here, or None."""
    if not flatpak_id():
        return shutil.which(name)
    if "/" in name:
        candidates = [system_path(name) if name.startswith("/") else name]
    else:
        folders = [system_path(folder) for folder in HOST_PATH]
        folders += [str(Path(home) / ".local/bin"), str(Path(home) / ".local/share/flatpak/exports/bin"),
                    system_path("/var/lib/flatpak/exports/bin")]
        candidates = [posixpath.join(folder, name) for folder in folders]
    return next((c for c in candidates if _host_executable(c)), None)


def _host_executable(path):
    """Is `path` an executable on the host? Symlinks are followed one hop at a
    time: an absolute target (/usr/bin/rustdesk -> /usr/share/rustdesk/rustdesk)
    means the host's /usr, not the sandbox's, so it goes back through
    system_path. At most 40 hops, like the kernel."""
    for _ in range(40):
        if not os.path.islink(path):
            return os.path.isfile(path) and os.access(path, os.X_OK)
        try:
            target = os.readlink(path)
        except OSError:
            return False
        path = system_path(target) if target.startswith("/") else posixpath.normpath(
            posixpath.join(posixpath.dirname(path), target))
    return False


def data_dirs(home):
    """The host's XDG data folders (where launchers live), as readable from here."""
    if not flatpak_id():
        return [Path(root) for root in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(os.pathsep) if root]
    return [Path(home) / ".local/share/flatpak/exports/share", Path(system_path("/var/lib/flatpak/exports/share")),
            Path(system_path("/usr/local/share")), Path(system_path("/usr/share"))]


def icon_dirs(home):
    """Icon folders to add inside a Flatpak: other Flatpaks export their app
    icons here, outside the sandbox's icon search path (the host's own
    /usr/share/icons is already there, at /run/host/share/icons)."""
    if not flatpak_id():
        return []
    return [Path(home) / ".local/share/flatpak/exports/share/icons",
            Path(system_path("/var/lib/flatpak/exports/share/icons"))]

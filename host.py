"""Running Keep inside a Flatpak.

A few programs Keep runs belong to the host session, not the sandbox:
systemctl (the user timer), flatpak (installed apps), findmnt (the host's
mounts and drive UUIDs) and fusermount (archives are mounted by the host's
fusermount, see packaging/flatpak). Inside a Flatpak, command() runs them
through flatpak-spawn --host; anywhere else it leaves them unchanged.
"""
import os

FLATPAK_BIN = "/usr/bin/flatpak"


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
        return os.path.join(runtime, "app", app_id, name)
    return os.path.join("/tmp", name)


def backup_command(config_path):
    """How the host's systemd timer starts a Flatpak Keep backup, or None
    outside a Flatpak (then the timer runs keep_backup.py directly)."""
    app_id = flatpak_id()
    if not app_id:
        return None
    return [FLATPAK_BIN, "run", "--command=keep-backup", app_id, "--config", config_path]

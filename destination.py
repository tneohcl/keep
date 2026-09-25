#!/usr/bin/env python3
"""Resolves a Keep "destination" config entry into an actual usable repo
path, or a clear reason it's unavailable right now. One source of truth
shared between Keep itself (main.py imports resolve_destination directly)
and the backup script (invokes this file as a subprocess, since bash has no
equivalent of "import"), so "the NAS isn't mounted" or "the USB drive isn't
connected" means the same thing and gets checked the same way in both
places, instead of maintaining separate detection logic in Python and bash
that could silently drift apart from each other over time.

Three destination types:
  "network"   - always-on network storage (NFS/CIFS/etc mounted at a fixed
                path). Checked via mountpoint + optionally an expected
                filesystem type.
  "removable" - a USB/external drive, identified by filesystem UUID rather
                than a mount path, since removable drives don't reliably
                get the same mount point every time they're plugged back
                in. Resolved via `findmnt -S UUID=...` to whatever path
                it's CURRENTLY mounted at, if it's connected at all.
  "other"     - any other local or already-mounted folder, e.g. a second
                internal disk. No mount-type assumptions, just checked for
                being (or being about to be, for first-time init) a usable
                directory.

Deliberately never falls back to some OTHER path when the configured
destination is unavailable - resolve_destination() reports {"available":
false, ...} and it's up to the caller (Keep's UI, or the backup script) to
stop there rather than silently writing a backup to local disk instead.

Run directly for the backup script's use:
    python3 destination.py <config.json path>
prints one JSON object to stdout and always exits 0 - resolution succeeding
just means "here's the current status", not "the destination is usable
right now"; check the "available" field for that.
"""
import json
import os
import subprocess
import sys


def _findmnt_target_for_uuid(uuid):
    """Current mountpoint of the filesystem with this UUID, or None if it
    isn't mounted (not connected, or connected but not yet mounted)."""
    try:
        out = subprocess.run(
            ["findmnt", "-n", "-o", "TARGET", "-S", f"UUID={uuid}"],
            capture_output=True, text=True, timeout=5,
        )
        target = out.stdout.strip()
        return target if out.returncode == 0 and target else None
    except Exception:
        return None


def mount_details(path):
    """Read structured mount records; autofs is a trigger, not the backing FS."""
    try:
        out = subprocess.run(["findmnt", "--json", "--target", path, "--output", "TARGET,FSTYPE"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return None
        records = []
        def collect(rows):
            for row in rows:
                if row.get("target") and row.get("fstype") and row["fstype"] != "autofs":
                    records.append((row["target"], row["fstype"]))
                collect(row.get("children", []))
        collect(json.loads(out.stdout).get("filesystems", []))
        unique = set(records)
        return unique.pop() if len(unique) == 1 else None
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


def _fstype_of(path):
    details = mount_details(path)
    return details[1] if details else None


def expected_filesystem(value, mount_check):
    """Recognize the exact old stacked-findmnt serialization, not arbitrary text."""
    if not isinstance(value, str) or "\n" not in value:
        return value
    lines = value.strip().splitlines()
    if len(lines) == 2 and lines[0].strip() == "autofs":
        parts = lines[1].rsplit(None, 1)
        if len(parts) == 2 and parts[0].strip() == mount_check and parts[1] in ("cifs", "nfs", "nfs4"):
            return parts[1]
    return value


def _looks_like_borg_repo(folder):
    """Same check as main.py's looks_like_borg_repo() - kept as its own tiny
    copy here rather than importing across the two modules (main.py already
    imports THIS module; importing back the other way would be circular),
    since it's a small, stable, self-contained check not worth the coupling."""
    try:
        config_file = os.path.join(folder, "config")
        return os.path.isfile(config_file) and "[repository]" in open(config_file, errors="ignore").read()
    except Exception:
        return False


def _unavailable(dest_type, label, mount_check, reason):
    return {"available": False, "repo": None, "mount_check": mount_check, "label": label, "type": dest_type, "reason": reason}


def _available(dest_type, label, repo, mount_check):
    return {"available": True, "repo": repo, "mount_check": mount_check, "label": label, "type": dest_type, "reason": None}


def resolve_destination(dest):
    """dest is the "destination" object from config.json. Returns a plain
    dict: {available, repo, mount_check, label, type, reason}."""
    dest_type = dest.get("type", "network")
    label = dest.get("label") or dest.get("repo") or "backup destination"

    if dest_type == "removable":
        uuid = dest.get("uuid")
        if not uuid:
            return _unavailable(dest_type, label, None, f"{label} has no UUID configured")
        mountpoint = _findmnt_target_for_uuid(uuid)
        if not mountpoint:
            return _unavailable(dest_type, label, None, f"{label} is not connected")
        subpath = (dest.get("repo_subpath") or "").strip("/")
        repo = f"{mountpoint.rstrip('/')}/{subpath}" if subpath else mountpoint
        mount_check = mountpoint
        if os.path.commonpath([os.path.realpath(repo), os.path.realpath(mountpoint)]) != os.path.realpath(mountpoint):
            return _unavailable(dest_type, label, mount_check, f"{label} repository path escapes the selected drive")

    elif dest_type == "network":
        repo = dest.get("repo")
        mount_check = dest.get("mount_check") or repo
        if not mount_check or not os.path.ismount(mount_check):
            return _unavailable(dest_type, label, mount_check, f"{label} is not mounted")
        expected_fstype = expected_filesystem(dest.get("fstype"), mount_check)
        if expected_fstype and _fstype_of(mount_check) != expected_fstype:
            return _unavailable(dest_type, label, mount_check, f"{label} is mounted but not as {expected_fstype} as expected")
        if not repo or os.path.commonpath([os.path.realpath(repo), os.path.realpath(mount_check)]) != os.path.realpath(mount_check):
            return _unavailable(dest_type, label, mount_check, f"{label} repository path is outside the expected mount")

    else:  # "other" - any local/already-mounted folder, e.g. a second
        # internal disk. No mount-type assumptions - just needs a usable
        # parent directory.
        repo = dest.get("repo")
        if not repo:
            return _unavailable(dest_type, label, None, f"{label} has no path configured")
        parent = os.path.dirname(repo.rstrip("/")) or "/"
        if not os.path.isdir(parent):
            return _unavailable(dest_type, label, None, f"{label} is not available")
        mount_check = None

    # The storage itself being reachable (mounted/connected/parent exists)
    # isn't the same as "there's actually a backup repo here" - a stale
    # config pointing at a folder that got cleared, or a removable drive's
    # configured subpath just not being on THIS particular drive, would
    # otherwise still report "available" right up until borg itself fails.
    # Not checked for change_backup_destination()'s own init-a-new-repo flow
    # (that uses looks_like_borg_repo() directly, before this repo exists
    # yet) - only for "is this destination usable for a backup RIGHT NOW".
    if not _looks_like_borg_repo(repo):
        return _unavailable(dest_type, label, mount_check, f"{label} is connected, but no Borg repository was found at {repo}")

    return _available(dest_type, label, repo, mount_check)


if __name__ == "__main__":
    config = json.load(open(sys.argv[1]))
    print(json.dumps(resolve_destination(config["destination"])))

#!/usr/bin/env python3
"""Consumer-facing configuration helpers for Keep.

Kept deliberately small and dependency-free so the GUI, the unattended
backup engine, and tests can share the same schema/command/schedule rules
without importing PySide6.
"""
from __future__ import annotations

import os
import re
import shlex
import socket
import sys
import json
import tempfile
from pathlib import Path


def config_path(app_dir: str) -> Path:
    """Select user state, preserving a genuine legacy configuration once."""
    override = os.environ.get("KEEP_CONFIG_PATH")
    if override:
        return Path(override).expanduser().absolute()
    target = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "keep/config.json"
    legacy = Path(app_dir) / "config.json"
    if not target.exists() and legacy.is_file():
        data = json.loads(legacy.read_text(encoding="utf-8"))
        if isinstance(data, dict) and destination_configured(data):
            target.parent.mkdir(parents=True, exist_ok=True)
            # Publish a fully written file without replacing concurrent user state.
            fd, temporary = tempfile.mkstemp(prefix=".keep-migrate-", dir=target.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, target)
            except FileExistsError:
                pass
            finally:
                os.unlink(temporary)
    return target


def write_config(path: Path, config: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".keep-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_retention(config: dict) -> str:
    prefix = str(config.get("archive_prefix") or default_archive_prefix())
    if not re.fullmatch(r"keep-[A-Za-z0-9._-]+-", prefix):
        raise ValueError("Archive prefix must start with keep-, end with -, and contain no glob characters")
    values = [config.get("retention", {}).get(k, d) for k, d in (("daily", 7), ("weekly", 4), ("monthly", 6))]
    if any(type(v) is not int or v < 0 for v in values) or not any(values):
        raise ValueError("Retention needs nonnegative integer counts and at least one positive count")
    return prefix


def support_summary(text: str) -> str:
    """Allow only operational summary lines; omit paths and raw diagnostics."""
    markers = ("Keep backup started", "Engine:", "Repository protection:", "Application data:",
               "Retention:", "repository access check passed", "prescan complete:",
               "borg create exited with rc=", "borg prune exited with rc=", "borg compact exited with rc=",
               "backup completed", "Keep backup finished with exit code")
    result = []
    for line in text.splitlines():
        message = re.sub(r"^\d{4}-\d\d-\d\dT\S+\s+", "", line)
        if message.startswith(markers):
            result.append(message)
    return "\n".join(result) or "No completed backup summary is available."


def recent_activity(log_dir: str) -> list[tuple[str, str]]:
    """Read bounded tails of the three newest run logs, without exposing paths."""
    result = []
    paths = sorted(Path(log_dir).glob("backup-*.log"), reverse=True)[:3]
    for path in paths:
        try:
            with path.open("rb") as stream:
                first = stream.readline(512).decode("utf-8", errors="replace").split()
                stream.seek(max(0, path.stat().st_size - 65536))
                tail = stream.read(65536).decode("utf-8", errors="replace")
            timestamp = first[0] if first else ""
            if "STOPPED BY USER" in tail:
                status = "Backup stopped"
            elif "backup completed with warnings" in tail:
                status = "Completed with warnings"
            elif "backup completed successfully" in tail:
                status = "Backup completed"
            elif "Keep backup finished with exit code" in tail:
                status = "Backup failed"
            else:
                status = "No final result recorded"
            result.append((timestamp, status))
        except OSError:
            continue
    return result


DEFAULT_CURATED_ITEMS = [
    ["SSH Keys", [".ssh"], "dialog-password", "system"],
    ["GPG Keys", [".gnupg"], "dialog-password", "system"],
    ["Password Manager (KWallet)", [".local/share/kwalletd"], "kwalletmanager", "system"],
    ["Desktop Look & Feel", [".config/kdeglobals", ".config/plasma-org.kde.plasma.desktop-appletsrc"], "preferences-desktop-theme", "system"],
    ["Flatpak App Permissions", [".local/share/flatpak/overrides"], "preferences-system-privacy", "system"],
]


def default_archive_prefix() -> str:
    """Stable, human-readable namespace for archives created by Keep.

    Prune must never sweep unrelated archives merely because the user adopted
    an existing Borg repository. A hostname-derived prefix is stable without
    requiring a randomly-generated ID to be persisted before the first run.
    """
    host = re.sub(r"[^A-Za-z0-9._-]+", "-", socket.gethostname()).strip("-") or "computer"
    return f"keep-{host}-"


def destination_configured(config: dict) -> bool:
    """Whether the user has actually chosen a destination yet.

    This is configuration state, not current reachability: an unplugged USB
    disk is still configured and must not trigger first-run onboarding on
    every launch.
    """
    dest = config.get("destination") or {}
    if dest.get("type") == "removable":
        return bool(dest.get("uuid"))
    return bool(dest.get("repo"))


def default_config(home: str | None = None) -> dict:
    home = home or str(Path.home())
    suggested = []
    for name in ("Documents", "Pictures", "Desktop", "Videos", "Music"):
        path = os.path.join(home, name)
        suggested.append({"label": name, "path": path})
    return {
        "destination": {"type": "other", "label": "Not configured", "repo": ""},
        "setup_complete": False,
        "backup_engine": "builtin",
        "archive_prefix": default_archive_prefix(),
        "backup_sources": suggested,
        "include_app_data": True,
        "retention": {"daily": 7, "weekly": 4, "monthly": 6},
        "schedule": {
            "managed_by_keep": True,
            "enabled": False,
            "frequency": "daily",
            "time": "04:00",
            "weekday": "Monday",
            "timer_unit": "keep-backup.timer",
        },
        "curated_items": DEFAULT_CURATED_ITEMS,
        "cross_install_apps": {},
        "extra_flatpak_paths": {},
        "native_process_names": {},
    }


def normalize_config(config: dict, home: str | None = None) -> bool:
    """Bring a legacy config up to the consumer schema in memory.

    Returns True when fields were added/normalized. It intentionally does not
    write the file; the GUI commits only after an explicit user change.
    """
    changed = False
    home = home or str(Path.home())

    if "setup_complete" not in config:
        config["setup_complete"] = destination_configured(config)
        changed = True

    if "backup_sources" not in config:
        sources = []
        legacy = config.get("project_dir")
        if legacy:
            legacy = os.path.abspath(os.path.expanduser(legacy))
            sources.append({"label": os.path.basename(legacy.rstrip("/")) or "Files", "path": legacy})
        # Older Keep configs also encoded ordinary personal folders as
        # curated restore items. Promote those into first-class backup
        # sources so upgrading does not make Documents/Pictures/etc vanish
        # from the new Folders tab.
        for item in config.get("curated_items", []):
            try:
                label, rel_paths, _icon, category = item
            except ValueError:
                continue
            if category != "personal":
                continue
            for rel in rel_paths:
                sources.append({"label": label, "path": os.path.join(home, rel)})
        if not sources:
            for name in ("Documents", "Pictures"):
                sources.append({"label": name, "path": os.path.join(home, name)})
        config["backup_sources"] = sources
        changed = True

    normalized_sources = []
    for entry in config.get("backup_sources", []):
        if isinstance(entry, str):
            path = os.path.abspath(os.path.expanduser(entry))
            label = os.path.basename(path.rstrip("/")) or path
        elif isinstance(entry, dict) and entry.get("path"):
            path = os.path.abspath(os.path.expanduser(str(entry["path"])))
            label = str(entry.get("label") or os.path.basename(path.rstrip("/")) or path)
        else:
            continue
        if not any(e["path"] == path for e in normalized_sources):
            normalized_sources.append({"label": label, "path": path})
    if normalized_sources != config.get("backup_sources"):
        config["backup_sources"] = normalized_sources
        changed = True

    if "include_app_data" not in config:
        config["include_app_data"] = True
        changed = True

    if "backup_engine" not in config:
        # Existing installations keep their proven external script until the
        # user explicitly switches; fresh installs use Keep's bundled engine.
        config["backup_engine"] = "external" if config.get("backup_script") else "builtin"
        changed = True

    if "archive_prefix" not in config:
        config["archive_prefix"] = default_archive_prefix()
        changed = True

    if "retention" not in config:
        config["retention"] = {"daily": 7, "weekly": 4, "monthly": 6}
        changed = True

    if "schedule" not in config:
        legacy = bool(config.get("backup_script"))
        config["schedule"] = {
            "managed_by_keep": False if legacy else True,
            "enabled": None if legacy else False,
            "frequency": "daily",
            "time": "04:00",
            "weekday": "Monday",
            "timer_unit": "borg-backup.timer" if legacy else "keep-backup.timer",
        }
        changed = True
    else:
        sched = config["schedule"]
        defaults = {
            "managed_by_keep": True,
            "enabled": False,
            "frequency": "daily",
            "time": "04:00",
            "weekday": "Monday",
            "timer_unit": "keep-backup.timer",
        }
        for k, v in defaults.items():
            if k not in sched:
                sched[k] = v
                changed = True

    config.setdefault("curated_items", DEFAULT_CURATED_ITEMS)
    config.setdefault("cross_install_apps", {})
    config.setdefault("extra_flatpak_paths", {})
    config.setdefault("native_process_names", {})
    return changed


def app_data_sources(config: dict, home: str | None = None) -> list[str]:
    """Broad app-data roots plus explicit app/system exceptions.

    Keeping both native XDG data and Flatpak sandboxes is deliberate: it
    allows a later restore/migration even if the packaging format changed.
    """
    home = home or str(Path.home())
    if config.get("app_selection_mode", "all") == "selected":
        from applications import selected_paths
        return selected_paths(config, home)
    paths = [
        os.path.join(home, ".config"),
        os.path.join(home, ".local/share"),
        os.path.join(home, ".var/app"),
    ]
    for item in config.get("curated_items", []):
        try:
            _label, rel_paths, _icon, category = item
        except ValueError:
            continue
        if category not in ("applications", "system"):
            continue
        for rel in rel_paths:
            paths.append(os.path.join(home, rel))
    for rel_paths in config.get("extra_flatpak_paths", {}).values():
        for rel in rel_paths:
            paths.append(os.path.join(home, rel))
    return paths


def source_destination_conflict(repo: str, sources: list[str]) -> str | None:
    """Return the first source that overlaps the Borg repository path."""
    if not repo:
        return None
    repo_real = os.path.realpath(repo)
    for source in sources:
        source_real = os.path.realpath(source)
        try:
            common = os.path.commonpath([repo_real, source_real])
        except ValueError:
            continue
        if common == repo_real or common == source_real:
            return source
    return None


def backup_source_entries(config: dict) -> list[dict]:
    normalize_config(config)
    return [dict(e) for e in config.get("backup_sources", [])]


def archive_path_for_source(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path)).lstrip("/")


def dedupe_paths(paths: list[str]) -> list[str]:
    """Canonicalize, remove missing paths and nested duplicates."""
    canonical = []
    for raw in paths:
        if not raw:
            continue
        p = os.path.abspath(os.path.expanduser(raw))
        if not os.path.lexists(p):
            continue
        if p not in canonical:
            canonical.append(p)
    canonical.sort(key=lambda p: (p.count(os.sep), len(p), p))
    result = []
    for p in canonical:
        if any(p == parent or p.startswith(parent.rstrip(os.sep) + os.sep) for parent in result):
            continue
        result.append(p)
    return result


def backup_command(config: dict, config_path: str, app_dir: str, python_exe: str | None = None) -> list[str]:
    normalize_config(config)
    if config.get("backup_engine") == "external" and config.get("backup_script"):
        return [os.path.expanduser(config["backup_script"])]
    python_exe = python_exe or sys.executable or "/usr/bin/python3"
    return [python_exe, os.path.join(app_dir, "keep_backup.py"), "--config", config_path]


def on_calendar(schedule: dict) -> str:
    hhmm = str(schedule.get("time") or "04:00")
    try:
        hh, mm = [int(x) for x in hhmm.split(":", 1)]
    except Exception:
        hh, mm = 4, 0
    hh = min(23, max(0, hh))
    mm = min(59, max(0, mm))
    clock = f"{hh:02d}:{mm:02d}:00"
    if schedule.get("frequency") == "weekly":
        weekday = str(schedule.get("weekday") or "Monday")[:3].title()
        return f"{weekday} *-*-* {clock}"
    return f"*-*-* {clock}"


def _systemd_quote(value: str) -> str:
    # systemd's ExecStart supports normal double-quoted arguments. Escape
    # backslashes and quotes rather than relying on shell parsing.
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$').replace('\n', '\\n').replace('\r', '\\r') + '"'


def render_systemd_units(config: dict, config_path: str, app_dir: str, python_exe: str | None = None) -> tuple[str, str]:
    cmd = backup_command(config, config_path, app_dir, python_exe)
    exec_start = " ".join(_systemd_quote(part) for part in cmd)
    service = f"""[Unit]\nDescription=Keep backup\n\n[Service]\nType=oneshot\nExecStart={exec_start}\n"""
    schedule = config.get("schedule", {})
    timer = f"""[Unit]\nDescription=Run Keep backup automatically\n\n[Timer]\nOnCalendar={on_calendar(schedule)}\nPersistent=true\nAccuracySec=1m\nUnit=keep-backup.service\n\n[Install]\nWantedBy=timers.target\n"""
    return service, timer

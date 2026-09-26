#!/usr/bin/env python3
"""Bundled unattended backup engine for consumer Keep installations.

Every run writes one authoritative, non-secret diagnostic log under
~/.local/state/borg-logs (or KEEP_LOG_DIR for isolated tests). The log is
opened before config parsing so failures in configuration, destination
resolution, authentication, prescan, create, prune, compact, or Python itself
are all captured in the same place.

The GUI may continue to use a legacy/custom external script when configured.
Fresh installations use this engine so user-selected folders and Keep's
schedule actually define what Borg backs up without requiring hand-written
shell scripts.
"""
from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import select
import shutil
import socket
import stat
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

import consumer
import destination
from operation_lock import RepositoryLock, RepositoryBusy


# Longer than the GUI's three-minute idle mount timeout, but never indefinite.
REPOSITORY_LOCK_WAIT_SECONDS = 300


def _lock_args(log) -> list[str]:
    _write(log, f"Repository lock: wait up to {REPOSITORY_LOCK_WAIT_SECONDS} seconds if busy; "
                "close archive browsing in other sessions if the wait persists")
    return ["--lock-wait", str(REPOSITORY_LOCK_WAIT_SECONDS)]


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _logdir() -> Path:
    override = os.environ.get("KEEP_LOG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".local/state/borg-logs"


def _write(log, message: str = "") -> None:
    log.write(f"{_timestamp()} {message}\n")
    log.flush()


def _write_raw_output(log, text: str) -> None:
    """Write subprocess output verbatim without inventing timestamps.

    Borg often writes progress using carriage returns; preserving the raw text
    keeps the run log useful to Keep's existing progress parser and for manual
    diagnosis. Secrets are never passed in command lines, so subprocess output
    is safe to retain unless Borg itself unexpectedly prints one.
    """
    if not text:
        return
    log.write(text)
    if not text.endswith("\n"):
        log.write("\n")
    log.flush()


def _load_passphrase() -> tuple[str, str]:
    """Return (value, source-description) without ever logging the secret."""
    if "BORG_PASSPHRASE" in os.environ:
        return os.environ["BORG_PASSPHRASE"], "session/environment"
    path = Path.home() / ".config/borg/passphrase"
    try:
        return path.read_text().strip(), f"saved file ({path})"
    except FileNotFoundError:
        return "", "none"


def _app_data_sources(config: dict, home: str) -> list[str]:
    return consumer.app_data_sources(config, home)


def collect_sources(config: dict) -> list[str]:
    home = str(Path.home())
    paths = [entry["path"] for entry in consumer.backup_source_entries(config)]
    if config.get("include_app_data", True):
        paths.extend(_app_data_sources(config, home))
    return consumer.dedupe_paths(paths)


def _protected_paths() -> list[str]:
    home = str(Path.home())
    paths = [
        # Borg updates this client state while backups run. Archiving it can
        # create spurious "file changed while we backed it up" warnings and
        # needlessly stores local unlock/security metadata.
        os.path.join(home, ".config/borg"),
        os.path.join(home, ".cache/borg"),
        os.path.join(home, ".local/share/borg"),
        os.path.join(home, "keep-new-repo-key-export.txt"),
        os.path.join(home, "Keep-Restored"),
        str(_logdir()),
    ]
    for name in ("BORG_CONFIG_DIR", "BORG_CACHE_DIR", "BORG_SECURITY_DIR", "BORG_KEYS_DIR", "BORG_KEY_FILE", "BORG_PASSCOMMAND_FILE"):
        if os.environ.get(name):
            paths.append(os.path.expanduser(os.environ[name]))
    for variable, default, suffix in (("XDG_CONFIG_HOME", ".config", "borg"), ("XDG_CACHE_HOME", ".cache", "borg"), ("XDG_DATA_HOME", ".local/share", "borg")):
        paths.append(os.path.join(os.environ.get(variable) or os.path.join(home, default), suffix))
    return paths


def _borg_base_args(config: dict) -> list[str]:
    args = []
    excludes = os.path.expanduser(config.get("excludes_file", "~/.config/borg/excludes.txt"))
    if os.path.isfile(excludes):
        args += ["--exclude-from", excludes]

    # These are intentionally not user-editable excludes. A backup should not
    # put the repository's own unlock material back inside itself (especially
    # dangerous when the destination was deliberately created unencrypted),
    # and Keep's restore/log output should not balloon a later whole-home
    # backup. Borg matches archive paths without the leading slash.
    protected = _protected_paths()
    for path in protected:
        args += ["--exclude", f"pp:{os.path.abspath(path).lstrip('/')}"]
    return args


def source_destination_conflict(repo: str, sources: list[str]) -> str | None:
    return consumer.source_destination_conflict(repo, sources)


def _borg_version(log, env: dict) -> None:
    try:
        p = subprocess.run(
            ["borg", "--version"], env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10,
        )
        version = (p.stdout or p.stderr).strip() or f"exit {p.returncode}"
        _write(log, f"Borg: {version}")
    except Exception as exc:
        _write(log, f"Borg version check failed: {exc}")


def _check_repo_access(repo: str, env: dict, log) -> bool:
    """Fail fast on wrong key/passphrase before doing an expensive prescan."""
    _write(log, "checking repository access")
    try:
        p = subprocess.run(
            ["borg", "info", "--json", *_lock_args(log), repo], env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=REPOSITORY_LOCK_WAIT_SECONDS + 60,
        )
    except subprocess.TimeoutExpired as exc:
        _write(log, f"ERROR repository access check timed out after {REPOSITORY_LOCK_WAIT_SECONDS + 60} seconds (including lock-wait allowance)")
        if exc.stdout:
            _write_raw_output(log, exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode(errors="replace"))
        if exc.stderr:
            _write_raw_output(log, exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode(errors="replace"))
        return False
    except Exception as exc:
        _write(log, f"ERROR repository access check could not run: {exc}")
        return False

    if p.returncode != 0:
        _write(log, f"ERROR repository access check failed (rc={p.returncode})")
        _write_raw_output(log, p.stdout)
        _write_raw_output(log, p.stderr)
        _write(log, "backup aborted before prescan/archive creation")
        return False

    _write(log, "repository access check passed")
    # Borg's own facts, not the settings label: the ID tells a re-created
    # repository at the same path apart (Recent activity filters on it).
    try:
        info = json.loads(p.stdout or "{}")
        repo_id = (info.get("repository") or {}).get("id")
        mode = (info.get("encryption") or {}).get("mode")
        if repo_id:
            _write(log, f"Repository ID: {repo_id}")
        if mode:
            _write(log, f"Repository encryption (reported by Borg): {mode}")
    except (ValueError, AttributeError):
        pass
    return True


def _prescan_paths_and_diagnostics(*streams: str) -> tuple[list[str], list[str]]:
    """Split Borg dry-run output into considered paths and diagnostics.

    Borg 1.4 sends ``--list`` records to stderr. During a dry run the normal
    status is ``-``. Accepting every non-excluded/non-error item makes the
    parser robust to cache/status variations without ever persisting private
    filenames into the diagnostic log.
    """
    paths: list[str] = []
    diagnostics: list[str] = []
    for stream in streams:
        for raw in (stream or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            item = re.match(r"^([A-Za-z?+\-])\s+(.+)$", line)
            if item:
                status = item.group(1)
                if status not in {"x", "E", "?"}:
                    paths.append(item.group(2).strip())
                continue
            diagnostics.append(line)
    return paths, diagnostics


def _candidate_local_paths(borg_path: str, sources: list[str]) -> list[str]:
    """Map one Borg-rendered path back to plausible local filesystem paths."""
    raw = borg_path.strip()
    candidates: list[str] = []
    if os.path.isabs(raw):
        candidates.append(os.path.normpath(raw))
    else:
        candidates.append(os.path.normpath("/" + raw))
        for source in sources:
            source_abs = os.path.abspath(os.path.expanduser(source))
            source_archive = source_abs.lstrip(os.sep)
            if raw == source_archive or raw.startswith(source_archive + os.sep):
                candidates.append(os.path.normpath(os.sep + raw))
            base = os.path.basename(source_abs.rstrip(os.sep))
            if raw == base:
                candidates.append(source_abs)
            elif base and raw.startswith(base + os.sep):
                candidates.append(os.path.join(os.path.dirname(source_abs), raw))
    return list(dict.fromkeys(candidates))


def _local_fallback_estimate(sources: list[str]) -> tuple[int, int]:
    """Conservative metadata-only size estimate used if Borg paths do not stat.

    Borg remains the authority for selection. This fallback exists only to
    keep progress determinate; it skips Keep/Borg protected state but does not
    attempt to reimplement Borg's complete pattern language.
    """
    protected = [os.path.abspath(os.path.expanduser(p)) for p in _protected_paths()]

    def blocked(path: str) -> bool:
        ap = os.path.abspath(path)
        for prefix in protected:
            try:
                if os.path.commonpath([ap, prefix]) == prefix:
                    return True
            except ValueError:
                continue
        return False

    total = 0
    files = 0
    for source in sources:
        root = os.path.abspath(os.path.expanduser(source))
        if blocked(root):
            continue
        try:
            st = os.lstat(root)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            total += st.st_size
            files += 1
            continue
        if not stat.S_ISDIR(st.st_mode):
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not blocked(os.path.join(dirpath, d))]
            for name in filenames:
                path = os.path.join(dirpath, name)
                if blocked(path):
                    continue
                try:
                    fst = os.lstat(path)
                except OSError:
                    continue
                if stat.S_ISREG(fst.st_mode):
                    total += fst.st_size
                    files += 1
    return total, files


def _estimate_with_borg(repo: str, sources: list[str], common: list[str], env: dict, log) -> int:
    """Best-effort Borg-native dry-run estimate, compatible with Borg 1.4.x."""
    archive = f"keep-prescan-{os.getpid()}"
    cmd = ["borg", "create", "--dry-run", "--list", *_lock_args(log), *common, f"{repo}::{archive}", *sources]
    try:
        p = subprocess.run(
            cmd, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=REPOSITORY_LOCK_WAIT_SECONDS + 120,
        )
    except subprocess.TimeoutExpired as exc:
        _write(log, f"WARNING prescan timed out after {REPOSITORY_LOCK_WAIT_SECONDS + 120} seconds (including lock-wait allowance); using local metadata estimate")
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        err = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        _, diagnostics = _prescan_paths_and_diagnostics(out, err)
        for line in diagnostics:
            _write_raw_output(log, line)
        total, files = _local_fallback_estimate(sources)
        _write(log, f"prescan fallback counted {files} regular files")
        return total
    except Exception as exc:
        _write(log, f"WARNING prescan could not run: {exc}; using local metadata estimate")
        total, files = _local_fallback_estimate(sources)
        _write(log, f"prescan fallback counted {files} regular files")
        return total

    paths, diagnostics = _prescan_paths_and_diagnostics(p.stdout, p.stderr)
    if p.returncode != 0:
        _write(log, f"WARNING prescan failed (rc={p.returncode}); using local metadata estimate")
        for line in diagnostics:
            _write_raw_output(log, line)
        total, files = _local_fallback_estimate(sources)
        _write(log, f"prescan fallback counted {files} regular files")
        return total

    if diagnostics:
        _write(log, "prescan diagnostics:")
        for line in diagnostics:
            _write_raw_output(log, line)

    total = 0
    regular_files = 0
    resolved_items = 0
    seen: set[str] = set()
    for borg_path in paths:
        resolved = None
        for candidate in _candidate_local_paths(borg_path, sources):
            try:
                st = os.lstat(candidate)
            except OSError:
                continue
            resolved = (candidate, st)
            break
        if resolved is None:
            continue
        path, st = resolved
        if path in seen:
            continue
        seen.add(path)
        resolved_items += 1
        if stat.S_ISREG(st.st_mode):
            total += st.st_size
            regular_files += 1

    _write(log, f"prescan resolved {resolved_items} of {len(paths)} considered items; {regular_files} regular files")
    if paths and regular_files == 0:
        _write(log, "WARNING Borg prescan paths did not resolve to regular files; using local metadata estimate")
        total, regular_files = _local_fallback_estimate(sources)
        _write(log, f"prescan fallback counted {regular_files} regular files")
    elif not paths:
        _write(log, "WARNING Borg prescan returned no item paths; using local metadata estimate")
        total, regular_files = _local_fallback_estimate(sources)
        _write(log, f"prescan fallback counted {regular_files} regular files")
    return total


_BORG_PROGRESS_O_RE = re.compile(r"^([\d.]+)\s*(TB|GB|MB|kB|B)\s+O\s")
_BORG_SIZE_UNITS = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}


def _progress_original_bytes(line: str) -> int | None:
    match = _BORG_PROGRESS_O_RE.match(line.strip())
    if not match:
        return None
    return int(float(match.group(1)) * _BORG_SIZE_UNITS[match.group(2)])


def _run_create_logged(cmd: list[str], log, env: dict, total_bytes: int) -> int:
    """Run ``borg create`` while keeping its normal file paths out of the log.

    Borg's ``--progress`` stream contains the current filename on nearly every
    update. Persisting that verbatim made ordinary logs huge and needlessly
    exposed private filenames. Keep consumes those progress records in-memory
    and writes at most one sanitized checkpoint per percentage point instead.
    Non-progress Borg output (warnings, errors and final --stats) is preserved.
    """
    log.flush()
    try:
        proc = subprocess.Popen(
            cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=0,
        )
    except Exception as exc:
        _write(log, f"ERROR could not start borg create: {exc}")
        return 127

    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    last_percent = -1

    def consume(record: str) -> None:
        nonlocal last_percent
        line = record.strip()
        if not line:
            return
        processed = _progress_original_bytes(line)
        if processed is not None:
            if total_bytes > 0:
                percent = min(99, max(0, int((processed * 100) / total_bytes)))
                if percent > last_percent:
                    last_percent = percent
                    _write(log, f"backup progress: {percent}% ({processed} of {total_bytes} bytes)")
            return
        # Anything that is not a routine progress/path record is useful Borg
        # diagnostic or summary output and stays in the authoritative log.
        _write_raw_output(log, line)

    while True:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if ready:
            chunk = os.read(fd, 65536)
            if chunk:
                pending += decoder.decode(chunk)
                # Borg uses both carriage returns and newlines as record
                # boundaries. Keep an incomplete tail for the next read.
                last_boundary = max(pending.rfind("\r"), pending.rfind("\n"))
                if last_boundary >= 0:
                    complete = pending[: last_boundary + 1]
                    pending = pending[last_boundary + 1 :]
                    for record in re.split(r"[\r\n]+", complete):
                        consume(record)
                continue
        if proc.poll() is not None:
            # Drain any bytes that arrived between poll() and this iteration.
            chunk = os.read(fd, 65536)
            if chunk:
                pending += decoder.decode(chunk)
                continue
            break

    pending += decoder.decode(b"", final=True)
    for record in re.split(r"[\r\n]+", pending):
        consume(record)

    rc = proc.wait()
    _write(log, f"borg create exited with rc={rc}")
    return rc


def _run_logged(cmd: list[str], log, env: dict, label: str) -> int:
    """Run a Borg stage with stdout+stderr streamed to the authoritative log."""
    log.flush()
    try:
        proc = subprocess.Popen(
            cmd, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, text=True,
        )
    except Exception as exc:
        _write(log, f"ERROR could not start {label}: {exc}")
        return 127
    rc = proc.wait()
    _write(log, f"{label} exited with rc={rc}")
    return rc


def _log_size(log) -> int:
    """Where the next stage's output starts in the run log."""
    log.flush()
    return os.fstat(log.fileno()).st_size


def _write_reason(log, start: int, repo: str) -> None:
    """After a failed Borg stage, add a plain-language reason when its output
    (the part of the log it wrote) shows a recognisable cause."""
    log.flush()
    try:
        with open(log.name, encoding="utf-8", errors="replace") as stream:
            stream.seek(start)
            output = stream.read()
    except OSError:
        return
    reason = consumer.borg_failure_reason(output, repo)
    if reason:
        _write(log, f"Reason: {reason}")


def _borg_rc_failed(rc: int) -> bool:
    # Borg 1.x default/legacy exit codes: 0 success, 1 completed with
    # warnings, 2 fatal error. Keep does not opt into modern detailed codes.
    return rc not in (0, 1)


def _borg_rc_warned(rc: int) -> bool:
    return rc == 1


def _run_with_log(config_path: str, log) -> int:
    _write(log, "Keep backup started")
    _write(log, "Engine: Keep built-in backup engine")
    _write(log, f"Host: {socket.gethostname()}")
    _write(log, f"Python: {sys.version.split()[0]} ({sys.executable})")
    _write(log, f"Config: {os.path.abspath(config_path)}")

    borg_path = shutil.which("borg")
    if borg_path is None:
        _write(log, "ERROR BorgBackup is not installed or is not on PATH")
        return 2
    _write(log, f"Borg executable: {borg_path}")

    # Open/logging has already succeeded before this point, so malformed or
    # missing configuration is now diagnosable from Show Log too.
    try:
        config = json.loads(Path(config_path).read_text())
    except Exception as exc:
        _write(log, f"ERROR could not read configuration: {exc}")
        return 2
    consumer.normalize_config(config)
    from applications import selection_conflict
    if selection_conflict(config, [entry["path"] for entry in consumer.backup_source_entries(config)]):
        _write(log, "ERROR selected folders overlap application data; remove broad app-data/home folders or use all application data")
        return 2
    try:
        consumer.validate_retention(config)
    except ValueError as exc:
        _write(log, f"ERROR unsafe retention configuration: {exc}")
        return 2

    missing = [e["path"] for e in consumer.backup_source_entries(config) if not os.path.exists(e["path"])]
    if missing:
        _write(log, f"ERROR {len(missing)} selected backup source(s) unavailable; reconnect them or update your folder selection")
        return 2

    try:
        status = destination.resolve_destination(config.get("destination", {}))
    except Exception as exc:
        _write(log, f"ERROR destination resolution failed unexpectedly: {exc}")
        return 2

    dest_cfg = config.get("destination", {})
    _write(log, f"Destination: {status.get('label') or dest_cfg.get('label') or 'unnamed'} ({status.get('type') or dest_cfg.get('type') or 'unknown'})")
    if not status.get("available"):
        _write(log, f"ERROR destination unavailable: {status.get('reason') or 'unknown reason'}")
        return 2

    repo = status["repo"]
    _write(log, f"Repository: {repo}")
    protection = dest_cfg.get("encryption")
    if protection:
        # Recorded when Keep created the repository; Borg's own report
        # follows the access check below.
        _write(log, f"Repository protection (at setup): {protection}")

    try:
        with RepositoryLock(repo, wait=REPOSITORY_LOCK_WAIT_SECONDS):
            sources = collect_sources(config)
            _write(log, f"Application data: {'enabled' if config.get('include_app_data', True) else 'disabled'}")
            _write(log, f"Backup sources ({len(sources)}):")
            for source in sources:
                _write(log, f"  - {source}")
            if not sources:
                _write(log, "ERROR no backup sources are configured or currently available")
                return 2

            conflict = source_destination_conflict(repo, sources)
            if conflict:
                _write(
                    log,
                    f"ERROR backup destination overlaps selected source: {conflict}. "
                    "Choose a destination outside the folders being backed up.",
                )
                return 2

            retention = config.get("retention", {})
            daily = int(retention.get("daily", 7))
            weekly = int(retention.get("weekly", 4))
            monthly = int(retention.get("monthly", 6))
            _write(log, f"Retention: {daily} daily / {weekly} weekly / {monthly} monthly")

            passphrase, passphrase_source = _load_passphrase()
            _write(log, f"Repository credential source: {passphrase_source} (secret not logged)")
            env = os.environ.copy()
            env["BORG_PASSPHRASE"] = passphrase
            env["BORG_EXIT_CODES"] = "legacy"
            _borg_version(log, env)

            if not _check_repo_access(repo, env, log):
                return 2

            common = _borg_base_args(config)
            excludes = os.path.expanduser(config.get("excludes_file", "~/.config/borg/excludes.txt"))
            _write(log, f"Exclude file: {excludes if os.path.isfile(excludes) else 'none'}")

            _write(log, "scanning what will be backed up")
            total = _estimate_with_borg(repo, sources, common, env, log)
            _write(log, f"prescan complete: {total} bytes to back up")

            archive_prefix = str(config.get("archive_prefix") or consumer.default_archive_prefix())
            archive = f"{archive_prefix}{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
            _write(log, f"Archive: {archive}")
            create = [
                "borg", "create", "--stats", "--progress", "--compression", "lz4", *_lock_args(log),
                *common, f"{repo}::{archive}", *sources,
            ]
            _write(log, "running borg create")
            had_warnings = False
            stage = _log_size(log)
            rc = _run_create_logged(create, log, env, total)
            if _borg_rc_failed(rc):
                _write(log, f"ERROR borg create failed (rc={rc})")
                _write_reason(log, stage, repo)
                _write(log, "backup aborted; prune/compact were not run")
                return 3
            if _borg_rc_warned(rc):
                had_warnings = True
                _write(log, "WARNING borg create completed with warnings; archive was created")

            prune = [
                "borg", "prune", "--stats", "--list", *_lock_args(log),
                "--keep-daily", str(daily),
                "--keep-weekly", str(weekly),
                "--keep-monthly", str(monthly),
                "--glob-archives", f"{archive_prefix}*",
                repo,
            ]
            _write(log, "running borg prune")
            stage = _log_size(log)
            rc = _run_logged(prune, log, env, "borg prune")
            if _borg_rc_failed(rc):
                _write(log, f"ERROR borg prune failed (rc={rc})")
                _write_reason(log, stage, repo)
                _write(log, "archive creation succeeded, but retention cleanup did not complete")
                return 4
            if _borg_rc_warned(rc):
                had_warnings = True
                _write(log, "WARNING borg prune completed with warnings")

            _write(log, "running borg compact")
            stage = _log_size(log)
            rc = _run_logged(["borg", "compact", *_lock_args(log), repo], log, env, "borg compact")
            if _borg_rc_failed(rc):
                _write(log, f"ERROR borg compact failed (rc={rc})")
                _write_reason(log, stage, repo)
                _write(log, "archive creation/prune succeeded, but repository space reclamation did not complete")
                return 5
            if _borg_rc_warned(rc):
                had_warnings = True
                _write(log, "WARNING borg compact completed with warnings")

            if had_warnings:
                _write(log, "backup completed with warnings")
            else:
                _write(log, "backup completed successfully")
            return 0
    except RepositoryBusy as exc:
        _write(log, f"ERROR {exc}")
        return 2


def run(config_path: str) -> int:
    """Run one backup and always attempt to leave a complete per-run log."""
    logdir = _logdir()
    try:
        logdir.mkdir(parents=True, exist_ok=True)
        log_path = logdir / f"backup-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f')}.log"
        log = log_path.open("w", buffering=1)
    except Exception as exc:
        # If even the log itself cannot be created there is no authoritative
        # file to write to; stderr/systemd journal is the only possible last
        # resort. Do not silently fail.
        print(f"Keep backup: could not create run log: {exc}", file=sys.stderr)
        return 98

    with log:
        try:
            rc = _run_with_log(config_path, log)
            _write(log, f"Keep backup finished with exit code {rc}")
            return rc
        except Exception:
            _write(log, "ERROR unexpected Python exception")
            _write_raw_output(log, traceback.format_exc())
            _write(log, "Keep backup finished with exit code 99")
            return 99


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    return run(args.config)


if __name__ == "__main__":
    raise SystemExit(main())

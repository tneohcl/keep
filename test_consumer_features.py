#!/usr/bin/env python3
"""Dependency-free regression checks for Keep's consumer configuration layer."""
import copy
import os
import tempfile
from pathlib import Path

import consumer
import keep_backup


def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print("OK", name)


legacy = {
    "backup_script": "~/bin/backup.sh",
    "project_dir": "/srv/work",
    "curated_items": [
        ["Documents", ["Documents"], "folder-documents", "personal"],
        ["SSH", [".ssh"], "dialog-password", "system"],
    ],
}
consumer.normalize_config(legacy, "/home/alice")
paths = {e["path"] for e in legacy["backup_sources"]}
check("legacy project promoted to backup source", "/srv/work" in paths)
check("legacy personal curated folder promoted to backup source", "/home/alice/Documents" in paths)
check("legacy install preserves external engine", legacy["backup_engine"] == "external")
check("legacy install preserves existing timer instead of silently replacing it", legacy["schedule"]["timer_unit"] == "borg-backup.timer")

fresh = consumer.default_config("/home/bob")
check("fresh consumer config uses bundled engine", fresh["backup_engine"] == "builtin")
check("fresh automatic schedule starts off", fresh["schedule"]["enabled"] is False)
check("fresh setup starts incomplete until a destination is chosen", fresh["setup_complete"] is False and not consumer.destination_configured(fresh))
check("fresh archives use a Keep-specific namespace", fresh["archive_prefix"].startswith("keep-") and fresh["archive_prefix"].endswith("-"))
check("daily OnCalendar is correct", consumer.on_calendar({"frequency": "daily", "time": "04:30"}) == "*-*-* 04:30:00")
check("weekly OnCalendar is correct", consumer.on_calendar({"frequency": "weekly", "weekday": "Tuesday", "time": "21:05"}) == "Tue *-*-* 21:05:00")

service, timer = consumer.render_systemd_units(fresh, "/tmp/config.json", "/opt/keep", "/usr/bin/python3")
check("bundled systemd service calls keep_backup.py", 'keep_backup.py' in service and '--config' in service)
check("systemd timer contains requested calendar", "OnCalendar=*-*-* 04:00:00" in timer)

external = copy.deepcopy(fresh)
external["backup_engine"] = "external"
external["backup_script"] = "~/bin/custom keep backup.sh"
service, _ = consumer.render_systemd_units(external, "/tmp/config.json", "/opt/keep", "/usr/bin/python3")
check("external compatibility service preserves custom script", os.path.expanduser("~/bin/custom keep backup.sh") in service)

with tempfile.TemporaryDirectory() as td:
    parent = Path(td) / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    unique = Path(td) / "unique"
    unique.mkdir()
    deduped = consumer.dedupe_paths([str(child), str(parent), str(unique), str(parent)])
    check("nested source is removed when parent already protects it", str(parent) in deduped and str(child) not in deduped)
    check("independent source is retained", str(unique) in deduped)

with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    docs = home / "Documents"
    docs.mkdir()
    cfg = {
        "backup_sources": [{"label": "Docs", "path": str(docs)}],
        "include_app_data": False,
        "backup_engine": "builtin",
        "retention": {"daily": 7, "weekly": 4, "monthly": 6},
        "schedule": {"enabled": False, "frequency": "daily", "time": "04:00", "weekday": "Monday", "timer_unit": "keep-backup.timer", "managed_by_keep": True},
        "curated_items": [], "cross_install_apps": {}, "extra_flatpak_paths": {}, "native_process_names": {},
    }
    got = keep_backup.collect_sources(cfg)
    check("bundled engine consumes configured folder source", got == [str(docs)])


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    repo = root / "Backups" / "KeepRepo"
    source = root / "Backups"
    repo.mkdir(parents=True)
    source.mkdir(exist_ok=True)
    check("repository inside a selected source is rejected", consumer.source_destination_conflict(str(repo), [str(source)]) == str(source))
    elsewhere = root / "Documents"
    elsewhere.mkdir()
    check("independent repository and source are allowed", consumer.source_destination_conflict(str(repo), [str(elsewhere)]) is None)

app_paths = consumer.app_data_sources({"curated_items": [], "extra_flatpak_paths": {}}, "/home/alice")
check("native XDG config is included as app data", "/home/alice/.config" in app_paths)
check("Flatpak sandboxes are included as app data", "/home/alice/.var/app" in app_paths)

base_args = keep_backup._borg_base_args({"excludes_file": "/definitely/not/present"})
joined = " ".join(base_args)
check("entire Borg client state is hard-excluded from Keep-managed backups", ".config/borg" in joined and ".cache/borg" in joined)
check("Keep restore output is hard-excluded from whole-home backups", "Keep-Restored" in joined)

# Authoritative built-in-engine logging: one per-run file must contain
# non-secret context, preflight auth, every Borg stage/output, exit codes,
# and enough failure detail to diagnose an auth failure from Show Log alone.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    home = root / "home"
    home.mkdir()
    source = home / "Documents"
    source.mkdir()
    (source / "hello.txt").write_text("hello")
    repo = root / "repo"
    repo.mkdir()
    (repo / "config").write_text("[repository]\nversion = 1\n")
    bindir = root / "bin"
    bindir.mkdir()
    fake_borg = bindir / "borg"
    fake_borg.write_text(r'''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("borg 1.fake")
    raise SystemExit(0)
if args and args[0] in ("info", "create", "prune", "compact"):
    assert "--lock-wait" in args, args
    assert args[args.index("--lock-wait") + 1] == "300", args
if args and args[0] == "info":
    if os.environ.get("FAKE_BORG_AUTH_FAIL") == "1":
        print("Passphrase supplied is incorrect.", file=sys.stderr)
        raise SystemExit(2)
    print('{"repository": {"id": "fake"}}')
    raise SystemExit(0)
if args and args[0] == "create" and "--dry-run" in args:
    # Borg 1.4.x emits --list records on stderr. Keep must use these for
    # estimation without copying the private path list into its run log.
    if os.environ.get("FAKE_BORG_BAD_PRESCAN_PATH") == "1":
        print("- definitely/not/a/real/path", file=sys.stderr)
    else:
        print("- " + os.environ["FAKE_SOURCE"].lstrip("/"), file=sys.stderr)
    print("fake prescan diagnostic", file=sys.stderr)
    raise SystemExit(0)
if args and args[0] == "create":
    # Real Borg --progress records carry a filename and use carriage returns.
    # The built-in engine must consume them live, persist only a sanitized
    # percentage checkpoint, and retain ordinary Borg summary output.
    print("5 B O 5 B C 5 B D 1 N home/alice/PRIVATE-FILENAME.txt", file=sys.stderr, end="\r", flush=True)
    print("FAKE CREATE OUTPUT")
    if os.environ.get("FAKE_BORG_CREATE_WARN") == "1":
        print("some file changed while we backed it up", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)
if args and args[0] == "prune":
    if "--prefix" in args or "--glob-archives" not in args:
        print("wrong prune archive selector", file=sys.stderr)
        raise SystemExit(9)
    print("FAKE PRUNE OUTPUT")
    raise SystemExit(0)
if args and args[0] == "compact":
    print("FAKE COMPACT OUTPUT")
    raise SystemExit(0)
print("unexpected fake borg args: " + repr(args), file=sys.stderr)
raise SystemExit(9)
''')
    fake_borg.chmod(0o755)
    cfg = {
        "destination": {"type": "other", "label": "Test Disk", "repo": str(repo), "encryption": "encrypted (test)"},
        "setup_complete": True,
        "backup_engine": "builtin",
        "archive_prefix": "keep-test-",
        "backup_sources": [{"label": "Documents", "path": str(source)}],
        "include_app_data": False,
        "retention": {"daily": 7, "weekly": 4, "monthly": 6},
        "schedule": {"managed_by_keep": True, "enabled": False, "frequency": "daily", "time": "04:00", "weekday": "Monday", "timer_unit": "keep-backup.timer"},
        "curated_items": [], "cross_install_apps": {}, "extra_flatpak_paths": {}, "native_process_names": {},
    }
    cfg_path = root / "config.json"
    cfg_path.write_text(__import__('json').dumps(cfg))
    logdir = root / "logs"

    saved_env = {k: os.environ.get(k) for k in ("HOME", "PATH", "KEEP_LOG_DIR", "BORG_PASSPHRASE", "FAKE_SOURCE", "FAKE_BORG_AUTH_FAIL", "FAKE_BORG_CREATE_WARN", "FAKE_BORG_BAD_PRESCAN_PATH")}
    try:
        os.environ["HOME"] = str(home)
        os.environ["PATH"] = str(bindir) + os.pathsep + (saved_env["PATH"] or "")
        os.environ["KEEP_LOG_DIR"] = str(logdir)
        os.environ["BORG_PASSPHRASE"] = "SECRET-MUST-NOT-BE-LOGGED"
        os.environ["FAKE_SOURCE"] = str(source / "hello.txt")
        os.environ.pop("FAKE_BORG_AUTH_FAIL", None)
        os.environ.pop("FAKE_BORG_CREATE_WARN", None)
        os.environ.pop("FAKE_BORG_BAD_PRESCAN_PATH", None)

        rc = keep_backup.run(str(cfg_path))
        logs = list(logdir.glob("backup-*.log"))
        check("built-in backup creates exactly one authoritative run log", rc == 0 and len(logs) == 1)
        text = logs[0].read_text()
        check("run log includes non-secret execution context", "Engine: Keep built-in backup engine" in text and "Repository: " in text and "Retention: 7 daily / 4 weekly / 6 monthly" in text)
        check("run log authenticates before prescan", text.index("repository access check passed") < text.index("scanning what will be backed up"))
        check("Borg 1.4 stderr prescan paths produce a real byte estimate", "prescan complete: 5 bytes to back up" in text)
        borg_style_source = str(source / "hello.txt").lstrip("/")
        check("successful prescan path list is not persisted", ("- " + borg_style_source) not in text)
        check("run log captures create prune compact output", all(x in text for x in ("FAKE CREATE OUTPUT", "FAKE PRUNE OUTPUT", "FAKE COMPACT OUTPUT")))
        check("create progress is sanitized but still drives a percentage", "backup progress: 99%" in text and "PRIVATE-FILENAME.txt" not in text)
        check("run log captures stage exit codes and final success", all(x in text for x in ("borg create exited with rc=0", "borg prune exited with rc=0", "borg compact exited with rc=0", "backup completed successfully")))
        check("prune uses Borg's current glob archive selector", "wrong prune archive selector" not in text)
        check("run log never records passphrase value", "SECRET-MUST-NOT-BE-LOGGED" not in text)

        # Borg rc=1 means the archive was created with warnings, not failure.
        # Keep must continue retention/compact and return success to the GUI
        # while leaving an explicit warning verdict in the authoritative log.
        os.environ["FAKE_BORG_CREATE_WARN"] = "1"
        rc = keep_backup.run(str(cfg_path))
        logs = sorted(logdir.glob("backup-*.log"), key=lambda p: p.stat().st_mtime_ns)
        text = logs[-1].read_text()
        check("Borg warning rc does not falsely fail a completed backup", rc == 0 and "borg create exited with rc=1" in text and "backup completed with warnings" in text and "FAKE PRUNE OUTPUT" in text and "FAKE COMPACT OUTPUT" in text)
        os.environ.pop("FAKE_BORG_CREATE_WARN", None)

        # If Borg's path rendering cannot be mapped back to the filesystem,
        # Keep must not regress to a bogus 0-byte progress estimate.
        os.environ["FAKE_BORG_BAD_PRESCAN_PATH"] = "1"
        rc = keep_backup.run(str(cfg_path))
        logs = sorted(logdir.glob("backup-*.log"), key=lambda p: p.stat().st_mtime_ns)
        text = logs[-1].read_text()
        check("unresolvable Borg prescan paths fall back to local size estimate", rc == 0 and "prescan fallback counted 1 regular files" in text and "prescan complete: 5 bytes to back up" in text)
        os.environ.pop("FAKE_BORG_BAD_PRESCAN_PATH", None)

        # New run with an authentication failure. It must fail before prescan
        # and retain Borg's actual stderr in the same per-run log.
        os.environ["FAKE_BORG_AUTH_FAIL"] = "1"
        rc = keep_backup.run(str(cfg_path))
        logs = sorted(logdir.glob("backup-*.log"), key=lambda p: p.stat().st_mtime_ns)
        # Runs can start within the same wall-clock second. If the filename
        # collided, the later run is still authoritative but there will be one
        # file; content is what matters here.
        text = logs[-1].read_text()
        check("auth failure is fully captured in run log", rc == 2 and "repository access check failed (rc=2)" in text and "Passphrase supplied is incorrect." in text)
        check("auth failure aborts before expensive prescan", "backup aborted before prescan/archive creation" in text and "scanning what will be backed up" not in text)
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

print("consumer feature checks passed")

#!/usr/bin/env python3
"""Keep's additive, Qt-free CLI. See CLI.md for the versioned contract."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

import borg_ops
import consumer
import destination
from operation_lock import RepositoryLock, RepositoryBusy


def state_root():
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "keep"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="keep-cli")
    parser.add_argument("--version", action="version", version="keep-cli 0.9.2")
    parser.add_argument("--config", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "archives", "doctor", "logs", "check"):
        sub = commands.add_parser(name)
        sub.add_argument("--json", action="store_true")
        if name == "check":
            sub.add_argument("--deep", action="store_true")
    args = parser.parse_args(argv)
    result, code = {}, 0
    check_record = None
    previous = None
    if hasattr(signal, "SIGTERM"):
        previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        path = args.config or Path(os.environ.get("KEEP_CONFIG_PATH") or
                                    str(Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "keep/config.json"))
        config = json.loads(path.read_text(encoding="utf-8"))
        logdir = os.environ.get("KEEP_LOG_DIR", str(Path.home() / ".local/state/borg-logs"))
        if args.command == "logs":
            result = {"recent_activity": consumer.recent_activity(logdir)}
        else:
            dest = destination.resolve_destination(config.get("destination", {}))
            if args.command in ("status", "doctor"):
                result = {"destination": dest, "recent_activity": consumer.recent_activity(logdir),
                          "configured_schedule": config.get("schedule", {}),
                          "borg_available": bool(shutil.which("borg"))}
                if shutil.which("systemctl"):
                    unit = config.get("schedule", {}).get("timer_unit") or "keep-backup.timer"
                    try:
                        timer = subprocess.run(["systemctl", "--user", "show", unit,
                                                "-p", "ActiveState", "-p", "UnitFileState",
                                                "-p", "NextElapseUSecRealtime"],
                                               capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
                        result["timer"] = dict(line.split("=", 1) for line in timer.stdout.splitlines() if "=" in line) if timer.returncode == 0 else {"available": False}
                    except (OSError, subprocess.TimeoutExpired):
                        result["timer"] = {"available": False}
                health_file = state_root() / "last-check.json"
                if health_file.exists():
                    health = json.loads(health_file.read_text(encoding="utf-8"))
                    if dest.get("available"):
                        rc, output, _ = borg_ops.run_borg(["info", "--json", "--lock-wait", "1", dest["repo"]],
                                                         borg_ops.credential_environment(), timeout=10)
                        identity = json.loads(output).get("repository", {}).get("id") if rc == 0 else None
                        if consumer.matches_repository(health, dest.get("repo"), identity):
                            result["last_integrity_check"] = health
                if args.command == "doctor":
                    result["platform_supported"] = sys.platform.startswith("linux")
                    units = Path.home() / ".config/systemd/user"
                    result["legacy_timer_files"] = [p.name for p in units.glob("borg-*.timer")]
                    result["legacy_timer_note"] = "File presence alone does not establish whether a timer is enabled."
                    code = 0 if dest.get("available") and result["borg_available"] and result["platform_supported"] else 1
            else:
                if not dest.get("available"):
                    raise ValueError("Backup destination is unavailable")
                repo = dest["repo"]
                with RepositoryLock(repo):
                    env = borg_ops.credential_environment()
                    if args.command == "archives":
                        prefix = consumer.validate_retention(config)
                        rc, output, diagnostic = borg_ops.run_borg(["list", "--json", "--lock-wait", "5", "--glob-archives", prefix + "*", repo], env)
                        if rc != 0:
                            reason = borg_ops.classify_borg_auth_error(diagnostic) or "repository_query_failed"
                            code, result = 2, {"error": reason}
                        else:
                            result = {"archives": json.loads(output).get("archives", [])}
                    else:
                        print("Checking backup integrity; this may take a long time. Ctrl+C cancels.", file=sys.stderr)
                        arguments = ["check", "--progress", "--lock-wait", "5"]
                        if args.deep:
                            arguments.append("--verify-data")
                        arguments.append(repo)
                        rc, output, _ = borg_ops.run_borg(["info", "--json", "--lock-wait", "5", repo], env)
                        # A damaged repository may fail info but still needs a check.
                        repository_id = json.loads(output).get("repository", {}).get("id") if rc == 0 else None
                        started = datetime.now(timezone.utc).isoformat()
                        check_record = {"repository": repo, "repository_id": repository_id, "deep": args.deep, "started": started, "result": "running"}
                        state_root().mkdir(parents=True, exist_ok=True, mode=0o700)
                        consumer.write_config(state_root() / "last-check.json", check_record)
                        # Keep raw Borg diagnostics out of JSON and private logs.
                        with tempfile.TemporaryFile(mode="w+") as diagnostics:
                            rc, _, _ = borg_ops.run_borg(arguments, env, timeout=None, progress=diagnostics)
                        code = 0 if rc == 0 else 1 if rc == 1 else 2
                        result = {"repository": repo, "repository_id": repository_id, "deep": args.deep, "started": started,
                                  "finished": datetime.now(timezone.utc).isoformat(), "borg_exit_code": rc,
                                  "result": "success" if code == 0 else "warning" if code == 1 else "failed"}
                        root = state_root()
                        root.mkdir(parents=True, exist_ok=True, mode=0o700)
                        consumer.write_config(root / "last-check.json", result)
                        check_record = None
    except RepositoryBusy as exc:
        code, result = 3, {"error": str(exc)}
    except KeyboardInterrupt:
        code, result = 130, {"error": "Operation cancelled; no successful result recorded"}
    except subprocess.TimeoutExpired:
        code, result = 4, {"error": "Repository query timed out"}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        code, result = 2, {"error": "Operation failed. Check configuration, destination, Borg installation and credentials."}
    finally:
        if check_record is not None:
            check_record.update(result="cancelled" if code == 130 else "failed", finished=datetime.now(timezone.utc).isoformat())
            try:
                consumer.write_config(state_root() / "last-check.json", check_record)
            except OSError:
                pass
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    envelope = {"schema_version": 1, "command": args.command, "exit_code": code, "data": result}
    print(json.dumps(envelope, ensure_ascii=False) if args.json else json.dumps(result, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

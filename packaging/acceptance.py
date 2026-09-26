#!/usr/bin/env python3
"""Failure-scenario acceptance checks for Keep's backup engine.

Runs keep_backup.py, the script the 04:00 timer runs, against throwaway
Borg repositories inside an unprivileged user + mount namespace, with HOME,
KEEP_LOG_DIR and the Borg directories pointed at a temporary folder. The
real config, passphrase, logs and backups are never read or touched.

Start it with packaging/acceptance.sh (Linux; needs unshare, borg, and
Keep's PySide6 interpreter for the Status-page verdicts).

Scenarios:
  1. the network destination isn't mounted when a backup starts
  2. the destination disappears in the middle of a backup (lazy unmount)
  3. the backup process is killed mid-archive (crash / power loss)
  4. the destination fills up mid-archive

Each checks what matters for recoverability: the run is never reported as
a success, nothing is written where it shouldn't be, and after the
problem is fixed the next backup succeeds and `borg check` passes.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

APP = Path(__file__).resolve().parent.parent
PASSPHRASE = "keep-acceptance-only"
PREFIX = "keep-acceptance-"
failures = []


def check(label, ok, detail=""):
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(label)


def sh(*args, **kwargs):
    return subprocess.run(list(args), capture_output=True, text=True, **kwargs)


class Sandbox:
    """A temporary HOME with its own Borg state, logs and Keep config."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="keep-acceptance-"))
        self.home = self.root / "home"
        self.logs = self.home / ".local/state/borg-logs"
        self.source = self.home / "Documents"
        self.source.mkdir(parents=True)
        (self.source / "notes.txt").write_text("keep acceptance\n")
        self.env = dict(os.environ, HOME=str(self.home), KEEP_LOG_DIR=str(self.logs),
                        BORG_PASSPHRASE=PASSPHRASE, BORG_BASE_DIR=str(self.home),
                        XDG_STATE_HOME=str(self.home / ".local/state"),
                        XDG_CONFIG_HOME=str(self.home / ".config"),
                        KEEP_CONFIG_PATH=str(self.home / ".config/keep/config.json"))
        for name in ("BORG_REPO", "BORG_PASSCOMMAND", "BORG_PASSPHRASE_FD", "BORG_KEY_FILE"):
            self.env.pop(name, None)

    def borg(self, *args):
        return subprocess.run(["borg", *args], capture_output=True, text=True, env=self.env)

    def config(self, mountpoint, repo):
        path = Path(self.env["KEEP_CONFIG_PATH"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "destination": {"type": "network", "label": "Acceptance NAS",
                            "repo": str(repo), "mount_check": str(mountpoint)},
            "setup_complete": True, "backup_engine": "builtin", "archive_prefix": PREFIX,
            "backup_sources": [{"label": "Documents", "path": str(self.source)}],
            "include_app_data": False,
            "retention": {"daily": 7, "weekly": 4, "monthly": 6},
        }))
        return path

    def add_data(self, megabytes, name):
        with open(self.source / name, "wb") as out:
            for _ in range(megabytes):
                out.write(os.urandom(1024 * 1024))

    def start_backup(self, config):
        # The timer's interpreter and script, in its own process group.
        return subprocess.Popen(["/usr/bin/python3", str(APP / "keep_backup.py"), "--config", str(config)],
                                env=self.env, cwd=str(APP), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)

    def backup(self, config):
        return self.start_backup(config).wait(timeout=900)

    def newest_log(self):
        logs = sorted(self.logs.glob("backup-*.log"))
        return logs[-1].read_text() if logs else ""

    def wait_for(self, text, process, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline and process.poll() is None:
            if text in self.newest_log():
                return True
            time.sleep(0.1)
        return False

    def verdict(self, repo):
        """What Keep's Status page would say about the newest run."""
        import main
        main.LOGDIR = str(self.logs)
        info = self.borg("info", "--json", str(repo))
        repo_id = json.loads(info.stdout)["repository"]["id"] if info.returncode == 0 else None
        return main.backup_history(str(repo), repo_id)

    def reason(self, repo):
        """The failure reason Keep's Status page would show."""
        import main
        main.LOGDIR = str(self.logs)
        return main.backup_failure_reason(str(repo), None)

    def age_newest_log(self, seconds):
        newest = sorted(self.logs.glob("backup-*.log"))[-1]
        os.utime(newest, (time.time() - seconds, time.time() - seconds))

    def archives(self, repo):
        listing = self.borg("list", "--json", str(repo))
        return [a["name"] for a in json.loads(listing.stdout)["archives"]] if listing.returncode == 0 else None

    def cleanup(self):
        # Unmount the scenario mounts first (deepest first), or the folders
        # under them can't be removed.
        mounts = [line.split()[1] for line in Path("/proc/self/mounts").read_text().splitlines()
                  if line.split()[1].startswith(str(self.root))]
        for mountpoint in sorted(mounts, key=len, reverse=True):
            sh("umount", "-l", mountpoint)
        shutil.rmtree(self.root, ignore_errors=True)


def nas(box, name, size="1g"):
    """A NAS: its disk is a tmpfs of its own (a different filesystem, like a
    real network share, so os.path.ismount sees the mount), and its share is
    bind-mounted onto the mount folder. Returns (share, mountpoint)."""
    disk, mountpoint = box.root / f"{name}-disk", box.root / name
    disk.mkdir(), mountpoint.mkdir()
    sh("mount", "-t", "tmpfs", "-o", f"size={size}", "tmpfs", str(disk), check=True)
    share = disk / "share"
    share.mkdir()
    connect(share, mountpoint)
    return share, mountpoint


def connect(share, mountpoint):
    sh("mount", "--bind", str(share), str(mountpoint), check=True)


def init_repo(box, repo):
    rc = box.borg("init", "--encryption=repokey-blake2", str(repo)).returncode
    check("throwaway repository created", rc == 0)


def strike_after_create_starts(box, process, action, min_bytes, watch):
    """Wait until borg create is running and has written data, then act."""
    started = box.wait_for("running borg create", process)
    baseline = du(watch)
    deadline = time.time() + 120
    while started and process.poll() is None and time.time() < deadline and du(watch) - baseline < min_bytes:
        time.sleep(0.05)
    running = process.poll() is None
    if running:
        action()
    return started and running


def du(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def show_error(log):
    """Print the error Keep logged, as the person would see it in Show log."""
    errors = [line.split(" ", 1)[-1] for line in log.splitlines() if " ERROR " in f" {line} "]
    print(f"       Keep logged: {errors[0] if errors else '(no ERROR line; the run was cut off)'}")


def recovered(box, config, repo, before):
    rc = box.backup(config)
    log = box.newest_log()
    check("next backup after recovery succeeds", rc == 0 and "backup completed successfully" in log,
          f"rc={rc}")
    verify = box.borg("check", str(repo))
    check("borg check passes after the failure", verify.returncode == 0, verify.stderr.strip()[-200:])
    after = box.archives(repo) or []
    complete = [a for a in after if not a.endswith(".checkpoint")]
    check("exactly one new complete archive (the recovery run)", len(complete) == len(before) + 1,
          f"before={before} after={after}")


# ---------------------------------------------------------------------------
def scenario_not_mounted(box):
    print("=== 1. The NAS isn't mounted when the backup starts ===")
    mountpoint = box.root / "nas-unmounted"
    mountpoint.mkdir()
    config = box.config(mountpoint, mountpoint / "repo")
    rc = box.backup(config)
    log = box.newest_log()
    check("backup refuses to run (exit code 2)", rc == 2, f"rc={rc}")
    check("log says the destination isn't mounted", "destination unavailable" in log and "is not mounted" in log)
    check("nothing written into the empty mount folder", list(mountpoint.iterdir()) == [])
    check("never reported as a success", "backup completed" not in log)
    show_error(log)


def scenario_disappears(box):
    print("=== 2. The destination disappears mid-backup ===")
    store, mountpoint = nas(box, "nas2")
    repo = mountpoint / "repo"
    init_repo(box, repo)
    config = box.config(mountpoint, repo)
    check("baseline backup succeeds", box.backup(config) == 0)
    before = box.archives(repo)
    box.add_data(300, "big-2.bin")
    process = box.start_backup(config)
    struck = strike_after_create_starts(
        box, process, lambda: sh("umount", "-l", str(mountpoint), check=True), 40 << 20, store)
    rc = process.wait(timeout=900)
    log = box.newest_log()
    check("destination removed while borg create was writing", struck)
    check("the interrupted run fails (non-zero exit)", rc != 0, f"rc={rc}")
    check("the interrupted run is never reported as a success", "backup completed" not in log)
    show_error(log)
    check("the log gives the reason: destination disconnected", "Reason: " in log and "disconnected" in log)
    connect(store, mountpoint)                          # reconnect
    verdict = box.verdict(repo)
    check("Status page reports the interrupted run as failed", verdict[0] == "FAILED", str(verdict))
    reason = box.reason(repo) or ""
    check("Status page says the destination disconnected", "disconnected" in reason, reason)
    recovered(box, config, repo, [a for a in before if not a.endswith(".checkpoint")])
    (box.source / "big-2.bin").unlink()


def scenario_crash(box):
    print("=== 3. The backup is killed mid-archive (crash / power loss) ===")
    store, mountpoint = nas(box, "nas3")
    repo = mountpoint / "repo"
    init_repo(box, repo)
    config = box.config(mountpoint, repo)
    check("baseline backup succeeds", box.backup(config) == 0)
    before = box.archives(repo)
    box.add_data(300, "big-3.bin")
    process = box.start_backup(config)
    struck = strike_after_create_starts(
        box, process, lambda: os.killpg(process.pid, signal.SIGKILL), 40 << 20, store)
    process.wait(timeout=60)
    log = box.newest_log()
    check("keep_backup and borg killed while borg create was writing", struck)
    check("the killed run is never reported as a success", "backup completed" not in log)
    show_error(log)
    check("Borg's lock was left behind by the crash", any(repo.glob("lock*")))
    verdict = box.verdict(repo)
    check("Status page reports the killed run as failed", verdict[0] == "FAILED", str(verdict))
    box.age_newest_log(600)                            # seen later, nothing running
    reason = box.reason(repo) or ""
    check("Status page says the backup stopped before finishing", "stopped before finishing" in reason, reason)
    recovered(box, config, repo, [a for a in before if not a.endswith(".checkpoint")])
    check("the stale lock was cleared", not any(repo.glob("lock.exclusive")))
    (box.source / "big-3.bin").unlink()


def scenario_full(box):
    print("=== 4. The destination fills up mid-archive ===")
    share, mountpoint = nas(box, "nas4", size="400m")
    repo = mountpoint / "repo"
    init_repo(box, repo)
    config = box.config(mountpoint, repo)
    check("baseline backup succeeds", box.backup(config) == 0)
    before = box.archives(repo)
    filler = share / "other-data.bin"                  # the NAS's other contents
    with open(filler, "wb") as out:
        for _ in range(250):
            out.write(b"\0" * (1024 * 1024))
    box.add_data(300, "big-4.bin")                     # won't fit in the ~150 MB left
    rc = box.backup(config)
    log = box.newest_log()
    check("the run fails when the destination is full", rc != 0, f"rc={rc}")
    check("the failure is recorded in the log", "ERROR borg create failed" in log or "ERROR" in log)
    check("never reported as a success", "backup completed" not in log)
    show_error(log)
    check("the log gives the reason: destination full", "Reason: " in log and "full" in log)
    verdict = box.verdict(repo)
    check("Status page reports it as failed", verdict[0] == "FAILED", str(verdict))
    reason = box.reason(repo) or ""
    check("Status page says the destination is full", "full" in reason, reason)
    filler.unlink()                                    # space freed
    recovered(box, config, repo, [a for a in before if not a.endswith(".checkpoint")])
    (box.source / "big-4.bin").unlink()


def main():
    if os.getuid() != 0 or os.environ.get("KEEP_ACCEPTANCE_NS") != "1":
        sys.exit("Run this through packaging/acceptance.sh (it needs its own mount namespace).")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    box = Sandbox()
    os.environ.update({k: box.env[k] for k in ("HOME", "KEEP_CONFIG_PATH", "XDG_STATE_HOME", "XDG_CONFIG_HOME")})
    sys.path.insert(0, str(APP))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841 (main imports Qt widgets)
    try:
        for scenario in (scenario_not_mounted, scenario_disappears, scenario_crash, scenario_full):
            try:
                scenario(box)
            except Exception as exc:
                check(f"{scenario.__name__} ran to completion", False, repr(exc))
    finally:
        box.cleanup()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("ALL ACCEPTANCE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

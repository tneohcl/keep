"""Recorded recovery test: prove a file comes back using only the passphrase.

Qt-free so the GUI worker, the CLI and the tests share one implementation.

What a passed test proves: with an EMPTY Borg keys directory (no local key
file), the passphrase the person typed opened the repository, one real file
from the latest backup was restored into a private temporary folder, and its
size and SHA-256 matched what the archive records. The restored copy is then
deleted with the temporary folder. Only the outcome and date are kept, in
~/.local/state/keep/recovery-test.json; never the passphrase, the file name
or Borg's raw output.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import select
import signal
import subprocess
import tempfile
import time

import consumer

STATE_FILE = "recovery-test.json"
MAX_SAMPLE_BYTES = 16 * 1024 * 1024
CANDIDATE_LIMIT = 400
STALE_AFTER_DAYS = 182  # remind after six months
RECORDED_REASONS = ("passed", "wrong_passphrase", "key_missing", "mismatch")

MESSAGES = {
    "passed": "A file was restored from your latest backup using only your passphrase. The copy matched the backup and was then deleted.",
    "wrong_passphrase": "That passphrase didn't open the backup. Nothing was changed.",
    "key_missing": "This backup needs its key file as well as the passphrase. Export the key file and keep it with your recovery kit.",
    "no_archives": "There are no backups to test yet. Back up first, then test recovery.",
    "no_sample": "The latest backup has no file small enough to test (up to 16 MB).",
    "mismatch": "The restored file did not match the backup. Run an integrity check before relying on this backup.",
    "busy": "Another Keep operation is using the backup. Try again when it finishes.",
    "cancelled": "The recovery test was cancelled. Nothing was recorded as tested.",
    "unavailable": "Keep couldn't read the backup. Check that the destination is connected and try again.",
}


class Cancelled(Exception):
    pass


def state_root():
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "keep"


def _environment(passphrase_fd, keys_dir, security_dir):
    env = {k: v for k, v in os.environ.items()
           if k not in ("BORG_PASSPHRASE", "BORG_PASSCOMMAND", "BORG_PASSPHRASE_FD", "BORG_KEY_FILE", "BORG_REPO")}
    env.update({
        "BORG_PASSPHRASE_FD": str(passphrase_fd),
        # Empty keys dir: a repository that still needs a local key file fails
        # here, exactly as it would on a fresh machine after a disaster.
        "BORG_KEYS_DIR": str(keys_dir),
        "BORG_SECURITY_DIR": str(security_dir),
        "BORG_EXIT_CODES": "legacy",
        "BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK": "no",
        "BORG_RELOCATED_REPO_ACCESS_IS_OK": "yes",
        "BORG_DISPLAY_PASSPHRASE": "no",
    })
    return env


def _classify(stderr):
    text = (stderr or "").casefold()
    if "passphrase" in text and ("incorrect" in text or "wrong" in text):
        return "wrong_passphrase"
    if "no key file for repository" in text or ("key file" in text and "not found" in text):
        return "key_missing"
    if "failed to create/acquire the lock" in text or ("lock" in text and "timeout" in text):
        return "busy"
    return "unavailable"


class _Borg:
    """Runs borg with the passphrase on a fresh pipe per call (never in argv
    or the environment), honouring a cancel callback."""

    def __init__(self, passphrase, workdir, cancelled=None, borg="borg"):
        self.passphrase = passphrase
        self.cancelled = cancelled or (lambda: False)
        self.borg = borg
        self.keys = Path(workdir) / "keys"
        self.security = Path(workdir) / "security"
        for folder in (self.keys, self.security):
            folder.mkdir(mode=0o700)

    def _start(self, arguments, cwd=None):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, (self.passphrase + "\n").encode())
        finally:
            os.close(write_fd)
        try:
            return subprocess.Popen([self.borg, *arguments], env=_environment(read_fd, self.keys, self.security),
                                    cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, pass_fds=(read_fd,), start_new_session=True)
        finally:
            os.close(read_fd)

    def _stop(self, process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        for stream in (process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()

    def run(self, arguments, cwd=None, timeout=600):
        process = self._start(arguments, cwd)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=.25)
                    return process.returncode, stdout, stderr.decode(errors="replace")
                except subprocess.TimeoutExpired:
                    if self.cancelled():
                        raise Cancelled()
                    if time.monotonic() > deadline:
                        return 2, b"", "timeout"
        finally:
            self._stop(process)

    def first_records(self, arguments, limit, timeout=600):
        """NUL-separated records from a listing, stopping borg once `limit`
        are read so a huge archive isn't listed in full."""
        process = self._start(arguments)
        records, buffer = [], b""
        deadline = time.monotonic() + timeout
        stdout = process.stdout.fileno()
        try:
            while len(records) < limit and time.monotonic() < deadline:
                if self.cancelled():
                    raise Cancelled()
                if not select.select([stdout], [], [], .25)[0]:
                    continue
                chunk = os.read(stdout, 65536)
                if not chunk:
                    records.append(buffer)
                    break
                *complete, buffer = (buffer + chunk).split(b"\0")
                records.extend(complete)
            records = [record for record in records if record][:limit]
            if len(records) >= limit:
                return records, 0, ""
            if time.monotonic() >= deadline:
                self._stop(process)
                return records, 2, "timeout"
            stderr = process.stderr.read().decode(errors="replace")
            process.wait()
            return records, process.returncode, stderr
        finally:
            self._stop(process)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(repository, passphrase, cancelled=None, borg="borg", chooser=random.choice):
    """Run the test and return a result dict (not yet recorded)."""
    started = datetime.datetime.now().astimezone()
    result = {"repository": repository, "started": started.isoformat(timespec="seconds")}

    def finish(outcome, reason, **extra):
        result.update(extra, result=outcome, reason=reason, message=MESSAGES[reason],
                      finished=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
        return result

    try:
        with tempfile.TemporaryDirectory(prefix="keep-recovery-test-") as workdir:
            os.chmod(workdir, 0o700)
            restore_dir = Path(workdir) / "restore"
            restore_dir.mkdir(mode=0o700)
            runner = _Borg(passphrase, workdir, cancelled, borg)

            rc, info_output, stderr = runner.run(["info", "--json", repository], timeout=300)
            if rc not in (0, 1):
                return finish("failed", _classify(stderr))
            result["repository_id"] = json.loads(info_output).get("repository", {}).get("id")
            rc, stdout, stderr = runner.run(["list", "--last", "1", "--format", "{barchive}{NUL}", repository], timeout=300)
            if rc not in (0, 1):
                return finish("failed", _classify(stderr))
            archives = [part for part in stdout.split(b"\0") if part]
            if not archives:
                return finish("failed", "no_archives")
            archive = os.fsdecode(archives[-1])
            location = f"{repository}::{archive}"

            records, rc, stderr = runner.first_records(
                ["list", "--format", "{type}\t{size}\t{bpath}{NUL}", location], CANDIDATE_LIMIT)
            if rc not in (0, 1):
                return finish("failed", _classify(stderr), archive=archive)
            candidates = []
            for record in records:
                kind, size, path = record.split(b"\t", 2)
                if kind == b"-" and 0 < int(size) <= MAX_SAMPLE_BYTES:
                    candidates.append((os.fsdecode(path), int(size)))
            if not candidates:
                return finish("failed", "no_sample", archive=archive)
            path, size = chooser(candidates)
            pattern = "pf:" + path

            rc, stdout, stderr = runner.run(["list", "--format", "{sha256}", location, pattern])
            if rc not in (0, 1):
                return finish("failed", _classify(stderr), archive=archive)
            expected = stdout.decode().strip()
            rc, _, stderr = runner.run(["extract", location, pattern], cwd=restore_dir)
            if rc not in (0, 1):
                return finish("failed", _classify(stderr), archive=archive)
            restored = restore_dir / path
            ok = (restored.is_file() and not restored.is_symlink()
                  and restored.stat().st_size == size and _sha256(restored) == expected)
            return finish("passed" if ok else "failed", "passed" if ok else "mismatch",
                          archive=archive, bytes=size)
    except Cancelled:
        return finish("cancelled", "cancelled")
    except OSError:
        return finish("failed", "unavailable")


def record(result, root=None):
    """Keep only what the Status page needs. Only outcomes that say something
    about recoverability are recorded; a cancelled test, a busy or offline
    destination, or an empty backup leave the previous record alone."""
    if result.get("reason") not in RECORDED_REASONS:
        return None
    kept = {key: result[key] for key in ("result", "reason", "repository", "repository_id", "archive", "finished") if key in result}
    path = Path(root or state_root()) / STATE_FILE
    consumer.write_config(path, kept)
    os.chmod(path, 0o600)
    return kept


def load(root=None):
    try:
        return json.loads((Path(root or state_root()) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def status(record_, repository, now=None, repository_id=None):
    """(state, when-iso-or-None, advice) for the Status page's Recovery tested fact.

    state: "never" (no test for THIS repository), "ok", "warning" (passed but
    over six months ago), or "error" (the last test failed)."""
    if not consumer.matches_repository(record_, repository, repository_id):
        return "never", None, "Restore one file using only your passphrase to prove you can get your files back."
    when = record_.get("finished")
    if record_.get("result") != "passed":
        return "error", when, MESSAGES.get(record_.get("reason"), MESSAGES["unavailable"])
    try:
        finished = datetime.datetime.fromisoformat(when)
        now = now or datetime.datetime.now(finished.tzinfo)
        if (now - finished).days > STALE_AFTER_DAYS:
            return "warning", when, "Last tested over six months ago. Test again to make sure you still know your passphrase."
    except (TypeError, ValueError):
        return "warning", when, "Test again to confirm recovery still works."
    return "ok", when, "A file was restored using only your passphrase."


# --------------------------------------------------------------------------
# Recovery access: what only the person can confirm (Keep can't see a
# password manager or a drawer). Stored per repository with the date each
# item was confirmed, so Keep can ask again after a year. Never where the
# copies are.
ACCESS_FILE = "recovery-access.json"
CONFIRM_STALE_DAYS = 365
CONFIRMATIONS = ("passphrase_saved", "kit_offsite", "destination_access")


def _load_all_access(root=None):
    try:
        data = json.loads((Path(root or state_root()) / ACCESS_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_access(repository, root=None, repository_id=None):
    entry = _load_all_access(root).get(repository_id) if repository_id else None
    return entry if isinstance(entry, dict) else {}


def save_access(repository, changes, root=None, repository_id=None):
    """Merge `changes` ({key: iso-date or None to clear}) into this repository's entry."""
    if not repository_id:
        return {}
    everything = _load_all_access(root)
    entry = everything.setdefault(repository_id, {})
    for key, value in changes.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    path = Path(root or state_root()) / ACCESS_FILE
    consumer.write_config(path, everything)
    os.chmod(path, 0o600)
    return entry


def confirmation(entry, key, now=None):
    """("confirmed" | "stale" | "no", when-iso-or-None) for one confirmation."""
    when = entry.get(key)
    if not when:
        return "no", None
    try:
        confirmed = datetime.datetime.fromisoformat(when)
        now = now or datetime.datetime.now(confirmed.tzinfo)
        return ("stale" if (now - confirmed).days > CONFIRM_STALE_DAYS else "confirmed"), when
    except (TypeError, ValueError):
        return "stale", None


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

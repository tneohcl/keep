"""Qt-free Borg queries and checks with explicit credentials and cancellation."""
import json
import os
from pathlib import Path
import signal
import sys
import subprocess


def credential_environment(source=None, passfile=None):
    env = dict(os.environ if source is None else source)
    # Borg's own command/fd providers remain valid; never silently override them.
    if not any(key in env for key in ("BORG_PASSPHRASE", "BORG_PASSCOMMAND", "BORG_PASSPHRASE_FD")):
        path = Path(passfile) if passfile else Path.home() / ".config/borg/passphrase"
        env["BORG_PASSPHRASE"] = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    env["BORG_EXIT_CODES"] = "legacy"
    return env


def run_borg(arguments, env, timeout=60, progress=None):
    """No prompts, no shell, and no raw stderr in public diagnostic results.

    Queries return captured JSON; maintenance streams stdout/stderr to a caller
    supplied file (use a private temporary file when output is not requested).
    """
    process = subprocess.Popen(["borg", *arguments], env=env, stdin=subprocess.DEVNULL,
                               stdout=progress if progress else subprocess.PIPE,
                               stderr=progress if progress else subprocess.PIPE,
                               text=True, start_new_session=os.name != "nt",
                               pass_fds=(int(env["BORG_PASSPHRASE_FD"]),) if os.name != "nt" and "BORG_PASSPHRASE_FD" in env else ())
    try:
        if timeout is None:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    print("Integrity check still running (or waiting for the repository)…", file=sys.stderr)
        else:
            stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout or "", stderr or ""
    except BaseException:
        if process.poll() is None:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.communicate()
        raise


def classify_borg_auth_error(stderr_text):
    normalized = (stderr_text or "").casefold()
    if "is incorrect" in normalized and "passphrase" in normalized:
        return "wrong_passphrase"
    if "no key file for repository" in normalized:
        return "key_missing"
    return None


def query_json(arguments, env, timeout=30, runner=None):
    """Legacy GUI query contract, with all session inputs supplied by its adapter."""
    runner = runner or subprocess.run
    try:
        result = runner(["borg", *arguments], env=env, stdin=subprocess.DEVNULL,
                        capture_output=True, text=True, timeout=timeout)
        return (json.loads(result.stdout), None) if result.returncode == 0 else (None, result.stderr)
    except Exception as exc:
        return None, str(exc)

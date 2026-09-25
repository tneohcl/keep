"""Per-user, cross-process repository coordination; Borg remains authoritative."""
import hashlib
import os
from pathlib import Path
import time


class RepositoryBusy(RuntimeError):
    pass


class RepositoryLock:
    def __init__(self, repository, wait=0):
        identity = os.path.normcase(os.path.realpath(repository)) if os.name == "nt" or ":" not in repository else repository
        root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "keep/locks"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / (hashlib.sha256(identity.encode()).hexdigest() + ".lock")
        self.wait = wait
        self.fd = None

    def acquire(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        deadline = time.monotonic() + self.wait
        try:
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        if os.fstat(fd).st_size == 0:
                            os.write(fd, b"0")
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.fd = fd
                    return self
                except (BlockingIOError, PermissionError):
                    if time.monotonic() >= deadline:
                        raise RepositoryBusy("Another Keep operation is using this repository. Close archive browsing or wait for it to finish.") from None
                    time.sleep(.1)
        except BaseException:
            os.close(fd)
            raise

    def release(self):
        if getattr(self, "fd", None) is not None:
            os.close(self.fd)
            self.fd = None

    def __del__(self):
        self.release()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.release()

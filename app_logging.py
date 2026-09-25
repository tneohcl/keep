"""Private, bounded session diagnostics. Never capture raw backend output or secrets."""
import atexit
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import traceback
import uuid
import warnings

_session = None


def log_directory():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "keep/logs"


class PrivateHandler(RotatingFileHandler):
    def _open(self):
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.baseFilename, flags, 0o600)
        return os.fdopen(descriptor, "a", encoding="utf-8")

    def handleError(self, record):
        # Logging failure (full/read-only disk) must not interrupt a backup.
        pass


class SessionLog:
    def __init__(self, directory, max_bytes=2 * 1024 * 1024, keep_sessions=20):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = directory / f"session-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}.log"
        self.logger = logging.Logger("keep.session", level=logging.INFO)
        self.handler = PrivateHandler(self.path, maxBytes=max_bytes, backupCount=2, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(self.handler)
        self.closed = False
        # Only remove files owned by this logging scheme, never backup logs.
        sessions = sorted(directory.glob("session-*.log"), reverse=True)
        for old in sessions[keep_sessions:]:
            for candidate in (old, Path(str(old) + ".1"), Path(str(old) + ".2")):
                try:
                    if candidate.is_file() and not candidate.is_symlink():
                        candidate.unlink()
                except OSError:
                    pass

    def event(self, name, **fields):
        if self.closed:
            return
        # Explicit metadata only. No exception messages, locals, env, argv,
        # repository/file paths, stdout/stderr, passphrases or key material.
        allowed = {"operation", "result", "error_type", "source", "line", "frames", "count", "failed", "exit_code", "python", "platform"}
        payload = {key: value for key, value in fields.items() if key in allowed}
        payload.update(time=datetime.now(timezone.utc).isoformat(), event=name)
        try:
            self.logger.info(json.dumps(payload, ensure_ascii=True))
        except Exception:
            pass

    def exception(self, kind, value, tb):
        frames = [{"source": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                  for frame in traceback.extract_tb(tb)][-20:]
        self.event("exception", error_type=kind.__name__, frames=frames)

    def close(self):
        if not self.closed:
            self.event("session.closed")
            self.closed = True
            self.handler.close()
            self.logger.removeHandler(self.handler)


def record(name, **fields):
    if _session is not None:
        _session.event(name, **fields)


def start():
    global _session
    if _session is not None:
        return _session
    try:
        _session = SessionLog(log_directory())
    except OSError:
        print("Keep could not create its application log.", file=sys.stderr)
        return None
    _session.event("session.started", python=sys.version.split()[0], platform=sys.platform)
    previous_exception = sys.excepthook
    def exception_hook(kind, value, tb):
        _session.exception(kind, value, tb)
        previous_exception(kind, value, tb)
    sys.excepthook = exception_hook
    previous_thread = threading.excepthook
    def thread_hook(args):
        _session.exception(args.exc_type, args.exc_value, args.exc_traceback)
        previous_thread(args)
    threading.excepthook = thread_hook
    previous_warning = warnings.showwarning
    def warning_hook(message, category, filename, lineno, file=None, line=None):
        record("python.warning", error_type=category.__name__, source=Path(filename).name, line=lineno)
        previous_warning(message, category, filename, lineno, file, line)
    warnings.showwarning = warning_hook
    atexit.register(_session.close)
    return _session


def install_qt_handler():
    from PySide6.QtCore import qInstallMessageHandler
    previous = None
    def handler(kind, context, message):
        # Qt messages can embed source paths or arbitrary user text.
        record("qt.message", error_type=kind.name)
        if previous is not None:
            previous(kind, context, message)
    previous = qInstallMessageHandler(handler)

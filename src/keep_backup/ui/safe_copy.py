"""Copy reviewed restore entries into an exclusively created directory."""
import contextlib
from datetime import datetime
import os
from pathlib import Path
import shutil
import stat
import tempfile

PARTIAL_PREFIX = ".keep-partial-"
INCOMPLETE_MARKER = "RESTORE-INCOMPLETE.txt"


class CopyCancelled(Exception):
    pass


def copy_new(source, target, cancelled, skipped=None):
    """Copy `source` to the new path `target`. Files are written to a
    uniquely created temporary file and renamed when complete, so a cancelled
    or failed copy never leaves a truncated file under the real name, and
    cleanup only ever removes the file this copy created. Sockets, FIFOs and
    device files are skipped and listed in `skipped` instead of failing the
    whole folder they sit in."""
    if cancelled():
        raise CopyCancelled()
    if os.path.lexists(target):
        raise FileExistsError(f"Restore target already exists: {target}")
    if source.is_symlink():
        os.symlink(os.readlink(source), target)
    elif source.is_dir():
        target.mkdir()
        for child in source.iterdir():
            copy_new(child, target / child.name, cancelled, skipped)
        shutil.copystat(source, target)
    elif not stat.S_ISREG(source.stat().st_mode):
        if skipped is None:
            raise ValueError("Unsupported special file in backup")
        skipped.append(str(source))
    else:
        # mkstemp creates a new file exclusively (never an existing file or a
        # symlink), so whatever names the backup contains can't collide with it.
        fd, partial = tempfile.mkstemp(prefix=PARTIAL_PREFIX, dir=target.parent)
        try:
            # Own the descriptor before anything else can fail (opening the
            # source included): leaving the with block always closes it, and
            # only then can the cleanup below remove the file on Windows.
            try:
                dst = os.fdopen(fd, 'wb')
            except BaseException:
                os.close(fd)
                raise
            with dst, source.open('rb') as src:
                while True:
                    if cancelled():
                        raise CopyCancelled()
                    block = src.read(1024 * 1024)
                    if not block:
                        break
                    dst.write(block)
            shutil.copystat(source, partial)
            if os.path.lexists(target):
                raise FileExistsError(f"Restore target already exists: {target}")
            os.rename(partial, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(partial)
            raise


def restore_entries(entries, destination, cancelled=lambda: False, progress=lambda *args: None):
    """Entries are frozen (source, relative target, display name) tuples.
    Returns (done, failed, skipped): skipped lists special files left out.

    Existing roots are rejected, even if empty. Files copied before a cancel
    or error are complete, and the folder gets a RESTORE-INCOMPLETE note, so
    what's there is never mistaken for a finished restore."""
    done, failed, skipped = [], [], []
    root = Path(destination)
    try:
        if cancelled():
            raise CopyCancelled()
        if not entries:
            raise ValueError("No items were selected")
        if root.parent.resolve() != root.parent.absolute():
            raise ValueError("Choose a restore folder without symbolic-link parents")
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir(mode=0o700)
    except Exception as exc:
        return [], ["Restore cancelled" if isinstance(exc, CopyCancelled) else str(exc)], []
    for index, (source, relative, name) in enumerate(entries):
        try:
            rel = Path(relative)
            if rel.is_absolute() or '..' in rel.parts or not rel.parts:
                raise ValueError("Invalid restore target")
            target = root / rel
            if not os.path.lexists(source):
                raise FileNotFoundError("Requested data is missing from this backup")
            # Never traverse a link created by an earlier entry.
            parent = target.parent
            while parent != root:
                if parent.is_symlink():
                    raise ValueError("Restore target crosses a symbolic link")
                parent = parent.parent
            target.parent.mkdir(parents=True, exist_ok=True)
            entry_skipped = []
            copy_new(Path(source), target, cancelled, entry_skipped)
            done.append(f"{name}: {target}")
            skipped += [f"{name}: {path}" for path in entry_skipped]
        except CopyCancelled:
            failed.append(f"Restore cancelled: {len(entries) - index} item(s) incomplete or not started")
            break
        except Exception as exc:
            failed.append(f"{name}: {exc}")
        progress(index + 1, len(entries))
    if failed:
        _mark_incomplete(root, failed)
    return done, failed, skipped


def _mark_incomplete(root, failed):
    """Leave a note saying the restore didn't finish. Created exclusively and
    never through a symlink: if restored content already uses the name, the
    note takes the next free one (RESTORE-INCOMPLETE-2.txt, ...)."""
    note = ("This restore did not finish. The files in this folder are complete copies,\n"
            "but some of the items you chose are missing or only partly restored.\n\n"
            f"Stopped: {datetime.now().astimezone().isoformat(timespec='seconds')}\n\n"
            + "\n".join(failed) + "\n")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    stem, suffix = os.path.splitext(INCOMPLETE_MARKER)
    for n in range(1, 100):
        path = root / (INCOMPLETE_MARKER if n == 1 else f"{stem}-{n}{suffix}")
        try:
            fd = os.open(path, flags, 0o644)
        except FileExistsError:
            continue
        except OSError:
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(note)
        return path
    return None

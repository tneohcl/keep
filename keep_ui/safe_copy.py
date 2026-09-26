"""Copy reviewed restore entries into an exclusively created directory."""
import os
from pathlib import Path
import shutil
import stat


class CopyCancelled(Exception):
    pass


def copy_new(source, target, cancelled):
    if cancelled():
        raise CopyCancelled()
    if os.path.lexists(target):
        raise FileExistsError(f"Restore target already exists: {target}")
    if source.is_symlink():
        os.symlink(os.readlink(source), target)
    elif source.is_dir():
        target.mkdir()
        for child in source.iterdir():
            copy_new(child, target / child.name, cancelled)
        shutil.copystat(source, target)
    else:
        if not stat.S_ISREG(source.stat().st_mode):
            raise ValueError("Unsupported special file in backup")
        # Exclusive creation also rejects dangling destination symlinks.
        with source.open('rb') as src, target.open('xb') as dst:
            while True:
                if cancelled():
                    raise CopyCancelled()
                block = src.read(1024 * 1024)
                if not block:
                    break
                dst.write(block)
        shutil.copystat(source, target)


def restore_entries(entries, destination, cancelled=lambda: False, progress=lambda *args: None):
    """Entries are frozen (source, relative target, display name) tuples.

    Existing roots are rejected, even if empty. Partial copies are kept for
    inspection on cancellation/error; they are never reported as completed.
    """
    done, failed = [], []
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
        return [], ["Restore cancelled" if isinstance(exc, CopyCancelled) else str(exc)]
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
            copy_new(Path(source), target, cancelled)
            done.append(f"{name}: {target}")
        except CopyCancelled:
            failed.append(f"Restore cancelled: {len(entries) - index} item(s) incomplete or not started")
            break
        except Exception as exc:
            failed.append(f"{name}: {exc}")
        progress(index + 1, len(entries))
    return done, failed

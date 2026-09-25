#!/usr/bin/env python3
import app_logging
import borg_ops
from operation_lock import RepositoryLock

if __name__ == "__main__":
    app_logging.start()

from keep_ui.mount_progress import ArchiveOpenProgress, ArchiveMountWorker
from mount_service import MountCoordinator
from keep_ui.restore_picker import RestorePicker
from keep_ui.backup_panel import BackupPanel
from keep_ui.status_page import StatusPage
from keep_ui.restore_page import RestorePage
from keep_ui.common import DisclosureSection
from keep_ui.restore_results import show_restore_results
from keep_ui.application_selection import ApplicationSelectionDialog
from keep_ui.recovery_test_dialog import RecoveryTestDialog
from keep_ui import recovery_access
from keep_ui.review_restore import ReviewRestoreDialog
from keep_ui.backup_list import BackupListPanel
import recovery_test
import base64
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import theming  # also puts the bundled vendor/odcs_ui on sys.path
from odcs_ui import timefmt as odcs_timefmt
from odcs_ui.widgets import ViewSwitch
from collections import namedtuple
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QDir, QSize, QTimer, QRect, QEvent, QTime, QLocale
from PySide6.QtGui import QAction, QActionGroup, QIcon, QColor, QPalette, QFont, QFontMetrics, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QPlainTextEdit, QFileSystemModel, QTreeView, QLayout,
    QComboBox, QFileDialog, QMessageBox, QAbstractItemView, QProgressDialog,
    QTabWidget, QListWidget, QListWidgetItem, QSplitter, QProgressBar,
    QSizePolicy, QDialog, QLineEdit, QScrollArea, QFrame, QStackedWidget,
    QStyledItemDelegate, QStyle, QInputDialog, QCheckBox, QMenuBar,
    QButtonGroup, QTimeEdit, QStyleOptionFocusRect, QStackedWidget, QMenu,
)

HOME = str(Path.home())
# Everything in this file that's specific to one person's machine (repo path,
# project folder, which apps to show, which apps migrated between native and
# Flatpak) lives in config.json, not here - so someone else can reuse this
# tool by editing that file, not this code.
APP_DIR = Path(__file__).parent
CONFIG_PATH = APP_DIR / "config.json"

import consumer as _consumer_module  # noqa: E402 - pure-python config helpers
CONFIG_PATH = _consumer_module.config_path(str(APP_DIR))

if CONFIG_PATH.exists():
    CONFIG = json.loads(CONFIG_PATH.read_text())
else:
    # A consumer build must be able to launch before a hand-written config
    # exists. The UI can then guide the user through sources/destination/
    # schedule rather than crashing at import time.
    CONFIG = _consumer_module.default_config(HOME)
_consumer_module.normalize_config(CONFIG, HOME)

import destination as _destination_module  # noqa: E402 - needs CONFIG_PATH/HOME resolved first

# How Borg stores this user's home dir inside the archive: it was backed up
# as an absolute path (e.g. /home/alice), and Borg strips only the leading
# "/" - so archive paths are "home/<username>/...". Derived from the actual
# running user, not assumed, so this works under any account.
HOME_IN_ARCHIVE = f"home/{os.path.basename(HOME)}"

PASSFILE = f"{HOME}/.config/borg/passphrase"
EXCLUDES_FILE = f"{HOME}/.config/borg/excludes.txt"
# Where a brand-new repo's paper key gets written during Change Destination
# -> create new repo - a real module-level constant (not inlined at the
# call site) specifically so tests can redirect it like every other path
# here. Missing that redirect is exactly what let this leak into the real
# $HOME repeatedly: every test that drives a real new-repo-init through
# change_backup_destination() re-wrote a real file at this fixed location,
# since the export happens before KeyExportDialog is even constructed, so
# stubbing that dialog alone never prevented it.
NEW_REPO_KEY_EXPORT_PATH = f"{HOME}/keep-new-repo-key-export.txt"
LOGDIR = f"{HOME}/.local/state/borg-logs"
BACKUP_SCRIPT = os.path.expanduser(CONFIG.get("backup_script", ""))
# Legacy aliases retained for the mature regression harness and for old
# configs. The consumer UI no longer assumes one special "project" folder;
# build_folder_catalog() reads CONFIG["backup_sources"] dynamically.
_legacy_sources = _consumer_module.backup_source_entries(CONFIG)
PROJECT_DIR = CONFIG.get("project_dir") or (_legacy_sources[0]["path"] if _legacy_sources else HOME)
PROJECT_DIR_IN_ARCHIVE = PROJECT_DIR.lstrip("/")
PROJECT_EXCLUDE = set(CONFIG.get("project_exclude", []))
MOUNTPOINT = "/tmp/borg-keep-mount"

# REPO/NAS_MOUNT_CHECK (kept under that name for the least code churn, even
# though it's not always literally a NAS now) are resolved fresh - not just
# once at import time - since a removable destination's availability can
# change WHILE Keep is already open (plug in the drive after launching), and
# a stale cached "unavailable" from startup would keep it looking broken
# after that. refresh_destination() is called from refresh_status() (already
# the central "check everything" entry point) and again right before any
# action that actually touches the repo, so it's never staler than the last
# user-visible status refresh.
DEST_STATUS = {}
REPO = None
NAS_MOUNT_CHECK = None


def refresh_destination():
    global DEST_STATUS, REPO, NAS_MOUNT_CHECK
    DEST_STATUS = _destination_module.resolve_destination(CONFIG["destination"])
    REPO = DEST_STATUS["repo"]
    NAS_MOUNT_CHECK = DEST_STATUS["mount_check"]
    return DEST_STATUS


refresh_destination()


def save_config():
    # temp file + os.replace(), same reasoning as save_passphrase_atomic():
    # a crash or power loss mid-write must never leave config.json
    # truncated/corrupt - that would break both Keep's next launch and the
    # backup script's own read_config() (a plain json.load()) equally.
    # Found missing via a reviewer's whole-app sweep.
    _consumer_module.write_config(CONFIG_PATH, CONFIG)


def effective_backup_command():
    """Command used by Backup Now and by Keep-managed systemd schedules.

    Existing installations that already have a proven custom shell script
    stay on it until the user explicitly switches to Keep-managed backups;
    fresh consumer installs use the bundled Python engine.
    """
    if CONFIG.get("backup_engine") == "external" and BACKUP_SCRIPT:
        return [BACKUP_SCRIPT]
    return _consumer_module.backup_command(CONFIG, str(CONFIG_PATH), str(APP_DIR), sys.executable)


def configured_timer_unit():
    return CONFIG.get("schedule", {}).get("timer_unit") or "keep-backup.timer"


def timer_enabled(unit=None):
    unit = unit or configured_timer_unit()
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-enabled", unit],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and r.stdout.strip() in {"enabled", "enabled-runtime", "static"}
    except Exception:
        return False


def apply_keep_schedule(schedule):
    """Install/update or disable Keep's per-user systemd timer.

    Returns (ok, message). This only runs after an explicit user Save in the
    Automatic Backups dialog; opening Keep never mutates systemd state.

    Unit files are treated transactionally too: if systemd or config saving
    fails, the previous files and best-effort enablement state are restored.
    A consumer-facing schedule control must not leave a half-migrated timer
    behind just because one command failed midway through.
    """
    schedule = dict(schedule)
    old_schedule = dict(CONFIG.get("schedule", {}))
    old_unit = configured_timer_unit()
    old_unit_enabled = timer_enabled(old_unit) if old_unit else False
    keep_was_enabled = timer_enabled("keep-backup.timer")
    schedule["managed_by_keep"] = True
    schedule["timer_unit"] = "keep-backup.timer"

    user_dir = Path.home() / ".config/systemd/user"
    service_path = user_dir / "keep-backup.service"
    timer_path = user_dir / "keep-backup.timer"
    old_files = {}
    for path in (service_path, timer_path):
        try:
            old_files[path] = path.read_text() if path.exists() else None
        except Exception:
            old_files[path] = None

    def _systemctl(*args, check=False):
        return subprocess.run(
            ["systemctl", "--user", *args],
            check=check, capture_output=True, text=True, timeout=10,
        )

    def _restore_previous_state():
        # Best effort only; the original error is still what gets reported.
        try:
            for path, contents in old_files.items():
                if contents is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(contents)
            _systemctl("daemon-reload")
            if keep_was_enabled:
                _systemctl("enable", "--now", "keep-backup.timer")
            else:
                _systemctl("disable", "--now", "keep-backup.timer")
            if old_unit and old_unit != "keep-backup.timer":
                if old_unit_enabled:
                    _systemctl("enable", "--now", old_unit)
                else:
                    _systemctl("disable", "--now", old_unit)
        except Exception:
            pass

    try:
        user_dir.mkdir(parents=True, exist_ok=True)
        render_config = dict(CONFIG)
        render_config["schedule"] = schedule
        schedule_python = shutil.which("python3") or sys.executable
        service_text, timer_text = _consumer_module.render_systemd_units(
            render_config, str(CONFIG_PATH), str(APP_DIR), schedule_python,
        )
        service_path.write_text(service_text)
        timer_path.write_text(timer_text)
        _systemctl("daemon-reload", check=True)

        if schedule.get("enabled"):
            # Bring the new timer up FIRST. Only after that succeeds do we
            # retire a legacy timer; a failure must never turn a working
            # automatic backup schedule off just because migration failed.
            _systemctl("enable", "--now", "keep-backup.timer", check=True)
            if old_unit and old_unit != "keep-backup.timer" and old_unit_enabled:
                try:
                    _systemctl("disable", "--now", old_unit, check=True)
                except Exception:
                    # Avoid leaving BOTH the legacy and Keep timer enabled if
                    # takeover cannot finish cleanly.
                    _systemctl("disable", "--now", "keep-backup.timer")
                    raise
            message = "Automatic backups are on."
        else:
            _systemctl("disable", "--now", "keep-backup.timer", check=True)
            if old_unit and old_unit != "keep-backup.timer" and old_unit_enabled:
                _systemctl("disable", "--now", old_unit, check=True)
            message = "Automatic backups are off. You can still use Back Up Now."
        CONFIG["schedule"] = schedule
        save_config()
        return True, message
    except Exception as e:
        CONFIG["schedule"] = old_schedule
        _restore_previous_state()
        return False, str(e)


def set_destination(new_dest):
    """The one place both Keep AND the backup engine's destination change.

    Choosing a real destination also completes first-run setup; schedule may
    legitimately remain off for users who only want manual backups.
    """
    CONFIG["destination"] = new_dest
    CONFIG["setup_complete"] = True
    save_config()
    return refresh_destination()


def looks_like_borg_repo(folder):
    config_file = os.path.join(folder, "config")
    if not os.path.isfile(config_file):
        return False
    try:
        return "[repository]" in Path(config_file).read_text(errors="ignore")
    except Exception:
        return False


def generate_passphrase():
    return base64.b64encode(os.urandom(36)).decode()


# Apps known to have existed as EITHER a native (apt) or Flatpak install on
# this machine (see config.json to add your own) - "xdg_folder" is the
# folder name both the native app and Flatpak's redirected XDG dirs use
# identically under .config/ and .local/share/, which holds for most
# well-behaved apps (it's what Flatpak's sandboxing relies on).
CROSS_INSTALL_APPS = CONFIG.get("cross_install_apps", {})

# {catalog entry label: [process names]} - opt-in per entry, since there's no
# "list every native app currently running" equivalent to `flatpak ps` to
# check automatically. Keyed by label (not appid - curated items like
# digiKam have no appid at all) so it applies uniformly to any entry,
# cross-install or curated.
NATIVE_PROCESS_NAMES = CONFIG.get("native_process_names", {})


def is_natively_installed(dpkg_name):
    if not dpkg_name:
        return False
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f=${Status}", dpkg_name], capture_output=True, text=True, timeout=5)
        return out.returncode == 0 and "installed" in out.stdout
    except Exception:
        return False


def is_flatpak_installed(appid):
    try:
        out = subprocess.run(["flatpak", "info", appid], capture_output=True, timeout=5)
        return out.returncode == 0
    except Exception:
        return False


def is_flatpak_running(appid):
    try:
        out = subprocess.run(["flatpak", "ps", "--columns=application"], capture_output=True, text=True, timeout=5)
        return appid in out.stdout.split()
    except Exception:
        return False


def is_native_process_running(process_names):
    """Unlike Flatpak (flatpak ps lists every running sandboxed app by
    appid, cheaply), there's no equivalent single "what's running" query
    for native processes - so this only checks the specific names an entry
    is configured with (native_process_names in config.json), not a general
    "is any app on this machine running" scan."""
    for name in process_names:
        try:
            out = subprocess.run(["pgrep", "-x", name], capture_output=True, timeout=5)
            if out.returncode == 0:
                return True
        except Exception:
            continue
    return False


def flatpak_appid_from_path(path):
    """Pulls the appid back out of a .var/app/<appid>/... path, if that's
    what this is - used to check whether the app a restore is about to
    touch is currently running."""
    marker = "/.var/app/"
    if marker not in path:
        return None
    return path.split(marker, 1)[1].split("/", 1)[0]


# A catalog entry as shown in the Apps/Projects picker. subtitle is None for
# anything that isn't ambiguous about where it restores to - only cross-install
# apps with BOTH a native and Flatpak archived copy get a real subtitle
# ("Native backup" / "Flatpak backup"), kept separate from the label itself
# rather than crammed into one bracketed string. category groups entries into
# picker sections ("applications" / "personal" / "system" / "projects") -
# ItemPicker only shows section headers at all when a catalog mixes more than
# one category, so the Projects tab (always a single category) still renders
# as a single unheaded list, unchanged from before this existed.
CatalogEntry = namedtuple("CatalogEntry", "label subtitle rel_paths icon category")


def has_meaningful_content(path):
    """A path existing isn't enough to justify a restore tile - an empty
    leftover directory (e.g. a stale native app's config folder with nothing
    in it, left behind after switching to the Flatpak build) shouldn't
    generate a tile that has nothing real behind it. A file or symlink
    always counts; a directory only counts if something is actually inside
    it, anywhere in its subtree."""
    if os.path.isfile(path) or os.path.islink(path):
        return True
    if os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            if files:
                return True
            if any(os.path.islink(os.path.join(root, d)) for d in dirs):
                return True
    return False


# (display name, [paths relative to MOUNTPOINT], icon theme name, category) -
# curated entries only shown if they actually have meaningful content in the
# mounted archive. Config stores paths relative to $HOME; prefixed here with
# wherever this user's home actually landed in the archive.
CURATED_ITEMS = [
    (label, [f"{HOME_IN_ARCHIVE}/{p}" for p in rel_paths], icon, category)
    for label, rel_paths, icon, category in CONFIG.get("curated_items", [])
]


# set only via the Unlock Backup dialog, for the rest of THIS session - never
# written to disk unless the user explicitly checks "remember" (which writes
# to PASSFILE separately and makes this redundant, but harmless to also
# hold). Lets a one-time unlock actually stay usable for subsequent browsing/
# restoring without re-prompting every single borg call, while still
# defaulting to NOT persisting a recovery passphrase past this session.
_session_passphrase_override = None


def borg_env():
    """Missing PASSFILE is tolerated, not raised - on a genuinely fresh
    machine (nothing restored yet at all) there IS no passphrase file, and
    every caller here (mount, paper-key import, key-file import) needs
    Borg to actually run so it can report its own real error ("no key
    file", say) for classify_borg_auth_error() to work with. An empty
    BORG_PASSPHRASE doesn't change what Borg reports for a genuinely
    missing key either way - it checks for the key before the passphrase
    ever matters. Raising here instead would abort before Borg ever runs,
    silently defeating the whole recovery flow this exists for."""
    env = os.environ.copy()
    if _session_passphrase_override is not None:
        env["BORG_PASSPHRASE"] = _session_passphrase_override
    else:
        try:
            env["BORG_PASSPHRASE"] = Path(PASSFILE).read_text().strip()
        except FileNotFoundError:
            env["BORG_PASSPHRASE"] = ""
    return env


PASSPHRASE_AUDIT_LOG = f"{LOGDIR}/passphrase-audit.log"


def save_passphrase_atomic(passphrase, reason, destination_label):
    """The only two places Keep should ever change the persisted
    passphrase - a brand new repository being created, or a validated
    Unlock passphrase being remembered - go through here. Same crash-safe
    write as before (temp file in the same directory, then os.replace(),
    so a crash or power loss mid-write can't leave a truncated/corrupt
    passphrase file behind), now shared instead of duplicated, plus a
    plain-text audit line (timestamp + reason + destination, NEVER the
    passphrase itself) - added after a real incident where the passphrase
    changed unexpectedly and there was no way to tell whether Keep had
    done it or something else had. The audit line is best-effort: the
    credential write above is the operation that actually matters, and by
    the time the audit log is touched it has already succeeded, so a
    logging failure (disk full, odd permissions, whatever) must not make
    this raise and read as the credential change itself having failed."""
    tmp_passfile = f"{PASSFILE}.tmp"
    Path(tmp_passfile).write_text(passphrase)
    os.chmod(tmp_passfile, 0o600)
    os.replace(tmp_passfile, PASSFILE)
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        with open(PASSPHRASE_AUDIT_LOG, "a") as f:
            timestamp = datetime.now().isoformat(timespec="seconds")
            f.write(f"{timestamp} persisted passphrase - reason={reason} destination={destination_label!r}\n")
    except OSError:
        pass


def classify_borg_auth_error(stderr_text):
    return borg_ops.classify_borg_auth_error(stderr_text)


def import_paper_key(repo, pasted_text, timeout=60):
    """Drives `borg key import --paper` interactively - confirmed by testing
    that this is the ONLY supported input mode for --paper (it explicitly
    refuses a file path: "with --paper import from file is not supported"),
    and that just piping the whole exported text at once does NOT work
    (the header/blank lines confuse its line-by-line parser) - it genuinely
    expects one line's value at a time, in response to its own "id: " / " N:
    " prompts, checking each immediately (errors re-prompt the SAME line
    rather than advancing, confirmed empirically: "line checksum did not
    match, try same line again"). Parses the pasted export text back into
    those (label, value) pairs itself, then feeds them in that same call-
    and-response rhythm. Returns (success, message)."""
    pairs = []
    for line in pasted_text.splitlines():
        m = re.match(r'^\s*(id|\d+)\s*:\s*(.+?)\s*$', line)
        if m:
            pairs.append((m.group(1), m.group(2)))
    if not pairs or pairs[0][0] != "id":
        return False, "That doesn't look like a paper key export - it should start with an \"id:\" line."

    proc = subprocess.Popen(
        ["borg", "key", "import", "--paper", repo],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=borg_env(), text=True, bufsize=1,
    )

    def read_available(read_timeout):
        import select
        chunks = []
        end = time.time() + read_timeout
        while time.time() < end:
            r, _, _ = select.select([proc.stdout], [], [], 0.3)
            if r:
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                chunks.append(chunk.decode(errors="replace"))
                end = time.time() + 0.4
            elif chunks:
                break
        return "".join(chunks)

    try:
        read_available(5)  # initial "id: " prompt
        for label, value in pairs:
            if proc.poll() is not None:
                break
            proc.stdin.write(value + "\n")
            proc.stdin.flush()
            response = read_available(5)
            if "try again" in response or "checksum did not match" in response:
                return False, f"Line \"{label}:\" didn't validate - check for typos in the pasted key and try again."
        proc.stdin.close()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "Timed out waiting for the import to finish."
        if proc.returncode != 0:
            return False, "The import did not complete successfully - the pasted key may be incomplete."
        return True, None
    finally:
        if proc.poll() is None:
            proc.kill()


def check_passphrase(repo, passphrase):
    """Tests a candidate passphrase directly against the repo via a cheap
    read-only call, WITHOUT touching _session_passphrase_override unless
    it's confirmed to actually work - a wrong guess here must never leak
    into borg_env() and break whatever the next unrelated borg call was
    going to do. stdin=DEVNULL for the same reason every other call below
    has it - see run_borg_json_checked()."""
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = passphrase
    try:
        result = subprocess.run(["borg", "info", repo], env=env, stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stderr
    except subprocess.TimeoutExpired:
        return False, "Timed out checking that passphrase."


def import_key_file(repo, file_path, timeout=30):
    """The plain (non-paper) form of key import - unlike --paper, this one
    DOES accept a file path directly and isn't interactive (confirmed: the
    file-path rejection error is specific to --paper), so this is just a
    single bounded subprocess call, no prompt-response driving or worker
    thread needed. stdin=DEVNULL for the same reason every other call
    below has it - see run_borg_json_checked()."""
    try:
        result = subprocess.run(
            ["borg", "key", "import", repo, file_path], stdin=subprocess.DEVNULL,
            env=borg_env(), capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return False, result.stderr or "The import did not complete successfully."
        return True, None
    except subprocess.TimeoutExpired:
        return False, "Timed out waiting for the import to finish."


def run_borg_json(args):
    return run_borg_json_checked(args)[0]


def run_borg_json_checked(args):
    # Session credentials are resolved here, never inside the Qt-free service.
    return borg_ops.query_json(args, borg_env(), runner=subprocess.run)


def friendly_clock(hour, minute):
    """A clock time in the desktop's own format (QLocale), e.g. "4:00 AM" on
    en_US or "04:00" on a 24-hour locale. Every time Keep shows goes through
    here, so the schedule button and the status panel can't disagree again
    (they showed "04:00" and "4:00 AM" side by side before 2026-09-25).
    Delegates to the shared ODCS helper so every ODCS app formats alike."""
    return odcs_timefmt.friendly_clock(hour, minute)


def schedule_summary(schedule):
    """"Daily at 4:00 AM" / "Weekly on Monday at 4:00 AM" for the schedule button."""
    parsed = QTime.fromString(str(schedule.get("time") or "04:00"), "HH:mm")
    clock = friendly_clock(parsed.hour(), parsed.minute()) if parsed.isValid() else str(schedule.get("time"))
    if schedule.get("frequency") == "weekly":
        return f"Weekly on {schedule.get('weekday') or 'Monday'} at {clock}"
    return f"Daily at {clock}"


def friendly_datetime(dt):
    """Human-readable, not '2026-09-14T22:24:15+08:00' - a real user report
    ("shouldn't be too technical... looks like military time"). Today/
    Yesterday/Tomorrow/weekday name/short date, whichever is clearest, with
    a plain 12-hour time - the one shared formatter for every timestamp
    Keep shows (backup/maintenance log timestamps, Borg archive times, the
    next-scheduled run), each from a different source in a different raw
    format, so they all read the same way once actually displayed."""
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    today = now.date()
    d = dt.date()
    time_str = friendly_clock(dt.hour, dt.minute)
    if d == today:
        return f"Today at {time_str}"
    if d == today - timedelta(days=1):
        return f"Yesterday at {time_str}"
    if d == today + timedelta(days=1):
        return f"Tomorrow at {time_str}"
    if timedelta(days=0) < (d - today) < timedelta(days=7) or timedelta(days=0) < (today - d) < timedelta(days=7):
        return f"{dt.strftime('%A')} at {time_str}"  # e.g. "Monday at 3:22 PM" - covers both a recent past day and a later day this week
    if d.year == today.year:
        return f"{dt.strftime('%b')} {dt.day} at {time_str}"
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} at {time_str}"


def new_restore_folder():
    """A fresh, clearly-labelled folder for restored copies - never a live path."""
    return os.path.join(HOME, "Keep-Restored", datetime.now().strftime("%Y%m%d-%H%M%S"))


def friendly_timestamp(iso_string):
    """Parses Keep's two ISO-ish timestamp sources - `date -Is` (with a
    timezone offset, from backup/maintenance logs) or Borg's own naive-
    local archive time (microseconds, no offset) - into the shared
    friendly display. Returns the original string unchanged if parsing
    fails - never hide or break a real value just because reformatting
    couldn't handle it (a format this doesn't expect is exactly the kind
    of thing that should stay visible, not silently disappear)."""
    if not iso_string:
        return iso_string
    try:
        dt = datetime.fromisoformat(iso_string)
    except ValueError:
        return iso_string
    return friendly_datetime(dt)


def friendly_systemd_timestamp(text):
    """Parses systemctl show's own 'Tue 2026-09-15 04:04:52 +08' format
    (confirmed against the real output, not guessed) into the same shared
    friendly display. Returns the original text unchanged if parsing fails
    (e.g. 'n/a' when the timer genuinely has no next run scheduled).

    Python's %z needs a 2-digit offset padded to 4 (+0800, not systemd's
    bare +08) - confirmed by an actual failed parse against the real output
    before adding this, not assumed."""
    if not text:
        return text
    padded = re.sub(r"([+-]\d{2})$", r"\g<1>00", text)
    try:
        dt = datetime.strptime(padded, "%a %Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return text
    return friendly_datetime(dt)


def latest_log(prefix):
    files = sorted(glob.glob(f"{LOGDIR}/{prefix}-*.log"))
    return files[-1] if files else None


def log_verdict(prefix, pass_markers, fail_markers):
    path = latest_log(prefix)
    if not path:
        return "never run", None
    text = Path(path).read_text()
    ts_match = re.search(r"^\S+", text)
    ts = ts_match.group(0) if ts_match else ""
    for m in fail_markers:
        if m in text:
            return "FAILED", ts
    for m in pass_markers:
        if m in text:
            return "ok", ts
    return "unknown", ts


def last_backup_attempt_status():
    """The newest archive tells you the last SUCCESSFUL backup - not whether
    the most recent scheduled attempt actually worked. A failed run creates
    no new archive at all, so "last successful" alone can look healthy on a
    screen the same morning a backup silently failed. The script uses
    `set -euo pipefail`, so a failed run's log just stops partway through
    rather than printing an explicit FAILED marker - treat "didn't reach the
    success line" as failed, not merely "unknown".

    A run the user deliberately cancelled (BackupWorker.stop()) is NOT the
    same event as one that failed on its own - reviewer-flagged: showing a
    manual Stop as a red "failed backup" undermines trust in what that
    actually means elsewhere. BackupWorker.run() appends a STOPPED BY USER
    marker line to this same log once the killed process actually exits, so
    this is a pure text check like the others - no shared in-memory state
    with the worker, safe to call any time, from any process, exactly like
    the existing checks."""
    path = latest_log("backup")
    if not path:
        return "never run", None
    text = Path(path).read_text()
    ts_match = re.search(r"^\S+", text)
    ts = ts_match.group(0) if ts_match else ""
    if "backup completed with warnings" in text:
        return "warning", ts
    if "backup completed successfully" in text:
        return "ok", ts
    if "STOPPED BY USER" in text:
        return "stopped", ts
    return "FAILED", ts


def flatpak_app_names():
    """appid -> friendly name, from the currently-installed flatpak apps."""
    names = {}
    try:
        out = subprocess.run(
            ["flatpak", "list", "--app", "--columns=name,application"],
            capture_output=True, text=True, timeout=10,
        )
        for line in out.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                names[parts[1].strip()] = parts[0].strip()
    except Exception:
        pass
    return names


def readable_fallback_name(appid):
    """For a flatpak app that's no longer installed (so flatpak can't tell us
    its real name), turn 'net.waterfox.waterfox' into 'Waterfox' rather than
    showing the raw reverse-DNS id."""
    import applications
    return applications.display_name(appid, HOME)


# flatpak apps whose sandbox writes real data outside ~/.var/app too
EXTRA_PATHS = {
    appid: [f"{HOME_IN_ARCHIVE}/{p}" for p in rel_paths]
    for appid, rel_paths in CONFIG.get("extra_flatpak_paths", {}).items()
}


def as_pairs(rel_paths):
    """Plain 1:1 mapping: restore lands back at the same path it came from.
    The right choice for anything NOT in CROSS_INSTALL_APPS."""
    return [(p, f"/{p}") for p in rel_paths]


def cross_install_entries(display_name, spec):
    """Checks the archive for the native and/or Flatpak location of a known
    cross-install app.

    If only ONE form is present in this archive (the common case), maps it
    to wherever this app CURRENTLY lives (native or Flatpak, whichever's
    actually installed right now) - so a restore lands in the right place
    regardless of which form the backup was made from vs. installed today.

    If BOTH forms are present in this same archive, that's genuinely
    ambiguous - returns TWO separate entries instead of silently merging
    them (which previously let one source overwrite the other at the same
    target with no indication anything was lost)."""
    appid = spec["flatpak_appid"]
    folder = spec["xdg_folder"]
    native_config = f"{HOME_IN_ARCHIVE}/.config/{folder}"
    native_data = f"{HOME_IN_ARCHIVE}/.local/share/{folder}"
    flatpak_config = f"{HOME_IN_ARCHIVE}/.var/app/{appid}/config/{folder}"
    flatpak_data = f"{HOME_IN_ARCHIVE}/.var/app/{appid}/data/{folder}"
    # explicit membership check against the known candidates - no guessing
    # from the path string, which broke on Flatpak's dot-less "config"/"data"
    is_config_path = {native_config: True, native_data: False, flatpak_config: True, flatpak_data: False}

    found_native = [p for p in (native_config, native_data) if has_meaningful_content(f"{MOUNTPOINT}/{p}")]
    found_flatpak = [p for p in (flatpak_config, flatpak_data) if has_meaningful_content(f"{MOUNTPOINT}/{p}")]
    if not found_native and not found_flatpak:
        return []

    def pairs_for(found_paths, config_root, data_root):
        return [(p, f"{config_root if is_config_path[p] else data_root}/{folder}") for p in found_paths]

    flatpak_root = (f"{HOME}/.var/app/{appid}/config", f"{HOME}/.var/app/{appid}/data")
    native_root = (f"{HOME}/.config", f"{HOME}/.local/share")

    if found_native and found_flatpak:
        # both sources exist and are genuinely ambiguous - keep them as two
        # picks, but the name/subtitle split (rather than one bracketed
        # string) reads as "same app, two snapshots" instead of two apps
        flatpak_live = is_flatpak_installed(appid)
        native_live = is_natively_installed(spec["native_dpkg"])
        if flatpak_live and native_live:
            # both forms genuinely live right now - each source has its own
            # real, distinct target, so no ambiguity in restoring either or both
            return [
                CatalogEntry(display_name, "Native backup", pairs_for(found_native, *native_root), appid, "applications"),
                CatalogEntry(display_name, "Flatpak backup", pairs_for(found_flatpak, *flatpak_root), appid, "applications"),
            ]
        if flatpak_live or native_live:
            # only ONE form is live - per GOALS.md, restore should always land
            # wherever the app actually reads from today, regardless of which
            # form the chosen backup happened to be made from. Both sources
            # therefore target the SAME live location here; picking both at
            # once is caught separately (see ItemPicker._checked_items) since
            # that would just make one silently overwrite the other.
            target_root = flatpak_root if flatpak_live else native_root
            return [
                CatalogEntry(display_name, "Native backup", pairs_for(found_native, *target_root), appid, "applications"),
                CatalogEntry(display_name, "Flatpak backup", pairs_for(found_flatpak, *target_root), appid, "applications"),
            ]
        # neither currently installed - nothing "live" to converge on, so
        # mirror each source back to its own original archived location
        return [
            CatalogEntry(display_name, "Native backup", [(p, f"/{p}") for p in found_native], appid, "applications"),
            CatalogEntry(display_name, "Flatpak backup", [(p, f"/{p}") for p in found_flatpak], appid, "applications"),
        ]

    # only one form archived at all - unambiguous, so no subtitle needed;
    # the user shouldn't need to know or care which packaging form this was
    found = found_native or found_flatpak
    if is_flatpak_installed(appid):
        target_root = flatpak_root
    elif is_natively_installed(spec["native_dpkg"]):
        target_root = native_root
    else:
        return [CatalogEntry(display_name, None, [(p, f"/{p}") for p in found], appid, "applications")]  # neither installed - best effort, mirror as-is

    return [CatalogEntry(display_name, None, pairs_for(found, *target_root), appid, "applications")]


def build_app_catalog():
    """Scans the MOUNTED archive (not the live system) for what's actually
    available to restore, so this reflects that specific backup, not today's
    installed software. Returns CatalogEntry items grouped into 3 sections by
    ItemPicker - applications, personal files, and system/settings - since
    packaging format (native vs Flatpak) is only a meaningful distinction for
    a handful of apps and isn't the primary way any of this should be
    organized for someone who just wants their stuff back."""
    catalog = []
    names = flatpak_app_names()
    claimed_appids = {spec["flatpak_appid"] for spec in CROSS_INSTALL_APPS.values()}

    for display_name, spec in CROSS_INSTALL_APPS.items():
        catalog.extend(cross_install_entries(display_name, spec))

    var_app_dir = f"{MOUNTPOINT}/{HOME_IN_ARCHIVE}/.var/app"
    if os.path.isdir(var_app_dir):
        for appid in sorted(os.listdir(var_app_dir)):
            if appid in claimed_appids:
                continue  # already handled above with smart native/Flatpak targeting
            rel_paths = [f"{HOME_IN_ARCHIVE}/.var/app/{appid}"] + EXTRA_PATHS.get(appid, [])
            if not any(has_meaningful_content(f"{MOUNTPOINT}/{p}") for p in rel_paths):
                continue  # stale/empty leftover, not a real restore candidate
            label = names.get(appid) or readable_fallback_name(appid)
            # flatpak exports its app icon under the appid itself in the icon theme
            catalog.append(CatalogEntry(label, None, as_pairs(rel_paths), appid, "applications"))

    for label, rel_paths, icon, category in CURATED_ITEMS:
        # Consumer Keep moved ordinary user folders into the dedicated
        # user-configurable Folders tab. Keep legacy "personal" curated
        # entries readable in old config files, but don't duplicate them in
        # Apps & Data anymore.
        if category == "personal":
            continue
        if any(has_meaningful_content(f"{MOUNTPOINT}/{p}") for p in rel_paths):
            catalog.append(CatalogEntry(label, None, as_pairs(rel_paths), icon, category))

    # Native app data selected through the new picker must also be discoverable
    # in older archives, independently of today's installed apps/selection.
    import applications
    covered = [source for entry in catalog for source, _target in entry.rel_paths]
    archive_home = f"{MOUNTPOINT}/{HOME_IN_ARCHIVE}"
    for entry in applications.catalog(CONFIG, archive_home):
        remaining = [f"{HOME_IN_ARCHIVE}/{p}" for p in entry["paths"]
                     if not any(f"{HOME_IN_ARCHIVE}/{p}" == old or f"{HOME_IN_ARCHIVE}/{p}".startswith(old + "/") for old in covered)]
        if remaining:
            known = applications.KNOWN.get(entry["id"])
            icon = known[2] if known else "application-x-executable"
            # One app can have both native settings and a Flatpak sandbox.
            # Combine only known identities with unchanged 1:1 path mappings;
            # cross-install conversion choices above must remain separate.
            existing = next((i for i, item in enumerate(catalog)
                             if known and item.icon == icon and item.subtitle is None
                             and item.category == "applications"), None)
            if existing is not None:
                item = catalog[existing]
                catalog[existing] = item._replace(rel_paths=item.rel_paths + as_pairs(remaining))
            else:
                catalog.append(CatalogEntry(entry["label"], entry["formats"], as_pairs(remaining), icon, entry["category"]))
            covered.extend(remaining)
    catalog.sort(key=lambda e: e.label.lower())
    return catalog


def build_folder_catalog():
    """Friendly restore tiles for the folders the user chose to back up.

    Consumer Keep no longer assumes there is one special "Projects" root.
    Each configured source is a restoreable item of its own, whether it is
    Documents, a work tree, a mounted media folder, or anything else.
    Older archives simply omit a tile when that source wasn't present yet.
    """
    catalog = []
    for entry in _consumer_module.backup_source_entries(CONFIG):
        path = entry["path"]
        rel = _consumer_module.archive_path_for_source(path)
        mounted = f"{MOUNTPOINT}/{rel}"
        if os.path.lexists(mounted) and has_meaningful_content(mounted):
            icon = "folder-documents" if path.startswith(HOME.rstrip("/") + "/") else "folder"
            catalog.append(CatalogEntry(entry["label"], path, as_pairs([rel]), icon, "folders"))
    catalog.sort(key=lambda e: e.label.lower())
    return catalog


# Legacy name kept as an alias for old tests/third-party imports. The UI now
# calls build_folder_catalog() and labels the tab "Folders".
build_project_catalog = build_folder_catalog


# display order + header text for ItemPicker's sections - "applications"
# covers cross-install apps and auto-discovered Flatpak apps (packaging
# format is deliberately NOT the top-level grouping: most of what's in this
# picker isn't an "app" at all, and forcing Documents/SSH keys into a
# Native/Flatpak split would be exposing an implementation detail as
# navigation). "projects" is the Projects tab's only category - since it's
# always the sole category present, ItemPicker renders it as one flat
# unheaded list, identical to before sections existed.
def copy_item(src, dest):
    # islink checked BEFORE isdir - isdir() follows symlinks, so a
    # symlink-to-a-directory src would otherwise be dereferenced into a
    # real directory copy at dest, losing the fact that the live thing
    # being backed up (this is also used for Direct Restore's "save what's
    # there first" undo snapshot) was a symlink at all. Found via a
    # reviewer's whole-app sweep, the same class of bug already fixed for
    # the actual replacement step in _replace_live_path(). Module-level
    # (not an ItemPicker method, despite living next to it) since the
    # Advanced tab's raw restore needs the exact same symlink-safe copy
    # and isn't an ItemPicker at all - shared here rather than duplicated
    # or reached into across a "private" underscore-prefixed method.
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.islink(src):
        os.symlink(os.readlink(src), dest)
    elif os.path.isdir(src):
        # Preserve nested links too: Electron Singleton links may point to
        # missing runtime locks or sockets outside the archived directory.
        shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest)






class ItemPicker(RestorePicker):
    """Restore operations using the reusable, backend-independent picker view."""
    def __init__(self, *args, filter_installed=False, **kwargs):
        import applications
        provider = (lambda: applications.InstalledApps(HOME)) if filter_installed else None
        super().__init__(*args, process_names=NATIVE_PROCESS_NAMES, installed_apps_provider=provider, **kwargs)

    def _checked_items(self):
        checked = self._all_items()
        if not checked:
            QMessageBox.information(self, "Keep", "Select one or more items first.")
            return checked
        # two different catalog entries can resolve to the same live path -
        # e.g. a cross-install app's native and Flatpak sources both now
        # targeting the one form that's actually installed. Restoring both
        # would make whichever copy runs second silently overwrite the
        # first with no indication - block it instead.
        seen = {}
        for item in checked:
            identity = item.data(Qt.UserRole + 1)
            for _, live_target in item.data(Qt.UserRole):
                if live_target in seen and seen[live_target] != identity:
                    QMessageBox.warning(
                        self, "Keep",
                        f"'{seen[live_target]}' and '{identity}' would both restore to "
                        f"the same live location:\n{live_target}\n\n"
                        f"Pick only one — restoring both would make whichever runs second "
                        f"silently overwrite the first.",
                    )
                    return []
                seen[live_target] = identity
        return checked

    def _replace_live_path(self, src, dest):
        """Used only by Direct Restore, where `dest` must end up an EXACT
        match for `src` - unlike copy_item() above (used by the safe-to-
        review-folder restore, where `dest` is always a brand new path
        with nothing to conflict with). Whatever currently exists at
        `dest` is removed first, handling all three cases explicitly
        (symlink / file / real directory) - a reviewer's whole-app sweep
        found the previous approach (shutil.rmtree(dest,
        ignore_errors=True), only even attempted when src was a directory)
        left a stale symlink at dest completely untouched (rmtree refuses
        to operate on a symlink at all, and ignore_errors=True swallowed
        that refusal silently), after which shutil.copytree(...,
        dirs_exist_ok=True) would merge new content into whatever the OLD
        symlink pointed at instead of actually replacing anything - files
        unique to that old target survived a restore that was supposed to
        replace them entirely. Archived symlinks are recreated as
        symlinks here too, not dereferenced into a copy of their target's
        content."""
        if os.path.islink(dest) or os.path.isfile(dest):
            os.remove(dest)
        elif os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.islink(src):
            os.symlink(os.readlink(src), dest)
        elif os.path.isdir(src):
            shutil.copytree(src, dest, symlinks=True)
        else:
            shutil.copy2(src, dest)

    def restore_checked_safe(self, dest_dir=None):
        """No destination picker needed - lands in a clearly-labelled, always-new
        review folder (or the folder Review restore… was pointed at). Never touches the live path, so there's nothing to
        confirm and nothing that can be lost by picking the wrong archive."""
        app_logging.record("operation.requested", operation="safe_restore")
        checked = self._checked_items()
        if not checked:
            return
        # extract plain data BEFORE ensure_mounted_cb() - if the mount had
        # idled out while the user was still picking items, that call
        # remounts and repopulates the sections, invalidating these
        # QListWidgetItem objects. The archive content itself doesn't change
        # on remount, so plain (identity, rel_path, live_target) tuples stay
        # valid even after the widgets underneath them get rebuilt.
        entries = [(item.data(Qt.UserRole + 1), rel_path, live_target)
                   for item in checked
                   for rel_path, live_target in item.data(Qt.UserRole)]
        if not self._ensure_mounted_cb():
            QMessageBox.warning(self, "Keep", "Could not access the archive - nothing was restored.")
            return
        dest_dir = dest_dir or new_restore_folder()
        done, failed = [], []
        for identity, rel_path, live_target in entries:
            src = f"{MOUNTPOINT}/{rel_path}"
            # lexists(), not exists() - exists() follows symlinks, so a
            # legitimately-archived symlink whose target no longer exists
            # (a broken symlink is a valid filesystem object, not an
            # error) would read as "not there" and get silently skipped
            # instead of restored as the symlink it actually is.
            if not os.path.lexists(src):
                continue
            # last TWO path segments, not just the basename - a cross-install
            # app's config/<folder> and data/<folder> share the same basename
            # ("<folder>"), which previously collapsed both into one path and
            # silently merged config and data content together
            disambiguated = live_target.strip("/").split("/")[-2:]
            dest = os.path.join(dest_dir, identity.replace("/", "_"), *disambiguated)
            try:
                copy_item(src, dest)
                done.append(f"{identity}: {dest}  (live equivalent: {live_target})")
            except Exception as e:
                failed.append(f"{identity}: {e}")
        app_logging.record("operation.finished", operation="safe_restore", result="partial_failure" if failed else "success", count=len(done), failed=len(failed))
        show_restore_results(self, done, failed, dest_dir)


    def restore_checked_direct(self):
        """Auto-targets each item's real live path and overwrites it. Backs
        up whatever's currently there first (a local undo path even for a
        "direct" restore), warns if the app looks like it's currently
        running (it could rewrite its own files on exit and undo/corrupt
        the restore), and needs an explicit confirmation either way."""
        app_logging.record("operation.requested", operation="direct_restore")
        checked = self._checked_items()
        if not checked:
            return
        # extract plain data before touching the mount - see the matching
        # comment in restore_checked_safe for why
        entries = [(item.data(Qt.UserRole + 1), rel_path, live_target, item.data(Qt.UserRole + 2))
                   for item in checked
                   for rel_path, live_target in item.data(Qt.UserRole)]
        if not self._ensure_mounted_cb():
            QMessageBox.warning(self, "Keep", "Could not access the archive - nothing was restored.")
            return
        targets = []
        running_apps = set()
        for identity, rel_path, live_target, native_process_names in entries:
            # lexists(), not exists() - same reasoning as restore_checked_safe():
            # a broken symlink is a legitimate archived object, not absence
            if os.path.lexists(f"{MOUNTPOINT}/{rel_path}"):
                targets.append((identity, rel_path, live_target))
                appid = flatpak_appid_from_path(live_target)
                if appid and is_flatpak_running(appid):
                    running_apps.add(appid)
                elif native_process_names and is_native_process_running(native_process_names):
                    # no appid for a native process - the identity (e.g.
                    # "digiKam Photo Library") is the only name we have for it
                    running_apps.add(identity)
        if not targets:
            return

        listing = "\n".join(f"  {live}" for _, _, live in targets)
        msg = (
            f"This replaces the CURRENT content at these {len(targets)} path(s) with the "
            f"backed-up version:\n\n{listing}\n\n"
            f"Whatever's currently there is saved first to a Before-Direct-Restore "
            f"folder, so this has a local undo path — but the app itself won't know "
            f"its files changed underneath it."
        )
        if running_apps:
            msg += (
                f"\n\n⚠ Currently RUNNING: {', '.join(sorted(running_apps))}\n"
                f"If it's still open when it exits, it may rewrite these files itself "
                f"and undo or corrupt the restore. Close it first."
            )
        confirmed = QMessageBox.warning(
            self, "Keep — this will overwrite live data", msg,
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if confirmed != QMessageBox.Yes:
            return

        # the confirmation dialog above is modal but doesn't pause the
        # inactivity timer, so a slow "are you sure?" click could still let
        # the mount idle out before we get here - re-ensure it
        if not self._ensure_mounted_cb():
            QMessageBox.warning(self, "Keep", "Could not access the archive - nothing was restored, and nothing live was touched.")
            return

        before_dir = os.path.join(HOME, "Keep-Restored", "Before-Direct-Restore", datetime.now().strftime("%Y%m%d-%H%M%S"))
        done, failed = [], []
        for identity, rel_path, live_path in targets:
            src = f"{MOUNTPOINT}/{rel_path}"
            try:
                if os.path.lexists(live_path):
                    backup_dest = os.path.join(before_dir, identity.replace("/", "_"), *live_path.strip("/").split("/")[-2:])
                    copy_item(live_path, backup_dest)
                self._replace_live_path(src, live_path)
                done.append(live_path)
            except Exception as e:
                failed.append(f"{live_path}: {e}")
        app_logging.record("operation.finished", operation="direct_restore", result="partial_failure" if failed else "success", count=len(done), failed=len(failed))
        show_restore_results(self, done, failed, before_dir, direct=True)



# borg --progress writes size/count updates using \r to overwrite the same
# terminal line; redirected to a file (as the backup script does), each \r
# still lands as a real newline-separated line once a boundary is hit, but
# everything since the previous boundary is joined by \r rather than \n. The
# last \r-segment before that newline is the clean, final state - matches
# "<size> O <size> C <size> D <n> N <path>", distinct enough from any real
# log message that it won't be mistaken for one.
PROGRESS_RE = re.compile(r'\bO\s.*\bC\s.*\bD\s\d+\sN\s')


def collapse_progress_output(new_text, buf):
    """Split newly-read log text into (real log lines, latest progress line, leftover partial line)."""
    text = buf + new_text
    lines = text.split("\n")
    new_buf = lines.pop()  # last element may not be newline-terminated yet
    log_lines = []
    latest_progress = None
    for line in lines:
        collapsed = (line.split("\r")[-1] if "\r" in line else line).strip()
        if not collapsed:
            continue
        if PROGRESS_RE.search(collapsed):
            latest_progress = collapsed
        else:
            log_lines.append(collapsed)
    return log_lines, latest_progress, new_buf


# the script logs this once, right before "running borg create", after a
# `borg create --dry-run --list` pass sums the size of everything that would
# actually be included (respecting the same --exclude-from as the real run,
# since it's borg's own inclusion decision per path, not a hand-rolled
# reimplementation of its pattern matching) - this is the denominator for a
# real percentage instead of a fabricated one
PRESCAN_RE = re.compile(r'prescan complete: (\d+) bytes to back up')
KEEP_PROGRESS_RE = re.compile(r'backup progress: (\d+)% \(\d+ of \d+ bytes\)')

# confirmed empirically (not assumed): borg's --progress/--stats output uses
# decimal SI units (1 MB = 1000*1000 bytes), not IEC/binary ones, e.g. a file
# of EXACTLY 5,000,000 bytes is reported as "5.00 MB"
BORG_SIZE_UNITS = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
# anchored to the START of the line and requires " O " right after - the
# progress line repeats size+unit three times (O, C, D), this captures only
# the first (original bytes processed), which is the only one that tracks
# traversal progress; C and D can go nonsensical on incremental backups (a
# 500GB scan that dedups down to 800MB new data would make a D-based percent
# meaningless)
PROGRESS_O_RE = re.compile(r'^([\d.]+)\s*(TB|GB|MB|kB|B)\s+O\s')


def parse_borg_original_bytes(progress_line):
    """Extract just the Original-bytes-processed figure from a collapsed
    `--progress` line, or None if it doesn't match the expected format."""
    m = PROGRESS_O_RE.match(progress_line)
    if not m:
        return None
    return float(m.group(1)) * BORG_SIZE_UNITS[m.group(2)]


# the current file path is everything after the "D <count> N " marker in a
# collapsed --progress line - mirrors PROGRESS_RE's own already-verified
# tail (`\bD\s\d+\sN\s`) exactly: the file-COUNT integer comes BEFORE the
# "N" marker, not after it (found and fixed via an actual render - an
# earlier version of this regex had the digit and "N" reversed, which
# never matched anything real and left the stage line silently blank
# during the create phase instead of raising - the exact kind of mistake
# only rendering catches, not reading the regex). Used for the human
# "Backing up: <short path>" status line, not the percentage (that's still
# parse_borg_original_bytes above).
CURRENT_PATH_RE = re.compile(r'\bD\s\d+\sN\s+(.+)$')

# Plain, non-regex phase markers emitted by both the built-in engine and legacy
# external scripts. Built-in backups use a separate sanitized percentage line
# during create; legacy scripts may still expose Borg's raw progress format.
STAGE_MARKERS = [
    ("scanning what will be backed up", "Preparing backup…"),
    ("running borg create", "Backing up…"),
    ("running borg prune", "Cleaning up old backups…"),
    ("running borg compact", "Reclaiming space…"),
]


def short_path(path, segments=2):
    """The final 1-2 path components, for a compact 'Backing up: X' status
    line - the full path stays available as a tooltip (see
    MainWindow._on_backup_progress), not shown inline."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return path
    kept = parts[-segments:]
    shortened = "/".join(kept)
    return f".../{shortened}" if len(kept) < len(parts) else shortened


class BackupWorker(QThread):
    output = Signal(str)
    progress = Signal(str)
    percent_progress = Signal(int)  # sanitized built-in-engine progress
    stage = Signal(str)  # human-readable current phase - see STAGE_MARKERS
    prescan_total = Signal("qint64")  # Byte counts exceed Qt's 32-bit int above 2 GiB.
    finished_ok = Signal(bool)

    def __init__(self):
        super().__init__()
        self.proc = None
        self.user_stopped = False

    def stop(self):
        """Kill the whole process tree, not just the wrapper script - the
        script runs `borg create` as a plain (non-exec'd) child, so sending
        SIGTERM to just the script's own PID leaves borg running orphaned.
        start_new_session=True in run() makes the script its own process
        group leader so killpg reaches both it and borg together."""
        self.user_stopped = True
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run(self):
        # the script redirects its own stdout/stderr straight into a log file
        # (needed for the unattended systemd-timer runs, so there's nothing on
        # this process's own stdout to read) - instead, poll that log file's
        # growing content while the script runs, so progress shows up live
        # stage-by-stage ("running borg create" -> "running borg prune" -> ...)
        # rather than dumping everything only once the whole thing is done.
        #
        # The whole body is wrapped so finished_ok ALWAYS fires exactly
        # once - without this, an unexpected exception (e.g. the script
        # itself missing/not executable) would leave _repo_op_running
        # stuck True and the UI permanently "busy" forever. Found via a
        # reviewer's whole-app sweep.
        try:
            before = set(glob.glob(f"{LOGDIR}/backup-*.log"))
            # Pass the session-only passphrase through when there is one
            # (e.g. an Unlock where "Remember" was deliberately left off) -
            # otherwise a manual Backup Now would silently fail with the
            # same stale on-disk passphrase, even though Keep itself can
            # browse fine this session. Purely additive: the script only
            # honors a pre-set BORG_PASSPHRASE at all as of this same fix,
            # and the unattended systemd-timer run never sets this, so it
            # still always reads the file exactly as before. This does NOT
            # fix the SCHEDULED run - that has no session to inherit from
            # and always needs the passphrase actually persisted to disk;
            # Change Destination already warns clearly when that's not the
            # case. Found via a reviewer's whole-app sweep.
            backup_env = os.environ.copy()
            if _session_passphrase_override:
                backup_env["BORG_PASSPHRASE"] = _session_passphrase_override
            proc = subprocess.Popen(
                effective_backup_command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                stdin=subprocess.DEVNULL, start_new_session=True, env=backup_env,
            )
            self.proc = proc
            if self.user_stopped:
                # stop() was called in the narrow window between this
                # thread starting and self.proc being assigned - it could
                # only record the request back then (nothing existed yet
                # to kill), so honor it now instead of letting the backup
                # run to completion unattended.
                self.stop()

            log_path = None
            sent = 0
            buf = ""

            def pump():
                nonlocal sent, buf
                if not (log_path and os.path.exists(log_path)):
                    return
                text = Path(log_path).read_text()
                if len(text) <= sent:
                    return
                log_lines, latest_progress, buf = collapse_progress_output(text[sent:], buf)
                sent = len(text)
                if log_lines:
                    visible_lines = []
                    for line in log_lines:
                        progress_match = KEEP_PROGRESS_RE.search(line)
                        if progress_match:
                            self.percent_progress.emit(int(progress_match.group(1)))
                            continue
                        visible_lines.append(line)
                        m = PRESCAN_RE.search(line)
                        if m:
                            self.prescan_total.emit(int(m.group(1)))
                        for marker, stage_text in STAGE_MARKERS:
                            if marker in line:
                                self.stage.emit(stage_text)
                                break
                    if visible_lines:
                        self.output.emit("\n".join(visible_lines))
                if latest_progress:
                    self.progress.emit(latest_progress)

            while proc.poll() is None:
                if log_path is None:
                    after = set(glob.glob(f"{LOGDIR}/backup-*.log")) - before
                    if after:
                        log_path = max(after, key=os.path.getctime)
                pump()
                self.msleep(500)

            pump()  # catch anything written between the last poll and the process exiting
            if buf.strip():
                collapsed = buf.split("\r")[-1].strip() if "\r" in buf else buf.strip()
                if collapsed and not PROGRESS_RE.search(collapsed):
                    self.output.emit(collapsed)
            leftover = proc.stdout.read() if proc.stdout else ""
            if leftover.strip():
                self.output.emit(leftover)
            if self.user_stopped:
                if log_path is None:
                    # an extremely fast Stop - killed before the 500ms
                    # polling loop above ever got a chance to notice the
                    # log file the script had already created. The
                    # script's own mkdir/exec-redirect happens before any
                    # of the slower steps (destination check, passphrase
                    # read, manifest snapshot) that would otherwise give
                    # the polling loop time to catch up - one final direct
                    # check rather than assuming there's nothing to mark,
                    # since without this the next status refresh would see
                    # a log with neither the success line nor this marker
                    # and report FAILED for what was actually a deliberate
                    # cancel (reviewer-flagged, the same bug this marker
                    # exists to prevent, just on a faster timing window).
                    after = set(glob.glob(f"{LOGDIR}/backup-*.log")) - before
                    if after:
                        log_path = max(after, key=os.path.getctime)
                if log_path:
                    # the ONLY way last_backup_attempt_status() (a later, separate
                    # read of this same file - possibly from a future launch of
                    # Keep entirely) can tell "user cancelled this" apart from
                    # "this died on its own" - written only now, after the
                    # process has actually exited, so it can't race the script's
                    # own writes to the same file
                    try:
                        with open(log_path, "a") as f:
                            f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} STOPPED BY USER\n")
                    except OSError:
                        pass
            self.finished_ok.emit(proc.returncode == 0)
        except Exception as e:
            app_logging.record("worker.error", operation="backup", error_type=type(e).__name__)
            self.output.emit(f"Backup failed unexpectedly: {e}")
            self.finished_ok.emit(False)


# both borg delete and borg compact report an actual percentage of their own
# via --progress (confirmed empirically: "Decrementing references  NN%" /
# "Compacting segments  NN%") - unlike borg create, no pre-scan/estimate
# needed here, just read it directly.
DELETE_COMPACT_PERCENT_RE = re.compile(r'(?:Decrementing references|Compacting segments)\s+(\d+)%')


class DeleteWorker(QThread):
    """Runs `borg delete` then `borg compact` (delete alone doesn't reclaim
    space) in the background - both used to run as blocking subprocess.run()
    calls directly on the UI thread, freezing the whole window for however
    long compact took (it physically repacks segment files, can be slow on
    a large/fragmented repo) with no feedback at all."""

    output = Signal(str)
    finished_ok = Signal(bool, str)

    def __init__(self, repo, archive):
        super().__init__()
        self.repo = repo
        self.archive = archive

    def run(self):
        # The whole body is wrapped so finished_ok ALWAYS fires exactly
        # once - without this, an unexpected exception here (e.g. `borg`
        # not found, a permissions error from Popen itself) would leave
        # _repo_op_running stuck True and the UI permanently "busy"
        # forever, since _on_delete_finished (the only place that clears
        # it) would never run. Found via a reviewer's whole-app sweep.
        try:
            with RepositoryLock(self.repo):
                self.output.emit("Deleting archive...")
                proc = subprocess.Popen(
                    ["borg", "delete", "--progress", "--lock-wait", "60", f"{self.repo}::{self.archive}"],
                    env=borg_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                for raw_line in iter(proc.stdout.readline, ""):
                    m = DELETE_COMPACT_PERCENT_RE.search(raw_line)
                    if m:
                        self.output.emit(f"Deleting archive... {m.group(1)}%")
                proc.wait()
                if proc.returncode != 0:
                    self.finished_ok.emit(False, f"Delete failed (exit code {proc.returncode})")
                    return

                self.output.emit("Compacting repository (reclaiming space)...")
                proc = subprocess.Popen(
                    ["borg", "compact", "--progress", "--lock-wait", "60", self.repo],
                    env=borg_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                for raw_line in iter(proc.stdout.readline, ""):
                    m = DELETE_COMPACT_PERCENT_RE.search(raw_line)
                    if m:
                        self.output.emit(f"Compacting repository... {m.group(1)}%")
                proc.wait()
                if proc.returncode != 0:
                    self.finished_ok.emit(False, f"Archive deleted, but compact failed (exit code {proc.returncode}) - space wasn't reclaimed, but nothing is broken; try compacting again later")
                    return
                self.finished_ok.emit(True, "")
        except Exception as e:
            self.finished_ok.emit(False, f"Delete/compact failed unexpectedly: {e}")


class StatusQueryWorker(QThread):
    """Runs the two read-only `borg info`/`borg list` calls the dashboard
    needs off the UI thread - these used to run synchronously inside
    refresh_status()/refresh_archives(), each with its own internal 30s
    subprocess timeout, so a genuinely slow/unresponsive destination (a bad
    NAS - the actual motivating case) could freeze the whole window for up
    to a minute. One worker fetches BOTH results in one pass so a dashboard
    refresh queries Borg once, not twice (an earlier version had
    refresh_status() and refresh_archives() each independently listing the
    same repo) - the two results travel together in finished_result.

    Deliberately does NOT reuse run_borg_json() here, even though every
    other read-only caller in this file should keep using it - that helper
    calls subprocess.run(timeout=30), which blocks uninterruptibly until it
    either completes or hits the timeout. A worker built on it can't
    actually be cancelled: closing Keep or changing destination while it's
    stuck could mean a QThread hanging around for up to ~60s (two calls),
    either freezing shutdown or leaving something running that no longer
    matters. This worker keeps its own Popen handle instead (same pattern
    BackupWorker.stop() already uses for the real backup script) so stop()
    can terminate() whatever's actually in flight - self.cancelled makes a
    stop() that lands between the two calls skip the second one too,
    instead of dutifully running a query nobody wants anymore.

    Also deliberately does NOT use run_borg_json_checked()/the key-recovery
    dialog chain - a passive background dashboard refresh must never itself
    pop a passphrase/key recovery prompt. That chain stays reachable only
    from explicit browse intent (ensure_mounted()/
    _ensure_archives_available()), which already does its own synchronous
    checked-with-recovery fetch and applies the result directly via
    _apply_archive_listing() rather than going through this worker at all -
    see that method's docstring."""

    finished_result = Signal(str, object, object, bool)  # repo, info (dict|None), listing (dict|None), cancelled

    def __init__(self, repo, timeout=30):
        super().__init__()
        self.repo = repo
        self.timeout = timeout  # overridable so a test can force a real, fast timeout rather than waiting out a real 30s one
        self.proc = None
        self.cancelled = False

    def _kill_proc_group(self, sig):
        """Signals the WHOLE process group self.proc leads (see the
        start_new_session=True in _query below), not just that one PID -
        same reasoning and pattern as BackupWorker.stop(): a plain
        self.proc.terminate() only reaches the immediate child. `borg`
        itself is normally launched directly with nothing wrapping it, but
        this is what actually guarantees no descendant is ever left running
        regardless, rather than relying on that always holding true. Guards
        ProcessLookupError - the group may already be gone by the time this
        runs (a real, not just theoretical, race: found via this exact
        method flaking in testing when the target was a short-lived wrapped
        script, not a bare `borg` process)."""
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), sig)
        except ProcessLookupError:
            pass

    def stop(self):
        """Best-effort, fire-and-forget cancellation - called from the main
        thread while run() may be blocked inside communicate() on a
        different thread. Terminating the subprocess makes communicate()
        return promptly instead of waiting out its full timeout."""
        self.cancelled = True
        self._kill_proc_group(signal.SIGTERM)

    def _query(self, args):
        if self.cancelled:
            return None
        try:
            self.proc = subprocess.Popen(
                ["borg"] + args, env=borg_env(), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True,
            )
            out, _ = self.proc.communicate(timeout=self.timeout)
            if self.proc.returncode != 0:
                return None
            return json.loads(out)
        except subprocess.TimeoutExpired:
            # communicate()'s own timeout does NOT kill the child process -
            # that's Python's own documented behavior, not an oversight
            # here: "the child process is not killed if the timeout
            # expires, so in order to cleanup properly a well-behaved
            # application should kill the child process". Skipping this
            # would leave a genuinely stuck Borg process running,
            # unreferenced (the finally below clears self.proc
            # unconditionally) and untrackable - exactly the orphaned-
            # process problem this whole worker exists to avoid, just
            # relocated from "can't cancel" to "cancelled the wrong way".
            self._kill_proc_group(signal.SIGTERM)
            try:
                self.proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                self._kill_proc_group(signal.SIGKILL)
                self.proc.communicate()
            return None
        except Exception:
            return None
        finally:
            self.proc = None

    def run(self):
        # Wrapped so finished_result ALWAYS fires exactly once, same
        # discipline as every other worker in this file.
        try:
            info = self._query(["info", "--json", self.repo])
            listing = None if self.cancelled else self._query(["list", "--json", self.repo])
            self.finished_result.emit(self.repo, info, listing, self.cancelled)
        except Exception:
            self.finished_result.emit(self.repo, None, None, self.cancelled)


# borg diff emits one JSON object per line via --json-lines, each shaped
# like {"path": ..., "changes": [{"type": "modified"/"added"/"removed"/
# "ctime"/"mtime", ...}]}. Confirmed empirically against a real scratch repo
# rather than assumed: a changed directory shows up with ONLY ctime/mtime
# entries (its mtime naturally moves when something inside it changes) -
# with no real content change of its own. Filtering to just these three
# types is what keeps that timestamp noise out of the results.
DIFF_CONTENT_CHANGE_TYPES = ("added", "removed", "modified")


def format_diff_entry(path, changes):
    parts = []
    for c in changes:
        t = c.get("type")
        if t == "added":
            parts.append(f"+ added ({c.get('size', 0):,} B)")
        elif t == "removed":
            parts.append(f"- removed ({c.get('size', 0):,} B)")
        elif t == "modified":
            parts.append(f"~ modified (+{c.get('added', 0):,} B / -{c.get('removed', 0):,} B)")
    return f"{path}   {', '.join(parts)}"


class DiffWorker(QThread):
    """Runs `borg diff --json-lines` in the background. Reads both archives
    directly - no mount needed - but can still take real time on a large
    repo, so (like every other borg subprocess in this app) it never runs
    on the UI thread."""

    finished_result = Signal(bool, list, str)  # ok, entries ([(path, changes)]), error

    def __init__(self, repo, archive_a, archive_b):
        super().__init__()
        self.repo = repo
        self.archive_a = archive_a
        self.archive_b = archive_b

    def run(self):
        try:
            result = subprocess.run(
                ["borg", "diff", "--json-lines", "--lock-wait", "60",
                 f"{self.repo}::{self.archive_a}", self.archive_b],
                env=borg_env(), capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=300,
            )
        except subprocess.TimeoutExpired:
            self.finished_result.emit(False, [], "Timed out comparing archives.")
            return
        except Exception as e:
            self.finished_result.emit(False, [], f"Compare failed unexpectedly: {e}")
            return
        if result.returncode != 0:
            self.finished_result.emit(False, [], result.stderr.strip() or "borg diff failed.")
            return
        entries = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            content_changes = [c for c in obj.get("changes", []) if c.get("type") in DIFF_CONTENT_CHANGE_TYPES]
            if not content_changes:
                continue
            entries.append((obj.get("path", "?"), content_changes))
        self.finished_result.emit(True, entries, "")


class PaperKeyImportWorker(QThread):
    """Runs import_paper_key() in the background - it's an interactive
    subprocess with per-line read timeouts, so worst case (something hangs)
    it could take a while; running it on the UI thread would freeze the
    whole window for that whole time, the exact class of bug already fixed
    once this session for delete/compact."""

    finished_result = Signal(bool, str)

    def __init__(self, repo, pasted_text):
        super().__init__()
        self.repo = repo
        self.pasted_text = pasted_text

    def run(self):
        # finished_result must ALWAYS fire exactly once, even if
        # import_paper_key() itself raises (e.g. borg genuinely missing) -
        # otherwise ok/msg are never assigned, the emit below never
        # happens, and the wizard's Validate button/text field stay
        # disabled forever with no way to tell what went wrong. Found via
        # a reviewer's whole-app sweep - the same class of gap already
        # fixed for DeleteWorker/BackupWorker, missed here.
        try:
            try:
                ok, msg = import_paper_key(self.repo, self.pasted_text)
            finally:
                # Not real secure erasure (Python strings aren't built for
                # that), but there's no reason for this object to keep holding
                # the whole paper key once import_paper_key() is done with it.
                self.pasted_text = ""
        except Exception as e:
            self.finished_result.emit(False, f"Paper key import failed unexpectedly: {e}")
            return
        self.finished_result.emit(ok, msg or "")


class UnlockBackupDialog(QDialog):
    """Shown when Borg finds a key file for the repository but the
    passphrase on record doesn't open it - e.g. restoring on a machine
    other than the one that normally holds ~/.config/borg/passphrase, or
    that file having gone stale. Distinct from KeyRecoveryDialog, which is
    for when there's no key file at all - see classify_borg_auth_error().
    Never re-displays a passphrase once entered, and clears its own input
    field as soon as a value has been read out of it."""

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keep — Unlock Backup")
        self.resize(420, 0)
        self.passphrase = None
        self.remember = False
        layout = QVBoxLayout(self)

        intro = QLabel(
            f"Keep found a key for “{label}”, but the passphrase it has "
            f"on record doesn't unlock it. Enter the correct passphrase to continue."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.passphrase_edit = QLineEdit()
        self.passphrase_edit.setEchoMode(QLineEdit.Password)
        self.passphrase_edit.setPlaceholderText("Passphrase")
        self.passphrase_edit.returnPressed.connect(self._submit)
        layout.addWidget(self.passphrase_edit)

        self.chk_remember = QCheckBox("Remember this passphrase on this computer")
        self.chk_remember.setChecked(False)
        layout.addWidget(self.chk_remember)

        self.lbl_error = QLabel("")
        self.lbl_error.setWordWrap(True)
        self.lbl_error.hide()
        layout.addWidget(self.lbl_error)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        self.btn_unlock = QPushButton("Unlock")
        theming.role(self.btn_unlock, "primary")
        self.btn_unlock.setDefault(True)
        self.btn_unlock.clicked.connect(self._submit)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_unlock)
        layout.addLayout(btn_row)

    def show_error(self, message):
        """Called by the caller after re-checking a submitted passphrase
        and finding it still doesn't work - keeps the dialog open for
        another attempt rather than the caller having to build a second
        dialog instance."""
        theming.role(self.lbl_error, "error")
        self.lbl_error.setText(message)
        self.lbl_error.show()
        self.passphrase_edit.clear()
        self.passphrase_edit.setFocus()

    def _submit(self):
        text = self.passphrase_edit.text()
        if not text:
            return
        self.passphrase = text
        self.remember = self.chk_remember.isChecked()
        self.passphrase_edit.clear()
        self.accept()


class KeyRecoveryDialog(QDialog):
    """Shown when Borg can't find ANY local key for the repository - the
    disaster-recovery case, distinct from UnlockBackupDialog (where a key
    file exists but the passphrase is wrong). Expected the first time
    you're restoring onto a machine that never made this backup itself -
    you'll need whichever key material was saved when the repo was first
    set up (see KeyExportDialog)."""

    IMPORT_FILE = "import_file"
    PAPER_KEY = "paper_key"

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keep — Backup Key Not Found")
        self.choice = None
        layout = QVBoxLayout(self)

        intro = QLabel(
            f"Keep can't find a decryption key for “{label}” on this "
            f"computer. This is expected the first time you restore from this "
            f"backup on a new machine - pick whichever key material you saved "
            f"when it was first set up."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        btn_import = QPushButton("Import Key File...")
        btn_import.clicked.connect(lambda: self._choose(self.IMPORT_FILE))
        layout.addWidget(btn_import)

        btn_paper = QPushButton("Recover from Paper Key")
        btn_paper.clicked.connect(lambda: self._choose(self.PAPER_KEY))
        layout.addWidget(btn_paper)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        layout.addWidget(btn_cancel)

    def _choose(self, choice):
        self.choice = choice
        self.accept()


class PaperKeyWizardDialog(QDialog):
    """Disaster-recovery path for reconstructing a key from the printed
    paper key saved at repo-init time (KeyExportDialog), when there's no
    key file at all to unlock (KeyRecoveryDialog). Runs the actual import
    on a background thread (PaperKeyImportWorker) since it drives an
    interactive `borg key import --paper` subprocess line-by-line and can
    take a little while - the same reason DeleteWorker exists rather than
    calling borg directly on the UI thread. The pasted key is only cleared
    from the text box once it's been successfully consumed (on failure,
    Borg's own re-prompt is for the SAME line, so keeping the rest visible
    to fix a typo and resubmit is actually the more usable behavior)."""

    def __init__(self, repo, label, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.setWindowTitle("Keep — Recover From Paper Key")
        self.resize(520, 420)
        self.recovered = False
        self._worker = None
        layout = QVBoxLayout(self)

        intro = QLabel(
            f"Paste the paper key saved for “{label}” below, exactly as "
            f"printed - one “id:”/“N:” line per row. Keep checks each "
            f"line's checksum as it goes and will say exactly which line is wrong, if any are."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("id: ...\n 1: ...\n 2: ...\n...")
        layout.addWidget(self.text_edit, stretch=1)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_validate = QPushButton("Validate Key")
        theming.role(self.btn_validate, "primary")
        self.btn_validate.setDefault(True)
        self.btn_validate.clicked.connect(self._start)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_validate)
        layout.addLayout(btn_row)

    def reject(self):
        # Refuse to close while the worker is actually running a real borg
        # subprocess - letting the dialog (and Python's only reference to
        # the QThread) disappear out from under a still-running thread is
        # exactly the "QThread: Destroyed while thread is still running"
        # crash class. btn_cancel is disabled for the same reason, but this
        # also covers Escape and the window's own close button. Import is
        # short, so there's nothing useful to offer besides "wait a moment."
        if self._worker is not None and self._worker.isRunning():
            return
        self.text_edit.clear()
        super().reject()

    def _start(self):
        pasted = self.text_edit.toPlainText()
        if not pasted.strip():
            return
        self.text_edit.setReadOnly(True)
        self.btn_validate.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setStyleSheet("")
        self.lbl_status.setText("Checking key...")
        self._worker = PaperKeyImportWorker(self.repo, pasted)
        self._worker.finished_result.connect(self._on_result)
        self._worker.start()

    def _on_result(self, ok, message):
        self.btn_validate.setEnabled(True)
        self.btn_cancel.setEnabled(True)
        self.text_edit.setReadOnly(False)
        if ok:
            self.text_edit.clear()
            self.recovered = True
            self.accept()
        else:
            theming.role(self.lbl_status, "error")
            self.lbl_status.setText(message or "That key couldn't be imported.")


class KeyExportDialog(QDialog):
    """Shown once, right after a new repo is initialized. Replaces the old
    'go run these 3 commands in a terminal yourself' instruction with the
    same values in-app - both fields are read-only + selectable either way,
    so this doesn't introduce any new exposure that manually running `cat`
    in a terminal didn't already have (that output is also just as
    copyable). No sudo/elevation involved anywhere here - it's all plain
    reads/writes on files the user already owns in their own home dir."""

    def __init__(self, passphrase, export_path, parent=None):
        super().__init__(parent)
        self.export_path = export_path
        # export_path can point at a file that doesn't actually exist - the
        # paper-key export a moment earlier can itself fail (network blip,
        # disk full), and the repo/passphrase are still perfectly usable
        # without it (just no off-machine copy of the key yet), so this
        # dialog must still open safely rather than crash trying to read a
        # file that was never written. Found via a reviewer's whole-app
        # sweep: the old code unconditionally did Path(export_path).read_text()
        # here with no existence check at all.
        self._paper_key_available = bool(export_path) and os.path.isfile(export_path)
        self.setWindowTitle("Keep — Save Your New Repository Key")
        self.resize(600, 440)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Copy BOTH of these into your password manager (e.g. Bitwarden) now, "
            "and keep a printed copy somewhere safe away from this computer. The "
            "passphrase is required to open this backup — without it, the backup "
            "is permanently unreadable, even with NAS access. The paper key is a "
            "spare copy of the encryption key (the repository stores one too), "
            "for if the repository's copy is ever damaged."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(QLabel("Passphrase:"))
        pass_row = QHBoxLayout()
        self.passphrase_edit = QLineEdit(passphrase)
        self.passphrase_edit.setReadOnly(True)
        pass_row.addWidget(self.passphrase_edit, stretch=1)
        btn_copy_pass = QPushButton("Copy")
        btn_copy_pass.clicked.connect(lambda: self._copy(passphrase, "passphrase", btn_copy_pass))
        pass_row.addWidget(btn_copy_pass)
        layout.addLayout(pass_row)

        self._copied = {"passphrase": False}
        self.key_edit = None

        if self._paper_key_available:
            layout.addWidget(QLabel("Paper key (also written to a temporary file on disk for now). "
                                    "Its last line is shorter than the others — that's normal, not truncated:"))
            self.key_edit = QPlainTextEdit(Path(export_path).read_text())
            self.key_edit.setReadOnly(True)
            layout.addWidget(self.key_edit, stretch=1)
            btn_copy_key = QPushButton("Copy Paper Key")
            btn_copy_key.clicked.connect(lambda: self._copy(self.key_edit.toPlainText(), "paper key", btn_copy_key))
            layout.addWidget(btn_copy_key)
            self._copied["paper key"] = False
        else:
            missing_note = QLabel(
                "Paper key export failed earlier, so there's nothing to show here - "
                "the key itself is still stored inside the repository, you just "
                "don't have a spare copy of it yet. See the message shown just before this one for the "
                "exact command to export it yourself once you're ready."
            )
            missing_note.setWordWrap(True)
            layout.addWidget(missing_note)

        btn_row = QHBoxLayout()
        self.btn_done = QPushButton(
            "I've Saved Both — Delete the Temporary File" if self._paper_key_available
            else "I've Saved the Passphrase"
        )
        theming.role(self.btn_done, "primary")
        self.btn_done.setEnabled(False)
        self.btn_done.clicked.connect(self._finish)
        btn_not_now = QPushButton("Not Now")
        btn_not_now.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_done)
        btn_row.addWidget(btn_not_now)
        layout.addLayout(btn_row)

        if self._paper_key_available:
            note = QLabel(f"If you'd rather do this yourself later: {export_path}")
            note.setWordWrap(True)
            layout.addWidget(note)

    def _copy(self, text, which, button):
        QApplication.clipboard().setText(text)
        self._copied[which] = True
        button.setText(f"Copied ✓")
        if all(self._copied.values()):
            self.btn_done.setEnabled(True)

    def _finish(self):
        if not self._paper_key_available:
            # nothing was ever written to disk for this one - just confirm
            # the passphrase itself is saved and close, no file to shred
            self.accept()
            return
        confirmed = QMessageBox.warning(
            self, "Keep",
            "This permanently deletes the temporary export file on disk. Make sure "
            "both values are actually saved in your password manager first — this "
            "cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if confirmed != QMessageBox.Yes:
            return
        result = subprocess.run(["shred", "-u", self.export_path], capture_output=True, text=True)
        if result.returncode != 0:
            QMessageBox.warning(self, "Keep", f"Could not delete the temporary file:\n{result.stderr}\n\nYou can remove it yourself later:\nshred -u {self.export_path}")
        else:
            QMessageBox.information(self, "Keep", "Temporary file deleted.")
        self.accept()


class DestinationTypeDialog(QDialog):
    """First step of Change Destination - which kind of place is this?
    Determines whether the destination gets tracked by mount path (network/
    other) or by filesystem UUID (removable) - a USB drive doesn't reliably
    get the same mount path every time it's plugged back in, so tracking it
    by the drive's own identity instead means Keep still finds it correctly
    even if that changes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keep — Backup Destination Type")
        self.chosen = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("What kind of place is this backup going to?"))
        for type_key, label, hint in [
            ("network", "Network Storage", "Always-on network share (NAS, mapped network drive) - tracked by its mount path."),
            ("removable", "Removable / USB Drive", "External drive that gets connected and disconnected - tracked by the drive itself, so a different mount path next time still works."),
            ("other", "Other Location", "Any other local or already-mounted folder."),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked=False, t=type_key: self._choose(t))
            layout.addWidget(btn)
            hint_lbl = QLabel(hint)
            hint_lbl.setWordWrap(True)
            theming.role(hint_lbl, "secondary")
            layout.addWidget(hint_lbl)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        layout.addWidget(btn_cancel)

    def _choose(self, type_key):
        self.chosen = type_key
        self.accept()


class BackupSourcesDialog(QDialog):
    """Consumer-facing replacement for the old single project_dir setting.

    The list is intentionally ordinary folders, not Borg paths. Existing
    installations may keep a custom external backup script; fresh installs
    use Keep's bundled engine, which consumes these selections directly.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keep — What to Back Up")
        self.resize(620, 500)
        self.sources = _consumer_module.backup_source_entries(CONFIG)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Choose the folders you want protected. You can add folders from your home "
            "directory, another internal drive, or any mounted location."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        btn_add = QPushButton("Add Folder…")
        btn_add.clicked.connect(self._add_folder)
        row.addWidget(btn_add)
        btn_rename = QPushButton("Rename")
        btn_rename.clicked.connect(self._rename)
        row.addWidget(btn_rename)
        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._remove)
        row.addWidget(btn_remove)
        row.addStretch()
        layout.addLayout(row)

        self.cb_app_data = QCheckBox("Back up application data and settings")
        self.cb_app_data.setChecked(bool(CONFIG.get("include_app_data", True)))
        self.cb_app_data.setToolTip(
            "Includes application settings/data such as native XDG data and Flatpak sandboxes. "
            "This is what makes app recovery useful even after reinstalling Linux."
        )
        layout.addWidget(self.cb_app_data)

        # Compatibility choice is only meaningful on an upgraded install
        # that actually HAS an external script to fall back to. Fresh consumer
        # installs always use the bundled engine; offering an "external" mode
        # with no script would be a control that cannot do what it says.
        self.cb_managed = None
        if BACKUP_SCRIPT:
            self.cb_managed = QCheckBox("Use Keep's built-in backup engine (recommended)")
            self.cb_managed.setChecked(CONFIG.get("backup_engine") != "external")
            self.cb_managed.setToolTip(
                "Turn this on to make the folder list above define the real backup scope. "
                "Leave it off to keep using your existing custom backup script."
            )
            layout.addWidget(self.cb_managed)

        note = QLabel(
            "Keep keeps native and Flatpak application data as separate archived data when both "
            "exist. Restore still targets the appropriate installation safely."
        )
        note.setWordWrap(True)
        theming.role(note, "secondary")
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("Save")
        theming.role(save, "primary")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self._refresh()

    def _refresh(self):
        self.list.clear()
        for entry in self.sources:
            item = QListWidgetItem(f"{entry['label']}\n{entry['path']}")
            item.setData(Qt.UserRole, entry["path"])
            self.list.addItem(item)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to back up")
        if not folder:
            return
        folder = os.path.abspath(folder)
        if any(e["path"] == folder for e in self.sources):
            QMessageBox.information(self, "Keep", "That folder is already in the backup list.")
            return
        default = os.path.basename(folder.rstrip("/")) or folder
        label, ok = QInputDialog.getText(self, "Keep", "Name shown in Keep:", text=default)
        if not ok:
            return
        self.sources.append({"label": label.strip() or default, "path": folder})
        self._refresh()
        self.list.setCurrentRow(self.list.count() - 1)

    def _rename(self):
        row = self.list.currentRow()
        if row < 0:
            return
        current = self.sources[row]
        label, ok = QInputDialog.getText(self, "Keep", "Name shown in Keep:", text=current["label"])
        if ok and label.strip():
            current["label"] = label.strip()
            self._refresh()
            self.list.setCurrentRow(row)

    def _remove(self):
        row = self.list.currentRow()
        if row >= 0:
            self.sources.pop(row)
            self._refresh()

    def _save(self):
        if not self.sources and not self.cb_app_data.isChecked():
            QMessageBox.warning(self, "Keep", "Choose at least one folder, or keep application data and settings enabled.")
            return
        CONFIG["backup_sources"] = [dict(e) for e in self.sources]
        CONFIG["include_app_data"] = self.cb_app_data.isChecked()
        CONFIG["backup_engine"] = (
            "builtin" if self.cb_managed is None or self.cb_managed.isChecked() else "external"
        )
        save_config()
        self.accept()




class ScheduleDialog(QDialog):
    """Simple consumer schedule editor backed by a systemd --user timer."""

    WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keep — Automatic Backups")
        self.resize(500, 300)
        schedule = dict(CONFIG.get("schedule", {}))
        layout = QVBoxLayout(self)

        self.cb_enabled = QCheckBox("Automatic Backups")
        configured = schedule.get("enabled")
        self.cb_enabled.setChecked(timer_enabled(schedule.get("timer_unit")) if configured is None else bool(configured))
        layout.addWidget(self.cb_enabled)

        form = QFormLayout()
        self.frequency = QComboBox()
        self.frequency.addItem("Every day", "daily")
        self.frequency.addItem("Every week", "weekly")
        idx = self.frequency.findData(schedule.get("frequency", "daily"))
        self.frequency.setCurrentIndex(max(0, idx))
        self.frequency.currentIndexChanged.connect(self._sync)
        form.addRow("Frequency:", self.frequency)

        self.weekday = QComboBox()
        self.weekday.addItems(self.WEEKDAYS)
        day = schedule.get("weekday", "Monday")
        self.weekday.setCurrentText(day if day in self.WEEKDAYS else "Monday")
        form.addRow("Day:", self.weekday)

        self.time = QTimeEdit()
        self.time.setDisplayFormat("h:mm AP")
        parsed = QTime.fromString(schedule.get("time", "04:00"), "HH:mm")
        self.time.setTime(parsed if parsed.isValid() else QTime(4, 0))
        form.addRow("Time:", self.time)
        layout.addLayout(form)

        note = QLabel(
            "Automatic backups keep working when the Keep window is closed."
        )
        note.setWordWrap(True)
        theming.role(note, "secondary")
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("Save")
        theming.role(save, "primary")
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self._sync()

    def _sync(self):
        self.weekday.setEnabled(self.frequency.currentData() == "weekly")

    def schedule_value(self):
        return {
            "managed_by_keep": True,
            "enabled": self.cb_enabled.isChecked(),
            "frequency": self.frequency.currentData(),
            "time": self.time.time().toString("HH:mm"),
            "weekday": self.weekday.currentText(),
            "timer_unit": "keep-backup.timer",
        }


class FirstRunDialog(QDialog):
    """Lightweight consumer onboarding without a brittle multi-page wizard.

    It stays non-modal so each setup action can open its normal, already-tested
    dialog. Nothing here duplicates destination/schedule/source logic; it is
    only a calm checklist into those existing flows.
    """

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("Welcome to Keep")
        self.resize(560, 390)
        self.setModal(False)
        layout = QVBoxLayout(self)

        title = QLabel("Set up your backup")
        f = title.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        intro = QLabel(
            "Keep needs to know what to protect and where to keep it. "
            "Automatic backups are optional, and encryption is chosen when "
            "you create a new backup destination."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addSpacing(8)
        self.lbl_sources = QLabel()
        btn_sources = QPushButton("1. Choose Backup Folders…")
        btn_sources.clicked.connect(self._sources)
        layout.addWidget(btn_sources)
        layout.addWidget(self.lbl_sources)

        self.lbl_destination = QLabel()
        btn_dest = QPushButton("2. Choose Backup Destination…")
        btn_dest.clicked.connect(self._destination)
        layout.addWidget(btn_dest)
        layout.addWidget(self.lbl_destination)

        self.lbl_schedule = QLabel()
        btn_schedule = QPushButton("3. Automatic Backups…")
        btn_schedule.clicked.connect(self._schedule)
        layout.addWidget(btn_schedule)
        layout.addWidget(self.lbl_schedule)

        hint = QLabel("You can change all of these later from the Backup menu.")
        hint.setWordWrap(True)
        theming.role(hint, "secondary")
        layout.addWidget(hint)
        layout.addStretch()

        row = QHBoxLayout()
        row.addStretch()
        done = QPushButton("Done")
        theming.role(done, "primary")
        done.clicked.connect(self.accept)
        row.addWidget(done)
        layout.addLayout(row)
        self._refresh()

    def _refresh(self):
        sources = _consumer_module.backup_source_entries(CONFIG)
        app_data = bool(CONFIG.get("include_app_data", True))
        pieces = []
        if sources:
            pieces.append(f"{len(sources)} folder{'s' if len(sources) != 1 else ''}")
        if app_data:
            pieces.append("application data")
        self.lbl_sources.setText("Protected: " + (" + ".join(pieces) if pieces else "nothing selected yet"))

        dest = CONFIG.get("destination", {})
        if _consumer_module.destination_configured(CONFIG):
            self.lbl_destination.setText(f"Destination: {dest.get('label') or dest.get('repo') or 'configured'}")
        else:
            self.lbl_destination.setText("Destination: not configured")

        schedule = CONFIG.get("schedule", {})
        enabled = schedule.get("enabled")
        if enabled is None:
            enabled = timer_enabled(schedule.get("timer_unit"))
        self.lbl_schedule.setText("Automatic backups: On" if enabled else "Automatic backups: Off")

    def _sources(self):
        self.main_window.configure_backup_sources()
        self._refresh()

    def _destination(self):
        self.main_window.change_backup_destination()
        self._refresh()

    def _schedule(self):
        self.main_window.configure_schedule()
        self._refresh()


class HelpDialog(QDialog):
    """Static, always-available explanation of the concepts Keep's own
    in-context dialogs assume you already know - what Safe vs Direct restore
    actually do, what Remember-passphrase means, and where to look if this
    machine itself is ever gone. Deliberately just text, not a wizard: the
    moment-to-moment guidance already lives in each dialog's own copy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keep — Help")
        self.resize(600, 560)
        outer = QVBoxLayout(self)

        # Scrollable, with Close pinned outside the scroll area - the content
        # below is long enough that no one fixed dialog size fits it well on
        # every screen, and a button that scrolls out of view is a trap.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)

        sections = [
            ("What Keep does",
             "Keep is a front end for BorgBackup. When Automatic Backups are "
             "enabled, your files are backed up on the schedule you chose to "
             "the destination shown in the Status panel, whether or not Keep "
             "is open. You can also leave scheduling off and use Back Up Now. "
             "Keep itself "
             "lets you check that those backups are actually working, and "
             "restore things yourself when you need to - without needing "
             "to know any Borg commands. Most of what's here (checking "
             "status, browsing, restoring) never touches the backup "
             "configuration; the Backup menu is where you deliberately "
             "change what gets backed up, the automatic schedule, the "
             "destination, or the exclude rules."),
            ("Around the app",
             "The Status panel (left) leads with one line answering \"am I "
             "protected?\" - the backup destination and when the last "
             "backup ran or next runs. \"Details…\" has the rest: the "
             "repository and the monthly check / deep verify / restore-test "
             "maintenance jobs. A backup's progress bar and current status "
             "appear automatically while one is running; \"Show log\" "
             "reveals the raw, detailed output alongside it if you want "
             "it.\n\n"
             "\"Apps & Data\" is the curated picker for applications and "
             "system settings. \"Folders\" contains the locations you chose "
             "under Backup Sources. \"Advanced\" is a raw "
             "file browser into the mounted archive, for anything that "
             "isn't in either curated list."),
            ("Restoring: Safe vs Direct",
             "\"Review restore…\" (Ctrl+R) shows what you checked and copies it to "
             "~/Keep-Restored/<timestamp>/ without touching anything live - "
             "always safe to try, and the normal way to look at an older "
             "version of something. Change folder… picks another destination.\n\n"
             "More options ▸ \"Restore to original location…\" overwrites the real, live files. "
             "Keep automatically saves whatever was there first to "
             "~/Keep-Restored/Before-Direct-Restore/<timestamp>/ before it "
             "does, so it can be undone by hand if needed - but treat it as "
             "a real action, not something to click just to look around."),
            ("Comparing archives",
             "\"Compare Archives...\" shows exactly what changed between "
             "any two backed-up points in time - added, removed, and "
             "modified files. Purely informational and read-only; it "
             "doesn't touch anything, so there's nothing to be careful "
             "about here."),
            ("Backup, Delete, and Change Destination",
             "\"Backup Now\" runs an out-of-schedule backup; \"Stop\" cancels "
             "one already in progress. \"Delete This Archive\" permanently "
             "removes one backed-up point in time - it asks for "
             "confirmation first, and can't be undone.\n\n"
             "\"Backup sources…\" lets you choose ordinary folders, and "
             "optionally application data/settings, without editing a config "
             "file. \"Automatic backups…\" turns the systemd schedule on "
             "or off and chooses when it runs.\n\n"
             "\"Change Destination\" points Keep, and the scheduled backup, "
             "at a different network share, drive, or folder - either "
             "adopting an existing Borg repository already there, or "
             "creating a new encrypted (recommended) or unencrypted one.\n\n"
             "\"Edit backup excludes…\" changes what's deliberately left "
             "out of the backup (caches, reinstallable software, and "
             "similar) without needing to find and hand-edit the file "
             "yourself. Changes apply to the next backup, not retroactively."),
            ("If Keep asks for a passphrase or key",
             "This only happens when Borg can't open the backup on its own "
             "- a wrong passphrase, or no local key at all. Keep walks you "
             "through unlocking it or recovering the key, from a saved key "
             "file or your Borg paper key. \"Remember this passphrase on "
             "this computer\" is off by default - leave it off unless you "
             "want Keep and the scheduled backup to keep using it without "
             "asking again."),
            ("If this machine is ever lost",
             "The full step-by-step recovery process, for a new machine "
             "with Keep not even installed yet, lives in "
             "DISASTER_RECOVERY.md next to this app. Keep that file - and "
             "your backup key and passphrase - somewhere off this machine "
             "too, or losing this machine means losing the backup with it. "
             "\"About Keep\" (also in this menu) shows exactly which "
             "destination and repository this copy of Keep is currently "
             "pointed at - useful for a quick sanity check."),
        ]
        for i, (heading, body) in enumerate(sections):
            if i > 0:
                rule = QFrame()
                rule.setFrameShape(QFrame.HLine)
                rule.setFrameShadow(QFrame.Sunken)
                layout.addWidget(rule)
                layout.addSpacing(6)
            heading_lbl = QLabel(heading)
            bold_font = heading_lbl.font()
            bold_font.setBold(True)
            bold_font.setPointSize(bold_font.pointSize() + 1)
            heading_lbl.setFont(bold_font)
            layout.addWidget(heading_lbl)
            layout.addSpacing(2)
            body_lbl = QLabel(body)
            body_lbl.setWordWrap(True)
            theming.role(body_lbl, "secondary")
            layout.addWidget(body_lbl)
            layout.addSpacing(10)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        outer.addWidget(btn_close)




class AboutDialog(QDialog):
    """Identity first, diagnostics behind a disclosure - the previous
    version was a flat list of Built/Backend/Destination/Repository/Config
    all at once, reading as a diagnostics panel wearing an About box's
    title. No fabricated version number (this project isn't under version
    control, confirmed via `git rev-parse` earlier in the project) or
    invented copyright/studio line either - a generic app-identity mockup
    doesn't fit a solo personal tool with no releases, so this keeps
    main.py's own mtime as an honest build stamp instead, same as before."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Keep")
        layout = QVBoxLayout(self)
        # About fits its visible content on both disclosure transitions.
        layout.setSizeConstraint(QLayout.SetFixedSize)

        title_lbl = QLabel("Keep")
        title_font = title_lbl.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 4)
        title_lbl.setFont(title_font)
        layout.addWidget(title_lbl)

        layout.addWidget(QLabel("A BorgBackup front end for backup status, restore, and recovery."))

        try:
            build_stamp = datetime.fromtimestamp(os.path.getmtime(__file__)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            build_stamp = "unknown"
        build_lbl = QLabel(f"Build: {build_stamp}")
        theming.role(build_lbl, "secondary")
        layout.addWidget(build_lbl)

        # Deliberately still fetched eagerly, not lazily on first expand -
        # a single subprocess call with its own 10s timeout, on a dialog
        # the user explicitly opened; reviewed and declined as not worth
        # the added complexity for what it'd actually save.
        try:
            borg_version = subprocess.run(
                ["borg", "--version"], capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=10,
            ).stdout.strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired):
            borg_version = "unknown"

        sysinfo_content = QWidget()
        sysinfo_form = QFormLayout(sysinfo_content)
        sysinfo_form.setContentsMargins(0, 4, 0, 0)
        sysinfo_form.addRow("Backend:", QLabel(borg_version))
        sysinfo_form.addRow("Application logs:", QLabel(str(app_logging.log_directory())))
        sysinfo_form.addRow("Destination:", QLabel(DEST_STATUS.get("label", "?")))
        sysinfo_form.addRow("Repository:", QLabel(REPO))
        sysinfo_form.addRow("Config file:", QLabel(str(CONFIG_PATH)))
        self.sysinfo_section = DisclosureSection("System Information…", "Hide System Information", sysinfo_content)
        layout.addWidget(self.sysinfo_section)

        layout.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)


class CompareArchivesDialog(QDialog):
    """What changed between two backed-up points in time - wraps `borg
    diff`, which reads both archives directly and needs no mount. Purely
    read-only: no credentials, no writes, nothing destructive - the
    lowest-risk of the Vorta-inspired additions."""

    def __init__(self, repo, archives, current_archive, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keep — Compare Archives")
        self.resize(640, 480)
        self.repo = repo
        self._worker = None
        layout = QVBoxLayout(self)

        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel("Compare:"))
        self.combo_a = QComboBox()
        self.combo_a.addItems(archives)
        if current_archive in archives:
            self.combo_a.setCurrentText(current_archive)
        pick_row.addWidget(self.combo_a, stretch=1)
        pick_row.addWidget(QLabel("with:"))
        self.combo_b = QComboBox()
        self.combo_b.addItems(archives)
        other = next((a for a in archives if a != self.combo_a.currentText()), None)
        if other:
            self.combo_b.setCurrentText(other)
        pick_row.addWidget(self.combo_b, stretch=1)
        self.btn_compare = QPushButton("Compare")
        theming.role(self.btn_compare, "primary")
        self.btn_compare.clicked.connect(self._run_compare)
        pick_row.addWidget(self.btn_compare)
        layout.addLayout(pick_row)

        self.status_lbl = QLabel("Pick two archives and click Compare.")
        theming.role(self.status_lbl, "secondary")
        layout.addWidget(self.status_lbl)

        self.results = QListWidget()
        layout.addWidget(self.results, stretch=1)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def _run_compare(self):
        a = self.combo_a.currentText()
        b = self.combo_b.currentText()
        if not a or not b:
            return
        if a == b:
            self.status_lbl.setText("Pick two different archives to compare.")
            return
        self.results.clear()
        self.btn_compare.setEnabled(False)
        self.status_lbl.setText(f"Comparing {a} -> {b}...")
        self._worker = DiffWorker(self.repo, a, b)
        self._worker.finished_result.connect(self._on_result)
        self._worker.start()

    def _on_result(self, ok, entries, error):
        self.btn_compare.setEnabled(True)
        if not ok:
            self.status_lbl.setText(f"Could not compare: {error}")
            return
        if not entries:
            self.status_lbl.setText("No differences found.")
            return
        self.status_lbl.setText(f"{len(entries)} changed path(s):")
        for path, changes in entries:
            self.results.addItem(format_diff_entry(path, changes))


class ExcludeEditorDialog(QDialog):
    """Raw text editor for the excludes file, opened from inside Keep
    instead of requiring a separate text editor outside it. Deliberately
    edits the file as plain text rather than a structured pattern list -
    the real file has hand-written comments explaining WHY certain
    patterns exist (e.g. why flatpak/overrides/ is deliberately NOT
    excluded, after an earlier setup pass silently lost it), and parsing
    the file into a pattern list and rebuilding it on save would throw
    those comments away. A plain text editor can't lose anything that
    wasn't already exactly what was typed."""

    def __init__(self, excludes_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keep — Edit Backup Excludes")
        self.resize(640, 520)
        self.excludes_path = excludes_path
        layout = QVBoxLayout(self)

        info_lbl = QLabel(
            "One Borg exclude pattern per line (lines starting with # are "
            "comments, ignored by Borg). Changes take effect on the next "
            "backup, not retroactively - nothing already backed up is "
            "affected either way."
        )
        info_lbl.setWordWrap(True)
        theming.role(info_lbl, "secondary")
        layout.addWidget(info_lbl)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("monospace"))
        try:
            self.editor.setPlainText(Path(excludes_path).read_text())
        except OSError:
            self.editor.setPlainText("")
        layout.addWidget(self.editor, stretch=1)

        self.status_lbl = QLabel("")
        theming.role(self.status_lbl, "secondary")
        layout.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        self.btn_save = QPushButton("Save")
        theming.role(self.btn_save, "primary")
        self.btn_save.clicked.connect(self._save)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

    def _save(self):
        content = self.editor.toPlainText()
        tmp_path = f"{self.excludes_path}.tmp"
        try:
            Path(tmp_path).write_text(content)
            os.replace(tmp_path, self.excludes_path)
        except OSError as e:
            self.status_lbl.setText(f"Could not save: {e} (nothing was changed)")
            return
        self.accept()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("keepWindow")
        self.setWindowTitle("Keep")
        self.setWindowIcon(QIcon.fromTheme("drive-harddisk"))
        self.resize(820, 600)
        self.mount_coordinator = MountCoordinator()
        self.mounted = False
        if getattr(self, "_browse_operation_lock", None):
            self._browse_operation_lock.release()
            self._browse_operation_lock = None
        self.mounted_archive = None
        self._prescan_total_bytes = None
        # True for the whole duration of a running backup or delete/compact -
        # both hold the actual repo lock via their own borg subprocess the
        # entire time, so a mount attempted while either is running would
        # just fail with a lock-timeout error (or, if given a generous
        # --lock-wait, block the UI thread for that long instead - worse).
        # ensure_mounted() checks this and quietly declines rather than
        # showing a confusing lock-error dialog for something that isn't
        # really a problem, just timing - switching tabs or archives while
        # a repo operation is in progress is completely reasonable to do.
        self._repo_op_running = False
        self._was_browsing_before_backup = False
        # a refresh_status() call that arrives while a passive status query
        # is still finishing (e.g. just cancelled by _stop_status_query()
        # but not yet actually exited) doesn't get dropped silently - it's
        # remembered here and retried once the in-flight one actually
        # completes, see _start_status_query()/_on_status_query_finished()
        self._status_refresh_pending = False
        self._status_refresh_pending_select_latest = False
        self._first_run_dialog = None
        # don't hold a Borg mount (and its repo lock) just because the window
        # is open - only while actually browsing, and not indefinitely even
        # then, since that can block the unattended scheduled backup
        self.unmount_timer = QTimer(self)
        self.unmount_timer.setSingleShot(True)
        self.unmount_timer.timeout.connect(self._unmount)
        self._build_ui()
        self._apply_theme_dependent_styling()
        self.refresh_status()
        if CONFIG.get("setup_complete") is False:
            QTimer.singleShot(0, self._show_first_run_setup)
        # deliberately NOT mounting here - status/NAS/archive-list are all
        # available with zero repo lock held. Apps & Data and Projects show
        # a placeholder with a "Browse This Archive" button until the user
        # actually asks to see restore content (a real tab switch, an
        # archive change, or that button - all funnel through
        # ensure_mounted()), keeping Keep genuinely lock-free until then.

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.menu_bar = QMenuBar(self)
        backup_menu = self.menu_bar.addMenu("Backup")
        self.action_back_up_now = backup_menu.addAction("Back up now", self.start_backup)
        backup_menu.addSeparator()
        self.action_backup_sources = backup_menu.addAction("Backup sources…", self.configure_backup_sources)
        self.action_schedule = backup_menu.addAction("Automatic backups…", self.configure_schedule)
        self.action_change_destination = backup_menu.addAction("Change destination…", self.change_backup_destination)
        self.action_edit_excludes = backup_menu.addAction("Edit backup excludes…", self.edit_excludes)
        self.action_back_up_now.setShortcut(QKeySequence("Ctrl+B"))
        self.action_review_restore = backup_menu.addAction("Review restore…", self.review_restore)
        self.action_review_restore.setShortcut(QKeySequence("Ctrl+R"))
        self.action_backup_sources.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self.action_schedule.setShortcut(QKeySequence("Ctrl+Shift+A"))
        backup_menu.addSeparator()
        self.action_recovery_access = backup_menu.addAction("Recovery access…", self.show_recovery_access)
        self.action_test_recovery = backup_menu.addAction("Test recovery…", self.test_recovery)
        backup_menu.addSeparator()
        self.action_exit = backup_menu.addAction("Exit", self.close)
        self.action_exit.setMenuRole(QAction.QuitRole)
        self.action_exit.setShortcut(QKeySequence(QKeySequence.Quit) if not QKeySequence(QKeySequence.Quit).isEmpty() else QKeySequence("Ctrl+Q"))
        view_menu = self.menu_bar.addMenu("&View")
        self.action_refresh = view_menu.addAction("&Refresh", self.refresh_status)
        self.action_refresh.setShortcut(QKeySequence("F5"))
        self.action_show_log = view_menu.addAction("Show &log")
        self.action_show_log.setCheckable(True)
        self.action_show_log.setShortcut(QKeySequence("Ctrl+L"))
        view_menu.addSeparator()
        # Manual override of the desktop theme, same three choices and labels
        # as VeloCoder (2026-09-25). Saved in config.json as "theme".
        theme_menu = view_menu.addMenu("&Theme")
        self.theme_actions = QActionGroup(self)
        self.theme_actions.setExclusive(True)
        for value, label in theming.THEME_CHOICES:
            action = theme_menu.addAction(label)
            action.setCheckable(True)
            action.setData(value)
            action.setChecked(CONFIG.get("theme", "system") == value)
            self.theme_actions.addAction(action)
        self.theme_actions.triggered.connect(self._on_theme_chosen)
        help_menu = self.menu_bar.addMenu("Help")
        help_menu.addAction("Keep help", self._show_help).setShortcut(QKeySequence("F1"))
        help_menu.addAction("About Keep", self._show_about)
        root.setMenuBar(self.menu_bar)

        self.backup_panel = BackupPanel(self)
        self.status_page = StatusPage()
        self.status_page.recoveryTestRequested.connect(self.test_recovery)
        self.restore_page = RestorePage(self, CONFIG, MOUNTPOINT, HOME_IN_ARCHIVE, ItemPicker)
        # Existing application handlers use these stable widget references. Views
        # own their construction; no view imports main or executes backend work.
        for view in (self.backup_panel, self.status_page, self.restore_page):
            for name in view.CONTROL_NAMES:
                setattr(self, name, getattr(view, name))
        self.restore_surface = self.restore_page
        # The toolbar's Review restore… replaces the per-tab restore buttons.
        self.btn_restore.hide()
        for picker in (self.apps_picker, self.folders_picker):
            picker.btn_restore_safe.hide()
        # ODCS window model: toolbar (view switch, primary action trailing)
        # over a sidebar + content split. A stacked widget replaces the old
        # tab widget; the ViewSwitch in the toolbar drives it.
        self.pages = QStackedWidget()
        self.pages.setObjectName("keepContent")
        self.pages.addWidget(self.status_page)
        self.pages.addWidget(self.restore_page)
        self.view_switch = ViewSwitch(["Status", "Restore"], accessible_name="Keep view")
        self.view_switch.currentChanged.connect(self.pages.setCurrentIndex)
        self.pages.currentChanged.connect(self.view_switch.setCurrentIndex)
        self.pages.currentChanged.connect(self._update_primary_action)
        toolbar = QWidget()
        toolbar.setObjectName("keepToolbar")
        toolbar.setAttribute(Qt.WA_StyledBackground, True)
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(16, 8, 16, 8)
        bar.setSpacing(8)
        app_title = QLabel("Keep")
        app_title.setObjectName("keepAppTitle")
        app_title.setFixedWidth(284)
        bar.addWidget(app_title)
        bar.addWidget(self.view_switch)
        bar.addStretch(1)
        # Restore view's task: one review step for every tab, always into a new folder.
        self.btn_review_restore = QPushButton("Review restore…")
        self.btn_review_restore.clicked.connect(self.review_restore)
        self.btn_review_restore.hide()
        for widget in (self.btn_stop, self.btn_backup, self.btn_review_restore):
            bar.addWidget(widget)
        root.addWidget(toolbar)
        for index in (0, 1):
            action = QAction(self)
            action.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            action.triggered.connect(lambda checked=False, index=index: self.pages.setCurrentIndex(index))
            self.addAction(action)
        self.action_show_log.toggled.connect(self.log_section.set_expanded)
        self.action_show_log.toggled.connect(lambda checked: self.pages.setCurrentIndex(0) if checked else None)
        self.log_section.toggle.toggled.connect(self.action_show_log.setChecked)
        self.pages.currentChanged.connect(self._on_workspace_changed)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        # Status shows the backup settings; Restore shows the stored backups.
        self.backup_list_panel = BackupListPanel()
        self.backup_list_panel.chosen.connect(self.archive_combo.setCurrentIndex)
        self.archive_combo.currentIndexChanged.connect(self._on_archive_selection_shown)
        self.sidebar_stack = QStackedWidget()
        self.sidebar_stack.setFixedWidth(300)
        self.sidebar_stack.addWidget(self.backup_panel)
        self.sidebar_stack.addWidget(self.backup_list_panel)
        self.pages.currentChanged.connect(self.sidebar_stack.setCurrentIndex)
        body.addWidget(self.sidebar_stack)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        self._refresh_setup_labels()
        self._refresh_activity()
        self.resize(1100, 720)

    def _on_theme_chosen(self, action):
        CONFIG["theme"] = action.data()
        save_config()
        self._apply_theme_dependent_styling()

    def _update_primary_action(self, index=None):
        """The strongest accent marks the current view's task: Back up now on
        Status, Review restore… on Restore (DESIGN.md principle 4)."""
        index = self.pages.currentIndex() if index is None else index
        theming.role(self.btn_backup, "primary" if index == 0 else "")
        theming.role(self.btn_review_restore, "primary" if index == 1 else "")
        self.btn_review_restore.setVisible(index == 1)

    def _apply_theme_dependent_styling(self):
        theming.install(QApplication.instance(), CONFIG.get("theme", "system"))
        self._update_primary_action()
        theming.role(self.lbl_progress_detail, "secondary")
        theming.role(self.lbl_delete_status, "secondary")

    def changeEvent(self, event):
        super().changeEvent(event)
        # fires when the OS-level light/dark mode switches live while Keep
        # is already running (confirmed: KDE Plasma's own theme switch
        # delivers this) - everything else in this window is either a
        # native widget Qt repaints on its own, or already re-derives its
        # color on its own next refresh_status() call (destructive/error
        # text) - only the handful of colors baked into a static QSS
        # string at construction time actually need this nudge.
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.FontChange) and hasattr(self, "btn_backup"):
            self._apply_theme_dependent_styling()

    def _show_help(self):
        HelpDialog(self).exec()

    def _show_about(self):
        AboutDialog(self).exec()

    def closeEvent(self, event):
        # Closing while a backup/delete worker (a QThread) is still running
        # risks "QThread: Destroyed while thread is still running" - the
        # same crash class already fixed once this session for
        # PaperKeyWizardDialog, found again here via a reviewer's whole-
        # app sweep. Backup can be stopped and waited for; delete/compact
        # has no safe interrupt (compact physically rewrites segment files
        # mid-operation), so closing during one is refused outright rather
        # than risking it.
        if self._repo_op_running:
            if hasattr(self, "worker") and self.worker.isRunning():
                confirmed = QMessageBox.warning(
                    self, "Keep",
                    "A backup is still running. Quitting now will stop it before it "
                    "finishes - existing archives are safe either way, but this run "
                    "won't complete.\n\nQuit anyway and stop the backup?",
                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
                )
                if confirmed != QMessageBox.Yes:
                    event.ignore()
                    return
                self.worker.stop()
                if not self.worker.wait(5000):
                    # SIGTERM didn't finish the job in time (ignored/delayed,
                    # a slow NAS still flushing, whatever) - closing anyway
                    # would destroy a QThread object that's still actually
                    # running. Refuse rather than risk that; the process
                    # group kill already sent will very likely land soon
                    # regardless, so a moment's wait and trying again works.
                    QMessageBox.warning(
                        self, "Keep",
                        "The backup didn't stop in time - it may still be shutting "
                        "down. Wait a moment and try closing again.",
                    )
                    event.ignore()
                    return
            elif hasattr(self, "delete_worker") and self.delete_worker.isRunning():
                QMessageBox.warning(
                    self, "Keep",
                    "A delete/compact is currently in progress and can't be safely "
                    "interrupted partway through - please wait for it to finish "
                    "before closing Keep.",
                )
                event.ignore()
                return
        # Deliberately its own check, not nested under _repo_op_running -
        # a passive status query can be running independently of any real
        # repo operation. Unlike backup/delete, there's nothing here to
        # confirm (interrupting a read has zero data/safety consequence),
        # just a real QThread that must not be destroyed while still
        # running. stop() terminates whatever Borg call is currently in
        # flight rather than waiting out its full 30s timeout, so this
        # wait is expected to return almost immediately in the normal
        # case - the "wait a moment" path is a genuine rare edge case now,
        # not the up-to-a-minute wait an uncancellable worker would risk.
        if hasattr(self, "status_worker") and self.status_worker.isRunning():
            self.status_worker.stop()
            if not self.status_worker.wait(3000):
                QMessageBox.warning(
                    self, "Keep",
                    "Still checking backup status - please wait a moment and try "
                    "closing again.",
                )
                event.ignore()
                return
        if not self._unmount():
            # `borg mount` daemonizes into its own background process by
            # default - it does NOT die when Keep's own process exits, so
            # closing while still genuinely mounted would leave an orphaned
            # mount holding the repo lock indefinitely, with nothing left
            # running to ever retry releasing it (the retry timer above
            # only helps while Keep itself is still open). Refuse to close
            # rather than risk that - found via a reviewer's whole-app sweep.
            QMessageBox.warning(
                self, "Keep",
                "The archive browser is still mounted and won't unmount right now "
                "(still in use) - closing anyway could leave it locked in the "
                "background even after Keep exits. Try again in a moment.",
            )
            event.ignore()
            return
        event.accept()

    def _set_repo_actions_enabled(self, enabled):
        """Grey out the app-level actions that need uncontended repo access
        while a backup/delete holds the lock - btn_backup/btn_delete_archive/
        archive_combo/action_back_up_now are handled at their own call sites
        since they ALSO depend on destination availability, not just this.
        Compare Archives is read-only but still takes Borg's own repository
        lock like anything else - included here so it doesn't sit clickable
        only to contend with (or wait behind) the actual operation. All of
        these already refuse safely if clicked anyway (change_backup_destination/
        compare_archives both check _repo_op_running first) - this is about
        the UI visibly reflecting what's usable, not a new safety guard."""
        self.source_choice.setEnabled(enabled)
        self.apps_choice.setEnabled(enabled)
        self.settings_choice.setEnabled(enabled)
        self.destination_choice.setEnabled(enabled)
        self.schedule_button.setEnabled(enabled)
        self.action_backup_sources.setEnabled(enabled)
        self.action_schedule.setEnabled(enabled)
        self.action_change_destination.setEnabled(enabled)
        self.action_edit_excludes.setEnabled(enabled)
        self.btn_compare_archives.setEnabled(enabled)
        self.btn_test_recovery.setEnabled(enabled)
        self.action_test_recovery.setEnabled(enabled)

    # --- status ---

    def _apply_status_headline(self, dest_available, verdict, ts):
        """One literally-true line answering 'am I protected?' without
        needing to interpret several rows. Priority: an unreachable
        destination beats a stale last-attempt verdict (can't back up at
        all right now, the more urgent fact); a failed attempt beats
        reporting nothing wrong. Deliberately never says "up to date" -
        that implies a currentness guarantee (the schedule is still
        actually running) this data doesn't verify, only that the last
        attempt succeeded - see this method's callers' comments.

        "stopped" (the user clicked Stop) is deliberately NOT treated as a
        failure here - reviewer-flagged: cancelling on purpose isn't the
        same event as the backup dying on its own, and showing it as a red
        "failed" after an intentional action undermines trust in what red
        actually means elsewhere in this app. Neutral styling, calm
        wording, same as "never run"."""
        if not dest_available:
            self.lbl_headline.setText("! Backup destination unavailable")
            theming.role(self.lbl_headline, "error")
        elif verdict == "FAILED":
            # no timestamp here - it's the exact same value already shown
            # right below in "Last attempt:" (and, in the common healthy
            # case, in "Last backup:" too); repeating it in the one line
            # meant to be readable at a glance just adds noise
            self.lbl_headline.setText("! Last backup failed")
            theming.role(self.lbl_headline, "error")
        elif verdict == "warning":
            self.lbl_headline.setText("! Backup completed with warnings")
            theming.role(self.lbl_headline, "")
        elif verdict == "stopped":
            self.lbl_headline.setText("Backup was stopped")
            theming.role(self.lbl_headline, "")
        elif verdict == "never run":
            self.lbl_headline.setText("No backups yet")
            theming.role(self.lbl_headline, "")
        else:
            self.lbl_headline.setText("✓ Last backup completed successfully")
            theming.role(self.lbl_headline, "")

    def _refresh_setup_labels(self):
        sources = _consumer_module.backup_source_entries(CONFIG)
        labels = [entry["label"] for entry in sources]
        description = (f"{len(labels)} folder" + ("s" if len(labels) != 1 else "")) if labels else "No folders selected"
        self.source_choice.setValue(description)
        self.source_choice.setToolTip("\n".join(labels))
        destination = CONFIG.get("destination", {})
        label = destination.get("label") or "Choose backup location"
        self._show_destination_row(label, getattr(self, "_destination_available", None))
        self.destination_choice.setToolTip(label)
        if hasattr(self, "apps_choice"):
            import applications
            try:
                pending = applications.unreviewed(CONFIG, applications.catalog(CONFIG, include_unrecognized=True))
            except OSError:
                pending = []
            new_apps = [entry["label"] for entry in pending if entry["category"] == "applications"]
            new_settings = [entry["label"] for entry in pending if entry["category"] != "applications"]
            if not CONFIG.get("include_app_data", True):
                summary = "None selected"
            elif CONFIG.get("app_selection_mode", "all") == "selected":
                system_ids = {"curated:" + item[0] for item in CONFIG.get("curated_items", []) if item[3] == "system"}
                selection = set(CONFIG.get("selected_applications", []))
                summary = f"{len(selection - system_ids)} selected"
            else:
                summary = "All application data"
            self._set_choice_with_review(self.apps_choice, summary, new_apps)
            if not CONFIG.get("include_app_data", True):
                settings_summary = "None selected"
            elif CONFIG.get("app_selection_mode", "all") == "all":
                settings_summary = "All curated settings"
            else:
                settings_summary = f"{len(selection & system_ids)} selected"
            self._set_choice_with_review(self.settings_choice, settings_summary, new_settings)
            schedule = CONFIG.get("schedule", {})
            self.schedule_button.setValue(schedule_summary(schedule) if schedule.get("enabled") else "Off")

    def _show_destination_row(self, label, available):
        """Sidebar Destination row: the name, and once known the live state
        as a dot plus words (wraps; never elided, DESIGN.md Resilience)."""
        if not CONFIG.get("destination", {}).get("label"):
            self.destination_choice.setValue(label)  # "Choose backup location"
        elif available is None:
            self.destination_choice.setValue(label)
        elif available:
            self.destination_choice.setValue(f"{label} · Connected", indicator="ok")
        else:
            self.destination_choice.setValue(f"{label} · Not connected", "error", indicator="error")

    @staticmethod
    def _set_choice_with_review(row, summary, unreviewed_labels):
        """App data that appeared after the last choice isn't backed up in
        "selected" mode; say so on the row instead of skipping it silently."""
        if unreviewed_labels:
            row.setValue(f"{summary} · {len(unreviewed_labels)} to review", "warning")
            row.setToolTip("Not backed up yet, and not reviewed: " + ", ".join(sorted(unreviewed_labels)))
        else:
            row.setValue(summary)
            row.setToolTip("")

    def configure_applications(self, category="applications"):
        if self._repo_op_running:
            return
        if CONFIG.get("backup_engine") == "external":
            QMessageBox.information(self, "Application selection", "Choose the built-in backup engine in Backup Sources before selecting individual apps. Your external script controls its own sources.")
            return
        dialog = ApplicationSelectionDialog(CONFIG, HOME, self, category)
        if dialog.exec() != QDialog.Accepted:
            return
        candidate = dict(CONFIG)
        candidate["include_app_data"] = True
        candidate["app_selection_mode"] = "all" if dialog.all_data.isChecked() else "selected"
        import applications
        entries = applications.catalog(CONFIG, include_unrecognized=True)
        previous = set(CONFIG.get("selected_applications", []))
        if CONFIG.get("include_app_data", True) and CONFIG.get("app_selection_mode", "all") == "all":
            previous.update(entry["id"] for entry in entries)
        other = [entry["id"] for entry in entries if entry["category"] != category and entry["id"] in previous]
        candidate["selected_applications"] = dialog.selection() + other
        # Everything the person just saw in this category now counts as
        # decided; app data that appears later is flagged "to review".
        candidate["known_applications"] = sorted(
            set(CONFIG.get("known_applications", []))
            | {entry["id"] for entry in entries if entry["category"] == category})
        conflict = applications.selection_conflict(candidate, [e["path"] for e in _consumer_module.backup_source_entries(candidate)])
        if conflict:
            QMessageBox.warning(self, "Overlapping backup folders", f"The folder {conflict} includes application data directly. Remove that broad folder in Choose Folders before choosing individual apps. Your selection was not changed.")
            return
        CONFIG.update(candidate)
        save_config()
        self._refresh_setup_labels()

    def _refresh_activity(self):
        repo, info, _count, _when = getattr(self, "_verified_repo_info", (None, None, None, None))
        repo_id = ((info or {}).get("repository") or {}).get("id") if repo == REPO else None
        entries = _consumer_module.recent_activity(LOGDIR, REPO, repo_id)
        for index, (timestamp, outcome) in enumerate(self.activity_rows):
            visible = index < len(entries) or index == 0
            timestamp.setVisible(visible)
            outcome.setVisible(visible)
            if index < len(entries):
                date, result = entries[index]
                timestamp.setText(friendly_timestamp(date) if date else "Unknown time")
                outcome.setText(result)
            elif index == 0:
                timestamp.setText("No activity yet")
                outcome.setText("Your backup results will appear here.")

    def _refresh_facts(self, dest):
        """Backup completed / Integrity checked / Recovery tested, each with its
        own result and date, from the same sources as the Details rows."""
        facts = self.status_facts
        verdict, ts = last_backup_attempt_status()
        state, description = {
            "ok": ("ok", "Your selected files were saved"),
            "warning": ("warning", "Finished with warnings. Show the log for details."),
            "stopped": ("warning", "The last backup was stopped before it finished"),
            "FAILED": ("error", "The last backup didn't finish. Show the log for details."),
        }.get(verdict, ("never", "Your selected files were saved"))
        facts.setFact("backup", state, friendly_timestamp(ts) if ts else "", description)

        checks = []
        for prefix in ("check", "check-verify-data"):
            outcome, when = log_verdict(prefix, ["completed successfully in", "Repository check complete"], ["FAILED"])
            if when:
                checks.append((outcome, when))
        health_path = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "keep/last-check.json"
        try:
            health = json.loads(health_path.read_text(encoding="utf-8"))
            if health.get("repository") == dest.get("repo") and (health.get("finished") or health.get("started")):
                checks.append((health.get("result", "unknown"), health.get("finished") or health.get("started")))
        except (OSError, ValueError, TypeError, AttributeError):
            pass

        def moment(item):
            try:
                return datetime.fromisoformat(item[1]).astimezone().timestamp()
            except (TypeError, ValueError):
                return 0

        if checks:
            outcome, when = max(checks, key=moment)
            state, description = {
                "ok": ("ok", "Stored data is readable and consistent"),
                "success": ("ok", "Stored data is readable and consistent"),
                "running": ("info", "An integrity check is running now"),
                "FAILED": ("error", "The last check found a problem. Show the log for details."),
                "failed": ("error", "The last check found a problem. Show the log for details."),
            }.get(outcome, ("warning", "The last check didn't finish cleanly"))
            facts.setFact("check", state, friendly_timestamp(when), description)
        else:
            facts.setFact("check", "never", "", "Stored data is readable and consistent")

        record = recovery_test.load()
        state, when, advice = recovery_test.status(record, dest.get("repo"))
        facts.setFact("recovery", state, friendly_timestamp(when) if when else "", advice)
        self.recovery_access_row.setValue(*recovery_access.summary(dest.get("repo")))
        if state != "never":
            self.lbl_restore_test.setText(f"{record.get('result')} ({friendly_timestamp(when)})")
            theming.role(self.lbl_restore_test, "error" if state == "error" else "")

    def show_recovery_access(self):
        dest = refresh_destination()
        repo, info, archive_count, verified = getattr(self, "_verified_repo_info", (None, None, None, None))
        if repo != dest.get("repo"):
            info, archive_count, verified = None, None, None
        dialog = recovery_access.RecoveryAccessDialog(
            dest.get("repo"), dest, info if dest["available"] else None, archive_count,
            friendly_timestamp(verified) if verified else None, borg_env, self.test_recovery, self)
        dialog.changed.connect(lambda: self.recovery_access_row.setValue(*recovery_access.summary(dest.get("repo"))))
        dialog.exec()

    def test_recovery(self):
        """Prove a file comes back using only the typed passphrase (recovery_test.py)."""
        if self._repo_op_running:
            QMessageBox.information(self, "Keep", "Wait for the current backup or delete to finish, then test recovery.")
            return
        dest = refresh_destination()
        if not dest["available"] or not dest.get("repo"):
            QMessageBox.information(self, "Keep", f"Connect {dest.get('label') or 'your backup destination'} first, then test recovery.")
            return
        if not self._unmount():
            QMessageBox.warning(self, "Keep", "Close the archive you're browsing, then test recovery.")
            return
        dialog = RecoveryTestDialog(dest["repo"], self)
        dialog.recorded.connect(lambda _record: self.refresh_status())
        dialog.exec()

    def refresh_status(self, select_latest_archive=False):
        self._refresh_setup_labels()
        self._refresh_activity()
        dest = refresh_destination()
        type_label = {"network": "network drive", "removable": "external drive", "other": "local folder"}.get(dest["type"], dest["type"])
        dest_path_text = dest["repo"] or "(unavailable)"
        dest_fm = self.lbl_dest.fontMetrics()
        self.lbl_dest.setText(dest_fm.elidedText(dest_path_text, Qt.ElideMiddle, self.lbl_dest.width() or 220))
        self.lbl_dest.setToolTip(dest_path_text)
        dest_status_fm = self.lbl_dest_status.fontMetrics()
        self._destination_available = bool(dest["available"])
        self._show_destination_row(CONFIG.get("destination", {}).get("label") or dest["label"], self._destination_available)
        if dest["available"]:
            dest_status_text = f"{dest['label']} ({type_label}) — connected"
            self.lbl_dest_status.setText(dest_status_fm.elidedText(dest_status_text, Qt.ElideRight, self.lbl_dest_status.width() or 220))
            self.lbl_dest_status.setToolTip(dest_status_text)
            theming.role(self.lbl_dest_status, "")
            self.lbl_dest_reason.hide()
            self.btn_backup.setEnabled(not self._repo_op_running)
            self.action_back_up_now.setEnabled(not self._repo_op_running)
        else:
            dest_status_text = f"{dest['label']} ({type_label}) — NOT CONNECTED"
            self.lbl_dest_status.setText(dest_status_fm.elidedText(dest_status_text, Qt.ElideRight, self.lbl_dest_status.width() or 220))
            self.lbl_dest_status.setToolTip(dest_status_text)
            theming.role(self.lbl_dest_status, "error")
            self.lbl_dest_reason.setText(f"{dest['reason']}. Backup Now is disabled until it's available again.")
            theming.role(self.lbl_dest_reason, "error")
            self.lbl_dest_reason.show()
            self.btn_backup.setEnabled(False)
            self.action_back_up_now.setEnabled(False)

        # local-only, fast - stays synchronous. Only the NAS-dependent Borg
        # info/list calls below move to a background worker.
        verdict, ts = last_backup_attempt_status()
        self.lbl_last_attempt.setText(f"{verdict} ({friendly_timestamp(ts)})" if ts else verdict)
        theming.role(self.lbl_last_attempt, "error" if verdict == "FAILED" else "")
        self._apply_status_headline(dest["available"], verdict, ts)

        schedule = CONFIG.get("schedule", {})
        if schedule.get("managed_by_keep") and schedule.get("enabled") is False:
            self.lbl_next.setText("Off")
        else:
            try:
                unit = configured_timer_unit()
                out = subprocess.run(
                    ["systemctl", "--user", "show", unit, "-p", "NextElapseUSecRealtime", "--value"],
                    capture_output=True, text=True, timeout=5,
                )
                raw_next = out.stdout.strip()
                if raw_next:
                    self.lbl_next.setText(friendly_systemd_timestamp(raw_next))
                else:
                    self.lbl_next.setText("Off" if not timer_enabled(unit) else "unknown")
            except Exception:
                self.lbl_next.setText("unknown")

        verdict, ts = log_verdict("check", ["completed successfully in", "Repository check complete"], ["FAILED"])
        self.lbl_check.setText(f"{verdict} ({friendly_timestamp(ts)})" if ts else verdict)
        verdict, ts = log_verdict("check-verify-data", ["completed successfully in", "Repository check complete"], ["FAILED"])
        self.lbl_verify.setText(f"{verdict} ({friendly_timestamp(ts)})" if ts else verdict)
        verdict, ts = log_verdict("restore-test", ["restore test passed"], ["restore test FAILED", "FAIL:"])
        self.lbl_restore_test.setText(f"{verdict} ({friendly_timestamp(ts)})" if ts else verdict)
        theming.role(self.lbl_restore_test, "error" if verdict == "FAILED" else "")
        # CLI check outcomes are independent of backup success and match this repo.
        health_path = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "keep/last-check.json"
        try:
            health = json.loads(health_path.read_text(encoding="utf-8"))
            if health.get("repository") == dest.get("repo"):
                label = self.lbl_verify if health.get("deep") else self.lbl_check
                outcome = health.get("result", "unknown")
                timestamp = health.get("finished") or health.get("started")
                label.setText(f"{outcome} ({friendly_timestamp(timestamp)})" if timestamp else outcome)
                theming.role(label, "error" if outcome in ("failed", "cancelled", "warning") else "")
        except (OSError, ValueError, TypeError):
            pass
        self._refresh_facts(dest)

        if not dest["available"]:
            self.lbl_repo.setText("unavailable")
            self.lbl_last.setText("no archives yet")
            self._apply_archive_listing(None, select_latest=select_latest_archive)
            return
        self._start_status_query(select_latest_archive)

    def _start_status_query(self, select_latest_archive):
        # _repo_op_running (backup/delete/destination-switch) and a passive
        # status query are deliberately separate concepts - see
        # _stop_status_query()'s docstring for why they must stay that way.
        # A running repo-op still means "don't start ANOTHER query right
        # now" though: the next refresh_status() call once it finishes will
        # pick this up, no need to race it.
        if self._repo_op_running:
            return
        if hasattr(self, "status_worker") and self.status_worker.isRunning():
            # one already in flight - could just be Refresh clicked twice
            # fast, but could also be a query _stop_status_query() just
            # cancelled that hasn't actually exited yet (stop() is fire-
            # and-forget, so isRunning() can still read True for a brief
            # moment after). Either way, don't drop this request silently -
            # remember it and retry once the in-flight one actually
            # finishes, in _on_status_query_finished().
            self._status_refresh_pending = True
            self._status_refresh_pending_select_latest = (
                self._status_refresh_pending_select_latest or select_latest_archive
            )
            return
        # Previous destination's numbers would otherwise linger, visibly
        # wrong, for however long this query takes - most noticeable right
        # after switching destinations. No spinner/animation, just says so.
        self.lbl_repo.setText("Checking…")
        self.lbl_last.setText("Checking…")
        self.action_refresh.setEnabled(False)
        self._pending_select_latest = select_latest_archive
        self.status_worker = StatusQueryWorker(REPO)
        self.status_worker.finished_result.connect(self._on_status_query_finished)
        self.status_worker.start()

    def _stop_status_query(self):
        """Pre-empts a passive dashboard read so it never delays or
        contends for the repo lock against something the user actually
        asked for. A plain `borg info`/`borg list` still takes Borg's own
        repository lock like any other operation - called from the few
        things that need it for real (start_backup, delete_current_archive,
        change_backup_destination) and from closeEvent(). Fire-and-forget
        here: these callers already use --lock-wait themselves, so a brief
        moment while the cancelled query's subprocess is still exiting
        isn't worth blocking on. closeEvent() is the one caller that DOES
        wait afterward, since it also needs to know the thread is genuinely
        gone before it's safe to let the window close."""
        if hasattr(self, "status_worker") and self.status_worker.isRunning():
            self.status_worker.stop()

    def _on_status_query_finished(self, queried_repo, info, listing, cancelled):
        # not unconditionally True - a backup/delete may have started
        # (that's exactly how a query gets cancelled in the first place,
        # see _stop_status_query()), and Refresh must stay disabled through
        # that regardless of what this now-finished query returned.
        self.action_refresh.setEnabled(not self._repo_op_running)
        if self._status_refresh_pending:
            self._status_refresh_pending = False
            pending_select_latest = self._status_refresh_pending_select_latest
            self._status_refresh_pending_select_latest = False
            QTimer.singleShot(0, lambda: self._start_status_query(pending_select_latest))
        if cancelled:
            # a cancelled query can carry PARTIAL data - e.g. info succeeded
            # before stop() landed, listing was skipped entirely. Applying
            # that would show a real repo as "no archives yet" / clear the
            # archive combo for no actual reason - never trust a cancelled
            # result's data, whatever it happens to contain.
            return
        if queried_repo != REPO:
            # the destination changed while this query was in flight - these
            # results describe a repo that's no longer current, and applying
            # them would show stale/wrong info for a moment. Whatever
            # changed the destination already triggered its own fresh
            # refresh_status() call, so just drop this one.
            return
        # Recovery access shows these as "Verified by Keep" facts.
        self._verified_repo_info = (queried_repo, info, len(listing.get("archives", [])) if listing else None,
                                    datetime.now().astimezone().isoformat(timespec="seconds"))
        self._refresh_activity()  # now that the repository ID is known
        if info:
            stats = info.get("cache", {}).get("stats", {})
            size_gb = stats.get("unique_csize", 0) / (1024**3)
            n_archives = len(listing.get("archives", [])) if listing else "?"
            self.lbl_repo.setText(f"{n_archives} archives, {size_gb:.2f} GB deduplicated")
        else:
            self.lbl_repo.setText("unavailable")
        if listing and listing.get("archives"):
            last = listing["archives"][-1]
            self.lbl_last.setText(friendly_timestamp(last.get("time", "?")))
        else:
            self.lbl_last.setText("no archives yet")
        self._apply_archive_listing(listing, select_latest=self._pending_select_latest)

    def _apply_archive_listing(self, listing, select_latest=False):
        """Pure UI update from an already-fetched Borg listing dict (or
        None) - no Borg subprocess call in here. Two different callers feed
        this: _on_status_query_finished (a passive background fetch) and
        _ensure_archives_available (an explicit-browse-intent fetch that
        already went through the checked/recovery path). Splitting the
        fetch from the apply means neither path ever queries Borg twice for
        the same listing just to populate this combo - an earlier version
        had refresh_status() and refresh_archives() each independently
        listing the same repo, and _ensure_archives_available() triggering
        a third fetch on top of its own.

        Preserves whatever archive was already selected across a refresh by
        default - otherwise every refresh (even just clicking Refresh)
        would silently yank you back to the newest archive while you're
        examining an older one. select_latest=True overrides that (used
        after a backup THIS session just triggered manually, or when
        explicit browse intent just confirmed the repo is reachable - both
        cases where jumping to the newest archive is expected, not
        disruptive)."""
        previously_selected = self.archive_combo.currentText()
        names = [a["name"] for a in reversed(listing.get("archives", []))] if listing else []
        self._archive_times = {a["name"]: a.get("start") or a.get("time") for a in (listing or {}).get("archives", [])}
        self.archive_combo.blockSignals(True)
        self.archive_combo.clear()
        for n in names:
            self.archive_combo.addItem(n)
        if names:
            if select_latest or previously_selected not in names:
                self.archive_combo.setCurrentIndex(0)  # newest - or nothing sensible to preserve
            else:
                self.archive_combo.setCurrentText(previously_selected)
        self.archive_combo.blockSignals(False)
        if hasattr(self, "backup_list_panel"):  # test stand-ins borrow this method without the sidebar
            MainWindow._refresh_backup_list(self)
        if self.mounted_archive and self.mounted_archive not in names:
            self._unmount()  # the mounted archive was deleted from under us
        if names and not previously_selected and self.pages.currentIndex() == 1:
            QTimer.singleShot(0, self._load_visible_archive)

    def _show_first_run_setup(self):
        if CONFIG.get("setup_complete") is not False:
            return
        if self._first_run_dialog is not None and self._first_run_dialog.isVisible():
            self._first_run_dialog.raise_()
            self._first_run_dialog.activateWindow()
            return
        self._first_run_dialog = FirstRunDialog(self)
        self._first_run_dialog.show()

    def _rebuild_source_ui(self):
        """Refresh the consumer folder surfaces after Backup Sources changes."""
        current = self.section_combo.currentText()
        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItem("My Files", f"{MOUNTPOINT}/{HOME_IN_ARCHIVE}")
        for source in _consumer_module.backup_source_entries(CONFIG):
            rel = _consumer_module.archive_path_for_source(source["path"])
            self.section_combo.addItem(source["label"], f"{MOUNTPOINT}/{rel}")
        idx = self.section_combo.findText(current)
        self.section_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.section_combo.blockSignals(False)
        if self.mounted:
            self.folders_picker.populate(build_folder_catalog())
            self.on_section_changed(self.section_combo.currentIndex())
        else:
            self.folders_picker.reset_to_placeholder()

    def configure_backup_sources(self):
        if self._repo_op_running:
            QMessageBox.information(self, "Keep", "Wait for the current backup or delete to finish before changing backup sources.")
            return False
        old_engine = CONFIG.get("backup_engine")
        old_schedule = dict(CONFIG.get("schedule", {}))
        dlg = BackupSourcesDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return False

        self._rebuild_source_ui()
        new_engine = CONFIG.get("backup_engine")
        if old_engine != new_engine:
            schedule = dict(CONFIG.get("schedule", {}))
            active = schedule.get("enabled")
            if active is None:
                active = timer_enabled(schedule.get("timer_unit"))

            # If an upgraded installation switches from its custom script to
            # Keep's built-in engine, the automatic timer must not keep running
            # the OLD script behind the user's back. Migrate an active timer in
            # one transaction; if it is off, just record that future scheduling
            # is Keep-managed and no systemd mutation is needed yet.
            if new_engine == "builtin" and not schedule.get("managed_by_keep"):
                migrated = dict(schedule)
                migrated["managed_by_keep"] = True
                migrated["enabled"] = bool(active)
                migrated["timer_unit"] = "keep-backup.timer"
                if active:
                    ok, message = apply_keep_schedule(migrated)
                    if not ok:
                        CONFIG["backup_engine"] = old_engine
                        CONFIG["schedule"] = old_schedule
                        save_config()
                        QMessageBox.warning(
                            self, "Keep",
                            "Your folder list was saved, but Keep could not safely move the active automatic schedule to the built-in backup engine. "
                            "The existing custom backup script remains in control for now.\n\n" + message,
                        )
                        return True
                else:
                    CONFIG["schedule"] = migrated
                    save_config()
            elif schedule.get("managed_by_keep") and active:
                # The timer is already Keep-managed; changing engine means its
                # ExecStart must change too. Keep the switch transactional.
                ok, message = apply_keep_schedule(schedule)
                if not ok:
                    CONFIG["backup_engine"] = old_engine
                    CONFIG["schedule"] = old_schedule
                    save_config()
                    QMessageBox.warning(
                        self, "Keep",
                        "Your folder list was saved, but Keep could not safely update the active automatic schedule. "
                        "The previous backup engine remains in use.\n\n" + message,
                    )
                    return True

        if CONFIG.get("backup_engine") == "external":
            QMessageBox.information(
                self, "Keep",
                "Your folder list was saved, but this installation is still using its existing custom backup script. "
                "That script continues to control backup scope until you enable Keep's built-in backup engine.",
            )
        return True

    def configure_schedule(self):
        if self._repo_op_running:
            QMessageBox.information(self, "Keep", "Wait for the current backup or delete to finish before changing the automatic schedule.")
            return False
        dlg = ScheduleDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return False
        requested = dlg.schedule_value()
        if requested.get("enabled") and not _consumer_module.destination_configured(CONFIG):
            QMessageBox.warning(
                self, "Keep",
                "Choose a backup destination before turning on Automatic Backups.",
            )
            return False
        ok, message = apply_keep_schedule(requested)
        if ok:
            QMessageBox.information(self, "Keep", message)
        else:
            QMessageBox.warning(
                self, "Keep",
                "Keep could not update the automatic backup schedule. Your existing timer state was not assumed to have changed.\n\n"
                + message,
            )
        self.refresh_status()
        return ok

    def change_backup_destination(self):
        if self._repo_op_running:
            QMessageBox.information(self, "Keep", "A backup or delete is currently in progress - try again once it finishes.")
            return
        if shutil.which("borg") is None:
            QMessageBox.warning(
                self, "Keep",
                "BorgBackup is required before Keep can create or adopt a backup destination. Install BorgBackup, then try again.",
            )
            return
        # a passive status read against the OLD destination shouldn't
        # linger into the switch - also avoids it landing after REPO has
        # moved to the new destination and getting (correctly) discarded
        # as stale anyway, doing pointless work either way
        self._stop_status_query()
        type_dialog = DestinationTypeDialog(self)
        if type_dialog.exec() != QDialog.Accepted or not type_dialog.chosen:
            return
        dest_type = type_dialog.chosen

        folder = QFileDialog.getExistingDirectory(self, "Choose a backup destination folder")
        if not folder:
            return

        default_label = os.path.basename(folder.rstrip("/")) or "Backup Destination"
        label, ok = QInputDialog.getText(
            self, "Keep", "A short name for this destination (shown in the status panel):",
            text=default_label,
        )
        label = label.strip() if ok and label.strip() else default_label

        # Resolve and validate ALL destination metadata (type, UUID,
        # repo_subpath, mount info) BEFORE touching the repo or the shared
        # passphrase file at all - not just before writing the passphrase.
        # A real incident during development: UUID resolution failing after
        # `borg init` + passphrase-write had already happened left the
        # passphrase file holding the NEW repo's passphrase while config
        # still pointed at the OLD repo - the exact "orphaned passphrase"
        # class of bug this whole ordering exists to prevent. Doing the
        # part that can still fail FIRST, with nothing destructive committed
        # yet, means a failure here is a plain no-op: return, nothing
        # touched, try again.
        if dest_type == "removable":
            # tracked by the drive's own filesystem UUID, not this mount
            # path - a USB drive doesn't reliably remount at the same path
            # every time it's plugged back in, so resolve_destination() finds
            # it fresh each time via `findmnt -S UUID=...` instead
            try:
                out = subprocess.run(["findmnt", "-T", folder, "-n", "-o", "UUID,TARGET"], capture_output=True, text=True, timeout=5)
                parts = out.stdout.strip().split(None, 1) if out.returncode == 0 else []
            except Exception:
                parts = []
            if len(parts) < 2 or not parts[0]:
                QMessageBox.warning(
                    self, "Keep",
                    f"Could not determine a filesystem UUID for '{folder}' - it may not be on its "
                    f"own removable filesystem. Destination NOT changed; pick Network Storage or "
                    f"Other Location instead if this isn't actually a removable drive.",
                )
                return
            uuid, target = parts
            subpath = folder[len(target):].strip("/")
            new_dest = {"type": "removable", "label": label, "uuid": uuid, "repo_subpath": subpath}
        elif dest_type == "network":
            mount_check, fstype = folder, None
            details = _destination_module.mount_details(folder)
            if details:
                mount_check, fstype = details
            new_dest = {"type": "network", "label": label, "repo": folder, "mount_check": mount_check, "fstype": fstype}
        else:  # other
            new_dest = {"type": "other", "label": label, "repo": folder}

        if CONFIG.get("backup_engine") != "external":
            source_paths = [e["path"] for e in _consumer_module.backup_source_entries(CONFIG)]
            if CONFIG.get("include_app_data", True):
                source_paths.extend(_consumer_module.app_data_sources(CONFIG, HOME))
            conflict = _consumer_module.source_destination_conflict(folder, _consumer_module.dedupe_paths(source_paths))
            if conflict:
                QMessageBox.warning(
                    self, "Keep",
                    "This destination overlaps a folder selected for backup:\n\n"
                    f"{conflict}\n\nChoose a destination outside the folders being backed up.",
                )
                return

        global _session_passphrase_override

        if looks_like_borg_repo(folder):
            confirmed = QMessageBox.question(
                self, "Keep",
                f"This looks like an existing Borg repository:\n{folder}\n\n"
                f"Use this existing repository with Keep? If it is encrypted, Keep will "
                f"verify access and offer Unlock or key recovery if this computer does not "
                f"already have the right credentials.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if confirmed != QMessageBox.Yes:
                return
            # unmount the OLD repo and drop its session passphrase before
            # switching - otherwise borg_env() would keep using the OLD
            # repo's unlocked passphrase against the NEW repo after
            # set_destination() below, silently ignoring whatever's
            # actually saved for the new one (a reviewer's whole-app sweep
            # found _session_passphrase_override is never cleared anywhere
            # once Unlock sets it, including across a destination switch).
            if not self._unmount():
                QMessageBox.warning(
                    self, "Keep",
                    "Could not unmount the current destination (still in use) - "
                    "try again once whatever's holding it open is done. "
                    "Destination NOT changed.",
                )
                return
            _session_passphrase_override = None
            set_destination(new_dest)
            # Confirming the switch without checking the passphrase actually
            # works here just defers the failure to the next scheduled
            # backup - validate now, while there's still a person here to
            # fix it, reusing the exact same Unlock/Recover chain a failed
            # mount would offer (key_missing -> recover -> retry ->
            # wrong_passphrase -> unlock -> retry - a reviewer's sweep found
            # the first version of this only tried ONE recovery step and
            # trusted it, so a recovered key with a still-wrong passphrase
            # was accepted as a working switch).
            info, stderr = run_borg_json_checked(["info", "--json", REPO])
            if info is None:
                problem = classify_borg_auth_error(stderr)
                if problem == "key_missing" and self._offer_key_recovery(REPO):
                    info, stderr = run_borg_json_checked(["info", "--json", REPO])
                    problem = classify_borg_auth_error(stderr) if info is None else None
                if info is None and problem == "wrong_passphrase" and self._offer_unlock(REPO):
                    info, stderr = run_borg_json_checked(["info", "--json", REPO])
                if info is None:
                    QMessageBox.warning(
                        self, "Keep",
                        f"Backup destination is now:\n{label}\n{folder}\n\n"
                        f"But Keep could not authenticate against it just now"
                        f"{' (' + stderr.strip() + ')' if stderr else ''}. "
                        f"Fix the passphrase before the next scheduled backup runs — "
                        f"Browse or Backup Now will offer to unlock/recover it too.",
                    )
                    self.refresh_status()
                    return
            # Session auth is confirmed working (possibly via _session_passphrase_override,
            # which the SCHEDULED backup script never sees - it only reads
            # PASSFILE directly). Check that too, separately, so the success
            # message doesn't claim the scheduled backup will work when only
            # this session's Browse/Backup Now actually can (a reviewer's
            # sweep found the old message claimed both unconditionally, even
            # right after an Unlock with "Remember" deliberately left off).
            try:
                on_disk_passphrase = Path(PASSFILE).read_text().strip()
            except FileNotFoundError:
                on_disk_passphrase = ""
            persisted_ok, _ = check_passphrase(REPO, on_disk_passphrase)
            if persisted_ok:
                QMessageBox.information(self, "Keep", f"Backup destination is now:\n{label}\n{folder}\n\nKeep and automatic backups can use this destination from now on.")
            else:
                QMessageBox.warning(
                    self, "Keep",
                    f"Backup destination is now:\n{label}\n{folder}\n\n"
                    f"Keep can browse it for the rest of this session, but the "
                    f"passphrase isn't saved to disk, so the next SCHEDULED backup "
                    f"will still fail. Browse this destination and check "
                    f"\"Remember this passphrase on this computer\" when Unlock asks, "
                    f"or set ~/.config/borg/passphrase yourself, before relying on "
                    f"the automatic schedule.",
                )
            self.refresh_status()
            return

        confirmed = QMessageBox.question(
            self, "Keep",
            f"'{folder}' doesn't look like an existing Borg repository.\n\n"
            f"Initialize a brand new Borg repository there? You'll choose whether "
            f"to encrypt it in the next step.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if confirmed != QMessageBox.Yes:
            return
        # Unmount the OLD repo FIRST, before anything about the new one is
        # touched at all - a reviewer's sweep found a real BLOCKER here:
        # this check used to run AFTER borg init + paper-key export, so an
        # unmount failure at that point meant returning with a brand new,
        # fully-initialized, passphrase-protected repo already sitting on
        # disk, but the generated passphrase only ever held in this local
        # variable - never persisted (save_passphrase_atomic() hadn't run
        # yet), never shown to the user (KeyExportDialog hadn't run
        # either), gone the instant this function returned. The warning
        # shown in that path even claimed "its passphrase is safe," which
        # was false. Doing this first instead means an unmount failure is
        # a clean no-op - nothing about the new repo has been created yet,
        # so there is nothing to lose.
        if not self._unmount():
            QMessageBox.warning(
                self, "Keep",
                "Could not unmount the current destination (still in use) - "
                "try again once whatever's holding it open is done. Nothing "
                "was created.",
            )
            return
        _session_passphrase_override = None

        # Encryption is a repository-creation decision, not a toggle that
        # can safely be flipped on an existing repository later. Encrypted
        # stays the recommended/default choice, but consumer Keep no longer
        # forces it when the user deliberately wants an unencrypted target.
        encryption_choice, accepted = QInputDialog.getItem(
            self, "Keep — Backup Encryption", "Protect this backup with encryption:",
            ["Encrypted (recommended)", "No encryption"], 0, False,
        )
        if not accepted:
            return
        encrypted = encryption_choice.startswith("Encrypted")
        # Non-secret metadata for the bundled engine's diagnostic run log.
        # This records only the repository protection MODE chosen here, never
        # the generated passphrase or key material. Existing adopted repos may
        # legitimately have no such field and are logged as existing/unknown.
        new_dest["encryption"] = "encrypted (repokey-blake2)" if encrypted else "none"
        if not encrypted:
            proceed = QMessageBox.question(
                self, "Keep",
                "An unencrypted backup can be read by anyone who can access the repository files. "
                "That may include documents, browser/app profiles, SSH or GPG data, and other private settings.\n\n"
                "Create it without encryption anyway?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if proceed != QMessageBox.Yes:
                return

        # generate + init BEFORE touching the shared passphrase file - if
        # init fails, the OLD repo's passphrase must still be intact,
        # since Keep is still pointed at the old repo at that point.
        # repokey, not keyfile: the key is stored inside the repository, so
        # losing this machine can't strand the backup - the passphrase alone
        # opens it from anywhere. With keyfile the only key copy lived in
        # ~/.config/borg/keys/ on the machine being backed up. Borg 1.x can't
        # convert an existing repo between the two, so this has to be right
        # at creation time.
        passphrase = generate_passphrase() if encrypted else ""
        init_env = os.environ.copy()
        init_env["BORG_PASSPHRASE"] = passphrase
        result = subprocess.run(
            ["borg", "init", f"--encryption={'repokey-blake2' if encrypted else 'none'}", folder],
            env=init_env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            QMessageBox.warning(self, "Keep", f"Could not initialize a repository there:\n{result.stderr}\n\nYour existing repository/passphrase are untouched.")
            return
        export_path = NEW_REPO_KEY_EXPORT_PATH if encrypted else None
        if encrypted:
            export_result = subprocess.run(
                ["borg", "key", "export", "--paper", folder, export_path],
                env=init_env, capture_output=True, text=True,
            )
            if export_result.returncode != 0:
                QMessageBox.warning(
                    self, "Keep",
                    f"Repository was created, but exporting the paper key failed:\n"
                    f"{export_result.stderr}\n\n"
                    f"The key itself is stored inside the repository, so the passphrase "
                    f"still opens it - but you have no spare copy of the key yet in case "
                    f"the repository's copy is ever damaged. Run this yourself before "
                    f"relying on this backup:\n\n"
                    f"borg key export --paper {folder} {export_path}",
                )
        # Commit the passphrase write AND the destination switch together,
        # BEFORE showing any dialog that could stay open indefinitely
        # (KeyExportDialog has no auto-close - "Not Now" or a crash could
        # leave it open, or never closed, for as long as the user likes).
        # The old repo is already safely unmounted by this point (above),
        # so nothing left below this line can orphan the passphrase.
        if encrypted:
            save_passphrase_atomic(passphrase, "new_repository", label)
        set_destination(new_dest)
        if encrypted:
            QMessageBox.information(self, "Keep", f"New encrypted repository created at:\n{folder}")
            KeyExportDialog(passphrase, export_path, parent=self).exec()
        else:
            QMessageBox.information(self, "Keep", f"New unencrypted repository created at:\n{folder}")
        self.refresh_status()

    def edit_excludes(self):
        ExcludeEditorDialog(EXCLUDES_FILE, parent=self).exec()

    def compare_archives(self):
        archives = [self.archive_combo.itemText(i) for i in range(self.archive_combo.count())]
        if len(archives) < 2:
            QMessageBox.information(self, "Keep", "Need at least two archives to compare.")
            return
        CompareArchivesDialog(REPO, archives, self.archive_combo.currentText(), parent=self).exec()

    def delete_current_archive(self):
        app_logging.record("operation.requested", operation="delete_archive")
        if self._repo_op_running:
            # start_backup() only ever disabled ITS OWN button pair, not
            # this one (and vice versa) - a reviewer's whole-app sweep
            # found Delete could still be clicked while a backup was
            # actively running, launching a DeleteWorker against the same
            # repo concurrently. Borg's own locking would serialize the
            # two at the repository level, but both operations independently
            # set/clear the SAME self._repo_op_running flag, so whichever
            # finished first would incorrectly report "nothing running"
            # while the other was still actually going.
            QMessageBox.information(self, "Keep", "A backup or delete is already in progress - try again once it finishes.")
            return
        # a passive status read shouldn't delay or contend for the repo
        # lock against something the user actually asked for
        self._stop_status_query()
        archive = self.archive_combo.currentText()
        if not archive:
            QMessageBox.information(self, "Keep", "No archive selected.")
            return
        total = self.archive_combo.count()
        warning = (
            f"Permanently delete archive:\n\n  {archive}\n\n"
            f"This cannot be undone. Everything only present in this specific "
            f"point-in-time snapshot is gone for good — files unchanged since "
            f"stay safe in your other archives (Borg dedup means most content "
            f"is shared between archives, but anything unique to this one isn't)."
        )
        if total <= 1:
            warning += "\n\n⚠ This is your ONLY remaining archive. Deleting it empties the entire backup history."
        confirmed = QMessageBox.warning(
            self, "Keep — permanently delete this archive?", warning,
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if confirmed != QMessageBox.Yes:
            return

        if not self._unmount():  # can't delete an archive that's currently mounted
            QMessageBox.warning(self, "Keep", "Could not unmount the archive browser (still in use) - try again in a moment.")
            return
        self._repo_op_running = True
        self.btn_delete_archive.setEnabled(False)
        self.archive_combo.setEnabled(False)
        self.btn_backup.setEnabled(False)  # contends for the same repo lock
        self.action_back_up_now.setEnabled(False)
        self._set_repo_actions_enabled(False)
        self.delete_progress.setRange(0, 0)  # indeterminate until a real % arrives
        self.delete_progress.show()
        self.lbl_delete_status.setText("Deleting archive...")
        self.lbl_delete_status.show()
        self.delete_worker = DeleteWorker(REPO, archive)
        self.delete_worker.output.connect(self._on_delete_output)
        self.delete_worker.finished_ok.connect(self._on_delete_finished)
        self.delete_worker.start()

    def _on_delete_output(self, text):
        self.lbl_delete_status.setText(text)
        m = DELETE_COMPACT_PERCENT_RE.search(text)
        if m:
            self.delete_progress.setRange(0, 100)
            self.delete_progress.setValue(int(m.group(1)))

    def _on_delete_finished(self, ok, error_msg):
        app_logging.record("operation.finished", operation="delete_archive", result="success" if ok else "failed")
        self._repo_op_running = False
        self.btn_delete_archive.setEnabled(True)
        self.archive_combo.setEnabled(True)
        self.btn_backup.setEnabled(True)
        self.action_back_up_now.setEnabled(True)
        self._set_repo_actions_enabled(True)
        self.delete_progress.hide()
        self.lbl_delete_status.hide()
        if ok:
            QMessageBox.information(self, "Keep", "Archive deleted.")
        else:
            QMessageBox.warning(self, "Keep", error_msg)
        self.refresh_status()

    # --- backup ---

    def start_backup(self):
        app_logging.record("operation.requested", operation="backup")
        if shutil.which("borg") is None:
            QMessageBox.warning(
                self, "Keep",
                "BorgBackup is required but was not found on this computer. Install BorgBackup, then try again.",
            )
            return
        if self._repo_op_running:
            # symmetric with the same guard in delete_current_archive() -
            # see its comment for why this matters (found via a reviewer's
            # whole-app sweep: neither operation checked for the other
            # already running, so both could end up active at once)
            QMessageBox.information(self, "Keep", "A backup or delete is already in progress - try again once it finishes.")
            return
        if CONFIG.get("backup_engine") != "external":
            has_folder = bool(_consumer_module.backup_source_entries(CONFIG))
            if not has_folder and not CONFIG.get("include_app_data", True):
                QMessageBox.information(
                    self, "Keep",
                    "Nothing is selected for backup yet. Choose Backup > Backup sources… first.",
                )
                return
        # a passive status read shouldn't delay or contend for the repo
        # lock against something the user actually asked for
        self._stop_status_query()
        # defense in depth - btn_backup is already disabled by refresh_status()
        # when the destination is unavailable, but this covers the gap if it
        # became unavailable in between (e.g. a removable drive unplugged
        # after the last refresh) rather than launching a worker that's just
        # going to fail inside the script anyway
        dest = refresh_destination()
        if not dest["available"]:
            QMessageBox.warning(self, "Keep", f"Backup destination unavailable:\n{dest['reason']}.\n\nConnect it and try again.")
            self.refresh_status()
            return
        if CONFIG.get("backup_engine") != "external":
            source_paths = [e["path"] for e in _consumer_module.backup_source_entries(CONFIG)]
            if CONFIG.get("include_app_data", True):
                source_paths.extend(_consumer_module.app_data_sources(CONFIG, HOME))
            source_paths = _consumer_module.dedupe_paths(source_paths)
            conflict = _consumer_module.source_destination_conflict(dest["repo"], source_paths)
            if conflict:
                QMessageBox.warning(
                    self, "Keep",
                    "The backup destination overlaps a folder selected for backup:\n\n"
                    f"{conflict}\n\nChoose a destination outside the folders being backed up, or remove that folder from Backup Sources.",
                )
                return
        # captured BEFORE the unmount just below (which sets self.mounted
        # False on success) - on_backup_finished needs to know whether the
        # user was actually browsing right before clicking Backup Now, to
        # decide whether to remount+rebuild after
        self._was_browsing_before_backup = self.mounted
        # the archive browser below holds the repo lock while mounted, which
        # would otherwise make a backup started from here block on itself -
        # a stuck unmount means that lock is still held, so starting the
        # backup now would just fail waiting for it (or worse, wait the
        # full --lock-wait with nothing ever going to release it)
        if not self._unmount():
            QMessageBox.warning(self, "Keep", "Could not unmount the archive browser (still in use) - try again in a moment.")
            return
        self._repo_op_running = True
        self.btn_backup.setEnabled(False)
        self.action_back_up_now.setEnabled(False)
        self.btn_delete_archive.setEnabled(False)  # contends for the same repo lock
        self.pages.setCurrentIndex(0)  # Keep progress and Stop visible when started from Restore.
        self._set_repo_actions_enabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_stop.setVisible(True)
        # indeterminate until (if) the prescan total arrives - the script's
        # dry-run scan phase itself takes a few seconds with nothing to
        # report yet, and a prescan failure should still let the backup
        # proceed, just without a real percentage (see _on_prescan_total)
        self._prescan_total_bytes = None
        self.backup_progress.setRange(0, 0)
        self.backup_progress.setTextVisible(False)
        self.backup_progress.show()
        self.lbl_progress_detail.show()
        # the script's dry-run prescan phase itself takes a few seconds
        # before any log line arrives at all - set directly here rather
        # than waiting on the worker's first signal, so the stage line
        # never shows genuinely blank
        self.lbl_progress_detail.setText("Preparing backup…")
        self.log_view.clear()
        # Show log is left exactly as the user had it - starting a backup
        # no longer auto-expands it (see log_section's own construction
        # comment for why forcing it open stopped making sense)
        self.worker = BackupWorker()
        self.worker.output.connect(self.log_view.appendPlainText)
        self.worker.progress.connect(self._on_backup_progress)
        self.worker.percent_progress.connect(self._on_backup_percent)
        self.worker.stage.connect(self._on_backup_stage)
        self.worker.prescan_total.connect(self._on_prescan_total)
        self.worker.finished_ok.connect(self.on_backup_finished)
        self.worker.start()
        app_logging.record("operation.started", operation="backup")

    def stop_backup(self):
        app_logging.record("operation.stop_requested", operation="backup")
        if hasattr(self, "worker") and self.worker.isRunning():
            self.btn_stop.setEnabled(False)
            self.log_view.appendPlainText("--- stopping backup (user requested) ---")
            self.lbl_progress_detail.setText("Stopping…")
            self.lbl_progress_detail.setToolTip("")
            self.worker.stop()

    def _on_prescan_total(self, total_bytes):
        self._prescan_total_bytes = total_bytes
        if total_bytes > 0:
            self.backup_progress.setRange(0, 100)
            self.backup_progress.setFormat("%p%")
            self.backup_progress.setTextVisible(True)
            self.backup_progress.setValue(0)
        # total_bytes == 0 means the prescan itself failed (logged as a
        # WARNING by the script) - leave the bar indeterminate, same as if
        # this signal had never fired at all

    def _on_backup_percent(self, percent):
        # Built-in Keep backups deliberately sanitize Borg's per-file progress
        # before it reaches the persistent log. The engine emits one compact
        # percentage checkpoint instead, so the UI stays determinate without
        # retaining users' filenames in diagnostic logs.
        if self._prescan_total_bytes:
            self.backup_progress.setRange(0, 100)
            self.backup_progress.setFormat("%p%")
            self.backup_progress.setTextVisible(True)
            self.backup_progress.setValue(min(99, max(0, int(percent))))
        self.lbl_progress_detail.setText("Backing up…")
        self.lbl_progress_detail.setToolTip("")

    def _on_backup_progress(self, text):
        # the percentage math is unchanged - only the DISPLAYED text
        # changed, from the raw O/C/D/N/path line (redundant with the
        # percentage bar's own numbers) to a short, human "Backing up: X"
        # line, full path in the tooltip
        if self._prescan_total_bytes:
            processed = parse_borg_original_bytes(text)
            if processed is not None:
                # capped below 100 until the run actually finishes - files
                # can change/appear mid-backup, so this is an estimate, and
                # 100% should mean "actually done", not "matched the guess"
                percent = min(99, round(100 * processed / self._prescan_total_bytes))
                self.backup_progress.setValue(percent)
        m = CURRENT_PATH_RE.search(text)
        if m:
            path = m.group(1).strip()
            # short_path() only limits path COMPONENTS, not pixel width - an
            # exceptionally long single filename can still overflow the
            # fixed-width sidebar and clip. Same elidedText() treatment as
            # lbl_dest_status elsewhere in this column.
            prefix = "Backing up: "
            fm = self.lbl_progress_detail.fontMetrics()
            width = self.lbl_progress_detail.width() or 300
            path_width = max(0, width - fm.horizontalAdvance(prefix))
            detail_text = prefix + fm.elidedText(short_path(path), Qt.ElideMiddle, path_width)
            self.lbl_progress_detail.setText(detail_text)
            self.lbl_progress_detail.setToolTip(path)

    def _on_backup_stage(self, text):
        self.lbl_progress_detail.setText(text)
        self.lbl_progress_detail.setToolTip("")

    def on_backup_finished(self, ok):
        app_logging.record("operation.finished", operation="backup", result="success" if ok else "stopped" if self.worker.user_stopped else "failed")
        self._repo_op_running = False
        self.btn_backup.setEnabled(True)
        self.action_back_up_now.setEnabled(True)
        self.btn_delete_archive.setEnabled(True)
        self._set_repo_actions_enabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setVisible(False)
        if ok and self._prescan_total_bytes:
            self.backup_progress.setValue(100)
        self.backup_progress.hide()
        self.lbl_progress_detail.hide()
        if not ok and not self.worker.user_stopped:
            QMessageBox.warning(self, "Keep", "Backup did not finish successfully. Open Show log for details.")
        # a successful manual Backup Now explicitly initiated this, so
        # jumping the archive picker to what it just created is expected,
        # not disruptive (_apply_archive_listing defaults to preserving
        # whatever was already selected - deliberately overridden here, but
        # NOT for a failed run, which created nothing new to jump to)
        self.refresh_status(select_latest_archive=ok)
        if ok and self._was_browsing_before_backup:
            # only remount+rebuild if the user was ALREADY actively
            # browsing (self.mounted True) - mounting unconditionally after
            # every backup, browsing or not, is exactly the "held open for
            # no reason" problem the lazy-mount design exists to avoid.
            # ensure_mounted() already rebuilds both catalogs from scratch
            # whenever it actually remounts (see its own code) - never an
            # incremental append, so a stale/deleted item can't linger.
            self.ensure_mounted()

    # --- browse / restore ---

    def _unmount(self):
        """Returns True if nothing is mounted by the time this returns
        (either it wasn't, or it just got unmounted), False if a real
        mount is still actively held - callers about to start something
        that needs the repo lock free (a new mount, a backup, a delete, a
        destination switch) must check this and abort rather than
        continue on top of a stuck lock. Found missing via a reviewer's
        whole-app sweep: this used to leave self.mounted True on failure
        (correct) but every caller just barrelled ahead regardless."""
        self.unmount_timer.stop()
        if not self.mounted:
            return True
        result = subprocess.run(["borg", "umount", MOUNTPOINT], env=borg_env(), stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True)
        if result.returncode != 0:
            # borg umount can fail (mountpoint busy, a shell cd'd into it,
            # etc.) - fall back to a plain fusermount, which doesn't need
            # borg's own env/lock handling at all
            fallback = subprocess.run(["fusermount", "-u", MOUNTPOINT], capture_output=True, text=True)
            if fallback.returncode != 0:
                # still actually mounted - don't pretend otherwise. Leaving
                # self.mounted True here means the NEXT browse/backup/delete
                # attempt correctly retries the unmount instead of silently
                # believing the repo lock was released when it wasn't
                # (exactly what the idle-unmount timer exists to prevent).
                print(f"warning: failed to unmount {MOUNTPOINT}: {result.stderr.strip() or fallback.stderr.strip()}",
                      file=sys.stderr)
                # this method itself just stopped the timer above (needed
                # to actually attempt the unmount) - restart it on a short
                # retry rather than leaving it stopped forever. Without
                # this, one transient "mount busy" moment permanently
                # disables the idle-unmount safety net for the rest of
                # this Keep session (nothing else ever restarts it except
                # real user interaction via _touch_activity()), which could
                # hold the repo lock indefinitely and block the 4am
                # scheduled backup - found via a reviewer's whole-app sweep.
                self.unmount_timer.start(60 * 1000)
                return False
        self.mounted = False
        if getattr(self, "_browse_operation_lock", None):
            self._browse_operation_lock.release()
            self._browse_operation_lock = None
        self.mounted_archive = None
        self.mount_coordinator.closed()
        # tiles from the archive that was just unmounted must not stay
        # visible/clickable describing something that may no longer be
        # what's mounted next time - see reset_to_placeholder()'s docstring
        self.apps_picker.reset_to_placeholder()
        self.folders_picker.reset_to_placeholder()
        # same reasoning for the Advanced raw browser - QFileSystemModel
        # DOES eventually notice the underlying mountpoint went empty on
        # its own (confirmed empirically: a real mount/unmount cycle
        # self-corrects after one event-loop cycle), but not synchronously
        # - there's a real, if brief, window where the tree still shows
        # the old mount's rows as if they were still there and selectable.
        # Re-pointing at the SAME path forces an immediate re-scan instead
        # of waiting on the file watcher's own timing, and clearing the
        # selection means restore_selected() can't act on stale rows
        # regardless of what's still visually painted in that window.
        self.tree.clearSelection()
        self.fs_model.setRootPath(MOUNTPOINT)
        self.tree.setRootIndex(self.fs_model.index(MOUNTPOINT))
        return True

    def _touch_activity(self):
        """Call after any interaction that implies the user is still actively
        browsing, so the inactivity auto-unmount doesn't fire mid-use."""
        self.unmount_timer.start(3 * 60 * 1000)

    @property
    def _opening_archive(self):
        return self.mount_coordinator.busy

    def ensure_mounted(self):
        app_logging.record("operation.requested", operation="open_archive")
        result = self.mount_coordinator.open(self._ensure_mounted_impl)
        app_logging.record("operation.finished", operation="open_archive", result="success" if result else "unavailable")
        return result

    def _ensure_mounted_impl(self):
        """Mount the currently selected archive on explicit browse/restore
        intent - safe to call repeatedly, a no-op if nothing changed. This
        is the ONLY place a mount is created. Keep starts with no Borg mount
        held; this is called by the Browse button, a tab switch, an archive
        selection, or immediately before a restore reads from it - never
        just from startup or a status refresh happening in the background.
        An empty archive_combo is usually just "nothing backed up yet,"
        but it can also mean listing the repo itself failed for the exact
        same recoverable reasons a mount can fail (missing key, wrong
        passphrase) - the passive background status refresh (StatusQueryWorker)
        swallows that failure into a plain empty list with no way to tell
        the two apart, by design (see its docstring - a background refresh
        must never pop a recovery dialog on its own). _ensure_archives_available()
        re-checks with the
        stderr-preserving variant and offers recovery before giving up, so
        explicit browse intent on a broken repo doesn't just silently do
        nothing (a real gap found via live restore testing on a genuinely
        fresh machine - the recovery dialogs existed but were unreachable,
        since there was never anything to select in order to trigger a
        mount attempt in the first place).

        Returns True if the requested archive ends up genuinely mounted,
        False otherwise - callers that are about to READ from the mount
        (the two restore methods) must check this and abort rather than
        assume a call here always succeeds. A reviewer's sweep found that
        Safe Restore could report "Restored 0" as if that were a normal
        empty result, and Direct Restore could silently do nothing, when
        the real cause was a cancelled recovery dialog, a failed mount, or
        a backup/delete holding the lock at that exact moment."""
        archive = self.archive_combo.currentText()
        if not archive:
            if not self._ensure_archives_available():
                return False
            archive = self.archive_combo.currentText()
            if not archive:
                return False
        if self.mounted and self.mounted_archive == archive:
            self._touch_activity()
            return True
        if not REPO:
            # the archive combo can still show a stale selection from before
            # a removable destination got unplugged mid-session - refresh
            # rather than attempting a mount against a now-nonexistent path
            self.refresh_status()
            return False
        if self._repo_op_running:
            # a backup or delete/compact is actively holding the repo lock
            # right now (switching tabs or archives mid-operation is a
            # completely normal thing to do) - a mount attempt would just
            # fail with a lock-timeout error, which reads as a real problem
            # when it's really just timing. The progress bar/status label
            # already visible for that operation explains why browsing isn't
            # refreshing; the next tab/archive interaction after it finishes
            # will mount normally.
            return False
        if not self._unmount():
            QMessageBox.warning(self, "Keep", "Could not switch archives - the current one is still in use. Try again in a moment.")
            return False
        os.makedirs(MOUNTPOINT, exist_ok=True)
        # defensively clear any stale mount left by a prior crashed/killed instance,
        # since self.mounted only tracks this instance's own knowledge
        subprocess.run(["fusermount", "-u", MOUNTPOINT], capture_output=True)
        ok, stderr = self.mount_coordinator.mount_with_recovery(
            lambda: self._attempt_mount(archive), classify_borg_auth_error,
            lambda: self._offer_key_recovery(REPO), lambda: self._offer_unlock(REPO),
        )
        if not ok:
            if not ok and "does not exist" in stderr:
                # a real, live-use report: the archive named here can
                # genuinely no longer exist by the time this runs, even
                # though it was a valid selection moments ago - `archive`
                # was read from the combo once, at the top of this method,
                # but ANOTHER backup (the scheduled timer, or just Keep
                # itself running one that finished while this browse was
                # in flight) can complete and prune it in between. Keep's
                # own _repo_op_running guard only knows about operations
                # THIS instance started, so it can't prevent this - the
                # archive list itself is now known-stale, so refresh it
                # rather than leaving a dead selection behind, and say
                # plainly what likely happened instead of showing Borg's
                # bare error with no context.
                self.refresh_status()
                QMessageBox.warning(
                    self, "Keep",
                    f"'{archive}' is no longer available - most likely a more "
                    f"recent backup's cleanup step removed it while you were "
                    f"browsing.\n\nThe archive list has been refreshed; pick "
                    f"another archive and try again.",
                )
                return False
            if not ok:
                QMessageBox.warning(self, "Keep", f"Could not mount archive:\n{stderr}")
                return False
        self.mounted = True
        self.mounted_archive = archive
        self._touch_activity()
        self.fs_model.setRootPath(MOUNTPOINT)
        self.on_section_changed(self.section_combo.currentIndex())
        self.apps_picker.populate(build_app_catalog())
        self.folders_picker.populate(build_folder_catalog())
        return True

    def _attempt_mount(self, archive):
        try:
            lock = RepositoryLock(REPO)
            lock.acquire()
        except Exception as exc:
            return False, str(exc)
        try:
            result = MainWindow._attempt_mount_uncoordinated(self, archive)
            if result[0]:
                self._browse_operation_lock = lock
            else:
                lock.release()
            return result
        except BaseException:
            lock.release()
            raise

    def _attempt_mount_uncoordinated(self, archive):
        # stdin=DEVNULL: an empty/wrong BORG_PASSPHRASE makes Borg fall
        # back to an interactive prompt rather than failing cleanly - see
        # run_borg_json_checked() for the full story. Without this, a
        # fresh-machine mount attempt can hang here instead of reaching
        # classify_borg_auth_error() at all. timeout=60 for the same
        # general reason run_borg_json_checked() has one - a genuine
        # network/FUSE/repository stall (NAS drops mid-mount, say)
        # shouldn't be able to freeze this indefinitely either, a
        # different failure mode than the interactive-prompt one but
        # deserving the same bound.
        progress = ArchiveOpenProgress("Opening backup…", "", 0, 0, self)
        progress.setWindowTitle("Restore")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        worker = ArchiveMountWorker(["borg", "mount", f"{REPO}::{archive}", MOUNTPOINT], borg_env(), self)
        worker.finished.connect(progress.accept)
        QTimer.singleShot(0, worker.start)
        progress.exec()
        # The modal progress keeps repository-changing controls unavailable.
        worker.wait()
        result = worker.result
        worker.deleteLater()
        progress.deleteLater()
        return result

    def _ensure_archives_available(self):
        """Called by ensure_mounted() when the archive combo is empty -
        could genuinely be an empty repo, but could also be the same
        key_missing/wrong_passphrase failure a mount can hit, just
        surfacing one step earlier (listing archives needs to decrypt the
        repo same as mounting does). Offers the same recovery chain as a
        failed mount, then retries the listing. Returns True if the repo
        is confirmed reachable (populating archive_combo via
        _apply_archive_listing() either way), False if not - a real
        key/passphrase problem that recovery couldn't resolve, or nothing
        reachable at all (no REPO). A reachable-but-genuinely-empty repo
        (no archives yet, not an error) still returns True, but also
        switches both pickers' placeholder to say so explicitly - a
        second "Browse This Archive" click can't fix an empty repo the
        way it can a cancelled recovery dialog, so it shouldn't look like
        the same "try again" state.

        Deliberately uses run_borg_json_checked() and the same key/
        passphrase recovery chain a failed mount offers - this is explicit
        browse intent, unlike the passive dashboard refresh in
        StatusQueryWorker, which must never pop a recovery dialog on its
        own. Applies the listing it already fetched directly via
        _apply_archive_listing() rather than triggering a second, redundant
        fetch through refresh_status()/StatusQueryWorker."""
        if not REPO:
            return False
        listing, stderr = run_borg_json_checked(["list", "--json", REPO])
        if listing is None:
            problem = classify_borg_auth_error(stderr)
            if problem == "key_missing" and self._offer_key_recovery(REPO):
                listing, stderr = run_borg_json_checked(["list", "--json", REPO])
                problem = classify_borg_auth_error(stderr) if listing is None else None
            if listing is None and problem == "wrong_passphrase" and self._offer_unlock(REPO):
                listing, stderr = run_borg_json_checked(["list", "--json", REPO])
        if listing is None:
            return False
        self._apply_archive_listing(listing, select_latest=True)
        if not listing.get("archives"):
            self.apps_picker.show_empty_repo_message()
            self.folders_picker.show_empty_repo_message()
        return True

    def _offer_key_recovery(self, repo):
        """Borg found no local key for this repo at all. Offer the two
        disaster-recovery paths instead of just failing outright. Returns
        True if a key now looks like it's in place and worth retrying the
        mount against, False if the user cancelled or the import failed."""
        label = DEST_STATUS.get("label") or repo
        dlg = KeyRecoveryDialog(label, self)
        if dlg.exec() != QDialog.Accepted:
            return False
        if dlg.choice == KeyRecoveryDialog.IMPORT_FILE:
            path, _ = QFileDialog.getOpenFileName(self, "Select Exported Key File")
            if not path:
                return False
            ok, err = import_key_file(repo, path)
            if not ok:
                QMessageBox.warning(self, "Keep", f"Could not import that key file:\n{err}")
                return False
            return True
        if dlg.choice == KeyRecoveryDialog.PAPER_KEY:
            wizard = PaperKeyWizardDialog(repo, label, self)
            wizard.exec()
            return wizard.recovered
        return False

    def _offer_unlock(self, repo):
        """Borg found a key but the passphrase on record doesn't open it -
        ask for the right one, verifying each guess directly against the
        repo (cheap `borg info`, not a full mount) before accepting it, so
        a typo is caught here rather than surfacing as the same failure one
        step later. Returns True if a working passphrase is now set for
        this session, False if the user cancelled."""
        global _session_passphrase_override
        label = DEST_STATUS.get("label") or repo
        dlg = UnlockBackupDialog(label, self)
        while dlg.exec() == QDialog.Accepted:
            candidate = dlg.passphrase
            remember = dlg.remember
            dlg.passphrase = None
            ok, _ = check_passphrase(repo, candidate)
            if ok:
                _session_passphrase_override = candidate
                if remember:
                    save_passphrase_atomic(candidate, "user_remembered_unlock", label)
                candidate = None
                return True
            candidate = None
            dlg.show_error("That passphrase didn't work - check for typos and try again.")
        return False

    def _on_workspace_changed(self, index):
        if index == 1 and self.archive_combo.count() and not self._repo_op_running:
            QTimer.singleShot(0, self._load_visible_archive)

    def _load_visible_archive(self):
        if self.pages.currentIndex() != 1 or self._repo_op_running or getattr(self, "_opening_archive", False):
            return
        self.ensure_mounted()

    def on_archive_changed(self, index):
        # this only ever fires from a real user click on the combo - the
        # combo is repopulated with signals blocked (see _apply_archive_listing),
        # so picking a different archive here always means genuine browse
        # intent, whether or not something was already mounted.
        self.ensure_mounted()

    def on_tab_changed(self, index):
        self.ensure_mounted()

    def on_section_changed(self, index):
        if not self.mounted:
            return
        self._touch_activity()
        path = self.section_combo.currentData()
        self.fs_model.setRootPath(path)
        self.tree.setRootIndex(self.fs_model.index(path))

    def friendly_archive(self, name):
        """'Today at 8:51 AM' for an archive; its Borg name if the time is unknown."""
        when = getattr(self, "_archive_times", {}).get(name)
        return friendly_timestamp(when) if when else name

    def _refresh_backup_list(self):
        destination = CONFIG.get("destination", {}).get("label") or "Backup"
        rows = []
        for i in range(self.archive_combo.count()):
            name = self.archive_combo.itemText(i)
            rows.append((self.friendly_archive(name), destination + (" · newest" if i == 0 else ""), name))
        self.backup_list_panel.set_backups(rows, self.archive_combo.currentIndex())
        retention = CONFIG.get("retention", {})
        self.backup_list_panel.set_caption(
            f"{len(rows)} backup{'s' if len(rows) != 1 else ''} stored. Up to "
            f"{retention.get('daily', 7)} daily, {retention.get('weekly', 4)} weekly and "
            f"{retention.get('monthly', 6)} monthly backups are kept." if rows else "")
        self._on_archive_selection_shown()

    def _on_archive_selection_shown(self, _index=None):
        index = self.archive_combo.currentIndex()
        self.backup_list_panel.select_row(index)
        name = self.archive_combo.currentText()
        destination = CONFIG.get("destination", {}).get("label") or "your backup"
        self.archive_friendly.setText(
            f"Restoring from <b>{self.friendly_archive(name)}</b> · {destination}" if name
            else "Choose a backup in the list on the left.")

    def review_restore(self):
        """Toolbar primary on the Restore view: gather what the current tab has
        checked or selected, show exactly what goes where, then restore into a
        new Keep-Restored folder (or one the person picks)."""
        self.pages.setCurrentIndex(1)
        tab = self.tabs.currentIndex()
        picker = {0: self.apps_picker, 1: self.folders_picker}.get(tab)
        if picker is not None:
            names = [item.text() for item in picker._all_items()]
        else:
            names = [os.path.basename(self.fs_model.filePath(index)) or self.fs_model.filePath(index)
                     for index in self.tree.selectionModel().selectedRows()]
        if not names:
            QMessageBox.information(self, "Keep", "Check the items you want to restore first." if picker is not None
                                    else "Select one or more files or folders first (Ctrl+click or Shift+click for several).")
            return
        archive = self.friendly_archive(self.archive_combo.currentText()) if self.archive_combo.currentText() else "this backup"
        dialog = ReviewRestoreDialog(names, archive, new_restore_folder(), HOME, self)
        if dialog.exec() != QDialog.Accepted:
            return
        if picker is not None:
            picker.restore_checked_safe(dest_dir=dialog.destination)
        else:
            self.restore_selected(dest_dir=dialog.destination)

    def restore_selected(self, dest_dir=None):
        app_logging.record("operation.requested", operation="file_restore")
        rows = self.tree.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Keep", "Select one or more files/folders in the browser first (Ctrl+click or Shift+click for several).")
            return
        # capture plain relative-path strings BEFORE ensure_mounted() - same
        # reasoning as restore_checked_safe(): a remount rebuilds fs_model's
        # state (ensure_mounted() re-populates the OTHER tabs too), so
        # holding onto these QModelIndex objects across that call risks
        # acting on stale/invalidated indexes. Paths relative to MOUNTPOINT
        # stay valid regardless of what the model does underneath - the
        # archive's own content doesn't change on a remount of the SAME
        # archive, only the model's in-memory state does.
        rel_paths = [os.path.relpath(self.fs_model.filePath(idx), MOUNTPOINT) for idx in rows]
        # the curated restore paths (Apps/Projects) already re-verify the
        # mount is actually still there immediately before reading from it -
        # this tab was missing that entirely, so an idle-unmount between
        # selecting files and clicking Restore could silently read (or fail
        # to read) from a stale/gone mount.
        if not self.ensure_mounted():
            QMessageBox.warning(self, "Keep", "Could not access the archive - nothing was restored.")
            return
        sources = [f"{MOUNTPOINT}/{rel_path}" for rel_path in rel_paths]
        if not dest_dir:  # also False when called straight from a button's clicked(bool)
            dest_dir = QFileDialog.getExistingDirectory(self, f"Restore {len(sources)} item(s) to folder (a copy, not the live location)")
        if not dest_dir:
            return
        done, failed = [], []
        for src in sources:
            # lexists(), not exists() - exists() follows symlinks, so a
            # broken symlink (a legitimate archived object whose target
            # happens to be gone) would read as "no longer exists" and
            # get skipped instead of restored as the symlink it is
            if not os.path.lexists(src):
                failed.append(f"{os.path.basename(src)}: no longer exists in this archive")
                continue
            dest = os.path.join(dest_dir, os.path.basename(src))
            try:
                # copy_item() checks islink() before isdir() - isdir()
                # follows symlinks, so a symlink-to-a-directory here would
                # otherwise be dereferenced into a real directory copy
                # instead of preserving that it was a symlink at all (the
                # same bug class already fixed for the curated restore
                # paths - this tab was missing that fix too).
                copy_item(src, dest)
                done.append(dest)
            except Exception as e:
                failed.append(f"{os.path.basename(src)}: {e}")
        app_logging.record("operation.finished", operation="file_restore", result="partial_failure" if failed else "success", count=len(done), failed=len(failed))
        show_restore_results(self, done, failed, dest_dir)



if __name__ == "__main__":
    app_logging.install_qt_handler()
    app = QApplication(sys.argv)
    # Ties Keep's windows to keep.desktop (the menu entry / the .deb's
    # launcher). On Wayland this is the app_id the taskbar uses to find the
    # icon - without it the window shows up as a generic "python3" app.
    app.setDesktopFileName("keep")
    win = MainWindow()
    win.show()
    app_logging.record("application.ready")
    exit_code = app.exec()
    app_logging.record("application.exited", exit_code=exit_code)
    sys.exit(exit_code)

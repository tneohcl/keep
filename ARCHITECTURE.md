# Keep architecture and maintenance guide

Updated: 2026-09-23. Current status: personal beta; consumer production release requirements are tracked in [CONSUMER_RELEASE_INSTRUCTIONS.md](CONSUMER_RELEASE_INSTRUCTIONS.md). This document describes implemented architecture; planned work is identified separately.

## Module ownership

| Module | Responsibility |
| --- | --- |
| `main.py` | Application composition, configuration/session context, backup and restore handlers, credential dialogs and Borg adapters |
| `keep_ui/backup_panel.py` | Fixed backup controls and their callbacks |
| `keep_ui/status_page.py` | Summary, activity, progress, details and log surfaces |
| `keep_ui/restore_page.py` | Archive selector, restore tabs and advanced file browser |
| `keep_ui/restore_picker.py` | Checkable catalog, view modes, placeholders and selection summaries |
| `keep_ui/application_selection.py` | Backup application/settings choices; configuration and home are explicit inputs |
| `keep_ui/checkable_list.py` | Native checkable list with full-cell mouse toggling and explicit cell sizes for wrapped labels |
| `keep_ui/restore_results.py` | Shared bounded restore-results dialog with scrollable plain-text paths and errors |
| `keep_ui/common.py` | Shared disclosure widget |
| `app_logging.py` | Private per-launch diagnostics, bounded rotation, safe exception hooks |
| `mount_service.py` | Re-entry guard, open/recovery states and bounded authentication retry policy; no Qt imports |
| `keep_ui/mount_progress.py` | Background mount worker and its modal Qt progress adapter |
| `applications.py` | Known data mappings, desktop/Flatpak installation detection, display names and selected source resolution |
| `consumer.py`, `destination.py`, `keep_backup.py` | Configuration/schedules, destination resolution and unattended backup execution |
| `style.qss`, `themes.py`, `theming.py` | Shared visual rules, color tokens and live palette integration |

## Boundaries

UI modules never import `main`. They receive input data, a picker factory or controller callbacks. They do not launch Borg or modify configuration directly. `CONTROL_NAMES` explicitly identifies view-owned controls used by existing application handlers; avoid automatic attribute copying. New view-local behavior belongs in its view, not in the entry point.

`ItemPicker` in `main.py` subclasses `RestorePicker` and owns safe/direct restore operations. Selection is exclusively checked state, never highlighted rows. Copy plain path data before ensuring the mount: opening another archive can rebuild and delete the old Qt items.

`MountCoordinator.open()` guards the entire opening transaction, including recovery dialogs. States describe that transaction: idle, opening, recovering, ready or failed. Mounted archive identity remains in the application session. Never clear mounted state before an unmount succeeds. Recovery callbacks return approval/success; each key/passphrase recovery receives at most one mount retry. Passive status refresh must not prompt for credentials.

Initialize the theme controller on the application before applying its stylesheet: applying it may synchronously emit palette-change events. MainWindow explicitly applies the theme after composing its views; startup must not depend on incidental widget events.

## Runtime flows

- **Interactive backup:** `MainWindow` starts the backup worker, receives progress and completion signals, and updates Status. Prescan byte totals use Qt `qint64` to avoid overflow above 2 GiB.
- **Scheduled backup:** the user systemd `keep-backup.timer` activates `keep-backup.service`, which invokes `keep_backup.py --config <path>` without starting Qt. The built-in engine resolves sources and destination, checks repository access, and runs Borg create, prune and compact with detailed logging. Legacy external-script configurations remain supported.
- **Archive browsing:** the controller requests a mount through `MountCoordinator`; `ArchiveMountWorker` performs the blocking mount in a Qt worker thread. Catalogs describe the mounted archive; an installation snapshot controls default visibility of application entries.
- **Restore:** curated Apps/Folders use `ItemPicker`; Advanced uses the file-browser handler. Safe restore writes a review copy under `~/Keep-Restored`. Direct restore first copies existing content to an undo location, then replaces the selected live paths. All three result presentations use `RestoreResultsDialog`.

## Restore and selection invariants

- `copy_item()` preserves both top-level and nested symbolic links. Never follow archived runtime links such as Discord's `SingletonLock`, `SingletonCookie` and `SingletonSocket` during copying. Genuine copy failures must remain visible.
- Direct replacement preserves links and handles an existing destination symlink before copying. Its undo copy uses the same link-preserving helper as safe restore. Do not describe direct restore as an atomic transaction; partial failures remain possible.
- Known native settings and the same application's Flatpak data can share one catalog entry while retaining their original source/target pairs. Cross-install conversion alternatives remain separate; conflicting live destinations must not be silently merged.
- Data discovery and installation detection serve different purposes. Old app data remains in archives; default picker visibility uses executable-backed desktop metadata and active Flatpak deployments. The uninstalled-data toggle reveals older data without deleting it.
- Select all and Clear selection affect visible choices. Explicit bulk selection in the backup dialog turns off broad application-data coverage; hidden saved choices are preserved.
- Clicking an icon or label toggles its checkbox. Native checkbox handling is retained without toggling twice. Keyboard behavior remains provided by Qt.
- Icon cells use explicit item sizes so short labels cannot constrain the wrapping width of longer names. Hover styling must keep the same border, padding and geometry as the normal state.
- Restore results stay in a bounded, resizable dialog. Full paths and errors are selectable plain text in a scrolling field; failures appear before successful entries.
- Exit routes through the existing window close handler, including active-operation checks. Use Qt's standard Quit shortcut with a Ctrl+Q fallback.

## Persistence, deployment and diagnostics

| Data | Current location or behavior |
| --- | --- |
| User configuration | `~/.config/keep/config.json`, respecting XDG configuration location and `KEEP_CONFIG_PATH` override |
| Borg credentials and keys | Existing Borg configuration locations; keep separate from application files and preserve during migration/uninstall |
| Session diagnostics | `$XDG_STATE_HOME/keep/logs`, defaulting to `~/.local/state/keep/logs`; private files, bounded rotation and retention |
| Detailed backup logs | `~/.local/state/borg-logs` by default; separate from session diagnostics |
| Scheduled units | `~/.config/systemd/user/keep-backup.service` and `keep-backup.timer` |
| Debian application files | `/usr/lib/keep`; launcher `/usr/bin/keep` |
| Source checkout launch | `launch.sh`; may create a private `.venv` for Qt dependencies |

Session logging starts before GUI/configuration imports for a real application launch. It records lifecycle, operation outcomes and limited exception metadata without raw exception messages, credentials or full command environments. Backup logs and restore-result details have a different purpose and can contain user paths; do not assume session-log redaction applies to them.

Source-mode service files contain absolute paths. Moving or renaming a checkout does **not** automatically repair an existing service. Update the exact path, reload the user systemd manager, and verify the timer and next run. Linux path case matters. The user's move from Desktop to `/mnt/data/tools/keep` exposed this limitation; it is not a portable application default.

Existing migration code can disable a configured legacy timer, but broader discovery of conflicting jobs is unfinished. A separate old job was observed pruning a Keep-created archive in the same repository. Legacy backup/check scripts are not Keep runtime dependencies; Borg itself and repository credentials are dependencies. Removing legacy check timers does not create replacement integrity checks in Keep.

## Removed compatibility code

The hidden Status/Restore buttons, hidden icon/list toggle buttons, hidden Refresh button, hidden direct-restore button, old setup/options layouts, custom tile painter, palette-derived local QSS helpers and Projects picker alias have been removed. Use QTabWidget, the view combo, Refresh QAction, direct-restore QAction and `folders_picker` respectively.

## Validation

Portable:

```sh
python -m unittest -q test_applications test_release_safety test_theming test_mount_service test_app_logging test_restore_copy test_backup_locking
python test_gui_smoke.py
```

Full Debian/Borg/FUSE suite and installed-package smoke check:

```sh
docker build -f packaging/Dockerfile.test -t keep-validation .
docker run --rm --device /dev/fuse --cap-add SYS_ADMIN --security-opt apparmor=unconfined keep-validation
```

Run only the package check in a disposable container:

```sh
docker run --rm keep-validation bash packaging/test-package.sh
```

The package probe runs outside the checkout and imports `/usr/lib/keep/main.py`, so source files cannot hide packaging omissions. The test image contains all dependencies; no user repository or credentials are mounted into it. Shell scripts must retain LF endings. The legacy recovery harness uses throwaway encrypted repositories; current visual behavior is covered by `test_gui_smoke.py` and `test_theming.py`, replacing assertions about deleted controls/delegates.

`test_restore_copy.py` exercises real nested broken links, links to Unix sockets, directory links and unsuppressed copy errors; those filesystem checks run on Linux and skip on Windows. GUI smoke checks cover full-cell selection, stable hover geometry, bulk selection, catalog grouping, bounded result dialogs and both themes. Native desktop interaction and accessibility still need real-machine acceptance.

The package allowlist includes Python modules, `keep_ui/*.py`, QSS and SVG assets, including `check.svg` and `chevron-down.svg`. Add required assets to packaging when introducing them. Refresh `dist/SHA256SUMS` after rebuilding a distributable package. A successful old validation log is not evidence for subsequently changed behavior.

## Consumer-release architecture priorities (planned)

The complete acceptance criteria are in [CONSUMER_RELEASE_INSTRUCTIONS.md](CONSUMER_RELEASE_INSTRUCTIONS.md); validation history and limitations are in [RELEASE_READINESS.md](RELEASE_READINESS.md).

1. Prove clean-machine recovery and interrupted-operation behavior before expanding features.
2. Make installed execution paths stable and legacy schedule conflicts discoverable; validate upgrades, missed schedules and logged-out behavior.
3. Add explicit integrity-check orchestration and backup-health reporting. Routine and deep checks need distinct costs, progress and results.
4. Establish versioned builds, supported environments, credential/privacy review and consumer recovery documentation.
5. Complete a controlled release-candidate trial. Personal-machine success and container tests do not establish general consumer readiness.

These are release requirements, not claims that the corresponding features are implemented or validated.

## Remaining boundaries

`main.py` still contains backend adapters, catalog construction and credential/restore workflows. These are existing behavior, not new UI responsibilities. Extract those into focused services when changing them; do not move them through implicit global imports. Container tests do not establish accessibility, host desktop integration or unattended systemd behavior on every distribution.

## Scheduled-backup contention policy (0.9.1)

The built-in engine explicitly passes `--lock-wait 300` to repository `info`, prescan `create --dry-run`, archive creation, prune and compact. The policy is centralized in `keep_backup.REPOSITORY_LOCK_WAIT_SECONDS`. Each stage logs its bounded wait policy; Borg's failure diagnostics remain in the job log when a lock cannot be acquired.

The access-check subprocess permits 360 seconds total (300 for waiting plus 60 for execution); prescan permits 420 seconds (300 plus 120). Create, prune and compact retain their existing execution/cancellation behavior. A failed access check aborts before creation; failed creation still skips prune and compact. Prescan remains best-effort and may fall back to local metadata estimation.

This is a per-command wait, not an overall operation deadline or cross-process Keep lock. It does not guarantee success while another session keeps a mount open indefinitely, and it never breaks an active lock. `test_backup_locking.py` exercises real Borg contention, release before the deadline, exhaustion and process-group cancellation using isolated repositories.

## CLI foundation (0.9.2)

`cli.py` provides `status`, `archives`, `doctor`, `logs`, and routine/deep `check` without importing Qt. `borg_ops.py` accepts explicit execution environments; the GUI retains responsibility for resolving session credentials before calling shared JSON-query and authentication-classification helpers. `operation_lock.py` coordinates same-user repository operations across CLI, GUI mount/delete and the built-in scheduled engine. Borg locks still protect access from other clients and users. See [CLI.md](CLI.md) for output, exit-code, cancellation and credential contracts and current limits.

`recovery_test.py` implements the recorded **Test recovery…** check without Qt. With an empty `BORG_KEYS_DIR` and a fresh security directory, it opens the repository using only the passphrase the person types (passed to Borg on a pipe, never in argv or the environment). It restores one randomly chosen file of 16 MB or less from the latest archive into a private temporary folder, compares its size and SHA-256 with the archive's own record, then deletes the folder. `~/.local/state/keep/recovery-test.json` records only the outcome, repository, archive name and date, and only for outcomes that say something about recoverability. The Status page's *Recovery tested* fact reads it and asks for a new test after six months. `keep_ui/recovery_test_dialog.py` runs the test under `RepositoryLock`, so it never contends with a backup, delete or open archive.

`keep_ui/recovery_access.py` (sidebar *Recovery access*, Backup ▸ Recovery access…) keeps two kinds of facts apart. *Verified by Keep* comes from the last status query: destination reachable, encryption mode (repokey or keyfile), whether the saved passphrase opens the repository, and whether a key file was exported with Keep. *Only you can confirm* is three dated checkboxes (passphrase in a password manager, printed kit stored away, destination reachable without this computer), saved per repository in `recovery-access.json` and asked again after a year. Keep never records where the copies are. **Export key file…** runs `borg key export` off the UI thread and writes the file with mode 0600.

Integrity outcomes are persisted separately in XDG state and displayed in Status details for the matching repository after refresh. Maintenance timers, restore CLI and interactive prompts remain future work. `keep-cli` is packaged alongside `keep`; only the command execution path is Qt-independent, not the combined package dependencies.

Network destination detection uses structured `findmnt` JSON instead of splitting multi-line tabular output. An autofs trigger alone does not establish the backing filesystem. The exact legacy `autofs` plus mount-path/CIFS-or-NFS malformed setting is interpreted compatibly only when the actual filesystem still matches. No fallback to local disk is introduced.

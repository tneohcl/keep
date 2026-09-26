# Keep validation — 2026-09-16

## Scope

This report replaces earlier running notes. The UI refactor and maintainability work are complete. The final combined Debian 13 validation completed with exit code 0; desktop acceptance remains separate.

## Architecture changes

- Dedicated Backup, Status and Restore views, backup application selector, restore picker and shared disclosure widget in `keep_ui`.
- Explicit view inputs/callbacks and control exports; UI components do not import the application entry point.
- Qt-independent mount coordinator with explicit opening/recovery/result states, re-entry protection and bounded authentication retries.
- Removed old hidden controls, duplicate layout construction, unused custom tile delegate and local palette/QSS helpers.
- Shared theme installation is explicitly initialized and safe against synchronous palette-event re-entry.
- Architecture guide, Linux test runner, isolated installed-package smoke test and CI workflow included.

## Bugs found during validation

- Newer Borg emits lowercase passphrase errors; authentication classification is now case-insensitive.
- Theme installation could recursively construct controllers during a palette event; the controller is registered before applying QSS.
- Eliding long progress text could remove the space after its label; only the path is now elided.
- The Debian runtime lacked the Qt SVG image plugin; `qt6-svg-plugins` is now an explicit dependency, verified by decoding the packaged chevron.
- Legacy shell line endings and recovery-test fixtures were updated for Linux and the current UI/backup-engine behavior.

## Evidence

- 24 portable unit tests and expanded offscreen Qt smoke checks pass on Windows.
- The Debian 13 run passes the real Borg recovery harness, including encrypted repositories, key/passphrase recovery, FUSE mounts, safe restore, symlink preservation, cancellation, repository-operation guards and stale-result handling.
- Final combined Linux run: exit code 0. All 24 unit tests, consumer checks, Borg/FUSE recovery checks and installed-package checks passed.
- Debian package `dist/keep-backup_0.9.0_all.deb` built, installed and opened from installed files; the packaged SVG decoded successfully. `dist/SHA256SUMS` records its checksum.
- CI workflow added for future pushes/pull requests; it has not been dispatched remotely in this session.
- `linux-validation.log` and `package-validation.log` contain detailed results.

## Remaining desktop acceptance

Container tests do not establish accessibility/screen-reader behavior, real KDE theme changes, unattended systemd timer execution, removable-drive unplug/reconnect behavior or network-loss behavior on a user's machine.

2026-09-26: `packaging/acceptance.sh` now covers the engine side of the loss scenarios on a real machine (Fedora 44, Borg 1.4.5), all passing. It simulates a network destination (a separate filesystem bind-mounted like a NAS share) that isn't mounted at start, disappears mid-backup, is killed mid-archive, or fills up. In every case the run fails, is never reported as a success, and the Status page reports it as failed; after recovery the next backup succeeds and `borg check` passes. Nothing is written into an unmounted mount folder. Failed runs now log a plain-language `Reason:` (destination full, disconnected, permission, passphrase, in use), taken from the failed Borg stage's own output. The Status page shows it, and says a run whose log just stops "stopped before finishing" once its log has been quiet for a minute and Keep's repository lock is free. The acceptance checks verify these against real Borg output. Still open: a real USB unplug (removable destinations resolve by UUID through findmnt, not exercised here) and a CIFS server that hangs instead of erroring. Unattended timer execution is confirmed on TITAN-i: nightly 04:00 runs succeed. App-path coverage also varies by installed application/version. These remain deployment acceptance checks; no bug-free or universal-distribution claim is made.

See `ARCHITECTURE.md` for module ownership and repeatable validation commands.

## Large-backup progress correction

Changed `BackupWorker.prescan_total` from Qt signed 32-bit `int` to `qint64`. Queued worker-to-GUI regression coverage verifies 2^31−1, 2^31, the reported 21,404,408,200 bytes, 2^40 and 2^63−1 without truncation. The receiver runs on the GUI thread and the status handler retains the exact total. Passed the expanded Qt smoke check on Windows and Debian, all 24 portable tests, and rebuilt/installed the corrected Debian package. The full Borg suite result above predates this narrowly scoped signal correction; it was not repeated for this change.

## Installed-app selection correction

Backup and Apps & data Restore now default to installed apps, using executable-backed desktop metadata and active Flatpak deployments rather than leftover data directories. Both offer an explicit “Show data for uninstalled apps” option. Archive contents remain available; source resolution and previously saved backup choices are preserved. Switching from broad coverage to per-app selection no longer preselects uninstalled app leftovers. Installation snapshots are taken per picker load, not per tile. Passed 27 unit tests and GUI smoke checks on Windows and Debian, including both filter toggles, plus package installation. Full Borg recovery checks were not rerun for this filtering-only change.

## About dialog sizing

The About dialog now fits its visible content using a Qt fixed-size layout constraint. Collapsing System Information restores the compact dimensions. Three consecutive expand/collapse cycles pass on Windows and Debian; the updated package builds and passes its installed-file smoke check.

## Automatic session logging

Each application entry-point launch now opens a private rotating session log before UI/config imports. Logs include lifecycle and operation events, warning categories and exception frame locations without raw messages, paths, credentials or locals. About shows the log directory. Added tests for actual entry-point startup/exit, retention/rotation, Linux 0600 permissions and secret exclusion. All 30 unit tests and GUI smoke checks pass on Windows and Debian; the rebuilt package passes installation checks. Existing backup-job logs are unchanged. The full Borg suite was not repeated for this diagnostic-only change.

## Nested application-data links during restore

Safe Restore, Advanced Restore and the Direct Restore undo copy now preserve nested symbolic links rather than following them. This fixes Electron/Chromium runtime links such as Discord SingletonLock, SingletonCookie and SingletonSocket that reference unavailable runtime targets. Direct replacement already preserved nested links. Regression fixtures check broken links, a link to a live Unix socket, an external directory link and byte-identical settings data; actual unsupported socket objects still report copy errors.

Validation after this correction: all 32 unit tests, Qt smoke checks, consumer feature checks, the complete real Borg/FUSE recovery harness and installed Debian package checks passed in Debian 13 (combined run exit 0; `linux-validation.log`). Rebuilt the package and refreshed `dist/SHA256SUMS`.

## Restore results and selection usability

All three restore modes now share a screen-sized result dialog with scrollable, selectable plain-text details. Errors appear before successes and partial-copy failures stay explicit. Restore Apps/Folders and backup Applications/System Settings offer Select all and Clear selection; bulk selection affects visible choices only, and choosing explicit backup items turns off broad coverage. Known native app settings and the same app's Flatpak archive data share one restore entry with all original path mappings retained. Cross-install conversion alternatives remain separate. GUI regression coverage includes 300 results with long errors, filtered bulk selection, and GIMP/Krita native-plus-Flatpak catalog fixtures.

Validation: Windows and Debian GUI checks passed, including the final backup bulk-selection assertions. The combined Debian run passed all 32 unit tests, consumer checks, the full real Borg/FUSE recovery harness and installation checks (exit 0). The final package was rebuilt, installation-tested and checksummed.

## 0.9.1: bounded repository lock waits

The unattended engine now uses explicit five-minute lock waits for access checks, prescan, create, prune and compact. Access-check and prescan deadlines include this allowance. This addresses short-lived contention without forcing unmounts or breaking locks. Long-lived mounts and broader GUI/CLI/scheduler coordination remain separate work. Added real Borg contention tests for release, exhaustion and cancellation. The distributed package version advances to 0.9.1; CLI implementation is not included in this fix.

0.9.1 validation: combined Debian run exited 0 with 35 unit tests, GUI smoke checks, consumer checks, full real Borg/FUSE recovery and installed-package validation. A subsequent consumer harness run also passed with assertions requiring `--lock-wait 300` on every repository stage. Evidence: `linux-validation.log` and `lock-policy-validation.log`. No live user repository was used.

## CLI foundation and automount parser (2026-09-23, working tree)

Added the Qt-free CLI commands and same-user repository coordination described in CLI.md. An intermediate full Linux run passed after repairing lock ownership cleanup; its log is cli-validation.log. Subsequent shared query extraction, installed CLI probe and the reported autofs/CIFS parsing correction have not yet received a final full Linux run: Docker Desktop's Linux engine was unavailable during final verification. Do not treat the existing 0.9.2 package as containing those later changes.

Current Windows verification: 42 tests discovered, 35 passed and 7 Linux-specific tests skipped; GUI smoke passed. Mount fixtures cover stacked autofs/CIFS, autofs-only rejection and compatibility with the exact malformed stored filesystem value without accepting local ext4. CLI-focused Linux testing before the final edits covered actual archive listing, routine/deep checks, cancellation persistence and cross-process contention. A final Linux suite/package rebuild remains required before distributing this working tree as a completed 0.9.2 release.

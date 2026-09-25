# Keep Consumer Preview — progress/logging fix

This preview intentionally ships **without `config.json`** so a fresh extraction enters Keep's first-run setup instead of inheriting the developer workstation's repository/script settings.

For a true fresh-install test, extract this ZIP into a **new directory**. If you unpack it over an older Keep folder, that folder's existing `config.json` will remain on disk and Keep will correctly treat it as an upgrade rather than a fresh install.

## Authoritative backup log

Keep's built-in `keep_backup.py` now opens the per-run log before reading configuration and records:

- built-in engine, host, Python and Borg version;
- config path;
- destination type/name and repository path;
- repository protection mode when Keep created that repository;
- selected source paths and whether application data is enabled;
- retention policy and exclude-file state;
- where the repository credential came from (session / saved file / none), **never the credential value**;
- a repository-authentication preflight before prescan;
- prescan failures/warnings;
- Borg warnings/errors and final statistics for `borg create`, plus complete output for `borg prune` and `borg compact`;
- sanitized create-progress checkpoints (percentage/bytes only; normal per-file paths are not persisted);
- each Borg stage's exit code;
- unexpected Python tracebacks;
- the final Keep backup exit code.

Successful prescan file-by-file output is intentionally discarded after it is used to calculate the total because it can contain millions of private paths; prescan errors and warnings are retained. Borg 1.4.x writes this dry-run listing to stderr, which Keep now handles correctly.

Logs live at:

```text
~/.local/state/borg-logs/backup-*.log
```

**Show Log** reads this same per-run log.


## Fixes from the 2026-09-15 real 28.91 GB test

- **Prescan percentage fixed for Borg 1.4.x.** Keep now reads dry-run list records from either stderr or stdout, so the prescan denominator no longer incorrectly reports `0 bytes` on Borg 1.4.5.
- **Normal logs are compact/private.** Dry-run path listings are not persisted, and `borg create --progress` filenames are consumed in memory and replaced by at most one sanitized percentage checkpoint per percentage point. Warnings/errors and final Borg statistics remain in the log.
- **Retention warning removed.** `borg prune --prefix` was replaced with Borg's current `--glob-archives '<keep-prefix>*'` selector. Retention remains scoped to Keep-created archives only.

## Authentication behavior

The built-in engine now runs `borg info` before the expensive prescan. A wrong/missing passphrase therefore fails immediately and the actual Borg error is retained in the run log.

## Validation performed in the build environment

- Python syntax/compile checks: passed.
- Dependency-free consumer feature checks: passed, including authoritative logging, auth-before-prescan, Borg stage output/exit-code capture, and secret-not-logged assertions.
- `launch.sh` syntax check: passed.

The build environment does not provide PySide6, so the existing full Qt GUI regression suite still needs to be run on the Linux/PySide6 test machine before treating this preview as a release candidate.


## First-run Python GUI dependency

This source/preview package does not bundle Qt itself. `./launch.sh` now checks for a working PySide6 interpreter and, if none is available, creates a private `.venv` inside the Keep folder and installs `PySide6` from `requirements.txt`. On Debian/Ubuntu, `python3-venv` must be available. This is a preview convenience; a normal consumer package should bundle its runtime.

## 2026-09-15 real-backup follow-up

A real Borg 1.4.5 run exposed three consumer-engine issues that are fixed in this preview:

- Borg return code 1 is now treated correctly as "completed with warnings" rather than a failed/no-archive backup. Retention and compact continue, while Keep surfaces a warning verdict.
- Keep now hard-excludes the entire local Borg client-state directories (`~/.config/borg`, `~/.cache/borg`, `~/.local/share/borg`) so Borg does not back up its own mutable security metadata and trigger self-generated "file changed while we backed it up" warnings.
- Prescan path resolution is more tolerant and has a privacy-safe local metadata fallback, so an unresolvable Borg dry-run path list no longer becomes a bogus 0-byte progress estimate.

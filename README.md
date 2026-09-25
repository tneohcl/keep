# Keep

**Development status:** not yet approved for deployment. See [release readiness](RELEASE_READINESS.md) for validation evidence and outstanding Linux gates.

Configuration now lives at `$XDG_CONFIG_HOME/keep/config.json` (default `~/.config/keep/config.json`). `KEEP_CONFIG_PATH` overrides it for development/testing. A genuine app-local legacy `config.json` is copied once when user configuration is absent; the original remains untouched. Package upgrades preserve user configuration.

The log area includes **Copy Summary**, which copies selected operational results without source/repository paths or raw error output. Open the full log for diagnostic details. Missing explicitly selected folders now stop backup; reconnect the source or remove it from the folder selection.

A consumer-oriented PySide6 front end for [BorgBackup](https://borgbackup.readthedocs.io/): choose what to protect, where the backup lives, whether automatic backups are on, and restore apps/files without knowing Borg syntax or archive-internal paths.

The product model is intentionally simple:

- **What** — user-selected folders plus optional application data/settings.
- **Where** — a network share, removable drive, or another local/mounted location chosen by the user.
- **When** — manual **Back Up Now** and an optional systemd-backed daily/weekly schedule controlled from Keep.
- **Protection** — new repositories can be encrypted (recommended) or deliberately created without encryption.

The restore side keeps Keep's original safety model: **Restore Safely** lands in a review folder, **Restore Directly…** has explicit overwrite confirmation and a pre-restore copy, and **Advanced** remains a raw archive browser for anything the friendly catalogs do not expose.

## Consumer setup

### Application selection and fixed control panel

The fixed left panel now holds folders, applications/settings, destination, scheduling and Back Up Now. Status and Restore occupy the right workspace.

**Applications & settings** opens a filterable checklist. Keep groups recognized native and Flatpak paths for Firefox, Thunderbird, Krita, VLC, GIMP and OBS, and lists detected Flatpak IDs, curated settings and unrecognized data folders. Select an item to inspect its exact paths. These are initial path mappings, not a guarantee of complete support for every version or installation format; custom data outside the displayed paths requires an explicit folder selection.

Existing configurations retain broad application coverage. To choose individual apps, turn off **Include all application data and settings**, check the desired entries, and save. The built-in backup engine consumes that selection. External-script configurations must switch to the built-in engine first. Broad home/app-data folder selections that bypass individual choices are rejected with an explanation rather than silently including unchecked data. Missing optional app paths are omitted; existing archives remain unchanged.

The main window starts on **Overview**, showing backup health, last/next backup and destination. **Restore** is a separate view with backup selection and safe recovery controls. Compare and Delete are in **Manage Backup**. Use Ctrl+1/Ctrl+2 to switch views; Ctrl+L opens the overview log, and F5 refreshes status.

Normal users should not need to edit `config.json` by hand. The **Backup** menu contains:

- **Backup Sources...** — add/remove/rename arbitrary folders, decide whether to include application data/settings, and choose Keep-managed backups versus an existing custom script.
- **Automatic Backups...** — turn scheduling on/off and choose daily or weekly + time. Keep uses a per-user system timer underneath, so backups continue even when the GUI is closed.
- **Change Destination...** — choose the target location and adopt or create a Borg repository. New repos ask whether to use encryption; encrypted is the recommended default.
- **Edit Backup Excludes...** — edit the exclude file used by the backup engine.

Fresh configurations use the bundled `keep_backup.py` engine. Existing installations that already have a proven `backup_script` remain in compatibility mode until the user explicitly enables **Use Keep's built-in backup engine (recommended)** — this prevents an upgrade from silently replacing a working production backup pipeline.

On a brand-new install, Keep can launch without any `config.json`. A small first-run setup window links directly to **Backup Sources**, **Backup Destination**, and **Automatic Backups**. Scheduling is optional; choosing a destination completes the required setup.

## Configuration schema

The UI owns the common settings, but `config.json` remains the portable source of truth:

| Field | What it is |
|---|---|
| `destination` | Where your Borg repository lives and how to tell if it's currently reachable — see below, it's a small object, not a single path |
| `backup_engine` | `"builtin"` for Keep-managed consumer backups, or `"external"` to preserve an existing custom script |
| `archive_prefix` | Keep-specific archive namespace used to ensure retention pruning never sweeps unrelated archives in an adopted repository |
| `setup_complete` | First-run setup state; a configured destination is the required completion point, while scheduling may remain off |
| `backup_script` | Compatibility-mode script when `backup_engine` is `"external"` (`~` is expanded) |
| `venv_python` | Optional: path to a Python interpreter with PySide6 installed, used by `launch.sh`. Omit it to just use `python3` on `PATH` |
| `backup_sources` | User-selected folders, stored as `{label, path}` objects and shown in the **Folders** restore tab |
| `include_app_data` | Whether Keep-managed backups also include standard native/XDG data, Flatpak sandboxes, and configured app/system entries |
| `schedule` | Automatic backup state/frequency/time and the systemd user timer Keep manages |
| `retention` | Keep-managed Borg prune retention (defaults: 7 daily / 4 weekly / 6 monthly) |
| `project_dir` / `project_exclude` | Legacy compatibility fields; no longer the consumer UI model |
| `curated_items` | `[label, [paths relative to $HOME], icon-theme-name, category]` entries — only shown if the path actually has real content in the archive being browsed (an empty leftover directory doesn't count). `category` is one of `"applications"`, `"personal"`, or `"system"` — which picker section it appears under |
| `cross_install_apps` | Apps you know have moved (or might move) between a native package and a Flatpak — see below |
| `extra_flatpak_paths` | Flatpak apps whose sandbox also writes real data *outside* `~/.var/app` (paths relative to `$HOME`) |
| `native_process_names` | `{"entry label": ["process", "names"]}` — for Direct Restore's "this app is currently running" warning, which otherwise only catches Flatpak apps (`flatpak ps` lists everything running by appid; there's no equivalent single query for native processes, so this is opt-in per entry). Use the actual binary name (`ps`/`pgrep` match against it exactly, and Linux truncates it to 15 characters - `digikam`, `qbittorrent`, not a long descriptive string) |

Everything else — your username, actual home directory, currently installed Flatpaks, and removable-drive mount path — is detected at runtime, not hardcoded.

### Backup destination

Keep is a Borg front-end, not something that should only think in terms of "the NAS" — `destination` supports three types, and the same `destination.py` resolution logic (shared between Keep and the backup script, so both agree on what "available" means) checks whichever one you've configured. **Whichever type, if it's not available, the backup aborts rather than silently falling back to writing somewhere else** — check the log / Keep's status panel for why.

**Network storage** (NAS, mapped network drive) — tracked by its mount path, since it's expected to always be there:
```json
"destination": {
  "type": "network",
  "label": "Home NAS",
  "repo": "/mnt/nas/backups/my-computer",
  "mount_check": "/mnt/nas",
  "fstype": "cifs"
}
```
`mount_check` can be a parent of `repo` (useful if the repo folder itself might not exist yet on a fresh init) — Keep checks that path is currently a mountpoint. `fstype` is optional; if set, the mount must match it exactly (catches "mounted, but as the wrong thing" - e.g. a stale local directory sitting where the network share should be).

**Removable / USB drive** — tracked by the filesystem's own UUID, not a mount path, since a USB drive doesn't reliably get the same path every time it's plugged back in:
```json
"destination": {
  "type": "removable",
  "label": "Samsung T7",
  "uuid": "1234-5678-ABCD",
  "repo_subpath": "Keep/my-computer"
}
```
Keep resolves the UUID to wherever it's *currently* mounted (via `findmnt -S UUID=...`) each time it checks, and joins that with `repo_subpath` to get the real repo path for that session. Find a drive's UUID with `lsblk -o NAME,UUID,MOUNTPOINT` while it's plugged in — or just use Keep's "Change Destination..." flow, which captures it automatically from whatever folder you pick.

**Other location** — any other local or already-mounted folder (a second internal disk, for example). No mount-type assumptions, just needs to exist:
```json
"destination": {
  "type": "other",
  "label": "Second disk",
  "repo": "/mnt/backup-disk/Keep/my-computer"
}
```

Changing destinations through Keep's UI ("Change Destination...") walks through picking one of these three types, then a folder, then adopting or initializing a repo there exactly as before — it just also asks for a short display label, and for a removable drive, captures the UUID automatically rather than you needing to look it up.

For a newly-created repository, Keep asks whether to use encryption. **Encrypted is recommended**. If you deliberately choose no encryption, Keep warns that anyone with access to the repository can read its contents. Keep-managed backups also hard-exclude Borg's own passphrase/key files, temporary key exports, Keep restore output, and its backup logs. The selected backup source must not contain the repository (or vice versa); Keep blocks that configuration at backup time instead of recursively backing up the backup.

Keep-managed archive retention is scoped to archives created under Keep's own prefix. This is important when adopting a repository that may already contain older or unrelated Borg archives: automatic pruning does not treat those unrelated archives as Keep's retention candidates.

Multiple saved destination *profiles* (e.g. Home NAS on a daily schedule, a USB drive for occasional/manual backups) aren't supported yet — there's one active destination at a time, changed via "Change Destination...". A real second-copy disaster-recovery strategy (beyond the 1-2 of the [3-2-1 rule](https://en.wikipedia.org/wiki/Backup#Storage) this currently covers) would need that, but it's future work, not something to assume exists.

### Cross-install apps

If an app might exist as either a native (apt) package or a Flatpak on your system — and especially if you've ever migrated one to the other — add it to `cross_install_apps`:

```json
"qBittorrent": {"native_dpkg": "qbittorrent", "flatpak_appid": "org.qbittorrent.qBittorrent", "xdg_folder": "qBittorrent"}
```

`xdg_folder` is the folder name both the native app and Flatpak's redirected XDG dirs use identically under `.config/` and `.local/share/` — true for most well-behaved apps, since that's exactly what Flatpak's sandboxing relies on. Keep checks the archive for *either* location and restores to whichever one is *currently* installed on your live system — regardless of which form the backup was actually made from.

### Adding a native (apt) app that isn't a curated tile yet

Flatpak apps get auto-discovered (Keep can enumerate what's installed via `flatpak list` and match archive paths under `.var/app/<id>` to them). Native apps have no equivalent "list what's installed and where its data lives" convention to hook into, so they aren't auto-discovered the same way — but they don't need special support either. Just add the app's config path to `curated_items` like anything else:

```json
["Firefox", [".mozilla/firefox"], "firefox", "applications"]
```

It'll show up as a tile whenever that path exists in the archive being browsed. Until then it's still reachable via the Advanced tab's raw file browser — curated tiles are a shortcut, not the only way in.

## Running it

```bash
./launch.sh
```

`launch.sh` first reuses any working Python/PySide6 interpreter already configured. On a fresh source/preview install where PySide6 is unavailable, it creates a private `.venv` beside Keep and installs the GUI dependency from `requirements.txt`. This keeps the system Python untouched. A packaged consumer release should bundle the runtime so end users do not need this bootstrap step.

### Backup logs

Every Keep-managed backup creates one authoritative per-run log under `~/.local/state/borg-logs/backup-*.log`. **Show Log** reads that same log. The log starts before configuration is parsed and records non-secret context (engine, host, Borg version, destination/repository, sources, retention and credential source), repository authentication, prescan diagnostics, Borg warnings/errors and final statistics, each stage's exit code, and unexpected Python tracebacks. Passphrases and key contents are never logged.

Keep validates repository access before the prescan, so a wrong/missing passphrase fails quickly instead of spending time scanning first. Successful prescan file-by-file output is used for the size estimate but never persisted. During `borg create`, per-file progress is likewise consumed in memory and represented in the log by sanitized percentage/byte checkpoints, so routine diagnostic logs do not expose tens of thousands of filenames. Prescan/create errors and warnings are retained.

A **Help** menu in the top-left covers the same ground as this file, in-app: what Safe vs Direct restore do, what "Remember this passphrase" means, and where the disaster-recovery runbook lives. **About Keep** shows the currently active destination, repo path, config file, and Borg's own reported version — useful for sanity-checking which build/destination is actually live.

## Restore, two speeds

- **Restore Safely** — no destination picker, no confirmation needed: lands in a fresh timestamped folder under `~/Keep-Restored/`, alongside a note of where each item's real live path would be. Never touches anything live.
- **Restore Directly…** — auto-targets the real live path and writes there, after a confirmation listing exactly what gets overwritten. Whatever was there gets copied to `~/Keep-Restored/Before-Direct-Restore/<timestamp>/` first, so there's a local undo path — but the app itself won't know its files changed underneath it, and if it's still running it may rewrite them itself on exit (Keep warns if it detects that).

## Unlocking a backup that won't open

On a normal day, browsing/restoring stays completely silent about secrets — Keep already has a working key and passphrase, so nothing extra ever appears. This only kicks in on explicit browse/restore intent, when Borg genuinely can't authenticate — either while listing archives or while mounting the selected one — and Keep looks at *why*:

- **Wrong passphrase, key present** (e.g. restoring on a different machine than the one that normally holds `~/.config/borg/passphrase`) → an **Unlock Backup** prompt. Each guess is checked against the repo directly (`borg info`) before being accepted, so a typo is caught immediately. "Remember this passphrase on this computer" is unchecked by default — only tick it if this machine should keep using it going forward.
- **No local key at all** (a fresh or reinstalled machine, or restoring somewhere that's never held this repo's key) → a **Backup Key Not Found** dialog, with two ways back in:
  - **Import Key File** — the plain key export from `borg key export <repo> <file>`, if you have one.
  - **Recover from Paper Key** — pastes the printed paper key (from `KeyExportDialog` at repo-init time, or `borg key export --paper`) and reconstructs the key by actually driving Borg's own interactive `--paper` import, line by line, with per-line checksum feedback if something was mistyped.

If key recovery succeeds but the passphrase still doesn't match, Keep chains straight into the Unlock prompt next — no need to fail all the way out and start over.

Neither dialog ever shows a passphrase or reconstructed key back to you once it's been used, and both clear their own input fields right after submitting. This is a recovery path, not a shortcut — the paper key only ever shows up here, never as a routine part of restoring.

## Shared desktop theme

Keep uses `style.qss` for shared surfaces, tabs, fields, buttons and item views, `themes.py` for light/dark color tokens, and `theming.py` for loading and live Qt palette updates. The system accent is read from Qt; primary-action text chooses black or white for contrast. Widget roles (`primary`, `secondary`, `error`) replace local color overrides. Keep geometry shared across palettes and add reusable rules here when introducing controls.

Portable verification: `python -m unittest -q test_applications test_release_safety test_theming test_mount_service` and `python test_gui_smoke.py`. Screenshots use synthetic data and do not verify real Borg operations.

## Maintenance and release checks

See [ARCHITECTURE.md](ARCHITECTURE.md) for module boundaries, extension rules and the full Linux/Borg/package validation commands.

## Application session logs

Every launch writes a private session log under `$XDG_STATE_HOME/keep/logs` (normally `~/.local/state/keep/logs`). About → System Information shows this directory. Logs record startup, readiness, operation requests/results, warnings and exception types/stack locations, and normal exit. They exclude raw exception messages, backend output, environment values, passwords, keys and local variables. Each session rotates at 2 MiB with two backups; the newest 20 sessions are retained. Backup-job logs remain in `~/.local/state/borg-logs`. An abrupt process kill may leave a session without an exit record. Logging failures do not stop backup operations.

## Command-line interface (0.9.2)

See [CLI.md](CLI.md). From the complete updated source checkout:

```sh
python3 cli.py status
python3 cli.py archives --json
python3 cli.py doctor
python3 cli.py logs
python3 cli.py check
python3 cli.py check --deep
```

These commands do not require PySide6. The Debian package adds `keep-cli` while retaining `keep` for the GUI. Update the full source checkout when testing this release: the GUI and engine now also depend on `borg_ops.py` and `operation_lock.py`. Preserve local configuration and Linux `.venv` when transferring source files.

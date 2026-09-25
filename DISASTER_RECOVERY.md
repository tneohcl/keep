# Keep disaster recovery — restore without the Keep GUI

Keep is a convenience layer over BorgBackup. Your backups do **not** depend on Keep being available: a fresh Linux installation can recover directly with the Borg command line.

Store these separately from the computer being backed up:

- the location of the Borg repository;
- credentials needed to reach the destination (for example NAS credentials);
- if the repository is encrypted, the Borg key/paper key and repository passphrase.

Do not keep the only recovery key inside the backup it unlocks.

## 1. Install BorgBackup

Use your distribution's package manager:

```bash
sudo dnf install borgbackup    # Fedora
sudo apt install borgbackup    # Debian/Ubuntu
```

If the repository is on a NAS or removable disk, connect/mount that destination first.

## 2. Recover the key if the repository is encrypted

Repositories Keep creates now use **repokey** mode: the key is stored inside the repository, so only the passphrase is needed and you can skip to setting it below. Check with `borg info /path/to/repository` — it shows `repokey` or `key file`.

Older **keyfile** repositories (and a repokey repository whose stored key is damaged) need the key you saved separately. If this machine already has the correct Borg key, skip this step. Otherwise import it:

```bash
borg key import /path/to/repository /path/to/exported-key.txt
```

For a paper key, use `borg key import --paper /path/to/repository` and type it line by line, including the `id:` line. Each line ends in a 2-character checksum, so typos are caught per line. The last line is shorter than the others; that is normal.

Set the passphrase for the current terminal session without putting it in shell history:

```bash
read -rsp 'Borg passphrase: ' BORG_PASSPHRASE
export BORG_PASSPHRASE
echo
```

Then verify access:

```bash
borg list /path/to/repository
```

## 3. Identify the archive to restore

```bash
borg list --short /path/to/repository
```

Pick the archive you want. The newest archive is not always the right one if you are recovering an older version of a file.

## 4. Restore to staging first

Avoid extracting directly over a newly-installed system until you have inspected the result.

```bash
mkdir -p ~/keep-recovery-staging
cd ~/keep-recovery-staging
borg extract /path/to/repository::ARCHIVE_NAME
```

Borg recreates the archived absolute paths without their leading `/` beneath the current directory. Inspect the files, then copy only what you want back to the live system with `rsync`, your file manager, or another tool you trust.

## 5. After recovery

- reinstall applications separately as needed;
- restore application data only after the relevant application is installed and closed;
- check ownership/permissions if restoring onto a machine with a different user/UID;
- securely remove any temporary key-export file after import;
- unset the shell passphrase when finished:

```bash
unset BORG_PASSPHRASE
```

Keep's **Review restore…** workflow follows the same principle: restore to a review location first, then decide what should replace live data.

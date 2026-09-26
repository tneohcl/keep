# Keep as a Flatpak

Build and install for the current user (needs `org.flatpak.Builder` from Flathub):

```sh
flatpak run org.flatpak.Builder --user --install --force-clean \
    --install-deps-from=flathub build-flatpak packaging/flatpak/io.github.tneohcl.Keep.yml
flatpak run io.github.tneohcl.Keep
```

Build from a folder under your home or `/mnt`, not `/tmp`: the Builder is itself
a Flatpak and has a private `/tmp`.

## What it needs, and why

A backup app reads nearly everything, so like Vorta and Pika Backup it's only
lightly sandboxed:

- `--filesystem=host`, plus `~/.var/app` and `xdg-data/flatpak:ro` (which "host" leaves out),
  to back up folders, other apps' data and Flatpak permission overrides, and to reach the
  destination. Without `xdg-data/flatpak`, the "Flatpak App Permissions" item
  (`~/.local/share/flatpak/overrides`) is invisible to the app and was silently skipped.
- `host-os:ro` and `/var/lib/flatpak:ro`, so Keep can tell which apps are installed: the
  sandbox's `/usr` is the runtime's, so the host's launchers and executables are read under
  `/run/host` and system Flatpaks under `/var/lib/flatpak` (`host.system_path`, `host.which`,
  `host.data_dirs`). Without them the app chooser saw only per-user Flatpaks.
- `--device=all` for `/dev/fuse` (browsing a backup mounts it with `borg mount`).
- `--talk-name=org.freedesktop.Flatpak`, so `host.py` can run `systemctl --user`
  (the schedule), `flatpak` (installed apps), `findmnt` (drives) and `fusermount`
  on the host. Archives are mounted by the host's `fusermount3` via
  `dependencies/fusermount-wrapper.sh`, at `$XDG_RUNTIME_DIR/app/io.github.tneohcl.Keep/`,
  which the host and the app see alike.

## One setup, shared with a source install

`keep.sh` points `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` and `XDG_DATA_HOME`
at the usual home folders. So the Flatpak uses the same `~/.config/keep/config.json`,
recovery records, logs and Borg cache/keys as a source checkout. Saving the schedule from
the Flatpak rewrites `~/.config/systemd/user/keep-backup.service` to run
`/usr/bin/flatpak run --command=keep-backup io.github.tneohcl.Keep`.

## Credits

- Borg, pyfuse3, libfuse and the host wrappers: Vorta's Flathub recipes (`dependencies/README.md`).
- Icon: `drive-harddisk` from KDE's Breeze icons (LGPL-3.0-or-later).

These build recipes come from Vorta's Flathub packaging
(https://github.com/flathub/com.borgbase.Vorta, `dependencies/`), which builds
the same Borg 1.4.5 stack for a Qt app on the KDE runtime:

- `libfuse.json`, `python3-pkgconfig.json`, `pyfuse3.json`: FUSE support for `borg mount`
- `borgbackup.json`: BorgBackup 1.4.5 and its Python dependencies
- `host-command-wrapper.json`, `fusermount-wrapper.sh`, `umount-wrapper.sh`:
  `fusermount3`/`umount` inside the sandbox run the host's through
  `flatpak-spawn --host`, forwarding the FUSE file descriptor, so archives
  can be mounted for browsing

Keep's one change: the wrappers pass `--directory=/` to `flatpak-spawn`, which otherwise runs
the host command in the caller's working directory and fails when that exists only in the
sandbox (for example `/app/share/keep`).

Update them from there when Borg or libfuse is updated.

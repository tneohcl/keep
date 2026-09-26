#!/bin/sh
# Keep uses the same folders as a source install: its config and state, its
# logs, and Borg's cache, keys and security data. So the Flatpak and a source
# checkout share one setup, and the host's systemd timer (which runs
# `flatpak run --command=keep-backup`) sees the same configuration.
export XDG_CONFIG_HOME="$HOME/.config" XDG_STATE_HOME="$HOME/.local/state"
export XDG_CACHE_HOME="$HOME/.cache" XDG_DATA_HOME="$HOME/.local/share"
case "$(basename "$0")" in
    keep-backup) exec python3 /app/share/keep/keep_backup.py "$@" ;;
    keep-cli) exec python3 /app/share/keep/cli.py "$@" ;;
    *) exec python3 /app/share/keep/main.py "$@" ;;
esac

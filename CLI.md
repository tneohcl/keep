# Keep CLI milestone 1

`keep-cli` is an additive executable; `keep` still launches the desktop app.
From a source checkout use `python3 cli.py`. Neither command execution nor its
imports require Qt. The combined Debian package still depends on Qt for the GUI.

## Commands

```sh
keep-cli --config ~/.config/keep/config.json status --json
keep-cli archives
keep-cli doctor
keep-cli logs
keep-cli check
keep-cli check --deep
```

Configuration is read from `--config`, `KEEP_CONFIG_PATH`, or the XDG Keep
configuration file. No default configuration is created by diagnostic commands.
`status` reports configured scheduling and, when the user systemd manager is reachable, the timer active/enabled state and next run. An unavailable manager is reported separately.
`doctor` reports destination availability, Borg presence, Linux support and legacy
timer file presence; a file alone does not establish that a timer is enabled.
`logs` returns bounded summaries of the latest three backup logs.
`archives` scopes the list to the configured archive prefix. Checks cover the whole
repository, including other archive prefixes. There is no repair option.

## Output contract (schema 1)

Every command accepts `--json`. Stdout contains one object with `schema_version`,
`command`, `exit_code` and `data`. Progress messages go to stderr. Without `--json`,
the initial interface prints indented result objects. Parser/usage errors follow
argparse conventions and go to stderr, without a JSON envelope.

Exit codes: 0 success; 1 warning or unhealthy diagnostic; 2 operation/configuration
or usage failure; 3 another Keep operation is busy; 4 query timeout; 130 cancellation.
No command prompts for input. No raw Borg error output or credentials are returned.
Missing credentials fail noninteractively; unlock through existing GUI recovery or
configure an explicit credential provider before retrying.

## Credentials and execution

An explicit environment is passed to the shared Borg runner. Existing Borg
environment providers take precedence over `~/.config/borg/passphrase`. There is
no plaintext passphrase flag. Subprocess stdin is closed. Query execution is bounded;
integrity checks have no fixed duration limit and emit periodic liveness messages,
not an invented percentage. Ctrl+C and SIGTERM terminate the child process group,
then escalate after a grace period if necessary. No lock-breaking is performed.

## Coordination and persistence

Per-user OS locks live in `$XDG_STATE_HOME/keep/locks`, keyed by canonical repository
path. The engine holds one across backup/retention, GUI browsing across its mount
lifetime, and GUI deletion across delete/compact. CLI queries/checks fail promptly
when busy; scheduled backups wait up to five minutes. OS locks release on process
exit; lock files are not deleted to avoid inode races. Borg locks remain authoritative
for old clients, other users, path aliases and lingering mount processes.

`$XDG_STATE_HOME/keep/last-check.json` records the latest started check and its
completion/cancellation result. Status displays it only for the matching repository.
An abruptly killed process can leave a `running` record, which is not a success.
This is a single latest-result file, not per-repository history.

## Boundaries

Restore, restore-test, deletion from CLI, backup convenience commands, interactive
selection and maintenance timers are not part of this first command implementation.
GUI Status details display the latest check outcome for the matching repository after refresh. External legacy
backup scripts do not acquire Keep's lock and continue to rely on Borg coordination.

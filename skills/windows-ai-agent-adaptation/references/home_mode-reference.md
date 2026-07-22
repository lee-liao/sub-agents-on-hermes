# `terminal.home_mode` reference

Canonical source: `hermes_cli/config.py` DEFAULT_CONFIG comment in the Hermes Agent repo.

```yaml
terminal:
  home_mode: auto   # auto | real | profile
```

| Mode | Behavior |
|---|---|
| `auto` | Host subprocesses keep the real OS-user `$HOME`. Containerized backends (Docker, Modal, Daytona, Singularity) use `HERMES_HOME/home` for persistent state. |
| `real` | Force the real OS-user `$HOME` for every subprocess. |
| `profile` | Force `HERMES_HOME/home` as `$HOME` when that directory exists. This is the old strict per-profile CLI isolation mode; it keeps each profile's tool dotfiles inside the profile so state does not leak between profiles or into the OS user account. |

## When to use which

- Use `real` when tools expect to find auth/state in your normal home directory (e.g., Windows/WSL where `auto` can drift to a synthetic sibling home and orphan dotfiles).
- Use `profile` when you want full isolation between Hermes profiles and do not need tool dotfiles shared with your OS user account.
- `auto` works for most local macOS/Linux installs; verify on Windows.

## Verification

```bash
hermes config set terminal.home_mode real
hermes config path
grep home_mode ~/.hermes/config.yaml
```

From a spawned subprocess, check that `$HOME` resolves to the intended path:

```bash
terminal(command="echo HOME=$HOME")
```

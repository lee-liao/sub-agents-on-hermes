# Golden Session on Windows — worked example

> Condensed reference from analyzing `D:/MyCode/Ivan/sub-agents-on-hermes` for a Windows host.

## Original Linux/Docker setup

- Hermes Agent runs in a Docker container (`hermes-lee`).
- Container home `/opt/data` is bind-mounted from `/home/lee/.hermes`.
- `golden_session` (stdlib Python) is installed onto `/opt/data/.local/lib`.
- `claude -p` is invoked by the agent inside the container.
- Auth: `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` + `_HERMES_FORCE_ANTHROPIC_BASE_URL`.
- `terminal.home_mode: real` is required so agent subprocesses see the same `$HOME` as the gateway.

## Windows mapping options

### Option A: Native Hermes + WSL2 for Claude

Keep Hermes on Windows, but override `golden_session`'s runner so it calls `claude` inside WSL2.

```python
# Example Windows runner that forwards to WSL2
import subprocess

def wsl_runner(args, cwd, env=None):
    # Map Windows cwd to WSL2 path if necessary
    wsl_args = ["wsl", "-d", "Ubuntu", "--", "claude"] + list(args[1:])
    proc = subprocess.run(
        wsl_args,
        cwd=cwd,
        env={**os.environ, **env} if env else None,
        capture_output=True,
        text=True,
    )
    return RunOutput(proc.returncode, proc.stdout, proc.stderr)
```

Pros: keeps Windows host as primary OS.
Cons: path translation between Windows and WSL2; stdout/stderr routing.

### Option B: Full WSL2 stack

Install Hermes, Claude Code, and `golden_session` inside WSL2.

```bash
# In WSL2
export HERMES_HOME=/home/liao/.hermes
hermes config set terminal.home_mode real
export ANTHROPIC_AUTH_TOKEN=...
export ANTHROPIC_BASE_URL=...
export _HERMES_FORCE_ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL
```

Pros: closest to the original Linux/Docker setup.
Cons: projects must be accessible from WSL2 (clone into WSL2 or use `/mnt/d/...`).

### Option C: Docker Desktop on Windows

Translate `docker-compose.yml` paths to Windows:

```yaml
volumes:
  - D:\Hermes\home:/opt/data
  - D:\Hermes\wiki:/opt/data/wiki
  - D:\MyCode\projects:/opt/data/projects
  - D:\MyCode\Ivan\sub-agents-on-hermes:/opt/data/hermes-docker-lee:ro
```

Notes:
- `HERMES_UID`/`HERMES_GID` are less meaningful on Windows; the bind mount usually works permissively.
- Still need `terminal.home_mode: real` and `_HERMES_FORCE_ANTHROPIC_BASE_URL`.
- Bash shim at `bin/golden_session` needs a Windows equivalent or use `python -m golden_session`.

## Environment variables to set on Windows

```cmd
set HERMES_HOME=C:\Users\liao_\AppData\Local\hermes
set GOLDEN_SESSION_REGISTRY=%HERMES_HOME%\.golden_session\registry.json
set GOLDEN_SESSION_PROJECTS_DIR=%HERMES_HOME%\.claude\projects
set CLAUDE_CONFIG_FILE=%HERMES_HOME%\.claude.json
set ANTHROPIC_AUTH_TOKEN=...
set ANTHROPIC_BASE_URL=...
set _HERMES_FORCE_ANTHROPIC_BASE_URL=%ANTHROPIC_BASE_URL%
```

## Key gotchas

1. **Claude Code CLI on Windows can be native.** There is a native `claude.cmd` via npm (`%APPDATA%\npm\claude.cmd`). It supports `claude -p`, `--output-format json`, `--session-id`, `--resume`, and `--fork-session`, but it ignores `--session-id` for brand-new non-interactive sessions and folds `_` to `-` in project-dir names. See `references/claude-code-windows-cli-quirks.md`.
2. **Hermes strips `ANTHROPIC_BASE_URL`.** Use `_HERMES_FORCE_ANTHROPIC_BASE_URL` or the shim re-export trick.
3. **`terminal.home_mode: real` prevents dotdir drift.** Without it, agent subprocesses may use a fake home and lose auth/state.
4. **Registry defaults to Windows home.** Align it with `HERMES_HOME` via `GOLDEN_SESSION_REGISTRY`.
5. **`GS_RUN_DIR` default may assume POSIX `/tmp`.** Override with `--run-dir` or adjust defaults to use `tempfile.gettempdir()`.
6. **Bash shims don't run on native Windows.** Use `python -m golden_session` or create a `.cmd` wrapper.
7. **Isolate project workspaces.** Copy source directories into `HERMES_HOME\projects` before priming so the agent only writes under Hermes control.

## Smoke-test checklist

- [ ] `hermes config path` and `grep home_mode` show `real`.
- [ ] `hermes doctor` passes.
- [ ] From a Hermes `terminal()` call, `echo HOME=%HOME%` matches `HERMES_HOME`.
- [ ] `claude -p "reply OK" --max-turns 1` returns OK from the target environment.
- [ ] `golden_session list` runs clean (JSON output, exit 0).
- [ ] `golden_session prime` creates a registry entry under `HERMES_HOME`.
- [ ] `golden_session run` returns a new `session_id` and writes to the configured run-dir.

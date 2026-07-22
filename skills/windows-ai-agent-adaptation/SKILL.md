---
name: windows-ai-agent-adaptation
description: Adapt Linux/Docker-based AI agent workflows (Hermes, Claude Code, golden_session, MCP, gateway) to run natively on Windows, WSL2, or Docker Desktop.
version: 1.0.0
author: Hermes Agent
platforms: [windows]
metadata:
  hermes:
    tags: [windows, wsl2, docker, hermes, claude-code, cross-platform, deployment, ai-agents]
---

# Windows AI Agent Adaptation

> **Source of truth: the `sub-agents-on-hermes` repo** (`skills/windows-ai-agent-adaptation/`). This file is
> deployed to `%HERMES_HOME%\skills\` by `scripts/deploy-skills.ps1`. **Do not edit the
> deployed copy** — edits there are unversioned and have been silently lost before.
> Edit in the repo, commit, then run the script.

Adapting Linux/Docker-first AI agent tooling to Windows requires deciding on a substrate, then mapping paths, env vars, and subprocess behavior across the boundary. This skill covers the common patterns and pitfalls when moving a Linux/Docker Hermes/Claude Code/golden_session-style deployment onto a Windows host.

## Decision tree: pick a substrate

| Approach | Best for | Trade-offs |
|---|---|---|
| **WSL2 (full Linux stack)** | Closest 1:1 fidelity; you already use WSL2 | Hermes and projects live in Linux filesystem; Windows paths need translation |
| **Docker Desktop on Windows** | Strong isolation; you already use Docker | Bind-mount path/permission quirks; WSL2 backend required for best performance |
| **Native Windows + WSL2 for Claude only** | Keep Windows host as primary OS | Requires a custom runner/shim to forward `claude` calls into WSL2 |
| **Native Windows only** | Avoid WSL2/Docker entirely | Often blocked because Claude Code CLI and similar tools are Linux/macOS only |

## Core invariants that survive every substrate

1. **One home directory.** Keep `HERMES_HOME`, OS home, and tool dotfiles (`~/.claude`, `~/.claude.json`, `~/.golden_session`, `~/.npmrc`) under the same root so spawned subprocesses find auth and state.

2. **Understand `terminal.home_mode` and set it deliberately.** Hermes defaults to `auto`. The three modes are:

   - `auto` — host subprocesses keep the real OS-user `$HOME`; containerized backends (Docker, Modal, etc.) use `HERMES_HOME/home` for persistent state.
   - `real` — force the real OS-user `$HOME` for all subprocesses. Use this when tool dotfiles/auth must live in your normal home directory (common on Windows/WSL to avoid orphaned auth/credential dotfiles).
   - `profile` — force `HERMES_HOME/home` as `$HOME` when that directory exists. This is the old strict per-profile isolation mode; it keeps each profile's tool dotfiles in its own private home so state does not leak between profiles or into the OS user account.

   On Windows/WSL, `real` is usually the safest choice because `auto` can drift to a synthetic sibling home and orphan auth files.

   ```bash
   hermes config set terminal.home_mode real
   # verify
   hermes config path
   grep home_mode ~/.hermes/config.yaml
   ```

   See `references/home_mode-reference.md` for the canonical source definition.

3. **Escape the env blocklist for `ANTHROPIC_BASE_URL`.** Hermes strips `ANTHROPIC_BASE_URL` from terminal subprocess envs (security blocklist). The supported escape hatch is `_HERMES_FORCE_ANTHROPIC_BASE_URL`.

   ```bash
   # .env or shell
   export ANTHROPIC_BASE_URL=https://...
   export _HERMES_FORCE_ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL
   ```

4. **Use absolute paths in registry/config.** Relative paths break as soon as the working directory changes between the caller and the subprocess. Always store absolute paths in registries like `golden_session`'s `registry.json`.

## Windows-specific path considerations

- `HERMES_HOME` on Windows commonly lives in `C:\Users\<user>\AppData\Local\hermes` rather than `~/.hermes`.
- Use `os.path.abspath()` and normalize separators when encoding paths for session/transcript IDs.
- Set `GOLDEN_SESSION_REGISTRY` and `GOLDEN_SESSION_PROJECTS_DIR` explicitly if you want them under `HERMES_HOME` instead of the default Windows home.
- Set `CLAUDE_CONFIG_FILE` explicitly if you want Claude Code state under `HERMES_HOME`.

## Common blockers

| Symptom | Likely cause | Fix |
|---|---|---|
| `claude: command not found` | Claude Code CLI not installed or not on PATH | Windows supports a native `claude.cmd` via npm; otherwise run inside WSL2/Docker, or wrap `golden_session` to invoke `wsl -d Ubuntu -- claude ...` |
| `401 "Invalid bearer token"` | `ANTHROPIC_BASE_URL` stripped by Hermes blocklist | Set `_HERMES_FORCE_ANTHROPIC_BASE_URL` |
| Agent subprocess sees different `$HOME` than gateway | `terminal.home_mode: auto` | Set `terminal.home_mode: real` |
| Transcripts/registry written to unexpected location | `HERMES_HOME` and OS home not aligned | Set explicit env vars or unify under one directory |
| PreToolUse hook not executing | Hook is a POSIX shell script on native Windows | Rewrite hook in Python, or keep workspace inside WSL2 |
| `WinError 193` / `FileNotFoundError` spawning `claude` | npm installs `claude` as a `.cmd` shim; CreateProcess cannot spawn it by bare name, and Python may not inherit the git-bash PATH | Resolve the shim via `shutil.which` on Windows, inject the npm prefix dir if needed, and rewrite `argv[0]` to the absolute `.cmd` path before `subprocess.run` (see `references/winerror-193-subprocess-resolution.md`). |
| Gateway says `pbi: command not found` or skill cannot call newly installed CLI | The gateway launcher did not add the venv `Scripts` directory to PATH; user PATH alone is not always inherited by the gateway process | Patch `%LOCALAPPDATA%\hermes\gateway-service\Hermes_Gateway.cmd` and `.vbs` to prepend the venv `Scripts` to PATH before launching the gateway. See `references/gateway-launcher-path-fix.md`. |
- **Multiple Hermes gateways polling the same email inbox.** When you send a
  workflow command by email but the reply comes from the wrong Hermes instance
  (e.g., it says the skill is missing, the tool is on another machine, or it
  treats the command as a request to a remote system), another gateway consumed
  the message first. IMAP `SEEN` semantics mean whichever gateway fetches first
  wins. **Tell the user the exact steps to clear it:**
  1. Identify the other Hermes host (check the reply's tone/paths, or the email
     account's recent login sessions).
  2. On that host, stop the gateway: `hermes gateway stop` (or kill the
     process). If it is a cloud/Nous-hosted instance the user does not control,
     say so explicitly — they cannot stop it from the Windows machine.
  3. Send the command again as a new email (not a reply to the old thread) so
     the Windows gateway starts a fresh session with the patched skill.
  4. Restart the other gateway afterward if needed.
  Long-term fixes: give the Windows gateway a dedicated email address, or use
  Discord (a dedicated channel cannot be intercepted by the other instance).
  See `references/powerbi-workflow-orchestrator-windows.md` for the Power BI
  workflow context and `references/hermes-gateway-email-163-workaround.md` for
  the 163.com-specific setup.
- **Email reply loop from gateway agent** | Default model cannot use tools; agent sends replies but never runs `pbi continue` | Set the default model to one that can use tools (`hermes config set model.default kimi-for-coding; hermes config set model.provider kimi-coding`), restart the gateway. See `references/hermes-gateway-email-163-workaround.md`. |
| Inbound email from Hotmail/Outlook dropped by 163.com gateway | Hermes adapter expects `smtp.mailfrom`/`header.d` in `Authentication-Results`; 163.com stamps `smtp.mail`/`header.i` | Set `EMAIL_TRUST_FROM_HEADER=true` as a Windows user environment variable and restart the gateway. See `references/hermes-gateway-email-163-workaround.md`. |
| Workflow state stuck `running` after a tool timeout | The engine process was killed, leaving a stale `.lock` and an un-updated state file | Check `.lock` PID; if dead, run `pbi continue <case-id>` to re-acquire the lock and resume from the last completed node. See `references/powerbi-workflow-orchestrator-windows.md`. |
| Resume does not pick up the next node | The response value was not exactly what the `approval_gate` node expects | For the `approval_gate` capability, the response must be exactly `approve`; anything else fails the node. Match the case and whitespace. |
| `pbi build` uses mock ADO when credentials exist | `PBI_USE_MOCK_ADO=true` is set, or the workspace secret file is missing | The scripts now default to real ADO when any credential is present. Set `PBI_USE_MOCK_ADO=true` explicitly to force mocks, or verify the workspace's `.claude/settings.local.json` env block contains the PAT. See `references/powerbi-workflow-orchestrator-windows.md`. |


If you install `claude` on Windows via npm (usually as `%APPDATA%\npm\claude.cmd`), it works natively, but it differs from the Linux/macOS build in subtle ways that break `golden_session` and similar wrappers:

1. **`--session-id` is ignored for brand-new sessions in non-interactive mode.** The CLI mints its own UUID and writes the transcript under that name. Wrappers that pass a pre-generated `golden_id` to `--session-id` during `prime` will not find a transcript with that name. Two fixes work:

   - **Automatic fix:** Omit `--session-id` for `prime` and derive the real session ID from the transcript file that was actually written.
   - **Interactive fixed-ID fix:** Prime interactively with `claude --session-id <fixed-uuid>`, paste the workspace context, then register the fixed UUID in the registry. This is robust when `claude -p` cannot be trusted to honor `--session-id` or return JSON for context-only prompts.

2. **Project directory encoding folds `_` to `-`.** The native Windows CLI encodes the workspace path for `~/.claude/projects` by replacing path separators, the drive colon, and underscores with `-`. A workspace like `C:\Users\liao_\...` becomes `C--Users-liao--AppData-...`, not `C--Users-liao_-AppData-...`. Any wrapper that builds the expected transcript path must match this encoding exactly.

3. **`--output-format json` may return plain text for context-only prompts.** If the prompt is purely project context (e.g. a `CONTEXT.md` file) with no explicit task, the Windows CLI can respond with a conversational "What would you like me to do?" message instead of JSON. Keep priming prompts focused and task-oriented, or use the interactive fixed-ID approach.

4. **Use `.cmd` path-aware subprocess invocation.** Python's `subprocess.run` with the absolute `claude.cmd` path generally works; if you invoke it through a shell, ensure `COMSPEC` and `PATHEXT` are preserved. Prefer passing a list of arguments over shell-joining. The wrapper runner should also let the operator override the executable via `CLAUDE_BIN` because the npm `claude` directory is not always on the PATH inherited by native Windows Python subprocesses.

5. **Golden-session stdout parsing is not a last-line problem.**
   The `golden_session` CLI emits pretty-printed JSON; on resumed sessions the underlying Claude CLI may append extra usage metadata. A wrapper that parses only the last line or `json.loads` of the whole stream will fail even when the CLI succeeds. Parse the first complete JSON object with `json.JSONDecoder().raw_decode()` and ignore trailing text. See `references/golden-session-stdout-parsing.md`.

6. **Windows env vars that contain paths should not be split with `shlex.split()`.** POSIX quoting treats backslashes as escapes, which mangles Windows paths like `D:\Program Files\Python311\python.exe`. Use a Windows-aware splitter that respects double quotes and treats backslashes literally. See `references/golden-session-stdout-parsing.md`.

See `references/claude-code-windows-cli-quirks.md` for the reproduction recipe and the exact code changes used in a working `golden_session` integration. For the specific `WinError 193` subprocess bug and the path-resolution implementation, see `references/winerror-193-subprocess-resolution.md`.

## Workflow: adapt a new Linux/Docker repo to Windows

1. **Inventory the Linux assumptions:** Docker bind mounts, UID/GID, shell scripts, `~/.something` paths, `/tmp`, `npm`/`node` availability.
2. **Verify substrate tool availability:** Is `claude`/`node` available in WSL2/Docker? Is Hermes installed on Windows natively?
3. **Map the home directory:** Choose or verify `HERMES_HOME` and make sure all tool dotfiles live there.
4. **Set `terminal.home_mode: real`:** This is the most common silent failure.
5. **Fix the env blocklist:** If the tool needs a URL/endpoint env var, check whether Hermes strips it and add `_HERMES_FORCE_*` if needed.
6. **Translate bind mounts:** For Docker Desktop, replace Linux paths with Windows paths (`D:\...:/opt/data`).
7. **Handle shell scripts:** Convert bash shims to `.cmd`/`.bat`, Python entry points, or `python -m <module>` invocations.
8. **Isolate project workspaces under Hermes control before priming or registration.** Copy the source project into `HERMES_HOME\projects\<name>` (or another Hermes-managed directory) and point the registry at the copy, never at the original source. This is a deliberate safety boundary: Hermes/GOLD only writes under the isolated directory, leaving the user's original repo untouched and preventing path drift. After copying, also sanitize any project-local Claude settings that contain hard-coded paths to the original directory (e.g. `Bash(python C:/Users/.../original-dir/.../gen_pbip.py)` permissions in `.claude/settings.local.json`); replace them with relative paths or wildcarded paths under the copied workspace.
9. **Verify from a spawned subprocess:** Run `echo HOME=%HOME%` or `hermes doctor` from a Hermes `terminal()` call, not just from your interactive shell.
10. **Smoke test auth:** `claude -p "reply OK" --max-turns 1` should work from the target environment before any real tasks.
11. **Inspect copied project-local Claude settings for stale paths and credentials.** `.claude/settings.local.json` often contains env vars (PATs, tokens) and absolute permissions tied to the original path. Audit these after copying and before the first `run`.

### Prime options on Windows

Because the native Windows `claude` CLI does not honor `--session-id` for new `claude -p` sessions, choose one of:

- **Automatic `prime`:** Use the patched `golden_session prime` that omits `--session-id` and derives the real ID from the transcript file. Works best when `claude -p` returns clean JSON for your priming prompt.
- **Interactive fixed-ID prime:** Run `claude --session-id <fixed-uuid>` in the workspace, paste the context, ask for confirmation, then `/exit`. Update the registry to use that fixed UUID. This is the most reliable method when `claude -p` produces plain text or a minted UUID for context-only prompts.

See `references/golden-session-windows-example.md` for a concrete installation recipe and `references/golden-session-windows-projects.md` for the project inventory used in this session.

## References

- `references/golden-session-windows-example.md` — concrete worked example from the `sub-agents-on-hermes` repo, including the automatic `prime` path.
- `references/golden-session-windows-projects.md` — project inventory and fixed golden IDs from this session.
- `references/golden-session-stdout-parsing.md` — stdout parsing pitfalls (last-line JSON, resumed-session metadata, Windows env-var splitting).
- `references/claude-code-windows-cli-quirks.md` — exact reproduction recipes and code patches.
- `references/hermes-gateway-email-163-workaround.md` — Hermes email gateway on Windows with 163.com: loading `.env`, authentication-header workaround, model fix, and test loop.
- `references/powerbi-workflow-orchestrator-windows.md` — Windows setup for the Power BI workflow orchestrator (gateway PATH, real ADO, golden-session workspace, validator, staged/live skill sync, email-triggered workflows, and the `build` template approval checkpoint).

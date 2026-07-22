---
name: claude-code-gold
description: Delegate a coding task to a primed Claude Code GOLD session via the golden_session CLI. Use when the user asks to run a coding task against a known project by name (e.g. "golden ADO 238").
---

# claude-code-gold — GOLD-aware delegation guide

This is the thin trigger/knowledge layer (doc 05 Decision A1, Option B). It does
**not** orchestrate — it instructs you to delegate to the `golden_session` CLI,
which holds the orchestration logic and enforces the GOLD invariants F1–F10 in
code. Do **not** call raw `claude -p` for these tasks; that bypasses every
guardrail (GOLD protection, budget caps, single-writer, loud-not-found).

## Project setup and priming

Before delegating a project to GOLD, its workspace must be **isolated under Hermes
control** so the engine only writes under an isolated directory and never touches
the original source. Copy the project directory into `HERMES_HOME/projects/<name>`
(or another Hermes-managed path) before priming.

There are two reliable ways to prime on Windows; choose one per project:

- **Interactive fixed-ID prime (preferred on Windows):** Complex project contexts
  with MCP setup often cause the native `claude -p` non-interactive mode to return
  plain text instead of JSON, or the Windows CLI ignores `--session-id` for a
  fresh session. Prime interactively in PowerShell so the transcript is created
  with a known, stable session ID:

  ```powershell
  cd "<copy-path>"
  claude --session-id <fixed-uuid>
  ```

  Paste the project context, ask for OK confirmation, then `/exit`. Finally
  register the name by updating `~/.golden_session/registry.json` to map
  `<name>` to that fixed UUID, or use the engine's `prime` command with
  `--golden-id <fixed-uuid>` once the transcript exists.

  This is the most robust method for Windows workspaces with MCP servers.

- **Automatic `prime`:** `golden_session prime --name <name> --cwd <copy-path> ...`.
  Use only when the native `claude -p` returns clean JSON for the priming prompt.
  On Windows the engine may need to omit `--session-id` and derive the real ID
  from the transcript file.

## MCP permissions and tool approvals

A fresh `golden_session run` from a non-interactive `claude -p` fork may ask for
permission for each new MCP/Bash tool. Let the user decide the approval model:

- **Manual approval (recommended for sensitive workspaces):** The user runs one
  `golden_session run` interactively and approves each tool use in sequence. The
  Claude Code CLI remembers approved permissions in the workspace's
  `.claude/settings.local.json` for later headless runs.
- **Auto-skip via `--allow-dangerously-skip-permissions`:** If the user opts in,
  the engine can pass that flag to `claude -p` to auto-approve all tools. Do
  **not** add this unilaterally — it is a security policy decision and changes the
  engine code.

Before any run, check the workspace's `.claude/settings.local.json` for stale
absolute paths (e.g., pointing to an old source directory instead of the copied
workspace). Replace them with relative paths or a glob matching the copied
workspace path so the permissions remain valid after the project is isolated
under Hermes control.

## What you do

1. **Identify the session name and task** from the user's request. The user
   references a memorable *name*, never a UUID or path.
2. **Delegate via `terminal()`** to the wrapper. Never supply `golden_id` or
   `cwd` yourself — the registry resolves identity in code:

   ```
   golden_session run --name <name> --task "<task>"
   ```

   For a task that has a shipped template in the workspace (e.g. an ADO work
   item), pass the template file name and its parameters instead of a literal
   task — the **engine** reads the template from the session's workspace cwd and
   fills `${KEY}` placeholders in code (you do no substitution yourself):

   ```
   golden_session run --name ado-ready \
     --task-template ado-workitem-task.md --param WORK_ITEM_ID=<id>
   ```

   When the workflow is part of a multi-stage pipeline (e.g. analysis → plan →
   implementation → qa), the orchestrator should pass a shared case ID rather
   than constructing the run directory path. Use the CLI's ID arguments so the
   engine resolves the workspace and sets `GS_RUN_DIR` in the environment:

   ```
   golden_session run --name <session> --task-template analysis-task.md --case-id <id>
   golden_session run --name <session> --task-template plan-task.md --case-id <id>
   golden_session run --name <session> --task-template implementation-task.md --case-id <id>
   golden_session run --name <session> --task-template qa-task.md --case-id <id>
   ```

   **`--name` is the session (a workspace), not the phase.** One GOLD per
   workspace, so every stage passes the *same* `--name` and varies only
   `--task-template`. Naming a session after a phase would require priming a
   redundant GOLD per stage against one workspace. See "Sharing GOLD sessions
   across skills and consumers" in `docs/prd/00-project-overview.md`.

   Supported ID arguments:
   - `--case-id <id>` — generic case identifier.
   - `--work-item-id <id>` — ADO-specific convenience.
   - `--pipeline-id <id>` — multi-stage pipeline identifier.
   - `--run-dir <path>` — manual override.
   - `--continue` — reuse an existing run directory for recovery.

   Do not build the `GS_RUN_DIR` path in the orchestrator and do not pass it
   in the prompt text; the CLI owns workspace resolution and environment setup.

   **Windows pitfall:** do not use `--task` with a long, multi-line prompt on
   Windows. Native Windows `claude -p` truncates multi-line argv strings at the
   first newline, so the engine routes the prompt through `stdin` when templates
   are used. If Claude responds that the message was "cut off mid-sentence", the
   prompt was passed as an argument instead of via stdin — check that the
   `default_runner` in `runner.py` is feeding `input` to the subprocess, not
   embedding the prompt in the argv.

   Optional overrides (clamped to the session's ceilings):
   `--budget <usd>`, `--turns <n>`, `--tools Read Edit Bash`, `--model <m>`.

3. **Read the JSON result** the command prints and report back: `is_error`,
   `terminal_reason`, `cost_usd`, `session_id`, and `result`. Treat **only
   explicit success** (`is_error: false`) as success — a green-but-stalled task
   is a known Phase 1 gap (PRD §5).

   **Also verify artifacts on disk.** A JSON result alone does not prove the task
   wrote the expected files. After a successful run, list the files under
   `result.run_dir`, especially the `out/` or `input/` subdirectories. If the
   run directory is empty but the result is green, the task template was probably
   not applied (the prompt was truncated or `--task` was used instead of
   `--task-template`) and the agent only produced a text report. Always ask the
   user to confirm the output files they expected.

## Windows pitfall: WinError 193 when spawning `claude`

If a non-interactive `golden_session run` on Windows fails to launch Claude with
`OSError: [WinError 193] %1 is not a valid Win32 application`, the engine is
trying to spawn the npm `claude.cmd` wrapper by its bare name. Python's
`subprocess.run` with `shell=False` cannot execute a `.cmd` file by bare name;
it needs the absolute path to the `.cmd` shim (resolved via `shutil.which`) or
`cmd.exe /c`.

The correct fix is to resolve the bare name to the on-disk file before calling
`subprocess.run` and, if necessary, inject the npm prefix directory into the
search PATH. Setting `shell=True` is not the recommended fix; it introduces
quoting complications and is unnecessary once the executable is resolved.

See `references/windows-claude-spawn-error.md` for the root cause, the exact
`shutil.which` resolution pattern, and a reusable probe script
(`scripts/check_windows_claude_spawn.py`).

## IM trigger grammar (`golden` namespace)

When triggered from a chat surface (Discord/IM) alongside many other skills, the
trigger **must** lead with the distinctive keyword `golden` — generic verbs
(`handle 238`, `process 238`) and bare numbers (`238`) are too ambiguous with
hundreds of skills loaded and will **not** route here.

- `golden ADO <id>` — run an ADO work item. Expand this to the template form
  above: `golden_session run --name ado-ready --task-template
  ado-workitem-task.md --param WORK_ITEM_ID=<id>`. The engine (not you) resolves
  the template from the workspace and substitutes the id.
- `golden run <id> on <session>` — run with an explicit session name.
- `golden list` — the registered sessions and their args.
- `golden status <session>` — discovery for one session.

Note: the reference IM gateway (`gateway.py`) currently parses only the generic
`run on <name>: <task>` grammar; the `golden …` shorthands above are expanded by
this skill into the CLI calls, not by the gateway parser.

## Discovery

- `golden_session list` → the available names, their workspace, and required /
  optional args. Use it when the user is unsure what to run.
- Unknown name → the command returns a structured error with `known_names`;
  relay the "did you mean …" hint.

## Recovery (direct/automation only in Phase 1)

If a task fails and you have its `session_id`, an operator/automation can append
a fix without losing progress:

```
golden_session continue --name <name> --session-id <sid> --task "fix: <what to change>"
```

Continuation from a chat surface is Phase 2; the MVP triggers fresh forks by name.

## Hard rules (enforced by the wrapper, do not work around)

- Never `prime` a name twice and never `continue` on a GOLD id — both are refused.
- Never drop the budget/turn caps; they are mandatory.
- Never point a run at an ad-hoc cwd — identity comes from the registry only.

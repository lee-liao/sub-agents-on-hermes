# Windows MCP GOLD priming recipe

Session: 2026-07-13. Context: Hermes Agent on Windows, native `claude` npm CLI
v2.1.207, native Python 3.13.

> **Updated 2026-07-22 — do not copy the engine.** This recipe originally
> described copying the package into
> `%HERMES_HOME%\.local\lib\golden_session`. That second copy silently went
> stale (it lacked `--case-id` for months). `golden_session` is now a pip
> **editable** install pointing at the repo, so `python -m golden_session` and
> the `golden_session` console script both resolve to the one current source
> with **no `PYTHONPATH` needed**. See `docs/WINDOWS_DEPLOYMENT.md` §2.

## Problem

For complex project workspaces that set up MCP servers (ADO, Power BI Desktop),
`claude -p` (non-interactive JSON mode) on Windows returns **plain text** when
passed a context-only prompt, or ignores `--session-id` for a fresh session. The
engine's JSON parser fails and the GOLD session ID is not known in advance.

## Fix: interactive fixed-ID prime

1. Copy the workspace into an isolated Hermes-managed directory, e.g.:

   ```powershell
   # C:\Users\liao_\AppData\Local\hermes\projects\<name>
   ```

2. Choose a fixed UUID and prime interactively:

   ```powershell
   cd "C:\Users\liao_\AppData\Local\hermes\projects\<name>"
   claude --session-id <fixed-uuid>
   ```

3. Paste the project context and ask Claude to confirm (e.g., "verify ADO MCP PAT
   works and reply OK").

4. `/exit`.

5. Verify the transcript exists at:

   ```
   C:\Users\liao_\.claude\projects\C--Users-liao--AppData-Local-hermes-projects-<name>\<fixed-uuid>.jsonl
   ```

   Note that Windows Claude folds underscores to dashes in the project directory
   name (`C--Users-liao--...` not `C--Users-liao_--...`).

6. Register the name in `~/.golden_session/registry.json` with the fixed UUID and
   the copied workspace path.

## Permission cleanup

After copying a workspace, open `.claude/settings.local.json` and check
`permissions.allow` for stale absolute paths that still point to the original
source directory. Replace them with relative paths or a glob that matches the
new copied workspace path so headless forks retain the permissions.

## Engine-side patches (if copying the engine locally)

On Windows, the engine may also need these small adjustments:

- `CLAUDE_BIN` env var: native Python subprocesses often don't see the npm
  `claude` binary. Export `CLAUDE_BIN=D:\Users\liao_\AppData\Roaming\npm\claude.cmd`
  or extend the subprocess `PATH` to include the npm prefix directory.

- `PYTHONPATH`: **no longer required.** The engine is a pip editable install, so
  `python -m golden_session` resolves it from site-packages. Verify with
  `python -c "import golden_session; print(golden_session.__file__)"` — it should
  print the repo path. Only set `PYTHONPATH` if you are deliberately running a
  Python that lacks the install.

- `encode_cwd()` in `session.py` should fold underscores to dashes to match the
  Windows Claude project directory naming convention.

- **Prompt via stdin for task templates:** `_build_args()` should not embed the
  prompt string in the argv. Instead, pass it separately to the runner and feed
  it to `subprocess.run(..., input=prompt)`. Native Windows `claude -p` truncates
  multi-line argv strings at the first newline; a long task template passed via
  `--task` will be cut off and Claude will complain the message ended
  mid-sentence. Using `--task-template` and stdin is the reliable Windows path.

## Running a task on Windows

Always prefer a shipped template over a literal prompt:

```bash
golden_session run --name <name> --task-template <template>.md --param KEY=VALUE
```

Do **not** use `--task "long multi-line prompt..."` on Windows. If you must, the
engine must route the prompt through stdin, not argv.

## After-run verification checklist

A successful JSON result (`is_error: false`) is not enough. Always check the
run directory artifacts:

```python
import os
run_dir = result["run_dir"]
print("input:", os.listdir(os.path.join(run_dir, "input")))
print("out:", os.listdir(os.path.join(run_dir, "out")))
```

If the run directory is empty but the result is green, the task template was
probably not applied (prompt truncated or `--task` used instead of
`--task-template`) and the agent only produced a text report. Root causes:

- The command used `--task` instead of `--task-template`.
- The engine was not using stdin for prompts.
- `.claude/settings.local.json` has a stale `permissions.allow` path that blocked
  the writes.

## Verifying registration

```python
import json, os
registry_path = os.path.join(os.path.expanduser("~"), ".golden_session", "registry.json")
with open(registry_path) as f:
    print(json.load(f).get("<name>"))
```

# GOLD context — <session name>

This file is the **stable project context** baked into a GOLD session at prime
time (`golden_session prime --context-file .claude/gold-context.md`). It is read
*once*; every task forked from this GOLD inherits it. Keep it durable and
task-agnostic — put per-task detail in the `--task` prompt, not here.

## What this workspace is

<One or two sentences: what project/system this GOLD operates on and why it
exists. e.g. "Runs ADO work items for the billing team: reads a work item,
downloads its attachment, produces the requested artifact under GS_RUN_DIR.">

## Ground rules for every task

- Outputs go under `$GS_RUN_DIR` only (enforced by the confine-writes hook).
  Never write elsewhere in the workspace.
- This is a **headless** session — no human answers prompts. Follow the
  "Headless task-prompt rules" in this workspace's `README.md`:
  - No `$VAR` in Bash commands; resolve env vars with
    `python3 -c "import os; print(os.environ.get('VAR'))"`.
  - Authenticated network calls go through a helper script (e.g.
    `.claude/ado_download.py`), never an inline `curl -u`.
- Fail loud: if a required input (attachment, credential) is missing, stop and
  report — do not fabricate a result.

## Domain facts the agent should assume

<Stable, slowly-changing knowledge: repo layout, naming conventions, the ADO
org/project, output formats expected. Anything a task would otherwise have to
re-derive every run.>

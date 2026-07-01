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

   Optional overrides (clamped to the session's ceilings):
   `--budget <usd>`, `--turns <n>`, `--tools Read Edit Bash`, `--model <m>`.

3. **Read the JSON result** the command prints and report back: `is_error`,
   `terminal_reason`, `cost_usd`, `session_id`, and `result`. Treat **only
   explicit success** (`is_error: false`) as success — a green-but-stalled task
   is a known Phase 1 gap (PRD §5).

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

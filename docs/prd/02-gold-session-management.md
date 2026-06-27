# GOLD Session Management

## What GOLD is and why

A **GOLD session** is a stable, primed Claude Code conversation that holds project context once and is reused as the starting point for many independent tasks. Its defining property: **the GOLD transcript never grows** as tasks run. Tasks get GOLD's context, but their turns are written elsewhere.

This solves the central requirement of the Hermes pipeline: many parametrized tasks against the same workspace, without per-task chatter polluting the shared context that subsequent tasks depend on.

The pattern is conceptually independent of any orchestration approach (see `01-considered-approaches.md`), but only some approaches make it native.

## The pattern

```
                    prime once
                        │
                        ▼
              ┌─────────────────┐
              │  GOLD session   │   ← pristine template, never grows
              │  (project ctx)  │
              └─────────────────┘
                       │
           ┌───────────┼───────────┬───────────┐
       fork│       fork│        fork│        fork│
           ▼           ▼           ▼           ▼
        [task1]     [task2]     [task3]     [task4]
           │
           │  on failure / decision point
           │
        ┌──┴──┐
        │     │
    resume  resume --fork-session
    (append)  (new session, snapshot preserved)
```

Three operations:

| Operation | Purpose | CLI flags |
|---|---|---|
| **Prime** | Initialize GOLD with stable project context. Run once. | `claude -p "…" --session-id $GOLD` |
| **Fork task** | Start a new task with GOLD's context. GOLD stays pristine. | `claude -p "…" --resume $GOLD --fork-session` |
| **Continue (retry/fix)** | Append turns to an existing fork to recover from failure. | `claude -p "…" --resume $S_task` (no fork) |
| **Continue (branch)** | Create a new fork from an existing one to try a different decision. Original preserved. | `claude -p "…" --resume $S_task --fork-session` |

## How Claude Code stores sessions

Sessions persist as JSONL transcript files on disk:

```
~/.claude/projects/
├── <encoded-cwd>/                       ← one directory per workspace
│   ├── $GOLD.jsonl                      ← GOLD transcript (priming only)
│   ├── $task1.jsonl                     ← forked task sessions
│   ├── $task2.jsonl
│   └── ...
```

The directory name is the workspace path with `/` replaced by `-`. For example `/tmp/ws-test` becomes `-tmp-ws-test`. Session lookup by id is **scoped to the current cwd** — `--resume $SID` from a different directory silently fails to find the session.

## Verified behaviors

These were confirmed by live runs against Claude Code 2.1.x, not just by reading docs:

| Behavior | Verification |
|---|---|
| GOLD never grows across forks | Line count stayed flat (e.g. 6 → 6 → 6) across many forked tasks |
| Forks inherit GOLD's context | Forked tasks correctly recalled facts (e.g. `ProjectX`) set only during priming |
| `--resume` appends to the same session | Same session id returned; `.jsonl` grew from 11 → 22 lines |
| `--resume --fork-session` creates a branch | New session id; original fork's transcript preserved |
| Resume is cwd-scoped | Calling `--resume $SID` from a different cwd silently fails |
| Forked sessions can be resumed and re-forked arbitrarily deep | Three-level fork chain tested successfully |

## The wrapper

A pure-Python wrapper (`GoldenSession` class) encapsulates the pattern. Source lives at `/tmp/golden_session.py` — **note**: `/tmp` is volatile; copy the file into the project before relying on it.

API surface:

```python
from golden_session import GoldenSession, TaskResult

gs = GoldenSession(
    workspace="/path/to/ws",
    golden_id="<uuid>",                  # store this once in Hermes
    allowed_tools=["Read", "Write", "Bash"],
    max_turns=20,
    max_budget_usd=1.0,
)

gs.prime("…project context…")           # call once

t1 = gs.run_task(prompt)                # fork from GOLD
t2 = gs.continue_task(t1.session_id, "fix: …")                  # retry (append)
t3 = gs.continue_task(t1.session_id, "decide: A", fork=True)    # branch (new sid)
t4 = gs.continue_task(t1.session_id, "decide: B", fork=True)    # branch (new sid)

gs.list_forks()                         # all session files for the workspace
gs.cleanup_forks(keep={t3.session_id})  # delete all except GOLD + winners
```

`TaskResult` exposes: `session_id`, `is_error`, `subtype`, `terminal_reason`, `result`, `cost_usd`, `num_turns`, `usage`, `raw`.

The wrapper uses `--output-format json`, which blocks until the CLI exits. For mid-task streaming, swap to `--output-format stream-json --verbose` (open thread #1).

## Gotchas

1. **GOLD is sacred.** Never call `prime()` twice on the same id. Never call `continue_task(golden_id, …)`. Consider `chmod 400 ~/.claude/projects/<encoded-cwd>/$GOLD.jsonl` as a tripwire — Claude will fail loudly instead of silently polluting.

2. **Workspace is part of session identity.** Always pass `cwd` explicitly. Never rely on process cwd inheritance. A wrong cwd causes silent session-not-found failures.

3. **Keep GOLD lean.** Every fork pays a fresh prompt-cache write (~$0.05 floor, more for big GOLDs). Retries within the same session id reuse that session's cache; new forks don't. Pruning priming chatter pays off across thousands of tasks.

4. **Single-writer per session id.** Concurrent writes to the same `.jsonl` will corrupt it. Forks off the same sid from different processes are safe (different output files).

5. **Workspace, `CLAUDE.md`, hooks are cwd-level configs.** Forks inherit them. Freeze `CLAUDE.md` and don't change hooks between runs if you need identical task behavior.

6. **Forked files accumulate.** Each task creates a new `.jsonl`. After thousands of tasks, disk usage matters. Add a janitor (open thread #2).

7. **Per-call budget caps are mandatory.** `--max-turns` and `--max-budget-usd` bound runaway tasks. Without them, a stuck task burns unbounded money.

8. **`< /dev/null` on stdin** silences a harmless "no stdin data received in 3s" warning from the CLI.

## Operation policies

These are the rules Hermes should enforce around the wrapper:

| Rule | Mechanism |
|---|---|
| One GOLD per workspace | Store `(workspace, golden_id)` pair; never reuse golden_id across workspaces |
| Track fork chain per task | Persist `(task_id, current_session_id)`; update on every successful call |
| Serialize calls per session id | Queue retries/branches on the same sid; parallel only across different sids |
| Budget per task, not per call | Aggregate `total_cost_usd` across the chain; abort above threshold (open thread #5) |
| Clean up losing branches | After branch selection, `cleanup_forks(keep={winner})` (open thread #4) |

## What's not in scope here

- **Approach selection.** See `01-considered-approaches.md`. GOLD is approach-independent as a requirement but varies in implementation difficulty.
- **Decision detection.** See `03-open-threads.md` thread #3.
- **Mid-task streaming.** See `03-open-threads.md` thread #1.

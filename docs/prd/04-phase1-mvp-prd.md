# Phase 1 PRD — Hermes → Claude Code Orchestration (Usable Internal MVP)

> Scope record for the first shippable slice. Design background lives in the sibling docs:
> [`01-considered-approaches.md`](./01-considered-approaches.md),
> [`02-gold-session-management.md`](./02-gold-session-management.md),
> [`03-open-threads.md`](./03-open-threads.md). This PRD does not duplicate them — it
> selects from them and defines the contract for Phase 1.

## 1. Overview & goal

Hermes Agent needs to drive the Claude Code CLI programmatically to run non-interactive
coding tasks against a stable workspace. Per task: Hermes supplies parameters, Claude Code
runs with stable project context, writes files to `output/`, and Hermes parses the terminal
result — **without per-task chatter polluting the shared context** later tasks depend on,
and with the ability to **continue a task on failure without losing progress** (see doc 01
§Problem statement).

**Phase 1 goal — a usable internal MVP:** the thinnest slice Hermes can actually call to run
*one real task end-to-end and recover on failure*, built on the GOLD session pattern
(doc 02). Not a demo; it must be safe to point at real work (real money, real workspaces).

**Definition of done:** all functional requirements F1–F10 met and the §6 acceptance
criteria pass.

## 2. Solution / substrate

**Build Phase 1 on the Headless CLI (`claude -p`) driving the existing `GoldenSession`
wrapper** (doc 02 §The wrapper). The Claude Agent SDK is the documented Phase 2 upgrade
path; `claude-agent-acp` and tmux are out of scope.

Selected via a weighted comparison (criteria: GOLD fit ×5, time-to-MVP ×5, maturity ×4,
ops/concurrency ×3, forward-compat ×2; see doc 01 §Comparison matrix for the full
dimensions):

| Option | Weighted score / 95 |
|---|---|
| **Headless CLI + `GoldenSession`** | **93** |
| Claude Agent SDK | 90 |
| Hermes claude-code skill | 78 |
| claude-agent-acp | 39 |
| tmux + send-keys | 34 |

> The official Hermes claude-code doc confirms **print mode (`claude -p`) is Hermes' own
> preferred mode** and returns JSON with `session_id` / `total_cost_usd` / `stop_reason` —
> independent confirmation of this choice. See
> [`05-integration-and-deployment.md`](./05-integration-and-deployment.md) §1 and Decision A2.

Why headless wins Phase 1 specifically:

- Headless and the SDK are the **same substrate** (identical CLI flags: `--session-id`,
  `--resume`, `--fork-session`); the SDK only adds a typed layer. Headless wins on
  time-to-MVP because `GoldenSession` is **already written and live-verified against Claude
  Code 2.1.x** (doc 02 §Verified behaviors).
- ACP's one genuine edge — native streaming and permission/decision events — only pays off
  if the MVP needs mid-task streaming (thread #1) or decision-detection (thread #3). Both are
  **deferred to Phase 2**, so blocking `--output-format json` with a final result object is
  sufficient and ACP's advantage never cashes in.
- **No lock-in:** because the SDK wraps the same flags and session semantics, migrating later
  (when streaming/decision-detection become real requirements) is low-cost and
  non-destructive.

## 3. Functional requirements

The Phase 1 build is less "write features" than "**guarantee these invariants and expose a
clean contract**." Most of F1–F7 already exist in `GoldenSession`; the under-built parts are
the guardrails (F5–F10). Each requirement below is testable — see §6.

### Capability floor (F1–F7)

| # | Requirement | Serves | Wrapper surface |
|---|---|---|---|
| **F1** | **Prime once.** Initialize GOLD from project context; a second prime on the same GOLD id MUST be refused. | context inherit / no-pollute | `prime()` + double-prime guard |
| **F2** | **Fork a task.** Start a task from GOLD's context; GOLD MUST stay pristine (transcript line count flat across forks). | start task / no-pollute | `run_task()` |
| **F3** | **Parseable result.** Every task returns a `TaskResult` exposing at least `session_id`, `is_error`, `terminal_reason`, `result`, `cost_usd`. | terminal status | `TaskResult` |
| **F4** | **Recover on failure.** Append a fix to an existing task by resuming the same session id (no fork), without losing prior progress. | continuable | `continue_task(sid, …)` |
| **F5** | **Bounded cost.** `max_turns` and `max_budget_usd` are mandatory per call; a task MUST abort at the cap. | safety | constructor caps |
| **F6** | **Correct workspace identity.** The wrapper always passes an explicit `cwd`; it MUST never rely on inherited process cwd. | no silent failure | wrapper enforces |
| **F7** | **GOLD protection.** `continue_task(golden_id, …)` MUST be refused (GOLD is append-forbidden). | no-pollute | guard / tripwire |

### Fail-loud guardrails (F8–F10)

A pre-mortem showed the capability floor defines *what the system does* but not *how it fails*.
The MVP's hardest property is **failing loudly, not silently** — both context corruption and
lost-progress otherwise fail quietly and look like success.

| # | Requirement | Prevents (silent failure) |
|---|---|---|
| **F8** | **Single-writer per session id.** Calls to the same session id MUST be serialized; concurrent forks off GOLD remain safe (distinct output files). | Transcript corruption from concurrent writes to one `.jsonl` (doc 02 gotcha 4). Distinct from F7's pollution guard. |
| **F9** | **Loud failure on session-not-found.** After `continue_task`, the wrapper MUST assert the returned session id equals the requested one and raise otherwise — never silently branch into a fresh, empty context. | A wrong-cwd resume silently starts a new session and loses all prior progress (doc 02 gotcha 2). Scariest case: it *looks* like success. |
| **F10** | **Retry ceiling.** A per-task `max_retries` / `max_continues` MUST bound the recover-on-failure loop. | Money runaway: per-call caps (F5) do not bound a task that retries N times (full chain ledger = thread #5, Phase 2). |

### Multi-trigger & human-readable resolution (F11)

| # | Requirement | Serves |
|---|---|---|
| **F11** | **Name-based GOLD resolution.** `golden_session` MUST resolve a human-readable **name** (via a registry) to `{golden_id, cwd, defaults, ceilings}`, so a caller references a memorable name instead of a UUID / absolute path. User-supplied overrides MUST be **clamped to the session's ceilings**. A `list` command MUST expose the available names + cwd + required/optional args for discovery. | usable triggering from chat surfaces; keeps identity in code (preserves F6/F7) |

### Integration requirements (on Hermes, not wrapper code)

- **IR1.** Hermes MUST persist `(task_id → current_session_id)` and update it on every
  successful call, so F4 targets the correct session. This is the floor for "continue without
  losing progress." The integration wiring (how the `/claude-code` skill invokes the wrapper)
  and deployment (how `claude` is installed in the container) are specified in
  [`05-integration-and-deployment.md`](./05-integration-and-deployment.md).
- **IR2.** A **gateway trigger adapter** (Discord) MUST parse `{name, task, overrides}` from an
  instant message, resolve the name via the registry (F11), enforce an **allowlist** of
  permitted user IDs and the per-session **budget/turn ceilings**, invoke `golden_session`, and
  relay the result + discovery hints back to the channel. MVP triggers **fresh forks by name
  only**. Detailed in [`05-integration-and-deployment.md`](./05-integration-and-deployment.md)
  (Decision A3).

## 4. Non-goals / deferred to Phase 2

Explicitly out of scope for Phase 1 (none is required to run one task end-to-end and recover):

- **Branching as a contracted feature.** `continue_task(fork=True)` exists in the wrapper and
  may ride along as *available-but-not-contracted*; Hermes Phase 1 does not depend on it, and
  branch-selection policy (thread #4) is deferred.
- **Streaming wrapper** (thread #1) — blocking JSON is sufficient for Phase 1.
- **Decision-detection protocol** (thread #3) — see §5 for the accepted gap.
- **Fork janitor** (thread #2) — manual cleanup only in Phase 1.
- **Chain-level budget ledger** (thread #5) — F10's retry ceiling is the Phase 1 stand-in.
- **Partial-output streaming** (thread #6).
- **Claude Agent SDK migration** — triggered only when streaming/decision-detection become
  requirements.
- **Continuation/retry from a chat surface (Discord)** — MVP triggers fresh forks by name;
  resuming a prior task over Discord (needs a task handle surfaced back + follow-up grammar)
  and live progress streaming are Phase 2. (Recover-on-failure F4 still works via direct
  CLI / automation.)

## 5. Accepted Phase 1 limitations (documented, not fixed)

- **"Green but garbage" decision gap.** A task that stalls at a decision point and exits 0 is
  indistinguishable from genuine success (real fix = thread #3, Phase 2). **Mitigation:**
  author tasks to **fail loud** (non-zero / explicit error) on ambiguity, and have Hermes
  treat *only* explicit success as success. Known trade-off, accepted for internal use.
- **Disk accumulation / transcript privacy.** Forked `.jsonl` files accumulate under
  `~/.claude/projects/<encoded-cwd>/` (janitor = thread #2, Phase 2). **Mitigation:** ship a
  manual `cleanup_forks(keep=…)` command and document the limit.

### Operational notes (no code, enforced by process)

- **Keep GOLD lean** — priming chatter inflates the per-fork prompt-cache write (~$0.05 floor,
  more for big GOLDs; doc 02 gotcha 3).
- **Freeze `CLAUDE.md` and hooks during a run** — forks inherit cwd-level configs, so changing
  them mid-run silently alters task behavior (doc 02 gotcha 5).

## 6. Acceptance criteria

The MVP is accepted when all of the following pass end-to-end:

1. **Prime + pristine GOLD (F1, F2, F7):** prime a GOLD session; run 3 forked tasks; the GOLD
   `.jsonl` line count stays flat across all forks; `prime()` called twice and
   `continue_task(golden_id, …)` both raise.
2. **End-to-end task (F2, F3):** a real task writes to `output/`; `TaskResult.is_error` is
   `False`, the output file exists, and `cost_usd` is populated.
3. **Recover on failure (F4):** a deliberately failed task is fixed via
   `continue_task(sid, fix)`; the same session id is returned, its transcript grew, and the
   task now succeeds.
4. **Budget + retry caps (F5, F10):** with a tiny `max_budget_usd` / `max_turns` and a low
   retry ceiling, the task aborts at the cap and the recover loop stops at the ceiling.
5. **cwd correctness + loud not-found (F6, F9):** `continue_task` called from the wrong cwd
   raises (it does **not** silently start a fresh context).
6. **Single-writer (F8):** two concurrent `continue_task` calls on one session id serialize;
   the resulting `.jsonl` is not interleaved or corrupted.
7. **Gateway trigger (F11, IR2):** a Discord message `run on <name>: <task>` from an
   allowlisted user forks a task from that GOLD and replies with the result; an **unknown name**
   returns hints listing valid names; a **non-allowlisted** user is rejected; a user **budget
   override above the ceiling is clamped**; `list` returns the available sessions.

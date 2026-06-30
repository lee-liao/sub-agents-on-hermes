# Open Threads

Follow-up work on the Hermes → Claude Code task management architecture that has been identified but not yet picked up. Roughly ordered by increasing complexity, but priority is the user's call.

> **Phase 1 status (2026-06-30): SHIPPED.** Threads #1–#7 remain open — none
> was closed by Phase 1; they were all explicitly deferred per PRD §4. Threads
> #8 and #9 were added after deploy surfaced two issues the pre-mortem missed.
> See [`04-phase1-mvp-prd.md`](./04-phase1-mvp-prd.md) Status header for the
> per-criterion outcome.

## Thread 1 — Streaming variant of the wrapper

Switch the wrapper from `--output-format json` (which blocks until the CLI exits) to `--output-format stream-json --verbose`. Newline-delimited JSON events arrive as the task runs: tool calls, partial messages, retries, and a final result object.

**Why it matters:** without streaming, Hermes is blind to task progress until the process exits. A long-running task could be stuck for minutes and Hermes wouldn't know whether to wait or kill. Streaming also unlocks mid-task decision detection (thread #3) — Hermes can react to a `DECISION_NEEDED` sentinel as soon as it appears rather than after the task finishes.

**Shape of the work:** add a `run_task_streaming()` method that yields `StreamEvent` objects. Keep `run_task()` for callers who just want the final result.

## Thread 2 — Janitor for forked sessions

Implement `cleanup_forks_by_age(max_age_hours, keep=...)` that deletes forked session files older than the threshold, preserving GOLD and any sids Hermes is actively tracking.

**Why it matters:** every task creates a new `.jsonl` under `~/.claude/projects/<encoded-cwd>/`. After thousands of tasks, that's real disk space. There's also a privacy angle — old transcripts may contain sensitive data from past runs.

**Open question:** how does the janitor know which sids are "actively tracked"? Options: (a) Hermes passes a `keep` set explicitly, (b) the wrapper writes a manifest of active sids to disk, (c) the janitor reads Hermes' task store.

## Thread 3 — Decision-detection protocol

Establish a prompt convention so Claude emits a structured sentinel when it cannot proceed autonomously, instead of treating "I need a decision" as normal completion. Candidates:

```
DECISION_NEEDED: <one-line question>
OPTIONS:
  A: <option A description>
  B: <option B description>
```

Hermes parses the result text (or watches stream events per thread #1), routes to a human or policy engine, then resumes the fork with the chosen option.

**Why it matters:** the current pattern treats every successful completion the same. A task that finished because it couldn't decide what to do next looks identical to a task that finished because it succeeded. Without a sentinel, Hermes can't tell when to invoke the retry/branch flow.

**ACP alternative:** claude-agent-acp has native permission-request events that solve part of this — see `01-considered-approaches.md`. Worth comparing before committing to a prompt convention.

## Thread 4 — Branch selection policy

When a decision point triggers `fork=True` for multiple alternatives (try A, try B, try C), how does Hermes pick the winner? Candidates:

- **Lowest cost** — min `total_cost_usd` among successful branches.
- **Best result text** — semantic scoring (LLM-as-judge? rubric?).
- **Tool-call success rate** — branches with fewer failed tool calls win.
- **First to complete** — race semantics, kill the rest.
- **Human pick** — present branches to a human reviewer.

And how are losers cleaned up? `cleanup_forks(keep={winner_sid})` after selection.

**Why it matters:** without a policy, branches either accumulate forever (waste) or get cleaned up arbitrarily (lose good work). The choice also affects cost — racing branches in parallel is faster but more expensive than sequential with early exit.

## Thread 5 — Cost observability across the retry chain

Aggregate `total_cost_usd` across a task plus its whole retry/branch chain, and enforce per-task budgets that span the chain rather than per-call.

**Why it matters:** the current `max_budget_usd` is per CLI invocation. A task that retries five times can spend 5× the per-call cap. For a service processing many tasks, this drift compounds. Need a chain-level ledger.

**Shape of the work:** add a `TaskChain` abstraction that tracks `(task_id, [session_id, session_id, ...])` and accumulates `total_cost_usd`. Hermes sets a chain-level budget; the wrapper refuses new `continue_task` calls past the threshold.

## Thread 6 — Streaming partial outputs

As `output/` files get written mid-task, surface them to Hermes without waiting for full process exit. Useful for: dashboards, early validation, downstream pipeline triggers.

**Why it matters:** some tasks produce large artifacts (datasets, model weights, generated code) that take minutes to write. Waiting for the CLI to exit before Hermes can see them adds latency to the overall pipeline.

**Shape of the work:** filesystem watcher on `output/` per task. Emit events on file create/modify/close. Stream alongside the JSON message stream from thread #1.

## Thread 7 (added during approach comparison) — ACP-specific GOLD workaround

If claude-agent-acp is chosen despite its poor GOLD fit, design the workaround precisely so it can be costed against the headless path. Options:

- **One ACP server per task, externally primed.** No shared GOLD — each task spawns a fresh server, runs an external priming script, then the task. Loses the "prime once" benefit.
- **Snapshot/restore.** Use ACP for the conversation, but reach under it to manipulate the underlying Claude Agent SDK session files directly (copy `GOLD.jsonl` to a per-task file before each task starts). Fragile — depends on undocumented internals.
- **ACP + thin headless side-channel.** ACP for task execution, headless CLI for priming and fork bookkeeping. Hybrid; gets the worst of both operationally.

**Why it matters:** only worth pursuing if ACP's native permission events (thread #3) are compelling enough to justify the engineering tax. If headless's prompt-convention approach to thread #3 is good enough, this thread is moot.

## Picking up a thread

When the user signals to start one, find the corresponding entry in `/home/lee/.claude/projects/-home-lee-hermes-docker-lee/memory/project_hermes_open_threads.md` — that's the cross-conversation reference. This document is the in-project design record; memory is the index.

## Thread 8 (added after Phase 1 deploy) — Skill auto-loading is model-dependent

The `claude-code-gold` skill ships a `SKILL.md` that documents how to delegate via `golden_session`. The Phase 1 assumption was that the gateway agent would read `SKILL.md` and route "run on X:" triggers accordingly. **glm-5.2 did not** — without an explicit `platform_hints.discord.append` system-prompt injection, the model used its own file/terminal tools to do the work directly (9 API calls, buttons, clarifications, direct patch, git commit), defeating the GOLD pattern's "no per-task chatter in shared context" guarantee.

**Why it matters:** the workaround is per-platform (`platform_hints.discord.append`, `platform_hints.slack.append`, etc.) and per-trigger-grammar. Every new skill that depends on the agent choosing delegation over direct action will need the same workaround. That doesn't scale, and a future model may behave differently again.

**Shape of the work:**
- Audit which models auto-load `SKILL.md` and which don't (claude Sonnet/Opus likely do; glm/qwen/kimi-class models may not).
- Decide between: (a) stricter guardrails that refuse direct file edits when a registered skill matches the trigger, (b) auto-generated `platform_hints` from the skill registry, (c) doc + acceptance criteria that require per-model verification before shipping a skill.
- The `platform_hints.discord.append` value currently in `config.yaml` is the deployment-specific fix — promote it to code if (b) is chosen.

## Thread 9 (added after Phase 1 deploy) — Hermes env-blocklist surface area

The Phase 1 deploy hit a 2-hour auth blocker because Hermes silently strips `ANTHROPIC_BASE_URL` from terminal subprocess envs (security blocklist in `tools/environments/local.py`). The blocklist is dynamic — built from `PROVIDER_REGISTRY` plus a hardcoded set — and `env_passthrough` cannot override it (GHSA-rhgp-j443-p4rf). The `_HERMES_FORCE_` prefix is the only escape hatch.

**Why it matters:** any future integration that runs a third-party tool via `terminal()` and depends on env vars that look "secret-y" (base URLs, API endpoints, custom provider config) can hit the same wall. The fix is documented for `ANTHROPIC_BASE_URL` (`docs/HERMES_DEPLOY_AND_TEST.md` Phase B gotcha), but the general pattern is untracked.

**Shape of the work:**
- One-time audit script that diffs the deployment's required env vars (from `.env`, compose `environment:`, and any tool the wrapper calls) against the live `_build_provider_env_blocklist()` output. Run on every Hermes image bump.
- Decide whether the `_HERMES_FORCE_` prefix should be set automatically for vars the operator has explicitly put in compose `environment:` (treat compose as opt-in force), or kept as an explicit per-var escape hatch (current behavior).
- Document the pattern in `docs/HERMES_HOME_AND_OS_HOME.md` or a sibling ops doc so future maintainers don't re-derive it.

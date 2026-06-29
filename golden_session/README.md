# golden_session — Hermes → Claude Code orchestration (Phase 1 MVP)

Code-in-the-loop engine that drives the headless `claude -p` CLI using the GOLD
session pattern: **prime once → fork per task → resume to recover**, with the
Phase 1 contract (F1–F11) enforced in code, not improvised by an LLM.

This implements [`docs/prd/04-phase1-mvp-prd.md`](../docs/prd/04-phase1-mvp-prd.md)
on the substrate selected there (headless CLI + `GoldenSession`). Wiring &
deployment live in [`docs/prd/05-integration-and-deployment.md`](../docs/prd/05-integration-and-deployment.md).

Zero third-party runtime dependencies (stdlib only) so it deploys onto the Hermes
bind mount with no pip step (Decision D1).

## Layout

| File | Responsibility (SRP) |
|---|---|
| `session.py` | `GoldenSession` engine — F1, F2, F4, F5, F6, F7, F9, F10 |
| `locking.py` | single-writer-per-session-id serialization — F8 |
| `registry.py` | name → `{golden_id, cwd, defaults, ceilings}` + clamp — F11 |
| `result.py` | `TaskResult` (parseable terminal status) — F3 |
| `runner.py` | the `claude -p` subprocess seam (injectable) + `ensure_claude` (D2) |
| `cli.py` | `golden_session` command — prime/run/continue/list/cleanup/remove |
| `gateway.py` | reference Discord/IM trigger adapter — IR2 |
| `errors.py` | the fail-loud exception hierarchy |

## The contract → where it lives → how it's tested

| # | Requirement | Code | Test |
|---|---|---|---|
| F1 | Prime once; double-prime refused | `GoldenSession.prime` | `test_double_prime_is_refused` |
| F2 | Fork a task; GOLD stays pristine | `GoldenSession.run_task` | `test_prime_then_three_forks_keep_gold_flat` |
| F3 | Parseable `TaskResult` | `result.py` | `test_task_result_is_parseable_and_populated` |
| F4 | Recover on failure (append, same sid) | `GoldenSession.continue_task` | `test_recover_appends_to_same_session` |
| F5 | Bounded cost (mandatory caps) | constructor + `_build_args` clamp | `test_caps_*`, `test_per_call_override_is_clamped_down_to_ceiling` |
| F6 | Correct workspace identity (explicit cwd) | constructor + `runner` cwd | `test_workspace_is_mandatory` |
| F7 | GOLD protection (`continue_task(gold)` refused) | `continue_task` guard | `test_continue_on_gold_is_refused` |
| F8 | Single-writer per session id | `locking.session_lock` | `test_concurrent_appends_serialize` |
| F9 | Loud failure on session-not-found | `continue_task` id-equality assert | `test_continue_from_wrong_cwd_raises_loudly` |
| F10 | Retry ceiling | `continue_task` ledger | `test_retry_loop_stops_at_ceiling` |
| F11 | Name-based resolution + clamp | `registry.py` | `test_registry.py`, `test_gateway.py` |
| IR2 | Gateway trigger adapter | `gateway.py` | `test_gateway.py` |

## Library usage

```python
from golden_session import GoldenSession

gs = GoldenSession(
    workspace="/opt/data/projects/billing-api",   # F6 — always explicit
    golden_id="f47ac10b-…",                        # store once in Hermes (IR1)
    max_turns=20, max_budget_usd=1.0,              # F5 — mandatory caps
    max_continues=3,                               # F10 — retry ceiling
    allowed_tools=["Read", "Edit", "Bash"],
)

gs.prime("…stable project context…")               # F1 — once

t = gs.run_task("add retries to outbound HTTP")     # F2 — fork; new sid
if t.is_error:
    fixed = gs.continue_task(t.session_id, "fix: …")  # F4 — recover (append)
```

## CLI usage

```bash
golden_session prime --name billing-api \
  --cwd /opt/data/projects/billing-api \
  --context-file CONTEXT.md \
  --max-turns 20 --max-budget-usd 0.50 \
  --ceiling-turns 40 --ceiling-budget 2.00 \
  --tools Read Edit Bash                       # prints golden_id, writes registry.json

golden_session run  --name billing-api --task "add a healthcheck endpoint"
golden_session run  --name billing-api --task "fix the test" --budget 1.00   # clamped to ceiling
golden_session continue --name billing-api --session-id <sid> --task "fix: …"  # F4 (direct/automation)
golden_session list
golden_session cleanup --name billing-api --keep <winner-sid>
```

Every command prints a JSON object (`{"ok": …}`); errors print a structured JSON
error to stderr with `known_names` hints for unknown names. Override the registry
path with `--registry` or `$GOLDEN_SESSION_REGISTRY`; override the transcript root
with `$GOLDEN_SESSION_PROJECTS_DIR` (the container's `$HOME` differs — doc 05).

## Gateway (IR2) — chat trigger

`gateway.GatewayAdapter` is the transport-agnostic reference adapter:

```python
from golden_session import GatewayAdapter, Registry

adapter = GatewayAdapter(Registry(), allowlist={"discord-user-id-1"})
reply = adapter.handle(user_id, "run on billing-api: add retries  budget=1.00")
# reply.text -> post back to the channel
```

It enforces the two trigger-boundary guardrails (allowlist + ceiling clamp) and
resolves identity in code (the caller never supplies `golden_id`/`cwd`). MVP
triggers **fresh forks by name only**; continuation & streaming are Phase 2.

## Accepted Phase 1 limitations (from the PRD §5)

- **"Green but garbage" decision gap** — author tasks to fail loud; treat only
  explicit success as success.
- **Disk accumulation** — `cleanup_forks(keep=…)` is the manual janitor; an
  age-based janitor is Phase 2 (thread #2).

## Tests

```bash
pip install -e .[test]   # or just: python -m pytest
python -m pytest
```

The suite drives a faithful in-memory fake of `claude -p` (`tests/conftest.py`)
that reproduces the cwd-scoped session lookup, pristine-GOLD forks, and
silent-fresh-session bug — so all guardrails are verified without auth or the
real binary.

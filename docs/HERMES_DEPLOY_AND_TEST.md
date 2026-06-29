# Hermes Agent Runbook — Deploy & Test `golden_session` (Phase 1 MVP)

> **Audience: the Hermes Agent.** This is a guide you *read and follow*, not code
> that runs on its own. You execute the steps via your `terminal()` function. It
> deploys and verifies the `golden_session` engine that implements
> [`prd/04-phase1-mvp-prd.md`](./prd/04-phase1-mvp-prd.md); design & wiring detail
> is in [`prd/05-integration-and-deployment.md`](./prd/05-integration-and-deployment.md).

## Prime directives (read first)

1. **Fail loud, never improvise around a guardrail.** If a step's check fails,
   **stop and report** with the exact command + output. Do **not** "work around"
   it by calling raw `claude -p`, dropping a budget cap, or guessing a path —
   that defeats the entire purpose of this engine (F5–F10).
2. **Verify, don't assume.** Run each ✅ *check* and confirm its expected result
   before moving on. Paths like `$HOME` differ between hosts (see Phase A).
3. **Two roles.** Steps tagged **[OPERATOR]** need a human on the Docker host
   (editing compose, handling secrets, copying files). Steps tagged **[AGENT]**
   are yours to run inside the container via `terminal()`. When you hit an
   **[OPERATOR]** step you cannot do, post the instruction and wait.
4. **Money is real.** Only Phase D and Phase F-2 spend money (real `claude`
   calls). Everything before that is free. Keep caps tiny during testing.

Environment facts (from `docker-compose.yml`): container `hermes-lee`, runtime
user `hermes`, bind mount host `/home/lee/.hermes` ↔ container `/opt/data`
(persists across image updates), projects `/home/lee/projects` ↔
`/opt/data/projects`, this repo `/home/lee/hermes-docker-lee` ↔
`/opt/data/hermes-docker-lee` (read-only — you can read the runbook and package,
cannot write back), and `PATH` already includes `/opt/data/.local/bin`. `$HOME`
is `/opt/data` for every process (matches `/etc/passwd`; `terminal.home_mode: real`
in `~/.hermes/config.yaml` — set so the gateway's terminal subprocesses don't drift
to a fake `$HERMES_HOME/home` and lose the credential dotdirs). Auth vars
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and `API_TIMEOUT_MS` are substituted
from the host `.env` (see Phase B and `.env.example`).

---

## Phase A — Preflight (free) **[AGENT]**

Run these and capture outputs. All commands assume you are the `hermes` user
inside the container (your `terminal()` already is). The host equivalent for an
operator is `docker exec -u hermes hermes-lee bash -lc '<cmd>'`.

```bash
# A1. Base runtime: Node (from the image) + claude (from the bind mount)
node --version            # expect v20+/v22
claude --version          # expect 2.1.x  (doc 05: v2.1.195 was installed)

# A2. Confirm $HOME — doc 05 and TROUBLESHOOTING.md disagree; the truth wins.
echo "HOME=$HOME"         # expect /opt/data (matches /etc/passwd; terminal.home_mode: real)
echo "PATH=$PATH"

# A3. Where claude stores sessions (must be under the real $HOME, on the mount)
ls -d "$HOME/.claude/projects" 2>/dev/null || echo "no projects dir yet (ok)"

# A4. Python for the wrapper (stdlib only; 3.10+)
python3 --version
```

✅ **Check:** `node`, `claude`, and `python3` all print a version, and `$HOME`
resolves under `/opt/data...` (i.e. on the bind mount).
🛑 **If `claude` is missing:** install it user-local (no sudo in this container):
`npm install -g @anthropic-ai/claude-code` with an npm prefix on the mount (doc 05
§3 / Decision D1), then re-check. If `node` is also missing, stop and report —
the image lacks the runtime.

> **Record the real `$HOME`** from A2. Everywhere below that says `$HOME`, use the
> value you just confirmed.

---

## Phase B — Auth (the known blocker, D3) **[OPERATOR]**

Headless automation cannot complete an interactive login. Use either a long-lived
API key **or** an auth-token + custom endpoint pair — **not** OAuth device flow.
This deployment uses the second form (see `.env.example` for both templates).

**[OPERATOR / host]** populate `.env` from `.env.example`, ensure
`docker-compose.yml` references the matching vars, and recreate (a restart is
*not* enough — env only takes effect on recreate):

```yaml
services:
  hermes:
    environment:
      # Form 1 — direct Anthropic API key:
      ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"
      # Form 2 — custom endpoint / relay (this deployment's choice):
      ANTHROPIC_AUTH_TOKEN: "${ANTHROPIC_AUTH_TOKEN}"
      ANTHROPIC_BASE_URL:   "${ANTHROPIC_BASE_URL}"
      API_TIMEOUT_MS:       "${API_TIMEOUT_MS}"
      # — NEVER commit `.env`; `.env.example` is the template.
```
```bash
docker compose up -d
```

**[AGENT]** verify auth works with a tiny real call:

```bash
claude -p "reply OK" --max-turns 1
```

✅ **Check:** the call returns without an auth error.
🛑 **If it fails with `loggedIn: false` / auth error:** stop and tell the operator
"auth blocker D3 — set the auth vars in `.env` (see `.env.example`) and
`docker compose up -d`." No delegation runs until this passes.

---

## Phase C — Install the wrapper onto the bind mount **[OPERATOR]** + **[AGENT]**

The wrapper must live on `/opt/data` so it survives image updates alongside
`claude`. It is pure stdlib — **no pip required**.

This repo is bind-mounted read-only at `/opt/data/hermes-docker-lee`, so you
can install directly from inside the container without `docker cp`.

**[AGENT]** install the package + shim from the mounted repo (paths use the
`$HOME` confirmed in A2; this example uses `/opt/data`):

```bash
install -d /opt/data/.local/lib /opt/data/.local/bin
cp -r /opt/data/hermes-docker-lee/golden_session /opt/data/.local/lib/
cp /opt/data/hermes-docker-lee/bin/golden_session /opt/data/.local/bin/
chmod 755 /opt/data/.local/bin/golden_session

# Optional: also copy the test suite so you can self-test in-container (Phase F-1):
cp -r /opt/data/hermes-docker-lee/tests /opt/data/.local/lib/tests
cp /opt/data/hermes-docker-lee/pyproject.toml /opt/data/.local/lib/pyproject.toml
```

**[OPERATOR / host]** alternative — if the repo is NOT mounted into the container,
copy from a host checkout instead (paths use the `$HOME` confirmed in A2; this
example uses `/opt/data`):

```bash
# from a checkout of this repo on the host:
docker cp golden_session  hermes-lee:/opt/data/.local/lib/golden_session
docker cp bin/golden_session hermes-lee:/opt/data/.local/bin/golden_session
docker exec -u hermes hermes-lee bash -lc 'chmod 755 /opt/data/.local/bin/golden_session'

# Optional: also copy the test suite so the agent can self-test in-container (Phase F-1):
docker cp tests       hermes-lee:/opt/data/.local/lib/tests
docker cp pyproject.toml hermes-lee:/opt/data/.local/lib/pyproject.toml
```

Point the shim at the lib dir and ensure its `bin` is on `PATH`. The shim reads
`$GS_LIB` (default `/opt/data/.local/lib`); set it if your `$HOME`/layout
differs. `/opt/data/.local/bin` is already on `PATH` per compose — if you put the
shim there instead, no `.bashrc` edit is needed.

```bash
# if the shim's bin dir is NOT already on PATH, add it (same trick the claude install used):
docker exec -u hermes hermes-lee bash -lc \
  'grep -q ".local/bin" ~/.bashrc || echo "export PATH=\$HOME/.local/bin:\$PATH" >> ~/.bashrc'
```

**[AGENT]** verify the CLI resolves and runs:

```bash
golden_session --help
golden_session list          # expect: "No sessions registered."
```

✅ **Check:** `--help` prints the subcommands; `list` runs cleanly (exit 0).
🛑 **If `golden_session: command not found`:** the shim isn't on `PATH` or `GS_LIB`
is wrong. Fall back to `GS_LIB=$HOME/.local/lib python3 -m golden_session list`
and report the PATH gap to the operator.

---

## Phase D — Create the registry & prime a GOLD (spends a little) **[AGENT]**

Pick a stable workspace under `/opt/data/projects/<name>` (session lookup is
cwd-scoped — F6/F9). Prime **once** per project; priming twice is refused (F1/F7).

```bash
golden_session prime \
  --name billing-api \
  --cwd /opt/data/projects/billing-api \
  --context-file /opt/data/projects/billing-api/CONTEXT.md \
  --description "Billing service — Python/FastAPI" \
  --tools Read Edit Bash \
  --max-turns 20 --max-budget-usd 0.50 \
  --ceiling-turns 40 --ceiling-budget 2.00

golden_session list          # confirm the name now appears with cwd + args
```

✅ **Check:** `prime` prints `{"ok": true, ... "golden_id": "<uuid>"}` and `list`
shows `billing-api`. The registry persists at `$HOME/.golden_session/registry.json`.
🛑 **If prime says the name already exists:** that GOLD is already primed — do
**not** re-prime (it's sacred). Use the existing one or pick a new name.

> Keep `CONTEXT.md` lean — every fork pays a fresh prompt-cache write (~$0.05
> floor; doc 02 gotcha 3). Freeze `CLAUDE.md`/hooks during runs (gotcha 5).

---

## Phase E — Wire the triggers **[OPERATOR]** + **[AGENT]**

**[OPERATOR]** install the thin GOLD-aware skill so you (the agent) are told to
delegate via `golden_session` instead of raw `claude -p`:

```bash
docker cp skills/claude-code-gold hermes-lee:/opt/data/skills/claude-code-gold
docker exec -u hermes hermes-lee ls /opt/data/skills/claude-code-gold/SKILL.md
```

**[OPERATOR]** configure the Discord gateway (bot token + channel) via
`hermes setup`, and set the **allowlist** of permitted Discord user IDs that may
trigger tasks. The allowlist + the registry `ceilings` are the trigger-boundary
guardrails (IR2) — without them, anyone in a channel can spend money.

✅ **Check:** the skill file is present; `hermes status` shows the Discord
connection up; the allowlist contains at least one real user ID.

---

## Phase F — Test

### F-1. Offline self-test of the engine (free, no auth) **[AGENT]**

The shipped suite proves the F1–F11 guardrails against a faithful fake of
`claude -p` — run it in-place to validate the deployed code without spending:

```bash
cd /opt/data/.local/lib            # the dir CONTAINING the golden_session package + tests
python3 -m pytest -q                    # if tests/ was copied alongside the package
# (if tests aren't deployed, run this on the host checkout instead)
```

✅ **Check:** all tests pass (35 green). This covers acceptance criteria 1–7's
*logic*: pristine GOLD, double-prime/GOLD guards, recover, caps+retry ceiling,
wrong-cwd loud failure, single-writer, gateway authz/clamp/hints.
🛑 **If any fail:** the deployed copy is broken — stop and report which test.

### F-2. Live acceptance criteria (spends real money — keep caps tiny) **[AGENT]**

Run these against the primed `billing-api` GOLD. Each maps to PRD §6.

```bash
# Criterion 2 — end-to-end real task writes to output/, returns a clean result
golden_session run --name billing-api --task "create output/hello.txt containing OK"
#   expect: {"ok": true, "is_error": false, "session_id": "...", "cost_usd": >0}
#   then confirm the file:  ls /opt/data/projects/billing-api/output/hello.txt

# Criterion 1 — GOLD stays pristine across forks
golden_session run --name billing-api --task "list the repo's top-level files"
golden_session run --name billing-api --task "print the python version used"
#   expect each returns a NEW session_id; GOLD transcript line count stays flat.

# Criterion 7 — gateway trigger (via Discord, from an ALLOWLISTED user):
#   "run on billing-api: add a /health endpoint"   -> forks + replies with result
#   "run on nope: do x"                             -> reply lists valid names
#   "list"                                          -> reply shows the sessions
#   "run on billing-api: x   budget=99"             -> effective budget clamped to 2.00
#   (a NON-allowlisted user is rejected)
```

✅ **Check:** Criterion 2 returns `is_error: false` and the output file exists;
forks return distinct session ids; the Discord flows behave as commented.
🛑 **Treat only explicit success as success.** A task that exits 0 but stalled at
a decision is a *known Phase 1 gap* (PRD §5) — if a `result` looks evasive or the
expected artifact is missing, report it as a failure, not a pass.

> **Recover-on-failure (Criterion 3 / F4)** is exercised via direct CLI /
> automation in Phase 1, not over Discord:
> `golden_session continue --name billing-api --session-id <sid> --task "fix: ..."`
> — returns the **same** sid and grows its transcript. Continuation from chat is
> Phase 2.

---

## Housekeeping & rollback **[AGENT]**

```bash
# Manual fork janitor (Phase 1 has no age-based janitor — thread #2):
golden_session cleanup --name billing-api --keep <winner-sid>   # GOLD always kept

# Remove a registry alias (does NOT delete transcripts):
golden_session remove --name billing-api
```

Persistence recap: `claude`, the wrapper, `registry.json`, GOLD transcripts
(`$HOME/.claude/projects/…`), and auth (`$HOME/.claude.json`) all live on the
bind mount → they survive `docker compose up -d` and image updates. Only Node
comes from the image (Decision D2 risk; `ensure_claude()` is the opt-in self-heal).

---

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `claude` auth error / `loggedIn: false` | D3 blocker — no API key | **[OPERATOR]** set `ANTHROPIC_API_KEY`, `docker compose up -d` (recreate, not restart) |
| `golden_session: command not found` | shim not on PATH / wrong `GS_LIB` | use `GS_LIB=$HOME/.local/lib python3 -m golden_session …`; fix PATH in `~/.bashrc` |
| `SessionNotFoundError` on continue | wrong cwd — F9 caught a silent fresh session | run from the **registered** workspace; never pass an ad-hoc cwd |
| `DoublePrimeError` / "name already exists" | GOLD already primed (F1/F7) | reuse it; never re-prime a name |
| `RetryCeilingError` | hit `max_continues` (F10) | intended — stop retrying; investigate the task, raise the ceiling only deliberately |
| run aborts immediately at a tiny cap | F5 budget/turn cap hit | expected under tiny test caps; raise within the session's ceiling |
| Discord trigger ignored | user not on allowlist (IR2) | **[OPERATOR]** add the user ID to the allowlist |
| files owned by root after a step | ran as root, not `hermes` | always `docker exec -u hermes` (TROUBLESHOOTING.md Issue 2) |

---

## Report template (post this when done or blocked)

```
Deploy & test — golden_session Phase 1
Host $HOME confirmed: <value>
Phase A preflight:   PASS / FAIL (<detail>)
Phase B auth (D3):   PASS / BLOCKED (<detail>)
Phase C install:     PASS / FAIL
Phase D prime:       PASS / FAIL  (golden_id, name)
Phase E triggers:    PASS / FAIL  (skill present?, allowlist set?)
Phase F-1 self-test: <n> passed / <m> failed
Phase F-2 live:      criteria {1,2,3,7} -> PASS/FAIL each, total cost $<x>
Blockers / next step: <...>
```

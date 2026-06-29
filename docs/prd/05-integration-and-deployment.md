# Integration & Deployment — Hermes ↔ Claude Code

> The missing "wiring" view. Docs 01–04 specify the orchestration *mechanism* (GOLD
> pattern, `GoldenSession`, the Phase 1 contract F1–F10). This doc specifies how that
> mechanism actually plugs into Hermes and how Claude Code is deployed inside the Hermes
> container. Companion to [`04-phase1-mvp-prd.md`](./04-phase1-mvp-prd.md) (it grounds the
> PRD's integration requirement **IR1**).

## 1. How the upstream Hermes claude-code skill actually works

Verified against the upstream source
(`NousResearch/hermes-agent/skills/autonomous-ai-agents/claude-code/SKILL.md`):

- In this deployment, **`/claude-code` is a real command — but it loads a *document*, not a
  tool.** It reads the skill's markdown (an orchestration guide) into the agent's context;
  **nothing in it executes on its own.** The agent then *follows* the guide, calling the
  `claude` binary via Hermes' `terminal()` function. (Confirmed live: invoking `/claude-code`
  with no task made the agent run only the guide's "Prerequisites: install + auth" step, then
  stop at the first blocker.)
- It **assumes `claude` is pre-installed** (`npm install -g @anthropic-ai/claude-code`,
  v2.x+) and that **auth is already configured**. The skill does not install or authenticate.
- Its **preferred** pattern is print mode (`claude -p … --allowedTools … --max-turns …`) —
  the same substrate this project chose in doc 04 (Decision 1).
- Crucially: it **does not manage persistent sessions by default**. Multi-turn work needs
  `--continue`/`--resume` with *"explicit Hermes orchestration"*.

> **That missing session layer is exactly what our GOLD solution is.** The upstream skill
> runs raw `claude -p` per task and stops; `GoldenSession` (prime-once / fork-per-task /
> resume-to-recover, with guardrails F1–F10) is the "explicit orchestration" the skill says
> it needs.

Because the skill is a guide the agent *follows* (not executable code), **the agent is the
executor.** Making it GOLD-aware therefore means two things: (1) install the `golden_session`
CLI on the volume (§3), and (2) author/extend the guide so the agent is instructed to delegate
via that CLI instead of raw `claude -p`. There is no code in the skill to "swap out" — you
change the *instructions the agent reads* and give it a better tool to call.

## 2. Integration architecture — trigger vs. engine

```
/claude-code (trigger)  →  GoldenSession wrapper (engine)  →  claude -p (substrate)
 Hermes skill / terminal()   prime/fork/resume + F1–F10          headless print mode
```

The skill remains the entry point; we replace its **raw `claude -p` call** with a call to
the wrapper. The skill's existing parameters map 1:1 onto `GoldenSession`:
`--allowedTools` → `allowed_tools`, `--max-turns` → `max_turns`, `--max-budget-usd` →
`max_budget_usd`, `--model` → model, `workdir` → `cwd`, `--output-format` → wrapper internal.

### Decision A1 — wiring approach

| Option | How | Trade-off |
|---|---|---|
| **B — wrapper CLI + thin skill (recommended)** | Install `golden_session` as a command in the container; a thin custom skill calls *it* via `terminal()` | Upstream skill untouched → no fork-drift; cleanest to maintain |
| A — GOLD-aware skill | Fork/customize the upstream skill so its task path calls `golden_session.py` instead of `claude -p` | Fewer moving parts, but you now maintain a fork of the upstream skill |

**Recommendation: Option B**, pending confirmation against the actual `/claude-code` skill
on the host. Either way the wrapper must be installed on the persistent volume (§3) so it
survives image updates alongside `claude`.

### Decision A2 — control plane: code-enforced, not agent-improvised

The deeper question is not *how* to call Claude Code but *who holds the orchestration logic*:

| | Agent-in-the-loop (`/claude-code` guide) | Code-in-the-loop (`GoldenSession` process) |
|---|---|---|
| Decides `--resume` vs `--fork-session`, cwd, budget caps | the **LLM agent**, improvising `terminal()` calls per the markdown | **code**, deterministically |
| GOLD invariants F1–F10 | *suggested* by the guide, not enforced | *enforced* in code |
| Official Hermes doc stance | this is what it documents | not covered — **no golden/primed pattern exists** |

The official Hermes doc presents Claude Code as a CLI the **agent itself** drives via
`terminal(command="claude -p …")`, with **task-specific sessions and no golden reuse**. That
is fine for an ad-hoc coding request, but it **cannot guarantee** the Phase 1 contract: an LLM
improvising calls will eventually `--resume` onto GOLD (breaks F7), drop the explicit cwd
(F6/F9 → silent fresh context), skip a budget cap (F5), or double-write a session id (F8) — and
there is no golden pattern in the guide to follow in the first place. Invariants belong in
code, not a prompt.

**Decision:** the production task pipeline runs through the **code-in-the-loop wrapper
process**; `/claude-code` is the **trigger + knowledge** layer (how a task is initiated and how
the agent knows to delegate), never the orchestrator. (Corollary: the doc runs *parallel* tasks
via tmux around `claude -p` — an agent-in-the-loop workaround; the wrapper spawns subprocesses
directly, so Phase 1 needs no tmux.)

### IR1 restated (the glue)

Hermes persists `(task_id → current_session_id)` and updates it on every successful call,
so recover-on-failure (F4) targets the right session. This *is* the "explicit Hermes
orchestration" the upstream skill defers to the caller.

### Gateway trigger surface — Discord → `golden_session`

The Hermes gateway accepts instant messages (Discord, etc.), making a chat message a third
**interactive trigger** alongside in-chat `/claude-code` and the programmatic direct call.
The A2 split holds: the **agent** does the flexible parsing; the **registry + `golden_session`**
enforce identity and invariants.

```
Discord IM ─▶ gateway ─▶ Hermes agent
   1. parse {name, task, overrides}     (agent, NL)
   2. resolve name ─▶ REGISTRY ─▶ {golden_id, cwd, defaults, ceilings}   (code)
   3. authz (allowlist) + clamp overrides to ceilings                    (code)
   4. terminal("golden_session run --name <name> --task '…'")  → blocking, F1–F10
   5. TaskResult ─▶ agent replies (result, cost, output path)
```

**Registry (requirement F11).** A manifest on the persistent volume
(`/opt/data/home/.golden_session/registry.json`) maps a human-readable **name** →
`{golden_id, cwd, description, defaults, ceilings}`. It materializes doc 02's "one GOLD per
workspace" policy and adds an alias plus per-session defaults/ceilings:

```jsonc
{
  "billing-api": {
    "golden_id": "f47ac10b-…",            // stored once, never shown to users
    "cwd": "/opt/data/projects/billing-api",
    "description": "Billing service — Python/FastAPI",
    "defaults": { "allowed_tools": ["Read","Edit","Bash"], "max_turns": 20, "max_budget_usd": 0.50, "model": "sonnet" },
    "ceilings": { "max_turns": 40, "max_budget_usd": 2.00 }   // user overrides clamped to these
  }
}
```

`golden_session run --name <name>` resolves identity **in code** — the agent never supplies
`golden_id` or `cwd` (preserves F6/F7/A2).

**Grammar (hybrid).** The user writes natural language; the agent extracts `{name, task,
overrides}`; the **name is always resolved via the registry**. Required: name + task. Optional
overrides (`budget`, `turns`, `tools`, `model`) are **clamped to the session's ceilings**.

```
@hermes run on billing-api: add retries to outbound HTTP calls
@hermes run on billing-api: fix the failing test   budget=1.00
@hermes list
```

**Discovery & hints.** `golden_session list` → the agent replies with names + cwd +
description + required/optional args. Unknown name → structured error → agent replies with the
valid names ("did you mean…"). Missing required arg → agent replies with that session's arg
hints. Users never memorize UUIDs, paths, or argument names.

**Authorization (trigger-boundary guardrail).** Discord is a lower-trust, possibly multi-user
surface, so the adapter enforces (1) an **allowlist of Discord user IDs** permitted to trigger,
and (2) **budget/turn ceilings the user cannot override** (the registry `ceilings`). This
hardens F5/F10 at the entry point — without it, anyone in a channel could spend money.

**UX under the blocking model.** The run is a blocking subprocess (A2 / Decision 1), so the
agent posts an **immediate ack** ("▶ running on `billing-api`…") and a **final reply** with the
`TaskResult`. Live progress is Phase 2 (thread #1).

**IR2 — gateway trigger adapter (on Hermes).** Parse `{name, task, overrides}` from the IM,
resolve via the registry, enforce allowlist + ceiling clamp, invoke `golden_session`, and relay
the result + hints back to the originating channel.

**Phase 2 (deferred).** Continuation/retry from Discord (F4 over chat — needs a task handle
surfaced back and a follow-up grammar) and live progress streaming. **MVP triggers fresh forks
by name only.**

## 3. Deployment — Claude Code inside the Hermes container

### Verified topology (from live host investigation)

`docker-compose.yml` runs `nousresearch/hermes-agent:latest` with the bind mount
`/opt/data → host /home/lee/.hermes` (persists across `docker rm` + recreate).

| Path | Origin | Survives image update? |
|---|---|---|
| `/opt/data/home/.npm-global/bin/claude` (+ its `node_modules`) | **bind mount** | ✅ yes |
| `/opt/data/home/.npmrc` (`prefix=$HOME/.npm-global`) | bind mount | ✅ yes |
| `/opt/data/home/.bashrc` (PATH export) | bind mount | ✅ yes |
| `/opt/data/home/.claude.json` + `/opt/data/home/.claude/` (auth + sessions) | bind mount | ✅ yes |
| `/usr/local/bin/node` (v22), `/usr/local/bin/npm` (v10) | **image layer** | ❌ replaced on update |

**`$HOME` = `/opt/data/home`** — corroborated by two live investigations (npm prefix,
`.bashrc`, and `.claude/` all resolve under it). `TROUBLESHOOTING.md` §Issue 1 (which states
`$HOME = /opt/data`) predates this and is likely stale; confirm with
`docker exec -u hermes hermes-lee bash -lc 'echo $HOME'` and correct whichever doc is wrong.

### Decision D1 — install method: persistent volume (not a derived image)

`claude` is already installed on the bind mount, so it **survives image updates with zero
action**. A derived image (`FROM … + npm install -g`) is therefore **unnecessary** — it
would add a build pipeline for no benefit here (KISS/YAGNI). `docker-compose.yml` alone
cannot install software anyway; it only runs the image.

**Install reality:** a true global `npm install -g` **fails with EACCES** — the container
runs as `hermes` (uid 1001) with no sudo, and the global `node_modules` is root-owned. The
working method (already applied) is a **user-local npm prefix** (`~/.npm-global`, persisted
via a `PATH` export in `~/.bashrc`), which lands on the bind mount and so survives updates.
Currently installed: **claude v2.1.195**.

### Decision D2 — Node-dependency risk + self-heal

`claude` is a Node.js CLI whose runtime (`node`/`npm`) comes from the **image**. Risk: a
future image that **drops or major-bumps Node** could break the persisted `claude`
(`node_modules` ABI / missing runtime). Auth and config still survive.

**Mitigation — preflight self-heal (recommended):** before running a task, the wrapper (or
the thin skill) ensures `claude` is usable and reinstalls if not:

```
ensure_claude():
  if `claude --version` fails and `node` is present:
    npm install -g @anthropic-ai/claude-code   # re-link against the new runtime
```

Preferred over a custom container entrypoint because compose keeps the image's
`["gateway", "run"]` entrypoint — no entrypoint surgery (KISS). Covers both the Node-bump
case and a fresh-host bootstrap.

### Decision D3 — auth (live blocker)

**Current state: `claude auth status` → `loggedIn: false`.** Auth is not yet done and is the
blocker that stops any delegation. Headless automation has no human to complete an interactive
login, so the method matters:

- **API key (`ANTHROPIC_API_KEY`) — recommended.** Long-lived, non-interactive; set once via
  compose `environment:` / an `env_file` / a Docker secret (never commit the key). This is the
  correct choice for a Hermes-driven automated pipeline.
- **OAuth device flow — avoid for automation.** It needs a human to complete the flow in a
  browser, and the device code **expires while unattended** — exactly what blocked the live
  `/claude-code` run. Fine for a developer's manual session; wrong for the MVP.

Once set, auth persists at `/opt/data/home/.claude.json` on the bind mount (survives image
updates), so it is a one-time setup.

### Decision D4 — session persistence & workspace-path stability

Because `$HOME` is on the bind mount, Claude Code sessions persist under
`/opt/data/home/.claude/projects/<encoded-cwd>/` — so **GOLD and its forks survive container
restarts** (a bonus for the GOLD pattern). Consequence: each task's workspace **must be a
stable path inside the container** (e.g. `/opt/data/projects/<project>`), because session
lookup is **cwd-scoped** (doc 02; enforced by F6/F9). The current compose already mounts
`/home/lee/projects → /opt/data/projects`, so this is satisfied.

### Wrapper placement

Install `golden_session.py` / the `golden_session` CLI on the **bind mount** (e.g. under
`/opt/data/home/.local/`), not in the image and not in `/tmp` (doc 02 notes the source
currently lives at volatile `/tmp/golden_session.py`). This keeps the engine persistent
alongside `claude`.

## Deployment runbook — standing up the MVP on Hermes

Everything below lives on the **bind mount** (`/opt/data` → host `/home/lee/.hermes`), so it
survives `docker compose up -d` recreate and image updates. **Always use `docker exec -u
hermes`** — running as root pollutes file ownership (see `TROUBLESHOOTING.md` Issue 2).

**Step 0 — Verify the base (claude + node).**
```bash
docker exec -u hermes hermes-lee bash -lc 'node --version && claude --version && echo $HOME'
```
Expect Node present, `claude` v2.1.x, `$HOME=/opt/data/home`. If `claude` is missing, install
per §3 / Decision D1 (user-local npm prefix).

**Step 1 — Auth (Decision D3).** Add the API key to `docker-compose.yml`, recreate, verify:
```yaml
environment:
  ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"   # from host env / .env — never commit the key
```
```bash
docker compose up -d                          # recreate so the new env takes effect (a restart is NOT enough)
docker exec -u hermes hermes-lee bash -lc 'claude -p "reply OK" --max-turns 1'
```

**Step 2 — Install the wrapper (`golden_session`).** Copy the pure-Python wrapper onto the
bind mount and put its CLI on `PATH` (extend `~/.bashrc` the same way the `claude` install did,
so the agent's shell finds it):
```bash
# from the host (bind mount maps /home/lee/.hermes ↔ /opt/data):
mkdir -p /home/lee/.hermes/home/.local/lib/golden_session
cp golden_session.py /home/lee/.hermes/home/.local/lib/golden_session/
# expose a `golden_session` shim on PATH, then:
docker exec -u hermes hermes-lee bash -lc 'golden_session --help'
```

**Step 3 — Create the registry + prime each GOLD (once).** For every project, pick a stable
workspace under `/opt/data/projects/<name>`, prime GOLD once, and record the alias:
```bash
docker exec -u hermes hermes-lee bash -lc \
  'golden_session prime --name billing-api \
     --cwd /opt/data/projects/billing-api \
     --context-file /opt/data/projects/billing-api/CONTEXT.md'   # prints golden_id, writes registry.json
docker exec -u hermes hermes-lee bash -lc 'golden_session list'  # confirm the alias appears
```
Registry lands at `/opt/data/home/.golden_session/registry.json` (F11). **GOLD is sacred** —
never prime the same name twice (F1/F7).

**Step 4 — Wire the trigger.**
- *Skill/guide (A1, Option B):* place a thin GOLD-aware skill in Hermes' skills directory so
  the agent delegates via `golden_session run --name … --task …` instead of raw `claude -p`.
  *Confirm the skills path on host:* `docker exec -u hermes hermes-lee ls /opt/data/skills`.
- *Discord gateway (IR2):* configure the Discord connection (bot token + channel) via the
  Hermes gateway / `hermes setup`, and set the **allowlist** of permitted Discord user IDs.
  *Exact gateway config commands are Hermes-specific — confirm on host.*

**Step 5 — Verify end-to-end.** Run the [`04-phase1-mvp-prd.md`](./04-phase1-mvp-prd.md) §6
acceptance criteria, finishing with criterion 7: from an **allowlisted** Discord user, `run on
billing-api: <task>` forks a task and replies with the result; `list` returns the sessions; an
unknown name returns hints; a non-allowlisted user is rejected.

**Step 6 — Robustness (optional, Decision D2).** Add the preflight `ensure_claude()` so a
future Node bump self-heals. Remember: env changes need a **recreate** (`docker compose up -d`),
not a restart.

**Persistence recap.** `claude`, the wrapper, `registry.json`, GOLD transcripts
(`/opt/data/home/.claude/projects/…`), and auth (`/opt/data/home/.claude.json`) all live on the
bind mount → they survive recreate and image updates. Only Node comes from the image (the D2
risk).

## 4. Decisions summary

- **A1** — Wire via a thin custom skill that calls the `golden_session` CLI (Option B);
  upstream skill untouched. *(confirm against host `/claude-code`)*
- **A2** — Orchestration logic lives in the **wrapper process (code-in-the-loop)**, which
  enforces F1–F10; `/claude-code` is the trigger/knowledge layer, not the orchestrator.
- **A3** — Discord/gateway is a third **interactive trigger**: the agent parses NL (hybrid
  grammar), a **registry** resolves the human-readable name → `{golden_id, cwd, defaults,
  ceilings}` in code (F11), an **allowlist + ceilings** guard the boundary (IR2), and the MVP
  triggers **fresh forks by name only** (continuation/streaming = Phase 2).
- **D1** — Keep the persistent-volume install of `claude`; no derived image.
- **D2** — Add a preflight `ensure_claude()` self-heal in the wrapper/skill for the
  Node-bump / fresh-host edge case.
- **D3** — Use an **API key** (`ANTHROPIC_API_KEY`), not OAuth device flow; auth is the
  current live blocker (`loggedIn: false`).
- **D4** — Run tasks from stable container workspace paths under `/opt/data/projects/<…>`;
  install the wrapper on the bind mount.

## 5. Open items

- **Auth (blocking):** confirm API key over OAuth (D3) and wire `ANTHROPIC_API_KEY` — no
  delegation runs until `loggedIn` is true.
- Confirm `$HOME` (`docker exec -u hermes hermes-lee bash -lc 'echo $HOME'`) and fix
  `TROUBLESHOOTING.md` if stale.
- Decide whether `ensure_claude()` (D2) is Phase 1 or a fast-follow.
- Author the initial `registry.json` (names → `golden_id`/`cwd`/defaults/ceilings) for the
  first project(s).
- Define where the Discord **allowlist** of permitted user IDs is configured (gateway config
  vs registry vs Hermes settings).
- *Resolved:* `/claude-code` exists and loads the orchestration guide (a document, not a
  tool); `claude` **v2.1.195** is installed via the user-local npm prefix.

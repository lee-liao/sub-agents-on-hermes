# Windows deployment — `golden_session` on a local Hermes install

> **Audience: anyone working on the Windows box that runs Hermes natively.** Every
> other doc in this repo describes the `hermes-lee` **Docker/Linux** deployment
> (`/opt/data`, bind mounts, `docker exec -u hermes`). This is a *second, separate*
> deployment with different paths, different sessions, and one trap the container
> setup structurally cannot hit.
>
> **Verified 2026-07-21, updated 2026-07-22** on this host — Hermes native, Claude
> Code v2.1.217, Python 3.13.7 (Anaconda). Paths below were observed, not assumed.

## TL;DR — where things actually are

| Thing | Path |
|---|---|
| **`registry.json` (F11)** | `C:\Users\liao_\.golden_session\registry.json` |
| GOLD + fork transcripts | `C:\Users\liao_\.claude\projects\<encoded-cwd>\` |
| Claude Code trust/auth config | `C:\Users\liao_\.claude.json` |
| GOLD workspaces (`cwd`) | `C:\Users\liao_\AppData\Local\hermes\projects\<name>\` |
| Per-task run dirs (F12) | `<workspace>\runs\<case-id or ts-uid>\` |
| `HERMES_HOME` | `C:\Users\liao_\AppData\Local\hermes` |
| Hermes skills (deployed) | `C:\Users\liao_\AppData\Local\hermes\skills\` — authored in `skills/`, deployed by `scripts/deploy-skills.ps1` (§8) |
| `claude` CLI | `D:\Users\liao_\AppData\Roaming\npm\claude.cmd` |
| `golden_session` on PATH | `D:\Users\liao_\AppData\Roaming\Python\Python313\Scripts\golden_session.exe` |

**The registry is NOT under `HERMES_HOME`.** That is the single most common
wrong-place-to-look on this box, and §1 explains why.

## 1. The trap: `HERMES_HOME` ≠ OS home on Windows

[`HERMES_HOME_AND_OS_HOME.md`](./HERMES_HOME_AND_OS_HOME.md) explains that the
container has *two* conceptual homes that happen to be the same directory. On
Windows they are **not** the same, so the distinction becomes visible:

| | `HERMES_HOME` | OS home (`expanduser("~")`) |
|---|---|---|
| **Container** | `/opt/data` | `/opt/data` — **identical**, trap invisible |
| **This Windows box** | `C:\Users\liao_\AppData\Local\hermes` | `C:\Users\liao_` — **diverge** |

`golden_session` and Claude Code both resolve their state against the **OS home**:

- `registry.py:24` → `os.path.expanduser("~") + \.golden_session\registry.json`
- `session.py:35` → `os.path.expanduser("~") + \.claude\projects`
- `trust.py:29` → `~\.claude.json`

None of them knows `HERMES_HOME` exists. So on Windows all engine state lands
under `C:\Users\liao_\`, while Hermes' own state (`config.yaml`, `sessions/`,
`auth.json`, and *Hermes' own unrelated `registry.json`*) stays under
`AppData\Local\hermes`.

> **Name collision warning.** There are two unrelated `registry.json` files:
> `AppData\Local\hermes\registry.json` belongs to **Hermes**;
> `C:\Users\liao_\.golden_session\registry.json` is **ours** (F11). Grepping for
> the filename finds both.

`GOLDEN_SESSION_REGISTRY` is now **set** for this user account (2026-07-21) to that
same path, so the location is a decision rather than a default. Hermes must be
restarted to observe it; until then the default resolves to the same file.

## 2. Two entry points (they used to be different code ⚠)

There are two ways to invoke the engine on this box. Until 2026-07-21 they
resolved to **different snapshots of the source**; they now agree:

| Entry point | Resolves to | State |
|---|---|---|
| `golden_session.exe` (**on PATH, wins**) | `D:\MyCode\Ivan\sub-agents-on-hermes\golden_session\` — pip **editable** install | ✅ live repo |
| `golden_session.bat` (`%HERMES_HOME%\.local\bin\`) | `%HERMES_HOME%\.local\lib\golden_session\` — copied snapshot | ✅ re-synced 2026-07-21 |

`%HERMES_HOME%\.local\bin` is **not on PATH**, so the `.bat` is reachable only by
absolute path. It was still a landmine, because it hard-codes `GS_LIB` to a copy
that had gone stale.

Drift the copy had accumulated before the re-sync — kept as a record of how far a
second copy can silently diverge in five weeks:

| Module | Differing lines | Consequence |
|---|---|---|
| `cli.py` | 72 | **No `--case-id` / `--work-item-id` / `--pipeline-id` at all** (0 occurrences) |
| `session.py` | 97 | missing `run_dir_for_id`, `sanitize_case_id`, later hardening |
| `runner.py` | 45 | predates the 2026-07-19 Windows spawn fix (`4e8a964`) |
| `registry.py` | 0 | identical |

So a call routed through the `.bat` shim would fail with an argparse error on
`--case-id 238` — the exact contract the Power BI orchestrator depends on.

**Two consequences of the editable install worth internalising:**

1. **Uncommitted edits in the repo are live — and this is intended** (decided
   2026-07-21). Editing `D:\MyCode\Ivan\sub-agents-on-hermes\golden_session\*.py`
   changes the behaviour of the next Hermes-triggered run immediately. There is no
   deploy step and no staging between "I'm experimenting" and "production." The
   trade is deliberate: fast iteration, and the repo stays the single source of
   truth rather than one copy among several. **Treat the working tree as live** —
   don't leave the engine mid-edit, and prefer a branch for anything exploratory.
2. **The engine's own repo is not a GOLD workspace.** Don't confuse it with the
   workspaces in §4.

**Resolved 2026-07-21 by re-syncing, not deleting.** Deletion was the first
instinct and it was wrong: the `.local\lib` copy is referenced by
`claude-code-gold/references/windows-mcp-prime.md` and by the whole
`software-development/windows-ai-agent-adaptation` skill, which instructs setting
`PYTHONPATH` to it. Removing it would have broken documented procedures. The copy
is now byte-identical to the repo, so both entry points run current code.

Note also that this interpreter's **editable install wins over `PYTHONPATH`** — a
modern editable install registers a meta-path finder that runs before path-based
lookup, so even `PYTHONPATH=%HERMES_HOME%\.local\lib python -m golden_session`
resolves to the repo. The lib copy only matters for a *different* Python without
the editable install; keeping it in sync covers that case.

Two copies of a guardrail engine still violates DRY, and "invariants in code" only
holds if there is one copy of the code. **Fixed 2026-07-22:** those skill
references no longer instruct copying the package or setting `PYTHONPATH` — the
editable install resolves without either. The `.local\lib` copy is now legacy and
can be deleted once you're confident nothing invokes the `.bat` by absolute path.

## 3. Windows-specific engine behaviour

Three things the engine does differently here, all already handled in `runner.py`:

### npm `.cmd` shims cannot be spawned by bare name
`CreateProcess` fails with WinError 193 on `claude` because npm installs it as
`claude.cmd`. `_resolve_cmd` (`runner.py:38`) uses `shutil.which` — which honours
`PATHEXT` — to rewrite `argv[0]` to the absolute `.cmd` path. No-op on POSIX.

### The npm prefix is on a different drive than the user profile
This box has `USERPROFILE=C:\Users\liao_` but `APPDATA=D:\Users\liao_\AppData\Roaming`.
That splits the fallback chain in `runner.py:84–99`:

| # | Candidate | Result here |
|---|---|---|
| 1 | `$npm_config_prefix` | unset |
| 2 | `expanduser("~\AppData\Roaming\npm")` | ❌ `C:\...` — **does not exist** |
| 3 | `dirname($APPDATA) + \Roaming\npm` | ✅ `D:\Users\liao_\AppData\Roaming\npm\claude.cmd` |

The "obvious" candidate #2 fails; the deployment is saved by #3, which happens to
reconstruct the `D:` path from `APPDATA`. This only matters when `claude` is *not*
already on PATH (it currently is). If that fallback ever needs to be reliable, set
`CLAUDE_NPM_PREFIX=D:\Users\liao_\AppData\Roaming\npm` explicitly rather than
depending on the coincidence.

### Multi-line prompts must stream via stdin
The npm `.cmd` shim truncates a multi-line argument at the first newline, so the
task prompt is never placed in argv — `default_runner` passes it as `input=`
(`runner.py:112`). This is why `_build_args` keeps a `prompt` parameter it
deliberately does not use. Relevant to every `--task-template`, which is always
multi-line.

### Workspace path encoding
`encode_cwd` folds `[\\/:._]` to `-`, so the drive colon and the `_` in the
username both collapse. Verified live:

```
C:\Users\liao_\AppData\Local\hermes\projects\ado-ready
  → C--Users-liao--AppData-Local-hermes-projects-ado-ready
```

## 4. Registered sessions (as of 2026-07-21)

Both are primed and actively forked — this is where the real work happens, not
`billing-api` (that one is the container deployment's example).

| Name | Workspace | Defaults | Ceilings |
|---|---|---|---|
| `ado-ready` | `%HERMES_HOME%\projects\ado-ready` | turns 50, budget $5, continues 5, tools `Read Edit Bash ado` | turns 100, budget $20 |
| `fresh-power-bi` | `%HERMES_HOME%\projects\fresh-power-bi` | (same) | turns 100, budget $20 |

`fresh-power-bi` carries the Power BI task templates (`analysis-task.md`,
`plan-task.md`, `implementation-task.md`, `qa-task.md`,
`pbip-from-workitem-task.md`) that the workflow orchestrator drives by name.

Its `runs\` directory shows **both** run-dir forms in use — confirming the
orchestrator contract is live:

```
runs\20260713-200102-8e297f5f    ← default <ts>-<uid> (no id passed)
runs\255                          ← --case-id 255
runs\255-mock-test                ← --case-id 255-mock-test
runs\255-preflight-check
```

### GOLD audit (2026-07-21)

**F2 holds on both sessions.** Each GOLD's line count is stable and its mtime
predates every subsequent fork — no fork has grown its parent:

| Session | GOLD | Forks | Transcript disk |
|---|---|---|---|
| `ado-ready` | 258 lines / 493 KB, last written 07-13 18:35 | 9 | 2.9 MB |
| `fresh-power-bi` | 374 lines / 746 KB, last written 07-13 19:49 | 32 | 21.8 MB |

Forks consistently have *fewer* lines than their GOLD (max 247 vs 258; 362 vs
374), which is expected — a fork copies the conversation, not every transcript
metadata line. The property F2 actually requires is that **GOLD does not grow**,
and it hasn't.

Two things to keep an eye on:

#### Is GOLD too big? Measured, not guessed — **no**

Transcript **file size is a poor proxy** for context cost; the `.jsonl` carries
attachments and metadata that never enter the prompt. Reading the `usage` blocks
out of the fork transcripts gives the real numbers:

| | `ado-ready` | `fresh-power-bi` |
|---|---|---|
| **GOLD's primed context** (tokens at fork start) | ~53 k | ~42 k |
| Cache **creation** per fork, median | 518 k | 884 k |
| Total cache creation, all forks | 2.6 M | 24.4 M |

GOLD contributes roughly **5%** of a typical fork's billed cache writes. The
dominant cost is the *task* — long multi-turn runs re-writing cache as their
context grows, not the primed template.

**So a leaner re-prime is not worth it.** Halving GOLD would cut ~2% off a run
while destroying a working, primed session. If per-fork cost matters, the lever
is task-level: fewer turns (`--turns`), tighter `max_turns` ceilings, and task
templates that don't accumulate context. PRD §5's "keep GOLD lean" is still sound
advice at prime time; it just isn't the binding constraint here.

To re-measure after future runs, read `cache_creation_input_tokens` /
`cache_read_input_tokens` from the `message.usage` blocks in each fork's `.jsonl`.

> ⚠️ **`ado-ready` has an orphaned twin GOLD.** Two transcripts exist whose ids
> differ only in the final character:
>
> ```
> d2f4b6e8-1a3c-4e5f-8b7d-9c0e1f2a3b4c   476 KB, 247 lines — ORPHAN, unreferenced
> d2f4b6e8-1a3c-4e5f-8b7d-9c0e1f2a3b4d   493 KB, 258 lines — the registered GOLD
> ```
>
> These ids are hand-authored (the Windows "interactive fixed-ID prime" method
> lets you pick the UUID), and the registry was then hand-edited to point at one
> of them. **That path bypasses the engine's guards**: `DoublePrimeError` only
> fires for the *same* id, and `Registry.add`'s duplicate-name refusal never runs
> when the JSON is edited by hand. Nothing detects a one-character twin.
>
> **Retired 2026-07-22** to `…3b4c.jsonl.bak-20260722` after verifying nothing
> referenced it. `list_forks` matches `*.jsonl`, so the engine no longer sees it;
> delete the `.bak` once you're satisfied. **Prefer generated UUIDs over
> hand-authored ones** — `prime` mints one for you — to avoid recreating this.

## 5. Verifying the deployment

```powershell
# 1. Engine reachable, and WHICH copy is it?
golden_session list
python -c "import golden_session; print(golden_session.__file__)"   # expect the repo path

# 2. Registry location (the thing everyone looks for in the wrong place)
python -c "import os; print(os.path.expanduser('~/.golden_session/registry.json'))"

# 3. Substrate
claude --version                      # expect 2.1.x
(Get-Command claude).Source           # expect D:\...\npm\claude.cmd

# 4. Transcripts for a session's workspace
python -c "from golden_session.session import GoldenSession as G; print(G.encode_cwd(r'C:\Users\liao_\AppData\Local\hermes\projects\ado-ready'))"

# 5. Are the deployed skills in sync with the repo? (exits 1 on drift)
.\scripts\deploy-skills.ps1 -Check
```

If `golden_session list` returns `No sessions registered`, you are reading a
*different* registry than the one in §1 — check `GOLDEN_SESSION_REGISTRY` and
which `golden_session` is first on PATH, in that order.

## 6. How this differs from the container deployment

| | Container (`hermes-lee`) | This Windows box |
|---|---|---|
| `HERMES_HOME` vs OS home | same (`/opt/data`) | **different** (§1) |
| Registry | `/opt/data/.golden_session/registry.json` | `C:\Users\liao_\.golden_session\registry.json` |
| Engine install | copied to bind mount + bash shim | **pip editable → the git repo** |
| Invocation | `bin/golden_session` (bash) | `golden_session.exe` (console script) |
| Skill deployment | copy onto the bind mount by hand | `scripts/deploy-skills.ps1` (§8) |
| Spawn quirks | none | `.cmd` shim, PATHEXT, stdin prompt (§3) |
| `ANTHROPIC_BASE_URL` stripping | yes — needs `_HERMES_FORCE_` escape hatch | not applicable |
| `terminal.home_mode` | must be `real` | `real` (config.yaml:50) |
| Sessions | `billing-api` | `ado-ready`, `fresh-power-bi` |

If you find `/opt/data/home/...` anywhere, it is the `home_mode: auto` drift, not a real
path — no deployment uses it. Doc 05 carried that error throughout its topology and runbook
sections and was corrected on 2026-07-21; the remaining mentions there are deliberate
historical context, labelled as such.

## 7. Open items

### Open

- [ ] **Restart Hermes** so it observes `GOLDEN_SESSION_REGISTRY` (set 2026-07-21).
      No urgency — the default resolves to the same file.
- [ ] **Delete `…3b4c.jsonl.bak-20260722`** (the retired twin GOLD, §4) once a few
      clean runs have passed.
- [ ] **Delete the legacy `%HERMES_HOME%\.local\{bin,lib}` engine copy** (§2) once
      you're confident nothing invokes `golden_session.bat` by absolute path. It is
      currently in sync, so it is harmless — just redundant.
- [ ] **Consider wiring `deploy-skills.ps1 -Check` into a pre-commit hook.** Nothing
      prevents an agent writing to `%HERMES_HOME%\skills\`; the check is only useful
      if it actually runs.

### Resolved

| Item | Outcome |
|---|---|
| Duplicate engine copy (§2) | **2026-07-21** — re-synced, not deleted: skill references pointed at it. Those references were corrected on 07-22, so it is now legacy. |
| Pin `GOLDEN_SESSION_REGISTRY` | **2026-07-21** — set for the user account to the path the default already resolved to, so there was no cutover. |
| Audit the GOLD transcripts (F2) | **2026-07-21** — F2 holds on both sessions; see §4. |
| Orphaned twin GOLD `…3b4c` | **2026-07-22** — verified unreferenced, retired to `.bak` (§4). |
| Leaner re-prime? | **2026-07-22 — measured, not worth it.** GOLD is ~5% of a fork's billed cache writes (§4). |
| Skill docs pointing at a second engine copy | **2026-07-22** — `windows-mcp-prime.md` and both `windows-ai-agent-adaptation` references now state that the editable install needs no `PYTHONPATH`. |
| `windows-ai-agent-adaptation` had no repo home | **2026-07-22** — vendored into `skills/` here; a personal email was redacted, as this repo is public. |
| No declared sync direction | **2026-07-22** — `scripts/deploy-skills.ps1` + a source-of-truth banner in every `SKILL.md` (§8). |
| Editable-install-as-production? | **2026-07-21 — intended, kept.** Fast iteration and one source of truth, at the cost of a live working tree (§2). |
| Stale `/opt/data/home/...` registry path | **2026-07-21** — corrected in doc 05 and `registry.py`; that path was the `home_mode: auto` drift, never a real location. |

## 8. Deploying skills (the sync direction)

**The repo is the source of truth. The deployment is a copy.** Skills are authored
in `skills/` and pushed to `%HERMES_HOME%\skills\` by one script:

```powershell
.\scripts\deploy-skills.ps1 -Check   # report drift, change nothing (exit 1 if drift)
.\scripts\deploy-skills.ps1          # deploy repo -> deployment
```

Both repos carry the same script:

| Repo | Skill | Deploys to |
|---|---|---|
| `sub-agents-on-hermes` | `claude-code-gold` | `skills\claude-code-gold` |
| `sub-agents-on-hermes` | `windows-ai-agent-adaptation` | `skills\software-development\windows-ai-agent-adaptation` |
| `powerbi-workflow-orchestrator` | `powerbi-workflow` | `skills\powerbi-workflow` |

Three properties worth knowing:

- **One-way by design.** The script never copies deployment → repo. If the
  deployed copy has edits worth keeping, `-Check` reports them as
  `DEPLOYMENT ONLY`; reverse-sync by hand and commit *before* deploying, or they
  are overwritten.
- **It never deletes.** Deployment-only files are reported, never removed — they
  may be content that belongs in the repo and hasn't been rescued yet.
- **Content-hash comparison**, so Git's CRLF normalisation doesn't read as drift.

`-Check` exits 1 on drift, so it works as a pre-commit hook or CI guard.

### Why this exists

Editing the deployed copy is how ~20 KB of `claude-code-gold` content — four
sections, two references, and a probe script — came to exist on exactly one
machine, unversioned. The same thing happened to `powerbi-workflow` (whose live
copy was self-patched by the gateway agent mid-build) and to
`windows-ai-agent-adaptation` (which had no repo at all). Every `SKILL.md` now
opens with a banner saying not to edit the deployed copy; this script is what
makes following that advice easy. See `docs/prd/03-open-threads.md` Thread 10.

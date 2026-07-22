# Windows deployment — `golden_session` on a local Hermes install

> **Audience: anyone working on the Windows box that runs Hermes natively.** Every
> other doc in this repo describes the `hermes-lee` **Docker/Linux** deployment
> (`/opt/data`, bind mounts, `docker exec -u hermes`). This is a *second, separate*
> deployment with different paths, different sessions, and one trap the container
> setup structurally cannot hit.
>
> **Verified 2026-07-21** on `liao_@` this host — Hermes native, Claude Code
> v2.1.217, Python 3.13.7 (Anaconda). Paths below were observed, not assumed.

## TL;DR — where things actually are

| Thing | Path |
|---|---|
| **`registry.json` (F11)** | `C:\Users\liao_\.golden_session\registry.json` |
| GOLD + fork transcripts | `C:\Users\liao_\.claude\projects\<encoded-cwd>\` |
| Claude Code trust/auth config | `C:\Users\liao_\.claude.json` |
| GOLD workspaces (`cwd`) | `C:\Users\liao_\AppData\Local\hermes\projects\<name>\` |
| Per-task run dirs (F12) | `<workspace>\runs\<case-id or ts-uid>\` |
| `HERMES_HOME` | `C:\Users\liao_\AppData\Local\hermes` |
| Hermes skills | `C:\Users\liao_\AppData\Local\hermes\skills\claude-code-gold\` |
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

To make the location deliberate instead of incidental, set `GOLDEN_SESSION_REGISTRY`
(currently **unset** on this box — the path is a default, not a decision).

## 2. Two entry points — and they are not the same code ⚠

There are two ways to invoke the engine on this box, and **they resolve to
different snapshots of the source**:

| Entry point | Resolves to | State |
|---|---|---|
| `golden_session.exe` (**on PATH, wins**) | `D:\MyCode\Ivan\sub-agents-on-hermes\golden_session\` — pip **editable** install | ✅ live repo |
| `golden_session.bat` (`%HERMES_HOME%\.local\bin\`) | `%HERMES_HOME%\.local\lib\golden_session\` — copied snapshot | ⚠️ **stale (2026-07-13)** |

`which -a golden_session` returns only the `.exe`, so the `.bat` appears dormant.
But it is a live landmine: it mirrors the container's `GS_LIB` shim layout and
would be picked up if PATH order changed or something invoked it by full path.

Measured drift of the `.bat` copy against the repo:

| Module | Differing lines | Consequence |
|---|---|---|
| `cli.py` | 72 | **No `--case-id` / `--work-item-id` / `--pipeline-id` at all** (0 occurrences) |
| `session.py` | 97 | missing `run_dir_for_id`, `sanitize_case_id`, later hardening |
| `runner.py` | 45 | predates the 2026-07-19 Windows spawn fix (`4e8a964`) |
| `registry.py` | 0 | identical |

So a call routed through the `.bat` shim would fail with an argparse error on
`--case-id 238` — the exact contract the Power BI orchestrator depends on.

**Two consequences of the editable install worth internalising:**

1. **Uncommitted edits in the repo are live.** Editing
   `D:\MyCode\Ivan\sub-agents-on-hermes\golden_session\*.py` changes the behaviour
   of the next Hermes-triggered run immediately. There is no deploy step, and no
   staging between "I'm experimenting" and "production."
2. **The engine's own repo is not a GOLD workspace.** Don't confuse it with the
   workspaces in §4.

**Recommended cleanup:** delete `%HERMES_HOME%\.local\lib\golden_session\` and
`%HERMES_HOME%\.local\bin\golden_session.bat`, or re-sync them from the repo. Two
divergent copies of a guardrail engine is exactly the failure mode this project
exists to prevent (DRY, and "invariants in code" only holds if there's one copy of
the code).

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

> ⚠️ **`ado-ready`'s GOLD transcript is 504 KB.** PRD §5 warns to keep GOLD lean —
> every fork pays a prompt-cache write proportional to its size (~$0.05 floor,
> more for big GOLDs). Not yet investigated; see §7.

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
| Spawn quirks | none | `.cmd` shim, PATHEXT, stdin prompt (§3) |
| `ANTHROPIC_BASE_URL` stripping | yes — needs `_HERMES_FORCE_` escape hatch | not applicable |
| `terminal.home_mode` | must be `real` | `real` (config.yaml:50) |
| Sessions | `billing-api` | `ado-ready`, `fresh-power-bi` |

Doc 05's `/opt/data/home/.golden_session/registry.json` is **stale** even for the
container — it predates the `home_mode` fix. Neither deployment uses it.

## 7. Open items

- [ ] **Resolve the duplicate engine copy** (§2) — delete or re-sync
      `%HERMES_HOME%\.local\{bin,lib}`. Highest-value cleanup here.
- [ ] **Pin `GOLDEN_SESSION_REGISTRY`** so the path is chosen, not defaulted.
- [ ] **Audit `ado-ready`'s 504 KB GOLD** — confirm it is still line-count-flat
      across forks (F2) and decide whether it needs a leaner re-prime.
- [ ] **Decide whether editable-install-as-production is intended** (§2). It is
      convenient for iteration and dangerous for uncommitted work.
- [ ] Fix the stale `/opt/data/home/...` registry path in
      [`prd/05-integration-and-deployment.md`](./prd/05-integration-and-deployment.md)
      (lines 108, 286) and `registry.py:22`.

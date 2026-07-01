# Workspace template — enforced per-task output isolation

Copy this directory's contents into a GOLD session's workspace (its `cwd`, e.g.
`/opt/data/projects/<name>/`). It installs a PreToolUse hook that confines every
fork's file writes to that task's `GS_RUN_DIR`, so one task's downloaded CSVs and
outputs can never land in — or clobber — another's. It also ships the pieces a
real ADO deployment needed: the secrets split, an attachment-download helper, a
context stub, and a parameterized task prompt.

```
<golden-cwd>/
  .gitignore                       ← ignores the two secret files below
  .mcp.json.example                ← copy → .mcp.json  (MCP server registration, no secrets)
  ado-workitem-task.md             ← parameterized ${WORK_ITEM_ID} task prompt
  .claude/
    settings.json                  ← hook + permissions + enabledMcpjsonServers (git-tracked, NO secrets)
    settings.local.json.example    ← copy → settings.local.json  (env block: PAT etc.)
    confine_writes.py              ← blocks writes outside $GS_RUN_DIR
    ado_download.py                ← authenticated attachment download (reads PAT from env)
    gold-context.md                ← stable context stub for `prime --context-file`
```

## Install (once, per GOLD)

1. Copy this directory's contents into the workspace.
2. Turn the two `.example` files into real ones and fill in your values:
   ```bash
   cp .mcp.json.example .mcp.json
   cp .claude/settings.local.json.example .claude/settings.local.json
   # edit .claude/settings.local.json → real AZURE_DEVOPS_ORG_URL + PAT + project
   ```
   Both real files are gitignored (see `.gitignore`) — the PAT never gets
   committed.
3. Prime the GOLD, pointing `--context-file` at the shipped stub (edit it first
   to describe your project):
   ```bash
   golden_session prime --name <name> --cwd <workspace> \
     --context-file <workspace>/.claude/gold-context.md \
     --description "<one-line>"
   ```
   `prime` requires `--context` or `--context-file` — omitting both is the most
   common first-run error. Hooks are cwd-level, so every fork inherits this one
   (doc 02 gotcha 5).

## Secrets architecture (where the PAT lives)

The PAT (or any secret) goes in **one** place: the `env` block of
`.claude/settings.local.json`. Do **not** put it in `.mcp.json` — an `env` block
there reaches the MCP server subprocess *only*, not the agent's Bash/`python3`,
so authenticated `curl`/downloads 401.

| File | Purpose | Git-tracked? | Contents |
|------|---------|:---:|----------|
| `.claude/settings.json` | shared config | **yes** | hooks, `permissions`, `enabledMcpjsonServers` — **NO secrets** |
| `.claude/settings.local.json` | per-machine secrets | **no** (gitignored) | `env` block only: `AZURE_DEVOPS_PAT`, org URL, project |
| `.mcp.json` | MCP server registration | **no** (gitignored) | server `command` + `args` only — **NO** `env` block |

Claude Code merges `settings.json` + `settings.local.json` and injects the `env`
block into claude's process environment. The MCP server is a child process that
**inherits** that environment, so it gets the PAT without `.mcp.json` needing an
`env` block. The agent's Bash/`python3` reads the same values via `os.environ`.
Single source of truth. (Verified in Claude Code 2.1.197 on Linux — no launch-time
`export` hack needed.)

## Headless task-prompt rules (the #1 cause of failed runs)

`golden_session` launches headless `claude -p`. Two behaviors bite every
first-time task author — encode these in your `--task` prompt (the shipped
`ado-workitem-task.md` already does):

1. **No `$VAR` in Bash commands.** Headless claude blocks *any* Bash command
   containing `$NAME` — even `echo $GS_RUN_DIR` — with "Contains
   simple_expansion", **regardless of** the `Bash(prefix *)` allow-list. It's a
   second security layer beyond prefix matching. Resolve env vars in Python:
   ```bash
   python3 -c "import os; print(os.environ.get('GS_RUN_DIR'))"
   ```
   (In our first WI-237 run the agent burned all 21 turns trying `echo`/`env`/
   `printenv` on `$GS_RUN_DIR` — all blocked — then hallucinated the error
   string as the literal directory path.)
2. **Authenticated network calls go in a helper script.** `curl -u ":$AZURE_DEVOPS_PAT"`
   trips rule 1 *and* puts the secret on a command line. Use the shipped
   `.claude/ado_download.py`, which reads `AZURE_DEVOPS_PAT` from `os.environ`
   internally — the Bash line `python3 .claude/ado_download.py <url> <out>` has
   no `$` and no secret.
3. **`.claude/` is auto-denied** as a "sensitive file" by claude (separate from
   the confine-writes hook). Outputs go under `$GS_RUN_DIR`.

## Per task

Pass `--run-dir` — the engine creates it and exports it as `GS_RUN_DIR`. For an
ADO work item, point `--task-template` at the shipped `ado-workitem-task.md` and
fill its `${WORK_ITEM_ID}` placeholder with `--param`; the engine reads the
template **from this workspace** (relative paths resolve against the GOLD's cwd)
and substitutes in code, so the caller supplies only the file name and the id:
```bash
golden_session run --name ado-ready \
  --run-dir /tmp/gs-runs/$(uuidgen) \
  --task-template ado-workitem-task.md --param WORK_ITEM_ID=181
```
Any `Write`/`Edit` outside `GS_RUN_DIR` is blocked before it happens —
convention becomes guarantee. (`--task "…"` still works for one-off literal
prompts; `--task` and `--task-template` are mutually exclusive.)

## Recommended layout

- **Transient scratch + downloads → `/tmp/gs-runs/<id>`** (off the persistent
  mount; vanishes on container restart, never grows `/opt/data`).
- **Durable results → copy to `<golden-cwd>/out/<id>/`** inside the workspace.

## Caveat: Bash is not confined

The hook guards the path-carrying edit tools (`Write`/`Edit`/`MultiEdit`). A
`Bash` command (`curl -o …`, `cp`, redirects) — and `ado_download.py`'s own
`open(out, "wb")` — can still write anywhere, so a task must direct those at a
path under `$GS_RUN_DIR`. For a hard boundary, **leave `Bash` out of the GOLD's
`allowed_tools`** and let the agent fetch data via an MCP tool / `Write`. See
`docs/OUTPUT_ISOLATION.md` for the full rationale and the Bash-allowed fallback.

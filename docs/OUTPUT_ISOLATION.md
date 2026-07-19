# Per-task output isolation (F12)

> **Problem.** A GOLD session has a single, static workspace (`cwd`) shared by
> every fork. Telling a task to "write under `./out/wi-181/`" is only a
> convention — nothing stops a fork from writing elsewhere, so one task's
> downloaded CSVs and outputs can leak into or clobber another's, and stale
> files can affect future forks that read them.
>
> **Solution.** Give each task a *unique, ephemeral* directory and *enforce* the
> boundary outside the LLM, without changing the GOLD's `cwd` (which is its
> session identity). This is the `GS_RUN_DIR` mechanism.

## The principle: separate identity from scratch

The `cwd` must stay fixed — it is how Claude Code scopes session lookup (doc 02
gotcha 2), so it cannot vary per task. But the *data* a task touches has no
reason to live at a fixed shared path. So:

| Concern | Where it lives |
|---|---|
| Session identity (must be stable) | the GOLD's `cwd`, e.g. `/opt/data/projects/ado-pipeline` |
| Per-task scratch + downloads (transient) | `GS_RUN_DIR`, e.g. `/tmp/gs-runs/<id>` |
| Durable results (must persist) | copied into `<cwd>/out/<id>/` |

## Three layers

### 1. Unique by construction
The orchestrator mints a per-task id *before* the call and passes
`--run-dir`. The engine creates the directory and exports its absolute path as
`GS_RUN_DIR` for that subprocess only (`session.py` `_run_env`, F12). Two tasks
can never collide because their ids differ.

```bash
golden_session run --name ado-pipeline \
  --run-dir /tmp/gs-runs/$(uuidgen) \
  --task "Download ADO work item 181's CSV into \$GS_RUN_DIR, write the result to \$GS_RUN_DIR/out/."
```

The path is also echoed back in the result JSON (`"run_dir": ...`) so the
caller knows where to collect output.

### 2. Ephemeral, so it can't affect future forks
A future fork inherits GOLD's *context*, not the filesystem — it is only
affected by a leftover file if it *reads* one. Keep transient inputs off the
persistent mount: `/tmp/gs-runs/<id>` inside the container is volatile (gone on
restart, never grows `/opt/data`). Copy only the final artifact into the
persistent `<cwd>/out/<id>/`.

### 3. Enforced, not just instructed
Install the workspace template's PreToolUse hook (see
[`examples/workspace-template/`](../examples/workspace-template/)). Because
hooks are cwd-level they are inherited by every fork (doc 02 gotcha 5), but the
boundary they enforce — `GS_RUN_DIR` — is per task. Any `Write`/`Edit` outside
the task's directory is blocked *before it happens*. Convention becomes
guarantee, matching the engine's "guardrails in code, not LLM discretion"
philosophy.

## How `GS_RUN_DIR` reaches the hook

`default_runner` spawns `claude` with `env={**os.environ, **{"GS_RUN_DIR": ...}}`
(`runner.py`). A fresh dict is built per call rather than mutating the shared
process environment, so concurrent forks (F8) don't race on it. Claude Code
passes the environment through to the hook subprocess, which reads
`os.environ["GS_RUN_DIR"]`.

## The Bash caveat

The hook guards the path-carrying edit tools (`Write`/`Edit`/`MultiEdit`),
which expose a clean `file_path`. A `Bash` command (`curl -o /elsewhere`, `cp`,
shell redirects) can write anywhere, and parsing arbitrary shell for paths is
fragile. Two clean options:

- **Preferred (hard boundary):** keep `Bash` out of the GOLD's `allowed_tools`
  and have the task fetch data via an MCP tool or `Write`. The
  `Write|Edit` hook is then airtight.
- **If `Bash` is required:** accept best-effort enforcement, or extend the hook
  with a `Bash` matcher that rejects commands referencing absolute paths
  outside `$GS_RUN_DIR`.

## Secrets reach the hook (and Bash) via `settings.local.json`, not `.mcp.json`

A workspace that talks to an authenticated service (e.g. Azure DevOps) needs a
secret (a PAT) reachable by both the MCP server *and* the agent's Bash/`python3`.
The reliable single-source-of-truth is the `env` block of
`.claude/settings.local.json` (git-ignored):

| File | Git-tracked? | Contents |
|---|:---:|---|
| `.claude/settings.json` | yes | hooks, `permissions`, `enabledMcpjsonServers` — no secrets |
| `.claude/settings.local.json` | no | `env` block only — the PAT, org URL, project |
| `.mcp.json` | no | server `command` + `args` only — **no** `env` block |

Claude Code injects that `env` block into its own process environment; the MCP
server (a child process) inherits it, and the agent's Bash/`python3` reads it via
`os.environ`. Putting the secret in `.mcp.json`'s `env` instead reaches the MCP
subprocess *only* — a `curl`/download from Bash then 401s. See
[`examples/workspace-template/`](../examples/workspace-template/) for the shipped
`.example` files and `.gitignore`.

## Headless `claude -p` blocks `$VAR` in Bash — resolve env vars in Python

This is the single biggest cause of failed golden-session runs. The headless CLI
rejects **any** Bash command containing `$NAME` (even `echo $GS_RUN_DIR`) with
"Contains simple_expansion" — a security layer *independent of* the
`Bash(prefix *)` allow-list. So a task must never reference `$GS_RUN_DIR` (or any
secret) in a Bash line. Instead:

- Resolve env vars in Python: `python3 -c "import os; print(os.environ.get('GS_RUN_DIR'))"`.
- Put authenticated network calls in a helper that reads `os.environ` internally
  (the shipped `.claude/ado_download.py`), so the Bash line carries no `$` and no
  secret.

The workspace-template README's "Headless task-prompt rules" and the shipped
`ado-workitem-task.md` encode these; author task prompts from that template.

## Cleanup

`cleanup_forks` deletes *transcripts* only (`session.py`), not data dirs. So the
run-dir is cleaned separately: rely on `/tmp` volatility, or have the
orchestrator `rm -rf "$GS_RUN_DIR"` after it has captured the result and copied
any durable output.

## Without the engine flag

`--run-dir` is sugar: it creates the dir and sets the env var for you. The same
isolation works with zero engine support by exporting `GS_RUN_DIR` in the shell
that calls `golden_session` — the value inherits through `default_runner` into
`claude` and on to the hook. The flag just removes the "operator forgot to
export it" footgun.

## Stable case run dirs (orchestrator contract)

The Power BI workflow orchestrator shares one `RUN_DIR` across every node of a
workflow. Instead of passing `--run-dir` manually, callers pass an id and the
CLI derives the directory deterministically:

```bash
golden_session run --name implementation --task-template implementation-task.md \
  --case-id case-238            # GS_RUN_DIR = <workspace>/runs/case-238
```

- `--case-id`, `--work-item-id`, and `--pipeline-id` all map to
  `<workspace>/runs/<sanitized-id>`; they are mutually exclusive with each
  other and with `--run-dir`.
- Ids are sanitized for filesystem safety (`[A-Za-z0-9._-]` kept, the rest
  collapse to `-`; `.`/`..` are impossible).
- A fresh `run` refuses an id whose directory already exists — pass
  `--continue` to reuse it (multi-stage workflows, orchestrator retries).
  `--continue` in turn refuses a directory that does not exist yet.
- The JSON result always reports the resolved `run_dir`.

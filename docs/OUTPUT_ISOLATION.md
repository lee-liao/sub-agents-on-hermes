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

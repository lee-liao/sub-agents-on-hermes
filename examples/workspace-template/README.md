# Workspace template — enforced per-task output isolation

Copy the `.claude/` directory from here into a GOLD session's workspace (its
`cwd`, e.g. `/opt/data/projects/<name>/`). It installs a PreToolUse hook that
confines every fork's file writes to that task's `GS_RUN_DIR`, so one task's
downloaded CSVs and outputs can never land in — or clobber — another's.

```
<golden-cwd>/
  .claude/
    settings.json        ← registers the PreToolUse hook
    confine_writes.py     ← blocks writes outside $GS_RUN_DIR
```

## How it fits together

1. **Install (once, per GOLD):** copy `.claude/` into the workspace, then prime
   the GOLD as usual. Hooks are cwd-level, so every fork inherits this one
   (doc 02 gotcha 5).
2. **Per task:** pass `--run-dir` — the engine creates it and exports it as
   `GS_RUN_DIR`:
   ```bash
   golden_session run --name ado-pipeline \
     --run-dir /tmp/gs-runs/$(uuidgen) \
     --task "Download ADO work item 181's CSV into \$GS_RUN_DIR, write the result to \$GS_RUN_DIR/out/."
   ```
3. **Enforcement:** any `Write`/`Edit` outside `GS_RUN_DIR` is blocked before it
   happens — convention becomes guarantee.

## Recommended layout

- **Transient scratch + downloads → `/tmp/gs-runs/<id>`** (off the persistent
  mount; vanishes on container restart, never grows `/opt/data`).
- **Durable results → copy to `<golden-cwd>/out/<id>/`** inside the workspace.

## Caveat: Bash is not confined

The hook guards the path-carrying edit tools (`Write`/`Edit`/`MultiEdit`). A
`Bash` command (`curl -o …`, `cp`, redirects) can still write anywhere. For a
hard boundary, **leave `Bash` out of the GOLD's `allowed_tools`** and let the
agent fetch data via an MCP tool / `Write`. See `docs/OUTPUT_ISOLATION.md` for
the full rationale and the Bash-allowed fallback.

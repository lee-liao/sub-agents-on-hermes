#!/usr/bin/env python3
"""PreToolUse hook: confine file writes to this task's GS_RUN_DIR (F12).

Claude Code invokes this before every Write/Edit/MultiEdit (see
`.claude/settings.json`). It reads the per-task directory from the `GS_RUN_DIR`
environment variable — set by `golden_session run/continue --run-dir` — and
blocks any write whose target falls outside it.

Because the hook lives at the workspace cwd it is inherited by *every* fork of
the GOLD session, but the boundary it enforces (`GS_RUN_DIR`) is per-task, so
each fork can only write inside its own directory. That turns output isolation
from a prompt convention into an enforced guarantee.

Contract: exit code 0 allows the tool call; exit code 2 blocks it and feeds the
stderr message back to the model. (Stable across Claude Code 2.x hook versions.)

Scope: this guards the path-carrying edit tools only. If a GOLD grants `Bash`,
a shell command can still write anywhere — keep `Bash` out of `allowed_tools`
for tasks that need a hard boundary (see docs/OUTPUT_ISOLATION.md).
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Can't parse the event — fail closed so a malformed call can't slip past.
        print("confine_writes: could not parse hook event; blocking", file=sys.stderr)
        return 2

    run_dir = os.environ.get("GS_RUN_DIR", "").strip()
    if not run_dir:
        print(
            "confine_writes: GS_RUN_DIR is not set; refusing the write. Launch the "
            "task with `golden_session run --run-dir <dir>` so writes are scoped.",
            file=sys.stderr,
        )
        return 2

    root = os.path.realpath(run_dir)
    target = event.get("tool_input", {}).get("file_path", "")
    if not target:
        # No file_path on the tool input — nothing to confine, let it through.
        return 0

    resolved = os.path.realpath(target)
    if resolved == root or resolved.startswith(root + os.sep):
        return 0

    print(
        f"confine_writes: write to {target!r} denied — outside this task's "
        f"GS_RUN_DIR ({root}). Write under that directory instead.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

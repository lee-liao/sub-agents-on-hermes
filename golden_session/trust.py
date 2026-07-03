"""Claude Code per-workspace trust flag (engine improvement #1).

Claude Code 2.1.x silently discards a workspace's **entire** ``permissions.allow``
list when the workspace has not been "trusted": ``Read`` still works (so a headless
fork looks healthy) but every ``Bash`` / ``mcp__*`` call is denied and the run
stalls *green-but-empty*. Trust lives in ``~/.claude.json`` (or ``$CLAUDE_CONFIG_FILE``)
under ``projects[<abs-cwd>].hasTrustDialogAccepted`` — a flag no interactive
dialog can set in a headless/Docker deployment. ``prime`` sets it here so a fresh
workspace works on its first run instead of burning a full budget asking for
approval that never comes.

The write is deliberately conservative: the config file also holds OAuth tokens
and other Claude Code state, so we read-modify-write **only** the one nested key,
publish atomically (temp file + ``os.replace``), and treat any failure as
non-fatal — the GOLD is still registered; the operator can set the flag later
(see the ``trust`` subcommand).
"""

from __future__ import annotations

import json
import os
from typing import Optional


def config_file_path(config_file: Optional[str] = None) -> str:
    """Resolve the Claude Code config path: explicit arg > ``$CLAUDE_CONFIG_FILE``
    > ``~/.claude.json`` (Claude Code honours ``CLAUDE_CONFIG_FILE``)."""
    return os.path.expanduser(
        config_file or os.environ.get("CLAUDE_CONFIG_FILE") or "~/.claude.json"
    )


def set_workspace_trust(cwd: str, *, config_file: Optional[str] = None) -> bool:
    """Mark ``cwd`` trusted in Claude Code's config, non-destructively.

    Reads the existing config (tolerating an absent or corrupt file), mutates
    **only** ``projects[<abs-cwd>].hasTrustDialogAccepted`` — without clobbering an
    existing value — and writes the whole document back atomically so every other
    key (OAuth tokens, history, …) is preserved.

    Returns ``True`` on success, ``False`` on any IO/permission error. The caller
    keeps going either way: trust is best-effort, never fatal to ``prime``. The
    temp file is created by (and thus owned by) the calling process, so as long as
    ``prime`` runs as the runtime user the flag file keeps that ownership — never
    write this as root.
    """
    path = config_file_path(config_file)
    try:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, json.JSONDecodeError):
            cfg = {}  # absent or corrupt — start a minimal doc
        if not isinstance(cfg, dict):
            cfg = {}

        projects = cfg.setdefault("projects", {})
        if not isinstance(projects, dict):
            return False  # unexpected shape — refuse rather than corrupt it
        entry = projects.setdefault(os.path.abspath(cwd), {})
        if not isinstance(entry, dict):
            return False
        # Don't clobber a value already present (idempotent, respects operator intent).
        entry.setdefault("hasTrustDialogAccepted", True)

        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        os.replace(tmp, path)  # atomic publish, preserves creating-process ownership
        return True
    except OSError:
        return False

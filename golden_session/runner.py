"""The subprocess seam (Dependency Inversion).

`GoldenSession` does not call `subprocess` directly; it depends on the abstract
``ClaudeRunner`` callable. Production wires :func:`default_runner` (which spawns
the real `claude` CLI); tests wire a fake runner that returns canned JSON and
records the argv. This keeps every guardrail (F1-F11) testable without auth, a
network, or the `claude` binary installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence

from .errors import ClaudeUnavailableError


@dataclass(frozen=True)
class RunOutput:
    """Raw outcome of one CLI invocation; parsing happens in the session layer."""

    returncode: int
    stdout: str
    stderr: str


# A runner takes the fully-built argv, the workspace cwd, and an optional env
# overlay, returning RunOutput. cwd is passed explicitly (F6) — the runner MUST
# NOT rely on the process cwd. ``env`` is an *overlay* on os.environ (None ==
# plain inherit), used to inject per-task vars like GS_RUN_DIR (F12) without
# mutating the shared process environment — safe for parallel forks.
ClaudeRunner = Callable[..., RunOutput]


def default_runner(
    args: Sequence[str], cwd: str, env: Optional[Mapping[str, str]] = None
) -> RunOutput:
    """Spawn the real `claude` CLI as a blocking subprocess.

    - ``cwd`` is passed explicitly so session lookup is scoped to the right
      workspace (F6 / doc 02 gotcha 2). Never inherit the process cwd.
    - ``env`` overlays os.environ for this call only (``None`` == inherit
      unchanged). Building a fresh dict per call keeps concurrent forks from
      racing on a shared, mutated environment.
    - ``stdin=DEVNULL`` silences the harmless "no stdin data received in 3s"
      warning (doc 02 gotcha 8).
    """
    try:
        proc = subprocess.run(
            list(args),
            cwd=cwd,
            env=({**os.environ, **env} if env else None),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:  # `claude` not on PATH
        raise ClaudeUnavailableError(
            f"`{args[0] if args else 'claude'}` not found on PATH (cwd={cwd})"
        ) from exc
    return RunOutput(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def ensure_claude(claude_bin: str = "claude") -> None:
    """Preflight self-heal for the Node-bump / fresh-host edge case (Decision D2).

    Opt-in: callers invoke this before a run if they want the wrapper to repair a
    `claude` that broke because the container image bumped or dropped Node. It is
    deliberately *not* called automatically — keeping the hot path KISS.
    """
    if _claude_works(claude_bin):
        return
    if shutil.which("node") is None:
        raise ClaudeUnavailableError(
            "`claude` is not runnable and `node` is absent — cannot self-heal (D2)."
        )
    # Re-link the persisted install against the current Node runtime.
    subprocess.run(
        ["npm", "install", "-g", "@anthropic-ai/claude-code"],
        stdin=subprocess.DEVNULL,
        check=True,
    )
    if not _claude_works(claude_bin):
        raise ClaudeUnavailableError("`claude` still not runnable after reinstall (D2).")


def _claude_works(claude_bin: str) -> bool:
    try:
        proc = subprocess.run(
            [claude_bin, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0

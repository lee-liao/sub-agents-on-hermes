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


def _resolve_cmd(executable: str, path: Optional[str] = None) -> str:
    """Map a bare command name to its on-disk file on Windows.

    npm installs CLIs (`claude`, `npm`) as `.cmd` shims, and CreateProcess
    cannot spawn a `.cmd` file given only its bare name — it fails with
    WinError 193 ("%1 is not a valid Win32 application") or FileNotFoundError.
    `shutil.which` honors PATHEXT, so it resolves `claude` -> `claude.cmd`;
    the absolute path *is* spawnable. No-op on POSIX or for absolute paths.
    """
    if os.name != "nt" or os.path.isabs(executable):
        return executable
    return shutil.which(executable, path=path) or executable


def default_runner(
    args: Sequence[str], cwd: str, env: Optional[Mapping[str, str]] = None, *, prompt: Optional[str] = None
) -> RunOutput:
    """Spawn the real `claude` CLI as a blocking subprocess.

    - ``cwd`` is passed explicitly so session lookup is scoped to the right
      workspace (F6 / doc 02 gotcha 2). Never inherit the process cwd.
    - ``env`` overlays os.environ for this call only (``None`` == inherit
      unchanged). Building a fresh dict per call keeps concurrent forks from
      racing on a shared, mutated environment.
    - ``stdin=DEVNULL`` silences the harmless "no stdin data received in 3s"
      warning (doc 02 gotcha 8).
    - On Windows, native Python subprocesses may not inherit the git-bash PATH
      that contains the npm-installed `claude`. We inject the npm prefix dir if
      `CLAUDE_BIN` is not absolute and not found on the inherited PATH, then
      rewrite argv[0] to the resolved `.cmd`/`.exe` path — CreateProcess cannot
      spawn a `.cmd` shim by bare name (WinError 193).
    - ``prompt`` is passed via stdin when supplied. The Windows `.cmd` shim that
      npm installs truncates multi-line arguments at the first newline, so long
      task templates must be streamed instead of passed as the [prompt]
      positional argument.
    """
    claude_bin = args[0] if args else "claude"
    env = dict(env) if env else {}

    # Resolve the claude executable on Windows if needed.
    # Honor any PATH overlay the caller supplied; the final env is the merge of
    # os.environ and env, so the search path should reflect that same effective
    # PATH (overlay wins if present).
    search_path = env.get("PATH") or os.environ.get("PATH", "")
    if not os.path.isabs(claude_bin) and shutil.which(claude_bin, path=search_path) is None:
        npm_prefix = os.environ.get("CLAUDE_NPM_PREFIX")
        if not npm_prefix:
            # Fallback: infer from known npm locations or PATH-like env vars.
            for candidate in (
                os.environ.get("npm_config_prefix"),
                os.path.expanduser("~\\AppData\\Roaming\\npm"),
                os.path.join(os.path.dirname(os.environ.get("APPDATA", "")), "Roaming", "npm"),
            ):
                if candidate and os.path.isdir(candidate):
                    possible = os.path.join(candidate, "claude.cmd")
                    if os.path.exists(possible):
                        npm_prefix = candidate
                        break
                    possible = os.path.join(candidate, "claude.exe")
                    if os.path.exists(possible):
                        npm_prefix = candidate
                        break
        if npm_prefix:
            env["PATH"] = os.pathsep.join([npm_prefix, search_path])

    # Rewrite argv[0] to the actual file (`claude.cmd` on Windows) so
    # subprocess can spawn it; searches the overlay PATH when we injected one.
    args = list(args)
    if args:
        args[0] = _resolve_cmd(args[0], path=env.get("PATH"))

    try:
        proc = subprocess.run(
            args,
            input=prompt if prompt is not None else None,
            cwd=cwd,
            env={**os.environ, **env},
            stdin=None if prompt is not None else subprocess.DEVNULL,
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
        [_resolve_cmd("npm"), "install", "-g", "@anthropic-ai/claude-code"],
        stdin=subprocess.DEVNULL,
        check=True,
    )
    if not _claude_works(claude_bin):
        raise ClaudeUnavailableError("`claude` still not runnable after reinstall (D2).")


def _claude_works(claude_bin: str) -> bool:
    try:
        proc = subprocess.run(
            [_resolve_cmd(claude_bin), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0

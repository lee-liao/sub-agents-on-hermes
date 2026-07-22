"""GoldenSession — the code-in-the-loop engine that enforces F1-F10.

The PRD frames Phase 1 as "guarantee these invariants and expose a clean
contract", not "write features". Every public method here is one line of the
contract; the guardrails are enforced deterministically in code (Decision A2)
rather than suggested to an improvising LLM.

    prime()         once   -> GOLD template (never grows)          F1
    run_task()      fork    -> new session, GOLD stays pristine     F2, F3
    continue_task() resume  -> append (recover) or branch (fork)    F4, F7, F9, F10
"""

from __future__ import annotations

import os
import re
import time
import uuid
from typing import Iterable, Optional

from .errors import (
    BudgetError,
    DoublePrimeError,
    GoldProtectionError,
    RetryCeilingError,
    SessionNotFoundError,
    WorkspaceError,
)
from .locking import session_lock
from .result import TaskResult
from .runner import ClaudeRunner, RunOutput, default_runner

# Default location Claude Code stores transcripts; overridable for tests / non
# default $HOME. $HOME is the OS home, not HERMES_HOME: /opt/data in the container,
# the user profile on Windows (docs/WINDOWS_DEPLOYMENT.md §1).
DEFAULT_PROJECTS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects")


def _resolve_claude_bin(claude_bin: str) -> str:
    """Allow overriding the claude executable via CLAUDE_BIN env var."""
    return os.environ.get("CLAUDE_BIN") or claude_bin


_CASE_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_case_id(case_id: str) -> str:
    """Sanitize a case/work-item/pipeline id for filesystem use.

    Non ``[A-Za-z0-9._-]`` characters collapse to a single ``-``; leading and
    trailing separators are stripped so the result can never be ``.``/``..`` or
    hide as a dotfile. An id with no safe characters at all is refused loudly.
    """
    cleaned = _CASE_ID_UNSAFE.sub("-", case_id.strip()).strip("-.")
    if not cleaned:
        raise WorkspaceError(f"case id {case_id!r} has no filesystem-safe characters")
    return cleaned


class GoldenSession:
    """Wraps one workspace's GOLD session and its forked tasks.

    One instance == one ``(workspace, golden_id)`` pair (doc 02 "one GOLD per
    workspace"). The mandatory caps (``max_turns``, ``max_budget_usd``,
    ``max_continues``) are the wrapper's own ceiling; per-call overrides are
    clamped *down* to them so no caller can exceed the configured bound (F5/F10).
    """

    def __init__(
        self,
        workspace: str,
        golden_id: str,
        *,
        max_turns: int,
        max_budget_usd: float,
        allowed_tools: Optional[Iterable[str]] = None,
        model: Optional[str] = None,
        max_continues: int = 3,
        claude_bin: str = "claude",
        runner: ClaudeRunner = default_runner,
        projects_dir: Optional[str] = None,
        lock_dir: Optional[str] = None,
    ) -> None:
        # F6 — workspace identity is mandatory and absolute; never inherit cwd.
        if not workspace:
            raise WorkspaceError("workspace (cwd) is mandatory; refusing to inherit process cwd")
        self.workspace = os.path.abspath(workspace)
        if not golden_id:
            raise WorkspaceError("golden_id is mandatory")
        self.golden_id = golden_id

        # F5 — per-call caps are mandatory and must be positive.
        if max_turns is None or max_turns <= 0:
            raise BudgetError("max_turns is mandatory and must be > 0 (F5)")
        if max_budget_usd is None or max_budget_usd <= 0:
            raise BudgetError("max_budget_usd is mandatory and must be > 0 (F5)")
        # F10 — retry ceiling must be a non-negative bound.
        if max_continues is None or max_continues < 0:
            raise RetryCeilingError("max_continues must be >= 0 (F10)")

        self.max_turns = int(max_turns)
        self.max_budget_usd = float(max_budget_usd)
        self.max_continues = int(max_continues)
        self.allowed_tools = list(allowed_tools) if allowed_tools else []
        self.model = model
        self.claude_bin = _resolve_claude_bin(claude_bin)
        self._run = runner
        # Resolve transcript root: explicit arg > env (container $HOME differs,
        # doc 05) > ~/.claude/projects.
        self.projects_dir = (
            projects_dir
            or os.environ.get("GOLDEN_SESSION_PROJECTS_DIR")
            or DEFAULT_PROJECTS_DIR
        )
        self.lock_dir = lock_dir or os.path.join(self.project_dir, ".gs-locks")

        # In-process F10 ledger: how many appends a session-chain has taken.
        self._continues: dict[str, int] = {}

    # --- workspace / transcript layout -----------------------------------

    @staticmethod
    def encode_cwd(path: str) -> str:
        """Encode an absolute workspace path the way Claude Code names its dir.

        Doc 02: the directory under ``~/.claude/projects`` is the workspace path
        with path separators, drive colons, ``.`` and ``_`` all replaced by
        ``-`` (the CLI dashes every non-alphanumeric character), so we fold the
        same set for a stable cross-platform match.
        """
        return re.sub(r"[\\/:._]", "-", os.path.abspath(path))

    @property
    def project_dir(self) -> str:
        return os.path.join(self.projects_dir, self.encode_cwd(self.workspace))

    def _transcript_path(self, sid: str) -> str:
        return os.path.join(self.project_dir, f"{sid}.jsonl")

    # --- F1: prime once --------------------------------------------------

    def prime(self, context: str) -> TaskResult:
        """Initialise GOLD with stable project context. Run exactly once (F1).

        Double-prime guard: if the GOLD transcript already exists, refuse — GOLD
        is write-once and re-priming would silently pollute the shared template.

        ``golden_id`` must be a valid, previously unused UUID — the CLI rejects
        ``--session-id`` values that are not UUIDs.
        """
        if os.path.exists(self._transcript_path(self.golden_id)):
            raise DoublePrimeError(
                f"GOLD {self.golden_id} already primed (transcript exists); "
                "priming twice is forbidden (F1)"
            )
        args = self._build_args(context, session_id=self.golden_id)
        # Serialize in case two processes try to prime the same id concurrently.
        with session_lock(self.golden_id, self.lock_dir):
            out = self._run(args, self.workspace, prompt=context)
        result = self._parse(out)
        # F9-style loudness: everything downstream (double-prime guard, forks,
        # cleanup) keys off golden_id, so a CLI that minted its own id would
        # silently orphan GOLD. Refuse instead.
        if result.session_id != self.golden_id:
            raise SessionNotFoundError(
                f"prime returned session id {result.session_id!r} instead of the "
                f"requested GOLD id {self.golden_id!r}; is golden_id a valid "
                "unused UUID?"
            )
        return result

    # --- F2/F3: fork a task ----------------------------------------------

    def run_task(
        self,
        prompt: str,
        *,
        allowed_tools: Optional[Iterable[str]] = None,
        max_turns: Optional[int] = None,
        max_budget_usd: Optional[float] = None,
        model: Optional[str] = None,
        run_dir: Optional[str] = None,
    ) -> TaskResult:
        """Fork a new task from GOLD (F2). GOLD stays pristine; a NEW sid returns.

        No single-writer lock is taken: each fork writes a *distinct* output file
        (F8 — "concurrent forks off GOLD remain safe"), so they run fully parallel.

        ``run_dir`` (F12) scopes the task's filesystem writes: the directory is
        created and exported as ``GS_RUN_DIR`` so a cwd-level PreToolUse hook can
        confine every write to it — turning output isolation from convention into
        an enforced boundary (see docs/OUTPUT_ISOLATION.md).
        """
        args = self._build_args(
            prompt,
            resume=self.golden_id,
            fork=True,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            model=model,
        )
        # #2 — default to <workspace>/runs/<ts>-<uid> so GS_RUN_DIR is always set.
        run_dir = self.resolve_run_dir(run_dir)
        out = self._run(args, self.workspace, env=self._run_env(run_dir), prompt=prompt)
        result = self._parse(out)
        # A fork must yield a fresh id distinct from GOLD; otherwise the resume
        # silently failed to branch.
        if not result.session_id or result.session_id == self.golden_id:
            raise SessionNotFoundError(
                f"fork from GOLD did not produce a new session id "
                f"(got {result.session_id!r}); refusing to treat as success (F9)"
            )
        return result

    # --- F4/F7/F9/F10: continue (recover) or branch ----------------------

    def continue_task(
        self,
        session_id: str,
        prompt: str,
        *,
        fork: bool = False,
        allowed_tools: Optional[Iterable[str]] = None,
        max_turns: Optional[int] = None,
        max_budget_usd: Optional[float] = None,
        model: Optional[str] = None,
        run_dir: Optional[str] = None,
    ) -> TaskResult:
        """Append a fix to an existing task (recover, F4) or branch a new fork.

        Guards:
        * F7 — refuse ``session_id == golden_id`` (GOLD is append-forbidden).
        * F10 — refuse once the chain has used its ``max_continues`` appends.
        * F8 — serialize appends to one sid (single-writer). Branches don't lock.
        * F9 — on append, assert the returned sid equals the requested one.
        """
        if session_id == self.golden_id:
            raise GoldProtectionError(
                "continue_task on the GOLD id is forbidden — GOLD is append-only "
                "protected (F7). Use run_task() to fork instead."
            )

        if not fork:
            used = self._continues.get(session_id, 0)
            if used >= self.max_continues:
                raise RetryCeilingError(
                    f"session {session_id} hit max_continues={self.max_continues} (F10); "
                    "refusing further recover attempts"
                )

        args = self._build_args(
            prompt,
            resume=session_id,
            fork=fork,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            model=model,
        )

        # #2 — default to <workspace>/runs/<ts>-<uid> so GS_RUN_DIR is always set.
        env = self._run_env(self.resolve_run_dir(run_dir))

        if fork:
            # Branch: writes a new distinct file -> no single-writer lock needed.
            out = self._run(args, self.workspace, env=env, prompt=prompt)
            result = self._parse(out)
            if not result.session_id or result.session_id == session_id:
                raise SessionNotFoundError(
                    f"branch from {session_id} did not produce a new session id "
                    f"(got {result.session_id!r}) (F9)"
                )
            # Inherit the parent's append-ledger so a branch can't reset the F10 budget.
            self._continues[result.session_id] = self._continues.get(session_id, 0)
            return result

        # Append (recover): single-writer lock on the sid (F8).
        with session_lock(session_id, self.lock_dir):
            out = self._run(args, self.workspace, env=env, prompt=prompt)
            result = self._parse(out)
            # F9 — loud failure on session-not-found. A wrong-cwd resume silently
            # starts a FRESH session and reports success; assert id-equality.
            if result.session_id != session_id:
                raise SessionNotFoundError(
                    f"resume of {session_id} returned a different session id "
                    f"({result.session_id!r}) — wrong cwd or missing session. "
                    "Refusing to silently continue into a fresh context (F9)."
                )
            self._continues[session_id] = self._continues.get(session_id, 0) + 1
        return result

    # --- housekeeping ----------------------------------------------------

    def list_forks(self) -> list[str]:
        """Session ids with a transcript under this workspace (GOLD + forks)."""
        try:
            names = os.listdir(self.project_dir)
        except FileNotFoundError:
            return []
        return sorted(n[:-6] for n in names if n.endswith(".jsonl"))

    def cleanup_forks(self, keep: Optional[Iterable[str]] = None) -> list[str]:
        """Delete forked transcripts except GOLD and the ``keep`` set (Phase 1 manual janitor).

        GOLD is *always* preserved regardless of ``keep`` — it is sacred.
        Returns the list of deleted session ids.
        """
        keep_set = set(keep or ())
        keep_set.add(self.golden_id)
        deleted: list[str] = []
        for sid in self.list_forks():
            if sid in keep_set:
                continue
            try:
                os.unlink(self._transcript_path(sid))
                deleted.append(sid)
            except FileNotFoundError:
                pass
        return deleted

    # --- internals -------------------------------------------------------

    def _default_run_dir(self) -> str:
        """Per-workspace default output dir when a caller passes no run_dir (#2).

        ``<workspace>/runs/<timestamp>-<uid>``. Colocated *inside the workspace
        tree* so the artifact inherits the same ``.mcp.json`` / trust / CLAUDE.md
        perimeter the fork ran under (a central dir or ``/tmp`` orphans it, and
        ``/tmp`` vanishes on container recreation). The ``<uid>`` suffix makes the
        path unique even for two runs launched in the same second, so concurrent
        or rapid runs never collide. Add ``runs/`` to the workspace ``.gitignore``.
        """
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return os.path.join(self.workspace, "runs", f"{stamp}-{uuid.uuid4().hex[:8]}")

    def run_dir_for_id(self, case_id: str) -> str:
        """Stable per-case run dir: ``<workspace>/runs/<sanitized-id>``.

        This is the orchestrator contract (--case-id / --work-item-id /
        --pipeline-id): every node of a workflow that shares the id shares the
        directory, so artifacts flow between stages via the filesystem.
        """
        return os.path.join(self.workspace, "runs", sanitize_case_id(case_id))

    def resolve_run_dir(self, run_dir: Optional[str]) -> str:
        """Resolve the effective task run-dir: the caller's override if given,
        else the per-workspace default. Always returns a concrete absolute path so
        ``GS_RUN_DIR`` is set on **every** run — no silent-empty footgun where a
        confine-writes hook blocks all writes because the var is unset (#2)."""
        return os.path.abspath(run_dir) if run_dir else self._default_run_dir()

    def _run_env(self, run_dir: Optional[str]) -> Optional[dict[str, str]]:
        """Build the per-call env overlay for a task run-dir (F12).

        Creates the directory (so the agent and the confine-writes hook agree it
        exists) and exports its absolute path as ``GS_RUN_DIR``. Callers route
        ``run_dir`` through :meth:`resolve_run_dir` first, so in normal use this
        always receives a concrete path; the ``None`` guard is kept for defensive
        direct calls.
        """
        if not run_dir:
            return None
        abs_dir = os.path.abspath(run_dir)
        os.makedirs(abs_dir, exist_ok=True)
        return {"GS_RUN_DIR": abs_dir}

    def _build_args(
        self,
        prompt: str,
        *,
        session_id: Optional[str] = None,
        resume: Optional[str] = None,
        fork: bool = False,
        allowed_tools: Optional[Iterable[str]] = None,
        max_turns: Optional[int] = None,
        max_budget_usd: Optional[float] = None,
        model: Optional[str] = None,
    ) -> list[str]:
        """Build the `claude -p` argv. Per-call caps are clamped to constructor caps.

        The prompt is deliberately NOT placed in argv: the runner streams it via
        stdin, because the Windows `.cmd` shim truncates multi-line arguments at
        the first newline (see runner.py). The ``prompt`` parameter stays in the
        signature so call sites read naturally.
        """
        args = [self.claude_bin, "-p", "--output-format", "json"]
        if session_id:
            args += ["--session-id", session_id]
        if resume:
            args += ["--resume", resume]
        if fork:
            args.append("--fork-session")

        tools = list(allowed_tools) if allowed_tools is not None else self.allowed_tools
        if tools:
            args += ["--allowedTools", *tools]

        # F5 — never exceed the configured ceiling, even if a caller asks for more.
        turns = self.max_turns if max_turns is None else min(int(max_turns), self.max_turns)
        budget = (
            self.max_budget_usd
            if max_budget_usd is None
            else min(float(max_budget_usd), self.max_budget_usd)
        )
        args += ["--max-turns", str(turns)]
        args += ["--max-budget-usd", str(budget)]

        chosen_model = model or self.model
        if chosen_model:
            args += ["--model", chosen_model]
        return args

    def _parse(self, out: RunOutput) -> TaskResult:
        """Parse the CLI's JSON result object into a TaskResult (F3)."""
        import json

        text = (out.stdout or "").strip()
        if not text:
            raise SessionNotFoundError(
                f"claude returned no JSON (exit={out.returncode}); stderr: "
                f"{(out.stderr or '').strip()[:500]}"
            )
        try:
            # Claude (especially on --resume/--continue) may append usage metadata after
            # the result object. raw_decode parses the first complete JSON object and
            # ignores the trailing noise.
            payload, _ = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError:
            # Some CLI versions emit log lines before the JSON; fall back to the last line.
            try:
                last = text.splitlines()[-1]
                payload = json.loads(last)
            except json.JSONDecodeError as exc:
                raise SessionNotFoundError(
                    f"claude stdout is not valid JSON (exit={out.returncode}); "
                    f"first 500 chars: {text[:500]}"
                ) from exc
        return TaskResult.from_cli_json(payload)

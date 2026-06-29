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
# default $HOME (doc 05: container $HOME is /opt/data/home).
DEFAULT_PROJECTS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects")


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
        self.claude_bin = claude_bin
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
        with ``/`` replaced by ``-`` (e.g. ``/tmp/ws-test`` -> ``-tmp-ws-test``).
        We also fold the Windows drive ``:`` and back-slashes so the encoding is
        stable on a dev box; on the Linux container only ``/`` is relevant.
        """
        return re.sub(r"[\\/:.]", "-", os.path.abspath(path))

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
        """
        if os.path.exists(self._transcript_path(self.golden_id)):
            raise DoublePrimeError(
                f"GOLD {self.golden_id} already primed (transcript exists); "
                "priming twice is forbidden (F1)"
            )
        args = self._build_args(context, session_id=self.golden_id)
        # Serialize in case two processes try to prime the same id concurrently.
        with session_lock(self.golden_id, self.lock_dir):
            out = self._run(args, self.workspace)
        return self._parse(out)

    # --- F2/F3: fork a task ----------------------------------------------

    def run_task(
        self,
        prompt: str,
        *,
        allowed_tools: Optional[Iterable[str]] = None,
        max_turns: Optional[int] = None,
        max_budget_usd: Optional[float] = None,
        model: Optional[str] = None,
    ) -> TaskResult:
        """Fork a new task from GOLD (F2). GOLD stays pristine; a NEW sid returns.

        No single-writer lock is taken: each fork writes a *distinct* output file
        (F8 — "concurrent forks off GOLD remain safe"), so they run fully parallel.
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
        out = self._run(args, self.workspace)
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

        if fork:
            # Branch: writes a new distinct file -> no single-writer lock needed.
            out = self._run(args, self.workspace)
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
            out = self._run(args, self.workspace)
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
        """Build the `claude -p` argv. Per-call caps are clamped to constructor caps."""
        args = [self.claude_bin, "-p", prompt, "--output-format", "json"]
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
            payload = json.loads(text)
        except json.JSONDecodeError:
            # Some CLI versions emit log lines before the JSON; take the last line.
            last = text.splitlines()[-1]
            payload = json.loads(last)
        return TaskResult.from_cli_json(payload)

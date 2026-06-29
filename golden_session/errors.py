"""Exception hierarchy for the GoldenSession wrapper.

Every guardrail in the Phase 1 contract (F1-F11) fails *loudly* by raising a
specific, catchable exception rather than returning a silent wrong result. The
hardest property of the MVP is "fail loud, not silent" (PRD §3 F8-F10), so the
exception type is part of the contract — callers (and tests) assert on it.
"""

from __future__ import annotations


class GoldenSessionError(Exception):
    """Base class for all wrapper errors."""


class DoublePrimeError(GoldenSessionError):
    """F1 — a GOLD id was primed a second time. GOLD is write-once."""


class GoldProtectionError(GoldenSessionError):
    """F7 — an append was attempted against the GOLD id. GOLD is append-forbidden."""


class SessionNotFoundError(GoldenSessionError):
    """F9 — a resume returned a session id different from the one requested.

    The scary silent failure this guards against: a wrong-cwd ``--resume``
    silently starts a *fresh, empty* session and reports success, losing all
    prior progress (doc 02 gotcha 2). We assert id-equality and raise instead.
    """


class BudgetError(GoldenSessionError):
    """F5 — a mandatory per-call cap (max_turns / max_budget_usd) is missing or invalid."""


class RetryCeilingError(GoldenSessionError):
    """F10 — the per-task recover-on-failure loop hit its max_continues ceiling."""


class WorkspaceError(GoldenSessionError):
    """F6 — workspace identity is missing or unusable; never fall back to process cwd."""


class LockTimeout(GoldenSessionError):
    """F8 — could not acquire the single-writer lock for a session id in time."""


class RegistryError(GoldenSessionError):
    """F11 — registry lookup / mutation failed (unknown name, duplicate prime, bad file)."""

    def __init__(self, message: str, *, known_names: list[str] | None = None) -> None:
        super().__init__(message)
        # Structured hint so a chat surface can reply "did you mean ...".
        self.known_names = known_names or []


class ClaudeUnavailableError(GoldenSessionError):
    """The `claude` CLI is missing or not runnable (D2 self-heal could not fix it)."""

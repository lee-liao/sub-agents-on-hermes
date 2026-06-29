"""golden_session — code-in-the-loop orchestration of Claude Code GOLD sessions.

Phase 1 MVP engine: prime-once / fork-per-task / resume-to-recover over the
headless `claude -p` CLI, with the F1-F11 guardrails enforced in code. See
docs/prd/04-phase1-mvp-prd.md for the contract this implements.
"""

from __future__ import annotations

from .errors import (
    BudgetError,
    ClaudeUnavailableError,
    DoublePrimeError,
    GoldProtectionError,
    GoldenSessionError,
    LockTimeout,
    RegistryError,
    RetryCeilingError,
    SessionNotFoundError,
    WorkspaceError,
)
from .gateway import GatewayAdapter, ParsedCommand, TriggerReply, parse_message
from .registry import Registry, RegistryEntry, ResolvedRun
from .result import TaskResult
from .runner import RunOutput, default_runner, ensure_claude
from .session import GoldenSession

__version__ = "0.1.0"

__all__ = [
    "GoldenSession",
    "TaskResult",
    "Registry",
    "RegistryEntry",
    "ResolvedRun",
    "GatewayAdapter",
    "ParsedCommand",
    "TriggerReply",
    "parse_message",
    "RunOutput",
    "default_runner",
    "ensure_claude",
    # errors
    "GoldenSessionError",
    "DoublePrimeError",
    "GoldProtectionError",
    "SessionNotFoundError",
    "BudgetError",
    "RetryCeilingError",
    "WorkspaceError",
    "LockTimeout",
    "RegistryError",
    "ClaudeUnavailableError",
]

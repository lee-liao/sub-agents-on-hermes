"""TaskResult — the parseable terminal status of one Claude Code invocation (F3).

`claude -p --output-format json` blocks until the CLI exits and prints a single
JSON result object. This module normalises that object into a stable dataclass
so callers depend on *our* field names, not the CLI's wire format (which differs
slightly across versions: `total_cost_usd` vs `cost_usd`, `stop_reason` vs
`subtype`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TaskResult:
    """Normalised result of a single `claude -p` run.

    F3 mandates at least: ``session_id``, ``is_error``, ``terminal_reason``,
    ``result``, ``cost_usd``. The rest are carried through for observability.
    """

    session_id: str
    is_error: bool
    terminal_reason: str
    result: str
    cost_usd: float
    subtype: str = ""
    num_turns: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_cli_json(cls, payload: dict[str, Any]) -> "TaskResult":
        """Build a TaskResult from the CLI's JSON result object.

        Tolerant of the field-name drift between Claude Code versions: we read
        whichever alias is present and fall back to safe defaults so a missing
        optional field never crashes the parse.
        """
        session_id = payload.get("session_id") or payload.get("sessionId") or ""
        # The CLI signals failure either via an explicit is_error flag or a
        # non-"success" subtype (e.g. "error_max_turns", "error_during_execution").
        subtype = str(payload.get("subtype") or payload.get("stop_reason") or "")
        is_error = bool(payload.get("is_error", subtype not in ("", "success")))
        # terminal_reason is the human-facing "why it stopped" — prefer the most
        # specific signal available.
        terminal_reason = (
            payload.get("stop_reason")
            or payload.get("subtype")
            or ("error" if is_error else "success")
        )
        cost = payload.get("total_cost_usd")
        if cost is None:
            cost = payload.get("cost_usd", 0.0)
        return cls(
            session_id=session_id,
            is_error=is_error,
            terminal_reason=str(terminal_reason),
            result=str(payload.get("result", "")),
            cost_usd=float(cost or 0.0),
            subtype=subtype,
            num_turns=int(payload.get("num_turns", 0) or 0),
            usage=dict(payload.get("usage", {}) or {}),
            raw=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        """Flat dict for JSON output on the CLI / gateway reply."""
        return {
            "session_id": self.session_id,
            "is_error": self.is_error,
            "terminal_reason": self.terminal_reason,
            "result": self.result,
            "cost_usd": self.cost_usd,
            "subtype": self.subtype,
            "num_turns": self.num_turns,
            "usage": self.usage,
        }

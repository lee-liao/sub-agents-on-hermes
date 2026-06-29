"""Gateway trigger adapter (IR2) — chat surface -> golden_session.

This is the reference implementation of the Hermes-side adapter described in
doc 05 (Decision A3). It is deliberately **transport-agnostic**: it takes a
plain ``(user_id, text)`` and returns reply text, so it unit-tests without a
live Discord connection. Wiring it to the actual Discord/Hermes gateway is a
thin shim that feeds incoming messages in and posts ``TriggerReply.text`` back.

The A2 split is preserved: the **agent/parser** does flexible NL extraction;
the **registry + GoldenSession** enforce identity (F6/F7) and ceilings (F5/F10).
Two trigger-boundary guardrails harden the entry point (doc 05 "Authorization"):

* an **allowlist** of permitted user ids, and
* **budget/turn ceilings the user cannot override** (registry ``ceilings``).

MVP scope (doc 05 / PRD §4): triggers **fresh forks by name only**. Continuation
and live streaming are Phase 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .errors import RegistryError
from .registry import Registry
from .runner import ClaudeRunner, default_runner
from .session import GoldenSession

# Trailing `key=value` overrides we recognise; anything else stays part of the task.
_OVERRIDE_KEYS = {"budget", "turns", "tools", "model"}
# rest is optional (.*) so `run on <name>:` with an empty task is still recognised
# as a run command — the handler then replies with that session's arg hints.
_RUN_RE = re.compile(r"^run\s+on\s+(?P<name>[\w.\-]+)\s*:\s*(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
_MENTION_RE = re.compile(r"^\s*@\S+\s*")


@dataclass
class ParsedCommand:
    command: str  # "run" | "list" | "unknown"
    name: Optional[str] = None
    task: Optional[str] = None
    overrides: dict = field(default_factory=dict)


@dataclass
class TriggerReply:
    ok: bool
    text: str


def parse_message(text: str) -> ParsedCommand:
    """Extract {command, name, task, overrides} from a chat message (hybrid grammar).

    Examples:
        run on billing-api: add retries to outbound HTTP calls
        run on billing-api: fix the failing test   budget=1.00
        list
    """
    body = _MENTION_RE.sub("", text or "").strip()
    if body.lower() == "list":
        return ParsedCommand(command="list")

    m = _RUN_RE.match(body)
    if not m:
        return ParsedCommand(command="unknown")

    name = m.group("name")
    rest = m.group("rest").strip()
    task, overrides = _split_trailing_overrides(rest)
    return ParsedCommand(command="run", name=name, task=task or None, overrides=overrides)


def _split_trailing_overrides(rest: str) -> tuple[str, dict]:
    """Peel trailing ``key=value`` tokens (known keys only) off the task text."""
    tokens = rest.split()
    overrides: dict = {}
    while tokens:
        last = tokens[-1]
        if "=" not in last:
            break
        key, _, value = last.partition("=")
        key = key.lower()
        if key not in _OVERRIDE_KEYS or not value:
            break
        overrides[key] = value
        tokens.pop()
    return " ".join(tokens), _normalise_overrides(overrides)


def _normalise_overrides(raw: dict) -> dict:
    """Map chat override names to registry/ResolvedRun field names + types."""
    out: dict = {}
    if "budget" in raw:
        out["max_budget_usd"] = float(raw["budget"])
    if "turns" in raw:
        out["max_turns"] = int(raw["turns"])
    if "model" in raw:
        out["model"] = raw["model"]
    if "tools" in raw:
        out["allowed_tools"] = [t for t in raw["tools"].split(",") if t]
    return out


class GatewayAdapter:
    """Bridges an instant-message trigger to the golden_session engine (IR2)."""

    def __init__(
        self,
        registry: Registry,
        allowlist: set[str],
        *,
        runner: ClaudeRunner = default_runner,
    ) -> None:
        self.registry = registry
        self.allowlist = set(allowlist)
        self.runner = runner

    def acknowledge(self, parsed: ParsedCommand) -> Optional[str]:
        """Immediate ack to post before the blocking run (doc 05 UX)."""
        if parsed.command == "run" and parsed.name:
            return f"▶ running on `{parsed.name}`…"
        return None

    def handle(self, user_id: str, text: str) -> TriggerReply:
        """Full trigger flow: authz -> parse -> resolve/clamp -> invoke -> reply."""
        # Guardrail 1: allowlist (doc 05). Lower-trust, possibly multi-user surface.
        if user_id not in self.allowlist:
            return TriggerReply(False, f"⛔ user {user_id} is not allowed to trigger tasks.")

        parsed = parse_message(text)

        if parsed.command == "list":
            return self._reply_list()
        if parsed.command == "unknown":
            return TriggerReply(
                False,
                "Sorry, I couldn't parse that. Try:\n"
                "  run on <name>: <task>   [budget=… turns=… model=… tools=a,b]\n"
                "  list",
            )

        # command == "run"
        if not parsed.name:
            return self._reply_list("Which session? Pick a name:")
        if not parsed.task:
            return self._reply_missing_task(parsed.name)

        try:
            # Guardrail 2: identity in code + overrides clamped to ceilings (F5/F10/F11).
            resolved = self.registry.resolve(parsed.name, parsed.overrides)
        except RegistryError as exc:
            return self._reply_unknown_name(parsed.name, exc.known_names)

        gs = GoldenSession(
            workspace=resolved.cwd,
            golden_id=resolved.golden_id,
            max_turns=resolved.max_turns,
            max_budget_usd=resolved.max_budget_usd,
            max_continues=resolved.max_continues,
            allowed_tools=resolved.allowed_tools,
            model=resolved.model,
            runner=self.runner,
        )
        result = gs.run_task(parsed.task, model=resolved.model)

        status = "✅" if not result.is_error else "❌"
        clamp_note = (
            f" (effective caps: turns≤{resolved.max_turns}, budget≤${resolved.max_budget_usd:g})"
        )
        return TriggerReply(
            not result.is_error,
            f"{status} `{parsed.name}` → {result.terminal_reason}{clamp_note}\n"
            f"cost: ${result.cost_usd:g} | session: {result.session_id}\n"
            f"{result.result}",
        )

    # --- reply builders --------------------------------------------------

    def _reply_list(self, prefix: str = "Available sessions:") -> TriggerReply:
        entries = self.registry.list_entries()
        if not entries:
            return TriggerReply(True, "No sessions registered yet.")
        lines = [prefix]
        for e in entries:
            desc = f" — {e.description}" if e.description else ""
            lines.append(f"• {e.name}{desc} (cwd: {e.cwd})")
            lines.append(f"    required: {', '.join(e.required_args)} | optional: {', '.join(e.optional_args)}")
        return TriggerReply(True, "\n".join(lines))

    def _reply_unknown_name(self, name: str, known: list[str]) -> TriggerReply:
        hint = f" Did you mean: {', '.join(known)}?" if known else ""
        return TriggerReply(False, f"Unknown session `{name}`.{hint} Try `list`.")

    def _reply_missing_task(self, name: str) -> TriggerReply:
        return TriggerReply(
            False,
            f"`{name}` needs a task. Try: run on {name}: <what to do>   "
            f"[optional: budget=… turns=… model=… tools=a,b]",
        )

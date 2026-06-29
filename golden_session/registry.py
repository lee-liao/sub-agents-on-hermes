"""Name-based GOLD resolution registry (F11).

A manifest on the persistent volume maps a human-readable **name** to
``{golden_id, cwd, description, defaults, ceilings}`` so a caller references a
memorable name instead of a UUID / absolute path. Identity (``golden_id``,
``cwd``) is resolved *in code* and never supplied by the caller — that is what
preserves F6/F7/A2 at the trigger boundary.

User-supplied overrides (budget, turns, tools, model) are **clamped to the
session's ceilings** so a chat user can never spend past the configured cap.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import RegistryError

# Doc 05: registry lands at /opt/data/home/.golden_session/registry.json. Default
# to $HOME/.golden_session/registry.json; override via env for tests / non-default HOME.
DEFAULT_REGISTRY_PATH = os.path.join(
    os.path.expanduser("~"), ".golden_session", "registry.json"
)

# Numeric override keys that MUST be clamped to ceilings; the rest pass through.
_CLAMPED_KEYS = ("max_turns", "max_budget_usd", "max_continues")
_DEFAULT_CONTINUES = 3


@dataclass
class RegistryEntry:
    name: str
    golden_id: str
    cwd: str
    description: str = ""
    defaults: dict[str, Any] = field(default_factory=dict)
    ceilings: dict[str, Any] = field(default_factory=dict)

    @property
    def required_args(self) -> list[str]:
        return ["task"]

    @property
    def optional_args(self) -> list[str]:
        return ["budget", "turns", "tools", "model"]


@dataclass
class ResolvedRun:
    """Everything needed to build a GoldenSession and run one task — identity in code."""

    golden_id: str
    cwd: str
    allowed_tools: list[str]
    max_turns: int
    max_budget_usd: float
    max_continues: int
    model: Optional[str]


class Registry:
    """Load/save the name->session manifest and resolve names to run configs."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.environ.get("GOLDEN_SESSION_REGISTRY") or DEFAULT_REGISTRY_PATH

    # --- persistence -----------------------------------------------------

    def _load_raw(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"could not read registry {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RegistryError(f"registry {self.path} is not a JSON object")
        return data

    def _save_raw(self, data: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)  # atomic publish

    # --- reads -----------------------------------------------------------

    def names(self) -> list[str]:
        return sorted(self._load_raw().keys())

    def get(self, name: str) -> RegistryEntry:
        data = self._load_raw()
        raw = data.get(name)
        if raw is None:
            raise RegistryError(
                f"unknown session name {name!r}", known_names=sorted(data.keys())
            )
        return RegistryEntry(
            name=name,
            golden_id=raw["golden_id"],
            cwd=raw["cwd"],
            description=raw.get("description", ""),
            defaults=dict(raw.get("defaults", {})),
            ceilings=dict(raw.get("ceilings", {})),
        )

    def list_entries(self) -> list[RegistryEntry]:
        return [self.get(n) for n in self.names()]

    # --- writes ----------------------------------------------------------

    def add(
        self,
        name: str,
        *,
        golden_id: str,
        cwd: str,
        description: str = "",
        defaults: Optional[dict[str, Any]] = None,
        ceilings: Optional[dict[str, Any]] = None,
    ) -> RegistryEntry:
        """Register a new name. Refuses to overwrite (one GOLD per name, F1/F7)."""
        data = self._load_raw()
        if name in data:
            raise RegistryError(
                f"session name {name!r} already exists — re-priming a name is forbidden "
                "(F1/F7). Pick a new name or remove it explicitly.",
                known_names=sorted(data.keys()),
            )
        data[name] = {
            "golden_id": golden_id,
            # Stored verbatim: cwd is a stable *container* path (doc 05, e.g.
            # /opt/data/projects/<name>). Never abspath it against the host FS.
            "cwd": cwd,
            "description": description,
            "defaults": defaults or {},
            "ceilings": ceilings or {},
        }
        self._save_raw(data)
        return self.get(name)

    def remove(self, name: str) -> None:
        data = self._load_raw()
        if name not in data:
            raise RegistryError(
                f"unknown session name {name!r}", known_names=sorted(data.keys())
            )
        del data[name]
        self._save_raw(data)

    # --- resolution + clamp (the F11 core) -------------------------------

    def resolve(self, name: str, overrides: Optional[dict[str, Any]] = None) -> ResolvedRun:
        """Resolve a name + caller overrides into a clamped run config.

        defaults <- overrides, then numeric fields clamped DOWN to ceilings.
        Identity (golden_id, cwd) comes from the registry only.
        """
        entry = self.get(name)
        eff = self.clamp(entry, overrides or {})
        return ResolvedRun(
            golden_id=entry.golden_id,
            cwd=entry.cwd,
            allowed_tools=list(eff.get("allowed_tools", []) or []),
            max_turns=int(eff["max_turns"]),
            max_budget_usd=float(eff["max_budget_usd"]),
            max_continues=int(eff.get("max_continues", _DEFAULT_CONTINUES)),
            model=eff.get("model"),
        )

    @staticmethod
    def clamp(entry: RegistryEntry, overrides: dict[str, Any]) -> dict[str, Any]:
        """Merge defaults with overrides, clamping numeric overrides to ceilings."""
        eff: dict[str, Any] = dict(entry.defaults)
        eff.setdefault("max_continues", _DEFAULT_CONTINUES)
        if "max_turns" not in eff or "max_budget_usd" not in eff:
            raise RegistryError(
                f"session {entry.name!r} defaults must set max_turns and max_budget_usd (F5)"
            )

        for key, value in (overrides or {}).items():
            if value is None:
                continue
            eff[key] = value

        # Clamp numeric fields DOWN to the ceiling (never above).
        for key in _CLAMPED_KEYS:
            ceiling = entry.ceilings.get(key)
            if ceiling is not None and key in eff and eff[key] is not None:
                eff[key] = min(type(ceiling)(eff[key]), ceiling)
        return eff

"""Registry + clamp tests (F11)."""

from __future__ import annotations

import pytest

from golden_session import Registry, RegistryError


def _add(reg: Registry, name="billing-api"):
    return reg.add(
        name,
        golden_id="f47ac10b-aaaa",
        cwd="/opt/data/projects/billing-api",
        description="Billing service",
        defaults={
            "allowed_tools": ["Read", "Edit", "Bash"],
            "max_turns": 20,
            "max_budget_usd": 0.50,
            "model": "sonnet",
        },
        ceilings={"max_turns": 40, "max_budget_usd": 2.00},
    )


def test_add_resolve_roundtrip(registry_path):
    reg = Registry(registry_path)
    _add(reg)
    resolved = reg.resolve("billing-api")
    assert resolved.golden_id == "f47ac10b-aaaa"
    assert resolved.cwd == "/opt/data/projects/billing-api"
    assert resolved.max_turns == 20
    assert resolved.max_budget_usd == 0.50
    assert resolved.model == "sonnet"


def test_duplicate_name_refused(registry_path):
    reg = Registry(registry_path)
    _add(reg)
    with pytest.raises(RegistryError):           # one GOLD per name (F1/F7)
        _add(reg)


def test_unknown_name_carries_hints(registry_path):
    reg = Registry(registry_path)
    _add(reg, "alpha")
    _add(reg, "beta")
    with pytest.raises(RegistryError) as ei:
        reg.resolve("gamma")
    assert ei.value.known_names == ["alpha", "beta"]


def test_override_below_ceiling_passes_through(registry_path):
    reg = Registry(registry_path)
    _add(reg)
    resolved = reg.resolve("billing-api", {"max_budget_usd": 1.00, "max_turns": 30})
    assert resolved.max_budget_usd == 1.00
    assert resolved.max_turns == 30


def test_override_above_ceiling_is_clamped(registry_path):
    reg = Registry(registry_path)
    _add(reg)
    resolved = reg.resolve("billing-api", {"max_budget_usd": 99.0, "max_turns": 999})
    assert resolved.max_budget_usd == 2.00       # clamped to ceiling
    assert resolved.max_turns == 40


def test_names_and_list(registry_path):
    reg = Registry(registry_path)
    _add(reg, "alpha")
    _add(reg, "beta")
    assert reg.names() == ["alpha", "beta"]
    assert [e.name for e in reg.list_entries()] == ["alpha", "beta"]

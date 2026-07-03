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


# --- update / set-ceiling (#3) -------------------------------------------


def test_update_raises_ceilings_and_tracks_defaults(registry_path):
    reg = Registry(registry_path)
    _add(reg)  # ceilings 40/2.0, defaults 20/0.5
    entry = reg.update("billing-api", max_turns=45, max_budget_usd=3.0)
    assert entry.ceilings == {"max_turns": 45, "max_budget_usd": 3.0}
    assert entry.defaults["max_turns"] == 45          # defaults track the new ceiling
    assert entry.defaults["max_budget_usd"] == 3.0
    # a later resolve reflects the raised cap
    assert reg.resolve("billing-api").max_budget_usd == 3.0


def test_update_default_is_clamped_to_ceiling(registry_path):
    reg = Registry(registry_path)
    _add(reg)
    # explicit default above the ceiling is clamped down (invariant preserved)
    entry = reg.update("billing-api", max_turns=30, default_turns=999)
    assert entry.ceilings["max_turns"] == 30
    assert entry.defaults["max_turns"] == 30


def test_update_unknown_name_hints(registry_path):
    reg = Registry(registry_path)
    _add(reg, "alpha")
    with pytest.raises(RegistryError) as ei:
        reg.update("ghost", max_turns=10)
    assert ei.value.known_names == ["alpha"]


def test_update_requires_at_least_one_field(registry_path):
    reg = Registry(registry_path)
    _add(reg)
    with pytest.raises(RegistryError):
        reg.update("billing-api")


def test_update_rejects_non_positive(registry_path):
    reg = Registry(registry_path)
    _add(reg)
    with pytest.raises(RegistryError):
        reg.update("billing-api", max_turns=0)
    with pytest.raises(RegistryError):
        reg.update("billing-api", max_budget_usd=-5.0)

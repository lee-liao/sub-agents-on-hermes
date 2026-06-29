"""Gateway trigger adapter tests — PRD §6 acceptance criterion 7 (F11, IR2)."""

from __future__ import annotations

import pytest

from golden_session import GatewayAdapter, Registry, parse_message


@pytest.fixture
def registry(registry_path, workspace):
    reg = Registry(registry_path)
    reg.add(
        "billing-api",
        golden_id="gold-billing",
        cwd=workspace,
        description="Billing service — Python/FastAPI",
        defaults={
            "allowed_tools": ["Read", "Edit", "Bash"],
            "max_turns": 20,
            "max_budget_usd": 0.50,
            "model": "sonnet",
        },
        ceilings={"max_turns": 40, "max_budget_usd": 2.00},
    )
    return reg


@pytest.fixture
def adapter(registry, fake):
    return GatewayAdapter(registry, allowlist={"user-allowed"}, runner=fake)


def _primed_adapter(adapter, registry, fake, workspace):
    """Prime the GOLD so forks have a template to copy."""
    from golden_session import GoldenSession

    gs = GoldenSession(
        workspace=workspace, golden_id="gold-billing", max_turns=20, max_budget_usd=0.5, runner=fake
    )
    gs.prime("ctx")
    return adapter


# --- parsing -------------------------------------------------------------


def test_parse_run_command():
    p = parse_message("@hermes run on billing-api: add retries to outbound HTTP calls")
    assert p.command == "run"
    assert p.name == "billing-api"
    assert p.task == "add retries to outbound HTTP calls"
    assert p.overrides == {}


def test_parse_with_trailing_override():
    p = parse_message("run on billing-api: fix the failing test   budget=1.00")
    assert p.task == "fix the failing test"
    assert p.overrides == {"max_budget_usd": 1.00}


def test_parse_list():
    assert parse_message("@hermes list").command == "list"


def test_parse_keeps_equals_inside_task():
    # `x=1` is not a known override key -> stays part of the task.
    p = parse_message("run on billing-api: set retries x=1 in config")
    assert p.task == "set retries x=1 in config"
    assert p.overrides == {}


# --- criterion 7 flows ---------------------------------------------------


def test_allowlisted_user_forks_and_replies(adapter, registry, fake, workspace):
    _primed_adapter(adapter, registry, fake, workspace)
    reply = adapter.handle("user-allowed", "run on billing-api: add a healthcheck")
    assert reply.ok
    assert "billing-api" in reply.text
    assert "cost" in reply.text


def test_non_allowlisted_user_rejected(adapter):
    reply = adapter.handle("stranger", "run on billing-api: do something")
    assert not reply.ok
    assert "not allowed" in reply.text


def test_unknown_name_returns_hints(adapter):
    reply = adapter.handle("user-allowed", "run on nope: do something")
    assert not reply.ok
    assert "billing-api" in reply.text           # "did you mean…"


def test_list_returns_sessions(adapter):
    reply = adapter.handle("user-allowed", "list")
    assert reply.ok
    assert "billing-api" in reply.text


def test_budget_override_above_ceiling_is_clamped(adapter, registry, fake, workspace):
    _primed_adapter(adapter, registry, fake, workspace)
    reply = adapter.handle("user-allowed", "run on billing-api: big job budget=99")
    # Effective budget reported in the reply is the ceiling (2.00), not 99.
    assert "budget≤$2" in reply.text


def test_missing_task_returns_arg_hints(adapter):
    reply = adapter.handle("user-allowed", "run on billing-api:   ")
    assert not reply.ok
    assert "needs a task" in reply.text

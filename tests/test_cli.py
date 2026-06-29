"""CLI tests — prime/run/continue/list/cleanup through main() with a fake runner."""

from __future__ import annotations

import json

import pytest

from golden_session.cli import main


def run_cli(argv, fake, capsys):
    code = main(argv, runner=fake)
    out = capsys.readouterr()
    return code, out.out, out.err


def prime(fake, capsys, workspace, name="billing-api"):
    code, out, _ = run_cli(
        [
            "prime",
            "--name", name,
            "--cwd", workspace,
            "--context", "project context",
            "--max-turns", "20",
            "--max-budget-usd", "1.0",
            "--ceiling-budget", "2.0",
            "--ceiling-turns", "40",
            "--tools", "Read", "Write", "Bash",
        ],
        fake,
        capsys,
    )
    return code, out


def test_prime_registers_and_returns_golden_id(fake, capsys, workspace, registry_path):
    code, out = prime(fake, capsys, workspace)
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] and payload["name"] == "billing-api"
    assert payload["golden_id"]


def test_prime_twice_is_refused(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, out, err = run_cli(
        ["prime", "--name", "billing-api", "--cwd", workspace, "--context", "x"],
        fake,
        capsys,
    )
    assert code == 2
    assert json.loads(err)["error"] == "RegistryError"


def test_run_forks_a_task(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, out, _ = run_cli(["run", "--name", "billing-api", "--task", "do work"], fake, capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] and payload["is_error"] is False
    assert payload["session_id"] and payload["cost_usd"] > 0


def test_run_unknown_name_emits_hints(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, out, err = run_cli(["run", "--name", "nope", "--task", "x"], fake, capsys)
    assert code == 2
    payload = json.loads(err)
    assert payload["error"] == "RegistryError"
    assert "billing-api" in payload["known_names"]


def test_run_budget_override_is_clamped(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    run_cli(["run", "--name", "billing-api", "--task", "x", "--budget", "99"], fake, capsys)
    last = fake.calls[-1]
    assert last[last.index("--max-budget-usd") + 1] == "2.0"   # clamped to ceiling


def test_continue_then_list_and_cleanup(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    _, out, _ = run_cli(["run", "--name", "billing-api", "--task", "attempt"], fake, capsys)
    sid = json.loads(out)["session_id"]

    code, out, _ = run_cli(
        ["continue", "--name", "billing-api", "--session-id", sid, "--task", "fix it"],
        fake,
        capsys,
    )
    assert code == 0
    assert json.loads(out)["session_id"] == sid      # F4 append, same sid

    code, out, _ = run_cli(["list", "--json"], fake, capsys)
    sessions = json.loads(out)["sessions"]
    assert sessions[0]["name"] == "billing-api"
    assert sessions[0]["required_args"] == ["task"]

    code, out, _ = run_cli(["cleanup", "--name", "billing-api"], fake, capsys)
    assert code == 0

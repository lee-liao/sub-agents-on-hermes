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


def test_run_with_run_dir_creates_dir_and_exports_env(fake, capsys, workspace, registry_path, tmp_path):
    prime(fake, capsys, workspace)
    run_dir = tmp_path / "runs" / "job-0042"
    code, out, _ = run_cli(
        ["run", "--name", "billing-api", "--task", "do work", "--run-dir", str(run_dir)],
        fake,
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["run_dir"] == str(run_dir)
    assert run_dir.is_dir()                                   # F12: dir created
    assert fake.envs[-1]["GS_RUN_DIR"] == str(run_dir)        # F12: exported to subprocess


def test_run_without_run_dir_leaves_env_untouched(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    run_cli(["run", "--name", "billing-api", "--task", "x"], fake, capsys)
    assert fake.envs[-1] is None                              # no overlay when run_dir absent


def _last_task(fake):
    """The task string the runner received (the arg after --task or -p)."""
    call = fake.calls[-1]
    for flag in ("--task", "-p", "--print"):
        if flag in call:
            return call[call.index(flag) + 1]
    # GoldenSession passes the prompt positionally at the end; fall back to it.
    return call[-1]


def test_run_task_template_resolves_against_cwd_and_substitutes(
    fake, capsys, workspace, registry_path, tmp_path
):
    prime(fake, capsys, workspace)
    # Template lives IN the workspace; caller passes only its file name.
    template = tmp_path / "ws" / "ado-workitem-task.md"
    template.write_text("Work item ${WORK_ITEM_ID}: do the thing.", encoding="utf-8")

    code, out, _ = run_cli(
        [
            "run", "--name", "billing-api",
            "--task-template", "ado-workitem-task.md",
            "--param", "WORK_ITEM_ID=238",
        ],
        fake,
        capsys,
    )
    assert code == 0
    assert "Work item 238: do the thing." in _last_task(fake)


def test_run_task_and_template_together_is_refused(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, _, err = run_cli(
        ["run", "--name", "billing-api", "--task", "x", "--task-template", "t.md"],
        fake,
        capsys,
    )
    assert code == 2
    assert json.loads(err)["error"] == "RegistryError"


def test_run_without_task_or_template_is_refused(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, _, err = run_cli(["run", "--name", "billing-api"], fake, capsys)
    assert code == 2
    assert json.loads(err)["error"] == "RegistryError"


def test_run_missing_template_fails_loud(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, _, err = run_cli(
        ["run", "--name", "billing-api", "--task-template", "nope.md"],
        fake,
        capsys,
    )
    assert code == 2
    assert json.loads(err)["error"] == "RegistryError"


def test_run_param_typo_fails_loud(fake, capsys, workspace, registry_path, tmp_path):
    prime(fake, capsys, workspace)
    template = tmp_path / "ws" / "t.md"
    template.write_text("Item ${WORK_ITEM_ID}", encoding="utf-8")
    code, _, err = run_cli(
        [
            "run", "--name", "billing-api",
            "--task-template", "t.md",
            "--param", "WORKITEM_ID=238",   # typo: missing underscore
        ],
        fake,
        capsys,
    )
    assert code == 2
    assert json.loads(err)["error"] == "RegistryError"


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

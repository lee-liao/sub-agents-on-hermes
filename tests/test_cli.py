"""CLI tests — prime/run/continue/list/cleanup through main() with a fake runner."""

from __future__ import annotations

import json
import os

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


def test_run_without_run_dir_defaults_under_workspace(fake, capsys, workspace, registry_path):
    # #2 — omitting --run-dir must NOT leave GS_RUN_DIR unset (the silent-empty
    # footgun); it defaults to <workspace>/runs/<ts>-<uid> and is created+exported.
    prime(fake, capsys, workspace)
    code, out, _ = run_cli(["run", "--name", "billing-api", "--task", "x"], fake, capsys)
    assert code == 0
    run_dir = json.loads(out)["run_dir"]
    expected_root = os.path.join(os.path.abspath(workspace), "runs")
    assert run_dir.startswith(expected_root)                 # per-workspace default
    assert os.path.isdir(run_dir)                            # created
    assert fake.envs[-1]["GS_RUN_DIR"] == run_dir            # exported to the fork


def test_run_default_run_dirs_are_unique(fake, capsys, workspace, registry_path):
    # #2 — two runs (even back-to-back within a second) get distinct dirs.
    prime(fake, capsys, workspace)
    _, out1, _ = run_cli(["run", "--name", "billing-api", "--task", "a"], fake, capsys)
    _, out2, _ = run_cli(["run", "--name", "billing-api", "--task", "b"], fake, capsys)
    assert json.loads(out1)["run_dir"] != json.loads(out2)["run_dir"]


def test_prime_sets_trust_flag(fake, capsys, workspace, registry_path, claude_config):
    # #1 — prime marks the workspace trusted in $CLAUDE_CONFIG_FILE.
    code, out = prime(fake, capsys, workspace)
    assert code == 0
    assert json.loads(out)["trust_set"] is True
    cfg = json.load(open(claude_config, encoding="utf-8"))
    assert cfg["projects"][os.path.abspath(workspace)]["hasTrustDialogAccepted"] is True


def test_prime_no_trust_skips_flag(fake, capsys, workspace, registry_path, claude_config):
    # #1 — --no-trust leaves the config untouched and reports trust_set=null.
    code, out, _ = run_cli(
        ["prime", "--name", "billing-api", "--cwd", workspace, "--context", "x", "--no-trust"],
        fake,
        capsys,
    )
    assert code == 0
    assert json.loads(out)["trust_set"] is None
    assert not os.path.exists(claude_config)                 # nothing written


def test_prime_trust_preserves_existing_config(fake, capsys, workspace, registry_path, claude_config):
    # #1 — the write must not clobber OAuth/other keys already in the file.
    with open(claude_config, "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"token": "secret"}, "projects": {"/other": {"x": 1}}}, fh)
    prime(fake, capsys, workspace)
    cfg = json.load(open(claude_config, encoding="utf-8"))
    assert cfg["oauthAccount"]["token"] == "secret"          # untouched
    assert cfg["projects"]["/other"] == {"x": 1}             # untouched
    assert cfg["projects"][os.path.abspath(workspace)]["hasTrustDialogAccepted"] is True


def test_trust_subcommand_by_name(fake, capsys, workspace, registry_path, claude_config):
    # Prime without trust, then set it via the subcommand resolving cwd from name.
    run_cli(
        ["prime", "--name", "billing-api", "--cwd", workspace, "--context", "x", "--no-trust"],
        fake,
        capsys,
    )
    code, out, _ = run_cli(["trust", "--name", "billing-api"], fake, capsys)
    assert code == 0 and json.loads(out)["trust_set"] is True
    cfg = json.load(open(claude_config, encoding="utf-8"))
    assert cfg["projects"][os.path.abspath(workspace)]["hasTrustDialogAccepted"] is True


def test_trust_subcommand_by_cwd(fake, capsys, workspace, registry_path, claude_config):
    code, out, _ = run_cli(["trust", "--cwd", workspace], fake, capsys)
    assert code == 0 and json.loads(out)["trust_set"] is True
    cfg = json.load(open(claude_config, encoding="utf-8"))
    assert cfg["projects"][os.path.abspath(workspace)]["hasTrustDialogAccepted"] is True


def test_set_ceiling_updates_ceilings_and_defaults(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)  # ceilings 40/2.0, defaults 20/1.0
    code, out, _ = run_cli(
        ["set-ceiling", "--name", "billing-api", "--max-turns", "45", "--max-budget-usd", "3.0"],
        fake,
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["ceilings"] == {"max_turns": 45, "max_budget_usd": 3.0}
    # defaults track the new ceiling when not given separately.
    assert payload["defaults"]["max_turns"] == 45
    assert payload["defaults"]["max_budget_usd"] == 3.0


def test_set_ceiling_unknown_name_hints(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, _, err = run_cli(["set-ceiling", "--name", "nope", "--max-turns", "9"], fake, capsys)
    assert code == 2
    payload = json.loads(err)
    assert payload["error"] == "RegistryError" and "billing-api" in payload["known_names"]


def test_set_ceiling_rejects_negative(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, _, err = run_cli(
        ["set-ceiling", "--name", "billing-api", "--max-budget-usd", "-1"], fake, capsys
    )
    assert code == 2
    assert json.loads(err)["error"] == "RegistryError"


def _last_task(fake):
    """The task string the runner received (streamed via stdin, not argv)."""
    return fake.prompts[-1]


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


# --- orchestrator id args: --case-id / --work-item-id / --pipeline-id ------


def test_run_case_id_creates_stable_run_dir(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, out, _ = run_cli(
        ["run", "--name", "billing-api", "--task", "build", "--case-id", "case-238"],
        fake,
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    expected = os.path.join(workspace, "runs", "case-238")
    assert payload["run_dir"] == expected
    assert payload["is_error"] is False
    assert os.path.isdir(expected)
    assert fake.envs[-1]["GS_RUN_DIR"] == expected


def test_run_work_item_and_pipeline_ids_map_like_case_id(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    _, out, _ = run_cli(
        ["run", "--name", "billing-api", "--task", "x", "--work-item-id", "238"],
        fake,
        capsys,
    )
    assert json.loads(out)["run_dir"] == os.path.join(workspace, "runs", "238")
    _, out, _ = run_cli(
        ["run", "--name", "billing-api", "--task", "x", "--pipeline-id", "pipe-7"],
        fake,
        capsys,
    )
    assert json.loads(out)["run_dir"] == os.path.join(workspace, "runs", "pipe-7")


def test_run_case_id_is_sanitized(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, out, _ = run_cli(
        ["run", "--name", "billing-api", "--task", "x", "--case-id", "case 238/../x"],
        fake,
        capsys,
    )
    assert code == 0
    run_dir = json.loads(out)["run_dir"]
    assert run_dir.startswith(os.path.join(workspace, "runs") + os.sep)
    assert "/" not in os.path.basename(run_dir) and " " not in os.path.basename(run_dir)


def test_run_existing_case_dir_requires_continue(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    run_cli(["run", "--name", "billing-api", "--task", "x", "--case-id", "case-238"], fake, capsys)
    code, _, err = run_cli(
        ["run", "--name", "billing-api", "--task", "x", "--case-id", "case-238"],
        fake,
        capsys,
    )
    assert code == 2
    assert "--continue" in json.loads(err)["message"]


def test_run_continue_reuses_existing_case_dir(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    _, out1, _ = run_cli(
        ["run", "--name", "billing-api", "--task", "x", "--case-id", "case-238"], fake, capsys
    )
    code, out2, _ = run_cli(
        ["run", "--name", "billing-api", "--task", "retry", "--case-id", "case-238", "--continue"],
        fake,
        capsys,
    )
    assert code == 0
    assert json.loads(out2)["run_dir"] == json.loads(out1)["run_dir"]


def test_run_continue_without_id_is_rejected(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, _, err = run_cli(
        ["run", "--name", "billing-api", "--task", "x", "--continue"], fake, capsys
    )
    assert code == 2
    assert "--continue" in json.loads(err)["message"]


def test_run_continue_missing_dir_is_rejected(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, _, err = run_cli(
        ["run", "--name", "billing-api", "--task", "x", "--case-id", "ghost", "--continue"],
        fake,
        capsys,
    )
    assert code == 2
    assert "does not exist" in json.loads(err)["message"]


def test_run_rejects_multiple_id_args(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    code, _, err = run_cli(
        ["run", "--name", "billing-api", "--task", "x", "--case-id", "a", "--work-item-id", "b"],
        fake,
        capsys,
    )
    assert code == 2
    assert "at most one" in json.loads(err)["message"]


def test_continue_subcommand_accepts_case_id(fake, capsys, workspace, registry_path):
    prime(fake, capsys, workspace)
    _, out, _ = run_cli(
        ["run", "--name", "billing-api", "--task", "x", "--case-id", "case-238"], fake, capsys
    )
    sid = json.loads(out)["session_id"]
    code, out, _ = run_cli(
        ["continue", "--name", "billing-api", "--session-id", sid, "--task", "fix",
         "--case-id", "case-238"],
        fake,
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["run_dir"] == os.path.join(workspace, "runs", "case-238")
    assert payload["session_id"] == sid

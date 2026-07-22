# Power BI workflow orchestrator on Windows

This reference documents the Windows-specific setup for running the `powerbi-workflow-orchestrator` repo under Hermes, including gateway integration, real ADO, golden-session invocation, and the live/staged skill sync.

## Repo and install

- Engine repo: `D:\MyCode\Ivan\powerbi-workflow-orchestrator`
- Manifest: `powerbi-workflow.yaml` (or `powerbi-workflow.test-mock.yaml` for mocks)
- CLI entry: `python -m pbi_workflow ...`
- The orchestrator must be importable by the **gateway's Python interpreter**. If the gateway runs Python 3.13, install into that environment:

  ```powershell
  cd "D:\MyCode\Ivan\powerbi-workflow-orchestrator"
  python -m pip install -e .
  ```

- After install, the `pbi` executable lands in the Python user `Scripts` directory (e.g. `D:\Users\liao_\AppData\Roaming\Python\Python313\Scripts`). Add that directory to the **Windows user PATH**, then restart the gateway so the new PATH is inherited.

  ```powershell
  hermes gateway restart
  ```

- If `python -m golden_session --help` fails from the gateway, set `PYTHONPATH` to the `sub-agents-on-hermes` repo (or wherever the `golden_session` package lives):

  ```powershell
  [Environment]::SetEnvironmentVariable("PYTHONPATH", "D:\MyCode\Ivan\sub-agents-on-hermes", "User")
  ```

## Gateway environment

The gateway does not auto-load the Hermes `.env` file on Windows. Patch the service launchers:

- `%LOCALAPPDATA%\hermes\gateway-service\Hermes_Gateway.cmd`
- `%LOCALAPPDATA%\hermes\gateway-service\Hermes_Gateway.vbs`

Have each load `C:\Users\<user>\AppData\Local\hermes\.env` into the process environment before starting the gateway. The `hermes-gateway-email-163-workaround.md` reference shows the parser pattern.

## Real ADO credentials

The orchestrator's ADO scripts (`scripts/ado_read_work_item.py`, `scripts/ado_download_attachment.py`) expect `AZURE_DEVOPS_EXT_PAT`. The golden-session workspace stores the PAT in `.claude/settings.local.json` as `AZURE_DEVOPS_PAT` or `ADO_MCP_AUTH_TOKEN`.

The scripts are patched to:

1. Load the workspace's `.claude/settings.local.json` env block into `os.environ`.
2. Fall back `AZURE_DEVOPS_EXT_PAT` → `AZURE_DEVOPS_PAT` → `ADO_MCP_AUTH_TOKEN`.
3. Derive the org URL from `.mcp.json` (e.g. `@azure-devops/mcp cubeforest3003 ...`).

Default behavior: if any ADO credential is present, the scripts use **real ADO**; set `PBI_USE_MOCK_ADO=true` to force fixtures/synthetic data. This is the MVP default because there is no backward-compatibility requirement.

## Golden-session workspace quirks

- The workspace is `C:\Users\liao_\AppData\Local\hermes\projects\fresh-power-bi`.
- Task templates (`analysis-task.md`, `plan-task.md`, `implementation-task.md`, `qa-task.md`) live in that workspace, not in the repo. The orchestrator's golden-session capabilities reference them by name.
- The implementation task template should **not** self-validate the PBIP; the orchestrator has a dedicated `validate` node. Remove or disable any internal validation step.
- The QA task template must read the validation report from `validate/validation.json`, not `qa/validation.json`.

## Real validator

The real PBIP validator is copied from `fresh-power-bi-from-ado-workitem/reference/validate_pbip.py` into `D:\MyCode\Ivan\powerbi-workflow-orchestrator/reference/validate_pbip.py`. The manifest calls it with `--pbip` and `--out`:

```yaml
command: >-
  python "${MANIFEST_DIR}/reference/validate_pbip.py"
  --pbip "${inputs.pbip_path}"
  --out "${RUN_DIR}/validate/validation.json"
```

The validator writes JSON with `ok`, `valid`, and `problems`.

## Staged vs live skill

The repo has a staged copy at `D:\MyCode\Ivan\powerbi-workflow-orchestrator/skills/powerbi-workflow/`. The live Hermes skill is at `%LOCALAPPDATA%\hermes\skills\powerbi-workflow/`. After editing the staged copy, sync it to the live directory before testing via Discord/email:

```powershell
Copy-Item -Path "D:\MyCode\Ivan\powerbi-workflow-orchestrator\skills\powerbi-workflow" `
          -Destination "$env:LOCALAPPDATA\hermes\skills\powerbi-workflow" -Recurse -Force
```

The live skill can be patched by the bot at runtime; sync back to the repo copy afterward.

## Email-triggered workflows

Email can be used as a workflow trigger. The email body should contain the command at the very top, with no quoted reply text above it. For example:

```
pbi build 255
```

The gateway agent loads the `powerbi-workflow` skill and runs the command. Requirements:

- Default model must be tool-capable (`kimi-for-coding` / `kimi-coding` in this session).
- `EMAIL_TRUST_FROM_HEADER=true` is needed for 163.com/Hotmail senders because the Hermes adapter's `Authentication-Results` parser expects `smtp.mailfrom`/`header.d` while 163.com stamps `smtp.mail`/`header.i`.
- See `hermes-gateway-email-163-workaround.md` for the full email setup.

## Human-in-the-loop approvals

The `build` template now pauses for human approval after the `plan` node before it enters the expensive `implement_pbip` golden session. The chain is:

```
read_wi → download → analyze → plan → approval → gate → implement → validate → qa
```

`human_approval` pauses the workflow and sends the configured async channel (email or Discord) a message asking the user to reply. `approval_gate` checks that the response is exactly `approve`; any other response fails the node. To resume after a pause:

```powershell
pbi continue <case-id> --response "approve"
```

If you want to run the old fully-autonomous build, set the `approval` node to skip the gate by using an env-only override, or add a separate `build_auto` template without the checkpoint.

The `ship` and `test_approval` templates also use:

```
human_approval → approval_gate
```

Discord replies to webhook messages do not reach the bot; reply in a DM or as a new channel message.

## Background long-running workflows

Golden-session phases can run for 10+ minutes. Do not run them inside a tool with a short timeout. Instead:

```powershell
terminal(background=true, notify_on_complete=true)
  pbi build 255
```

Then poll status with `pbi status 255` or read the run directory's `workflow_state.json`.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `pbi: command not found` from gateway | PATH change not inherited | Add `Scripts` dir to user PATH, restart gateway |
| `ModuleNotFoundError: No module named 'rpds.rpds'` | Python ABI mismatch (CPython 3.11 wheel on 3.13 runtime) | Reinstall `rpds-py` for the correct Python version |
| `WinError 193` spawning `claude` | `golden_session` tried to run `claude` as an executable instead of resolving `claude.cmd` | Patch `golden_session/runner.py` to use `shutil.which` and the absolute `.cmd` path |
| `az boards` unauthorized | `AZURE_DEVOPS_EXT_PAT` missing or wrong default org | Ensure PAT is loaded; org is derived from `.mcp.json` |
| Real ADO returns mock CSV | Fixture overrides real download | Use `PBI_USE_MOCK_ADO=true` explicitly for mocks; default is real when credentials exist |
| Email reply loop | Default model cannot run tools | Set `model.default: kimi-for-coding` / `model.provider: kimi-coding` |
| Workflow state stuck `running` | Engine killed, lock left stale | Check `.lock` PID; if dead, run `pbi continue <case-id>` to re-acquire and resume |

# End-to-end Power BI workflow: work item 255 on Windows

This reference is a worked example of running the full Power BI workflow orchestrator on Windows against a real ADO work item, resulting in a PBIP that opens in Power BI Desktop.

## Preconditions

From the environment established in this session:

- Hermes home: `C:\Users\liao_\AppData\Local\hermes`
- Workspace: `C:\Users\liao_\AppData\Local\hermes\projects\fresh-power-bi`
- Engine repo: `D:\MyCode\Ivan\powerbi-workflow-orchestrator`
- Orchestrator installed in Python 3.13; `pbi.exe` on the user PATH.
- Gateway `Hermes_Gateway.cmd` loads `C:\Users\liao_\AppData\Local\hermes\.env` before starting the gateway.
- Default model set to `kimi-for-coding` / `kimi-coding` so the gateway agent can run tools.
- Golden-session registry has a primed `fresh-power-bi` session.
- Workspace `.claude/settings.local.json` env block contains the ADO PAT.

## Run the build

```powershell
cd "D:\MyCode\Ivan\powerbi-workflow-orchestrator"
$env:PYTHONPATH = "D:\MyCode\Ivan\sub-agents-on-hermes"
python -m pbi_workflow template build 255
```

What happens:

1. `read_wi` calls ADO and writes `input/spec.json`.
2. `download` downloads `input/data.csv` (ExpenseLog, 102 rows, 13 columns).
3. `analyze` golden session writes `analysis/report.md`.
4. `plan` golden session writes `plan/plan.md`.
5. `approval` pauses the workflow and sends a notification via email or Discord.
6. `gate` checks that the resumed response is exactly `approve`.
7. `implement` golden session writes the PBIP under `implementation/pbip/`.
8. `validate` runs `reference/validate_pbip.py` and writes `validate/validation.json`.
9. `qa` golden session writes `qa/qa_report.md`.

Resume after the pause:

```powershell
python -m pbi_workflow continue 255 --response "approve"
```

## Expected final state

```json
{
  "status": "completed",
  "completed_nodes": [
    "read_wi", "download", "analyze", "plan", "approval", "gate",
    "implement", "validate", "qa"
  ],
  "failed_node": null,
  "waiting_on": null
}
```

Artifacts of interest:

- `C:\Users\liao_\AppData\Local\hermes\projects\fresh-power-bi\runs\255\implementation\pbip\NorthgateExpenses.pbip`
- `...\implementation\pbip\NorthgateExpenses.Report\report.json`
- `...\implementation\pbip\NorthgateExpenses.SemanticModel\definition\tables\*.tmdl`
- `...\qa\qa_report.md`
- `...\validate\validation.json`

## What was fixed in this session

- Real ADO is now the default when any credential is present; `PBI_USE_MOCK_ADO=true` forces mocks.
- ADO scripts load the workspace `.claude/settings.local.json` env block and fall back `AZURE_DEVOPS_EXT_PAT` → `AZURE_DEVOPS_PAT` → `ADO_MCP_AUTH_TOKEN`.
- The org URL is derived from `.mcp.json` with a fallback to `cubeforest3003`.
- The real PBIP validator (`reference/validate_pbip.py`) is wired into the `validate` node.
- The `build` template now includes a human approval checkpoint after `plan` and before `implement`.
- The gateway `.env` loading and PATH were configured so email/Discord-triggered workflows work.
- The workspace was isolated under `HERMES_HOME\projects` and project-local Claude settings were sanitized.

## Common pitfalls seen

- **Do not run the full build inside a tool with a short timeout.** The `implement` golden session alone can take 5–10 minutes. Use `terminal(background=true, notify_on_complete=true)` or run from a terminal.
- **If the workflow is stuck `running` and the `.lock` PID is dead**, the engine was interrupted. Run `pbi continue <case-id>` to re-acquire the lock and resume from the last completed node.
- **The approval gate is case-sensitive.** The response must be exactly `approve`.
- **The PBIP lives under `implementation/pbip/`, not at the root of the run directory.** The run directory contains workflow orchestration artifacts; the actual Power BI project is nested.

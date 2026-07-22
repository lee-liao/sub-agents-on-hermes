# Golden session project inventory — Windows native install

> **Updated 2026-07-22.** `golden_session` is a pip *editable* install pointing at
> the repo, so `python -m golden_session` resolves it with **no `PYTHONPATH`**.
> The `%HERMES_HOME%\.local\lib` copy below is legacy and went stale once; do not
> treat it as the source. See `docs/WINDOWS_DEPLOYMENT.md` §2 in sub-agents-on-hermes.

> Session-specific detail from installing `ado-ready` and `fresh-power-bi` into a
> native Windows Hermes Agent using the interactive fixed-ID priming method.
> This is a condensed recipe, not a full reproduction of the upstream project.

## Host and tool versions

- Hermes Agent: native Windows, `HERMES_HOME=C:\Users\liao_\AppData\Local\hermes`
- `terminal.home_mode`: `real` (required)
- Claude Code CLI: `2.1.207` installed via npm at `D:\Users\liao_\AppData\Roaming\npm\claude.cmd`
- Python: native Windows Miniconda `D:\ProgramData\miniconda3\python.exe` (3.13.7)
- `golden_session` package installed to `C:\Users\liao_\AppData\Local\hermes\.local\lib\golden_session`
- Windows shim: `C:\Users\liao_\AppData\Local\hermes\.local\bin\golden_session.bat`
- Hermes skill: `C:\Users\liao_\AppData\Local\hermes\skills\claude-code-gold`

## Project layout (isolated copies)

The original directories are left untouched. Hermes works on copies under
`HERMES_HOME\projects`:

```
C:\Users\liao_\AppData\Local\hermes\projects
├── ado-ready
│   ├── .claude
│   ├── .mcp.json
│   ├── README.md
│   ├── CLAUDE.md
│   ├── ado-workitem-task.md
│   └── ...
├── fresh-power-bi
│   ├── .claude
│   ├── .mcp.json
│   ├── README.md
│   ├── CLAUDE.md
│   ├── pbip-from-workitem-task.md
│   └── ...
└── test-gold
    └── CONTEXT.md
```

## Golden session IDs (interactive fixed-ID priming)

| Name | Workspace | Fixed golden ID |
|---|---|---|
| `ado-ready` | `C:\Users\liao_\AppData\Local\hermes\projects\ado-ready` | `d2f4b6e8-1a3c-4e5f-8b7d-9c0e1f2a3b4d` |
| `fresh-power-bi` | `C:\Users\liao_\AppData\Local\hermes\projects\fresh-power-bi` | `e5f7c9a0-2b4d-4e6f-9c8d-0e1f2a3b4c5d` |

## Registry location

`C:\Users\liao_\.golden_session\registry.json`

(Uses the real OS home because `terminal.home_mode: real` is set; Claude Code
stores transcripts under `C:\Users\liao_\.claude\projects\`.)

## Interactive priming steps (for each project)

1. Copy the source directory into `HERMES_HOME\projects\<name>`.
2. Open PowerShell in the copied workspace:

   ```powershell
   cd "C:\Users\liao_\AppData\Local\hermes\projects\<name>"
   claude --session-id <fixed-uuid>
   ```

3. Paste the project context / MCP instructions. Ask Claude to confirm by replying OK.
4. Verify `/status` shows the expected `Session ID` and `cwd`.
5. `/exit`.
6. Create or update the `golden_session` registry entry:

   ```python
   import json, os
   registry_path = os.path.join(os.path.expanduser("~"), ".golden_session", "registry.json")
   registry = json.load(open(registry_path, "r", encoding="utf-8"))
   registry["<name>"] = {
       "golden_id": "<fixed-uuid>",
       "cwd": r"C:\Users\liao_\AppData\Local\hermes\projects\<name>",
       "description": "Interactive primed GOLD session for <name>",
       "defaults": {
           "allowed_tools": ["Read", "Edit", "Bash"],
           "max_turns": 10,
           "max_budget_usd": 0.5,
           "max_continues": 3,
           "model": None
       },
       "ceilings": {
           "max_turns": 20,
           "max_budget_usd": 2.0
       }
   }
   json.dump(registry, open(registry_path, "w", encoding="utf-8"), indent=2)
   ```

7. Test a fork:

   ```powershell
   python -m golden_session run --name <name> --task "List the files in this workspace" --run-dir "C:\Users\liao_\AppData\Local\hermes\projects\<name>\run-001"
   ```

## Windows-specific patches applied to `golden_session`

- `runner.py`: Added `CLAUDE_BIN` env-var override and appended the npm `claude` directory to the subprocess `PATH` so native Windows Python can find `claude.cmd`.
- `session.py`: `encode_cwd()` now matches the native Windows CLI by folding `_` to `-`. `prime()` can derive the real session ID from the transcript file when `--session-id` is ignored.
- `cli.py`: Uses the actual session ID returned by `prime` as the registry `golden_id`.

## Environment variables needed for the Python engine

```powershell
$env:CLAUDE_BIN = "D:\Users\liao_\AppData\Roaming\npm\claude.cmd"
$env:GS_LIB = "C:\Users\liao_\AppData\Local\hermes\.local\lib"
$env:PYTHONPATH = "C:\Users\liao_\AppData\Local\hermes\.local\lib"
```

(Or set them permanently in the Windows environment.)

## Smoke-test commands

```powershell
# List golden sessions
python -m golden_session list

# Run a task against the ADO session
python -m golden_session run --name ado-ready --task "Summarize the ADO workitem task template" --run-dir "C:\Users\liao_\AppData\Local\hermes\projects\ado-ready\run-001"

# Run a task against the Power BI session
python -m golden_session run --name fresh-power-bi --task "Summarize the Power BI task template" --run-dir "C:\Users\liao_\AppData\Local\hermes\projects\fresh-power-bi\run-001"
```

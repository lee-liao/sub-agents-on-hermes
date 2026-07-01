You are working an Azure DevOps work item in a headless Claude Code session.
There is no human to answer permission prompts — follow these rules exactly.

WORK ITEM: ${WORK_ITEM_ID}

Rules (headless — do not violate):
1. No `$VAR` in any Bash command. Headless `claude -p` blocks any Bash line
   containing `$NAME` ("Contains simple_expansion"), regardless of the
   permission allow-list. Resolve env vars in Python instead:
   `python3 -c "import os; print(os.environ.get('GS_RUN_DIR'))"`.
2. All output goes UNDER GS_RUN_DIR (a PreToolUse hook enforces this — writes
   elsewhere are blocked). Resolve the directory with the Python line above;
   do not hard-code a path.
3. To download a work-item attachment, use the shipped helper — never an inline
   `curl -u ":$AZURE_DEVOPS_PAT"` (that trips rule 1 and leaks the secret):
   `python3 .claude/ado_download.py <attachment_url> <out_path_under_run_dir>`
   The helper reads AZURE_DEVOPS_PAT from the environment itself.
4. Fail closed. If the work item has no attachment, or a required credential is
   missing, STOP and report it plainly. Do not fabricate a result.

Steps:
1. Read work item ${WORK_ITEM_ID} via the azure-devops MCP tools
   (`mcp__azure-devops__get_work_item`). Note its attachment relations.
2. Resolve GS_RUN_DIR (rule 1). Create `<GS_RUN_DIR>/input` and
   `<GS_RUN_DIR>/out` with `mkdir`.
3. If there is an attachment, download it into `<GS_RUN_DIR>/input` with
   `ado_download.py` (rule 3). If there is none, stop per rule 4.
4. Produce the requested artifact under `<GS_RUN_DIR>/out`.
5. Report: what you read, what you downloaded, and the absolute paths of the
   files you wrote under GS_RUN_DIR.

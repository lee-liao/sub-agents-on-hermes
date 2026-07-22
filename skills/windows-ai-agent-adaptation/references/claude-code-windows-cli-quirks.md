# Native Windows Claude Code CLI quirks

> **Updated 2026-07-22.** `golden_session` is a pip *editable* install pointing at
> the repo, so `python -m golden_session` resolves it with **no `PYTHONPATH`**.
> The `%HERMES_HOME%\.local\lib` copy below is legacy and went stale once; do not
> treat it as the source. See `docs/WINDOWS_DEPLOYMENT.md` §2 in sub-agents-on-hermes.

Condensed reference from the session that brought `golden_session` up on Windows natively using `claude.cmd` installed via npm (`%APPDATA%\npm\claude.cmd`).

## Environment under test

- Host: Windows 10
- Shell: git-bash / MSYS2 (Hermes `terminal` runs here)
- Python: native Windows CPython 3.11
- Claude Code CLI: `claude.cmd` from npm, located at `D:\Users\liao_\AppData\Roaming\npm\claude.cmd`
- `golden_session` installed under `C:\Users\liao_\AppData\Local\hermes\.local\lib`
- Test workspace: `C:\Users\liao_\AppData\Local\hermes\projects\test-gold`

## Key findings

### 1. `--session-id` is ignored during `prime`

**What the wrapper expected:** pass a pre-generated UUID to `--session-id` so the GOLD transcript is written under a known, memorable ID.

**What Windows `claude` actually does:** it mints its own UUID and writes the transcript under that name. The CLI returns JSON, but the `session_id` field in the JSON may or may not match the requested one — do not rely on it. The transcript file on disk is the ground truth.

**Fix used in `golden_session/session.py`:**

```python
def prime(self, context: str) -> TaskResult:
    # ... guard omitted ...
    args = self._build_args(context, session_id=None)  # do not pass --session-id
    out = self._run(args, self.workspace)
    result = self._parse(out)
    real_id = self._latest_transcript_id()              # derive from disk
    return dataclass_replace(result, session_id=real_id)

def _latest_transcript_id(self) -> Optional[str]:
    best = None
    best_time = 0.0
    for name in os.listdir(self.project_dir):
        if name.endswith(".jsonl"):
            mtime = os.path.getmtime(os.path.join(self.project_dir, name))
            if mtime > best_time:
                best_time = mtime
                best = name[:-len(".jsonl")]
    return best
```

The CLI layer then stores the real ID as the registry `golden_id` so later `--resume` calls succeed.

### 2. Project directory encoding folds `_` to `-`

Claude Code places transcripts under `~/.claude/projects/<encoded-cwd>`. On Windows the encoding not only replaces `\`, `/`, and `:` with `-`, but also `_`.

| Workspace | Encoded directory name |
|---|---|
| `C:\Users\liao_\AppData\Local\hermes\projects\test-gold` | `C--Users-liao--AppData-Local-hermes-projects-test-gold` |

A wrapper that expects `C--Users-liao_-AppData-...` will look in the wrong place and fail to find transcripts or detect double-prime guards.

**Fix:** `re.sub(r"[\\/:_]", "-", os.path.abspath(path))`

### 3. Context-only prompts may not produce JSON

`claude -p "$(cat CONTEXT.md)" --output-format json --max-turns 5` returned plain text like:

```
I'm set up and ready to help with the Hermes golden_session test workspace. What would you like me to do?
```

This appears to be a Windows CLI heuristic: when the prompt reads like pure project context without a direct task, it switches to conversational mode and ignores `--output-format json`.

**Workarounds:**

- Keep `CONTEXT.md` concise and include an implied directive (e.g. "You are working in ...").
- For fully programmatic use, append a tiny explicit task such as "Reply OK to confirm you understand this context." before the first failing JSON parse, then recover gracefully.
- Make the parser robust to plain-text fallback: if the last line is not JSON, treat it as a non-error result and continue.

### 4. `.cmd` invocation works with list args

`subprocess.run([r"D:\Users\liao_\AppData\Roaming\npm\claude.cmd", "-p", "...", ...])` works directly from native Python on Windows. No need to wrap in `cmd.exe /c` unless you are shell-joining the command.

## Smoke-test commands

```bash
export CLAUDE_BIN="D:\Users\liao_\AppData\Roaming\npm\claude.cmd"
export GS_LIB="C:\Users\liao_\AppData\Local\hermes\.local\lib"
export PYTHONPATH="C:\Users\liao_\AppData\Local\hermes\.local\lib"

python -m golden_session prime \
  --name test-gold \
  --cwd "C:\Users\liao_\AppData\Local\hermes\projects\test-gold" \
  --context-file "C:\Users\liao_\AppData\Local\hermes\projects\test-gold\CONTEXT.md" \
  --description "Test GOLD session on Windows" \
  --tools Read Edit Bash \
  --max-turns 10 \
  --max-budget-usd 0.50 \
  --ceiling-turns 20 \
  --ceiling-budget 2.00

python -m golden_session run \
  --name test-gold \
  --task "Use Bash to run: echo Hello > run-001/hello.txt" \
  --run-dir "C:\Users\liao_\AppData\Local\hermes\projects\test-gold\run-001" \
  --tools Bash
```

## Registry entry after a successful prime

`C:\Users\liao_\.golden_session\registry.json` contains:

```json
{
  "test-gold": {
    "cwd": "C:\\Users\\liao_\\AppData\\Local\\hermes\\projects\\test-gold",
    "golden_id": "<the-real-session-id-from-the-transcript>",
    "description": "Test GOLD session on Windows",
    "defaults": { ... },
    "ceilings": { ... }
  }
}
```

The `golden_id` must be the transcript UUID, not the pre-generated one, for `--resume` to find the conversation.

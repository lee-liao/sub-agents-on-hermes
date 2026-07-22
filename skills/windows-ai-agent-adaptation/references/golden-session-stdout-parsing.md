# Golden-session stdout parsing pitfalls on Windows

When a Python wrapper invokes the `golden_session` CLI on Windows and then tries to interpret its stdout, two common parsing mistakes break otherwise successful runs. Both are easy to miss because the CLI exits with code 0 and produces the correct artifact.

## 1. Do not parse only the last line of stdout

`golden_session` (and many modern CLI tools) emits JSON with `json.dumps(..., indent=2)`. The last line of a multi-line JSON object is usually a stray closing brace:

```json
{
  "ok": true,
  "command": "run",
  ...
}
```

A parser that does `json.loads(stdout.strip().splitlines()[-1])` will see only `"}"` and fail with `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`.

**Fix:** parse the first complete JSON object from the stream and ignore trailing text.

```python
import json

def parse_first_json(stdout: str):
    text = (stdout or "").strip()
    if not text:
        raise ValueError("empty stdout")
    payload, _ = json.JSONDecoder().raw_decode(text)
    return payload

# Use it like this:
# result = parse_first_json(proc.stdout)
# success = not result.get("is_error", False)
```

`raw_decode` finds the first well-formed JSON value and returns the index where it ends, so any trailing text (Claude usage metadata, logs, newline noise) is ignored.

## 2. Resumed sessions append Claude usage metadata to stdout

When a golden session is resumed with `--continue`, the underlying Claude Code CLI may append extra usage metadata after the result JSON. For example:

```json
{
  "ok": true,
  "command": "run",
  ...
}
{
  "usage": {
    "input_tokens": 2548,
    "output_tokens": 1234
  }
}
```

A parser that does `json.loads(stdout)` on the whole buffer fails because the stream now contains two concatenated objects. A last-line parser fails because the last line is not a complete object.

**Fix:** the same `raw_decode` approach shown above handles this automatically — it extracts the first object and discards the rest.

## 3. Do not use `shlex.split()` on Windows env vars that contain paths

Workflows often expose the golden-session executable via an env var such as `PBI_GOLDEN_SESSION_CMD` or `GOLDEN_SESSION_CMD`. On Windows, a user might set:

```powershell
$env:PBI_GOLDEN_SESSION_CMD = '"D:\Program Files\Python311\python.exe" -m golden_session'
```

`shlex.split(value)` treats backslashes as POSIX shell escapes, so `D:\Program Files\Python311\python.exe` becomes `D:Program Files\Python311\python.exe` and the command fails to launch.

**Fix:** use a Windows-aware splitter that treats backslashes literally and respects double quotes.

```python
import re
import os

def split_windows_cmd(value: str) -> list[str]:
    tokens = re.findall(r'"[^"]*"|[^\s]+', value)
    return [t[1:-1] if t.startswith('"') and t.endswith('"') else t for t in tokens]

# Use it like this:
# cmd = os.environ.get("PBI_GOLDEN_SESSION_CMD")
# args = split_windows_cmd(cmd) if cmd else [sys.executable, "-m", "golden_session"]
```

On non-Windows platforms, `shlex.split(value)` is still the correct choice.

## Verification recipe

Run a golden-session CLI that already has a case directory and add `--continue` to exercise the resumed path. Then verify the wrapper parses the output and the artifact exists:

```bash
cd "D:\MyCode\Ivan\sub-agents-on-hermes"
PYTHONPATH="D:/MyCode/Ivan/sub-agents-on-hermes" \
  python -m golden_session run \
  --name fresh-power-bi --case-id 255-test \
  --continue --task-template analysis-task.md \
  --param WORK_ITEM_ID=255-test
```

The wrapper should be able to parse the result as JSON and locate the artifact under `HERMES_HOME\projects\fresh-power-bi\runs\255-test\analysis\report.md` (or whatever output the task template writes).

## See also

- `references/winerror-193-subprocess-resolution.md` — how to resolve `claude.cmd` before spawning.
- `references/claude-code-windows-cli-quirks.md` — other Windows-specific behaviors of the native Claude CLI.

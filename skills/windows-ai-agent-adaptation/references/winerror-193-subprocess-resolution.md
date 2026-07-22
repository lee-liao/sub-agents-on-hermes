# Resolving Windows `WinError 193` for npm-installed `.cmd` CLIs

Python `subprocess.run(["claude", ...])` on Windows can fail with:

```text
OSError: [WinError 193] %1 is not a valid Win32 application
FileNotFoundError: [WinError 2] The system cannot find the file specified
```

even when `claude` is installed and works from the git-bash shell. The root cause is usually that npm installs the CLI as a `.cmd` shim (e.g. `%APPDATA%\Roaming\npm\claude.cmd`), and Windows `CreateProcess` cannot spawn a `.cmd` file by bare name — it needs the absolute path or the `.cmd` extension resolved via `PATHEXT`.

## Why git-bash sees `claude` but native Python does not

git-bash receives an extensionless shell shim (`claude` with no extension). Native Windows Python uses `PATHEXT` and `shutil.which`, so it looks for `claude.exe`, `claude.cmd`, `claude.bat`, etc. If the `.cmd` exists but is not on the PATH inherited by the Python process, both lookups fail.

## Minimal fix in the Python runner

1. **Resolve the executable to the on-disk file.** Use `shutil.which` on Windows so `claude` → `claude.cmd` (or `claude.exe`). On POSIX or for absolute paths, leave it alone.

   ```python
   import os
   import shutil

   def _resolve_cmd(executable: str, path: Optional[str] = None) -> str:
       if os.name != "nt" or os.path.isabs(executable):
           return executable
       return shutil.which(executable, path=path) or executable
   ```

2. **Inject the npm prefix if the executable is not found.** Native Windows Python subprocesses may not inherit the git-bash PATH. Discover the npm bin directory (e.g. from `CLAUDE_NPM_PREFIX`, `npm_config_prefix`, `APPDATA\Roaming\npm`, or `~\AppData\Roaming\npm`) and prepend it to the effective PATH.

3. **Rewrite `argv[0]` before `subprocess.run`.** After step 2, resolve again with the effective PATH so the bare name becomes the absolute `.cmd` path.

   ```python
   args = list(args)
   args[0] = _resolve_cmd(args[0], path=env.get("PATH"))
   subprocess.run(args, ...)
   ```

4. **Respect the caller's PATH overlay.** If the caller passes an `env` overlay containing `PATH`, use that as the effective PATH for both the lookup and the npm-prefix injection. Do not overwrite the overlay with the process PATH.

   ```python
   search_path = env.get("PATH") or os.environ.get("PATH", "")
   if shutil.which(claude_bin, path=search_path) is None:
       ...
       env["PATH"] = os.pathsep.join([npm_prefix, search_path])
   ```

## Testing the fix on Windows

Create a fake `.cmd` shim and assert the runner resolves and spawns it:

```python
import pytest
from golden_session.runner import default_runner, RunOutput

@pytest.mark.skipif(os.name != "nt", reason="Windows-only behavior")
def test_default_runner_spawns_cmd_shim(tmp_path):
    shim = tmp_path / "fakeclaude.cmd"
    shim.write_text("@echo off\necho OK\n")
    out = default_runner(["fakeclaude"], str(tmp_path), env={"PATH": str(tmp_path)})
    assert isinstance(out, RunOutput)
    assert out.returncode == 0
    assert "OK" in out.stdout
```

This exercises the same CreateProcess path that a real `golden_session run` invocation uses.

## Also apply to helper commands

Any helper that checks whether `claude` is runnable should resolve the executable first:

```python
def _claude_works(claude_bin: str) -> bool:
    proc = subprocess.run(
        [_resolve_cmd(claude_bin), "--version"],
        ...
    )
    return proc.returncode == 0

def ensure_claude(claude_bin: str = "claude") -> None:
    ...
    subprocess.run(
        [_resolve_cmd("npm"), "install", "-g", "@anthropic-ai/claude-code"],
        ...
    )
```

## Why not `cmd.exe /c`?

Passing the absolute path to the `.cmd` file directly in a list argument is simpler and avoids shell-quoting issues. Use `cmd.exe /c "claude ..."` only when you must shell-join a command string or when the shim is not discoverable on PATH.

## Environment notes from this session

- Host: Windows 10
- Python: native Windows CPython 3.13
- npm prefix observed: `D:\Users\liao_\AppData\Roaming\npm`
- git-bash `which claude` returned the extensionless shim `D:\Users\liao_\AppData\Roaming\npm\claude`.
- Python `shutil.which("claude")` returned the `.cmd` shim once the npm prefix was on the search path.

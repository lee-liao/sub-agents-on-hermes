# Windows: `OSError: [WinError 193] %1 is not a valid Win32 application`

Session: 2026-07-19. Context: Hermes Agent on Windows, `golden_session` engine
running on native Python, trying to spawn the npm-installed `claude` CLI from a
subprocess.

## Symptom

```text
OSError: [WinError 193] %1 is not a valid Win32 application
```

when `golden_session run` tries to launch the Claude CLI.

## Root cause

`claude` is installed by npm as a `.cmd` batch wrapper (e.g.
`C:/Users/<user>/AppData/Roaming/npm/claude.cmd`). Python's `subprocess.run()`
with `shell=False` and a bare command name like `claude` attempts to execute the
file directly as a PE binary. Batch files are not binary executables, so Windows
rejects them with WinError 193.

The same error can appear when the npm `bin` directory is not on the PATH
inherited by the native Python process, so `shutil.which("claude")` returns
`None` and the bare name fails to resolve at all.

## Correct fix: resolve the shim to its absolute path

Use `shutil.which` on Windows to map the bare name to the on-disk `.cmd` (or
`.exe`) file, then pass that absolute path to `subprocess.run` with
`shell=False`. Also inject the npm prefix directory into the effective PATH if
the executable is not found on the inherited PATH.

```python
import os
import shutil
from typing import Optional


def _resolve_cmd(executable: str, path: Optional[str] = None) -> str:
    """Map a bare command name to its on-disk file on Windows.

    npm installs CLIs as `.cmd` shims, and CreateProcess cannot spawn a `.cmd`
    file by bare name. `shutil.which` honors PATHEXT, so it resolves `claude` ->
    `claude.cmd`; the absolute path is spawnable. No-op on POSIX or for absolute
    paths.
    """
    if os.name != "nt" or os.path.isabs(executable):
        return executable
    return shutil.which(executable, path=path) or executable


def default_runner(args, cwd, env=None):
    claude_bin = args[0] if args else "claude"
    env = dict(env) if env else {}

    if not os.path.isabs(claude_bin) and shutil.which(claude_bin) is None:
        npm_prefix = os.environ.get("CLAUDE_NPM_PREFIX")
        if not npm_prefix:
            for candidate in (
                os.environ.get("npm_config_prefix"),
                os.path.expanduser("~\\AppData\\Roaming\\npm"),
                os.path.join(os.path.dirname(os.environ.get("APPDATA", "")), "Roaming", "npm"),
            ):
                if candidate and os.path.isdir(candidate):
                    possible = os.path.join(candidate, "claude.cmd")
                    if os.path.exists(possible):
                        npm_prefix = candidate
                        break
                    possible = os.path.join(candidate, "claude.exe")
                    if os.path.exists(possible):
                        npm_prefix = candidate
                        break
        if npm_prefix:
            env["PATH"] = os.pathsep.join([npm_prefix, os.environ.get("PATH", "")])

    # Rewrite argv[0] to the actual file (`claude.cmd` on Windows) so
    # subprocess can spawn it; searches the overlay PATH when we injected one.
    args = list(args)
    if args:
        args[0] = _resolve_cmd(args[0], path=env.get("PATH"))

    import subprocess
    return subprocess.run(
        args, cwd=cwd, env={**os.environ, **env},
        capture_output=True, text=True, check=False,
    )
```

Apply the same `_resolve_cmd` to any helper that checks whether `claude` or
`npm` is runnable (e.g. `_claude_works`, `ensure_claude`).

## Why not `shell=True`?

`shell=True` on Windows delegates to `cmd.exe`, which can run `.cmd` files. It
works for a quick probe, but in a wrapper it introduces quoting and escaping
complexities for long task prompts and file paths. Resolving the executable path
once and keeping `shell=False` is simpler and more reliable for production code.

## Common wrong turns

- Adding the npm prefix to `PATH` but not resolving the bare name to the `.cmd`
  file still leaves `subprocess.run(["claude", ...])` failing on Windows.
- Setting `CLAUDE_BIN` to a bare `claude` instead of the absolute `.cmd` path
  does not help unless the wrapper also resolves it.
- Changing `PATHEXT` to include `.CMD` does not help; the issue is that a bare
  name is resolved to a `.cmd` file but passed to CreateProcess as a bare name.

## Verification

Run the skill probe script:

```bash
python "C:/Users/liao_/AppData/Local/hermes/skills/claude-code-gold/scripts/check_windows_claude_spawn.py"
```

It checks whether `claude` can be resolved and spawned. For a regression test,
create a fake `.cmd` shim and assert the runner resolves it to the absolute path
and returns exit code 0:

```python
import pytest
from golden_session.runner import default_runner

@pytest.mark.skipif(os.name != "nt", reason="Windows-only behavior")
def test_runner_spawns_cmd_shim(tmp_path):
    shim = tmp_path / "fakeclaude.cmd"
    shim.write_text("@echo off\necho OK\n")
    out = default_runner(["fakeclaude"], str(tmp_path), env={"PATH": str(tmp_path)})
    assert out.returncode == 0
    assert "OK" in out.stdout
```

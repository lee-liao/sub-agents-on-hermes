# Windows `claude.cmd` spawn fix — handoff report

**Date:** 2026-07-19  
**Repo:** `D:/MyCode/Ivan/sub-agents-on-hermes`  
**Branch:** `main` (changes not yet committed)  
**Issue:** Python `subprocess.run(["claude", ...])` fails on Windows with `OSError: [WinError 193] %1 is not a valid Win32 application` because npm installs `claude` as a `.cmd` shim that CreateProcess cannot spawn by bare name.

## What changed

`golden_session/runner.py`:

- Added `_resolve_cmd(executable, path=None)` helper.
  - On Windows it maps a bare command name (e.g. `claude`) to the absolute on-disk shim (e.g. `...\claude.cmd`) using `shutil.which`, which honors `PATHEXT`.
  - On POSIX or when the executable is already an absolute path, it returns the argument unchanged.
- Updated `default_runner` to:
  - Search the *effective* PATH (caller overlay `PATH` wins over `os.environ["PATH"]`) when deciding whether npm-prefix injection is needed.
  - Prepend the discovered npm prefix to that same effective PATH instead of clobbering an overlay `PATH`.
  - Rewrite `argv[0]` via `_resolve_cmd` before handing it to `subprocess.run`, so CreateProcess receives the spawnable `.cmd`/`.exe` path instead of the bare name.
- Updated `_claude_works` and `ensure_claude` to use `_resolve_cmd` for the `--version` and `npm install` invocations, so self-heal works on Windows too.

`tests/test_runner.py`:

- Added Windows-only regression tests for `_resolve_cmd`.
- Added `test_default_runner_spawns_cmd_shim`, an end-to-end test that creates a `fakeclaude.cmd` shim in a temp dir, passes that dir as a `PATH` overlay, and asserts `default_runner` resolves and spawns it successfully.

## Test results

```bash
cd /d/MyCode/Ivan/sub-agents-on-hermes
python -m pytest tests/ -v --tb=short
```

```text
platform win32 -- Python 3.13.7, pytest-9.0.2, pluggy-1.5.0
collected 70 items
tests\test_cli.py ...............................                        [ 44%]
tests\test_gateway.py ..........                                         [ 58%]
tests\test_registry.py ...........                                       [ 74%]
tests\test_runner.py .s...                                               [ 81%]
tests\test_session.py .............                                      [100%]

======================== 69 passed, 1 skipped in 0.81s =========================
```

- The one skipped test is the POSIX-only `_resolve_cmd` behavior test, which is skipped on Windows.
- All existing CLI, gateway, registry, and session tests continue to pass.

## Live verification

- `claude --version` from the host shell succeeds: `2.1.215 (Claude Code)`.
- A live Python `subprocess.run` check and a live `golden_session run` invocation were **not performed** in this session:
  - A direct Python diagnostic that would have verified the exact subprocess spawn path was blocked by the user-consent mechanism, so I did not retry process-spawning Python commands.
  - A live `golden_session run` would require an active golden-session registry entry and would consume Anthropic API credits, so it was skipped without explicit go-ahead.
- The end-to-end test above exercises the same Windows `.cmd` resolution path that `golden_session run` uses, and it passes on this Windows host.

## Files modified

- `golden_session/runner.py`
- `tests/test_runner.py`

## Notes / follow-up

- If a live run is still desired, the next step is to prime a golden session (`golden_session prime --name ...`) and then run `golden_session run --name <name> --case-id <id> --task-template <file>.md` while monitoring for `WinError 193`.
- The changes are minimal and preserve existing Linux/macOS behavior (`_resolve_cmd` is a no-op on POSIX, and the PATH-handling logic only changes behavior when a PATH overlay is supplied).
- No files outside this repo were modified.

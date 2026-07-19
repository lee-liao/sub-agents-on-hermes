"""Windows executable resolution in the runner seam.

Regression coverage for the WinError 193 launch failure: npm installs `claude`
as a `claude.cmd` shim, and CreateProcess cannot spawn a `.cmd` file by bare
name. `_resolve_cmd` must rewrite the bare name to the absolute shim path.
"""

import os

import pytest

from golden_session.runner import _resolve_cmd


def test_absolute_path_is_returned_unchanged(tmp_path):
    exe = str(tmp_path / "claude.cmd")
    assert _resolve_cmd(exe) == exe


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only behavior")
def test_posix_bare_name_is_untouched():
    assert _resolve_cmd("claude") == "claude"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only behavior")
def test_windows_resolves_cmd_shim_via_supplied_path(tmp_path):
    shim = tmp_path / "fakeclaude.cmd"
    shim.write_text("@echo off\n")
    resolved = _resolve_cmd("fakeclaude", path=str(tmp_path))
    assert os.path.normcase(resolved) == os.path.normcase(str(shim))


@pytest.mark.skipif(os.name != "nt", reason="Windows-only behavior")
def test_windows_unresolvable_name_falls_through(tmp_path):
    assert _resolve_cmd("no-such-binary-xyz", path=str(tmp_path)) == "no-such-binary-xyz"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only behavior")
def test_default_runner_spawns_cmd_shim(tmp_path):
    """End-to-end: a bare command name resolves to a .cmd file and is spawned.

    Regression for the WinError 193 bug: `default_runner` must rewrite the
    executable to the absolute .cmd shim path so CreateProcess can spawn it.
    """
    from golden_session.runner import RunOutput, default_runner

    shim = tmp_path / "fakeclaude.cmd"
    shim.write_text("@echo off\necho OK\n")
    args = ["fakeclaude"]
    out = default_runner(args, str(tmp_path), env={"PATH": str(tmp_path)})
    assert isinstance(out, RunOutput)
    assert out.returncode == 0
    assert "OK" in out.stdout

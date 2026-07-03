"""Test fixtures: a faithful in-memory fake of the `claude -p` CLI.

The fake reproduces the behaviours doc 02 verified against Claude Code 2.1.x so
the guardrails can be exercised without auth or the real binary:

* ``--session-id X``        -> prime: write X.jsonl (flat N lines) under the
                               cwd-scoped project dir; return sid X.
* ``--resume X --fork-session`` -> fork: snapshot X.jsonl to a NEW sid file; the
                               source (GOLD) file is untouched -> stays pristine.
* ``--resume X``            -> append: add a line to X.jsonl; return the SAME sid.
* resume when X.jsonl is absent in this cwd -> silently start a FRESH session
  with a new id (doc 02 gotcha 2) — this is what F9 must catch.

Session lookup is cwd-scoped exactly like the real CLI, so pointing a
GoldenSession at the wrong workspace reproduces the silent-fresh-session bug.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import pytest

from golden_session.runner import RunOutput
from golden_session.session import GoldenSession

PRIME_LINES = 6  # doc 02 example: GOLD stayed flat at 6 lines across forks


def _project_dir(projects_dir: str, cwd: str) -> str:
    return os.path.join(projects_dir, GoldenSession.encode_cwd(cwd))


def _read_lines(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


@dataclass
class FakeClaude:
    """Configurable fake runner. Inject via GoldenSession(runner=fake)."""

    projects_dir: str
    calls: list[list[str]] = field(default_factory=list)
    envs: list[dict | None] = field(default_factory=list)   # env overlay per call (F12)
    fail_mode: str | None = None          # None | "error" -> every run reports is_error
    cost_per_call: float = 0.01
    _counter: int = 0

    def __call__(self, args, cwd, env=None) -> RunOutput:
        self.calls.append(list(args))
        self.envs.append(dict(env) if env is not None else None)
        flags = _parse_flags(args)
        proj = _project_dir(self.projects_dir, cwd)
        os.makedirs(proj, exist_ok=True)

        if flags["session_id"]:                       # prime
            sid = flags["session_id"]
            self._write(proj, sid, PRIME_LINES)
        elif flags["resume"]:
            src = os.path.join(proj, f"{flags['resume']}.jsonl")
            if not os.path.exists(src):               # cwd-scoped miss -> fresh session
                sid = self._new_sid()
                self._write(proj, sid, 1)
            elif flags["fork"]:                       # branch: snapshot to new sid
                sid = self._new_sid()
                self._copy(src, os.path.join(proj, f"{sid}.jsonl"))
            else:                                      # append to same sid
                sid = flags["resume"]
                self._append(src)
        else:
            sid = self._new_sid()
            self._write(proj, sid, 1)

        is_error = self.fail_mode == "error"
        payload = {
            "type": "result",
            "subtype": "error_during_execution" if is_error else "success",
            "is_error": is_error,
            "result": "boom" if is_error else "done",
            "session_id": sid,
            "total_cost_usd": self.cost_per_call,
            "num_turns": 1,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        return RunOutput(returncode=1 if is_error else 0, stdout=json.dumps(payload), stderr="")

    # --- helpers ---------------------------------------------------------

    def _new_sid(self) -> str:
        self._counter += 1
        return f"sid-{self._counter:04d}"

    @staticmethod
    def _write(proj: str, sid: str, lines: int) -> None:
        with open(os.path.join(proj, f"{sid}.jsonl"), "w", encoding="utf-8") as fh:
            for i in range(lines):
                fh.write(json.dumps({"line": i}) + "\n")

    @staticmethod
    def _append(path: str) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"line": "appended"}) + "\n")

    @staticmethod
    def _copy(src: str, dst: str) -> None:
        with open(src, "r", encoding="utf-8") as r, open(dst, "w", encoding="utf-8") as w:
            w.write(r.read())


def _parse_flags(args) -> dict:
    out = {"session_id": None, "resume": None, "fork": False}
    args = list(args)
    for i, a in enumerate(args):
        if a == "--session-id" and i + 1 < len(args):
            out["session_id"] = args[i + 1]
        elif a == "--resume" and i + 1 < len(args):
            out["resume"] = args[i + 1]
        elif a == "--fork-session":
            out["fork"] = True
    return out


# --- pytest fixtures -----------------------------------------------------


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    d = tmp_path / "projects"
    d.mkdir()
    # GoldenSession + Registry read these envs when params aren't passed.
    monkeypatch.setenv("GOLDEN_SESSION_PROJECTS_DIR", str(d))
    return str(d)


@pytest.fixture
def registry_path(tmp_path, monkeypatch):
    p = tmp_path / "registry.json"
    monkeypatch.setenv("GOLDEN_SESSION_REGISTRY", str(p))
    return str(p)


@pytest.fixture(autouse=True)
def claude_config(tmp_path, monkeypatch):
    """Redirect Claude Code's trust file to a temp path for EVERY test.

    Improvement #1 makes `prime` write projects[cwd].hasTrustDialogAccepted into
    $CLAUDE_CONFIG_FILE (default ~/.claude.json). Autouse so no test — old or new —
    can mutate the developer's real config. Returns the temp path for assertions.
    """
    p = tmp_path / "claude.json"
    monkeypatch.setenv("CLAUDE_CONFIG_FILE", str(p))
    return str(p)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return str(ws)


@pytest.fixture
def fake(projects_dir):
    return FakeClaude(projects_dir=projects_dir)


@pytest.fixture
def line_counter(projects_dir):
    def count(cwd: str, sid: str) -> int:
        return _read_lines(os.path.join(_project_dir(projects_dir, cwd), f"{sid}.jsonl"))

    return count

"""Engine guardrail tests — PRD §6 acceptance criteria 1-6 (F1-F10)."""

from __future__ import annotations

import threading
import uuid

import pytest

from golden_session import (
    BudgetError,
    DoublePrimeError,
    GoldProtectionError,
    GoldenSession,
    RetryCeilingError,
    SessionNotFoundError,
    WorkspaceError,
)
from tests.conftest import PRIME_LINES


def make_session(workspace, fake, **kw):
    params = dict(
        workspace=workspace,
        golden_id=str(uuid.uuid4()),
        max_turns=20,
        max_budget_usd=1.0,
        max_continues=3,
        allowed_tools=["Read", "Write", "Bash"],
        runner=fake,
    )
    params.update(kw)
    return GoldenSession(**params)


# --- Criterion 1: prime + pristine GOLD (F1, F2, F7) ---------------------


def test_prime_then_three_forks_keep_gold_flat(workspace, fake, line_counter):
    gs = make_session(workspace, fake)
    gs.prime("project context")
    assert line_counter(workspace, gs.golden_id) == PRIME_LINES

    for _ in range(3):
        t = gs.run_task("do work")
        assert t.session_id != gs.golden_id          # F2 — fork yields a new sid
    # F2 — GOLD transcript stays flat across all forks
    assert line_counter(workspace, gs.golden_id) == PRIME_LINES


def test_double_prime_is_refused(workspace, fake):
    gs = make_session(workspace, fake)
    gs.prime("ctx")
    with pytest.raises(DoublePrimeError):       # F1
        gs.prime("ctx again")


def test_continue_on_gold_is_refused(workspace, fake):
    gs = make_session(workspace, fake)
    gs.prime("ctx")
    with pytest.raises(GoldProtectionError):    # F7
        gs.continue_task(gs.golden_id, "sneak an append")


# --- Criterion 2: end-to-end task (F2, F3) -------------------------------


def test_task_result_is_parseable_and_populated(workspace, fake):
    gs = make_session(workspace, fake)
    gs.prime("ctx")
    t = gs.run_task("write output/result.txt")
    assert t.is_error is False
    assert t.session_id
    assert t.terminal_reason == "success"
    assert t.cost_usd > 0                       # F3 — cost populated
    assert t.result == "done"


# --- Criterion 3: recover on failure (F4) --------------------------------


def test_recover_appends_to_same_session(workspace, fake, line_counter):
    gs = make_session(workspace, fake)
    gs.prime("ctx")
    t = gs.run_task("attempt")
    before = line_counter(workspace, t.session_id)

    fixed = gs.continue_task(t.session_id, "fix: handle the edge case")
    assert fixed.session_id == t.session_id      # F4 — same session id
    after = line_counter(workspace, t.session_id)
    assert after > before                        # transcript grew (append, not fork)


# --- Criterion 4: budget + retry caps (F5, F10) --------------------------


def test_caps_are_mandatory_and_positive(workspace, fake):
    with pytest.raises(BudgetError):
        make_session(workspace, fake, max_turns=0)
    with pytest.raises(BudgetError):
        make_session(workspace, fake, max_budget_usd=0)


def test_caps_are_passed_to_cli(workspace, fake):
    gs = make_session(workspace, fake, max_turns=5, max_budget_usd=0.10)
    gs.prime("ctx")
    gs.run_task("task")
    last = fake.calls[-1]
    assert "--max-turns" in last and last[last.index("--max-turns") + 1] == "5"
    assert "--max-budget-usd" in last and last[last.index("--max-budget-usd") + 1] == "0.1"


def test_per_call_override_is_clamped_down_to_ceiling(workspace, fake):
    gs = make_session(workspace, fake, max_turns=5, max_budget_usd=0.10)
    gs.prime("ctx")
    gs.run_task("task", max_turns=999, max_budget_usd=999.0)  # caller asks for more
    last = fake.calls[-1]
    assert last[last.index("--max-turns") + 1] == "5"          # clamped, not 999
    assert last[last.index("--max-budget-usd") + 1] == "0.1"


def test_retry_loop_stops_at_ceiling(workspace, line_counter):
    # A task that keeps failing: the recover loop must stop at max_continues (F10).
    from tests.conftest import FakeClaude
    import os

    fake = FakeClaude(projects_dir=os.environ["GOLDEN_SESSION_PROJECTS_DIR"])
    gs = make_session(workspace, fake, max_continues=2)
    gs.prime("ctx")
    t = gs.run_task("attempt")

    fake.fail_mode = "error"
    attempts = 0
    with pytest.raises(RetryCeilingError):
        for _ in range(10):                      # driver would loop forever without F10
            gs.continue_task(t.session_id, "retry fix")
            attempts += 1
    assert attempts == 2                          # exactly max_continues appends, then refused


# --- Criterion 5: cwd correctness + loud not-found (F6, F9) ---------------


def test_workspace_is_mandatory(fake):
    with pytest.raises(WorkspaceError):
        GoldenSession(workspace="", golden_id="x", max_turns=1, max_budget_usd=1.0, runner=fake)


def test_continue_from_wrong_cwd_raises_loudly(workspace, fake, tmp_path):
    gs = make_session(workspace, fake)
    gs.prime("ctx")
    t = gs.run_task("attempt")

    # Same golden_id + session id, but a DIFFERENT workspace -> the fake (like the
    # real CLI) can't find the transcript and silently starts a fresh session.
    wrong_ws = str(tmp_path / "wrong")
    import os

    os.makedirs(wrong_ws, exist_ok=True)
    gs_wrong = GoldenSession(
        workspace=wrong_ws,
        golden_id=gs.golden_id,
        max_turns=20,
        max_budget_usd=1.0,
        runner=fake,
    )
    with pytest.raises(SessionNotFoundError):     # F9 — does NOT silently continue
        gs_wrong.continue_task(t.session_id, "fix from the wrong place")


# --- Criterion 6: single-writer (F8) -------------------------------------


def test_concurrent_appends_serialize(workspace, fake, line_counter):
    gs = make_session(workspace, fake, max_continues=50)
    gs.prime("ctx")
    t = gs.run_task("attempt")
    start = line_counter(workspace, t.session_id)

    errors: list[Exception] = []

    def worker():
        try:
            gs.continue_task(t.session_id, "concurrent fix")
        except Exception as exc:  # pragma: no cover - recorded for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors
    # F8 — every append landed exactly once; no lost/torn writes from interleaving.
    assert line_counter(workspace, t.session_id) == start + len(threads)


def test_cleanup_preserves_gold_and_keep(workspace, fake):
    gs = make_session(workspace, fake)
    gs.prime("ctx")
    keep = gs.run_task("keep me")
    gs.run_task("delete me")
    gs.run_task("delete me too")

    deleted = gs.cleanup_forks(keep={keep.session_id})
    survivors = set(gs.list_forks())
    assert gs.golden_id in survivors             # GOLD always preserved
    assert keep.session_id in survivors
    assert len(deleted) == 2

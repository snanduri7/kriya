"""LR-R1-M1 fix: the attempt's evidence identity spans the whole retry-loop
iteration, not only ``run_attempt``.

MEASURED before the fix: the direct pipeline's terminal full-regression gate
(and every other check after ``run_attempt`` returns: required
verification, requirements, static analysis, approval) was recorded with no
attempt number, after attempt.closed; and attempt.closed could never say
PASSED, because overall_attempt_succeeded is set after run_attempt returns.
"""
from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    run_direct,
)

from kriya.core.attempt_evidence import reader
from kriya.core.state_paths import ENV_STATE_DIR


def _records(tmp_path, monkeypatch, developer):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    runtime = ChaosRuntime(lambda role, request: developer(request) if role == "developer"
                           else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        run_direct(chaos_engine(chaos_config()), "add sub to calc.py", workspace)
    [run_id] = reader.list_runs(str(state))
    return list(reader.open_run(str(state), run_id).records())


def test_the_terminal_regression_gate_belongs_to_its_attempt(tmp_path, monkeypatch):
    records = _records(tmp_path, monkeypatch, lambda request: CALC_WITH_SUB)
    gates = [r for r in records if r["kind"] == "gate.result" and r["payload"]["stage"] == "validator"]
    assert {g["payload"]["gate"] for g in gates} >= {"compile", "tests"}
    assert all(g["attempt_number"] == 1 for g in gates), [(g["payload"]["gate"], g["attempt_number"]) for g in gates]
    closed = next(r for r in records if r["kind"] == "attempt.closed")
    regression = next(g for g in gates if g["payload"]["gate"] == "tests")
    assert regression["seq"] > closed["seq"]          # after run_attempt returned, still attempt 1
    [concluded] = [r for r in records if r["kind"] == "attempt.concluded"]
    assert concluded["attempt_number"] == 1 and concluded["payload"]["succeeded"] is True
    assert concluded["seq"] > regression["seq"]


def test_each_failed_iteration_concludes_unsuccessful_with_its_own_number(tmp_path, monkeypatch):
    records = _records(tmp_path, monkeypatch, lambda request: CALC.replace("a + b", "a - b"))
    concluded = [r for r in records if r["kind"] == "attempt.concluded"]
    opened = [r["attempt_number"] for r in records if r["kind"] == "attempt.opened"]
    assert len(opened) > 1 and [r["attempt_number"] for r in concluded] == opened
    assert all(r["payload"]["succeeded"] is False for r in concluded)
    # The failed attempt's diagnosis and recovery decision carry its number.
    for kind in ("diagnosis", "recovery.decision"):
        first = next(r for r in records if r["kind"] == kind)
        assert first["attempt_number"] == first["payload"]["attempt"] == 1
    assert all(r["payload"]["outcome"] != "PASSED" for r in records if r["kind"] == "attempt.closed")

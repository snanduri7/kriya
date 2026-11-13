"""ENFORCE-TERMINAL-ENV-REASON-LOST-001 (R8-C, owner ruling 2026-11-12 after Scenario-A run 8, items 38-46): the enforce run's terminal
keeps the stopping unit's typed stop.

MEASURED, run 8: the unit result said `failure_category: containment_setup_failed` with the CONTAINMENT_SETUP_FAILED reason; the controller
logged "did not pass Quality Gates (status=None)" and the `.enforce` row said `failed / failed`.
Pre-fix characterization (passes on ff23469): ~/kriya-evidence/product-72h-confidence/run8-gaps-2026-11-12/r8c/.

Reporting only: the lifecycle status, the units' statuses and the plan-repair terminal of a plan-scope conflict are asserted unchanged.
NOT fixed here: R7-C (ENFORCE-TERMINAL-CATEGORY-FIRST-SCOPE-CONFLICT-001). In run 7 the stopping unit's OWN result carried the plan-scope
conflict and the typed stop (the generation budget) belonged to an owner invocation inside that unit's recovery: a different first wrong
decision. This change did nothing about it; it was closed on 2026-11-13 by ENFORCE-TERMINAL-CAUSE-NESTED-RECOVERY-STOP-001
(tests/test_enforce_terminal_nested_recovery_stop_001.py), which reproduced it model-free together with run 9's nested budget stop.
"""
import json
import logging

import pytest
import test_e12_enforce_characterization as e12
import test_enforce_owner_recovery_verifier_replay_001 as r7
from test_e14_pre_characterization import _trace_rows

from kriya.workflow.enforce_loop import unit_stop_message, unit_typed_stop

RUN8_REASON = "CONTAINMENT_SETUP_FAILED: CONTAINMENT_PROXY_START_TIMEOUT: Failed to start the registry proxy 'kriya-acq-proxy-x'"
CONTAINMENT_STOP = {"status": None, "quality_gates_passed": False, "files": [], "failure_category": "containment_setup_failed",
                    "environment_failure": RUN8_REASON}
BUDGET_REASON = "GENERATION TIME BUDGET EXHAUSTED: 71.0s remaining"
BUDGET_STOP = {"status": "failure", "quality_gates_passed": False, "files": [], "failure_category": "generation_budget_exhausted",
               "environment_failure": BUDGET_REASON}
ORDINARY_FAILURE = {"status": None, "quality_gates_passed": False, "files": [], "failure_category": "quality_gates_failed",
                    "environment_failure": None}
SCOPE_CONFLICT = {"classification": "PLAN_SCOPE_DEFECT", "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "test",
                  "reason": "the test needs the parser changed", "required_files": ["a.py"], "allowed_files": ["b.py"]}


async def _run(tmp_path, outcomes, caplog=None):
    (tmp_path / "pyproject.toml").write_text(e12._PYPROJECT)
    we = e12._enforce_engine(tmp_path)
    plan = e12._plan_of(e12._unit("s1", "a.py"), e12._unit("s2", "b.py", depends_on=("s1",)))
    result, calls = await e12._enforce(we, tmp_path, plan, goal="Create a.py and b.py", outcomes=outcomes)
    rows = [row for row in _trace_rows(we.kernel.config) if row[0].endswith(".enforce")]
    assert len(rows) == 1
    events = [event["kind"] for event in json.loads(rows[0][3])]
    return result.legacy_result, (rows[0][1], rows[0][2]), events, calls


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_run8_shape_the_terminal_keeps_the_stopping_units_typed_containment_failure(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        legacy, row, events, calls = await _run(tmp_path, {"s2": CONTAINMENT_STOP})

    assert [c["current_subtask_id"] for c in calls] == ["s1", "s2"]
    assert legacy["failure_type"] == "containment_setup_failed" and legacy["failure_category"] == "containment_setup_failed"
    assert legacy["terminal_cause"] == {"subtask_id": "s2", "failure_category": "containment_setup_failed", "reason": RUN8_REASON}
    assert row == ("failed", "containment_setup_failed")                       # the `.enforce` row: the typed cause, not `failed`
    assert "planning.failed" not in events
    s2 = legacy["subtask_results"][-1]
    assert s2["error"] == f"subtask did not pass Quality Gates (containment_setup_failed): {RUN8_REASON}"
    stop_lines = [r.getMessage() for r in caplog.records if "stopped at subtask" in r.getMessage()]
    assert len(stop_lines) == 1 and RUN8_REASON in stop_lines[0] and "status=None" not in stop_lines[0]
    # reporting only: the lifecycle the control plane acts on is what it was
    assert (legacy["status"], legacy["quality_gates_passed"]) == ("failed", False)
    assert [(r["subtask_id"], r["status"]) for r in legacy["subtask_results"]] == [("s1", "completed"), ("s2", "failed")]
    assert "recovery" not in legacy and "plan_scope_conflict" not in legacy and not legacy.get("reason_codes")


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_generation_budget_stop_is_the_terminal_cause_too(tmp_path):
    legacy, row, events, _ = await _run(tmp_path, {"s2": BUDGET_STOP})

    assert row == ("failed", "generation_budget_exhausted") and "planning.failed" not in events
    assert legacy["terminal_cause"] == {"subtask_id": "s2", "failure_category": "generation_budget_exhausted", "reason": BUDGET_REASON}


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_ordinary_gate_failure_keeps_its_lifecycle_category(tmp_path, caplog):
    """Negative control: a unit that merely failed its gates has no typed stop reason - nothing new is claimed for it."""
    with caplog.at_level(logging.WARNING):
        legacy, row, events, _ = await _run(tmp_path, {"s2": ORDINARY_FAILURE})

    assert row == ("failed", "failed") and "planning.failed" not in events
    assert "failure_type" not in legacy and "terminal_cause" not in legacy and legacy["failure_category"] == "failed"
    assert legacy["subtask_results"][-1]["error"] == "subtask did not pass Quality Gates (status=None)"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_stopping_unit_with_a_plan_scope_conflict_is_still_a_planning_error(tmp_path):
    """Negative control: when the STOPPING unit's own result carries the conflict, the terminal is the plan-repair one, exactly as before."""
    legacy, row, events, _ = await _run(tmp_path, {"s2": {**ORDINARY_FAILURE, "plan_scope_conflict": dict(SCOPE_CONFLICT)}})

    assert row[1] == "PLANNING_ERROR" and "planning.failed" in events
    assert legacy["failure_type"] == "PLANNING_ERROR" and legacy["recovery"] == "PLAN_REPAIR"
    assert "PLAN_SCOPE_REVISION_REQUIRED" in legacy["reason_codes"] and legacy["plan_scope_conflict"]["required_files"] == ["a.py"]
    assert "terminal_cause" not in legacy


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_typed_stop_never_outranks_the_stopping_units_own_scope_conflict(tmp_path):
    legacy, row, events, _ = await _run(tmp_path, {"s2": {**CONTAINMENT_STOP, "plan_scope_conflict": dict(SCOPE_CONFLICT)}})

    assert row[1] == "PLANNING_ERROR" and legacy["failure_type"] == "PLANNING_ERROR" and "terminal_cause" not in legacy


# ------------------------------------------------------------------------------------- a typed stop after a recovered plan-scope conflict

@pytest.mark.asyncio
async def test_a_typed_stop_of_the_last_invocation_after_a_recovered_conflict_is_the_terminal_cause(tmp_path):
    """The consumer s6 raises a plan-scope conflict, the owner s3 is reopened, and the consumer's replay then ends on a typed containment stop:
    the final result of the stopping unit names that stop, and so does the terminal."""
    shape = r7.Run7Shape(r7.NEVER_FIXED, r7._overflow_until_fixed, replay_answer=dict(CONTAINMENT_STOP))
    legacy = (await r7._run(tmp_path, shape)).legacy_result

    assert shape.roles() == ["planned", "planned", "owner_recovery", "verifier_replay"]
    assert legacy["plan_recovery_events"]                                                      # the recovery stays on record
    assert legacy["failure_type"] == "containment_setup_failed" and legacy["failure_category"] == "containment_setup_failed"
    assert legacy["terminal_cause"] == {"subtask_id": "s6", "failure_category": "containment_setup_failed", "reason": RUN8_REASON}
    assert "recovery" not in legacy and "plan_scope_conflict" not in legacy
    assert legacy["status"] == "needs_review"                                                  # the lifecycle is what it was


@pytest.mark.asyncio
async def test_a_recovery_cap_exhausted_by_ordinary_owner_failures_is_the_plan_repair_terminal(tmp_path):
    """When the owner never fixes it and nothing typed ends the run, the stopping unit's own last result carries the conflict and the
    terminal is the plan-repair one. (R7-C itself - the typed stop of an owner invocation inside the recovery - is closed by
    ENFORCE-TERMINAL-CAUSE-NESTED-RECOVERY-STOP-001, tests/test_enforce_terminal_nested_recovery_stop_001.py.)"""
    shape = r7.Run7Shape(r7.NEVER_FIXED, r7._overflow_until_fixed)
    legacy = (await r7._run(tmp_path, shape)).legacy_result

    assert legacy["failure_type"] == "PLANNING_ERROR" and legacy["reason_codes"] == ["PLAN_SCOPE_REVISION_REQUIRED"]
    assert legacy["status"] == "needs_review" and "terminal_cause" not in legacy


def test_only_a_result_with_a_category_and_its_stop_reason_is_a_typed_stop():
    assert unit_typed_stop(CONTAINMENT_STOP) == ("containment_setup_failed", RUN8_REASON)
    assert unit_typed_stop(ORDINARY_FAILURE) is None
    assert unit_typed_stop({"environment_failure": RUN8_REASON}) is None and unit_typed_stop({"failure_category": "x", "environment_failure": ""}) is None
    assert unit_typed_stop({"failure_category": "x", "environment_failure": "y" * 5000}) == ("x", "y" * 2000)
    assert unit_stop_message({"status": "failure"}) == "subtask did not pass Quality Gates (status='failure')"

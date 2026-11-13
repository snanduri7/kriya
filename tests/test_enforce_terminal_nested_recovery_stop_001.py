"""ENFORCE-TERMINAL-CAUSE-NESTED-RECOVERY-STOP-001 (owner ruling 2026-11-13 after Scenario-A run 9; closes R7-C,
ENFORCE-TERMINAL-CATEGORY-FIRST-SCOPE-CONFLICT-001): the run's terminal names the typed stop that ended it when that stop belongs to an owner
invocation made inside a consumer's recovery.

MEASURED, model-free, on b141fdd (~/kriya-evidence/product-72h-confidence/run9-fixes-2026-11-13/prefix-characterization/):
- run-9 shape (conflict -> owner repaired -> the carried verifier fails differently -> owner reopened -> generation budget): the terminal was
  ``needs_review`` / ``needs_review`` with no cause;
- run-7 shape (the owner never repairs it; the third reopen ends on the generation budget): the terminal was ``PLANNING_ERROR`` /
  ``PLAN_SCOPE_REVISION_REQUIRED`` with ``recovery: PLAN_REPAIR``.
One first wrong decision in both: the aggregate read the stopping CONSUMER's own result only. Reporting only - the lifecycle status, the units'
statuses and the recovery record are asserted unchanged; no retry or resume reader consumes these fields.
The unit side of the same truth (a unit that had already failed is not relabelled by a refused advisory review) is tests/test_outcome.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import test_enforce_owner_recovery_verifier_replay_001 as r7
import test_owner_recovery_implicated_scope_001 as scope

BUDGET_REASON = ("GENERATION TIME BUDGET EXHAUSTED: refusing to start a 1-file generation pass with 0.0s remaining; estimated generation plus "
                 "gate reserve requires 300.0s.")
BUDGET_STOP = {"status": "failure", "quality_gates_passed": False, "files": [], "failure_category": "generation_budget_exhausted",
               "environment_failure": BUDGET_REASON}
ORDINARY_OWNER_FAILURE = {"status": "failure", "quality_gates_passed": False, "files": [], "failure_category": "quality_gates_exhausted",
                          "environment_failure": None}
CAUSE = {"subtask_id": "s3", "failure_category": "generation_budget_exhausted", "reason": BUDGET_REASON}


def _run9_verdict(parser_text: str, replayed: bool) -> Any:
    """The carried test: an overflow inside the parser until it is repaired, then its own assertion (it names no owner)."""
    return ("owner", r7.OVERFLOW) if "putLong" in parser_text else ("other", scope.DATALENGTH)


def _always_the_owner(parser_text: str, replayed: bool) -> Any:
    return ("owner", r7.OVERFLOW)


async def _legacy(tmp_path, shape):
    return (await r7._run(tmp_path, shape, scope.run9_plan())).legacy_result


@pytest.mark.asyncio
async def test_run9_shape_the_budget_stop_of_the_reopened_owner_is_the_terminal_cause(tmp_path):
    shape = scope.Run9Shape(["putInt+putLong", "three bytes + four bytes", dict(BUDGET_STOP)], _run9_verdict,
                            replay_category="no_progress")
    legacy = await _legacy(tmp_path, shape)

    assert shape.roles() == ["s3:planned", "s5:planned", "s3:owner_recovery", "s5:verifier_replay", "s3:owner_recovery"]
    assert legacy["failure_type"] == "generation_budget_exhausted" == legacy["failure_category"]
    assert legacy["terminal_cause"] == CAUSE
    assert "recovery" not in legacy and "plan_scope_conflict" not in legacy and not legacy.get("reason_codes")
    # reporting only: what the control plane acts on is what it was before the correction
    assert legacy["status"] == "needs_review"
    assert [(r["subtask_id"], r["status"]) for r in legacy["subtask_results"]] == [("s3", "needs_review"), ("s5", "failed")]
    assert len(legacy["plan_recovery_events"]) == 2                               # both recoveries stay on record


@pytest.mark.asyncio
async def test_run7_shape_the_budget_stop_outranks_the_conflict_its_recovery_was_working_on(tmp_path):
    """R7-C: the consumer's own last result carries the plan-scope conflict; the run did not end on it but on the owner's budget stop."""
    shape = scope.Run9Shape(["putInt+putLong", "3 bytes+putLong", "3 bytes+putLong, body guard", dict(BUDGET_STOP)], _always_the_owner)
    legacy = await _legacy(tmp_path, shape)

    assert shape.roles()[-1] == "s3:owner_recovery" and shape.roles().count("s3:owner_recovery") == 3
    assert legacy["failure_type"] == "generation_budget_exhausted" == legacy["failure_category"]
    assert legacy["terminal_cause"] == CAUSE
    assert "recovery" not in legacy and "plan_scope_conflict" not in legacy and not legacy.get("reason_codes")   # not a plan-repair terminal
    assert legacy["status"] == "needs_review"
    assert [(r["subtask_id"], r["status"], r["reason_codes"]) for r in legacy["subtask_results"]] == [
        ("s3", "needs_review", ["PLAN_RECOVERY_OWNER_FAILED"]), ("s5", "needs_review", ["PLAN_SCOPE_REVISION_REQUIRED"])]
    assert len(legacy["plan_recovery_events"]) == 3


@pytest.mark.asyncio
async def test_a_recovery_that_exhausts_its_cap_without_a_typed_stop_is_still_the_plan_repair_terminal(tmp_path):
    """Negative control: nothing typed ended this run - the conflict was simply never resolved - so the terminal is the plan-repair one."""
    shape = scope.Run9Shape(list(r7.NEVER_FIXED), _always_the_owner)
    legacy = await _legacy(tmp_path, shape)

    assert shape.roles().count("s3:owner_recovery") == 3 and shape.roles()[-1] == "s5:verifier_replay"
    assert legacy["failure_type"] == "PLANNING_ERROR" and legacy["recovery"] == "PLAN_REPAIR"
    assert legacy["reason_codes"] == ["PLAN_SCOPE_REVISION_REQUIRED"] and "terminal_cause" not in legacy


@pytest.mark.asyncio
async def test_an_owner_that_fails_its_recovery_without_a_typed_stop_claims_no_cause(tmp_path):
    """Negative control: the reopened owner's last invocation merely failed its gates (a category, no stop reason)."""
    shape = scope.Run9Shape(["putInt+putLong", "three bytes + four bytes", dict(ORDINARY_OWNER_FAILURE)], _run9_verdict)
    legacy = await _legacy(tmp_path, shape)

    assert shape.roles()[-1] == "s3:owner_recovery"
    assert "terminal_cause" not in legacy and legacy.get("failure_type") != "quality_gates_exhausted"
    assert legacy["status"] == "needs_review"


@pytest.mark.asyncio
async def test_a_typed_stop_of_an_earlier_owner_invocation_is_not_the_cause_once_another_invocation_followed(tmp_path):
    """The cause is the typed stop of the run's LAST invocation. Here the reopened owner reports a typed stop AND a local pass (a shape the
    unit does not produce, used only to let the run go on): the consumer is verified afterwards, passes, and the run is a success with no
    cause left over from the earlier invocation."""
    passed_with_a_stale_stop = {"status": "success", "quality_gates_passed": True, "files": [scope.PARSER],
                                "verification_results": [r7.COMPILE_EVIDENCE], "failure_category": "generation_budget_exhausted",
                                "environment_failure": BUDGET_REASON,
                                "gate_outcomes": [{"type": "compile", "success": True, "attempt": 1}, dict(r7.ZERO_TESTS)]}

    class Shape(scope.Run9Shape):
        async def __call__(self, **kwargs):
            if kwargs["current_subtask_id"] == "s3" and self.owner_answers and isinstance(self.owner_answers[0], dict):
                (Path(kwargs["workspace_path"]) / scope.PARSER).write_text("three bytes + four bytes")
            return await super().__call__(**kwargs)

    shape = Shape(["putInt+putLong", dict(passed_with_a_stale_stop)], scope._fixed_after_one_repair)
    legacy = await _legacy(tmp_path, shape)

    assert shape.roles() == ["s3:planned", "s5:planned", "s3:owner_recovery", "s5:verifier_replay"]
    assert legacy["status"] == "success"
    assert "terminal_cause" not in legacy and "failure_type" not in legacy


@pytest.mark.asyncio
async def test_run7_shape_an_ordinary_failure_of_the_last_owner_invocation_leaves_the_plan_repair_terminal(tmp_path):
    """Negative control: the last invocation is the reopened owner, but it merely failed its gates. Only a TYPED stop outranks the conflict."""
    shape = scope.Run9Shape(["putInt+putLong", "3 bytes+putLong", "3 bytes+putLong, body guard", dict(ORDINARY_OWNER_FAILURE)], _always_the_owner)
    legacy = await _legacy(tmp_path, shape)

    assert shape.roles()[-1] == "s3:owner_recovery" and shape.roles().count("s3:owner_recovery") == 3
    assert legacy["failure_type"] == "PLANNING_ERROR" and legacy["recovery"] == "PLAN_REPAIR"
    assert legacy["reason_codes"] == ["PLAN_SCOPE_REVISION_REQUIRED"] and "terminal_cause" not in legacy


@pytest.mark.asyncio
async def test_a_typed_stop_of_the_consumers_own_last_invocation_never_outranks_its_own_scope_conflict(tmp_path):
    """Negative control (the R8-C rule, at the cap): the run's LAST invocation is the consumer's replay; its result carries a typed stop AND its
    own plan-scope conflict. Only an OWNER invocation inside the recovery is a nested stop: the consumer's own conflict keeps the terminal."""
    class Shape(scope.Run9Shape):
        async def __call__(self, **kwargs):
            result = await super().__call__(**kwargs)
            if self.roles().count("s5:verifier_replay") == 3 and kwargs["current_subtask_id"] == "s5":
                result.update(failure_category="containment_setup_failed", environment_failure="CONTAINMENT_SETUP_FAILED: stopped")
            return result

    shape = Shape(list(r7.NEVER_FIXED), _always_the_owner)
    legacy = await _legacy(tmp_path, shape)

    assert shape.roles()[-1] == "s5:verifier_replay" and shape.roles().count("s5:verifier_replay") == 3
    assert legacy["failure_type"] == "PLANNING_ERROR" and legacy["recovery"] == "PLAN_REPAIR" and "terminal_cause" not in legacy

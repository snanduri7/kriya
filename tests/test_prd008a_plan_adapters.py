"""PRD-008A A2/A3: each user-facing mode adapts into the canonical ExecutionPlan."""
import hashlib

import pytest

from kriya.workflow.execution_plan import PlanSourceKind, WorkUnitRole
from kriya.workflow.plan_adapters import (
    DIRECT_WORK_UNIT_ID,
    direct_execution_plan,
    work_unit_record,
)

GOAL = "Add a subtract(a, b) function to calc.py"


# --- A2: direct ----------------------------------------------------------------

def test_a_direct_goal_is_exactly_one_work_unit_with_the_goal_verbatim():
    plan = direct_execution_plan(GOAL, plan_id="run-1")
    assert plan.source_kind is PlanSourceKind.DIRECT
    [unit] = plan.work_units
    assert unit.id == DIRECT_WORK_UNIT_ID
    assert unit.goal == GOAL
    assert unit.role is WorkUnitRole.PRIMARY
    assert unit.depends_on == () and unit.acceptance_criteria == ()
    assert plan.terminal_phases == ()


def test_the_direct_unit_digest_is_the_goal_hash():
    [unit] = direct_execution_plan(GOAL, plan_id="run-1").work_units
    assert unit.definition_digest == hashlib.sha256(GOAL.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("goal", ["", "   "])
def test_a_blank_direct_goal_cannot_become_a_plan(goal):
    from kriya.workflow.execution_plan import InvalidExecutionPlanError

    with pytest.raises(InvalidExecutionPlanError):
        direct_execution_plan(goal, plan_id="run-1")


def test_the_same_goal_is_the_same_plan_whatever_the_run():
    assert direct_execution_plan(GOAL, plan_id="a").fingerprint == direct_execution_plan(GOAL, plan_id="b").fingerprint
    assert direct_execution_plan(GOAL, plan_id="a").fingerprint != direct_execution_plan(GOAL + ".", plan_id="a").fingerprint


def test_the_direct_record_is_stable_across_runs_and_goals():
    a = work_unit_record(direct_execution_plan(GOAL, plan_id="a"), DIRECT_WORK_UNIT_ID)
    b = work_unit_record(direct_execution_plan("another goal", plan_id="b"), DIRECT_WORK_UNIT_ID)
    assert a == b == {
        "kind": "direct", "group_id": None, "milestone_id": None,
        "definition_digest": None, "work_unit_id": DIRECT_WORK_UNIT_ID,
    }

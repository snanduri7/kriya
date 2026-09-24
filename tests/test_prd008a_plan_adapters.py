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


# --- A3: milestone ------------------------------------------------------------------

from kriya.agents.contracts import AcceptanceCriterion, MilestoneMode, MilestoneV2, ProvidedCapability  # noqa: E402
from kriya.workflow.execution_plan import InvalidExecutionPlanError, TerminalPhase  # noqa: E402
from kriya.workflow.milestone_completion import (  # noqa: E402
    integration_work_unit,
    milestone_definition_digest,
    milestone_work_unit,
)
from kriya.workflow.milestone_validation import topological_order  # noqa: E402
from kriya.workflow.milestones import MilestoneRunState, _plan_digest, build_integration_goal_text  # noqa: E402
from kriya.workflow.plan_adapters import INTEGRATION_WORK_UNIT_ID, milestone_execution_plan  # noqa: E402


def _m(mid, depends_on=(), **kwargs):
    return MilestoneV2(
        id=mid, goal=f"build {mid}", depends_on=list(depends_on),
        acceptance=[AcceptanceCriterion(id=f"{mid}-A1", description=f"{mid} works")], **kwargs,
    )


def _state(milestones, group_id="G1"):
    return MilestoneRunState(group_id=group_id, original_goal="build the app", milestones=list(milestones))


def _dag():
    return [
        _m("M1", provides=[ProvidedCapability(name="store", description="kv store")]),
        _m("M3", ["M2"], mode=MilestoneMode.EXTENSION, extends="M2", entrypoint="app.py"),
        _m("M2", ["M1"], consumes=["store"], mode=MilestoneMode.COMPOSITION, adds_dependencies=["click"]),
        _m("M4"),
    ]


def test_a_milestone_plan_is_one_unit_per_milestone_plus_integration():
    plan = milestone_execution_plan(_state(_dag()))
    assert plan.source_kind is PlanSourceKind.MILESTONE
    assert plan.plan_id == "G1"
    assert [u.id for u in plan.work_units] == ["M1", "M3", "M2", "M4", INTEGRATION_WORK_UNIT_ID]
    assert plan.terminal_phases == (TerminalPhase.REPLAY_PRIOR_VERIFICATIONS,)
    integration = plan.unit(INTEGRATION_WORK_UNIT_ID)
    assert integration.role is WorkUnitRole.INTEGRATION
    assert set(integration.depends_on) == {"M1", "M2", "M3", "M4"}


def test_execution_order_is_run_milestones_order_then_integration():
    milestones = _dag()
    plan = milestone_execution_plan(_state(milestones))
    expected = [m.id for m in topological_order(milestones)] + [INTEGRATION_WORK_UNIT_ID]
    assert [u.id for u in plan.execution_order()] == expected


def test_milestone_semantics_are_preserved_losslessly():
    milestones = _dag()
    plan = milestone_execution_plan(_state(milestones))
    for milestone in milestones:
        unit = plan.unit(milestone.id)
        assert unit.goal == milestone.goal
        assert unit.depends_on == tuple(milestone.depends_on)
        assert unit.acceptance_criteria[0].id == milestone.acceptance[0].id
        assert unit.acceptance_criteria[0].description == milestone.acceptance[0].description
        assert unit.provides == tuple(c.name for c in milestone.provides)
        assert unit.consumes == tuple(milestone.consumes)
        assert MilestoneV2(**unit.provenance_dict()["milestone"]) == milestone


def test_unit_identity_records_are_byte_compatible_with_existing_checkpoints_and_proofs():
    milestones = _dag()
    plan = milestone_execution_plan(_state(milestones))
    for milestone in milestones:
        assert plan.unit(milestone.id).definition_digest == milestone_definition_digest(milestone)
        assert work_unit_record(plan, milestone.id) == milestone_work_unit("G1", milestone)
    assert work_unit_record(plan, INTEGRATION_WORK_UNIT_ID) == integration_work_unit(
        "G1", _plan_digest(topological_order(milestones)),
    )


def test_the_integration_unit_goal_is_the_existing_integration_goal_text():
    milestones = _dag()
    plan = milestone_execution_plan(_state(milestones))
    assert plan.unit(INTEGRATION_WORK_UNIT_ID).goal == build_integration_goal_text(
        "build the app", topological_order(milestones),
    )


def test_a_legacy_v1_plan_file_still_adapts():
    state = MilestoneRunState.from_dict({
        "group_id": "G-legacy", "original_goal": "legacy goal",
        "milestones": [
            {"goal": "first", "success_criterion": "runs"},
            {"goal": "second", "success_criterion": "still runs"},
        ],
        "completed_milestone_indices": [1],
    })
    plan = milestone_execution_plan(state)
    assert [u.id for u in plan.execution_order()] == ["M1", "M2", INTEGRATION_WORK_UNIT_ID]
    assert plan.unit("M2").depends_on == ("M1",)


@pytest.mark.parametrize("milestones", [
    [_m("M1", ["M2"]), _m("M2", ["M1"])],
    [_m("M1"), _m("M2", ["M9"])],
])
def test_a_hand_edited_plan_that_would_silently_drop_milestones_is_refused(milestones):
    # Today's executor order silently loses these milestones:
    assert len(topological_order(milestones)) < len(milestones) or any(
        dep not in {m.id for m in milestones} for m in milestones for dep in m.depends_on
    )
    with pytest.raises(InvalidExecutionPlanError):
        milestone_execution_plan(_state(milestones))


def test_a_changed_milestone_changes_its_own_digest_but_not_an_unrelated_ones():
    before = milestone_execution_plan(_state(_dag()))
    edited = _dag()
    edited[3] = MilestoneV2(
        id="M4", goal="build M4 differently",
        acceptance=[AcceptanceCriterion(id="M4-A1", description="M4 works")],
    )
    after = milestone_execution_plan(_state(edited))
    assert before.fingerprint != after.fingerprint
    assert before.unit("M4").definition_digest != after.unit("M4").definition_digest
    for mid in ("M1", "M2", "M3"):
        assert before.work_unit_identity(mid)["definition_digest"] == after.work_unit_identity(mid)["definition_digest"]

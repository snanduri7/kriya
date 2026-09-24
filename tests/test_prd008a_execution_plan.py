"""PRD-008A A1: the ExecutionPlan / WorkUnit contracts.

Contracts only - no runtime routing. Covers serialization, the deterministic
fingerprint, every validation reason code, stable topological ordering (and
its parity with the milestone executor's own order) and the separation of
runtime lifecycle from definition identity.
"""
import dataclasses

import pytest

from kriya.agents.contracts import AcceptanceCriterion, MilestoneV2
from kriya.workflow.execution_plan import (
    DEPENDENCY_CYCLE,
    DIRECT_PLAN_UNIT_COUNT,
    DUPLICATE_WORK_UNIT_ID,
    EMPTY_PLAN,
    INTEGRATION_UNIT_DEPENDENCIES,
    INVALID_WORK_UNIT,
    MISSING_DEPENDENCY,
    PLAN_FINGERPRINT_MISMATCH,
    UNSUPPORTED_COMMIT_STRATEGY,
    UNSUPPORTED_PLAN_SCHEMA,
    CommitStrategy,
    ExecutionPlan,
    InvalidExecutionPlanError,
    PlanSourceKind,
    TerminalPhase,
    WorkUnit,
    WorkUnitRole,
    WorkUnitState,
    WorkUnitStatus,
)
from kriya.workflow.milestone_validation import topological_order


def _unit(uid, *, depends_on=(), goal=None, role=WorkUnitRole.PRIMARY, **kwargs):
    return WorkUnit.build(id=uid, goal=goal or f"do {uid}", depends_on=depends_on, role=role, **kwargs)


def _plan(*units, source_kind=PlanSourceKind.MILESTONE, plan_id="P1", **kwargs):
    return ExecutionPlan.build(plan_id=plan_id, source_kind=source_kind, work_units=units, **kwargs)


def _codes(excinfo):
    return set(excinfo.value.reason_codes)


def _chain():
    return _plan(_unit("W1"), _unit("W2", depends_on=["W1"]), _unit("W3", depends_on=["W2"]))


# --- serialization and fingerprint ------------------------------------------

def test_work_unit_round_trips_with_every_field():
    unit = _unit(
        "W1", acceptance_criteria=[("A1", "adds two numbers")], provides=["calc"], consumes=["io"],
        obligation_refs=["ledger:O1"], verification_requirements=["tests pass"],
        provenance={"mode": "extension", "extends": None, "adds_dependencies": ["x"]},
    )
    again = WorkUnit.from_dict(unit.to_dict())
    assert again == unit
    assert again.provenance_dict() == {"mode": "extension", "extends": None, "adds_dependencies": ["x"]}


def test_plan_round_trips_and_keeps_its_fingerprint():
    plan = _plan(
        _unit("W1"), _unit("INT", depends_on=["W1"], role=WorkUnitRole.INTEGRATION),
        terminal_phases=[TerminalPhase.REPLAY_PRIOR_VERIFICATIONS], provenance={"plan_file": "plan.json"},
    )
    again = ExecutionPlan.from_dict(plan.to_dict())
    assert again == plan
    assert again.fingerprint == plan.fingerprint
    assert again.provenance_dict() == {"plan_file": "plan.json"}


def test_a_tampered_definition_is_refused_not_refingerprinted():
    data = _chain().to_dict()
    data["work_units"][1]["goal"] = "something else"
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        ExecutionPlan.from_dict(data)
    assert _codes(excinfo) == {PLAN_FINGERPRINT_MISMATCH}


def test_an_unknown_schema_is_refused():
    data = _chain().to_dict()
    data["schema"] = 99
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        ExecutionPlan.from_dict(data)
    assert _codes(excinfo) == {UNSUPPORTED_PLAN_SCHEMA}


def test_fingerprint_is_deterministic_and_ignores_plan_id_and_provenance():
    a = _chain()
    b = _plan(
        _unit("W1"), _unit("W2", depends_on=["W1"]), _unit("W3", depends_on=["W2"]),
        plan_id="another-run", provenance={"plan_file": "moved.json"},
    )
    assert a.fingerprint == b.fingerprint
    assert len(a.fingerprint) == 64


@pytest.mark.parametrize("change", [
    lambda: _plan(_unit("W1", goal="changed"), _unit("W2", depends_on=["W1"]), _unit("W3", depends_on=["W2"])),
    lambda: _plan(_unit("W1", acceptance_criteria=[("A1", "new")]), _unit("W2", depends_on=["W1"]), _unit("W3", depends_on=["W2"])),
    lambda: _plan(_unit("W1"), _unit("W2"), _unit("W3", depends_on=["W2"])),
    lambda: _plan(_unit("W1"), _unit("W3", depends_on=["W2"]), _unit("W2", depends_on=["W1"])),
    lambda: _plan(_unit("W1"), _unit("W2", depends_on=["W1"]), _unit("W4", depends_on=["W2"])),
])
def test_goal_criterion_dependency_order_and_identity_changes_change_the_fingerprint(change):
    assert change().fingerprint != _chain().fingerprint


def test_runtime_state_is_not_part_of_any_identity():
    plan = _chain()
    fields = {f.name for f in dataclasses.fields(ExecutionPlan)} | {f.name for f in dataclasses.fields(WorkUnit)}
    assert not fields & {"status", "state", "lifecycle"}
    state = WorkUnitState("W3", WorkUnitStatus.BLOCKED, ("DEPENDENCY_FAILED",), ("W2",))
    assert WorkUnitState.from_dict(state.to_dict()) == state
    assert plan.fingerprint == _chain().fingerprint


def test_work_units_are_immutable_however_constructed():
    unit = WorkUnit(id="W1", goal="g", definition_digest="d", depends_on=["W0"], provides=["x"])
    assert unit.depends_on == ("W0",) and unit.provides == ("x",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        unit.goal = "other"


def test_an_adapter_supplied_digest_is_kept_verbatim():
    assert _unit("W1", definition_digest="legacy-digest").definition_digest == "legacy-digest"
    assert _unit("W1").definition_digest == _unit("W1").definition_digest != _unit("W1", goal="x").definition_digest


# --- validation ----------------------------------------------------------------

def test_an_empty_plan_is_invalid():
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        _plan()
    assert _codes(excinfo) == {EMPTY_PLAN}


def test_duplicate_ids_are_invalid():
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        _plan(_unit("W1"), _unit("W1", goal="again"))
    assert _codes(excinfo) == {DUPLICATE_WORK_UNIT_ID}


def test_a_missing_dependency_is_invalid():
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        _plan(_unit("W1", depends_on=["W0"]))
    assert _codes(excinfo) == {MISSING_DEPENDENCY}
    assert excinfo.value.issues[0].work_unit_id == "W1"


@pytest.mark.parametrize("units", [
    (_unit("W1", depends_on=["W1"]),),
    (_unit("W1", depends_on=["W3"]), _unit("W2", depends_on=["W1"]), _unit("W3", depends_on=["W2"])),
])
def test_dependency_cycles_are_invalid(units):
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        _plan(*units)
    assert _codes(excinfo) == {DEPENDENCY_CYCLE}


def test_blank_units_are_invalid():
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        _plan(WorkUnit(id="W1", goal="  ", definition_digest="d"))
    assert _codes(excinfo) == {INVALID_WORK_UNIT}


def test_a_direct_plan_has_exactly_one_unit():
    assert len(_plan(_unit("W1"), source_kind=PlanSourceKind.DIRECT).work_units) == 1
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        _plan(_unit("W1"), _unit("W2"), source_kind=PlanSourceKind.DIRECT)
    assert _codes(excinfo) == {DIRECT_PLAN_UNIT_COUNT}


def test_atomic_plan_commits_fail_closed_as_unsupported():
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        _plan(_unit("W1"), commit_strategy=CommitStrategy.ATOMIC_PLAN)
    assert _codes(excinfo) == {UNSUPPORTED_COMMIT_STRATEGY}


def test_an_integration_unit_must_depend_on_every_primary_unit():
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        _plan(_unit("W1"), _unit("W2"), _unit("INT", depends_on=["W1"], role=WorkUnitRole.INTEGRATION))
    assert _codes(excinfo) == {INTEGRATION_UNIT_DEPENDENCIES}


def test_every_issue_is_reported_together():
    with pytest.raises(InvalidExecutionPlanError) as excinfo:
        _plan(_unit("W1", depends_on=["nope"]), _unit("W2"), source_kind=PlanSourceKind.DIRECT,
              commit_strategy=CommitStrategy.ATOMIC_PLAN)
    assert _codes(excinfo) == {MISSING_DEPENDENCY, DIRECT_PLAN_UNIT_COUNT, UNSUPPORTED_COMMIT_STRATEGY}


# --- ordering and navigation ------------------------------------------------------

def test_topological_order_breaks_ties_by_declared_position():
    plan = _plan(_unit("C"), _unit("A", depends_on=["B"]), _unit("B"), _unit("D", depends_on=["C"]))
    assert [u.id for u in plan.execution_order()] == ["C", "B", "A", "D"]


def test_order_matches_the_milestone_executors_own_order():
    milestones = [
        MilestoneV2(id=mid, goal=f"g {mid}", depends_on=deps,
                    acceptance=[AcceptanceCriterion(id=f"{mid}-A", description="works")])
        for mid, deps in [("M3", ["M1"]), ("M1", []), ("M4", []), ("M2", ["M1", "M4"]), ("M5", ["M2"])]
    ]
    plan = _plan(*(_unit(m.id, depends_on=m.depends_on) for m in milestones))
    assert [u.id for u in plan.execution_order()] == [m.id for m in topological_order(milestones)]


def test_ancestors_and_descendants():
    plan = _plan(_unit("W1"), _unit("W2", depends_on=["W1"]), _unit("W3", depends_on=["W2"]), _unit("X"))
    assert plan.ancestors("W3") == ("W1", "W2")
    assert plan.descendants("W1") == ("W2", "W3")
    assert plan.ancestors("X") == () and plan.descendants("X") == ()


def test_work_unit_identity_carries_plan_unit_and_upstream_digests():
    plan = _chain()
    identity = plan.work_unit_identity("W3")
    assert identity["plan_id"] == "P1" and identity["work_unit_id"] == "W3"
    assert identity["plan_fingerprint"] == plan.fingerprint
    assert identity["definition_digest"] == plan.unit("W3").definition_digest
    assert identity["ancestor_digests"] == [plan.unit("W1").definition_digest, plan.unit("W2").definition_digest]


def test_an_unrelated_new_unit_changes_the_plan_but_not_an_existing_units_identity():
    before = _chain()
    after = _plan(_unit("W1"), _unit("W2", depends_on=["W1"]), _unit("W3", depends_on=["W2"]), _unit("W4"))
    assert before.fingerprint != after.fingerprint
    for uid in ("W1", "W2", "W3"):
        a, b = before.work_unit_identity(uid), after.work_unit_identity(uid)
        assert a["definition_digest"] == b["definition_digest"]
        assert a["ancestor_digests"] == b["ancestor_digests"]


def test_an_upstream_change_changes_downstream_identity():
    before = _chain()
    after = _plan(_unit("W1", goal="changed"), _unit("W2", depends_on=["W1"]), _unit("W3", depends_on=["W2"]))
    assert before.work_unit_identity("W3")["ancestor_digests"] != after.work_unit_identity("W3")["ancestor_digests"]
    assert before.unit("W3").definition_digest == after.unit("W3").definition_digest

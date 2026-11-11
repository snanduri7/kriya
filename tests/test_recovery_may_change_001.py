"""RECOVERY-SHARED-DECISION-PARTICIPANTS-CANNOT-MAY-CHANGE-001 (owner rulings 2026-11-10): the MAY_CHANGE recovery
disposition.

A failure that manifests in an artifact of a recorded cross-subtask DECISION (an IntegrationRelationship with
``alternatives`` and a ``selected_alternative``) used to reopen only the owner the locator named, MUST_CHANGE - the
wrong owner whenever the other participant is the one that deviates from the selection. The plan does not say which
participant deviates, so every structurally eligible participant is now reopened MAY_CHANGE: with the failure
evidence and the selected alternative, free to return its artifact unchanged.

The enforce loop is the real one; each unit (``run_generation_workflow``) is scripted, so these are deterministic
planning/recovery controls - they prove what the recovery does with a given Developer outcome, never which outcome a
live model produces. The outcome shapes are the ones measured in tests/test_recovery_may_change_characterization.py.
"""
import ast
import logging
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import strict_config, strict_kernel

import kriya
from kriya.control.persistence import load_control_state
from kriya.control.state import ControlState
from kriya.workflow import enforce_loop, owner_recovery, recovery_plan, workflow_controller
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.recovery_plan import (
    RECOVERY_MAY_CHANGE_ALL_UNCHANGED,
    RELATIONSHIP_ARTIFACT_HANDLED_BY_CONSUMER_RETRY,
    RELATIONSHIP_ARTIFACT_OWNER_NOT_COMPLETED,
    RecoveryAction,
    RecoveryParticipantRole,
)
from kriya.workflow.retry_progress import VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
from kriya.workflow.self_correction import SelfCorrectionResult
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow_controller import WorkflowController, build_recovery_execution_plan
from kriya.workflow.workflow_types import SubtaskStatus

MAY = "may_change_owner_recovery"
MUST = "owner_recovery"
EVIDENCE = "E   RuntimeError: connector 'alpha' is not configured"
VERIFY_DESCRIPTION = "run the application and confirm the value is printed"
UNCHANGED = {"status": "failed", "quality_gates_passed": False, "files": [],
             "no_progress_reason": VERIFICATION_RETRY_NO_CHANGE_POSSIBLE}


@pytest.fixture(autouse=True)
def _plan_worktree_is_the_workspace(monkeypatch):
    monkeypatch.setattr("kriya.workflow.workflow_controller.create_git_worktree", lambda workspace: workspace)


def _unit(uid, files=(), depends_on=(), *, verification=False, action="create"):
    unit = {"id": uid, "description": uid, "execution_method": "model", "depends_on": list(depends_on),
            "planned_files": [{"path": path, "action": action} for path in files]}
    if verification:
        unit["execution_role"] = "verification"
        unit["verification"] = [{"type": "judgment", "description": VERIFY_DESCRIPTION,
                                 "verifier_kind": "application_runtime"}]
    return unit


def _decision(rel_id="r1", producers=("p",), consumers=("c",), artifacts=(), **overrides):
    rel = {"id": rel_id, "kind": "configures", "producer_subtask_ids": list(producers),
           "consumer_subtask_ids": list(consumers), "participating_artifacts": list(artifacts),
           "relationship_statement": "the connector is defined once and used by the application",
           "alternatives": ["declarative", "programmatic"], "selected_alternative": "declarative"}
    rel.update(overrides)
    return rel


def _plan(units=None, relationships=None):
    """base s1 -> producer p (prov.cfg) -> consumer c (cons.py) -> verification v; d depends on p only (a dependent
    outside the decision). One decision relationship r1 between p and c unless ``relationships`` says otherwise."""
    units = units or [
        _unit("s1", ["base.cfg"]), _unit("p", ["prov.cfg"], ["s1"]), _unit("d", ["dep.py"], ["p"]),
        _unit("c", ["cons.py"], ["s1", "p"]), _unit("v", [], ["c"], verification=True),
    ]
    return EngineeringPlan.model_validate({
        "plan_id": "run1", "kind": "task", "subtasks": units,
        "integration_relationships": [_decision()] if relationships is None else relationships,
    })


def _conflict(files, failure_type="run_verification"):
    return {"reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": failure_type,
            "classification": "PLAN_SCOPE_DEFECT", "attribution_tier": "locator",
            "reason": "the application failed at start-up", "raw_evidence": EVIDENCE,
            "required_files": list(files), "allowed_files": []}


def _engine(kernel=None):
    engine = MagicMock()
    engine.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT))
    engine.planner.run = AsyncMock(return_value="fake plan text")
    engine.kernel = kernel
    if kernel is not None:
        engine.developer.llm = MagicMock()
    return engine


async def _execute(tmp_path, plan, *, conflicts, recovery, kernel=None):
    """Run the enforce loop over ``plan`` with every unit scripted.

    ``conflicts``: failing unit id -> the ``required_files`` of each of its successive failing calls (it passes once
    the list is used up). ``recovery``: owner id -> its successive reopen outcomes: ``"changed"`` (rewrites its first
    planned file), ``("changed", [paths])``, ``"unchanged"`` (the characterized typed stop), ``("off_target", path)``
    (gates pass, only that file written), ``"gate_fail"`` (nothing retained), ``"undeclared"``."""
    conflicts = {uid: list(items) for uid, items in conflicts.items()}
    recovery = {uid: list(items) for uid, items in recovery.items()}
    calls = []

    async def fake_run(**kwargs):
        uid = kwargs["current_subtask_id"]
        role = kwargs["execution_scope"].split("role=")[1]
        try:
            states = dict(load_control_state(str(tmp_path)).subtask_states)
        except Exception:  # noqa: BLE001 - no control state persisted yet (the first unit)
            states = {}
        calls.append({"unit": uid, "role": role, "kwargs": kwargs, "states": states})
        serial = len(calls)
        planned = list(kwargs["allowed_write_relpaths"])

        def write(paths):
            for path in paths:
                (tmp_path / path).write_text(f"{uid} wrote {path} at call {serial}\n")
            return {"status": "success", "quality_gates_passed": True, "files": list(paths)}

        if role in (MAY, MUST):
            outcome = recovery[uid].pop(0)
            if outcome == "unchanged":
                return dict(UNCHANGED)
            if outcome == "changed":
                return write(planned[:1])
            if outcome == "gate_fail":
                return {"status": "failed", "quality_gates_passed": False, "files": []}
            if outcome == "identical":  # gates pass, the file is reported written, its bytes are what they were
                return {"status": "success", "quality_gates_passed": True, "files": planned[:1]}
            if outcome == "stop_but_changed":  # the typed stop's shape, yet the target's bytes are different
                write(planned[:1])
                return dict(UNCHANGED)
            if outcome == "undeclared":
                return {**write(planned[:1]), "files": planned[:1] + ["somewhere/else.txt"]}
            kind, value = outcome
            return write(value if kind == "changed" else [value])
        if role == "dependent_reverification" and planned:
            # R4 (ENFORCE-RECOVERY-CONVERGENCE-001): a completed stage an accepted owner change invalidated is
            # re-entered; here it regenerates its artifacts byte-identical (the unit's typed stop, gates passed).
            return {**UNCHANGED, "candidate_gates_passed": True}
        if conflicts.get(uid):
            return {"status": "failed", "quality_gates_passed": False, "files": [],
                    "plan_scope_conflict": _conflict(conflicts[uid].pop(0))}
        if not planned:
            return {"status": "success", "quality_gates_passed": True, "files": [],
                    "verification_results": [{"type": "judgment", "tool_name": None, "passed": True,
                                              "description": VERIFY_DESCRIPTION, "source": "run_verification"}]}
        return write(planned)

    engine = _engine(kernel)
    engine.run_generation_workflow = fake_run
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
         patch("kriya.workflow.workflow_controller.validate_plan",
               new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        result = await WorkflowController(engine).execute(
            "Run the application and check its output.", str(tmp_path), migration_mode="enforce")
    return result, calls


def _reopens(calls):
    return [(c["unit"], c["role"]) for c in calls if c["role"] in (MAY, MUST, "consumer_retry")]


def _events(result):
    return {e["reopened_owner"]: e for e in result.legacy_result.get("plan_recovery_events") or []}


# --- Controls 1-5: who is reopened, and what an unchanged owner means ---

@pytest.mark.asyncio
async def test_c01_provider_deviates_the_provider_changes_and_the_located_consumer_stays_unchanged(tmp_path):
    """The failure is located in the consumer's artifact, but the PROVIDER is the one that deviates from the
    selection: it changes, the located owner legitimately returns its artifact unchanged, the verification passes."""
    result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                   recovery={"p": ["changed"], "c": ["unchanged"]})
    assert result.legacy_result["status"] == "success"
    assert _reopens(calls) == [("p", MAY), ("c", MAY), ("v", "consumer_retry")]
    events = _events(result)
    assert (events["p"]["may_change_outcome"], events["c"]["may_change_outcome"]) == ("changed", "unchanged")
    assert events["p"]["owner_recovery_passed"] is True and events["c"]["owner_recovery_passed"] is True
    assert events["c"]["recovery_action"] == "may_change" and events["c"]["reason_codes"] == []
    assert "call 6" in (tmp_path / "prov.cfg").read_text()  # the provider's reopen, the sixth unit call
    assert "call 3" in (tmp_path / "cons.py").read_text()  # the consumer's planned bytes, untouched


@pytest.mark.asyncio
async def test_c02_c03_consumer_deviates_the_first_owner_is_unchanged_and_the_second_changes(tmp_path):
    """An unchanged MAY_CHANGE owner is not progress and not the end of the recovery: the next group still runs."""
    result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                   recovery={"p": ["unchanged"], "c": ["changed"]})
    assert result.legacy_result["status"] == "success"
    assert _reopens(calls) == [("p", MAY), ("c", MAY), ("v", "consumer_retry")]
    events = _events(result)
    assert (events["p"]["may_change_outcome"], events["c"]["may_change_outcome"]) == ("unchanged", "changed")
    assert "call 2" in (tmp_path / "prov.cfg").read_text() and "call 7" in (tmp_path / "cons.py").read_text()


@pytest.mark.asyncio
async def test_c04_both_participants_change(tmp_path):
    result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                   recovery={"p": ["changed"], "c": ["changed"]})
    assert result.legacy_result["status"] == "success"
    assert _reopens(calls) == [("p", MAY), ("c", MAY), ("v", "consumer_retry")]


@pytest.mark.asyncio
async def test_c05_every_participant_unchanged_is_a_typed_stop_never_a_success(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="kriya.workflow.workflow_controller"):
        result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                       recovery={"p": ["unchanged"], "c": ["unchanged"]})
    assert result.legacy_result["status"] != "success"
    assert _reopens(calls) == [("p", MAY), ("c", MAY)]  # both asked; the consumer is NOT retried on nothing new
    events = _events(result)
    for owner in ("p", "c"):
        assert events[owner]["owner_recovery_passed"] is False
        assert events[owner]["reason_codes"] == [RECOVERY_MAY_CHANGE_ALL_UNCHANGED]
    by_id = {r.subtask_id: r for r in result.subtask_results}
    assert by_id["p"].status == SubtaskStatus.NEEDS_REVIEW and by_id["c"].status == SubtaskStatus.NEEDS_REVIEW
    assert by_id["p"].reason_codes == (RECOVERY_MAY_CHANGE_ALL_UNCHANGED,)
    assert by_id["v"].status != SubtaskStatus.COMPLETED
    assert any(RECOVERY_MAY_CHANGE_ALL_UNCHANGED in r.getMessage() for r in caplog.records)


# --- Control 6: a mixed group ---

def _mixed_plan():
    """The consumer owns two files; only cons.py is in the decision's declared scope."""
    return _plan(units=[
        _unit("s1", ["base.cfg"]), _unit("p", ["prov.cfg"], ["s1"]),
        _unit("c", ["cons.py", "extra.py"], ["s1", "p"]), _unit("v", [], ["c"], verification=True),
    ], relationships=[_decision(artifacts=["prov.cfg", "cons.py"])])


@pytest.mark.asyncio
async def test_c06_a_mixed_group_requires_a_change_only_of_its_must_change_file(tmp_path, caplog):
    """extra.py is located and outside the decision (MUST_CHANGE); cons.py is located and inside it (MAY_CHANGE). The
    owner changes extra.py and leaves cons.py alone: accepted."""
    with caplog.at_level(logging.INFO, logger="kriya.workflow.workflow_controller"):
        result, calls = await _execute(tmp_path, _mixed_plan(), conflicts={"v": [["cons.py", "extra.py"]]},
                                       recovery={"p": ["unchanged"], "c": [("changed", ["extra.py"])]})
    assert result.legacy_result["status"] == "success"
    assert _reopens(calls) == [("p", MAY), ("c", MUST), ("v", "consumer_retry")]  # MUST_CHANGE > MAY_CHANGE
    # one MUST-FIX obligation, whose identity names the mixed group's MUST_CHANGE file only (the id embeds the
    # obligation's files) - never a MAY_CHANGE artifact
    created = [r.getMessage() for r in caplog.records if "CROSS_OWNER_REQUIREMENT_CREATED" in r.getMessage()]
    assert len(created) == 1 and "requirement_id=recovery.v.c.extra.py.run_verification.0 " in created[0]
    (mixed,) = [c for c in calls if (c["unit"], c["role"]) == ("c", MUST)]
    context = mixed["kwargs"]["recovery_contract_block"]
    assert 'Required repair files: ["extra.py"]' in context  # the MUST-FIX names the MUST_CHANGE file only
    assert "INTEGRATION-DECISION RECOVERY" in context and "- cons.py: The failure manifested" in context
    assert mixed["kwargs"]["reopened_owner"] is False


@pytest.mark.asyncio
async def test_c06b_a_mixed_group_whose_must_change_file_is_unchanged_is_still_rejected(tmp_path):
    """MUST_CHANGE is not weakened by a MAY_CHANGE neighbour: changing only the MAY_CHANGE file is not the repair."""
    result, calls = await _execute(tmp_path, _mixed_plan(), conflicts={"v": [["cons.py", "extra.py"]]},
                                   recovery={"p": ["unchanged"], "c": [("changed", ["cons.py"])]})
    assert result.legacy_result["status"] != "success"
    assert _reopens(calls) == [("p", MAY), ("c", MUST)]
    assert _events(result)["c"]["owner_recovery_passed"] is False


# --- Controls 7-9: recurrence and the two existing dispositions ---

@pytest.mark.asyncio
async def test_c07_a_recurring_conflict_never_promotes_may_change(tmp_path):
    result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"], ["cons.py"]]},
                                   recovery={"p": ["unchanged", "changed"], "c": ["changed", "unchanged"]})
    assert result.legacy_result["status"] == "success"
    assert _reopens(calls) == [("p", MAY), ("c", MAY), ("v", "consumer_retry"),
                               ("p", MAY), ("c", MAY), ("v", "consumer_retry")]
    assert [c["kwargs"]["reopened_owner"] for c in calls if c["role"] == MAY] == [True] * 4


@pytest.mark.asyncio
async def test_c08_must_change_is_unchanged_an_identical_candidate_is_still_no_progress(tmp_path):
    """No decision in the plan: the located owner is MUST_CHANGE, reopened as before, and a candidate that leaves
    the file as it was is RECOVERY_NO_PROGRESS - the plan is rejected and the consumer is not retried."""
    plan = _plan(relationships=[])

    async def run(outcome):
        return await _execute(tmp_path, plan, conflicts={"v": [["cons.py"]]}, recovery={"c": [outcome]})

    result, calls = await run("identical")
    assert result.legacy_result["status"] != "success" and _reopens(calls) == [("c", MUST)]
    assert _events(result)["c"]["owner_recovery_passed"] is False
    assert calls[-1]["kwargs"]["reopened_owner"] is False
    assert "MUST FIX" in calls[-1]["kwargs"]["recovery_contract_block"]
    assert "recovery_action" not in _events(result)["c"]  # an existing event is not given the new keys


@pytest.mark.asyncio
async def test_c09_verify_is_unchanged_the_developer_is_not_called(tmp_path):
    """An ungrounded (attribution_rejected) conflict stays VERIFY even with a decision relationship in the plan: no
    owner is reopened at all, the consumer retry is the arbiter."""
    plan = _plan()
    participants = owner_recovery.derive_recovery_participants(
        plan, _conflict(["cons.py"], failure_type="attribution_rejected"), plan.subtask_by_id("v"))
    assert [(p.artifact, p.recovery_action) for p in participants] == [("cons.py", RecoveryAction.VERIFY)]


# --- Controls 10-14: what does NOT widen recovery ---

def _derive(plan, files, failed="v", completed=("s1", "p", "d", "c"), written=None, **conflict):
    written = written or {uid: [pf.path for pf in plan.subtask_by_id(uid).planned_files] for uid in completed}
    state = ControlState(schema_version=1, run_id="t").with_updates(
        subtask_states={uid: "completed" for uid in completed}, subtask_written_files=written)
    order = [st.id for st in plan.subtasks]
    return owner_recovery.derive_recovery_participants(
        plan, _conflict(files, **conflict), plan.subtask_by_id(failed), order=order, control_state=state)


def _summary(participants):
    return [(p.artifact, p.effective_owner_subtask_id, p.role.value, p.recovery_action.value, p.owner_resolution_basis)
            for p in participants]


_LOCATED_ONLY = [("cons.py", "c", "required_mutation", "must_change", "latest_successful_modifier")]


def test_c10_no_relationship_keeps_the_located_owner_must_change():
    assert _summary(_derive(_plan(relationships=[]), ["cons.py"])) == _LOCATED_ONLY


@pytest.mark.parametrize("fields", [
    {"alternatives": [], "selected_alternative": ""},          # an ordinary producer/consumer relationship
    {"alternatives": ["declarative", "programmatic"], "selected_alternative": ""},  # a menu without a selection
    {"alternatives": [], "selected_alternative": "declarative"},                    # a selection without a menu
])
def test_c11_a_relationship_that_records_no_decision_does_not_widen_recovery(fields):
    assert _summary(_derive(_plan(relationships=[_decision(**fields)]), ["cons.py"])) == _LOCATED_ONLY


def test_c12_a_decision_between_other_subtasks_does_not_widen_recovery():
    plan = _plan(relationships=[_decision(producers=("s1",), consumers=("d",))])
    assert _summary(_derive(plan, ["cons.py"])) == _LOCATED_ONLY


def test_c13_a_located_artifact_outside_the_declared_scope_does_not_widen_recovery():
    plan = _plan(relationships=[_decision(artifacts=["prov.cfg"])])  # the located cons.py is not a declared artifact
    assert _summary(_derive(plan, ["cons.py"])) == _LOCATED_ONLY


def test_c14_a_plan_written_before_decisions_existed_keeps_todays_behaviour():
    relationship = _decision()
    del relationship["alternatives"], relationship["selected_alternative"]  # the pre-4c70f83 relationship shape
    plan = _plan(relationships=[relationship])
    assert _summary(_derive(plan, ["cons.py"])) == _LOCATED_ONLY
    assert build_recovery_execution_plan(plan, _conflict(["cons.py"]), plan.subtask_by_id("v")).group_order == ("group.c",)


def test_the_eligible_shape_widens_to_both_participants_as_may_change():
    """The positive of controls 10-14 (they would pass vacuously if nothing ever widened)."""
    participants = _derive(_plan(), ["cons.py"])
    assert _summary(participants) == [
        ("cons.py", "c", "required_mutation", "may_change", "latest_successful_modifier"),
        ("prov.cfg", "p", "required_mutation", "may_change", "latest_successful_modifier"),
    ]
    assert all(p.evidence_ids == ("plan.integration.r1",) for p in participants)


# --- Controls 15-16: write scope and ordering ---

@pytest.mark.asyncio
async def test_c15_a_may_change_owner_keeps_its_bounded_planned_file_write_scope(tmp_path):
    _result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                    recovery={"p": ["changed"], "c": ["unchanged"]})
    reopened = {c["unit"]: c["kwargs"] for c in calls if c["role"] == MAY}
    assert reopened["p"]["allowed_write_relpaths"] == ["prov.cfg"]
    assert reopened["c"]["allowed_write_relpaths"] == ["cons.py"]
    assert all(k["write_scope_mode"] is None and k["reopened_owner"] is True for k in reopened.values())


def test_c16_groups_are_ordered_deterministically_and_several_decisions_are_a_sorted_union():
    """Two decisions name the same pair, listed in reverse id order, the conflict files in either order: the
    participants, their decision ids and the serial group order are the same."""
    relationships = [_decision("r2"), _decision("r1")]
    plan = _plan(relationships=relationships)
    plans = [build_recovery_execution_plan(plan, _conflict(files), plan.subtask_by_id("v"))
             for files in (["cons.py"], ["cons.py", "prov.cfg"], ["prov.cfg", "cons.py"])]
    assert {p.group_order for p in plans} == {("group.p", "group.c")}  # the provider first: a plan dependency
    for exec_plan in plans:
        assert {p.artifact: p.evidence_ids for p in exec_plan.participants} == {
            "cons.py": ("plan.integration.r1", "plan.integration.r2"),
            "prov.cfg": ("plan.integration.r1", "plan.integration.r2"),
        }
        assert all(p.recovery_action is RecoveryAction.MAY_CHANGE for p in exec_plan.participants)


# --- Additional controls from the pre-implementation trace ---

def _kernel():
    return strict_kernel(strict_config(autonomy={
        "mode": "guardrails", "self_correction_loop_enabled": True, "self_correction_loop_max_turns": 4,
        "spec_compliance_enabled": False,
    }))


@pytest.mark.asyncio
@pytest.mark.parametrize("relationships, expected_loop_calls, expected_reopens", [
    (None, 0, [("p", MAY), ("c", MAY), ("v", "consumer_retry")]),  # MAY_CHANGE: the loop is never entered
    ([], 1, [("c", MUST), ("v", "consumer_retry")]),               # MUST_CHANGE: the loop runs first, as before
])
async def test_the_self_correction_loop_is_bypassed_only_for_may_change(
    tmp_path, relationships, expected_loop_calls, expected_reopens,
):
    loop = AsyncMock(return_value=SelfCorrectionResult(
        resolved=False, turns_used=1, final_compile_output="", modified_files={}))
    with patch("kriya.workflow.self_correction.run_self_correction_loop", new=loop), \
         patch("kriya.tools.validate.PolymorphicValidator"):
        result, calls = await _execute(
            tmp_path, _plan(relationships=relationships), conflicts={"v": [["cons.py"]]},
            recovery={"p": ["unchanged"], "c": ["changed"]}, kernel=_kernel())
    assert result.legacy_result["status"] == "success"
    assert loop.await_count == expected_loop_calls
    assert _reopens(calls) == expected_reopens


@pytest.mark.asyncio
async def test_the_may_change_context_keeps_the_failure_evidence_and_states_each_owners_truthful_role(tmp_path):
    _result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                    recovery={"p": ["unchanged"], "c": ["changed"]})
    context = {c["unit"]: c["kwargs"]["recovery_contract_block"] for c in calls if c["role"] == MAY}
    for text in context.values():
        assert EVIDENCE in text and "the application failed at start-up" in text  # the failure itself
        assert 'decision r1: SELECTED "declarative"' in text and '["declarative", "programmatic"]' in text
        assert "MUST FIX" not in text and "return it unchanged" in text
    assert "- cons.py: The failure manifested in this owner's scope. Repair it if the failure evidence requires" in context["c"]
    assert "- prov.cfg: This artifact participates in the authoritative selected integration alternative." in context["p"]
    assert "The failure manifested" not in context["p"] and "This artifact participates" not in context["c"]


@pytest.mark.asyncio
@pytest.mark.parametrize("relationships, expected_created", [(None, 0), ([], 1)])
async def test_no_must_fix_obligation_is_created_for_a_may_change_owner(tmp_path, caplog, relationships, expected_created):
    """The cross-owner MUST-FIX obligation is recorded for the MUST_CHANGE owner of a plan without a decision (the
    positive) and for no MAY_CHANGE owner."""
    with caplog.at_level(logging.INFO, logger="kriya.workflow.workflow_controller"):
        result, _calls = await _execute(tmp_path, _plan(relationships=relationships), conflicts={"v": [["cons.py"]]},
                                        recovery={"p": ["changed"], "c": ["changed"]})
    assert result.legacy_result["status"] == "success"
    created = [r for r in caplog.records if "CROSS_OWNER_REQUIREMENT_CREATED" in r.getMessage()]
    assert len(created) == expected_created


@pytest.mark.asyncio
async def test_dependents_are_restored_after_an_unchanged_owner_and_stay_invalidated_after_a_changed_one(tmp_path):
    """d depends on the provider only. Snapshot, invalidate, then: restored when the provider is unchanged; when it
    changed, d stays invalidated until it is re-entered and reverified (R4, ENFORCE-RECOVERY-CONVERGENCE-001: this
    test used to pin d left ``pending`` under a successful run)."""
    _result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                    recovery={"p": ["unchanged"], "c": ["changed"]})
    by_role = {(c["unit"], c["role"]): c["states"] for c in calls}
    assert by_role[("p", MAY)]["d"] == "pending" and by_role[("p", MAY)]["c"] == "pending"  # invalidated first
    assert by_role[("c", MAY)]["d"] == "completed"  # restored once the provider came back unchanged
    assert by_role[("v", "consumer_retry")]["d"] == "completed"

    other = tmp_path / "second"
    other.mkdir()
    result, calls = await _execute(other, _plan(), conflicts={"v": [["cons.py"]]},
                                   recovery={"p": ["changed"], "c": ["unchanged"]})
    states_at_c = next(c["states"] for c in calls if (c["unit"], c["role"]) == ("c", MAY))
    assert states_at_c["d"] == "pending"  # the provider changed: its dependent stays invalidated
    assert [(c["unit"], c["role"]) for c in calls][-2:] == [("c", "dependent_reverification"),
                                                           ("d", "dependent_reverification")]   # canonical order
    assert all(c["states"]["d"] != "completed" for c in calls
               if (c["unit"], c["role"]) in (("c", MAY), ("v", "consumer_retry")))   # never completed while stale
    assert result.control_state.subtask_states["d"] == "completed" and result.legacy_result["status"] == "success"


def _two_file_provider_plan():
    """The provider also plans other.txt, which is outside the decision's declared scope."""
    return _plan(units=[
        _unit("s1", ["base.cfg"]), _unit("p", ["prov.cfg", "other.txt"], ["s1"]), _unit("d", ["dep.py"], ["p"]),
        _unit("c", ["cons.py"], ["s1", "p"]), _unit("v", [], ["c"], verification=True),
    ], relationships=[_decision(artifacts=["prov.cfg", "cons.py"])])


@pytest.mark.asyncio
async def test_an_off_target_write_is_a_group_failure_and_its_retained_bytes_keep_the_dependents_pending(tmp_path):
    """The MAY_CHANGE provider changes only its unrelated planned file (gates pass, the bytes are retained): not
    unchanged, not progress - the group fails, the next owner is not asked, the consumer is not retried, and the
    dependents stay pending because upstream bytes did change."""
    result, calls = await _execute(tmp_path, _two_file_provider_plan(), conflicts={"v": [["cons.py"]]},
                                   recovery={"p": [("off_target", "other.txt")], "c": ["changed"]})
    assert result.legacy_result["status"] != "success"
    assert _reopens(calls) == [("p", MAY)]
    event = _events(result)["p"]
    assert event["may_change_outcome"] == "failed" and event["owner_recovery_passed"] is False
    assert sorted(event["invalidated_subtasks"]) == ["c", "d", "v"]
    assert result.control_state.subtask_states["d"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["gate_fail", "undeclared"])
async def test_a_failed_may_change_owner_is_a_group_failure(tmp_path, outcome):
    """A gate failure that retained nothing restores the dependents; an undeclared write is a failure whose retained
    bytes keep them pending. Neither is 'unchanged' and neither lets the recovery continue."""
    result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                   recovery={"p": [outcome], "c": ["changed"]})
    assert result.legacy_result["status"] != "success"
    assert _reopens(calls) == [("p", MAY)]
    assert _events(result)["p"]["may_change_outcome"] == "failed"
    assert result.control_state.subtask_states["d"] == ("completed" if outcome == "gate_fail" else "pending")


@pytest.mark.asyncio
async def test_unchanged_requires_equal_target_fingerprints_whatever_the_result_says(tmp_path):
    """A result shaped like the typed stop while the target's bytes are different is NOT 'unchanged': fingerprint
    equality is mandatory. A group failure; the retained bytes keep the dependents pending."""
    result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                   recovery={"p": ["stop_but_changed"], "c": ["changed"]})
    assert result.legacy_result["status"] != "success"
    assert _reopens(calls) == [("p", MAY)]
    assert _events(result)["p"]["may_change_outcome"] == "failed"
    assert result.control_state.subtask_states["d"] == "pending"


@pytest.mark.asyncio
async def test_the_self_correction_loop_of_a_mixed_group_may_write_only_the_must_change_file(tmp_path):
    loop = AsyncMock(return_value=SelfCorrectionResult(
        resolved=False, turns_used=1, final_compile_output="", modified_files={}))
    with patch("kriya.workflow.self_correction.run_self_correction_loop", new=loop), \
         patch("kriya.tools.validate.PolymorphicValidator"):
        result, _calls = await _execute(
            tmp_path, _mixed_plan(), conflicts={"v": [["cons.py", "extra.py"]]},
            recovery={"p": ["unchanged"], "c": [("changed", ["extra.py"])]}, kernel=_kernel())
    assert result.legacy_result["status"] == "success"
    assert loop.await_count == 1  # the mixed MUST_CHANGE group only, never the MAY_CHANGE provider
    assert loop.await_args.kwargs["writable_files"] == ["extra.py"]


@pytest.mark.asyncio
async def test_an_unchanged_owner_is_finalized_truthfully(tmp_path):
    """The unchanged provider of an accepted recovery: completed, its written record exactly its planned run's, no
    new committed-write evidence, and its event says it was unchanged."""
    result, _calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                    recovery={"p": ["unchanged"], "c": ["changed"]})
    assert result.legacy_result["status"] == "success"
    assert result.control_state.subtask_states["p"] == "completed"
    assert result.control_state.subtask_written_files["p"] == ["prov.cfg"]
    assert result.control_state.subtask_written_files["c"] == ["cons.py"]
    event = _events(result)["p"]
    assert (event["may_change_outcome"], event["invalidated_subtasks"]) == ("unchanged", [])


@pytest.mark.asyncio
async def test_an_unrelated_defect_in_a_participant_is_still_repaired_from_the_failure_evidence(tmp_path):
    """The located owner conforms to the selection but has its own defect: it is handed the failure evidence, repairs
    it, and the co-participant stays as it was."""
    result, calls = await _execute(tmp_path, _plan(), conflicts={"v": [["cons.py"]]},
                                   recovery={"p": ["unchanged"], "c": ["changed"]})
    located = next(c["kwargs"]["recovery_contract_block"] for c in calls if (c["unit"], c["role"]) == ("c", MAY))
    assert EVIDENCE in located and "Repair it if the failure evidence requires a change" in located
    assert "while preserving the authoritative selected integration alternative" in located
    assert result.legacy_result["status"] == "success" and _events(result)["c"]["may_change_outcome"] == "changed"


# --- The five-way classification of an in-scope relationship artifact ---

@pytest.mark.asyncio
async def test_class3_the_failing_consumers_own_artifact_does_not_block_the_producers_recovery(tmp_path):
    """The consumer itself fails and names the producer's file. Its own artifact is an authoritative relationship
    artifact handled by the consumer retry: the producer is reopened MAY_CHANGE and the consumer is retried."""
    plan = _plan(units=[_unit("s1", ["base.cfg"]), _unit("p", ["prov.cfg"], ["s1"]), _unit("c", ["cons.py"], ["s1", "p"])])
    exec_plan = build_recovery_execution_plan(plan, _conflict(["prov.cfg"]), plan.subtask_by_id("c"))
    assert [(p.artifact, p.role, p.owner_resolution_basis) for p in exec_plan.participants] == [
        ("prov.cfg", RecoveryParticipantRole.REQUIRED_MUTATION, "dependency_ancestry"),
        ("cons.py", RecoveryParticipantRole.READ_ONLY_CONTEXT, RELATIONSHIP_ARTIFACT_HANDLED_BY_CONSUMER_RETRY),
    ]
    assert exec_plan.group_order == ("group.p",)
    result, calls = await _execute(tmp_path, plan, conflicts={"c": [["prov.cfg"]]}, recovery={"p": ["changed"]})
    assert result.legacy_result["status"] == "success"
    assert _reopens(calls) == [("p", MAY), ("c", "consumer_retry")]


def _late_participant_plan(action="create"):
    """A third participant ``late`` that runs after the verification unit, so it has not run when v fails."""
    return _plan(units=[
        _unit("s1", ["base.cfg"]), _unit("p", ["prov.cfg"], ["s1"]), _unit("c", ["cons.py"], ["s1", "p"]),
        _unit("v", [], ["c"], verification=True), _unit("late", ["late.py"], ["p", "v"], action=action),
    ], relationships=[_decision(consumers=("c", "late"))])


@pytest.mark.asyncio
async def test_class4_an_unrun_participant_whose_artifact_is_not_materialized_does_not_block_recovery(tmp_path):
    plan = _late_participant_plan()
    participants = _derive(plan, ["cons.py"], completed=("s1", "p", "c"))
    assert ("late.py", None, "read_only_context", "must_change", RELATIONSHIP_ARTIFACT_OWNER_NOT_COMPLETED) in _summary(participants)
    result, calls = await _execute(tmp_path, plan, conflicts={"v": [["cons.py"]]},
                                   recovery={"p": ["changed"], "c": ["unchanged"]})
    assert result.legacy_result["status"] == "success"
    assert _reopens(calls) == [("p", MAY), ("c", MAY), ("v", "consumer_retry")]
    assert [c["unit"] for c in calls][-1] == "late"  # it runs afterwards, on its planned turn


@pytest.mark.asyncio
async def test_class4_an_unrun_participant_with_an_existing_brownfield_artifact_is_not_ignored(tmp_path, caplog):
    """late.py already exists in the tree (a planned MODIFY). Its owner has not run, so no completed participant
    answers for bytes that can take part in the failure: the recovery is refused, nobody is reopened."""
    (tmp_path / "late.py").write_text("existing brownfield source\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):  # a brownfield tree is a committed one
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=tmp_path, check=True,
                       capture_output=True)
    with caplog.at_level(logging.WARNING, logger="kriya.workflow.workflow_controller"):
        result, calls = await _execute(tmp_path, _late_participant_plan(action="modify"),
                                       conflicts={"v": [["cons.py"]]}, recovery={"p": ["changed"], "c": ["changed"]})
    assert result.legacy_result["status"] != "success"
    assert _reopens(calls) == []
    assert any("RELATIONSHIP_ARTIFACT_OWNER_NOT_COMPLETED_BUT_MATERIALIZED" in r.getMessage() and "late.py" in r.getMessage()
               for r in caplog.records)


def test_class2_an_artifact_whose_effective_owner_is_not_a_participant_fails_closed(caplog):
    """d (outside the decision) was the last unit to write prov.cfg: it never received the selected decision."""
    plan = _plan(units=[
        _unit("s1", ["base.cfg"]), _unit("p", ["prov.cfg"], ["s1"]),
        _unit("d", ["prov.cfg"], ["p"], action="modify"), _unit("c", ["cons.py"], ["s1", "p", "d"]),
        _unit("v", [], ["c"], verification=True),
    ])
    participants = _derive(plan, ["cons.py"], completed=("s1", "p", "d", "c"))
    refused = [p for p in participants if p.artifact == "prov.cfg"]
    assert [(p.role, p.effective_owner_subtask_id, p.owner_resolution_basis) for p in refused] == [
        (RecoveryParticipantRole.READ_ONLY_CONTEXT, None, "unresolved")]
    assert refused[0].mutation_reason.startswith(owner_recovery.RELATIONSHIP_ARTIFACT_OWNER_NOT_PARTICIPANT)
    state = ControlState(schema_version=1, run_id="t").with_updates(
        subtask_states={uid: "completed" for uid in ("s1", "p", "d", "c")},
        subtask_written_files={"s1": ["base.cfg"], "p": ["prov.cfg"], "d": ["prov.cfg"], "c": ["cons.py"]})
    with caplog.at_level(logging.WARNING, logger="kriya.workflow.workflow_controller"):
        assert build_recovery_execution_plan(
            plan, _conflict(["cons.py"]), plan.subtask_by_id("v"),
            order=[st.id for st in plan.subtasks], control_state=state) is None
    assert any("RECOVERY_RELATIONSHIP_ARTIFACT_REFUSED" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("case", ["completed_unreconciled", "no_canonical_owner", "owner_outside_relationship"])
def test_class5_every_other_unresolved_in_scope_artifact_fails_closed(case):
    if case == "completed_unreconciled":
        # m is a participant that COMPLETED without writing its (brownfield) file and is not upstream of v
        plan = _plan(units=[
            _unit("s1", ["base.cfg"]), _unit("p", ["prov.cfg"], ["s1"]), _unit("m", ["side.cfg"], ["s1"], action="modify"),
            _unit("c", ["cons.py"], ["s1", "p"]), _unit("v", [], ["c"], verification=True),
        ], relationships=[_decision(producers=("p", "m"))])
        participants = _derive(plan, ["cons.py"], completed=("s1", "p", "m", "c"),
                               written={"s1": ["base.cfg"], "p": ["prov.cfg"], "m": [], "c": ["cons.py"]})
        artifact = "side.cfg"
    elif case == "no_canonical_owner":
        plan = _plan(relationships=[_decision(artifacts=["prov.cfg", "cons.py", "README.md"])])
        participants, artifact = _derive(plan, ["cons.py"]), "README.md"
    else:
        # dep.py is declared in the decision's scope but its only owner, d, is not one of the participants
        plan = _plan(relationships=[_decision(artifacts=["prov.cfg", "cons.py", "dep.py"])])
        participants = _derive(plan, ["cons.py"], completed=("s1", "p", "c"))
        artifact = "dep.py"
    (refused,) = [p for p in participants if p.artifact == artifact]
    assert (refused.effective_owner_subtask_id, refused.owner_resolution_basis) == (None, "unresolved")
    assert refused.mutation_reason.startswith(owner_recovery.RELATIONSHIP_ARTIFACT_OWNER_UNRECONCILED)
    # one refused artifact refuses the whole recovery plan (the existing unresolved-owner refusal)
    assert [p for p in participants if p.owner_resolution_basis == "unresolved"] == [refused]
    assert build_recovery_execution_plan(plan, _conflict(["cons.py"]), plan.subtask_by_id("v")) is None


# --- The context transport: one explicit controller forwarder; a scope conflict is failure evidence, never a request ---

REMOVED_TRANSPORT_KEY = "may_change_recovery_request"


async def _execute_spying_on_the_context_builders(tmp_path, plan, *, conflicts, recovery):
    """The run, plus what reached each of owner_recovery's two context builders (through the controller's forwarders)
    and the recovery execution plans the loop built."""
    real_may, real_plain = owner_recovery._build_may_change_recovery_context, owner_recovery._build_owner_recovery_context
    real_exec_plan = workflow_controller.build_recovery_execution_plan
    may_calls, plain_calls, exec_plans = [], [], []

    def spy_may(*args, **kwargs):
        text = real_may(*args, **kwargs)
        may_calls.append({"args": args, "kwargs": kwargs, "text": text})
        return text

    def spy_plain(*args, **kwargs):
        text = real_plain(*args, **kwargs)
        plain_calls.append({"args": args, "kwargs": kwargs, "text": text})
        return text

    def spy_exec_plan(*args, **kwargs):
        built = real_exec_plan(*args, **kwargs)
        if built is not None:  # the loop asks after every unit; a unit without a conflict has no recovery plan
            exec_plans.append(built)
        return built

    with patch.object(owner_recovery, "_build_may_change_recovery_context", spy_may), \
         patch.object(owner_recovery, "_build_owner_recovery_context", spy_plain), \
         patch.object(workflow_controller, "build_recovery_execution_plan", spy_exec_plan):
        result, calls = await _execute(tmp_path, plan, conflicts=conflicts, recovery=recovery)
    return result, calls, may_calls, plain_calls, exec_plans


def _may_change_participants_of(exec_plan, owner_id):
    return [p for p in exec_plan.participants
            if p.effective_owner_subtask_id == owner_id and p.recovery_action is RecoveryAction.MAY_CHANGE]


@pytest.mark.asyncio
async def test_the_may_change_request_reaches_the_canonical_builder_with_exactly_its_inputs(tmp_path):
    """Transport control: for every MAY_CHANGE owner the loop calls owner_recovery._build_may_change_recovery_context
    (the canonical builder) with the run's plan, the owner, the failing subtask, that owner's MAY_CHANGE participants
    of the recovery plan (decision evidence included) and the scope conflict AS IT IS - and what the builder returns
    is the Developer's recovery block, byte for byte. The MUST-FIX builder is not involved."""
    plan = _plan()
    _result, calls, may_calls, plain_calls, exec_plans = await _execute_spying_on_the_context_builders(
        tmp_path, plan, conflicts={"v": [["cons.py"]]}, recovery={"p": ["unchanged"], "c": ["changed"]})
    (exec_plan,) = exec_plans
    assert [c["kwargs"]["owner_id"] for c in may_calls] == ["p", "c"] and plain_calls == []
    blocks = {c["unit"]: c["kwargs"]["recovery_contract_block"] for c in calls if c["role"] == MAY}
    for call in may_calls:
        owner_id = call["kwargs"]["owner_id"]
        expected_participants = _may_change_participants_of(exec_plan, owner_id)
        assert len(expected_participants) == 1 and expected_participants[0].evidence_ids == ("plan.integration.r1",)
        assert call["args"] == (plan,)
        assert call["kwargs"] == {
            "owner_id": owner_id, "failed_subtask_id": "v", "participants": expected_participants,
            "scope_conflict": _conflict(["cons.py"]),
        }
        assert all(sent is planned for sent, planned in
                   zip(call["kwargs"]["participants"], expected_participants, strict=True))
        assert blocks[owner_id] == call["text"]
        assert call["text"].startswith("--- INTEGRATION-DECISION RECOVERY (a change is NOT presumed) ---\n")
        assert 'decision r1: SELECTED "declarative"' in call["text"] and EVIDENCE in call["text"]


@pytest.mark.asyncio
async def test_a_mixed_group_asks_each_builder_once_and_neither_conflict_carries_a_request(tmp_path):
    plan = _mixed_plan()
    conflict = _conflict(["cons.py", "extra.py"])
    _result, calls, may_calls, plain_calls, exec_plans = await _execute_spying_on_the_context_builders(
        tmp_path, plan, conflicts={"v": [["cons.py", "extra.py"]]},
        recovery={"p": ["unchanged"], "c": [("changed", ["extra.py"])]})
    (exec_plan,) = exec_plans
    assert [c["kwargs"]["owner_id"] for c in may_calls] == ["p", "c"]
    (plain,) = plain_calls
    assert plain["kwargs"]["owner_id"] == "c" and plain["kwargs"]["required_owner_files"] == ["extra.py"]
    assert plain["kwargs"]["cross_owner_obligation"] is not None
    assert plain["kwargs"]["scope_conflict"] == conflict
    mixed = may_calls[1]
    assert mixed["args"] == (plan,) and mixed["kwargs"] == {
        "owner_id": "c", "failed_subtask_id": "v", "participants": _may_change_participants_of(exec_plan, "c"),
        "scope_conflict": conflict,
    }
    assert [p.artifact for p in mixed["kwargs"]["participants"]] == ["cons.py"]
    (developer,) = [c for c in calls if (c["unit"], c["role"]) == ("c", MUST)]
    assert developer["kwargs"]["recovery_contract_block"] == plain["text"] + "\n\n" + mixed["text"]


def test_the_controller_forwarder_is_pure_transport_to_the_owner_recovery_builder():
    plan = _plan()
    conflict = _conflict(["cons.py"])
    participants = _may_change_participants_of(
        build_recovery_execution_plan(plan, conflict, plan.subtask_by_id("v")), "c")
    assert participants
    with patch.object(owner_recovery, "_build_may_change_recovery_context", return_value="the builder's text") as body:
        forwarded = workflow_controller._build_may_change_recovery_context(
            plan, owner_id="c", failed_subtask_id="v", participants=participants, scope_conflict=conflict)
    assert forwarded == "the builder's text"
    body.assert_called_once_with(
        plan, owner_id="c", failed_subtask_id="v", participants=participants, scope_conflict=conflict)
    sent = body.call_args
    assert sent.args[0] is plan and sent.kwargs["participants"] is participants
    assert sent.kwargs["scope_conflict"] is conflict
    # unpatched: the forwarder's answer is the owner's answer
    assert workflow_controller._build_may_change_recovery_context(
        plan, owner_id="c", failed_subtask_id="v", participants=participants, scope_conflict=conflict,
    ) == owner_recovery._build_may_change_recovery_context(
        plan, owner_id="c", failed_subtask_id="v", participants=participants, scope_conflict=conflict)


def test_a_scope_conflict_cannot_carry_a_may_change_request():
    """Negative control: the removed transport key on a scope conflict is inert - the MUST-FIX builder neither reads
    it nor reaches the MAY_CHANGE builder - and nothing in the product spells it."""
    plan = _plan()
    conflict = _conflict(["cons.py"])
    participants = build_recovery_execution_plan(plan, conflict, plan.subtask_by_id("v")).participants
    smuggled = {**conflict, REMOVED_TRANSPORT_KEY: {"plan": plan, "participants": participants}}
    arguments = {"owner_id": "c", "failed_subtask_id": "v", "required_owner_files": ["cons.py"],
                 "cross_owner_obligation": None}
    with patch.object(owner_recovery, "_build_may_change_recovery_context") as may_change_builder:
        text = owner_recovery._build_owner_recovery_context(scope_conflict=smuggled, **arguments)
        via_controller = workflow_controller._build_owner_recovery_context(scope_conflict=smuggled, **arguments)
    may_change_builder.assert_not_called()
    assert text == via_controller == owner_recovery._build_owner_recovery_context(scope_conflict=conflict, **arguments)
    assert text.startswith("--- authoritative plan recovery ---\n") and "INTEGRATION-DECISION" not in text
    assert not hasattr(recovery_plan, "MAY_CHANGE_RECOVERY_REQUEST")
    product = [*Path(kriya.__file__).parent.rglob("*.py"),
               *(Path(kriya.__file__).parent.parent / "plugins").rglob("*.py")]
    assert len(product) > 100
    assert [str(path) for path in product
            if REMOVED_TRANSPORT_KEY in path.read_text(encoding="utf-8").lower()] == []


def test_the_enforce_loop_hands_every_context_builder_the_scope_conflict_itself():
    """Static control: no ``deps._build_*_recovery_context(...)`` call in the enforce loop passes anything but the
    bare ``scope_conflict`` name - a copy with an added key (under any name) is how a request was once tunnelled."""
    tree = ast.parse(Path(enforce_loop.__file__).read_text(encoding="utf-8"))
    builder_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "deps"
        and node.func.attr in ("_build_owner_recovery_context", "_build_may_change_recovery_context")
    ]
    assert sorted(call.func.attr for call in builder_calls) == [
        "_build_may_change_recovery_context", "_build_may_change_recovery_context", "_build_owner_recovery_context"]
    for call in builder_calls:
        (conflict,) = [kw.value for kw in call.keywords if kw.arg == "scope_conflict"]
        assert isinstance(conflict, ast.Name) and conflict.id == "scope_conflict", ast.unparse(call)
        assert all(kw.arg is not None for kw in call.keywords), ast.unparse(call)  # no **splat

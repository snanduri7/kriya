"""PRD-008A A5: WorkUnit-aware checkpoint identity and common completion reuse.

Selection is by work-unit identity for every unit (direct and milestone
alike); PRD-008's validate_resume_against_reality() still decides whether
the selected checkpoint is reusable. Completion reuse is what PRD-008 S4b/S4c
validated, applied by the one executor, and fails closed if incoherent.
"""
import asyncio
import json
import os

import pytest
from _milestone_proof_harness import _milestone, _run, _workspace, git_workspace  # noqa: F401
from test_prd008a_plan_executor import (  # noqa: F401 - pytest fixture
    CALC,
    THREE,
    THREE_OUTPUTS,
    FailingEngine,
    ScriptedDriver,
    _direct,
    _execute,
    _plan,
    _states,
    _unit,
    calc_workspace,
)

from kriya.agents.contracts import AcceptanceCriterion
from kriya.control.persistence import scan_run_records
from kriya.control.workspace_identity import WorkspaceOwnershipError, ownership_metadata
from kriya.workflow.checkpoint import checkpoint_path, list_checkpoints, save_checkpoint
from kriya.workflow.plan_adapters import direct_execution_plan, work_unit_record
from kriya.workflow.plan_executor import (
    CHECKPOINT_IDENTITY_MISMATCH,
    CHECKPOINT_SELECTED,
    COMPLETION_REUSE_INCONSISTENT,
    NO_COMPATIBLE_CHECKPOINT,
    select_work_unit_checkpoint,
)

DIRECT = work_unit_record(direct_execution_plan("goal", plan_id="r"), "direct")


def _milestone_record(mid, digest="d"):
    return {"kind": "milestone", "group_id": "G", "milestone_id": mid, "definition_digest": digest}


def _save(workspace, run_id, saved_at, **data):
    save_checkpoint(str(workspace), run_id, {"stage": "plan", **data})
    path = checkpoint_path(str(workspace), run_id)
    with open(path) as handle:
        payload = json.load(handle)
    payload["saved_at"] = saved_at
    with open(path, "w") as handle:
        json.dump(payload, handle)


def _select(workspace, record, **kwargs):
    return select_work_unit_checkpoint(str(workspace), record, **{"resume": True, "resume_id": None, **kwargs})


# --- selection -------------------------------------------------------------------------

def test_a_direct_unit_never_resumes_a_newer_milestone_checkpoint(tmp_path):
    ws = _workspace(tmp_path)
    _save(ws, "direct-old", 1, work_unit=DIRECT)
    _save(ws, "milestone-new", 2, work_unit=_milestone_record("M1"), milestone_group_id="G")
    resume, resume_id, event = _select(ws, DIRECT, single_unit_plan=True)
    assert (resume, resume_id) == (False, "direct-old")
    assert event["code"] == CHECKPOINT_SELECTED and not event["explicit"]


def test_w1_resumes_its_own_latest_checkpoint_never_w2s(tmp_path):
    ws = _workspace(tmp_path)
    _save(ws, "w1-old", 1, work_unit=_milestone_record("W1"), milestone_group_id="G")
    _save(ws, "w1-new", 2, work_unit=_milestone_record("W1"), milestone_group_id="G")
    _save(ws, "w2-newest", 3, work_unit=_milestone_record("W2"), milestone_group_id="G")
    assert _select(ws, _milestone_record("W1"))[1] == "w1-new"
    assert _select(ws, _milestone_record("W2"))[1] == "w2-newest"
    # A changed definition is a different unit: nothing is offered.
    _, resume_id, event = _select(ws, _milestone_record("W1", digest="changed"))
    assert resume_id is None and event["code"] == NO_COMPATIBLE_CHECKPOINT


def test_a_pre_prd008a_direct_checkpoint_is_still_offered_to_a_direct_unit_only(tmp_path):
    ws = _workspace(tmp_path)
    _save(ws, "legacy-direct", 1)
    _save(ws, "legacy-milestone", 2, milestone_group_id="G")
    assert _select(ws, DIRECT)[1] == "legacy-direct"
    _, resume_id, event = _select(ws, _milestone_record("M1"))
    assert resume_id is None and event["code"] == NO_COMPATIBLE_CHECKPOINT


def test_an_explicit_id_in_a_single_unit_plan_goes_to_prd008_to_judge(tmp_path):
    ws = _workspace(tmp_path)
    _save(ws, "someone-elses", 1, work_unit=_milestone_record("M1"), milestone_group_id="G")
    assert _select(ws, DIRECT, resume=False, resume_id="someone-elses", single_unit_plan=True)[:2] == (
        False, "someone-elses",
    )
    # ...but in a multi-unit plan the id reaches every unit and only its own takes it.
    _, resume_id, event = _select(ws, _milestone_record("M2"), resume=False, resume_id="someone-elses")
    assert resume_id is None and event["code"] == CHECKPOINT_IDENTITY_MISMATCH


def test_no_resume_requested_selects_nothing(tmp_path):
    ws = _workspace(tmp_path)
    _save(ws, "c", 1, work_unit=DIRECT)
    assert select_work_unit_checkpoint(str(ws), DIRECT, resume=False, resume_id=None) == (False, None, None)


def test_another_workspaces_checkpoint_is_an_error_not_silently_skipped(tmp_path):
    ws = _workspace(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _save(ws, "foreign", 1, work_unit=DIRECT)
    path = checkpoint_path(str(ws), "foreign")
    with open(path) as handle:
        payload = json.load(handle)
    payload["_workspace"] = ownership_metadata(os.path.realpath(other))
    with open(path, "w") as handle:
        json.dump(payload, handle)
    with pytest.raises(WorkspaceOwnershipError):
        _select(ws, DIRECT)


# --- direct resume end to end -----------------------------------------------------------

def test_direct_resume_selects_its_own_checkpoint_and_prd008_still_judges_it(calc_workspace):
    _direct(calc_workspace, "[]")  # fails, keeps a direct checkpoint
    [own] = [c["run_id"] for c in list_checkpoints(str(calc_workspace))]
    _save(calc_workspace, "milestone-newer", 10 ** 12, work_unit=_milestone_record("M1"), milestone_group_id="G")

    from unittest.mock import AsyncMock

    from test_prd008a_plan_executor import _config, _role_llm

    from kriya.core.kernel import Kernel
    from kriya.workflow.workflow import WorkflowEngine

    cfg = _config()
    engine = WorkflowEngine(Kernel(config=cfg), _role_llm(cfg, "[]"))
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    asyncio.run(engine.run_generation_workflow(
        goal="add sub to calc.py", workspace_path=str(calc_workspace), resume=True,
    ))
    newest = max(scan_run_records(str(calc_workspace)).records, key=lambda r: r.created_at)
    # PRD-008's validator ran on the direct unit's own checkpoint.
    assert newest.resume_decision is not None
    assert newest.resume_decision["checkpoint_id"] == own


# --- completion reuse -------------------------------------------------------------------

def test_reuse_with_a_non_reused_ancestor_fails_closed_before_any_work(tmp_path):
    ws = _workspace(tmp_path)
    plan = _plan(_unit("W1"), _unit("W2", ["W1"]), _unit("W3", ["W2"]))
    driver = ScriptedDriver(ws)
    result, record = _execute(ws, plan, driver, reusable_unit_ids=["W2"])
    assert result["status"] == "needs_review"
    assert result["reason_codes"] == [COMPLETION_REUSE_INCONSISTENT]
    assert result["work_unit_ids"] == ["W2"]
    assert driver.ran == []
    assert _states(record)["W2"] == ("STALE", [COMPLETION_REUSE_INCONSISTENT], [])


def _goal_edited(milestones, mid, goal):
    return [m.model_copy(update={"goal": goal}) if m.id == mid else m for m in milestones]


@pytest.mark.parametrize("edit,rerun", [
    # Goal / criterion / dependency change of M2 invalidates M2 and its descendant.
    (lambda ms: _goal_edited(ms, "M2", "build M2 differently"), ["M2", "M3", "INTEGRATION"]),
    (lambda ms: [
        m.model_copy(update={"acceptance": [AcceptanceCriterion(id="M2-A1", description="M2 works better")]})
        if m.id == "M2" else m for m in ms
    ], ["M2", "M3", "INTEGRATION"]),
    (lambda ms: [m.model_copy(update={"depends_on": []}) if m.id == "M3" else m for m in ms],
     ["M3", "INTEGRATION"]),
    # An unrelated new unit changes the plan but not M1-M3's identity.
    (lambda ms: ms + [_milestone("M4")], ["M4", "INTEGRATION"]),
])
def test_plan_drift_invalidates_exactly_the_changed_units_and_their_descendants(tmp_path, edit, rerun):
    ws = _workspace(tmp_path)
    result, _ = _run(ws, THREE, FailingEngine(THREE, THREE_OUTPUTS))
    assert result["status"] == "success"
    first = scan_run_records(str(ws)).records[0].execution_plan["fingerprint"]

    edited = edit(list(THREE))
    engine = FailingEngine(edited, {**THREE_OUTPUTS, "M4": {"m4.py": b"M4 = 1\n"}})
    result, _ = _run(ws, edited, engine)
    assert result["status"] == "success", result
    assert engine.calls == rerun
    newest = max(scan_run_records(str(ws)).records, key=lambda r: r.created_at)
    assert newest.execution_plan["fingerprint"] != first
    reused = {uid for uid, state in newest.work_unit_states.items() if "COMPLETION_REUSED" in state["reason_codes"]}
    assert reused == {"M1", "M2", "M3"} - set(rerun)


INDEPENDENT = [_milestone("W1"), _milestone("W2"), _milestone("W3", ["W1"])]
INDEPENDENT_OUTPUTS = {m.id: {f"{m.id.lower()}.py": f"{m.id} = 1\n".encode()} for m in INDEPENDENT}


def _newest_record(ws):
    return max(scan_run_records(str(ws)).records, key=lambda r: r.created_at)


def _reused(record):
    return {uid for uid, state in record.work_unit_states.items() if "COMPLETION_REUSED" in state["reason_codes"]}


def test_reordering_independent_units_is_detected_but_does_not_invalidate_their_completion(tmp_path):
    """The plan fingerprint is a change detector, not the reuse key: a pure
    reorder of independent units changes it, and every unit is still reused
    because each unit's own definition and dependencies are unchanged. A real
    edit of W1 then invalidates W1 and its dependent W3 only; W2 stays reused."""
    ws = _workspace(tmp_path)
    result, _ = _run(ws, INDEPENDENT, FailingEngine(INDEPENDENT, INDEPENDENT_OUTPUTS))
    assert result["status"] == "success", result
    first = _newest_record(ws).execution_plan["fingerprint"]

    reordered = [INDEPENDENT[1], INDEPENDENT[0], INDEPENDENT[2]]  # W2, W1, W3
    engine = FailingEngine(reordered, INDEPENDENT_OUTPUTS)
    result, _ = _run(ws, reordered, engine)
    assert result["status"] == "success", result
    reorder_record = _newest_record(ws)
    assert reorder_record.execution_plan["fingerprint"] != first  # the change is detected and recorded
    assert [u["id"] for u in reorder_record.execution_plan["work_units"]][:3] == ["W2", "W1", "W3"]
    assert engine.calls == ["INTEGRATION"]  # no unit re-executed
    assert _reused(reorder_record) == {"W1", "W2", "W3"}

    edited = _goal_edited(reordered, "W1", "build W1 differently")
    engine = FailingEngine(edited, INDEPENDENT_OUTPUTS)
    result, _ = _run(ws, edited, engine)
    assert result["status"] == "success", result
    edit_record = _newest_record(ws)
    assert edit_record.execution_plan["fingerprint"] != reorder_record.execution_plan["fingerprint"]
    assert engine.calls == ["W1", "W3", "INTEGRATION"]
    assert _reused(edit_record) == {"W2"}

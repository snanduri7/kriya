"""NO SOURCE MUTATION -> NO SOURCE-CHANGE APPROVAL (owner run 20261015T075602-2b5a0ad7, unit s4).

MEASURED in that run (handover/DEMO-V2/GREENFIELD-OWNER-UX-FINDINGS.md): s4 was a verification-only unit (execution_role=verification, no planned files, DENY_ALL write scope).
It wrote nothing, yet the pre-apply source-change gate fired on ``autonomy.mode == "human-in-the-loop"`` alone and called the approval callback with an EMPTY change set; the desktop
declined it (``other_approval``), the workflow recorded ``approval.decision: rejected`` / ``human_rejected`` and the run ended FAILURE / NOT_COMMITTED.

Through the real WorkflowController enforce loop and the real workflow; only the model transport and the toolchain gates are stubbed (the P4 reproducer harness). Generic: no
package, goal or language special case.
"""
import json

import _enforce_unit_harness as harness

from kriya.workflow.plan_schema import EngineeringPlan

SERVICE = "shop/service.py"
OTHER = "shop/other.py"
BASE_SOURCE = "def page_size():\n    return 5\n"
PRODUCED_SOURCE = "PAGE_SIZE = 5\n\n\ndef page_size():\n    return PAGE_SIZE\n"
OTHER_SOURCE = "OTHER = 1\n"
UNITS = {"s1": (SERVICE, PRODUCED_SOURCE), "s2": (OTHER, OTHER_SOURCE)}


def _subtask(sid, path, *, depends=(), provides=()):
    return {"id": sid, "description": f"edit {path}", "execution_method": "model", "planned_files": [{"path": path, "action": "create" if path == OTHER else "modify"}],
            "depends_on": list(depends), "provides": list(provides), "relevant_global_invariant_ids": ["gi1"],
            "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]}


def _plan(editors, verification_only):
    subtasks = [_subtask(sid, path, depends=deps, provides=[f"p_{sid}"]) for sid, path, deps in editors]
    if verification_only:
        subtasks.append({"id": "sv", "description": "verify the application", "execution_method": "model", "execution_role": "verification", "planned_files": [],
                         "depends_on": [sid for sid, _p, _d in editors], "requires": [f"p_{sid}" for sid, _p, _d in editors], "relevant_global_invariant_ids": ["gi1"],
                         "verification": [{"type": "tool", "tool_name": "compile", "description": "build"}]})
    return EngineeringPlan.model_validate({"plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [], "subtasks": subtasks})


def run_enforce(tmp_path, *, editors, verification_only, answer=True):
    """-> (workspace, events, result, approvals); approvals are (unit, [file paths], reason) for EVERY approval-callback call."""
    run = harness.run_enforce(tmp_path, plan=_plan(editors, verification_only), units=UNITS, answer=answer, base_files={SERVICE: BASE_SOURCE, "shop/__init__.py": ""})
    return run.workspace, run.events, run.result, run.approvals


ONE = [("s1", SERVICE, ())]
TWO = [("s1", SERVICE, ()), ("s2", OTHER, ("s1",))]


_decisions = harness.decisions


def test_a_verification_only_unit_with_no_files_never_asks_for_a_source_change_approval(tmp_path):
    """THE MEASURED DEFECT: before the fix the callback is called a second time, for unit 'sv', with files == []."""
    _workspace, events, result, approvals = run_enforce(tmp_path, editors=ONE, verification_only=True)

    assert [(unit, files) for unit, files, _reason in approvals] == [("s1", [SERVICE])], approvals  # the real change only
    assert _decisions(events) == ["approved"]                                                     # no 'rejected' recorded for nothing
    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "sv": "completed"}
    assert result.legacy_result["status"] == "success"


def test_the_empty_gate_is_not_declined_into_a_rejected_run(tmp_path):
    """The owner's symptom: an automatic decline of an empty approval ended the run human_rejected / FAILURE."""
    _workspace, events, result, _approvals = run_enforce(tmp_path, editors=ONE, verification_only=True, answer=False)

    # the REAL change is still refused when the person says no (existing semantics): s1 is rejected and the run stops before sv exists
    assert _decisions(events) == ["rejected"]
    assert {r.subtask_id for r in result.subtask_results} == {"s1"}
    assert result.legacy_result["status"] == "failed"


def test_one_changed_file_asks_exactly_once_with_the_complete_subject(tmp_path):
    workspace, events, result, approvals = run_enforce(tmp_path, editors=ONE, verification_only=False)

    assert len(approvals) == 1 and approvals[0][0] == "s1" and approvals[0][1] == [SERVICE]
    assert "PAGE_SIZE" in (workspace / SERVICE).read_text()   # approved -> applied
    assert _decisions(events) == ["approved"] and result.legacy_result["status"] == "success"


def test_two_units_each_ask_once_for_their_own_files(tmp_path):
    _workspace, events, _result, approvals = run_enforce(tmp_path, editors=TWO, verification_only=True)

    assert [(unit, files) for unit, files, _reason in approvals] == [("s1", [SERVICE]), ("s2", [OTHER])]
    assert _decisions(events) == ["approved", "approved"]


def test_a_rejected_real_change_still_records_the_rejection_and_applies_nothing(tmp_path):
    workspace, events, result, approvals = run_enforce(tmp_path, editors=ONE, verification_only=True, answer=False)

    assert len(approvals) == 1 and approvals[0][1] == [SERVICE]
    assert _decisions(events) == ["rejected"]
    assert (workspace / SERVICE).read_text() == BASE_SOURCE
    assert result.legacy_result["status"] == "failed"


def test_the_approval_decision_event_is_json_serialisable_and_names_its_trigger(tmp_path):
    _workspace, events, _result, _approvals = run_enforce(tmp_path, editors=ONE, verification_only=True)
    [decision] = [e for e in events if e.kind == "approval.decision"]
    json.dumps(decision.details)
    assert decision.details["triggers"] == ["human_in_the_loop"]

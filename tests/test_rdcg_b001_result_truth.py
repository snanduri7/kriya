"""RDCG-B-001 (owner decision 2026-10-23): no workflow path returns terminal success while the run's authoritative RunRecord is UNCERTAIN.

MEASURED before the fix: one transient write error on the single RunRecord save that settles the commit cycle (after the source bytes and the COMMITTED journal entry were durable) made the DIRECT
workflow return `quality_gates_passed: True` with no mention of the problem while the record ended UNCERTAIN, and the ENFORCE workflow return `status: success` plus an error list. The ONE owner of
that reconciliation is `coordinated_mutation` (kriya/control/run_coordinator.py): a result that claims success while the record did not end SUCCESS becomes `needs_review` (UNCERTAIN) or `failed`
(any other terminal state), `quality_gates_passed` (the aggregate terminal truth the CLI exit code reads) becomes False, and every factual sub-result is kept.

The direct-workflow tests drive the real pipeline with the scripted runtime (tests/_chaos_harness.py); the wrapper tests drive the real decorator, not a stand-in."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
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
from click.testing import CliRunner

import kriya.control.run_coordinator as run_coordinator_module
from kriya.cli import main
from kriya.control.persistence import list_run_records
from kriya.control.run_coordinator import coordinated_mutation, current_run_context, transition_mutating_run
from kriya.control.run_record import RunLifecycle
from kriya.repository.commit import list_commit_evidence
from kriya.workflow.workflow_types import WorkflowResult

GOAL = "add sub to calc.py"
SUCCESS_CLAIM = {"status": "success", "quality_gates_passed": True, "candidate_gates_passed": True, "terminal_regression_passed": True,
                 "overall_attempt_passed": True, "files": ["app.py"], "run_id": "r-1"}


def _fail_the_settle_save_once(monkeypatch):
    """One transient write error exactly on the save that settles the commit cycle (the `record_errors` window of the terminal commit)."""
    real_save = run_coordinator_module.save_run_record
    fired = []

    def failing_settle(workspace_path, record, *, expected_revision):
        if bool(record.commits) and record.commits[-1].get("result") == "COMMITTED" and not fired:
            fired.append(1)
            raise OSError(5, "injected EIO on the RunRecord settle save")
        return real_save(workspace_path, record, expected_revision=expected_revision)

    monkeypatch.setattr(run_coordinator_module, "save_run_record", failing_settle)
    return fired


# --- the real direct pipeline --------------------------------------------------------------------------------------------------

def test_direct_workflow_with_a_failed_commit_settle_is_needs_review_not_success(tmp_path, monkeypatch):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    fired = _fail_the_settle_save_once(monkeypatch)
    runtime = ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), GOAL, workspace)

    assert fired == [1]
    # the run is not terminal success, and says why, in the shape the next-run refusal already uses
    assert result["status"] == "needs_review" and result["quality_gates_passed"] is False
    assert result["reason_codes"] == ["UNCERTAIN_RUN_RECORD_COMMIT_STATE"] and result["recovery_command"] == "kriya runs recover"
    assert result["run_record_state"] == "UNCERTAIN" and result["reported_status"] in (None, "success")
    # the persistence error is no longer dropped (it was never read on the direct path)
    assert [e["operation"] for e in result["post_commit_persistence_errors"]] == ["run_record_commit_committed"]
    assert "injected EIO" in result["post_commit_persistence_errors"][0]["error"]
    # independent evidence is not falsified: the gates passed, the bytes are in the workspace, the journal says committed
    assert result["candidate_gates_passed"] is True and result["overall_attempt_passed"] is True
    assert result["files"] == ["calc.py"]
    assert Path(workspace, "calc.py").read_text() == CALC_WITH_SUB
    assert [e.state.value for _p, e, _err in list_commit_evidence(str(workspace)) if e] == ["committed"]
    [record] = list_run_records(str(workspace))
    assert record.lifecycle_state == RunLifecycle.UNCERTAIN
    assert result["run_record_id"] == record.run_id  # names the record `kriya runs recover` will settle (the result's own run_id is the workflow's trace id)


def test_direct_workflow_without_a_fault_is_still_plain_success(tmp_path):
    """Normal output (rule 4/2): the reconciliation must not touch a run whose record ends SUCCESS - no extra keys, no status change."""
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    runtime = ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), GOAL, workspace)
    assert result["quality_gates_passed"] is True
    assert result.get("status") != "needs_review" and "run_record_state" not in result and "post_commit_persistence_errors" not in result
    [record] = list_run_records(str(workspace))
    assert record.lifecycle_state == RunLifecycle.SUCCESS


# --- the wrapper: the one result-truth owner ---------------------------------------------------------------------------------------

def _run(function, workspace):
    return asyncio.run(function(workspace_path=str(workspace)))


@pytest.mark.parametrize("final_state,status,code", [(RunLifecycle.UNCERTAIN, "needs_review", "UNCERTAIN_RUN_RECORD_COMMIT_STATE"),
                                                      (RunLifecycle.FAILURE, "failed", "RESULT_CONTRADICTS_RUN_RECORD")])
def test_a_success_claim_over_a_record_that_did_not_end_success_is_not_success(tmp_path, final_state, status, code):
    @coordinated_mutation
    async def run(workspace_path):
        transition_mutating_run(current_run_context(), final_state)  # the record is already terminal when the workflow returns
        return dict(SUCCESS_CLAIM)

    result = _run(run, tmp_path)
    assert result["status"] == status and result["quality_gates_passed"] is False
    assert result["reason_codes"] == [code] and result["run_record_state"] == final_state.value and result["reported_status"] == "success"
    assert ("recovery_command" in result) is (final_state == RunLifecycle.UNCERTAIN)
    for kept in ("candidate_gates_passed", "terminal_regression_passed", "overall_attempt_passed"):  # factual sub-results are not rewritten
        assert result[kept] is True
    assert result["files"] == ["app.py"] and result["run_id"] == "r-1"
    assert result["run_record_id"] == list_run_records(str(tmp_path))[0].run_id
    assert list_run_records(str(tmp_path))[0].lifecycle_state == final_state


def test_the_claim_is_recognised_from_quality_gates_passed_alone_and_from_status_alone(tmp_path):
    for claim in ({"quality_gates_passed": True}, {"status": "success"}):
        @coordinated_mutation
        async def run(workspace_path, claim=claim):
            transition_mutating_run(current_run_context(), RunLifecycle.UNCERTAIN)
            return dict(claim)

        result = _run(run, tmp_path / f"w{len(claim)}{next(iter(claim))}")  # distinct workspace per case
        assert result["status"] == "needs_review" and result["quality_gates_passed"] is False


def test_a_workflow_result_object_is_reconciled_in_its_legacy_result_and_keeps_its_other_fields(tmp_path):
    marker = object()

    @coordinated_mutation
    async def run(workspace_path):
        transition_mutating_run(current_run_context(), RunLifecycle.UNCERTAIN)
        return WorkflowResult(run_id="r-1", control_state=marker, route="route", legacy_result=dict(SUCCESS_CLAIM))

    result = _run(run, tmp_path)
    assert isinstance(result, WorkflowResult) and result.control_state is marker and result.route == "route" and result.run_id == "r-1"
    assert result.legacy_result["status"] == "needs_review" and result.legacy_result["quality_gates_passed"] is False


def test_a_result_that_is_not_a_success_claim_is_returned_untouched(tmp_path):
    returned = {"status": "failed", "quality_gates_passed": False, "files": []}

    @coordinated_mutation
    async def run(workspace_path):
        transition_mutating_run(current_run_context(), RunLifecycle.UNCERTAIN)
        return returned

    result = _run(run, tmp_path)
    assert result is returned and set(result) == {"status", "quality_gates_passed", "files"}


def test_a_success_claim_over_a_success_record_is_returned_as_the_same_object(tmp_path):
    returned = dict(SUCCESS_CLAIM)

    @coordinated_mutation
    async def run(workspace_path):
        return returned

    result = _run(run, tmp_path)
    assert result is returned and result["status"] == "success" and "run_record_state" not in result
    assert list_run_records(str(tmp_path))[0].lifecycle_state == RunLifecycle.SUCCESS


def test_a_nested_call_is_not_reconciled_only_the_outermost_result_is(tmp_path):
    seen = {}

    @coordinated_mutation
    async def inner(workspace_path):
        transition_mutating_run(current_run_context(), RunLifecycle.UNCERTAIN)
        return dict(SUCCESS_CLAIM)

    @coordinated_mutation
    async def outer(workspace_path):
        seen["inner"] = await inner(workspace_path=workspace_path)
        return dict(SUCCESS_CLAIM)

    result = _run(outer, tmp_path)
    assert seen["inner"]["status"] == "success"  # the caller inside the run still sees its own unit's answer
    assert result["status"] == "needs_review" and result["quality_gates_passed"] is False  # the run's answer is reconciled once


def test_the_reconciliation_does_not_mutate_the_workflows_own_dict(tmp_path):
    original = dict(SUCCESS_CLAIM)

    @coordinated_mutation
    async def run(workspace_path):
        transition_mutating_run(current_run_context(), RunLifecycle.UNCERTAIN)
        return original

    result = _run(run, tmp_path)
    assert result is not original and original == SUCCESS_CLAIM


# --- what the person sees ----------------------------------------------------------------------------------------------------------

def test_generate_reports_a_needs_review_result_with_files_as_written_but_unconfirmed_and_exits_nonzero(tmp_path):
    goal = tmp_path / "goal.md"
    goal.write_text("# Goal\nadd a feature\n")
    needs_review = {
        "status": "needs_review", "quality_gates_passed": False, "files": ["app.py"], "reason_codes": ["UNCERTAIN_RUN_RECORD_COMMIT_STATE"],
        "recovery_command": "kriya runs recover", "error": "The run's commit could not be confirmed in its run record.",
        "post_commit_persistence_errors": [{"operation": "run_record_commit_committed", "error": "OSError: disk full"}],
    }
    mock_run = AsyncMock(return_value=needs_review)
    with patch("os.getcwd", return_value=str(tmp_path)), patch("kriya.workflow.workflow.WorkflowEngine.run_generation_workflow", new=mock_run):
        result = CliRunner().invoke(main, ["generate", "-f", str(goal), "-y"])
    assert result.exit_code == 1
    out = result.output
    assert "NOT CONFIRMED" in out and "kriya runs recover" in out and "run_record_commit_committed" in out
    assert "Files written to workspace: app.py" in out
    assert "NOT applied" not in out and "Quality Gates: PASSED" not in out  # the files WERE written: they are never described as not applied

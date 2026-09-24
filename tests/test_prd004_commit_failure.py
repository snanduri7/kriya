"""PRD-004 (reopened): a failure AT the terminal real-workspace commit is a
structured, recorded outcome - never a raw exception - and terminal status can
never disagree with what reached the real workspace.

Every test snapshots the whole real workspace byte-for-byte (excluding Kriya's
own ``.kriya/`` control directory) rather than reading one file.
"""
import os
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import kriya.workflow.workflow_controller as workflow_controller_module
from kriya.control.persistence import list_run_records, load_control_state
from kriya.control.run_record import RunLifecycle
from kriya.workflow.edit_safety import UncertainCommitError
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    Subtask,
)
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow_controller import WorkflowController


def _workflow_engine():
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW,
        max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT,
    ))
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    return we


def _patched(plan):
    return (
        patch("kriya.workflow.workflow_controller.parse_planner_structured_output",
              return_value=(MagicMock(), None)),
        patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
              return_value=plan),
        patch("kriya.workflow.workflow_controller.validate_plan",
              new=AsyncMock(return_value=PlanValidationResult(valid=True))),
    )


def _tree_snapshot(root):
    """Every file under ``root`` except Kriya's own control/candidate state."""
    snapshot = {}
    for directory, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(directory, root)
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d not in (".kriya", "plan-sandbox")]
        for name in filenames:
            path = os.path.join(directory, name)
            with open(path, "rb") as handle:
                snapshot[os.path.relpath(path, root)] = handle.read()
    return snapshot


def _plan(plan_id, *files):
    return EngineeringPlan(
        plan_id=plan_id, kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="change files", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path=path, action=action) for path, action in files],
        )],
    )


@pytest.fixture
def sandboxed(tmp_path, monkeypatch):
    """A distinct candidate copy, so the real terminal commit boundary runs."""
    sandbox = tmp_path / "plan-sandbox"

    def create_plan_sandbox(workspace):
        shutil.copytree(workspace, sandbox, ignore=shutil.ignore_patterns(".kriya", "plan-sandbox"))
        return str(sandbox)

    monkeypatch.setattr("kriya.workflow.workflow_controller.create_git_worktree", create_plan_sandbox)
    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.remove_git_worktree",
        lambda workspace, candidate: shutil.rmtree(candidate),
    )
    return sandbox


def _only_run_record(workspace):
    records = list_run_records(str(workspace))
    assert len(records) == 1, records
    return records[0]


def _no_stage_files(root):
    return [
        os.path.join(directory, name)
        for directory, _, names in os.walk(root) for name in names
        if name.startswith(".kriya-stage-")
    ]


@pytest.mark.asyncio
async def test_real_concurrent_edit_between_gates_and_commit_is_structured_conflict(tmp_path, sandboxed):
    """The conflict is produced by a real workspace edit after the gates ran,
    detected by the real revision-grounded commit - nothing is mocked to raise."""
    (tmp_path / "app.py").write_text("original\n")
    (tmp_path / "keep.txt").write_text("untouched\n")
    plan = _plan("prd004-conflict", ("app.py", FileAction.MODIFY), ("new.py", FileAction.CREATE))

    async def fake_run(**kwargs):
        (sandboxed / "app.py").write_text("verified candidate\n")
        (sandboxed / "new.py").write_text("created\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py", "new.py"]}

    real_derive = workflow_controller_module.ArtifactRegistry.derive_from_workspace

    def last_gate_then_concurrent_edit(registry, workspace, milestone_id):
        derived = real_derive(registry, workspace, milestone_id)
        (tmp_path / "app.py").write_text("edited by the user meanwhile\n")
        return derived

    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.workflow_controller.ArtifactRegistry.derive_from_workspace",
        autospec=True, side_effect=last_gate_then_concurrent_edit,
    ):
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    legacy = result.legacy_result
    assert legacy["status"] == "needs_review"
    assert legacy["quality_gates_passed"] is False
    assert "WORKSPACE_REVISION_CONFLICT" in legacy["reason_codes"]
    assert legacy["workspace_commit_failure"]["workspace_state"] == "UNCHANGED"
    # Nothing from the candidate reached the workspace: the concurrent edit
    # survives and the planned CREATE never happened.
    assert _tree_snapshot(tmp_path) == {
        "app.py": b"edited by the user meanwhile\n", "keep.txt": b"untouched\n",
    }
    assert _no_stage_files(tmp_path) == []
    assert not sandboxed.exists()
    control_state = load_control_state(str(tmp_path))
    assert control_state.subtask_states.get("s1") != "completed"
    record = _only_run_record(tmp_path)
    assert record.lifecycle_state == RunLifecycle.FAILURE
    assert record.commit_intent == "APPLY_VERIFIED_CANDIDATE"
    assert record.commit_result in ("NOT_COMMITTED", "ROLLED_BACK")


@pytest.mark.asyncio
async def test_missing_candidate_file_is_structured_materialization_failure(tmp_path, sandboxed):
    (tmp_path / "app.py").write_text("original\n")
    (tmp_path / "other.py").write_text("other original\n")
    plan = _plan("prd004-materialize", ("app.py", FileAction.MODIFY), ("other.py", FileAction.MODIFY))

    async def fake_run(**kwargs):
        (sandboxed / "other.py").write_text("changed\n")
        os.unlink(sandboxed / "app.py")  # approved MODIFY target vanished
        return {"status": "success", "quality_gates_passed": True, "files": ["other.py"]}

    before = _tree_snapshot(tmp_path)
    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    commit_spy = MagicMock()
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.workflow_controller.commit_revision_grounded_batch", commit_spy,
    ):
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    legacy = result.legacy_result
    assert legacy["status"] == "needs_review"
    assert "CANDIDATE_MATERIALIZATION_FAILED" in legacy["reason_codes"]
    commit_spy.assert_not_called()
    assert _tree_snapshot(tmp_path) == before
    record = _only_run_record(tmp_path)
    assert record.lifecycle_state == RunLifecycle.FAILURE
    assert record.commit_result == "NOT_COMMITTED"
    assert record.commit_intent is None


@pytest.mark.asyncio
async def test_uncertain_commit_is_recorded_uncertain_and_candidate_retained(tmp_path, sandboxed):
    (tmp_path / "app.py").write_text("original\n")
    plan = _plan("prd004-uncertain", ("app.py", FileAction.MODIFY))

    async def fake_run(**kwargs):
        (sandboxed / "app.py").write_text("verified candidate\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}

    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.workflow_controller.commit_revision_grounded_batch",
        side_effect=UncertainCommitError("rollback of app.py failed"),
    ):
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    legacy = result.legacy_result
    assert legacy["status"] == "needs_review"
    assert legacy["quality_gates_passed"] is False
    assert "WORKSPACE_COMMIT_UNCERTAIN" in legacy["reason_codes"]
    failure = legacy["workspace_commit_failure"]
    assert failure["workspace_state"] == "UNCERTAIN"
    # The only copy of the verified bytes is kept for explicit recovery.
    assert failure["retained_candidate_path"] == str(sandboxed)
    assert (sandboxed / "app.py").read_text() == "verified candidate\n"
    record = _only_run_record(tmp_path)
    assert record.lifecycle_state == RunLifecycle.UNCERTAIN
    assert record.commit_result == "UNCERTAIN"
    assert record.commit_transaction_id == result.run_id


@pytest.mark.asyncio
async def test_run_record_failure_after_successful_commit_is_persistence_error_not_exception(
    tmp_path, sandboxed,
):
    (tmp_path / "app.py").write_text("original\n")
    plan = _plan("prd004-post-commit-record", ("app.py", FileAction.MODIFY))

    async def fake_run(**kwargs):
        (sandboxed / "app.py").write_text("verified candidate\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}

    real_transition = workflow_controller_module.transition_mutating_run

    def fail_committed_transition(context, state, **updates):
        if state == RunLifecycle.COMMITTED:
            raise OSError("run record disk full")
        return real_transition(context, state, **updates)

    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.workflow_controller.transition_mutating_run",
        side_effect=fail_committed_transition,
    ):
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    legacy = result.legacy_result
    # The verified candidate did reach the workspace; the record failure is
    # reported as persistence, never as an unverified or failed candidate.
    assert legacy["status"] == "success"
    assert legacy["commit_evidence"]["state"] == "committed"
    assert (tmp_path / "app.py").read_text() == "verified candidate\n"
    assert {
        "operation": "run_record_committed_transition",
        "error": "OSError: run record disk full",
    } in legacy["post_commit_persistence_errors"]

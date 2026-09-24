"""PRD-006 (reopened): the enforce controller's own isolated candidate is part
of the same mutating run.

The blocking defect was invisible to the earlier suite because its fixtures
either mapped ``create_git_worktree`` to the identity function or replaced the
engine with an undecorated fake. These tests use a REAL git repository, the
REAL ``create_git_worktree``/``remove_git_worktree``, and a stand-in engine
whose ``run_generation_workflow`` carries the REAL ``@coordinated_mutation``
wrapper (the production method is proven to carry it too).
"""
import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.control.persistence import list_run_records
from kriya.control.run_coordinator import (
    InvalidRunContextError,
    authorize_candidate_workspace,
    begin_mutating_run,
    coordinated_mutation,
    current_run_context,
)
from kriya.control.run_ownership import WorkspaceLockHeldError
from kriya.control.run_record import RunLifecycle
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import WorkflowController


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "app.py").write_text("original\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _coordinated_engine(route):
    """MagicMock engine (every other attribute the controller touches) whose
    run_generation_workflow carries the real coordinator wrapper."""
    engine = MagicMock()
    engine.engineering_triage.classify = AsyncMock(return_value=route)
    engine.planner.run = AsyncMock(return_value="fake plan text")
    engine.kernel = None
    engine.observed = []

    @coordinated_mutation
    async def run_generation_workflow(goal=None, workspace_path=None, **kwargs):
        context = current_run_context()
        engine.observed.append((workspace_path, context.run_id if context else None))
        with open(f"{workspace_path}/app.py", "w") as handle:
            handle.write("verified candidate\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}

    engine.run_generation_workflow = run_generation_workflow
    return engine


def _route():
    return EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW,
        max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT,
    )


def test_production_engine_method_carries_the_coordinator_wrapper():
    # The stand-in above is only representative if the real method is wrapped.
    assert hasattr(WorkflowEngine.run_generation_workflow, "__wrapped__")


@pytest.mark.asyncio
async def test_enforce_runs_subtasks_in_real_worktree_under_the_same_run(tmp_path):
    repo = _repo(tmp_path)
    plan = EngineeringPlan(
        plan_id="prd006-real-worktree", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="update app", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="app.py", action=FileAction.MODIFY)],
        )],
    )
    engine = _coordinated_engine(_route())
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output",
               return_value=(MagicMock(), None)), \
            patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
                  return_value=plan), \
            patch("kriya.workflow.workflow_controller.validate_plan",
                  new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        result = await WorkflowController(engine).execute(
            "goal", str(repo), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success", result.legacy_result
    # The subtask ran in the isolated candidate, not the real workspace ...
    [(subtask_workspace, nested_run_id)] = engine.observed
    assert subtask_workspace != str(repo) and ".kriya" in subtask_workspace
    # ... as part of the outer run (no second lock, no second record) ...
    [record] = list_run_records(str(repo))
    assert nested_run_id == record.run_id
    # ... and only the terminal commit reached the real workspace.
    assert (repo / "app.py").read_text() == "verified candidate\n"
    assert record.lifecycle_state == RunLifecycle.SUCCESS
    assert record.commit_result == "COMMITTED"


def test_unauthorized_other_workspace_is_still_refused_inside_a_run(tmp_path):
    workspace, other = tmp_path / "ws", tmp_path / "other"
    workspace.mkdir()
    other.mkdir()

    @coordinated_mutation
    async def mutate(workspace_path):
        return "mutated"

    with begin_mutating_run(str(workspace)):
        with pytest.raises(InvalidRunContextError, match="not a candidate workspace"):
            asyncio.run(mutate(workspace_path=str(other)))


def test_authorized_candidate_outside_the_workspace_is_accepted(tmp_path):
    # Authorization is explicit, never inferred from path containment.
    workspace, sandbox = tmp_path / "ws", tmp_path / "snapshot-sandbox"
    workspace.mkdir()
    sandbox.mkdir()

    @coordinated_mutation
    async def mutate(workspace_path):
        return current_run_context().run_id

    with begin_mutating_run(str(workspace)) as context:
        authorize_candidate_workspace(str(sandbox))
        assert asyncio.run(mutate(workspace_path=str(sandbox))) == context.run_id
    # Authorization expires with the run.
    assert current_run_context() is None


def test_authorization_without_an_active_run_is_a_no_op(tmp_path):
    assert authorize_candidate_workspace(str(tmp_path)) is None


def test_lock_and_capability_are_released_even_if_terminal_record_write_fails(tmp_path):
    with patch("kriya.control.run_coordinator._fail_active_run", side_effect=OSError("disk full")):
        with pytest.raises(RuntimeError, match="the run's own error"):
            with begin_mutating_run(str(tmp_path)):
                raise RuntimeError("the run's own error")
    assert current_run_context() is None
    # The flock was released: a new run can own the workspace at once.
    with begin_mutating_run(str(tmp_path)) as again:
        assert again.active


def test_possibly_mutating_tool_call_is_refused_while_workspace_is_owned(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from kriya.cli import main

    monkeypatch.chdir(tmp_path)
    target = tmp_path / "written.txt"
    with begin_mutating_run(str(tmp_path)):
        # Same process, but a fresh CLI invocation is a separate run context.
        import contextvars
        result = contextvars.Context().run(
            CliRunner().invoke, main,
            ["tools", "execute", "filesystem",
             f'{{"operation": "write", "path": "{target}", "content": "x"}}'],
        )
    assert result.exit_code == 1, result.output
    assert "Workspace Locked" in result.output
    assert not target.exists()


def test_read_only_tool_call_takes_no_workspace_lock(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from kriya.cli import main

    monkeypatch.chdir(tmp_path)
    with begin_mutating_run(str(tmp_path)):
        import contextvars
        result = contextvars.Context().run(
            CliRunner().invoke, main,
            ["tools", "execute", "filesystem", f'{{"operation": "list", "path": "{tmp_path}"}}'],
        )
    assert result.exit_code == 0, result.output


def test_workspace_lock_error_type_is_the_existing_diagnostic():
    assert issubclass(WorkspaceLockHeldError, RuntimeError)

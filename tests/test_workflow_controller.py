"""MA6.8/6.9/6.10/6.13/6.14: WorkflowController (kriya/workflow/workflow_controller.py) -
first real pytest coverage for this module (previously only ad hoc-verified,
never as a permanent regression test)."""

import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.workflow.plan_schema import AcceptanceCriterion, EngineeringPlan, ExecutionMethod, Subtask
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.triage import ChangeKind, ExecutionWeight, RiskClass
from kriya.workflow.workflow_controller import WorkflowController, WorkflowControllerConfigurationError
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus


def _route(kind=ChangeKind.TASK):
    route = MagicMock()
    route.kind = kind
    route.execution_weight = ExecutionWeight.LIGHT
    route.max_observed_risk_class = RiskClass.LOW
    return route


def _workflow_engine(route=None, legacy_result=None):
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=route or _route())
    we.run_generation_workflow = AsyncMock(return_value=legacy_result or {"status": "success", "run_id": "legacy-run"})
    return we


# --- migration_mode validation ---

@pytest.mark.asyncio
async def test_enforce_migration_mode_is_rejected():
    controller = WorkflowController(_workflow_engine())
    with pytest.raises(WorkflowControllerConfigurationError, match="not safe yet"):
        await controller.execute("goal", "/tmp/proj", migration_mode="enforce")


@pytest.mark.asyncio
async def test_unknown_migration_mode_is_rejected():
    controller = WorkflowController(_workflow_engine())
    with pytest.raises(WorkflowControllerConfigurationError):
        await controller.execute("goal", "/tmp/proj", migration_mode="bogus")


# --- legacy mode: zero-overhead passthrough, shadow fields stay empty ---

@pytest.mark.asyncio
async def test_legacy_mode_never_touches_the_shadow_path():
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "r1"})
    controller = WorkflowController(we)

    result = await controller.execute("goal", "/tmp/proj", migration_mode="legacy")

    assert result.legacy_result == {"status": "success", "run_id": "r1"}
    assert result.subtask_results == ()
    assert result.decisions == ()
    assert result.verification_report is None
    we.planner.run.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_mode_forwards_legacy_kwargs_unchanged():
    we = _workflow_engine()
    controller = WorkflowController(we)
    approval_cb = MagicMock()

    await controller.execute("goal", "/tmp/proj", migration_mode="legacy", approval_callback=approval_cb, resume=True)

    we.run_generation_workflow.assert_awaited_once_with(
        "goal", "/tmp/proj",
        milestone_group_id=None, milestone_index=None, milestone_total=None,
        approval_callback=approval_cb, resume=True,
    )


# --- per-kind control-plane bookkeeping (MA6.9) ---

@pytest.mark.asyncio
async def test_milestone_kind_attaches_milestone_metadata():
    we = _workflow_engine(route=_route(kind=ChangeKind.MILESTONE))
    controller = WorkflowController(we)

    result = await controller.execute(
        "goal", "/tmp/proj", migration_mode="legacy", milestone_group_id="grp1", milestone_index=2,
    )

    assert result.control_state.milestone_group_id == "grp1"
    assert result.control_state.current_milestone_id == "grp1:2"


@pytest.mark.asyncio
async def test_milestone_kind_with_no_group_id_leaves_state_unchanged():
    we = _workflow_engine(route=_route(kind=ChangeKind.MILESTONE))
    controller = WorkflowController(we)

    result = await controller.execute("goal", "/tmp/proj", migration_mode="legacy")

    assert result.control_state.milestone_group_id is None


@pytest.mark.asyncio
async def test_refactor_kind_attaches_real_git_baseline():
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init"], cwd=d, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=d, capture_output=True)
        with open(f"{d}/a.py", "w") as f:
            f.write("x = 1")
        subprocess.run(["git", "add", "a.py"], cwd=d, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=d, capture_output=True)

        we = _workflow_engine(route=_route(kind=ChangeKind.REFACTOR))
        controller = WorkflowController(we)

        result = await controller.execute("goal", d, migration_mode="legacy")

        assert result.control_state.base_commit is not None
        assert result.control_state.tree_hash is not None


@pytest.mark.asyncio
async def test_task_kind_gets_no_extra_bookkeeping():
    we = _workflow_engine(route=_route(kind=ChangeKind.TASK))
    controller = WorkflowController(we)

    result = await controller.execute("goal", "/tmp/proj", migration_mode="legacy")

    assert result.control_state.milestone_group_id is None
    assert result.control_state.base_commit is None


# --- shadow mode: never blocks or alters the real outcome ---

def _shadow_plan():
    return EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(id="s1", description="do a thing", execution_method=ExecutionMethod.MODEL)],
        acceptance_criteria=[AcceptanceCriterion(id="ac1", description="thing works")],
    )


@pytest.mark.asyncio
async def test_shadow_mode_still_returns_the_real_legacy_result_unchanged():
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "legacy-run"})
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    we.developer = MagicMock()
    fake_result = SubtaskResult(subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model")

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_shadow_plan()), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))), \
         patch("kriya.workflow.workflow_controller.subtask_executor.execute", new=AsyncMock(return_value=fake_result)):

        controller = WorkflowController(we)
        result = await controller.execute("goal", "/tmp/proj", migration_mode="shadow")

    # the real outcome is exactly what the legacy call produced, regardless
    # of what the shadow run did
    assert result.legacy_result == {"status": "success", "run_id": "legacy-run"}
    we.run_generation_workflow.assert_awaited_once()


@pytest.mark.asyncio
async def test_shadow_mode_populates_subtask_results_decisions_and_verification_report():
    we = _workflow_engine()
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    we.developer = MagicMock()
    fake_result = SubtaskResult(subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model")

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_shadow_plan()), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))), \
         patch("kriya.workflow.workflow_controller.subtask_executor.execute", new=AsyncMock(return_value=fake_result)):

        controller = WorkflowController(we)
        result = await controller.execute("goal", "/tmp/proj", migration_mode="shadow")

    assert len(result.subtask_results) == 1
    assert result.subtask_results[0].subtask_id == "s1"
    decision_types = [d.type for d in result.decisions]
    assert "plan_created" in decision_types
    assert "subtask_attempt" in decision_types
    assert result.verification_report is not None
    assert result.control_state.current_plan_hash == _shadow_plan().content_hash()


@pytest.mark.asyncio
async def test_shadow_mode_records_undeclared_file_touch_decision():
    we = _workflow_engine()
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    we.developer = MagicMock()
    fake_result = SubtaskResult(
        subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model", undeclared_files=("surprise.py",),
    )

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_shadow_plan()), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))), \
         patch("kriya.workflow.workflow_controller.subtask_executor.execute", new=AsyncMock(return_value=fake_result)):

        controller = WorkflowController(we)
        result = await controller.execute("goal", "/tmp/proj", migration_mode="shadow")

    assert "subtask_undeclared_file_touch" in [d.type for d in result.decisions]


@pytest.mark.asyncio
async def test_shadow_mode_no_structured_plan_leaves_shadow_fields_empty_but_legacy_still_runs():
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "legacy-run"})
    we.planner.run = AsyncMock(return_value="prose plan, no JSON block")

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(None, "no fenced JSON block found")):
        controller = WorkflowController(we)
        result = await controller.execute("goal", "/tmp/proj", migration_mode="shadow")

    assert result.subtask_results == ()
    assert result.decisions == ()
    assert result.verification_report is None
    assert result.legacy_result == {"status": "success", "run_id": "legacy-run"}


@pytest.mark.asyncio
async def test_shadow_mode_plan_validation_failure_still_lets_legacy_run():
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "legacy-run"})
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_shadow_plan()), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=False, errors=["bad plan"]))):

        controller = WorkflowController(we)
        result = await controller.execute("goal", "/tmp/proj", migration_mode="shadow")

    assert result.subtask_results == ()
    assert result.verification_report is None
    assert result.legacy_result == {"status": "success", "run_id": "legacy-run"}


@pytest.mark.asyncio
async def test_shadow_mode_exception_is_swallowed_and_never_fails_the_real_run():
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "legacy-run"})
    we.planner.run = AsyncMock(side_effect=RuntimeError("planner exploded"))

    controller = WorkflowController(we)
    result = await controller.execute("goal", "/tmp/proj", migration_mode="shadow")

    assert result.legacy_result == {"status": "success", "run_id": "legacy-run"}
    assert result.subtask_results == ()
    assert result.decisions == ()
    assert result.verification_report is None

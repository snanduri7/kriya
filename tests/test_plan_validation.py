"""MA6.2: PlanValidator (kriya/workflow/plan_validation.py) - first real
pytest coverage for this module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
)
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.triage import ChangeKind


def _plan(subtasks, **overrides):
    defaults = dict(plan_id="p1", kind=ChangeKind.TASK, subtasks=subtasks)
    defaults.update(overrides)
    return EngineeringPlan(**defaults)


def _model_subtask(**overrides):
    defaults = dict(id="s1", description="do a thing", execution_method=ExecutionMethod.MODEL)
    defaults.update(overrides)
    return Subtask(**defaults)


@pytest.mark.asyncio
async def test_valid_single_subtask_plan_passes(tmp_path):
    plan = _plan([_model_subtask()])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is True
    assert result.errors == []


@pytest.mark.asyncio
async def test_unknown_depends_on_reference_is_an_error(tmp_path):
    plan = _plan([_model_subtask(id="s1", depends_on=["ghost"])])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False
    assert any("unknown subtask id" in e for e in result.errors)


@pytest.mark.asyncio
async def test_dependency_cycle_is_detected():
    """EngineeringPlan itself allows constructing this (each subtask only
    forbids depending on ITSELF) - the cycle across two subtasks is exactly
    what validate_plan's _acyclic check exists to catch."""
    plan = EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK,
        subtasks=[
            _model_subtask(id="s1", depends_on=["s2"]),
            _model_subtask(id="s2", depends_on=["s1"]),
        ],
    )
    result = await validate_plan(plan, workspace_path="/tmp")
    assert result.valid is False
    assert any("cycle" in e for e in result.errors)


@pytest.mark.asyncio
async def test_acyclic_diamond_dependency_graph_passes(tmp_path):
    plan = EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK,
        subtasks=[
            _model_subtask(id="s1"),
            _model_subtask(id="s2", depends_on=["s1"]),
            _model_subtask(id="s3", depends_on=["s1"]),
            _model_subtask(id="s4", depends_on=["s2", "s3"]),
        ],
    )
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is True


@pytest.mark.asyncio
async def test_modify_action_on_a_nonexistent_file_is_an_error(tmp_path):
    plan = _plan([
        _model_subtask(planned_files=[PlannedFile(path="missing.py", action=FileAction.MODIFY)]),
    ])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False
    assert any("does not exist" in e for e in result.errors)


@pytest.mark.asyncio
async def test_create_action_on_a_nonexistent_file_is_fine(tmp_path):
    plan = _plan([
        _model_subtask(planned_files=[PlannedFile(path="new_file.py", action=FileAction.CREATE)]),
    ])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is True


@pytest.mark.asyncio
async def test_modify_action_on_an_existing_file_is_fine(tmp_path):
    (tmp_path / "existing.py").write_text("x = 1")
    plan = _plan([
        _model_subtask(planned_files=[PlannedFile(path="existing.py", action=FileAction.MODIFY)]),
    ])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is True


@pytest.mark.asyncio
async def test_uncovered_acceptance_criterion_is_an_error(tmp_path):
    plan = _plan(
        [_model_subtask(acceptance_criteria_ids=[])],
        acceptance_criteria=[AcceptanceCriterion(id="ac1", description="works")],
    )
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False
    assert any("not covered by any subtask" in e for e in result.errors)


@pytest.mark.asyncio
async def test_subtask_referencing_unknown_acceptance_criterion_is_an_error(tmp_path):
    plan = _plan([_model_subtask(acceptance_criteria_ids=["ghost-ac"])])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False
    assert any("unknown acceptance_criteria id" in e for e in result.errors)


@pytest.mark.asyncio
async def test_covered_acceptance_criterion_passes(tmp_path):
    plan = _plan(
        [_model_subtask(acceptance_criteria_ids=["ac1"])],
        acceptance_criteria=[AcceptanceCriterion(id="ac1", description="works")],
    )
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is True


@pytest.mark.asyncio
async def test_enhancement_plan_requires_extension_points(tmp_path):
    # a NON-empty workspace (real established content) - the case the
    # extension_points requirement is actually meant to protect
    (tmp_path / "Existing.java").write_text("class Existing {}")
    plan = _plan([_model_subtask()], kind=ChangeKind.ENHANCEMENT, extension_points=[])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False
    assert any("extension_points" in e for e in result.errors)


@pytest.mark.asyncio
async def test_milestone_plan_requires_extension_points(tmp_path):
    (tmp_path / "Existing.java").write_text("class Existing {}")
    plan = _plan([_model_subtask()], kind=ChangeKind.MILESTONE, extension_points=[])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False
    assert any("extension_points" in e for e in result.errors)


@pytest.mark.asyncio
async def test_milestone_plan_on_a_genuinely_empty_workspace_does_not_require_extension_points(tmp_path):
    """MA7.8 fix (2026-08-24, real live-validation finding,
    protocol_encoder_java): a workspace with zero established content has
    no real insertion point ANY plan could name - requiring
    extension_points there was asking for something that structurally
    cannot exist yet, not a justification the Planner failed to give."""
    plan = _plan([_model_subtask()], kind=ChangeKind.MILESTONE, extension_points=[])
    result = await validate_plan(plan, workspace_path=str(tmp_path))  # tmp_path is empty
    assert result.valid is True


@pytest.mark.asyncio
async def test_enhancement_plan_on_a_genuinely_empty_workspace_does_not_require_extension_points(tmp_path):
    plan = _plan([_model_subtask()], kind=ChangeKind.ENHANCEMENT, extension_points=[])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is True


@pytest.mark.asyncio
async def test_milestone_plan_with_real_extension_points_is_valid_even_on_a_non_empty_workspace(tmp_path):
    (tmp_path / "Existing.java").write_text("class Existing {}")
    plan = _plan([_model_subtask()], kind=ChangeKind.MILESTONE, extension_points=["Existing.java#method"])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is True


@pytest.mark.asyncio
async def test_task_plan_does_not_require_extension_points(tmp_path):
    plan = _plan([_model_subtask()], kind=ChangeKind.TASK, extension_points=[])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is True


@pytest.mark.asyncio
async def test_refactor_plan_requires_refactor_baseline(tmp_path):
    plan = _plan([_model_subtask()], kind=ChangeKind.REFACTOR, refactor_baseline=None)
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False
    assert any("refactor_baseline" in e for e in result.errors)


@pytest.mark.asyncio
async def test_refactor_plan_with_baseline_passes(tmp_path):
    plan = _plan([_model_subtask()], kind=ChangeKind.REFACTOR, refactor_baseline="abc123")
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is True


@pytest.mark.asyncio
async def test_unregistered_tool_name_on_subtask_is_rejected(tmp_path):
    plan = _plan([
        Subtask(id="s1", description="lint", execution_method=ExecutionMethod.TOOL, tool_name="nonexistent_tool"),
    ])
    result = await validate_plan(plan, workspace_path=str(tmp_path), available_tool_names=["filesystem", "git"])
    assert result.valid is False
    assert any("unregistered tool_name" in e for e in result.errors)


@pytest.mark.asyncio
async def test_registered_tool_name_on_subtask_passes(tmp_path):
    plan = _plan([
        Subtask(id="s1", description="lint", execution_method=ExecutionMethod.TOOL, tool_name="filesystem"),
    ])
    result = await validate_plan(plan, workspace_path=str(tmp_path), available_tool_names=["filesystem", "git"])
    assert result.valid is True


@pytest.mark.asyncio
async def test_available_tool_names_none_skips_tool_registry_check_entirely(tmp_path):
    plan = _plan([
        Subtask(id="s1", description="lint", execution_method=ExecutionMethod.TOOL, tool_name="nonexistent_tool"),
    ])
    result = await validate_plan(plan, workspace_path=str(tmp_path), available_tool_names=None)
    assert result.valid is True


@pytest.mark.asyncio
async def test_unregistered_tool_name_in_verification_method_is_rejected(tmp_path):
    plan = _plan([
        _model_subtask(verification=[
            VerificationMethod(type=VerificationMethodType.TOOL, description="check", tool_name="ghost_tool"),
        ]),
    ])
    result = await validate_plan(plan, workspace_path=str(tmp_path), available_tool_names=["filesystem"])
    assert result.valid is False
    assert any("verification references unregistered tool_name" in e for e in result.errors)


@pytest.mark.asyncio
async def test_unregistered_tool_name_in_acceptance_criterion_is_rejected(tmp_path):
    plan = _plan(
        [_model_subtask(acceptance_criteria_ids=["ac1"])],
        acceptance_criteria=[
            AcceptanceCriterion(id="ac1", description="compiles", method=VerificationMethodType.TOOL, tool_name="ghost_tool"),
        ],
    )
    result = await validate_plan(plan, workspace_path=str(tmp_path), available_tool_names=["filesystem"])
    assert result.valid is False
    assert any("acceptance criterion 'ac1' references unregistered tool_name" in e for e in result.errors)


@pytest.mark.asyncio
async def test_planned_file_outside_supplied_context_is_rejected(tmp_path):
    plan = _plan([
        _model_subtask(planned_files=[PlannedFile(path="new.py", action=FileAction.CREATE)]),
    ])
    result = await validate_plan(plan, workspace_path=str(tmp_path), context_files=["other.py"])
    assert result.valid is False
    assert any("outside the supplied context package" in e for e in result.errors)


@pytest.mark.asyncio
async def test_planned_file_inside_supplied_context_passes(tmp_path):
    plan = _plan([
        _model_subtask(planned_files=[PlannedFile(path="new.py", action=FileAction.CREATE)]),
    ])
    result = await validate_plan(plan, workspace_path=str(tmp_path), context_files=["new.py", "other.py"])
    assert result.valid is True


@pytest.mark.asyncio
async def test_context_files_none_skips_that_check_entirely(tmp_path):
    plan = _plan([
        _model_subtask(planned_files=[PlannedFile(path="new.py", action=FileAction.CREATE)]),
    ])
    result = await validate_plan(plan, workspace_path=str(tmp_path), context_files=None)
    assert result.valid is True


@pytest.mark.asyncio
async def test_duplicate_subtask_ids_are_rejected(tmp_path):
    plan = EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK,
        subtasks=[_model_subtask(id="s1"), _model_subtask(id="s1")],
    )
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False
    assert any("duplicate subtask ids" in e for e in result.errors)


@pytest.mark.asyncio
async def test_risk_recomputation_skipped_when_route_and_triage_service_not_both_supplied(tmp_path):
    plan = _plan([_model_subtask()])
    result = await validate_plan(plan, workspace_path=str(tmp_path), route=MagicMock())
    assert result.escalated_route is None


@pytest.mark.asyncio
async def test_risk_recomputation_calls_triage_service_with_real_touched_files(tmp_path):
    plan = _plan([
        _model_subtask(planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)]),
    ])
    route = MagicMock()
    escalated = MagicMock()
    triage_service = MagicMock()
    triage_service.recompute_from_files = AsyncMock(return_value=escalated)

    result = await validate_plan(
        plan, workspace_path=str(tmp_path), route=route, triage_service=triage_service,
    )

    triage_service.recompute_from_files.assert_awaited_once_with(
        route=route, workspace_path=str(tmp_path), planned_files=["a.py"],
    )
    assert result.escalated_route is escalated

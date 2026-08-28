"""MA6.2: PlanValidator (kriya/workflow/plan_validation.py) - first real
pytest coverage for this module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
    VerifierKind,
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
async def test_authoritative_validation_rejects_model_subtask_without_planned_files(tmp_path):
    plan = _plan([_model_subtask()])
    result = await validate_plan(
        plan, workspace_path=str(tmp_path), require_model_planned_files=True,
    )
    assert result.valid is False
    assert any("declares no planned_files" in error for error in result.errors)
    assert result.reason_codes == ["MODEL_SUBTASK_MISSING_PLANNED_FILES"]


@pytest.mark.asyncio
async def test_authoritative_validation_exempts_verification_role_from_missing_planned_files(tmp_path):
    """Regression test for PRV-05 (2026-08-28): a genuine, non-mutating
    regression-verification subtask (execution_role=verification) has zero
    planned_files by construction - this must NOT trip the same rule that
    catches a genuinely unbounded IMPLEMENTATION-role subtask. See
    ExecutionRole's own docstring (plan_schema.py) for the full incident:
    the Planner's identical s4 subtask was rejected 3 attempts running,
    because enforce mode had no legal shape for what it was correctly
    trying to express."""
    subtask = _model_subtask(
        execution_role=ExecutionRole.VERIFICATION,
        verification=[VerificationMethod(
            type=VerificationMethodType.TOOL, description="run tests",
            tool_name="test", verifier_kind=VerifierKind.TEST,
        )],
    )
    plan = _plan([subtask])
    result = await validate_plan(
        plan, workspace_path=str(tmp_path), require_model_planned_files=True,
    )
    assert result.valid is True
    assert "MODEL_SUBTASK_MISSING_PLANNED_FILES" not in result.reason_codes


@pytest.mark.asyncio
async def test_legacy_validation_keeps_empty_model_scope_backward_compatible(tmp_path):
    plan = _plan([_model_subtask()])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is True


@pytest.mark.asyncio
async def test_semantic_requirement_requires_provider_dependency_edge(tmp_path):
    provider = _model_subtask(
        id="build", planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
    ).model_copy(update={
        "provides": ["build.dependencies.ready"],
        "relevant_global_invariants": ["required platform dependencies are resolved"],
    })
    consumer = _model_subtask(
        id="app", planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)],
    ).model_copy(update={
        "requires": ["build.dependencies.ready"],
        "relevant_global_invariants": ["required platform dependencies are resolved"],
    })
    plan = _plan([provider, consumer]).model_copy(update={
        "global_invariants": ["required platform dependencies are resolved"],
    })

    result = await validate_plan(
        plan, workspace_path=str(tmp_path), require_semantic_contracts=True,
    )

    assert result.valid is False
    assert "SEMANTIC_DEPENDENCY_EDGE_MISSING" in result.reason_codes


@pytest.mark.asyncio
async def test_semantic_requirement_accepts_unique_provider_with_dependency_edge(tmp_path):
    provider = _model_subtask(
        id="config", planned_files=[PlannedFile(path="config.xml", action=FileAction.CREATE)],
    ).model_copy(update={
        "provides": ["runtime.config.ready"],
        "relevant_global_invariants": ["runtime configuration is externally defined"],
    })
    consumer = _model_subtask(
        id="app", planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)],
    ).model_copy(update={
        "requires": ["runtime.config.ready"], "depends_on": ["config"],
        "relevant_global_invariants": ["runtime configuration is externally defined"],
    })
    plan = _plan([provider, consumer]).model_copy(update={
        "global_invariants": ["runtime configuration is externally defined"],
    })

    result = await validate_plan(
        plan, workspace_path=str(tmp_path), require_semantic_contracts=True,
    )

    assert result.valid is True


@pytest.mark.asyncio
async def test_planned_file_has_exactly_one_owner(tmp_path):
    first = _model_subtask(
        id="s1", planned_files=[PlannedFile(path="shared.json", action=FileAction.CREATE)],
    )
    second = _model_subtask(
        id="s2", planned_files=[PlannedFile(path="shared.json", action=FileAction.CREATE)],
    )
    result = await validate_plan(_plan([first, second]), workspace_path=str(tmp_path))
    assert result.valid is False
    assert "AMBIGUOUS_PLANNED_FILE_OWNERSHIP" in result.reason_codes


@pytest.mark.asyncio
async def test_planned_file_with_dependency_ordered_co_owners_is_not_ambiguous(tmp_path):
    """Regression test for PRV-05 (2026-08-28 rerun): a real dependency-
    migration plan had two STRICTLY SEQUENTIAL stages both legitimately
    declare the same file (an early "identify usages" stage, then a later
    "migrate to the new API" stage depending on it transitively through the
    chain in between) - genuinely safe, since the later stage can only ever
    run after the earlier one's output already exists. Must NOT be flagged
    the same way as two independent/parallel subtasks racing to write the
    same path (see test_planned_file_has_exactly_one_owner above, which
    stays ambiguous - no dependency relationship between its two owners)."""
    (tmp_path / "shared.json").write_text("{}")
    first = _model_subtask(
        id="s1", planned_files=[PlannedFile(path="shared.json", action=FileAction.MODIFY)],
    )
    middle = _model_subtask(
        id="s2", depends_on=["s1"], planned_files=[PlannedFile(path="other.txt", action=FileAction.CREATE)],
    )
    last = _model_subtask(
        id="s3", depends_on=["s2"], planned_files=[PlannedFile(path="shared.json", action=FileAction.MODIFY)],
    )
    result = await validate_plan(_plan([first, middle, last]), workspace_path=str(tmp_path))
    assert result.valid is True
    assert "AMBIGUOUS_PLANNED_FILE_OWNERSHIP" not in result.reason_codes


@pytest.mark.asyncio
async def test_planned_file_with_only_partially_ordered_co_owners_is_still_ambiguous(tmp_path):
    """Three co-owners where two are dependency-ordered but the third has
    NO relationship to either - the set as a whole is still not a single
    unambiguous sequence, so this must stay rejected."""
    (tmp_path / "shared.json").write_text("{}")
    first = _model_subtask(
        id="s1", planned_files=[PlannedFile(path="shared.json", action=FileAction.MODIFY)],
    )
    last = _model_subtask(
        id="s2", depends_on=["s1"], planned_files=[PlannedFile(path="shared.json", action=FileAction.MODIFY)],
    )
    unrelated = _model_subtask(
        id="s3", planned_files=[PlannedFile(path="shared.json", action=FileAction.MODIFY)],
    )
    result = await validate_plan(_plan([first, last, unrelated]), workspace_path=str(tmp_path))
    assert result.valid is False
    assert "AMBIGUOUS_PLANNED_FILE_OWNERSHIP" in result.reason_codes


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
    assert "EXTENSION_POINT_REQUIRED" in result.reason_codes


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
async def test_milestone_plan_resuming_own_established_progress_does_not_require_extension_points(tmp_path):
    """Real live-validation finding, 2026-08-24, protocol_encoder_java: a
    resumed enforce-mode run's fresh re-plan correctly triggered this
    check (the workspace is no longer empty - subtask s1 already wrote a
    real file), but the Planner wasn't prompted about continuation and
    didn't supply extension_points. That validation failure sent the
    resumed run down the legacy whole-goal fallback, which regenerated and
    broke s1's already-working file. The caller (WorkflowController) now
    tells validate_plan the established content is its OWN prior subtask
    output for this same resumed goal, not foreign existing work."""
    (tmp_path / "Protocol.java").write_text("class Protocol {}")
    plan = _plan([_model_subtask()], kind=ChangeKind.MILESTONE, extension_points=[])
    result = await validate_plan(plan, workspace_path=str(tmp_path), resuming_own_established_progress=True)
    assert result.valid is True


@pytest.mark.asyncio
async def test_milestone_plan_not_resuming_still_requires_extension_points_on_non_empty_workspace(tmp_path):
    """The exemption is real and scoped - a plain (non-resume) run against
    a non-empty workspace must still require a real justification;
    resuming_own_established_progress defaults False."""
    (tmp_path / "Existing.java").write_text("class Existing {}")
    plan = _plan([_model_subtask()], kind=ChangeKind.MILESTONE, extension_points=[])
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False


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

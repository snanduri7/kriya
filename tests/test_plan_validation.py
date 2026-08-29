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
    GlobalInvariant,
    IntegrationRelationship,
    IntegrationRelationshipKind,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
    VerifierKind,
)
from kriya.workflow.obligations import ObligationKind, ObligationLedger, ObligationStatus
from kriya.workflow.plan_validation import canonicalize_planned_file_actions, validate_plan
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
        "relevant_global_invariant_ids": ["gi1"],
    })
    consumer = _model_subtask(
        id="app", planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)],
    ).model_copy(update={
        "requires": ["build.dependencies.ready"],
        "relevant_global_invariant_ids": ["gi1"],
    })
    plan = _plan([provider, consumer]).model_copy(update={
        "global_invariants": [GlobalInvariant(id="gi1", statement="required platform dependencies are resolved")],
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
        "relevant_global_invariant_ids": ["gi1"],
    })
    consumer = _model_subtask(
        id="app", planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)],
    ).model_copy(update={
        "requires": ["runtime.config.ready"], "depends_on": ["config"],
        "relevant_global_invariant_ids": ["gi1"],
    })
    plan = _plan([provider, consumer]).model_copy(update={
        "global_invariants": [GlobalInvariant(id="gi1", statement="runtime configuration is externally defined")],
    })

    result = await validate_plan(
        plan, workspace_path=str(tmp_path), require_semantic_contracts=True,
    )

    assert result.valid is True


# --- global invariant referential integrity (PRV-06, 2026-08-28) ---

@pytest.mark.asyncio
async def test_subtask_referencing_unknown_global_invariant_id_is_an_error(tmp_path):
    subtask = _model_subtask(
        id="s1", planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
    ).model_copy(update={"relevant_global_invariant_ids": ["gi_ghost"]})
    plan = _plan([subtask]).model_copy(update={
        "global_invariants": [GlobalInvariant(id="gi1", statement="a real invariant")],
    })

    result = await validate_plan(plan, workspace_path=str(tmp_path))

    assert result.valid is False
    assert result.reason_codes == ["UNKNOWN_GLOBAL_INVARIANT"]
    assert any(
        "gi_ghost" in e and "declared ids are" in e and "gi1" in e for e in result.errors
    )


@pytest.mark.asyncio
async def test_compound_global_invariant_referenced_by_multiple_subtasks_is_valid(tmp_path):
    """Regression test for PRV-06 (2026-08-28): a compound invariant
    ("retrieve the value from that service and print it") legitimately
    decomposes across two subtasks that each only implement HALF of it.
    Before ids existed, each subtask's paraphrased sub-clause could never
    exactly match the compound top-level string, so this exact shape
    non-convergently failed two full bounded repair rounds live. By id,
    both subtasks simply reference the SAME whole invariant - no
    paraphrase, no partial statement, no repair needed."""
    compound = GlobalInvariant(
        id="gi_retrieve_print",
        statement="The application must retrieve the value from that service and print it.",
    )
    retrieve_subtask = _model_subtask(
        id="s2", planned_files=[PlannedFile(path="Service.java", action=FileAction.CREATE)],
    ).model_copy(update={
        "provides": ["svc"], "relevant_global_invariant_ids": ["gi_retrieve_print"],
    })
    print_subtask = _model_subtask(
        id="s3", depends_on=["s2"],
        planned_files=[PlannedFile(path="Main.java", action=FileAction.CREATE)],
    ).model_copy(update={
        "requires": ["svc"], "relevant_global_invariant_ids": ["gi_retrieve_print"],
    })
    plan = _plan([retrieve_subtask, print_subtask]).model_copy(update={
        "global_invariants": [compound],
    })

    result = await validate_plan(
        plan, workspace_path=str(tmp_path), require_semantic_contracts=True,
    )

    assert result.valid is True
    assert "UNKNOWN_GLOBAL_INVARIANT" not in result.reason_codes


@pytest.mark.asyncio
async def test_subtask_with_no_relevant_global_invariants_still_flagged_missing(tmp_path):
    # SUBTASK_GLOBAL_INVARIANTS_MISSING only projects across a real
    # multi-subtask plan (validate_plan's own require_semantic_contracts
    # branch is gated on len(plan.subtasks) > 1) - a single-subtask plan
    # has nothing to "project" invariants across.
    covered = _model_subtask(
        id="s1", planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
    ).model_copy(update={"relevant_global_invariant_ids": ["gi1"]})
    uncovered = _model_subtask(
        id="s2", depends_on=["s1"], planned_files=[PlannedFile(path="b.py", action=FileAction.CREATE)],
    )
    plan = _plan([covered, uncovered]).model_copy(update={
        "global_invariants": [GlobalInvariant(id="gi1", statement="a real invariant")],
    })

    result = await validate_plan(
        plan, workspace_path=str(tmp_path), require_semantic_contracts=True,
    )

    assert result.valid is False
    assert "SUBTASK_GLOBAL_INVARIANTS_MISSING" in result.reason_codes


@pytest.mark.asyncio
async def test_invariant_reference_obligation_recorded_violated_then_satisfied(tmp_path):
    """MA8 binding for global invariant references (PRV-06, 2026-08-28):
    an unknown-id reference is recorded VIOLATED with the subtask as owner;
    once the repair swaps in a declared id, the SAME obligation id flips to
    SATISFIED and - because it just transitioned - relevant_for_preservation
    surfaces it, so the next repair prompt is told to keep it. This is the
    same regression-prevention mechanism already proven for planned-file
    metadata (run #8), now covering invariant references too."""
    gi = GlobalInvariant(id="gi1", statement="a real invariant")
    bad = _model_subtask(
        id="s1", planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
    ).model_copy(update={"relevant_global_invariant_ids": ["gi_ghost"]})
    good = _model_subtask(
        id="s1", planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
    ).model_copy(update={"relevant_global_invariant_ids": ["gi1"]})

    ledger = ObligationLedger()
    await validate_plan(
        _plan([bad]).model_copy(update={"global_invariants": [gi]}),
        workspace_path=str(tmp_path), obligation_ledger=ledger, revision=0,
    )
    assert ledger.current("plan.subtask.s1.invariant_ref.gi_ghost").status == ObligationStatus.VIOLATED
    assert ledger.current("plan.subtask.s1.invariant_ref.gi_ghost").owner_subtask_id == "s1"

    result = await validate_plan(
        _plan([good]).model_copy(update={"global_invariants": [gi]}),
        workspace_path=str(tmp_path), obligation_ledger=ledger, revision=1,
    )
    assert result.valid is True
    satisfied = ledger.current("plan.subtask.s1.invariant_ref.gi1")
    assert satisfied.status == ObligationStatus.SATISFIED

    # Same must_preserve computation workflow_controller.py's real repair
    # loop uses: currently-violated PLAN_STRUCTURAL_VALIDITY ids feed
    # relevant_for_preservation. Revision 0's now-abandoned "gi_ghost"
    # reference is still on record as VIOLATED (nothing re-visits an id no
    # longer referenced) and shares this record's owner_subtask_id="s1",
    # which is what actually surfaces the newly-satisfied "gi1" reference
    # for preservation - not a global truth, an owner-scoped one.
    currently_violated_ids = [
        rec.id for rec in ledger.current_by_kind(ObligationKind.PLAN_STRUCTURAL_VALIDITY)
        if rec.status == ObligationStatus.VIOLATED
    ]
    preserved = ledger.relevant_for_preservation(
        ObligationKind.PLAN_STRUCTURAL_VALIDITY, currently_violated_ids,
    )
    assert satisfied.id in {rec.id for rec in preserved}


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


# --- MA8 (PRV-05 run #8, 2026-08-28): obligation_ledger wiring, reproducing
# the exact hardened-planning-phase incident - a refactor plan whose repair
# loop fixed one constraint per attempt but regressed the other. ---

def _run8_plan(refactor_baseline, s4_action):
    s1 = _model_subtask(
        id="s1", depends_on=[],
        planned_files=[PlannedFile(path="src/main/java/com/example/JsonService.java", action=FileAction.MODIFY)],
    )
    s4 = _model_subtask(
        id="s4", depends_on=["s1"],
        planned_files=[PlannedFile(path="src/test/java/com/example/JsonServiceTest.java", action=s4_action)],
    )
    return _plan([s1, s4], kind=ChangeKind.REFACTOR, refactor_baseline=refactor_baseline)


def _run8_workspace(tmp_path):
    service = tmp_path / "src/main/java/com/example/JsonService.java"
    service.parent.mkdir(parents=True)
    service.write_text("class JsonService {}\n")
    # JsonServiceTest.java deliberately does NOT exist on disk.


@pytest.mark.asyncio
async def test_run8_both_constraints_initially_violated(tmp_path):
    _run8_workspace(tmp_path)
    ledger = ObligationLedger()
    result = await validate_plan(
        _run8_plan(None, FileAction.MODIFY), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=0,
    )
    assert not result.valid
    assert "REFACTOR_BASELINE_MISSING" in result.reason_codes
    assert "PLANNED_FILE_ACTION_MISMATCH" in result.reason_codes
    assert ledger.current("plan.refactor_baseline.non_blank").status == ObligationStatus.VIOLATED
    assert ledger.current(
        "plan.file.src/test/java/com/example/JsonServiceTest.java.action_consistency"
    ).status == ObligationStatus.VIOLATED


@pytest.mark.asyncio
async def test_run8_repair_fixes_one_constraint_and_records_it_satisfied(tmp_path):
    _run8_workspace(tmp_path)
    ledger = ObligationLedger()
    await validate_plan(
        _run8_plan(None, FileAction.MODIFY), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=0,
    )
    result = await validate_plan(
        _run8_plan("", FileAction.CREATE), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=1,
    )
    assert not result.valid
    assert "REFACTOR_BASELINE_MISSING" in result.reason_codes
    assert "PLANNED_FILE_ACTION_MISMATCH" not in result.reason_codes
    assert ledger.current(
        "plan.file.src/test/java/com/example/JsonServiceTest.java.action_consistency"
    ).status == ObligationStatus.SATISFIED


@pytest.mark.asyncio
async def test_run8_regression_detected_and_next_repair_receives_both_conditions(tmp_path):
    """The exact run #8 failure: repair 2 fixes refactor_baseline but
    regresses the already-fixed planned-file action. The ledger must
    detect the regression, and the caller-visible signal (MUST_PRESERVE
    computed from the ledger, oscillation detection) must be available for
    the next repair to receive both conditions."""
    _run8_workspace(tmp_path)
    ledger = ObligationLedger()
    await validate_plan(
        _run8_plan(None, FileAction.MODIFY), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=0,
    )
    await validate_plan(
        _run8_plan("", FileAction.CREATE), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=1,
    )
    result = await validate_plan(
        _run8_plan("s4", FileAction.MODIFY), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=2,
    )
    assert not result.valid
    assert "PLANNED_FILE_ACTION_MISMATCH" in result.reason_codes
    assert "REFACTOR_BASELINE_MISSING" not in result.reason_codes

    regressed_ids = [r.obligation_id for r in ledger.regressions]
    assert "plan.file.src/test/java/com/example/JsonServiceTest.java.action_consistency" in regressed_ids

    oscillating = ledger.oscillating_ids(ObligationKind.PLAN_STRUCTURAL_VALIDITY)
    assert "plan.file.src/test/java/com/example/JsonServiceTest.java.action_consistency" in oscillating

    # A next repair honoring BOTH conditions simultaneously must fully pass.
    final = await validate_plan(
        _run8_plan("s4", FileAction.CREATE), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=3,
    )
    assert final.valid, final.errors


# --- Batch 1 (spec §12/§20): owner_subtask_id/terminal_required on the
# recorded obligations, and MUST_PRESERVE relevance filtering fed by the
# real validate_plan() pipeline (not a hand-built ledger). ---

@pytest.mark.asyncio
async def test_plan_structural_obligations_carry_owner_and_terminal_required(tmp_path):
    _run8_workspace(tmp_path)
    ledger = ObligationLedger()
    await validate_plan(
        _run8_plan(None, FileAction.MODIFY), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=0,
    )
    action_rec = ledger.current(
        "plan.file.src/test/java/com/example/JsonServiceTest.java.action_consistency"
    )
    assert action_rec.owner_subtask_id == "s4"
    assert action_rec.terminal_required is True

    baseline_rec = ledger.current("plan.refactor_baseline.non_blank")
    assert baseline_rec.owner_subtask_id is None  # plan-level, not owned by a single subtask
    assert baseline_rec.terminal_required is True

    ownership_rec = ledger.current("plan.file.src/main/java/com/example/JsonService.java.ownership")
    assert ownership_rec.owner_subtask_id == "s1"
    assert ownership_rec.terminal_required is True


@pytest.mark.asyncio
async def test_must_preserve_includes_just_fixed_obligation_ahead_of_next_repair(tmp_path):
    """The run-8-shaped scenario, but checking the ACTUAL MUST_PRESERVE
    input WorkflowController would compute (relevant_for_preservation),
    not just the raw ledger state - confirms the just-fixed action-
    consistency obligation would be told to a repair-2 prompt even though
    only refactor_baseline is currently violated."""
    _run8_workspace(tmp_path)
    ledger = ObligationLedger()
    await validate_plan(
        _run8_plan(None, FileAction.MODIFY), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=0,
    )
    await validate_plan(
        _run8_plan("", FileAction.CREATE), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=1,
    )
    violated_ids = [
        r.id for r in ledger.current_by_kind(ObligationKind.PLAN_STRUCTURAL_VALIDITY)
        if r.status == ObligationStatus.VIOLATED
    ]
    assert violated_ids == ["plan.refactor_baseline.non_blank"]
    preserve_ids = [
        r.id for r in ledger.relevant_for_preservation(ObligationKind.PLAN_STRUCTURAL_VALIDITY, violated_ids)
    ]
    assert "plan.file.src/test/java/com/example/JsonServiceTest.java.action_consistency" in preserve_ids


@pytest.mark.asyncio
async def test_unresolved_terminal_obligations_empty_once_plan_fully_valid(tmp_path):
    _run8_workspace(tmp_path)
    ledger = ObligationLedger()
    result = await validate_plan(
        _run8_plan("s4", FileAction.CREATE), workspace_path=str(tmp_path),
        obligation_ledger=ledger, revision=0,
    )
    assert result.valid, result.errors
    assert ledger.unresolved_terminal_obligations() == []


# --- Correctness Continuity Part C (PRV-06, 2026-08-29): plan.
# integration_relationships / ObligationKind.CROSS_SUBTASK_INTEGRATION.
# See IntegrationRelationship's own docstring (plan_schema.py) for the live
# incident - two sibling subtasks each independently satisfied their own
# local goal_spec_compliance while never composing into one behavior. ---

@pytest.mark.asyncio
async def test_integration_relationship_unknown_subtask_id_is_an_error(tmp_path):
    plan = _plan([_model_subtask(id="s2", planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)])]).model_copy(update={
        "integration_relationships": [IntegrationRelationship(
            id="r1", kind=IntegrationRelationshipKind.USES,
            producer_subtask_ids=["s_ghost"], consumer_subtask_ids=["s2"],
            relationship_statement="App must use a producer that doesn't exist",
        )],
    })

    result = await validate_plan(plan, workspace_path=str(tmp_path))

    assert result.valid is False
    assert result.reason_codes == ["INTEGRATION_RELATIONSHIP_UNKNOWN_SUBTASK"]
    assert any("s_ghost" in e for e in result.errors)


@pytest.mark.asyncio
async def test_valid_integration_relationship_seeds_pending_terminal_obligation(tmp_path):
    s2 = _model_subtask(id="s2", planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)])
    s3 = _model_subtask(id="s3", planned_files=[PlannedFile(path="InMemoryService.java", action=FileAction.CREATE)])
    plan = _plan([s2, s3]).model_copy(update={
        "integration_relationships": [IntegrationRelationship(
            id="r1", kind=IntegrationRelationshipKind.USES,
            producer_subtask_ids=["s3"], consumer_subtask_ids=["s2"],
            relationship_statement="App.java must use InMemoryService.java",
        )],
    })
    ledger = ObligationLedger()

    result = await validate_plan(plan, workspace_path=str(tmp_path), obligation_ledger=ledger, revision=0)

    assert result.valid, result.errors
    rec = ledger.current("plan.integration.r1")
    assert rec is not None
    assert rec.status == ObligationStatus.PENDING
    assert rec.terminal_required is True
    assert rec.kind == ObligationKind.CROSS_SUBTASK_INTEGRATION
    # A PENDING, terminal_required obligation is unresolved by construction -
    # the plan is not yet globally correct just because it's structurally valid.
    assert "plan.integration.r1" in [r.id for r in ledger.unresolved_terminal_obligations()]


@pytest.mark.asyncio
async def test_revalidating_the_same_plan_does_not_clobber_a_settled_integration_status(tmp_path):
    """A plan-repair loop (or any caller) may call validate_plan() again on
    the SAME plan after subtask execution has already resolved an
    integration obligation - the seeding step must never re-record PENDING
    over an already-SATISFIED/VIOLATED status (Part A's own evidence-
    monotonicity spirit applied to seeding, not just re-judgment)."""
    from kriya.workflow.obligations import ObligationAuthority, ObligationRecord

    s2 = _model_subtask(id="s2", planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)])
    s3 = _model_subtask(id="s3", planned_files=[PlannedFile(path="InMemoryService.java", action=FileAction.CREATE)])
    plan = _plan([s2, s3]).model_copy(update={
        "integration_relationships": [IntegrationRelationship(
            id="r1", kind=IntegrationRelationshipKind.USES,
            producer_subtask_ids=["s3"], consumer_subtask_ids=["s2"],
            relationship_statement="App.java must use InMemoryService.java",
        )],
    })
    ledger = ObligationLedger()
    await validate_plan(plan, workspace_path=str(tmp_path), obligation_ledger=ledger, revision=0)
    ledger.record(ObligationRecord(
        id="plan.integration.r1", kind=ObligationKind.CROSS_SUBTASK_INTEGRATION,
        status=ObligationStatus.VIOLATED, authority=ObligationAuthority.DETERMINISTIC,
        description="d", source="workflow_controller.integration_check", revision=1,
        evidence={"missing_producer_references": ["InMemoryService.java"]}, terminal_required=True,
    ))

    await validate_plan(plan, workspace_path=str(tmp_path), obligation_ledger=ledger, revision=1)

    assert ledger.current("plan.integration.r1").status == ObligationStatus.VIOLATED


# --- canonicalize_planned_file_actions (PRV-05 run #10, 2026-08-28): the
# planner declared action=modify for a test file that did not yet exist and
# reproduced that same wrong value across two full repair rounds despite
# explicit, evidence-grounded correction instructions - confirmed via the
# live run's own persisted planning diagnostics (repository_evidence
# consistent throughout, same single obligation violated identically all
# three revisions). create/modify is fully derivable from os.path.exists(),
# so it should never need a repair round at all. ---

def test_canonicalize_corrects_modify_to_create_for_nonexistent_file(tmp_path):
    _run8_workspace(tmp_path)
    original = _run8_plan("s4", FileAction.MODIFY)
    corrected, corrections = canonicalize_planned_file_actions(original, str(tmp_path))
    assert corrected.subtask_by_id("s4").planned_files[0].action == FileAction.CREATE
    assert len(corrections) == 1
    assert "src/test/java/com/example/JsonServiceTest.java" in corrections[0]
    # The input plan is never mutated - only the returned copy is corrected.
    assert original.subtask_by_id("s4").planned_files[0].action == FileAction.MODIFY


def test_canonicalize_corrects_create_to_modify_for_existing_file(tmp_path):
    _run8_workspace(tmp_path)
    original = _run8_plan("s4", FileAction.CREATE)  # s4's own action is already correct
    original.subtask_by_id("s1").planned_files[0].action = FileAction.CREATE  # forced wrong: JsonService.java DOES exist on disk
    corrected, corrections = canonicalize_planned_file_actions(original, str(tmp_path))
    assert corrected.subtask_by_id("s1").planned_files[0].action == FileAction.MODIFY
    assert len(corrections) == 1


def test_canonicalize_leaves_already_correct_actions_unchanged(tmp_path):
    _run8_workspace(tmp_path)
    plan = _run8_plan("s4", FileAction.CREATE)
    corrected, corrections = canonicalize_planned_file_actions(plan, str(tmp_path))
    assert corrections == []
    assert corrected.subtask_by_id("s1").planned_files[0].action == FileAction.MODIFY
    assert corrected.subtask_by_id("s4").planned_files[0].action == FileAction.CREATE


def test_canonicalize_never_touches_delete_even_when_path_missing(tmp_path):
    _run8_workspace(tmp_path)
    plan = _run8_plan("s4", FileAction.DELETE)
    corrected, corrections = canonicalize_planned_file_actions(plan, str(tmp_path))
    assert corrections == []
    assert corrected.subtask_by_id("s4").planned_files[0].action == FileAction.DELETE


def test_canonicalize_does_not_leak_correction_across_repeated_calls_on_the_same_input(tmp_path):
    """Regression guard for the non-mutating contract itself: calling
    canonicalize_planned_file_actions twice against the SAME input plan
    object, against two different baseline states (e.g. a test double or
    resume path that reuses one plan instance across calls), must not let
    the first call's correction leak into what the second call sees as the
    plan's original declared action."""
    _run8_workspace(tmp_path)
    plan = _run8_plan("s4", FileAction.MODIFY)  # file does not exist yet
    canonicalize_planned_file_actions(plan, str(tmp_path))
    assert plan.subtask_by_id("s4").planned_files[0].action == FileAction.MODIFY

    (tmp_path / "src/test/java/com/example/JsonServiceTest.java").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "src/test/java/com/example/JsonServiceTest.java").write_text("class JsonServiceTest {}\n")
    corrected, corrections = canonicalize_planned_file_actions(plan, str(tmp_path))
    assert corrected.subtask_by_id("s4").planned_files[0].action == FileAction.MODIFY
    assert corrections == []


@pytest.mark.asyncio
async def test_canonicalize_then_validate_plan_closes_run10_incident_without_repair(tmp_path):
    """The exact run #10 shape, end to end: a fresh plan where the planner
    declared action=modify for a file that does not exist yet - after
    canonicalization runs (as the real caller now does before validate_plan),
    the plan must validate clean on the very first attempt, with zero
    PLANNED_FILE_ACTION_MISMATCH and zero VIOLATED action_consistency
    obligation - no repair round required at all."""
    _run8_workspace(tmp_path)
    plan = _run8_plan("s4", FileAction.MODIFY)
    plan, _ = canonicalize_planned_file_actions(plan, str(tmp_path))
    ledger = ObligationLedger()
    result = await validate_plan(
        plan, workspace_path=str(tmp_path), obligation_ledger=ledger, revision=0,
    )
    assert result.valid, result.errors
    assert "PLANNED_FILE_ACTION_MISMATCH" not in result.reason_codes
    assert ledger.current(
        "plan.file.src/test/java/com/example/JsonServiceTest.java.action_consistency"
    ).status == ObligationStatus.SATISFIED

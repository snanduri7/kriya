"""MA7.8: WorkflowController migration_mode="enforce" (kriya/workflow/
workflow_controller.py::_run_structured_enforce) - the first mode where
WorkflowController actually owns the real outcome. Reuses
run_generation_workflow() once per subtask (the same real pattern
kriya/workflow/milestones.py::run_milestones() already uses per milestone)
rather than reimplementing edit-application/verification/approval."""

import json
import os
import shutil
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.control.persistence import load_approved_plan, load_control_state
from kriya.control.state import ControlState
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    GlobalInvariant,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
    VerifierKind,
)
from kriya.workflow.plan_validation import PlanValidationResult, validate_plan
from kriya.workflow.planning_diagnostics import (
    approved_plan_diagnostic,
    normalized_ownership_validation_records,
    persist_planning_attempt_diagnostic,
    planning_diagnostics_path,
)
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.workflow_controller import (
    AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
    _StructuredPlanUnavailable,
    ArtifactOwnerResolutionBasis,
    WorkflowController,
    _attempt_owner_recovery_self_correction,
    _authoritative_planner_extension_candidates,
    _build_owner_recovery_context,
    _cross_owner_obligation_id,
    _evaluate_integration_obligations,
    _get_or_create_cross_owner_obligation,
    _integration_reference_token,
    _order_recovery_groups,
    _scope_conflict_evidence_authority,
    _transitive_upstream_ids,
    build_authoritative_planner_request,
    build_planning_structural_evidence,
    find_missing_grounded_production_artifacts,
    build_recovery_execution_plan,
    resolve_python_module_to_candidate_artifact_path,
    resolve_runtime_plan_gap_owner,
    revise_plan_for_planned_prerequisite,
    revise_plan_for_runtime_plan_gap,
    build_subtask_constraint_context,
    build_subtask_goal_text,
    build_subtask_semantic_context,
    build_structured_plan_repair_prompt,
    derive_recovery_participants,
    resolve_effective_artifact_owner,
    resolve_effective_scope_conflict_owners,
    resolve_scope_conflict_owners,
    revise_plan_for_grounded_scope_owner,
)
from kriya.workflow.recovery_plan import RecoveryAction, RecoveryParticipant, RecoveryParticipantRole
from kriya.workflow.self_correction import SelfCorrectionResult
from kriya.workflow.workflow_types import SubtaskStatus
from kriya.workflow.attribution import (
    FutureOwnerVerificationDeferral,
    resolve_future_owner_verification_deferral,
)
from kriya.workflow.workflow import (
    _record_future_owner_verification_deferred,
    _settle_future_owner_verification_obligations,
)


@pytest.fixture(autouse=True)
def _keep_mocked_controller_tests_on_their_explicit_workspace(monkeypatch):
    """Most tests mock the entire subtask workflow and isolate orchestration.

    Transactional-sandbox tests below override this with a distinct copy and
    therefore exercise the real terminal apply/discard boundary directly.
    """
    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.create_git_worktree", lambda workspace: workspace,
    )


def _route(kind=ChangeKind.TASK):
    return EngineeringRoute(
        kind=kind, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    )


def _workflow_engine(route=None):
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=route or _route())
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    return we


def test_scope_conflict_owner_resolution_accepts_unique_transitive_upstream_owner():
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="owner", description="implement behavior",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="src/Owner.py", action=FileAction.MODIFY)],
            ),
            Subtask(
                id="adapter", description="adapt integration",
                execution_method=ExecutionMethod.MODEL, depends_on=["owner"],
                planned_files=[PlannedFile(path="src/Adapter.py", action=FileAction.MODIFY)],
            ),
            Subtask(
                id="consumer", description="verify behavior",
                execution_method=ExecutionMethod.MODEL, depends_on=["adapter"],
                planned_files=[PlannedFile(path="tests/test_owner.py", action=FileAction.MODIFY)],
            ),
        ],
    )

    assert resolve_scope_conflict_owners(
        plan, ["src/Owner.py"], plan.subtask_by_id("consumer"),
    ) == {"owner": ["src/Owner.py"]}


# --- FUTURE_OWNER_VERIFICATION deferral (PRV-11, 2026-08-30) ---
# The live incident: s1 (Customer.java)'s own full-regression check kept
# failing on CustomerControllerTest.detailsIncludesUppercaseDisplayName -
# real evidence, but of a requirement s1's own scope can never satisfy
# alone. The already-approved plan's own provides/requires graph already
# proved this: s4 (the failing test's own owner) requires exactly the
# capability s3 (CustomerController.java, not yet executed) provides.
# Nothing consulted that graph before retrying s1. These tests match the
# 8-scenario matrix required for this fix, in order.

_S1_CUSTOMER = "src/main/java/com/example/customer/Customer.java"
_S2_SERVICE = "src/main/java/com/example/customer/CustomerService.java"
_S3_CONTROLLER = "src/main/java/com/example/customer/CustomerController.java"
_S4_TEST = "src/test/java/com/example/customer/CustomerControllerTest.java"

_DISPLAY_NAME_FAILURE_OUTPUT = (
    "org.opentest4j.AssertionFailedError: expected: <JOHN SMITH> but was: <null>\n"
    "\tat org.junit.jupiter.api.Assertions.assertEquals(Assertions.java:1145)\n"
    "\tat com.example.customer.CustomerControllerTest.detailsIncludesUppercaseDisplayName"
    "(CustomerControllerTest.java:8)\n"
    "\tat java.base/java.lang.reflect.Method.invoke(Method.java:568)\n"
)


def _prv11_style_plan() -> EngineeringPlan:
    """The exact real PRV-11 shape: s1 Customer -> s2 CustomerService ->
    s3 CustomerController -> s4 CustomerControllerTest, a linear
    provides/requires chain matching the real persisted plan verbatim."""
    return EngineeringPlan(
        plan_id="prv11", kind=ChangeKind.ENHANCEMENT,
        subtasks=[
            Subtask(
                id="s1", description="add displayName to Customer",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=_S1_CUSTOMER, action=FileAction.MODIFY)],
                provides=["customer_entity_with_display_name"],
            ),
            Subtask(
                id="s2", description="compute displayName in CustomerService",
                execution_method=ExecutionMethod.MODEL, depends_on=["s1"],
                planned_files=[PlannedFile(path=_S2_SERVICE, action=FileAction.MODIFY)],
                requires=["customer_entity_with_display_name"],
                provides=["customer_service_with_display_name_logic"],
            ),
            Subtask(
                id="s3", description="propagate displayName in CustomerController",
                execution_method=ExecutionMethod.MODEL, depends_on=["s2"],
                planned_files=[PlannedFile(path=_S3_CONTROLLER, action=FileAction.MODIFY)],
                requires=["customer_service_with_display_name_logic"],
                provides=["customer_controller_with_display_name_response"],
            ),
            Subtask(
                id="s4", description="test displayName in the controller response",
                execution_method=ExecutionMethod.MODEL, depends_on=["s3"],
                planned_files=[PlannedFile(path=_S4_TEST, action=FileAction.MODIFY)],
                requires=["customer_controller_with_display_name_response"],
            ),
        ],
    )


def test_future_owner_verification_defers_exact_multi_hop_capability_chain():
    """(1) The literal live incident shape: s1 is executing, the only
    locator in the raw output is the test's own assertion call site, and
    the chain evidence_path(s4) -> s4.requires -> providers[capability] ->
    s3 resolves exactly and uniquely. s3 is genuinely FUTURE_ORDERED (not
    yet completed) relative to s1 - defer."""
    plan = _prv11_style_plan()

    deferral = resolve_future_owner_verification_deferral(
        plan, "s1", _DISPLAY_NAME_FAILURE_OUTPUT, frozenset(),
    )

    assert deferral == FutureOwnerVerificationDeferral(
        verification_subtask_id="s4",
        evidence_path=_S4_TEST,
        required_capability="customer_controller_with_display_name_response",
        future_owner_id="s3",
    )


def test_future_owner_verification_does_not_defer_unrelated_already_satisfied_capability():
    """(2) The failing evidence still resolves to a future-owned test file
    (s4), but s4's OWN declared requirement resolves to a capability that
    is ALREADY satisfied (s1, completed) - nothing pending explains this
    failure, so it must not be deferred to anything."""
    plan = _prv11_style_plan()
    plan = plan.model_copy(deep=True)
    plan.subtask_by_id("s4").requires = ["customer_entity_with_display_name"]  # s1's own capability

    deferral = resolve_future_owner_verification_deferral(
        plan, "s2", _DISPLAY_NAME_FAILURE_OUTPUT, frozenset({"s1"}),
    )

    assert deferral is None


def test_future_owner_verification_does_not_defer_on_missing_provider():
    """(3) s4 requires a capability nothing in the plan provides at all -
    fail closed, never guess a provider."""
    plan = _prv11_style_plan()
    plan = plan.model_copy(deep=True)
    plan.subtask_by_id("s4").requires = ["nonexistent_capability"]

    deferral = resolve_future_owner_verification_deferral(
        plan, "s1", _DISPLAY_NAME_FAILURE_OUTPUT, frozenset(),
    )

    assert deferral is None


def test_future_owner_verification_fails_closed_on_multiple_providers_for_same_capability():
    """(4) A plan-authoring ambiguity - two subtasks both declare the same
    `provides` capability s4 requires. There is no unique provider to
    defer to, so this must fail closed rather than guess between them."""
    plan = _prv11_style_plan()
    plan = plan.model_copy(deep=True)
    duplicate_provider = Subtask(
        id="s3b", description="an ambiguous second provider of the same capability",
        execution_method=ExecutionMethod.MODEL, depends_on=["s2"],
        planned_files=[PlannedFile(path="src/main/java/com/example/customer/Other.java", action=FileAction.CREATE)],
        provides=["customer_controller_with_display_name_response"],
    )
    plan.subtasks.append(duplicate_provider)

    deferral = resolve_future_owner_verification_deferral(
        plan, "s1", _DISPLAY_NAME_FAILURE_OUTPUT, frozenset(),
    )

    assert deferral is None


def test_future_owner_verification_does_not_defer_when_future_owner_already_completed():
    """(5) s3 has ALREADY executed (completed_subtask_ids includes it) by
    the time this failure is observed - there is no pending future work
    left to explain it, so it must not be deferred."""
    plan = _prv11_style_plan()

    deferral = resolve_future_owner_verification_deferral(
        plan, "s1", _DISPLAY_NAME_FAILURE_OUTPUT, frozenset({"s3"}),
    )

    assert deferral is None


def test_future_owner_verification_obligation_settles_satisfied_when_future_owner_later_passes():
    """(6) s1 defers to s3; s3 later executes and its OWN full regression
    genuinely passes - the pending obligation settles SATISFIED, and the
    global terminal-obligation safety net sees nothing left unresolved."""
    ledger = ObligationLedger()
    deferral = FutureOwnerVerificationDeferral(
        verification_subtask_id="s4", evidence_path=_S4_TEST,
        required_capability="customer_controller_with_display_name_response",
        future_owner_id="s3",
    )

    record = _record_future_owner_verification_deferred(
        ledger, deferral, observed_by_subtask_id="s1", raw_output=_DISPLAY_NAME_FAILURE_OUTPUT,
    )
    assert record.status == ObligationStatus.PENDING
    assert record.owner_subtask_id == "s3"
    assert record.terminal_required is True
    assert ledger.unresolved_terminal_obligations() == [record]

    settled = _settle_future_owner_verification_obligations(ledger, "s3", satisfied=True)

    assert len(settled) == 1
    assert settled[0].id == record.id
    assert settled[0].status == ObligationStatus.SATISFIED
    assert ledger.current(record.id).status == ObligationStatus.SATISFIED
    assert ledger.unresolved_terminal_obligations() == []


def test_future_owner_verification_obligation_settles_violated_when_future_owner_still_fails():
    """(7) s1 defers to s3; s3 later executes but its OWN full regression
    STILL fails for the same evidence - the obligation settles VIOLATED
    (never silently cleared), the global terminal-obligation safety net
    still sees it unresolved, and the resolver itself refuses to defer
    AGAIN for s3's own execution (self-deferral is impossible by
    construction) - s3's own ordinary retry/attribution path is what
    "recovers against the now-responsible owner," not a second deferral."""
    ledger = ObligationLedger()
    deferral = FutureOwnerVerificationDeferral(
        verification_subtask_id="s4", evidence_path=_S4_TEST,
        required_capability="customer_controller_with_display_name_response",
        future_owner_id="s3",
    )
    record = _record_future_owner_verification_deferred(
        ledger, deferral, observed_by_subtask_id="s1", raw_output=_DISPLAY_NAME_FAILURE_OUTPUT,
    )

    settled = _settle_future_owner_verification_obligations(ledger, "s3", satisfied=False)

    assert settled[0].status == ObligationStatus.VIOLATED
    assert ledger.current(record.id).status == ObligationStatus.VIOLATED
    unresolved = ledger.unresolved_terminal_obligations()
    assert len(unresolved) == 1 and unresolved[0].id == record.id

    plan = _prv11_style_plan()
    replay_deferral = resolve_future_owner_verification_deferral(
        plan, "s3", _DISPLAY_NAME_FAILURE_OUTPUT, frozenset({"s1", "s2"}),
    )
    assert replay_deferral is None


def test_future_owner_verification_leaves_existing_scope_recovery_case_unaffected():
    """(8) The ORIGINAL PRV-11 scope-recovery shape (already fixed,
    §11.16/§11.18): a genuine locator names an existing production file
    (CustomerService.java, s2's own file) while s1 is executing. s2's own
    `requires` resolves to s1 - but s1 is exactly the CURRENTLY executing
    subtask, so this correctly refuses to defer (self-deferral guard) and
    falls straight through to the existing attribute_failure()/plan_scope_
    conflict path, completely unchanged - this new mechanism never
    intercepts the case it wasn't built for."""
    plan = _prv11_style_plan()
    compile_failure_output = (
        "[ERROR] /work/src/main/java/com/example/customer/CustomerService.java:[23,5] "
        "cannot find symbol\n"
    )

    deferral = resolve_future_owner_verification_deferral(
        plan, "s1", compile_failure_output, frozenset(),
    )

    assert deferral is None



@pytest.mark.asyncio
async def test_grounded_controller_revises_and_revalidates_service_only_scope(tmp_path):
    service = "src/main/java/example/CustomerService.java"
    controller = "src/main/java/example/CustomerController.java"
    test_file = "src/test/java/example/CustomerControllerTest.java"
    for path in (service, controller, test_file):
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("class Placeholder {}\n")

    plan = EngineeringPlan(
        plan_id="scope-recovery", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="Preserve the existing endpoint architecture.")],
        subtasks=[
            Subtask(
                id="s2", description="update service behavior",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=service, action=FileAction.MODIFY)],
                provides=["customer behavior"],
                relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="s3", description="compose endpoint response",
                execution_method=ExecutionMethod.MODEL, depends_on=["s2"],
                planned_files=[PlannedFile(path=controller, action=FileAction.MODIFY)],
                requires=["customer behavior"], provides=["endpoint response"],
                relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="s4", description="extend response coverage",
                execution_method=ExecutionMethod.MODEL, depends_on=["s3"],
                planned_files=[PlannedFile(path=test_file, action=FileAction.MODIFY)],
                requires=["endpoint response"],
                relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )

    revised = revise_plan_for_grounded_scope_owner(
        plan, "s2", [controller], str(tmp_path),
    )
    validation = await validate_plan(
        revised,
        workspace_path=str(tmp_path),
        route=_route(ChangeKind.TASK),
        require_model_planned_files=True,
        require_semantic_contracts=True,
    )

    assert validation.valid, validation.errors
    assert [pf.path for pf in revised.subtask_by_id("s2").planned_files] == [
        service, controller,
    ]
    assert revised.subtask_by_id("s3") is None
    assert revised.subtask_by_id("s4").depends_on == ["s2"]
    assert revised.file_owner(controller).id == "s2"
    assert revised.file_owner(controller).planned_files[1].reason == (
        "deterministically grounded architectural owner"
    )


def _two_subtask_plan():
    return EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="write b.py", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"], planned_files=[PlannedFile(path="b.py", action=FileAction.CREATE)],
            ),
        ],
    )


def _patched(plan, validation_result=None):
    return (
        patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)),
        patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan),
        patch(
            "kriya.workflow.workflow_controller.validate_plan",
            new=AsyncMock(return_value=validation_result or PlanValidationResult(valid=True)),
        ),
    )


def test_subtask_goal_preserves_overall_constraints_and_mapped_acceptance_without_expanding_scope():
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="Create the build manifest", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
            acceptance_criteria_ids=["ac1"],
        )],
        acceptance_criteria=[AcceptanceCriterion(
            id="ac1", description="All libraries required by the requested application are resolved",
        )],
    )

    rendered = build_subtask_goal_text(
        plan.subtasks[0], 1, 1,
        plan=plan,
    )
    constraints = build_subtask_constraint_context(
        "Build an application using framework X and integration Y.",
    )

    assert "All libraries required" in rendered
    assert "pom.xml (create)" in rendered
    assert "framework X and integration Y" in constraints
    assert "only its approved files" in constraints
    assert "not this stage's completion scope" in constraints


def test_subtask_goal_text_without_grounding_goal_is_unchanged():
    """Backward compatibility: a caller not passing grounding_goal (its
    default is "") gets the exact prior flat return - no section headers,
    no behavior change for that call shape."""
    from kriya.agents.contracts import AUTHORITATIVE_GOAL_SECTION_HEADER
    subtask = Subtask(
        id="s1", description="do a thing", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
    )
    rendered = build_subtask_goal_text(subtask, 1, 1)
    assert AUTHORITATIVE_GOAL_SECTION_HEADER not in rendered
    assert rendered == "do a thing\n\nFiles this subtask should touch:\n- a.py (create)"


def test_subtask_goal_text_separates_authoritative_goal_from_planner_strategy():
    """PRV-11 authority-isolation fix (2026-08-30): when grounding_goal (the
    real, unmediated top-level user request) is supplied, it must appear
    under its own labeled section, separate from the Planner's own
    subtask.description/acceptance/planned_files/verification - and a word
    the Planner introduced (here: "field") that never appears in the
    grounding_goal itself must NOT appear on the Authoritative Goal side of
    that split."""
    from kriya.agents.contracts import AUTHORITATIVE_GOAL_SECTION_HEADER, PLANNED_IMPLEMENTATION_SECTION_HEADER
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="Modify Customer.java to add a displayName field.",
            execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="Customer.java", action=FileAction.MODIFY)],
            acceptance_criteria_ids=["ac1"],
        )],
        acceptance_criteria=[AcceptanceCriterion(
            id="ac1", description="Customer returns a displayName field that is uppercase",
        )],
    )
    rendered = build_subtask_goal_text(
        plan.subtasks[0], 1, 1, plan=plan,
        grounding_goal="Add an uppercase displayName derived from the existing customer name fields.",
    )
    assert AUTHORITATIVE_GOAL_SECTION_HEADER in rendered
    assert PLANNED_IMPLEMENTATION_SECTION_HEADER in rendered
    authoritative_part, planner_part = rendered.split(PLANNED_IMPLEMENTATION_SECTION_HEADER)
    assert "displayName field" not in authoritative_part
    assert "displayName field" in planner_part
    assert "Modify Customer.java" in planner_part
    assert "Customer.java (modify)" in planner_part


def test_subtask_semantic_context_projects_invariants_upstream_and_downstream_contracts():
    invariant = "runtime configuration remains external"
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement=invariant)],
        subtasks=[
            Subtask(
                id="config", description="write config", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="config.xml", action=FileAction.CREATE)],
                provides=["config.ready"], relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="app", description="consume config", execution_method=ExecutionMethod.MODEL,
                depends_on=["config"],
                planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)],
                requires=["config.ready"], relevant_global_invariant_ids=["gi1"],
                verification=[VerificationMethod(
                    type=VerificationMethodType.JUDGMENT, description="application consumes config",
                    requires_runtime_execution=True,
                )],
            ),
        ],
    )

    provider_context = build_subtask_semantic_context(plan, plan.subtasks[0])
    consumer_context = build_subtask_semantic_context(plan, plan.subtasks[1])
    assert '"consumer": "app"' in provider_context
    assert '"capability": "config.ready"' in provider_context
    assert '"provider": "config"' in consumer_context
    assert invariant in consumer_context
    assert "application consumes config" in consumer_context
    assert '"runtime_execution_required": true' in consumer_context


def test_test_subtask_context_assigns_process_evidence_only_to_runtime_owner():
    tests = Subtask(
        id="tests", description="write safe unit tests", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="src/test/java/AppTest.java", action=FileAction.CREATE)],
        provides=["tests.ready"],
        verification=[VerificationMethod(
            type=VerificationMethodType.TOOL, tool_name="test",
            verifier_kind=VerifierKind.TEST, description="run ordinary unit tests",
        )],
    )
    runtime = Subtask(
        id="runtime", description="verify process behavior", execution_method=ExecutionMethod.MODEL,
        execution_role=ExecutionRole.VERIFICATION, planned_files=[],
        depends_on=["tests"], requires=["tests.ready"],
        verification=[VerificationMethod(
            type=VerificationMethodType.JUDGMENT,
            verifier_kind=VerifierKind.APPLICATION_RUNTIME,
            requires_runtime_execution=True,
            description="observe application stdout and exit status",
        )],
    )
    plan = EngineeringPlan(plan_id="runtime-owner", kind=ChangeKind.TASK, subtasks=[tests, runtime])

    context = build_subtask_semantic_context(plan, tests)

    assert '"application_runtime_owners": [\n    "runtime"\n  ]' in context
    assert "unit-test artifacts do not own process-exit" in context
    assert "owned exclusively by the declared application_runtime verifier" in context


def test_plan_repair_prompt_is_json_only_and_gives_exact_unscoped_check_correction():
    prompt = build_structured_plan_repair_prompt(
        "build the requested application",
        "previous response",
        ["MODEL subtask(s) ['verify'] declare no planned_files"],
        ["MODEL_SUBTASK_MISSING_PLANNED_FILES"],
        2,
    )
    assert "Return only one complete JSON object" in prompt
    assert "Do not use Markdown or code fences" in prompt
    # PRV-05 (2026-08-28): a non-editing check subtask is now corrected by
    # setting execution_role=verification and keeping it as its own subtask
    # (see ExecutionRole's own docstring, kriya/workflow/plan_schema.py) -
    # not by removing it, which the Planner's own repeated identical plan
    # proved doesn't reflect what a real regression-verification step needs.
    assert "set its execution_role to verification" in prompt
    assert "do NOT remove it" in prompt
    assert "Never invent a fake file" in prompt


def test_plan_repair_prompt_gives_exact_schema_contract_and_extension_evidence():
    prompt = build_structured_plan_repair_prompt(
        "extend the application",
        "previous response",
        ["schema invalid", "extension point required"],
        [
            "STRUCTURED_PLAN_SCHEMA_INVALID",
            "SUBTASK_REQUIREMENT_UNPROVIDED",
            "EXTENSION_POINT_REQUIRED",
        ],
        1,
        route_kind=ChangeKind.MILESTONE,
        extension_candidates=["Existing.java"],
    )
    assert "verification item must be an object" in prompt
    assert "character-for-character" in prompt
    assert '["Existing.java"]' in prompt
    assert "Do not invent a path" in prompt


def test_plan_repair_prompt_resolves_duplicate_file_ownership_without_weakening_scope():
    prompt = build_structured_plan_repair_prompt(
        "Fix the existing formatter and add regression coverage",
        "previous response",
        ["planned file ownership must be unique: {'formatter.py': ['implementation', 'verify']}"],
        ["AMBIGUOUS_PLANNED_FILE_OWNERSHIP"],
        1,
        repository_candidates=[
            "src/CustomerDisplayNameFormatter.java",
            "tests/CustomerDisplayNameFormatterTest.java",
        ],
    )

    assert "owned by exactly one MODEL subtask" in prompt
    assert "retain it only on the subtask that actually performs" in prompt
    assert "verification or acceptance criteria" in prompt
    assert "do not rename, replace, or invent files" in prompt
    assert "'formatter.py': ['implementation', 'verify']" in prompt
    assert "REMOVE any separate MODEL" in prompt
    assert "sole purpose is to analyze, inspect, research, or explain" in prompt
    assert "src/CustomerDisplayNameFormatter.java" in prompt


# --- PRV-11 (2026-08-31): the repair prompt gives every OTHER structural
# reason code an explicit, targeted correction - these two (added §11.23)
# previously had none, leaving the model with only the generic error text
# and no instruction on the actual corrective action. Live incident this
# closes: across two real repair attempts, the model saw the exact same
# MISSING_GROUNDED_PRODUCTION_ARTIFACT error twice, never once added the
# missing subtask, and instead regressed an unrelated field while trying
# to fix something else. ---

def test_plan_repair_prompt_gives_targeted_correction_for_missing_production_artifact():
    prompt = build_structured_plan_repair_prompt(
        "Add uppercase displayName to the customer lookup response",
        "previous response",
        [
            "grounded structural evidence shows test file(s) referencing a production artifact "
            "no subtask owns: src/test/.../CustomerControllerTest.java references "
            "src/main/.../CustomerController.java"
        ],
        ["MISSING_GROUNDED_PRODUCTION_ARTIFACT"],
        1,
    )

    assert "account for that grounded artifact in the planned production path" in prompt
    assert "If the authoritative goal and repository evidence show" in prompt
    assert "action=modify" in prompt
    assert "depends_on and requires" in prompt
    assert "Do not infer that the artifact must be modified solely" in prompt


def test_plan_repair_prompt_gives_targeted_correction_for_miswired_dependency_edge():
    prompt = build_structured_plan_repair_prompt(
        "Add uppercase displayName to the customer lookup response",
        "previous response",
        [
            "grounded structural evidence shows test file(s) whose own requires/depends_on skip "
            "past the real intermediate producer: CustomerControllerTest.java references "
            "CustomerController.java (owned by subtask 's3', which is not in this test's own "
            "depends_on chain)"
        ],
        ["MISWIRED_GROUNDED_DEPENDENCY_EDGE"],
        1,
    )

    assert "change that test subtask's own requires to the exact provides value" in prompt
    assert "add that owning subtask's id to the test subtask's own depends_on" in prompt
    assert "requires/depends_on must route through whichever subtask really owns" in prompt


def test_plan_repair_prompt_aligns_semantic_provider_with_grounded_owner():
    prompt = build_structured_plan_repair_prompt(
        "Change lookup behavior and verify it",
        "previous response",
        [
            "Test.java references Controller.java (owned by subtask 's3', "
            "provides=['controller_cap'], test requires=['service_cap'])"
        ],
        ["GROUNDED_SEMANTIC_PROVIDER_MISMATCH"],
        1,
    )

    assert "add one of the named owner's provides capabilities" in prompt
    assert "requires -> provides aligned with the same responsible owner" in prompt


# --- PRV-11 (2026-08-31): extension_points was never wired into the
# must_preserve/PLAN_STRUCTURAL_VALIDITY protection at all - only 4 checks
# were ever covered when that mechanism was built (§11.14). Live incident
# this closes: a repair attempt satisfied MISSING_GROUNDED_PRODUCTION_
# ARTIFACT's own instruction (added above) while, unprotected, silently
# dropping an already-valid extension_points - nothing caught it until the
# very next validation pass, one repair attempt too late. ---

def _enhancement_plan_with_extension_points(extension_points):
    return EngineeringPlan(
        plan_id="p1", kind=ChangeKind.ENHANCEMENT,
        extension_points=list(extension_points),
        subtasks=[Subtask(
            id="s1", description="d", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.MODIFY)],
        )],
    )


def test_extension_points_obligation_recorded_satisfied_when_present(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    plan = _enhancement_plan_with_extension_points(["a.py"])
    ledger = ObligationLedger()

    import asyncio
    asyncio.run(validate_plan(plan, workspace_path=str(tmp_path), obligation_ledger=ledger, revision=0))

    rec = ledger.current("plan.extension_points.non_empty")
    assert rec is not None
    assert rec.status == ObligationStatus.SATISFIED
    assert rec.kind == ObligationKind.PLAN_STRUCTURAL_VALIDITY
    assert rec.terminal_required is True


def test_extension_points_obligation_recorded_violated_when_missing(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    plan = _enhancement_plan_with_extension_points([])
    ledger = ObligationLedger()

    import asyncio
    asyncio.run(validate_plan(plan, workspace_path=str(tmp_path), obligation_ledger=ledger, revision=0))

    rec = ledger.current("plan.extension_points.non_empty")
    assert rec is not None
    assert rec.status == ObligationStatus.VIOLATED


def test_extension_points_satisfied_state_now_flows_into_must_preserve(tmp_path):
    """The actual live gap: a SATISFIED extension_points obligation from an
    earlier validate_plan() call must now survive into the NEXT repair
    round's own must_preserve list - exactly the same protection
    refactor_baseline/planned-file-action/ownership/model-scope already
    had, extended to this one, using the SAME ObligationLedger.
    relevant_for_preservation() call site workflow_controller.py's own
    repair loop already uses (not reimplemented here). Unconditional on
    `kind` since PRV-11 (2026-08-31) - see relevant_for_preservation's own
    docstring for why a single, never-violated, freshly-satisfied
    obligation like this one (nothing to correlate it against) still had
    to be included."""
    (tmp_path / "a.py").write_text("x = 1\n")
    plan = _enhancement_plan_with_extension_points(["a.py"])
    ledger = ObligationLedger()

    import asyncio
    asyncio.run(validate_plan(plan, workspace_path=str(tmp_path), obligation_ledger=ledger, revision=0))

    must_preserve = [
        rec.description for rec in
        ledger.relevant_for_preservation(ObligationKind.PLAN_STRUCTURAL_VALIDITY)
    ]
    assert any("extension_points" in description for description in must_preserve)


def test_plan_repair_prompt_directs_unknown_global_invariant_to_declared_ids(tmp_path):
    """Regression test for PRV-06 (2026-08-28): UNKNOWN_GLOBAL_INVARIANT
    used to have NO targeted correction block at all (every other reason
    code in this function does) - the model was shown the error but never
    told the fix mechanism, so it non-convergently repeated the same
    mismatch across two full bounded repair rounds live. This asserts the
    new targeted block actually reaches the model."""
    prompt = build_structured_plan_repair_prompt(
        "build the requested application",
        "previous response",
        ["subtask 's3' references unknown global invariant id(s): ['gi_ghost']; "
         "declared ids are ['gi1', 'gi2']"],
        ["UNKNOWN_GLOBAL_INVARIANT"],
        1,
    )
    assert "replace that entry with one of the declared ids" in prompt
    assert "Do not invent a new id" in prompt
    assert "do not restate the invariant's statement text as the id" in prompt
    assert "Existing global invariant ids from the previous draft must be preserved" in prompt
    assert "never restate the statement text as the id" in prompt


def test_normalized_ownership_diagnostic_exposes_duplicate_claims_without_inventing_symbols(tmp_path):
    (tmp_path / "formatter.py").write_text("def format_name(value): return value\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="implementation", description="fix formatter",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(
                    path="formatter.py", action=FileAction.MODIFY, reason="existing implementation",
                )],
            ),
            Subtask(
                id="verification", description="verify formatter",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(
                    path="formatter.py", action=FileAction.MODIFY, reason="run checks",
                )],
            ),
        ],
    )

    records = normalized_ownership_validation_records(plan, workspace_path=str(tmp_path))

    assert records == [{
        "planned_path": "formatter.py",
        "planned_symbol": None,
        "declared_owner": None,
        "declared_owners": ["implementation", "verification"],
        "candidate_existing_owners": ["formatter.py"],
        "repository_evidence": {
            "exact_path_exists": True,
            "ownership_discovery_performed": False,
            "claims": [
                {"subtask_id": "implementation", "action": "modify", "reason": "existing implementation"},
                {"subtask_id": "verification", "action": "modify", "reason": "run checks"},
            ],
        },
        "validator_rule": "each planned file path must be owned by exactly one subtask",
        "decision": "rejected",
        "reason": "planned path is claimed by 2 subtasks",
    }]


def test_planning_diagnostics_append_bounded_local_attempt_records(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="implementation", description="write app",
            execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="app.py", action=FileAction.CREATE)],
        )],
    )
    for attempt in range(3):
        persist_planning_attempt_diagnostic(
            str(tmp_path), "run/unsafe:id",
            attempt=attempt,
            planner_request="request",
            planner_system_prompt="system",
            raw_plan_response=f"response {attempt}",
            plan=plan,
            validation_errors=["validation error"],
            reason_codes=["REASON"],
            repository_evidence=[{"path": "app.py", "exists": False}],
            repair_prompt="repair" if attempt < 2 else None,
        )

    path = planning_diagnostics_path(str(tmp_path), "run/unsafe:id")
    records = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert [record["attempt"] for record in records] == [0, 1, 2]
    assert records[0]["raw_plan_response"] == "response 0"
    assert records[1]["repair_prompt"] == "repair"
    assert records[2]["repair_prompt"] is None
    assert records[0]["repository_evidence"] == [{"path": "app.py", "exists": False}]
    assert records[0]["approved_plan"] is None
    assert path.endswith("/.kriya/control/planning-diagnostics/run_unsafe_id.jsonl")


def test_approved_plan_diagnostic_dumps_runtime_ownership_contract():
    plan = EngineeringPlan(
        plan_id="approved", kind=ChangeKind.ENHANCEMENT,
        subtasks=[
            Subtask(
                id="s3", description="change controller",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="Controller.java", action=FileAction.MODIFY)],
                depends_on=["s2"], requires=["service_cap"], provides=["controller_cap"],
            ),
            Subtask(
                id="s4", description="verify controller",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="ControllerTest.java", action=FileAction.MODIFY)],
                depends_on=["s3"], requires=["controller_cap"], provides=[],
            ),
        ],
    )

    assert approved_plan_diagnostic(plan, validation_errors=[]) == {
        "plan_id": "approved",
        "subtasks": [
            {
                "id": "s3",
                "planned_files": [{
                    "path": "Controller.java", "action": "modify",
                    "environment_requirements": [],
                    "requires_capabilities": [],
                }],
                "depends_on": ["s2"],
                "requires": ["service_cap"],
                "provides": ["controller_cap"],
            },
            {
                "id": "s4",
                "planned_files": [{
                    "path": "ControllerTest.java", "action": "modify",
                    "environment_requirements": [],
                    "requires_capabilities": [],
                }],
                "depends_on": ["s3"],
                "requires": ["controller_cap"],
                "provides": [],
            },
        ],
    }
    assert approved_plan_diagnostic(plan, validation_errors=["invalid"]) is None


def test_planned_prerequisite_revision_wires_owner_upstream_without_moving_ownership():
    plan = EngineeringPlan(
        plan_id="prerequisite", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s2", description="create framework-backed test",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(
                    path="checks/behavior.spec", action=FileAction.CREATE,
                )],
            ),
            Subtask(
                id="s4", description="provide test tooling",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="build.config", action=FileAction.CREATE)],
                provides=["test_tooling"],
            ),
        ],
    )

    revised = revise_plan_for_planned_prerequisite(
        plan, consumer_subtask_id="s2", owner_subtask_id="s4",
        capability="test_tooling", consumer_artifacts=["checks/behavior.spec"],
    )

    assert revised.subtask_by_id("s2").depends_on == ["s4"]
    assert revised.subtask_by_id("s2").requires == ["test_tooling"]
    assert revised.subtask_by_id("s2").planned_files[0].requires_capabilities == ["test_tooling"]
    assert [pf.path for pf in revised.subtask_by_id("s4").planned_files] == ["build.config"]
    assert plan.subtask_by_id("s2").depends_on == []


def _prv17_run13_shaped_plan():
    """The exact ownership shape of the live PRV-17 Run 13 incident:
    myproject/ owned by BOTH s1 (__init__.py, settings.py) and s3 (urls.py)
    - the case that rules out a naive "unique owner of the parent
    directory" rule (see resolve_runtime_plan_gap_owner's own docstring).
    s5 (the managed-service verification subtask) has empty planned_files
    by design and depends transitively on s1 via s2->s3->s4->s5."""
    return EngineeringPlan(
        plan_id="prv17-run13", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="Django project scaffold", execution_method=ExecutionMethod.MODEL,
                planned_files=[
                    PlannedFile(path="manage.py", action=FileAction.CREATE),
                    PlannedFile(path="myproject/__init__.py", action=FileAction.CREATE),
                    PlannedFile(path="myproject/settings.py", action=FileAction.CREATE),
                ],
            ),
            Subtask(
                id="s2", description="customers app scaffold", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="customers/__init__.py", action=FileAction.CREATE)],
                depends_on=["s1"],
            ),
            Subtask(
                id="s3", description="urls", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="myproject/urls.py", action=FileAction.CREATE)],
                depends_on=["s2"],
            ),
            Subtask(
                id="s4", description="tests", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="customers/tests.py", action=FileAction.CREATE)],
                depends_on=["s3"],
            ),
            Subtask(
                id="s5", description="managed-service verification", execution_method=ExecutionMethod.MODEL,
                planned_files=[], depends_on=["s4"],
            ),
        ],
    )


def test_resolve_python_module_to_candidate_artifact_path_picks_module_file_form(tmp_path):
    os.makedirs(tmp_path / "myproject")
    (tmp_path / "myproject" / "__init__.py").write_text("")
    assert resolve_python_module_to_candidate_artifact_path(
        "myproject.wsgi", str(tmp_path),
    ) == "myproject/wsgi.py"


def test_resolve_python_module_to_candidate_artifact_path_refuses_bare_top_level_name(tmp_path):
    # No established parent package to anchor against - could equally be a
    # fresh single-file module or the start of a brand-new package.
    assert resolve_python_module_to_candidate_artifact_path("django", str(tmp_path)) is None


def test_resolve_python_module_to_candidate_artifact_path_refuses_when_parent_package_missing(tmp_path):
    assert resolve_python_module_to_candidate_artifact_path(
        "myproject.wsgi", str(tmp_path),
    ) is None


def test_resolve_python_module_to_candidate_artifact_path_refuses_when_module_file_already_exists(tmp_path):
    os.makedirs(tmp_path / "myproject")
    (tmp_path / "myproject" / "wsgi.py").write_text("existing")
    assert resolve_python_module_to_candidate_artifact_path(
        "myproject.wsgi", str(tmp_path),
    ) is None


def test_resolve_python_module_to_candidate_artifact_path_refuses_when_package_dir_already_exists(tmp_path):
    os.makedirs(tmp_path / "myproject" / "wsgi")
    assert resolve_python_module_to_candidate_artifact_path(
        "myproject.wsgi", str(tmp_path),
    ) is None


def test_resolve_runtime_plan_gap_owner_picks_the_init_py_owner_not_any_directory_owner():
    plan = _prv17_run13_shaped_plan()
    s5 = plan.subtask_by_id("s5")
    # myproject/ is owned by BOTH s1 (__init__.py) and s3 (urls.py) - only
    # __init__.py's owner (s1) is the correct, unambiguous answer.
    assert resolve_runtime_plan_gap_owner(plan, "myproject/wsgi.py", s5) == "s1"


def test_resolve_runtime_plan_gap_owner_refuses_when_no_parent_directory():
    plan = _prv17_run13_shaped_plan()
    s5 = plan.subtask_by_id("s5")
    assert resolve_runtime_plan_gap_owner(plan, "toplevel.py", s5) is None


def test_resolve_runtime_plan_gap_owner_refuses_when_init_py_is_multi_owned():
    plan = EngineeringPlan(
        plan_id="ambiguous-init", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="a", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pkg/__init__.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="b", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pkg/__init__.py", action=FileAction.MODIFY)],
                depends_on=["s1"],
            ),
            Subtask(
                id="s3", description="verify", execution_method=ExecutionMethod.MODEL,
                planned_files=[], depends_on=["s2"],
            ),
        ],
    )
    s3 = plan.subtask_by_id("s3")
    assert resolve_runtime_plan_gap_owner(plan, "pkg/missing.py", s3) is None


def test_resolve_runtime_plan_gap_owner_refuses_owner_not_upstream_of_failing_subtask():
    plan = EngineeringPlan(
        plan_id="downstream-owner", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="verify", execution_method=ExecutionMethod.MODEL,
                planned_files=[],
            ),
            Subtask(
                id="s2", description="unrelated package", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pkg/__init__.py", action=FileAction.CREATE)],
            ),
        ],
    )
    s1 = plan.subtask_by_id("s1")
    # s2 is not upstream of s1 (no depends_on edge either way) - FUTURE_
    # ORDERED/UNRELATED, not PAST_ORDERED, so it must not be reopened.
    assert resolve_runtime_plan_gap_owner(plan, "pkg/missing.py", s1) is None


def test_revise_plan_for_runtime_plan_gap_adds_exactly_one_planned_file_to_the_owner():
    plan = _prv17_run13_shaped_plan()
    revised = revise_plan_for_runtime_plan_gap(
        plan, owner_subtask_id="s1", artifact_path="myproject/wsgi.py", reason="test",
    )
    revised_s1_paths = [pf.path for pf in revised.subtask_by_id("s1").planned_files]
    assert revised_s1_paths == [
        "manage.py", "myproject/__init__.py", "myproject/settings.py", "myproject/wsgi.py",
    ]
    # Every other subtask's own planned_files/depends_on is untouched.
    assert [pf.path for pf in revised.subtask_by_id("s3").planned_files] == ["myproject/urls.py"]
    assert revised.subtask_by_id("s5").depends_on == ["s4"]
    # The original plan object passed in is never mutated.
    assert [pf.path for pf in plan.subtask_by_id("s1").planned_files] == [
        "manage.py", "myproject/__init__.py", "myproject/settings.py",
    ]


def test_revise_plan_for_runtime_plan_gap_rejects_unknown_owner():
    plan = _prv17_run13_shaped_plan()
    with pytest.raises(ValueError, match="unknown owner subtask"):
        revise_plan_for_runtime_plan_gap(
            plan, owner_subtask_id="does-not-exist", artifact_path="myproject/wsgi.py", reason="test",
        )


def test_revise_plan_for_runtime_plan_gap_rejects_already_planned_path():
    plan = _prv17_run13_shaped_plan()
    with pytest.raises(ValueError, match="already planned"):
        revise_plan_for_runtime_plan_gap(
            plan, owner_subtask_id="s1", artifact_path="myproject/settings.py", reason="test",
        )


def test_authoritative_planner_request_forbids_unsupported_tool_stages_without_changing_goal():
    request = build_authoritative_planner_request("Build one runnable application.")
    assert "Original product request:\nBuild one runnable application." in request
    assert "Do not emit execution_method=tool subtasks" in request
    # PRV-05 (2026-08-28): a non-editing check now gets its own
    # execution_role=verification subtask (see ExecutionRole's own
    # docstring, kriya/workflow/plan_schema.py) rather than being folded
    # into an implementation subtask's verification/acceptance_criteria.
    assert "execution_role: implementation or verification" in request
    assert "not product requirements" in request
    assert "Return only one complete JSON object" in request
    assert "Emit no prose" in request
    assert "observable application behavior" in request
    assert "actually runs the application" in request
    assert "Never emit verification strings" in request
    assert "exactly match one provides string" in request
    assert "tool_name=compile" in request
    assert "runnable entrypoint stage" in request
    assert "Each planned_files path must be owned by exactly one implementation MODEL subtask" in request
    assert "solely to analyze, inspect, research, or explain code" in request


def test_authoritative_planner_request_carries_testability_and_tooling_dag_guidance():
    """PRV-06 (2026-08-28): two generic, cross-language planning-time
    nudges added after a real greenfield failure - (1) a stage needing
    build/test tooling from another stage's manifest must declare that as
    a real requires/depends_on edge, not rely on incidental subtask-id
    ordering (topological_subtask_order's own tie-break is "sorted id...
    not a meaningful priority" - confirmed this run's s2/s3 ordering only
    held by alphabetical luck); (2) a process-terminating entrypoint and
    the tests exercising it in-process must stay structurally separable."""
    request = build_authoritative_planner_request("Build one runnable application.")
    assert "must declare that stage's provides value in its own requires and depends_on" in request
    assert "do not rely on planned_files or list position to imply execution order" in request
    assert "keep the process-terminating call separate from the" in request
    assert "System.exit" not in request  # generic across languages, not Java-specific
    assert "run()" not in request  # no required method name/shape


def test_plan_repair_prompt_contains_exact_prerequisite_correction_tuple():
    prompt = build_structured_plan_repair_prompt(
        "Build app", "{}", ["missing prerequisite"],
        ["PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED"], 1,
        validation_evidence=[{
            "consumer_subtask": "s3",
            "consumer_file": "src/test/java/AppTest.java",
            "prerequisite_capability": "maven_test_dependencies",
            "provider_subtask": "s2",
            "provider_file": "pom.xml",
            "missing_requires_edge": True,
            "missing_depends_on_edge": True,
        }],
    )
    assert "Consumer: subtask=s3 file=src/test/java/AppTest.java" in prompt
    assert "Required planned capability: maven_test_dependencies" in prompt
    assert "Provider: subtask=s2 file=pom.xml" in prompt
    assert "add maven_test_dependencies to s3.requires" in prompt
    assert "add s2 to s3.depends_on" in prompt


# --- PRV-17 Run 8 diagnostic audit (2026-09-03): validator evidence ->
# repair Planner boundary for VERIFICATION_PREREQUISITE_MANIFEST_MISSING.
# _stack_dependent_verification_prerequisite_evidence() (plan_validation.py)
# already reaches validate_plan()'s own `evidence` list and workflow_
# controller.py's `validation_evidence` parameter unchanged - the gap found
# here was that build_structured_plan_repair_prompt silently dropped it
# (the shared `prerequisite_evidence` filter above requires a
# `provider_subtask` key this record never has) instead of turning it into
# an explicit correction the way every other reason_code already does.

def _manifest_missing_stack_contract():
    from kriya.workflow.static_checks import StackContract
    return StackContract(languages=("python",), frameworks=("django",))


def _manifest_missing_consumer_only_plan():
    """No subtask anywhere plans a Python dependency manifest - s2's TEST
    verification has no provisioning prerequisite at all."""
    return EngineeringPlan(
        plan_id="manifest-missing-repair-audit", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="scaffold the app", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="manage.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="test the app", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="app/tests.py", action=FileAction.CREATE)],
                verification=[VerificationMethod(
                    type=VerificationMethodType.TOOL, description="run tests",
                    tool_name="test", verifier_kind=VerifierKind.TEST,
                )],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_manifest_missing_evidence_is_actionable_and_structured(tmp_path):
    """(1) validate_plan() produces a structured evidence record - not just
    a reason code/free-text error - naming the consumer, the required tool,
    and the candidate manifest filenames a repair could plan."""
    result = await validate_plan(
        _manifest_missing_consumer_only_plan(),
        workspace_path=str(tmp_path), stack_contract=_manifest_missing_stack_contract(),
    )

    assert result.valid is False
    assert "VERIFICATION_PREREQUISITE_MANIFEST_MISSING" in result.reason_codes
    manifest_records = [e for e in result.evidence if e.get("required_tool") == "python"]
    assert len(manifest_records) == 1
    record = manifest_records[0]
    assert record["consumer_subtask"] == "s2"
    assert set(record["candidate_manifests"]) == {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"}


def test_manifest_missing_evidence_reaches_the_real_repair_prompt():
    """(2) The SAME shape validate_plan() actually produces, fed straight
    into the real build_structured_plan_repair_prompt() (no paraphrase),
    produces a dedicated targeted-correction section - not just the
    generic reason-code/error dump every reason code already gets."""
    prompt = build_structured_plan_repair_prompt(
        "Create a Django application with a customers app and tests.", "{}",
        ["subtask 's2' runs 'python'-dependent verification, but no current/past-ordered subtask "
         "plans (or the workspace already establishes) a dependency manifest (pyproject.toml/"
         "requirements.txt/setup.cfg/setup.py) to provision it"],
        ["VERIFICATION_PREREQUISITE_MANIFEST_MISSING"], 1,
        validation_evidence=[{
            "consumer_subtask": "s2", "required_tool": "python",
            "candidate_manifests": ["pyproject.toml", "requirements.txt", "setup.cfg", "setup.py"],
        }],
    )

    assert "Establish the missing dependency-provisioning prerequisite" in prompt


def test_manifest_missing_repair_correction_preserves_consumer_identity():
    """(3) The exact consumer subtask id survives into the correction text,
    not a generic placeholder."""
    prompt = build_structured_plan_repair_prompt(
        "goal", "{}", ["error"], ["VERIFICATION_PREREQUISITE_MANIFEST_MISSING"], 1,
        validation_evidence=[{
            "consumer_subtask": "s2", "required_tool": "python",
            "candidate_manifests": ["requirements.txt"],
        }],
    )
    assert "Consumer: subtask=s2 runs python-dependent verification" in prompt


def test_manifest_missing_repair_correction_preserves_provider_requirement():
    """(4) The exact candidate manifest filenames survive - not a vague
    "add a dependency file" instruction."""
    prompt = build_structured_plan_repair_prompt(
        "goal", "{}", ["error"], ["VERIFICATION_PREREQUISITE_MANIFEST_MISSING"], 1,
        validation_evidence=[{
            "consumer_subtask": "s2", "required_tool": "python",
            "candidate_manifests": ["pyproject.toml", "requirements.txt"],
        }],
    )
    assert "dependency manifest (one of: pyproject.toml/requirements.txt) must be provided" in prompt


def test_manifest_missing_repair_correction_states_ordering_requirement():
    """(5) The correction explicitly states BOTH independently-legal
    satisfying relations - CURRENT (the consumer plans the manifest
    itself) and PAST_ORDERED (a separate provider subtask ordered ahead
    via depends_on) - not a single prescriptive "always add a separate
    provider to depends_on" instruction, which would wrongly rule out the
    simpler CURRENT repair classify_file_ownership() itself already
    accepts."""
    prompt = build_structured_plan_repair_prompt(
        "goal", "{}", ["error"], ["VERIFICATION_PREREQUISITE_MANIFEST_MISSING"], 1,
        validation_evidence=[{
            "consumer_subtask": "s2", "required_tool": "python",
            "candidate_manifests": ["requirements.txt"],
        }],
    )
    assert "provided either (1) by this consumer subtask itself" in prompt
    assert "or (2) by a subtask ordered before s2" in prompt
    assert "add that provider to s2.depends_on" in prompt


@pytest.mark.asyncio
async def test_manifest_missing_repaired_plan_current_self_provided_manifest_passes(tmp_path):
    """(6a) CURRENT: the consumer subtask itself plans the manifest
    alongside its own tests - no separate provider subtask, no depends_on
    edge involved at all. classify_file_ownership() already treats this as
    satisfying (a subtask's own planned_files are trivially CURRENT to
    itself); the repair correction's wording must not imply this is
    illegal by always demanding a separate provider be added to
    depends_on."""
    plan = EngineeringPlan(
        plan_id="manifest-missing-current", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="scaffold the app", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="manage.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="declare dependencies and test the app",
                execution_method=ExecutionMethod.MODEL, depends_on=["s1"],
                planned_files=[
                    PlannedFile(path="requirements.txt", action=FileAction.CREATE),
                    PlannedFile(path="app/tests.py", action=FileAction.CREATE),
                ],
                verification=[VerificationMethod(
                    type=VerificationMethodType.TOOL, description="run tests",
                    tool_name="test", verifier_kind=VerifierKind.TEST,
                )],
            ),
        ],
    )
    result = await validate_plan(
        plan, workspace_path=str(tmp_path), stack_contract=_manifest_missing_stack_contract(),
    )

    assert "VERIFICATION_PREREQUISITE_MANIFEST_MISSING" not in result.reason_codes


@pytest.mark.asyncio
async def test_manifest_missing_repaired_plan_with_ordered_provider_passes(tmp_path):
    """(6b) PAST_ORDERED: a repaired plan that adds a SEPARATE manifest-
    planning subtask ordered ahead of the verification consumer (s1
    provides requirements.txt, s2 depends on s1) satisfies the invariant -
    the other of the two independently-legal repairs the corrected
    targeted-correction text now names explicitly."""
    plan = EngineeringPlan(
        plan_id="manifest-missing-repaired", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="scaffold and declare dependencies", execution_method=ExecutionMethod.MODEL,
                planned_files=[
                    PlannedFile(path="manage.py", action=FileAction.CREATE),
                    PlannedFile(path="requirements.txt", action=FileAction.CREATE),
                ],
            ),
            Subtask(
                id="s2", description="test the app", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="app/tests.py", action=FileAction.CREATE)],
                verification=[VerificationMethod(
                    type=VerificationMethodType.TOOL, description="run tests",
                    tool_name="test", verifier_kind=VerifierKind.TEST,
                )],
            ),
        ],
    )
    result = await validate_plan(
        plan, workspace_path=str(tmp_path), stack_contract=_manifest_missing_stack_contract(),
    )

    assert "VERIFICATION_PREREQUISITE_MANIFEST_MISSING" not in result.reason_codes


@pytest.mark.asyncio
async def test_manifest_missing_future_ordered_provider_still_fails(tmp_path):
    """(7) A manifest planned only by a subtask ORDERED AFTER the
    verification consumer (s3 depends on s2, the consumer - so the
    manifest is FUTURE_ORDERED relative to s2, not CURRENT/PAST_ORDERED)
    must still fail - the invariant is about ordering, not mere presence
    anywhere in the plan."""
    plan = EngineeringPlan(
        plan_id="manifest-missing-future-ordered", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="scaffold the app", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="manage.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="test the app", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="app/tests.py", action=FileAction.CREATE)],
                verification=[VerificationMethod(
                    type=VerificationMethodType.TOOL, description="run tests",
                    tool_name="test", verifier_kind=VerifierKind.TEST,
                )],
            ),
            Subtask(
                id="s3", description="declare dependencies later", execution_method=ExecutionMethod.MODEL,
                depends_on=["s2"],
                planned_files=[PlannedFile(path="requirements.txt", action=FileAction.CREATE)],
            ),
        ],
    )
    result = await validate_plan(
        plan, workspace_path=str(tmp_path), stack_contract=_manifest_missing_stack_contract(),
    )

    assert "VERIFICATION_PREREQUISITE_MANIFEST_MISSING" in result.reason_codes


def test_manifest_missing_correction_does_not_fire_for_unrelated_reason_codes():
    """(8) Unrelated existing plan-repair diagnostics are unchanged: even
    when validation_evidence happens to contain records shaped like a
    manifest-missing record, the new correction text is gated on the
    VERIFICATION_PREREQUISITE_MANIFEST_MISSING reason code actually being
    present - it does not fire merely because matching-shaped evidence
    exists, and the pre-existing prerequisite-tuple correction (a
    DIFFERENT reason code, same function) is completely unaffected."""
    prompt = build_structured_plan_repair_prompt(
        "goal", "{}", ["some unrelated error"], ["SUBTASK_REQUIREMENT_UNPROVIDED"], 1,
        validation_evidence=[{
            "consumer_subtask": "s2", "required_tool": "python",
            "candidate_manifests": ["requirements.txt"],
        }],
    )
    assert "Establish the missing dependency-provisioning prerequisite" not in prompt
    assert "Replace each unprovided requires value" in prompt


def test_authoritative_planner_system_prompt_carries_testability_and_tooling_dag_guidance():
    assert "the terminating call sits in a thin wrapper" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "any process-termination mechanism" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "does not require a specific method name" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "own requires and depends_on" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "execution order is not" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT


def test_authoritative_planner_system_prompt_pairs_application_runtime_with_requires_runtime_execution():
    """Verification-routing fix (PRV-06, 2026-08-29) - the initial planning
    prompt used to tell the model WHEN to set verifier_kind=application_runtime
    but never said to pair it with requires_runtime_execution=true (unlike
    its own repair-prompt sibling, which already did) - live-confirmed as
    the root cause of a verification-only subtask silently missing the
    direct-execution predicate and burning its budget on doomed Developer
    generation under DENY_ALL."""
    assert "verifier_kind=application_runtime" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "requires_runtime_execution=true TOGETHER" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "never set one without the other" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT


def test_authoritative_planner_system_prompt_states_complete_application_runtime_four_field_relationship():
    """Repair-contract audit (2026-09-03, PRV-17 Run 10): a live repair
    attempt proved the prompt naming only verifier_kind/requires_runtime_
    execution (without also saying type=judgment/tool_name omitted) lets
    the model reuse the compile/test clause's own type=tool+tool_name
    template for application_runtime too, producing an unregistered
    tool_name that copies the verifier_kind's own value. The initial
    planning prompt must state the complete four-field relationship as one
    connected instruction, not separate sentences the model has to infer a
    connection between."""
    assert "type=judgment, omit tool_name entirely" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "verifier_kind=application_runtime and requires_runtime_execution=true TOGETHER" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "never set type=tool or any tool_name for this verifier" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT


def test_repair_prompt_states_the_identical_application_runtime_four_field_relationship():
    """The VERIFICATION_EVIDENCE_PATH_MISSING targeted-correction block
    must state the SAME complete relationship as the initial planning
    prompt above - initial generation and repair are the same contract,
    not two independently-maintained descriptions of it."""
    prompt = build_structured_plan_repair_prompt(
        "goal", "{}",
        ["subtask 's3' verification requirement '...' has no executable or deterministic evidence producer"],
        ["VERIFICATION_EVIDENCE_PATH_MISSING"], 1,
    )
    assert "set type=judgment, omit tool_name entirely" in prompt
    assert "verifier_kind=application_runtime and requires_runtime_execution=true TOGETHER" in prompt
    assert "never set type=tool or any tool_name for this case" in prompt


def test_application_runtime_repair_correction_does_not_disturb_compile_and_test_instructions():
    """Existing test/compile verification instructions remain unchanged -
    this fix only closes the application_runtime gap, it doesn't touch the
    already-explicit compile/test pairing in either prompt."""
    assert "tool_name=compile/verifier_kind=compile for compilation" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "tool_name=test/verifier_kind=test for tests" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT

    prompt = build_structured_plan_repair_prompt(
        "goal", "{}", ["some evidence-producer error"],
        ["VERIFICATION_EVIDENCE_PATH_MISSING"], 1,
    )
    assert (
        "set type=tool with tool_name=compile/verifier_kind=compile or "
        "tool_name=test/verifier_kind=test instead"
    ) in prompt


def test_authoritative_planner_system_prompt_carries_integration_relationship_guidance():
    """Correctness Continuity Part C completion (PRV-06, 2026-08-29) - the
    field/obligation/validation machinery was implemented but stayed
    dormant since the live Planner had no way to know it existed (a real
    PRV-06 run's own Planner never populated integration_relationships,
    confirmed by grepping that run's log). This closes it the same way
    every other structured field this prompt already asks for is
    introduced (global_invariants/provides/requires above)."""
    assert "integration_relationships" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "producer_subtask_ids" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "consumer_subtask_ids" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "STRONGER claim than depends_on/provides/requires" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT


def test_planner_structured_output_parses_a_real_integration_relationships_response():
    """Schema round-trip, no live model call: confirms a Planner response
    shaped exactly as the prompt now asks for actually parses into a real
    EngineeringPlan.integration_relationships, not silently dropped."""
    from kriya.agents.contracts import parse_planner_structured_output

    raw = json.dumps({
        "global_invariants": [],
        "subtasks": [
            {"id": "s2", "description": "App", "execution_method": "model",
             "planned_files": [{"path": "App.java", "action": "create"}]},
            {"id": "s3", "description": "Service", "execution_method": "model",
             "planned_files": [{"path": "Service.java", "action": "create"}]},
        ],
        "integration_relationships": [{
            "id": "ir1", "kind": "uses",
            "producer_subtask_ids": ["s3"], "consumer_subtask_ids": ["s2"],
            "relationship_statement": "App.java must use Service.java",
        }],
        "acceptance_criteria": [], "extension_points": [], "refactor_baseline": None,
    })

    output, error = parse_planner_structured_output(raw)

    assert error is None, error
    assert len(output.integration_relationships) == 1
    rel = output.integration_relationships[0]
    assert rel.producer_subtask_ids == ["s3"]
    assert rel.consumer_subtask_ids == ["s2"]
    assert rel.kind.value == "uses"


def test_task_planner_request_supplies_existing_paths_for_brownfield_owner_selection():
    request = build_authoritative_planner_request(
        "Fix the existing display-name formatter and update its tests",
        route_kind=ChangeKind.TASK,
        repository_candidates=[
            "src/CustomerDisplayNameFormatter.java",
            "tests/CustomerDisplayNameFormatterTest.java",
        ],
    )

    assert "Existing local workspace paths available as repository evidence" in request
    assert "src/CustomerDisplayNameFormatter.java" in request
    assert "tests/CustomerDisplayNameFormatterTest.java" in request
    assert "For modify/delete actions, select the exact relevant existing path" in request
    assert "Use action=create only" in request


def test_authoritative_planner_request_supplies_route_extension_candidates(tmp_path):
    (tmp_path / "Existing.java").write_text("class Existing {}")
    (tmp_path / "goal.md").write_text("private goal text")
    (tmp_path / "kriya.yaml").write_text("autonomy: {}")
    candidates = _authoritative_planner_extension_candidates(str(tmp_path))
    request = build_authoritative_planner_request(
        "Extend the application",
        route_kind=ChangeKind.MILESTONE,
        extension_candidates=candidates,
    )
    assert candidates == ["Existing.java"]
    assert "extension_points must name" in request
    assert '["Existing.java"]' in request
    assert "private goal text" not in request


def test_planner_repository_evidence_keeps_goal_relevant_owner_inside_bound(tmp_path):
    for index in range(110):
        (tmp_path / f"AUnrelated{index:03d}.java").write_text("class Unrelated {}")
    relevant = tmp_path / "ZCustomerDisplayNameFormatter.java"
    relevant.write_text("class ZCustomerDisplayNameFormatter {}")

    candidates = _authoritative_planner_extension_candidates(
        str(tmp_path), max_files=10,
        goal="Fix the existing customer display name formatter",
    )

    assert "ZCustomerDisplayNameFormatter.java" in candidates
    assert len(candidates) == 10


@pytest.mark.asyncio
async def test_enforce_planner_calls_use_json_only_system_contract(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["a.py"],
    })
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    planner_kwargs = we.planner.run.await_args.kwargs
    assert planner_kwargs["json_mode"] is True
    assert "Return exactly one valid JSON object" in planner_kwargs["system_prompt_override"]
    assert "no Markdown" in planner_kwargs["system_prompt_override"]


@pytest.mark.asyncio
async def test_enforce_classifies_schema_invalid_plan_separately_from_json_parse_failure(tmp_path):
    we = _workflow_engine()
    with patch(
        "kriya.workflow.workflow_controller.parse_planner_structured_output",
        return_value=(None, "structured plan JSON block failed schema validation: invalid verification"),
    ):
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "needs_review"
    assert "STRUCTURED_PLAN_SCHEMA_INVALID" in result.legacy_result["reason_codes"]
    assert "STRUCTURED_PLAN_PARSE_FAILED" not in result.legacy_result["reason_codes"]
    assert "STRUCTURED_PLAN_REPAIR_EXHAUSTED" in result.legacy_result["reason_codes"]


@pytest.mark.asyncio
async def test_enforce_knowledge_guard_stops_before_structured_planning(tmp_path):
    from kriya.config import AppConfig
    from kriya.tools.knowledge import GapReport

    report = GapReport()
    report.add_gap("org.example:new-lib", "9.0.0", None, "high", "after cutoff")
    we = _workflow_engine()
    config = AppConfig()
    config.paths.memory = str(tmp_path / "memory")
    config.paths.skills = str(tmp_path / "skills")
    we.kernel = MagicMock(config=config)

    with patch(
        "kriya.tools.knowledge.KnowledgeGuard.check_goal", return_value=report,
    ):
        result = await WorkflowController(we).execute(
            "Use new-lib 9.0.0", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "knowledge_gap"
    we.planner.run.assert_not_awaited()


# --- core per-subtask orchestration ---

@pytest.mark.asyncio
async def test_enforce_calls_run_generation_workflow_once_per_subtask(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert len(calls) == 2
    assert result.legacy_result["status"] == "success"
    assert [r.subtask_id for r in result.subtask_results] == ["s1", "s2"]
    assert all(r.status == SubtaskStatus.COMPLETED for r in result.subtask_results)


@pytest.mark.asyncio
async def test_enforce_canonicalizes_wrong_action_instead_of_repairing(tmp_path):
    """Regression test (PRV-05 run #10, 2026-08-28): the Planner declared
    action=modify for a file that does not exist yet - previously this
    always cost a PLANNED_FILE_ACTION_MISMATCH repair round, and the live
    incident showed the model can fail to correct it even across two full
    rounds. canonicalize_planned_file_actions() now fixes this before
    validate_plan() ever sees it, so the plan must validate on the very
    first Planner call - zero repair rounds, zero extra planner.run() calls."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a new file", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.MODIFY)],  # wrong: a.py doesn't exist yet
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(
        return_value={"status": "success", "quality_gates_passed": True, "files": []},
    )

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "success"
    assert we.planner.run.await_count == 1, "a real defect must not have required any repair round"
    # canonicalize_planned_file_actions() never mutates the Planner's own
    # object - only the corrected copy used for validation/execution.
    assert plan.subtasks[0].planned_files[0].action == FileAction.MODIFY


@pytest.mark.asyncio
async def test_enforce_reuses_one_skill_registry_and_projects_resolved_knowledge(tmp_path):
    from kriya.config import AppConfig
    from kriya.tools.knowledge import GapReport

    plan = _two_subtask_plan()
    we = _workflow_engine()
    config = AppConfig()
    config.paths.memory = str(tmp_path / "memory")
    config.paths.skills = str(tmp_path / "skills")
    we.kernel = MagicMock(config=config)
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run
    shared_engine = MagicMock()
    p1, p2, p3 = _patched(plan)
    with patch(
        "kriya.tools.knowledge.KnowledgeGuard.check_goal", return_value=GapReport(),
    ), patch(
        "kriya.skills.skill.SkillEngine.from_config", return_value=shared_engine,
    ), p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    shared_engine.discover_and_load.assert_called_once_with()
    assert calls[0]["skill_engine_override"] is shared_engine
    assert calls[1]["skill_engine_override"] is shared_engine
    assert calls[0]["resolved_knowledge_coordinates"] == []
    assert calls[1]["resolved_knowledge_coordinates"] == []


@pytest.mark.asyncio
async def test_enforce_logs_a_subtask_progress_banner_before_each_subtask(tmp_path, caplog):
    """Real live-validation gap, 2026-08-24: a 3-subtask enforce run's log
    showed 3 back-to-back PLANNING/ARCHITECTURE/DEVELOPMENT/REVIEW cycles
    with no indication of which subtask number was running or how many
    subtasks the plan had in total - unlike run_milestones(), which prints
    a real "MILESTONE 'id' (2/3): ..." banner per milestone. This mirrors
    that exact convention for subtasks."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with caplog.at_level("INFO", logger="kriya.workflow.workflow"):
        with p1, p2, p3:
            controller = WorkflowController(we)
            await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    banner_text = "\n".join(r.message for r in caplog.records)
    assert "SUBTASK 'S1' (1/2)" in banner_text.upper()
    assert "SUBTASK 'S2' (2/2)" in banner_text.upper()


@pytest.mark.asyncio
async def test_enforce_threads_established_files_forward_to_the_next_subtask(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "a.py").write_text("print('a')")
            return {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}
        return {"status": "success", "quality_gates_passed": True, "files": []}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert calls[0]["established_files"] == []
    assert calls[1]["established_files"] == ["a.py"]
    assert "print('a')" in calls[1]["supplementary_context"]


@pytest.mark.asyncio
async def test_enforce_subtask_goal_text_names_the_real_subtask_and_dependency(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "quality_gates_passed": True, "files": []}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    # s1 has no depends_on - no header, matching build_milestone_goal_text's
    # own precedent (a DAG root gets no "depends on" preamble either). Authority-
    # isolation fix (PRV-11, 2026-08-30): the real top-level goal ("goal") is now
    # a labeled section, separate from the Planner's own subtask.description -
    # both still appear, so this is a substring check (like the s2 assertions
    # below always were), not the old full-string equality.
    from kriya.agents.contracts import AUTHORITATIVE_GOAL_SECTION_HEADER, PLANNED_IMPLEMENTATION_SECTION_HEADER
    assert AUTHORITATIVE_GOAL_SECTION_HEADER in calls[0]["goal"]
    assert PLANNED_IMPLEMENTATION_SECTION_HEADER in calls[0]["goal"]
    assert "goal" in calls[0]["goal"].split(PLANNED_IMPLEMENTATION_SECTION_HEADER)[0]
    assert "write a.py\n\nFiles this subtask should touch:\n- a.py (create)" in calls[0]["goal"]
    # s2 depends on s1 - real header naming both the subtask id/position and the dependency
    assert "'s2' (2 of 2)" in calls[1]["goal"]
    assert "depending on: s1" in calls[1]["goal"]
    assert "write b.py" in calls[1]["goal"]


# --- stop-on-failure: real per-subtask retry locality ---

@pytest.mark.asyncio
async def test_enforce_stops_at_first_failing_subtask_never_calls_the_dependent(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"status": "failed", "quality_gates_passed": False}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert len(calls) == 1  # s2's own call never happened
    assert result.legacy_result["status"] == "failed"
    assert [r.subtask_id for r in result.subtask_results] == ["s1"]
    assert result.subtask_results[0].status == SubtaskStatus.FAILED


# --- knowledge_gap surfacing (2026-08-25, real live finding: ignite_qpid_protocol) ---

@pytest.mark.asyncio
async def test_enforce_surfaces_knowledge_gap_from_the_first_subtask_to_the_top_level(tmp_path):
    """Regression test for a real live bug, 2026-08-25 (ignite_qpid_protocol):
    subtask s1's own internal run_generation_workflow() call hit
    KnowledgeGuard's stage-0 gap check and returned status='knowledge_gap' -
    previously flattened into a generic 'did not pass Quality Gates' failure
    with the real gap_report silently discarded, so the CLI's own already-
    built confirmation UX (kriya/cli.py's `res.get('status') ==
    'knowledge_gap'` check) could never fire for enforce mode at all. The run
    produced zero files with zero visible explanation - the actual real-world
    symptom. Scoped to the SAME safety boundary _StructuredPlanUnavailable
    already uses (only when literally nothing has been established yet)."""
    plan = _two_subtask_plan()
    we = _workflow_engine()

    async def fake_run(**kwargs):
        return {
            "status": "knowledge_gap",
            "gap_report": {"gaps": [{"library": "org.apache.ignite:ignite-core", "version": "2.18.0"}]},
            "run_id": "gap-run-1",
        }

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "knowledge_gap"
    assert result.legacy_result["gap_report"] == {
        "gaps": [{"library": "org.apache.ignite:ignite-core", "version": "2.18.0"}]
    }
    assert result.legacy_result["run_id"] == "gap-run-1"
    # The real per-subtask diagnostic detail must still be there too (used by
    # the CLI's "No files written" fallback branch for any OTHER failure kind).
    assert result.legacy_result["subtask_results"][0]["error"]


@pytest.mark.asyncio
async def test_enforce_does_not_surface_knowledge_gap_from_a_later_subtask(tmp_path):
    """A knowledge gap on a LATER subtask, after real prior work already
    exists, must NOT promote to the top-level knowledge_gap status - that
    would invite the CLI's retry flow to silently re-run already-completed
    subtasks from scratch. Stays the ordinary generic failure instead."""
    plan = _two_subtask_plan()
    we = _workflow_engine()

    async def fake_run(**kwargs):
        if "'s2'" in kwargs["goal"]:
            return {"status": "knowledge_gap", "gap_report": {"gaps": []}, "run_id": "gap-run-2"}
        (tmp_path / "a.py").write_text("# a.py")
        return {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "failed"
    assert "gap_report" not in result.legacy_result


@pytest.mark.asyncio
async def test_enforce_never_forwards_trace_id_override_to_a_subtask_call(tmp_path):
    """trace_id_override (kriya/cli.py) is meant to overwrite the ONE
    transient knowledge_gap trace row a single whole-goal retry produces -
    forwarding it unfiltered to every subtask call in this loop would give
    them all the SAME trace id, and since traces.db does INSERT OR REPLACE
    keyed by run_id, each subtask's own real trace row would silently
    overwrite the previous subtask's. Every OTHER legacy kwarg must still
    pass through untouched."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    captured = []

    async def fake_run(**kwargs):
        captured.append(kwargs)
        path = "a.py" if len(captured) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute(
            "goal", str(tmp_path), migration_mode="enforce",
            trace_id_override="should-never-appear", knowledge_risk_confirmed=True,
        )

    assert len(captured) == 2
    for kwargs in captured:
        assert "trace_id_override" not in kwargs
        assert kwargs["knowledge_risk_confirmed"] is True


# --- bounded authoritative plan repair (before any implementation) ---

@pytest.mark.asyncio
async def test_enforce_repairs_late_unscoped_model_subtask_before_execution(tmp_path):
    bad_plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(id="s6", description="run tests", execution_method=ExecutionMethod.MODEL)],
    )
    good_plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s6", description="write report", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="report.txt", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.planner.run = AsyncMock(side_effect=["initial plan", "corrected plan"])
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["report.txt"],
    })
    validations = [
        PlanValidationResult(
            valid=False,
            errors=["subtask 's6' uses execution_method=model but declares no planned_files"],
            reason_codes=["MODEL_SUBTASK_MISSING_PLANNED_FILES"],
        ),
        PlanValidationResult(valid=True),
    ]
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), patch(
        "kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
        side_effect=[bad_plan, good_plan],
    ), patch(
        "kriya.workflow.workflow_controller.validate_plan",
        new=AsyncMock(side_effect=validations),
    ):
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert we.planner.run.await_count == 2
    repair_prompt = we.planner.run.await_args_list[1].args[0]
    assert "PLAN_REPAIR" in repair_prompt
    assert "MODEL_SUBTASK_MISSING_PLANNED_FILES" in repair_prompt
    assert "s6" in repair_prompt
    we.run_generation_workflow.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_repairs_unknown_global_invariant_reference_by_id(tmp_path):
    """Regression test for PRV-06 (2026-08-28): the exact live failure shape
    - a subtask references a global invariant id that isn't declared yet.
    Unlike the pre-fix text-matching contract, this converges in exactly one
    repair round once the model swaps in a declared id, because the
    validator and the repair-prompt guidance both operate on ids, not
    free-text reproduction."""
    compound = GlobalInvariant(
        id="gi_retrieve_print",
        statement="The application must retrieve the value from that service and print it.",
    )
    bad_plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK, global_invariants=[compound],
        subtasks=[Subtask(
            id="s2", description="service", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="Service.java", action=FileAction.CREATE)],
            relevant_global_invariant_ids=["gi_ghost"],
        )],
    )
    good_plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK, global_invariants=[compound],
        subtasks=[Subtask(
            id="s2", description="service", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="Service.java", action=FileAction.CREATE)],
            relevant_global_invariant_ids=["gi_retrieve_print"],
        )],
    )
    we = _workflow_engine()
    we.planner.run = AsyncMock(side_effect=["initial plan", "corrected plan"])
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["Service.java"],
    })
    validations = [
        PlanValidationResult(
            valid=False,
            errors=["subtask 's2' references unknown global invariant id(s): ['gi_ghost']; "
                    "declared ids are ['gi_retrieve_print']"],
            reason_codes=["UNKNOWN_GLOBAL_INVARIANT"],
        ),
        PlanValidationResult(valid=True),
    ]
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), patch(
        "kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
        side_effect=[bad_plan, good_plan],
    ), patch(
        "kriya.workflow.workflow_controller.validate_plan",
        new=AsyncMock(side_effect=validations),
    ):
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert we.planner.run.await_count == 2
    repair_prompt = we.planner.run.await_args_list[1].args[0]
    assert "gi_ghost" in repair_prompt
    assert "declared ids are" in repair_prompt
    assert "replace that entry with one of the declared ids" in repair_prompt


@pytest.mark.asyncio
async def test_enforce_structured_plan_unavailable_never_degrades_to_legacy(tmp_path):
    we = _workflow_engine()
    controller = WorkflowController(we)
    controller._run_structured_enforce = AsyncMock(
        side_effect=_StructuredPlanUnavailable("planner transport unavailable")
    )
    controller._run_legacy_generation = AsyncMock(
        side_effect=AssertionError("authoritative mode must not invoke legacy generation")
    )
    result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")
    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["reason_codes"] == ["STRUCTURED_PLAN_UNAVAILABLE"]
    controller._run_legacy_generation.assert_not_called()


@pytest.mark.asyncio
async def test_enforce_plan_repair_exhaustion_fails_closed_without_legacy_execution(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(id="s1", description="do something", execution_method=ExecutionMethod.MODEL)],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock()
    p1, p2, p3 = _patched(
        plan,
        validation_result=PlanValidationResult(
            valid=False, errors=["missing scope"],
            reason_codes=["MODEL_SUBTASK_MISSING_PLANNED_FILES"],
        ),
    )
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["failure_type"] == "PLANNING_ERROR"
    assert result.legacy_result["recovery"] == "PLAN_REPAIR"
    assert result.legacy_result["plan_repair_attempts"] == 2
    assert "STRUCTURED_PLAN_REPAIR_EXHAUSTED" in result.legacy_result["reason_codes"]
    assert "MODEL_SUBTASK_MISSING_PLANNED_FILES" in result.legacy_result["reason_codes"]
    assert we.planner.run.await_count == 3
    we.run_generation_workflow.assert_not_awaited()
    diagnostics = [
        json.loads(line)
        for line in open(
            planning_diagnostics_path(str(tmp_path), result.run_id), encoding="utf-8",
        )
    ]
    assert [record["attempt"] for record in diagnostics] == [0, 1, 2]
    assert diagnostics[0]["repair_prompt"] is not None
    assert diagnostics[1]["repair_prompt"] is not None
    assert diagnostics[2]["repair_prompt"] is None


# --- Grounded plan-completeness: missing production artifact (PRV-11,
# 2026-08-30). Traced live: the Planner produced Customer -> CustomerService
# -> CustomerControllerTest directly, omitting CustomerController.java (the
# real intermediate consumer/producer) entirely - nothing available to
# planning ever named the relationship. build_planning_structural_evidence()
# derives real, grounded import/call/constructor-instantiation relationships
# from a bounded, in-memory DependencyGraph; find_missing_grounded_
# production_artifacts() flags a test file's own grounded reference to a
# production file no subtask owns. ---

_STRUCTURAL_CUSTOMER_JAVA = (
    "package com.example.customer;\n"
    "public record Customer(long id, String firstName, String lastName) {\n"
    "    public String displayName() { return (firstName + \" \" + lastName).toUpperCase(); }\n"
    "}\n"
)
_STRUCTURAL_CUSTOMER_SERVICE_JAVA = (
    "package com.example.customer;\n"
    "public class CustomerService {\n"
    "    public Customer find(long id) { return new Customer(id, \"John\", \"Smith\"); }\n"
    "}\n"
)
_STRUCTURAL_CUSTOMER_CONTROLLER_JAVA = (
    "package com.example.customer;\n"
    "import java.util.*;\n"
    "public class CustomerController {\n"
    " private final CustomerService service=new CustomerService();\n"
    " public Map<String,Object> details(long id){\n"
    "   Customer c=service.find(id); Map<String,Object> m=new LinkedHashMap<>();\n"
    "   m.put(\"displayName\",c.displayName()); return m;\n"
    " }\n"
    "}\n"
)
_STRUCTURAL_CUSTOMER_CONTROLLER_TEST_JAVA = (
    "package com.example.customer;\n"
    "import static org.junit.jupiter.api.Assertions.*;\n"
    "import org.junit.jupiter.api.Test;\n"
    "class CustomerControllerTest {\n"
    " @Test void detailsIncludesUppercaseDisplayName() {\n"
    "   CustomerController controller = new CustomerController();\n"
    "   Object displayName = controller.details(1L).get(\"displayName\");\n"
    "   assertEquals(\"JOHN SMITH\", displayName);\n"
    " }\n"
    "}\n"
)

_CUSTOMER_PATH = "src/main/java/com/example/customer/Customer.java"
_CUSTOMER_SERVICE_PATH = "src/main/java/com/example/customer/CustomerService.java"
_CUSTOMER_CONTROLLER_PATH = "src/main/java/com/example/customer/CustomerController.java"
_CUSTOMER_CONTROLLER_TEST_PATH = "src/test/java/com/example/customer/CustomerControllerTest.java"


def _seed_structural_customer_repo(tmp_path):
    files = {
        _CUSTOMER_PATH: _STRUCTURAL_CUSTOMER_JAVA,
        _CUSTOMER_SERVICE_PATH: _STRUCTURAL_CUSTOMER_SERVICE_JAVA,
        _CUSTOMER_CONTROLLER_PATH: _STRUCTURAL_CUSTOMER_CONTROLLER_JAVA,
        _CUSTOMER_CONTROLLER_TEST_PATH: _STRUCTURAL_CUSTOMER_CONTROLLER_TEST_JAVA,
    }
    for relpath, content in files.items():
        full = tmp_path / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return list(files.keys())


def _structural_customer_subtask(
    sid, path, *, depends_on=(), requires=(), provides=(),
):
    return Subtask(
        id=sid, description=f"work on {path}", execution_method=ExecutionMethod.MODEL,
        depends_on=list(depends_on),
        requires=list(requires), provides=list(provides),
        planned_files=[PlannedFile(path=path, action=FileAction.MODIFY)],
    )


def test_structural_evidence_resolves_imports_calls_and_constructor_instantiation(tmp_path):
    """Real repository content, real (in-memory, ephemeral) DependencyGraph -
    proves all three resolution paths this fix needs: an explicit import
    (java.util.* doesn't resolve to anything IN the candidate set, correctly
    producing nothing for it), a uniquely-resolved method call (service.find
    -> CustomerService.java), and constructor instantiation ("new
    CustomerController()") resolving the test -> controller edge that a bare
    method-name lookup alone would miss (CustomerController.details()'s own
    Map<String,Object> return type breaks _parse_java()'s method regex -
    found live tracing the exact PRV-11 fixture, not assumed)."""
    candidates = _seed_structural_customer_repo(tmp_path)

    text, edges = build_planning_structural_evidence(str(tmp_path), candidates)

    assert _CUSTOMER_SERVICE_PATH in edges[_CUSTOMER_CONTROLLER_PATH]
    assert _CUSTOMER_CONTROLLER_PATH in edges[_CUSTOMER_CONTROLLER_TEST_PATH]
    assert f"{_CUSTOMER_CONTROLLER_TEST_PATH} references -> {_CUSTOMER_CONTROLLER_PATH}" in text
    # Never raw source content - only compact "A references -> B" lines.
    assert "public class" not in text
    assert "displayName" not in text


def test_structural_evidence_is_empty_for_candidates_with_no_real_relationship():
    """No false positives: an unrelated file never produces an edge."""
    text, edges = build_planning_structural_evidence("/nonexistent-path-xyz", ["a.java", "b.java"])
    assert text == ""
    assert edges == {}


def test_missing_grounded_production_artifact_flags_the_exact_omitted_file(tmp_path):
    candidates = _seed_structural_customer_repo(tmp_path)
    _, edges = build_planning_structural_evidence(str(tmp_path), candidates)
    incomplete_plan = EngineeringPlan(
        plan_id="incomplete", kind=ChangeKind.ENHANCEMENT,
        subtasks=[
            _structural_customer_subtask("s1", _CUSTOMER_PATH),
            _structural_customer_subtask("s2", _CUSTOMER_SERVICE_PATH, depends_on=["s1"]),
            _structural_customer_subtask("s3", _CUSTOMER_CONTROLLER_TEST_PATH, depends_on=["s2"]),
        ],
    )

    gaps = find_missing_grounded_production_artifacts(incomplete_plan, edges)

    assert gaps == [{
        "test_file": _CUSTOMER_CONTROLLER_TEST_PATH,
        "missing_production_artifact": _CUSTOMER_CONTROLLER_PATH,
        "reason": "unowned",
    }]


_WIDGET_JAVA = (
    "package com.example.widget;\n"
    "public class Widget {\n"
    "    public String label() { return \"widget\"; }\n"
    "}\n"
)
_WIDGET_SERVICE_JAVA = (
    "package com.example.widget;\n"
    "public class WidgetService {\n"
    "    public Widget make() { return new Widget(); }\n"
    "}\n"
)
_WIDGET_PATH = "src/main/java/com/example/widget/Widget.java"
_WIDGET_SERVICE_PATH = "src/main/java/com/example/widget/WidgetService.java"


def _seed_single_production_dependency_repo(tmp_path):
    files = {_WIDGET_PATH: _WIDGET_JAVA, _WIDGET_SERVICE_PATH: _WIDGET_SERVICE_JAVA}
    for relpath, content in files.items():
        full = tmp_path / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return list(files.keys())


def test_missing_grounded_production_artifact_is_silent_for_production_source_with_one_unplanned_dependency(tmp_path):
    """(2026-09-04) Source-role scoping fix: a PRODUCTION source referencing
    a single existing, unplanned production dependency (Widget.java is
    ordinary brownfield WidgetService.java already correctly depends on) is
    not the PRV-11 omitted-owner shape - only a test source omitting a
    production owner is."""
    candidates = _seed_single_production_dependency_repo(tmp_path)
    _, edges = build_planning_structural_evidence(str(tmp_path), candidates)
    assert edges[_WIDGET_SERVICE_PATH] == [_WIDGET_PATH]
    plan = EngineeringPlan(
        plan_id="widget-service-only", kind=ChangeKind.ENHANCEMENT,
        subtasks=[_structural_customer_subtask("s1", _WIDGET_SERVICE_PATH)],
    )

    assert find_missing_grounded_production_artifacts(plan, edges) == []


def test_missing_grounded_production_artifact_is_silent_for_production_source_with_multiple_unplanned_dependencies(tmp_path):
    """Same shape as above but with a source (CustomerController.java) that
    structurally references TWO unplanned production files (Customer.java
    AND CustomerService.java) - neither is flagged, since the source is
    production, not a test."""
    candidates = _seed_structural_customer_repo(tmp_path)
    _, edges = build_planning_structural_evidence(str(tmp_path), candidates)
    assert set(edges[_CUSTOMER_CONTROLLER_PATH]) == {_CUSTOMER_PATH, _CUSTOMER_SERVICE_PATH}
    plan = EngineeringPlan(
        plan_id="controller-only", kind=ChangeKind.ENHANCEMENT,
        subtasks=[_structural_customer_subtask("s1", _CUSTOMER_CONTROLLER_PATH)],
    )

    assert find_missing_grounded_production_artifacts(plan, edges) == []


def test_missing_grounded_production_artifact_is_silent_when_the_owner_is_planned(tmp_path):
    candidates = _seed_structural_customer_repo(tmp_path)
    _, edges = build_planning_structural_evidence(str(tmp_path), candidates)
    complete_plan = EngineeringPlan(
        plan_id="complete", kind=ChangeKind.ENHANCEMENT,
        subtasks=[
            _structural_customer_subtask("s1", _CUSTOMER_PATH),
            _structural_customer_subtask("s2", _CUSTOMER_SERVICE_PATH, depends_on=["s1"]),
            _structural_customer_subtask(
                "s3", _CUSTOMER_CONTROLLER_PATH, depends_on=["s2"],
                provides=["controller_cap"],
            ),
            _structural_customer_subtask(
                "s4", _CUSTOMER_CONTROLLER_TEST_PATH, depends_on=["s3"],
                requires=["controller_cap"],
            ),
        ],
    )

    assert find_missing_grounded_production_artifacts(complete_plan, edges) == []


def test_missing_grounded_production_artifact_flags_edge_miswiring_when_owner_exists(tmp_path):
    """(2026-08-31) The live incident this closes: the Planner correctly
    creates s3=CustomerController.java (unlike the "unowned" shape above),
    but s4's own requires/depends_on skips past it and points directly at
    s2=CustomerService.java instead - the exact shape that made
    FUTURE_ORDERED handling correctly refuse to defer (s2 IS the declared
    provider, and it IS the subtask currently executing) while the real
    fix belonged in s3, which was never reachable from s4's own
    depends_on chain at all."""
    candidates = _seed_structural_customer_repo(tmp_path)
    _, edges = build_planning_structural_evidence(str(tmp_path), candidates)
    miswired_plan = EngineeringPlan(
        plan_id="miswired", kind=ChangeKind.ENHANCEMENT,
        subtasks=[
            _structural_customer_subtask("s1", _CUSTOMER_PATH),
            _structural_customer_subtask("s2", _CUSTOMER_SERVICE_PATH, depends_on=["s1"]),
            _structural_customer_subtask("s3", _CUSTOMER_CONTROLLER_PATH, depends_on=["s2"]),
            # s4 exists and s3 IS a real, owned subtask - but s4 depends
            # only on s2, never on s3.
            _structural_customer_subtask("s4", _CUSTOMER_CONTROLLER_TEST_PATH, depends_on=["s2"]),
        ],
    )

    gaps = find_missing_grounded_production_artifacts(miswired_plan, edges)

    assert gaps == [{
        "test_file": _CUSTOMER_CONTROLLER_TEST_PATH,
        "missing_production_artifact": _CUSTOMER_CONTROLLER_PATH,
        "owning_subtask": "s3",
        "reason": "not_in_dependency_chain",
    }]


def test_grounded_edge_rejects_semantic_provider_mismatch_even_with_both_dependencies(tmp_path):
    """depends_on alone is insufficient: runtime FUTURE_OWNER routing uses
    requires -> provides and must resolve to the grounded artifact owner."""
    candidates = _seed_structural_customer_repo(tmp_path)
    _, edges = build_planning_structural_evidence(str(tmp_path), candidates)
    semantically_miswired_plan = EngineeringPlan(
        plan_id="semantic-miswire", kind=ChangeKind.ENHANCEMENT,
        subtasks=[
            _structural_customer_subtask("s1", _CUSTOMER_PATH),
            _structural_customer_subtask(
                "s2", _CUSTOMER_SERVICE_PATH, depends_on=["s1"],
                provides=["service_cap"],
            ),
            _structural_customer_subtask(
                "s3", _CUSTOMER_CONTROLLER_PATH, depends_on=["s2"],
                provides=["controller_cap"],
            ),
            _structural_customer_subtask(
                "s4", _CUSTOMER_CONTROLLER_TEST_PATH, depends_on=["s2", "s3"],
                requires=["service_cap"],
            ),
        ],
    )

    assert find_missing_grounded_production_artifacts(
        semantically_miswired_plan, edges,
    ) == [{
        "test_file": _CUSTOMER_CONTROLLER_TEST_PATH,
        "missing_production_artifact": _CUSTOMER_CONTROLLER_PATH,
        "owning_subtask": "s3",
        "owner_provides": ["controller_cap"],
        "test_requires": ["service_cap"],
        "reason": "semantic_provider_mismatch",
    }]


def test_grounded_plan_completeness_matrix_rejects_absence_and_miswire_then_accepts_alignment(tmp_path):
    candidates = _seed_structural_customer_repo(tmp_path)
    _, edges = build_planning_structural_evidence(str(tmp_path), candidates)

    def plan(*subtasks, plan_id):
        return EngineeringPlan(
            plan_id=plan_id, kind=ChangeKind.ENHANCEMENT, subtasks=list(subtasks),
        )

    base = _structural_customer_subtask("s1", _CUSTOMER_PATH)
    service = _structural_customer_subtask(
        "s2", _CUSTOMER_SERVICE_PATH, depends_on=["s1"], provides=["service_cap"],
    )
    controller = _structural_customer_subtask(
        "s3", _CUSTOMER_CONTROLLER_PATH, depends_on=["s2"],
        requires=["service_cap"], provides=["controller_cap"],
    )
    test_via_service = _structural_customer_subtask(
        "s4", _CUSTOMER_CONTROLLER_TEST_PATH, depends_on=["s2"],
        requires=["service_cap"],
    )
    test_miswired = _structural_customer_subtask(
        "s4", _CUSTOMER_CONTROLLER_TEST_PATH, depends_on=["s2", "s3"],
        requires=["service_cap"],
    )
    test_aligned = _structural_customer_subtask(
        "s4", _CUSTOMER_CONTROLLER_TEST_PATH, depends_on=["s3"],
        requires=["controller_cap"],
    )

    plan_a = plan(base, service, test_via_service, plan_id="missing-owner")
    plan_b = plan(base, service, controller, test_miswired, plan_id="wrong-provider")
    plan_c = plan(base, service, controller, test_aligned, plan_id="aligned")

    assert find_missing_grounded_production_artifacts(plan_a, edges)[0]["reason"] == "unowned"
    assert find_missing_grounded_production_artifacts(plan_b, edges)[0]["reason"] == "semantic_provider_mismatch"
    assert find_missing_grounded_production_artifacts(plan_c, edges) == []


def test_authoritative_planner_request_includes_structural_evidence_when_present():
    with_evidence = build_authoritative_planner_request(
        "goal", structural_evidence="a.java references -> b.java",
    )
    without_evidence = build_authoritative_planner_request("goal")

    assert "a.java references -> b.java" in with_evidence
    assert "Grounded structural evidence" in with_evidence
    assert "Grounded structural evidence" not in without_evidence


@pytest.mark.asyncio
async def test_enforce_flags_missing_grounded_production_artifact_and_exhausts_on_repair(tmp_path):
    """Integration proof this fires through the REAL enforce loop, not just
    the isolated functions: validate_plan() itself is mocked valid=True (the
    _patched() default) - this check must independently block regardless,
    proving it is a genuinely additive gate, not dependent on validate_plan
    somehow already covering it."""
    _seed_structural_customer_repo(tmp_path)
    incomplete_plan = EngineeringPlan(
        plan_id="incomplete", kind=ChangeKind.ENHANCEMENT,
        subtasks=[
            _structural_customer_subtask("s1", _CUSTOMER_PATH),
            _structural_customer_subtask("s2", _CUSTOMER_SERVICE_PATH, depends_on=["s1"]),
            _structural_customer_subtask("s3", _CUSTOMER_CONTROLLER_TEST_PATH, depends_on=["s2"]),
        ],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock()
    p1, p2, p3 = _patched(incomplete_plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "Add an uppercase displayName to the customer lookup response.",
            str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "needs_review"
    assert "STRUCTURED_PLAN_REPAIR_EXHAUSTED" in result.legacy_result["reason_codes"]
    assert "MISSING_GROUNDED_PRODUCTION_ARTIFACT" in result.legacy_result["reason_codes"]
    we.run_generation_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_enforce_reports_grounded_semantic_provider_mismatch(tmp_path):
    _seed_structural_customer_repo(tmp_path)
    semantic_miswire = EngineeringPlan(
        plan_id="semantic-miswire", kind=ChangeKind.ENHANCEMENT,
        subtasks=[
            _structural_customer_subtask("s1", _CUSTOMER_PATH),
            _structural_customer_subtask(
                "s2", _CUSTOMER_SERVICE_PATH, depends_on=["s1"],
                provides=["service_cap"],
            ),
            _structural_customer_subtask(
                "s3", _CUSTOMER_CONTROLLER_PATH, depends_on=["s2"],
                provides=["controller_cap"],
            ),
            _structural_customer_subtask(
                "s4", _CUSTOMER_CONTROLLER_TEST_PATH, depends_on=["s2", "s3"],
                requires=["service_cap"],
            ),
        ],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock()
    p1, p2, p3 = _patched(semantic_miswire)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "Change lookup behavior and verify it",
            str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "needs_review"
    assert "GROUNDED_SEMANTIC_PROVIDER_MISMATCH" in result.legacy_result["reason_codes"]
    we.run_generation_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_enforce_complete_plan_is_not_blocked_by_structural_completeness_check(tmp_path):
    """The positive case: when every grounded relationship IS covered by
    some subtask's own planned_files, this check contributes nothing and
    the run proceeds to subtask execution normally on the first attempt."""
    _seed_structural_customer_repo(tmp_path)
    complete_plan = EngineeringPlan(
        plan_id="complete", kind=ChangeKind.ENHANCEMENT,
        subtasks=[
            _structural_customer_subtask("s1", _CUSTOMER_PATH),
            _structural_customer_subtask("s2", _CUSTOMER_SERVICE_PATH, depends_on=["s1"]),
            _structural_customer_subtask(
                "s3", _CUSTOMER_CONTROLLER_PATH, depends_on=["s2"],
                provides=["controller_cap"],
            ),
            _structural_customer_subtask(
                "s4", _CUSTOMER_CONTROLLER_TEST_PATH, depends_on=["s3"],
                requires=["controller_cap"],
            ),
        ],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": [],
    })
    p1, p2, p3 = _patched(complete_plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "Add an uppercase displayName to the customer lookup response.",
            str(tmp_path), migration_mode="enforce",
        )

    diagnostics = [
        json.loads(line)
        for line in open(
            planning_diagnostics_path(str(tmp_path), result.run_id), encoding="utf-8",
        )
    ]
    assert len(diagnostics) == 1
    assert "MISSING_GROUNDED_PRODUCTION_ARTIFACT" not in diagnostics[0]["reason_codes"]
    assert diagnostics[0]["approved_plan"] == {
        "plan_id": "complete",
        "subtasks": [
            {
                "id": "s1", "planned_files": [{
                    "path": _CUSTOMER_PATH, "action": "modify",
                    "environment_requirements": [], "requires_capabilities": [],
                }],
                "depends_on": [], "requires": [], "provides": [],
            },
            {
                "id": "s2",
                "planned_files": [{
                    "path": _CUSTOMER_SERVICE_PATH, "action": "modify",
                    "environment_requirements": [], "requires_capabilities": [],
                }],
                "depends_on": ["s1"], "requires": [], "provides": [],
            },
            {
                "id": "s3",
                "planned_files": [{
                    "path": _CUSTOMER_CONTROLLER_PATH, "action": "modify",
                    "environment_requirements": [], "requires_capabilities": [],
                }],
                "depends_on": ["s2"], "requires": [], "provides": ["controller_cap"],
            },
            {
                "id": "s4",
                "planned_files": [{
                    "path": _CUSTOMER_CONTROLLER_TEST_PATH, "action": "modify",
                    "environment_requirements": [], "requires_capabilities": [],
                }],
                "depends_on": ["s3"], "requires": ["controller_cap"], "provides": [],
            },
        ],
    }
    assert we.planner.run.await_count == 1


@pytest.mark.asyncio
async def test_enforce_surfaces_grounded_out_of_scope_repair_as_plan_revision(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="implement application", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="src/App.java", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "failed", "quality_gates_passed": False, "files": [],
        "plan_scope_conflict": {
            "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
            "failure_type": "misdirected_edit",
            "required_files": ["pom.xml"],
            "allowed_files": ["src/App.java"],
        },
    })
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["failure_type"] == "PLANNING_ERROR"
    assert result.legacy_result["recovery"] == "PLAN_REPAIR"
    assert result.legacy_result["reason_codes"] == ["PLAN_SCOPE_REVISION_REQUIRED"]
    assert result.subtask_results[0].status == SubtaskStatus.NEEDS_REVIEW
    assert result.subtask_results[0].reason_codes == ("PLAN_SCOPE_REVISION_REQUIRED",)


@pytest.mark.asyncio
async def test_enforce_reopens_unique_upstream_owner_and_reruns_consumer(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="framework dependencies must be resolved")],
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
                provides=["build.dependencies.ready"],
                relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="s3", description="create application", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/App.java", action=FileAction.CREATE)],
                requires=["build.dependencies.ready"],
                relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if len(calls) == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "misdirected_edit",
                    "required_files": ["pom.xml"],
                    "allowed_files": ["src/App.java"],
                },
            }
        if len(calls) == 3:
            (tmp_path / "pom.xml").write_text("<project><dependencies/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "App.java").write_text("class App {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["src/App.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert result.legacy_result["quality_gates_passed"] is True
    assert all(item.status == SubtaskStatus.COMPLETED for item in result.subtask_results)
    assert result.legacy_result["last_subtask_result"]["quality_gates_passed"] is True
    assert len(calls) == 4
    assert calls[1]["allowed_write_relpaths"] == ["src/App.java"]
    assert calls[2]["allowed_write_relpaths"] == ["pom.xml"]
    assert calls[3]["allowed_write_relpaths"] == ["src/App.java"]
    assert result.legacy_result["plan_recovery_events"] == [{
        "failed_subtask": "s3",
        "reopened_owner": "s1",
        "required_repair_files": ["pom.xml"],
        "classification": "PLAN_SCOPE_INSUFFICIENT",
        "reason": None,
        "invalidated_subtasks": ["s3"],
        "ownership_revalidated": True,
        "revalidation_basis": "unique approved upstream file owner",
        "owner_recovery_passed": True,
        # MA8.1 completion (2026-08-29 v2): requirement-scoped identity,
        # not just the owner id - see _cross_owner_obligation_id.
        "requirement_id": "recovery.s3.s1.pom.xml.misdirected_edit.0",
        "generation": 0,
    }]


@pytest.mark.asyncio
async def test_enforce_owner_recovery_no_progress_blocks_completion_and_never_resumes_consumer(tmp_path):
    """Recovery Execution Contract (PRV-06, 2026-08-29): the live incident
    this closes reopened pom.xml across THREE separate generations, each
    regenerating byte-identical content that still failed the real (cross-
    owner) requirement while its own local gates happily kept saying PASS -
    burning a full consumer-retry cycle each time before the same defect
    resurfaced. This test pins down generation 0 of that failure mode in
    isolation: s1 owns build.config (baseline references OldMain), s2 is
    the consumer that needs it to reference RequiredMain and cannot edit
    build.config itself. When s1's owner-recovery attempt regenerates
    build.config UNCHANGED (still OldMain), that must be treated as
    RECOVERY_NO_PROGRESS regardless of its own quality_gates_passed=True -
    owner_recovery_passed must be False, s1 must land in NEEDS_REVIEW (not
    COMPLETED), and - critically - the consumer must NEVER be re-invoked
    off the back of a no-progress "recovery". Compare
    test_enforce_reopens_unique_upstream_owner_and_reruns_consumer just
    above, which is the mirror-image PROGRESS case (corrected, non-
    identical content) and must keep succeeding unchanged."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build config", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="build.config", action=FileAction.CREATE)],
                provides=["entrypoint.configured"],
            ),
            Subtask(
                id="s2", description="create application entrypoint", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="app_entrypoint.txt", action=FileAction.CREATE)],
                requires=["entrypoint.configured"],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "build.config").write_text("entrypoint=OldMain")
            return {"status": "success", "quality_gates_passed": True, "files": ["build.config"]}
        if len(calls) == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "misdirected_edit",
                    "required_files": ["build.config"],
                    "allowed_files": ["app_entrypoint.txt"],
                },
            }
        # Generation 0's owner-recovery candidate: byte-identical to what
        # was already there before recovery started - its own local gate
        # (nothing there can see the cross-owner RequiredMain requirement)
        # happily reports success anyway.
        (tmp_path / "build.config").write_text("entrypoint=OldMain")
        return {"status": "success", "quality_gates_passed": True, "files": ["build.config"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    # Only s1's initial pass, s2's failing attempt, and s1's one no-progress
    # recovery attempt happened - the consumer was never re-invoked off the
    # back of a "recovery" that changed nothing.
    assert len(calls) == 3
    assert result.legacy_result["status"] != "success"
    assert len(result.legacy_result["plan_recovery_events"]) == 1
    recovery_event = result.legacy_result["plan_recovery_events"][0]
    assert recovery_event["failed_subtask"] == "s2"
    assert recovery_event["reopened_owner"] == "s1"
    assert recovery_event["owner_recovery_passed"] is False
    assert recovery_event["generation"] == 0
    owner_result = next(item for item in result.subtask_results if item.subtask_id == "s1")
    assert owner_result.status == SubtaskStatus.NEEDS_REVIEW
    assert "PLAN_RECOVERY_OWNER_FAILED" in owner_result.reason_codes
    consumer_result = next(item for item in result.subtask_results if item.subtask_id == "s2")
    assert consumer_result.status != SubtaskStatus.COMPLETED
    # build.config on disk is still exactly the unfixed baseline - nothing
    # about the "recovery" actually changed it.
    assert (tmp_path / "build.config").read_text() == "entrypoint=OldMain"


@pytest.mark.asyncio
async def test_enforce_owner_recovery_wrong_fix_blocks_completion_and_never_resumes_consumer(tmp_path):
    """Recovery Execution Contract Invariant 3 (2026-08-29): a changed
    candidate can still be wrong - RECOVERY_NO_PROGRESS (see the sibling
    test just above) only catches a BYTE-IDENTICAL regeneration. This is
    the general case: s1's owner-recovery attempt genuinely CHANGES
    build.config (so no-progress does not fire) but still does not
    reference the authoritative entrypoint (RequiredMain) the originating
    scope_conflict names via `required_reference_token` - a deterministic,
    pre-consumer acceptance check (no LLM call, no downstream subtask
    re-invocation) must reject this BEFORE the consumer is ever resumed.
    "owner_local_accepted (local gates PASS) AND candidate changed" must
    NOT be treated as recovery success - only "AND the originating
    requirement is actually satisfied" may be. Compare
    test_enforce_owner_recovery_correct_fix_via_reference_token_completes_
    and_resumes_consumer just below (the same mechanism's ACCEPT path)."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build config", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="build.config", action=FileAction.CREATE)],
                provides=["entrypoint.configured"],
            ),
            Subtask(
                id="s2", description="create application entrypoint", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="app_entrypoint.txt", action=FileAction.CREATE)],
                requires=["entrypoint.configured"],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "build.config").write_text("entrypoint=OldMain")
            return {"status": "success", "quality_gates_passed": True, "files": ["build.config"]}
        if len(calls) == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "misdirected_edit",
                    "required_files": ["build.config"],
                    "allowed_files": ["app_entrypoint.txt"],
                    "required_reference_token": "RequiredMain",
                },
            }
        # Generation 0's owner-recovery candidate: genuinely CHANGED from
        # the baseline (so RECOVERY_NO_PROGRESS does not fire) but still
        # wrong - references a DIFFERENT incorrect entrypoint, never the
        # authoritative "RequiredMain" the scope_conflict names.
        (tmp_path / "build.config").write_text("entrypoint=DifferentButStillWrongMain")
        return {"status": "success", "quality_gates_passed": True, "files": ["build.config"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    # Only s1's initial pass, s2's failing attempt, and s1's one rejected
    # recovery attempt happened - the consumer was never re-invoked to
    # discover what a cheap, deterministic check already knew.
    assert len(calls) == 3
    assert result.legacy_result["status"] != "success"
    assert len(result.legacy_result["plan_recovery_events"]) == 1
    recovery_event = result.legacy_result["plan_recovery_events"][0]
    assert recovery_event["owner_recovery_passed"] is False
    assert recovery_event["generation"] == 0
    owner_result = next(item for item in result.subtask_results if item.subtask_id == "s1")
    assert owner_result.status == SubtaskStatus.NEEDS_REVIEW
    assert "PLAN_RECOVERY_OWNER_FAILED" in owner_result.reason_codes
    consumer_result = next(item for item in result.subtask_results if item.subtask_id == "s2")
    assert consumer_result.status != SubtaskStatus.COMPLETED
    # The still-wrong candidate is what's on disk - Kriya never pretended
    # otherwise, but it also never let the consumer act on it as if fixed.
    assert (tmp_path / "build.config").read_text() == "entrypoint=DifferentButStillWrongMain"


@pytest.mark.asyncio
async def test_enforce_owner_recovery_correct_fix_via_reference_token_completes_and_resumes_consumer(tmp_path):
    """Recovery Execution Contract Invariant 3 (2026-08-29), ACCEPT path:
    the same deterministic required_reference_token mechanism that
    rejected a still-wrong candidate in the sibling test just above must
    also correctly ACCEPT a genuine fix - owner local gates PASS AND the
    authoritative entrypoint is now actually referenced -> recovery
    accepted -> consumer resumes -> consumer succeeds. Together with the
    no-progress test and the wrong-fix test just above, these three prove
    the full contract: same wrong -> reject as no progress, different
    wrong -> reject as unmet recovery, correct -> accept."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build config", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="build.config", action=FileAction.CREATE)],
                provides=["entrypoint.configured"],
            ),
            Subtask(
                id="s2", description="create application entrypoint", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="app_entrypoint.txt", action=FileAction.CREATE)],
                requires=["entrypoint.configured"],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "build.config").write_text("entrypoint=OldMain")
            return {"status": "success", "quality_gates_passed": True, "files": ["build.config"]}
        if len(calls) == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "misdirected_edit",
                    "required_files": ["build.config"],
                    "allowed_files": ["app_entrypoint.txt"],
                    "required_reference_token": "RequiredMain",
                },
            }
        if len(calls) == 3:
            # Generation 0's owner-recovery candidate: the genuine fix -
            # references the authoritative entrypoint the scope_conflict
            # named.
            (tmp_path / "build.config").write_text("entrypoint=RequiredMain")
            return {"status": "success", "quality_gates_passed": True, "files": ["build.config"]}
        (tmp_path / "app_entrypoint.txt").write_text("RequiredMain")
        return {"status": "success", "quality_gates_passed": True, "files": ["app_entrypoint.txt"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 4
    assert all(item.status == SubtaskStatus.COMPLETED for item in result.subtask_results)
    recovery_event = result.legacy_result["plan_recovery_events"][0]
    assert recovery_event["owner_recovery_passed"] is True
    assert (tmp_path / "build.config").read_text() == "entrypoint=RequiredMain"


# --- General Obligation-Centric Recovery Execution (2026-08-30) -
# generalizes the owner-recovery loop above from exactly-one-owner to N
# independently, unambiguously resolved owners. See
# Kriya_General_Obligation_Centric_Recovery_Execution_Implementation_
# Specification.md (repo root) for the full design; deterministic test
# matrix per that spec's §14. ---

@pytest.mark.asyncio
async def test_enforce_recovery_plan_single_owner_matches_legacy_behavior(tmp_path):
    """spec §14 item 1 / §11.1: a single-owner scope_conflict must produce a
    one-group RecoveryExecutionPlan byte-for-byte equivalent to the
    pre-existing single-owner behavior - the primary regression bar for
    this whole change. Deliberately the SAME scenario as
    test_enforce_owner_recovery_correct_fix_via_reference_token_completes_
    and_resumes_consumer above, re-asserted under the new group-based
    execution path."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build config", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="build.config", action=FileAction.CREATE)],
                provides=["entrypoint.configured"],
            ),
            Subtask(
                id="s2", description="create application entrypoint", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="app_entrypoint.txt", action=FileAction.CREATE)],
                requires=["entrypoint.configured"],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "build.config").write_text("entrypoint=OldMain")
            return {"status": "success", "quality_gates_passed": True, "files": ["build.config"]}
        if len(calls) == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "misdirected_edit",
                    "required_files": ["build.config"],
                    "allowed_files": ["app_entrypoint.txt"],
                    "required_reference_token": "RequiredMain",
                },
            }
        if len(calls) == 3:
            (tmp_path / "build.config").write_text("entrypoint=RequiredMain")
            return {"status": "success", "quality_gates_passed": True, "files": ["build.config"]}
        (tmp_path / "app_entrypoint.txt").write_text("RequiredMain")
        return {"status": "success", "quality_gates_passed": True, "files": ["app_entrypoint.txt"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 4
    assert all(item.status == SubtaskStatus.COMPLETED for item in result.subtask_results)
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 1
    assert events[0]["owner_recovery_passed"] is True
    assert events[0]["reopened_owner"] == "s1"
    assert (tmp_path / "build.config").read_text() == "entrypoint=RequiredMain"


@pytest.mark.asyncio
async def test_enforce_recovery_plan_two_artifacts_one_owner(tmp_path):
    """spec §14 item 2: an owner asked to fix two of its OWN files gets
    exactly ONE RecoveryOwnerGroup (one combined owner-recovery call), not
    two - the scheduling unit is the whole subtask invocation."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build config", execution_method=ExecutionMethod.MODEL,
                planned_files=[
                    PlannedFile(path="pom.xml", action=FileAction.CREATE),
                    PlannedFile(path="Config.java", action=FileAction.CREATE),
                ],
            ),
            Subtask(
                id="s2", description="create application", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            (tmp_path / "Config.java").write_text("class Config {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml", "Config.java"]}
        if n == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "required_files": ["pom.xml", "Config.java"],
                    "allowed_files": ["App.java"],
                },
            }
        if n == 3:
            (tmp_path / "pom.xml").write_text("<project><fixed/></project>")
            (tmp_path / "Config.java").write_text("class Config { /* fixed */ }")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml", "Config.java"]}
        (tmp_path / "App.java").write_text("class App {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    # 1 (s1) + 1 (s2 fail) + 1 (ONE combined s1 recovery for both files) +
    # 1 (s2 consumer retry) = 4 - never two separate owner-recovery calls.
    assert len(calls) == 4
    assert calls[2]["allowed_write_relpaths"] == ["pom.xml", "Config.java"]
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 1
    assert events[0]["required_repair_files"] == ["Config.java", "pom.xml"]
    assert events[0]["owner_recovery_passed"] is True


@pytest.mark.asyncio
async def test_enforce_recovery_plan_two_artifacts_two_owners_pom_and_app(tmp_path):
    """spec §14 item 3 - the literal live PRV-06 shape (2026-08-30 overnight
    run): s3 needs BOTH pom.xml (owned by s1, via dependency ancestry) AND
    App.java (owned by s2, via EXECUTION PROVENANCE - s3 depends only on
    s1, never declares s2) fixed together. This used to fail closed purely
    because `len(owner_map) != 1` even though each artifact resolved to its
    own owner unambiguously - the exact defect this round closes. Must
    reopen BOTH owners (dependency-ordered: s1 before s2, since s2 itself
    depends_on s1) against ONE shared candidate workspace, then resume the
    consumer exactly once."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App { /* missing entrypoint */ }")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "reason": "pom.xml is missing the JUnit 5 dependency required by AppTest.java",
                    "required_files": ["pom.xml", "App.java"],
                    "allowed_files": ["AppTest.java"],
                },
            }
        if n == 4:
            (tmp_path / "pom.xml").write_text("<project><dependencies><junit/></dependencies></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 5:
            (tmp_path / "App.java").write_text("class App { /* fixed entrypoint */ }")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        (tmp_path / "AppTest.java").write_text("class AppTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["AppTest.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 6
    assert all(item.status == SubtaskStatus.COMPLETED for item in result.subtask_results)
    assert calls[3]["allowed_write_relpaths"] == ["pom.xml"]
    assert calls[4]["allowed_write_relpaths"] == ["App.java"]
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 2
    assert {e["reopened_owner"] for e in events} == {"s1", "s2"}
    assert all(e["owner_recovery_passed"] is True for e in events)
    assert (tmp_path / "pom.xml").read_text() == "<project><dependencies><junit/></dependencies></project>"
    assert (tmp_path / "App.java").read_text() == "class App { /* fixed entrypoint */ }"


@pytest.mark.asyncio
async def test_enforce_recovery_plan_three_artifacts_two_owners(tmp_path):
    """spec §14 item 4: N-artifact behavior - s1 owns TWO required files
    (pom.xml, Model.java), s2 owns a third (App.java). Two groups, s1's own
    group has two participants generated together in ONE owner-recovery
    call."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create manifest + model", execution_method=ExecutionMethod.MODEL,
                    planned_files=[
                        PlannedFile(path="pom.xml", action=FileAction.CREATE),
                        PlannedFile(path="Model.java", action=FileAction.CREATE),
                    ]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            (tmp_path / "Model.java").write_text("class Model {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml", "Model.java"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "required_files": ["pom.xml", "Model.java", "App.java"],
                    "allowed_files": ["AppTest.java"],
                },
            }
        if n == 4:
            (tmp_path / "pom.xml").write_text("<project><fixed/></project>")
            (tmp_path / "Model.java").write_text("class Model { /* fixed */ }")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml", "Model.java"]}
        if n == 5:
            (tmp_path / "App.java").write_text("class App { /* fixed */ }")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        (tmp_path / "AppTest.java").write_text("class AppTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["AppTest.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 6
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 2  # two GROUPS, not three artifacts
    s1_event = next(e for e in events if e["reopened_owner"] == "s1")
    assert s1_event["required_repair_files"] == ["Model.java", "pom.xml"]
    assert calls[3]["allowed_write_relpaths"] == ["pom.xml", "Model.java"]


def test_derive_recovery_participants_excludes_artifact_without_grounded_evidence():
    """spec §14 item 5: only artifacts named by the scope_conflict's own
    deterministic required_files/grounded_owner_files ever become
    participants - never a file merely mentioned in free-text reasoning.
    App.java (named only in the "reason" prose) must never appear."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create App", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s4", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1", "s3"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    scope_conflict = {
        "reason": "pom.xml is missing a dependency also used by App.java",
        "required_files": ["pom.xml"],
    }
    participants = derive_recovery_participants(plan, scope_conflict, plan.subtask_by_id("s4"))
    assert [p.artifact for p in participants] == ["pom.xml"]
    assert participants[0].role is RecoveryParticipantRole.REQUIRED_MUTATION
    assert participants[0].effective_owner_subtask_id == "s1"


def test_build_recovery_execution_plan_fails_closed_on_ambiguous_owner():
    """spec §14 item 6: at least one required artifact resolving to a
    genuinely unresolvable owner must fail the WHOLE plan closed, even
    when every OTHER artifact resolves cleanly."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create pom", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="consumer", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="Other.java", action=FileAction.CREATE)]),
        ],
    )
    scope_conflict = {
        "reason": "needs pom.xml and a file nobody declares",
        "required_files": ["pom.xml", "Ghost.java"],
    }
    exec_plan = build_recovery_execution_plan(
        plan, scope_conflict, plan.subtask_by_id("s2"), plan_generation=0,
    )
    assert exec_plan is None


@pytest.mark.asyncio
async def test_enforce_recovery_plan_group_upstream_changes_group_downstream_sees_it(tmp_path):
    """spec §14 item 7: sequential owner-groups already share ONE candidate
    workspace (the persistent plan_workspace_path git worktree) - the
    second group's own recovery call must see the FIRST group's real,
    freshly-written content, not the pre-recovery baseline. No MA9-style
    in-memory candidate_view needed at this layer (implementation spec
    §2)."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "required_files": ["pom.xml", "App.java"],
                    "allowed_files": ["AppTest.java"],
                },
            }
        if n == 4:
            (tmp_path / "pom.xml").write_text("<project><MARKER_JUNIT_FIXED/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 5:
            # This is the group.s2 call - its own context must already
            # contain group.s1's freshly-written marker content.
            assert "MARKER_JUNIT_FIXED" in kwargs.get("supplementary_context", "")
            (tmp_path / "App.java").write_text("class App { /* fixed */ }")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        (tmp_path / "AppTest.java").write_text("class AppTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["AppTest.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 6


@pytest.mark.asyncio
async def test_enforce_recovery_plan_group_unchanged_is_no_progress(tmp_path):
    """spec §14 item 8: RECOVERY_NO_PROGRESS still fires per-group under the
    new architecture - and when the FIRST group in dependency order fails
    that way, the SECOND group must never even be invoked (the whole plan
    fails atomically, no wasted call)."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "required_files": ["pom.xml", "App.java"],
                    "allowed_files": ["AppTest.java"],
                },
            }
        # group.s1's own recovery candidate: byte-identical to what pom.xml
        # already had - RECOVERY_NO_PROGRESS must fire, and group.s2 (App.java)
        # must NEVER be invoked as a result.
        (tmp_path / "pom.xml").write_text("<project/>")
        return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    assert len(calls) == 4  # s1, s2, s3-fail, s1-no-progress-recovery - group.s2 NEVER ran
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 1
    assert events[0]["reopened_owner"] == "s1"
    assert events[0]["owner_recovery_passed"] is False
    s2_result = next(item for item in result.subtask_results if item.subtask_id == "s2")
    assert s2_result.status == SubtaskStatus.COMPLETED  # s2's OWN initial pass is untouched


@pytest.mark.asyncio
async def test_enforce_recovery_plan_all_groups_pass_locally_but_consumer_still_fails(tmp_path):
    """spec §14 item 9 - the literal generalization of Invariant 3: "group 1
    PASS + group 2 PASS" must NOT be treated as "recovery PASS." Both
    owner-groups' own local gates pass here, but the consumer's own retry
    fails again (no new scope_conflict) - the whole plan must be rejected
    and neither cross-owner obligation SATISFIED. Both owners' PLAN state
    (plan_recovery_events/approved_stage_states) reflects needs_review, but
    - matching the pre-existing single-owner precedent exactly (see
    test_enforce_permits_a_second_distinct_generation_recovery_on_the_same_
    owner, where an owner whose OWN local gate passed must remain eligible
    for a LATER cycle to still resolve the requirement) - the FINAL
    reported subtask_results entries for s1/s2 are NOT downgraded to
    NEEDS_REVIEW, since neither owner's own local recovery gate failed;
    only a group that fails LOCALLY gets that downgrade."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "required_files": ["pom.xml", "App.java"],
                    "allowed_files": ["AppTest.java"],
                },
            }
        if n == 4:
            (tmp_path / "pom.xml").write_text("<project><changed/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 5:
            (tmp_path / "App.java").write_text("class App { /* changed */ }")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        # consumer retry - still fails, for an unrelated/unresolvable reason,
        # with NO new scope_conflict (so the loop ends rather than looping).
        return {"status": "failed", "quality_gates_passed": False, "files": []}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    assert len(calls) == 6
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 2
    assert all(e["owner_recovery_passed"] is False for e in events)
    for owner_id in ("s1", "s2"):
        owner_result = next(item for item in result.subtask_results if item.subtask_id == owner_id)
        # Neither owner's OWN local gate failed - only the plan-level
        # acceptance (the consumer's own retry) rejected the whole plan, so
        # the FINAL reported subtask_results entries stay whatever each
        # owner's own earlier successful pass already recorded (COMPLETED),
        # never forced to NEEDS_REVIEW - matching the pre-existing
        # single-owner precedent this generalizes.
        assert owner_result.status == SubtaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_enforce_recovery_plan_consumer_resumes_exactly_once(tmp_path):
    """spec §14 item 10 - the review's own explicit anti-circularity
    requirement: the originating consumer must be re-invoked EXACTLY ONCE
    per recovery plan, never once per owner-group."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "required_files": ["pom.xml", "App.java"],
                    "allowed_files": ["AppTest.java"],
                },
            }
        if n == 4:
            (tmp_path / "pom.xml").write_text("<project><fixed/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 5:
            (tmp_path / "App.java").write_text("class App { /* fixed */ }")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        (tmp_path / "AppTest.java").write_text("class AppTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["AppTest.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    consumer_calls = [c for c in calls if c.get("allowed_write_relpaths") == ["AppTest.java"]]
    assert len(consumer_calls) == 2  # s3's own first attempt + exactly one resumed retry


@pytest.mark.asyncio
async def test_enforce_recovery_plan_later_distinct_two_owner_requirement_reopens_same_owners(tmp_path):
    """spec §14 item 11: a SECOND, genuinely distinct multi-owner recovery
    requirement on the SAME pair of owners (s1, s2) - after the first
    two-owner plan (recovery.s3.0) succeeds, a later consumer (s4) hits a
    DIFFERENT requirement needing the same two owners fixed again. Must
    succeed as its own fresh plan (recovery.s4.0, a different id) without
    any false RECOVERY_NO_PROGRESS carried over from the first plan's own
    fingerprints - the fingerprint key is now plan-id-scoped (spec §9)."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
            Subtask(id="s4", description="create integration tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1", "s3"],
                    planned_files=[PlannedFile(path="IntegrationTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            # s3 - first requirement (JUnit).
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "compile",
                    "reason": "missing JUnit dependency", "required_files": ["pom.xml", "App.java"],
                    "allowed_files": ["AppTest.java"],
                },
            }
        if n == 4:
            (tmp_path / "pom.xml").write_text("<project><junit/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 5:
            (tmp_path / "App.java").write_text("class App { /* junit fix */ }")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 6:
            (tmp_path / "AppTest.java").write_text("class AppTest {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["AppTest.java"]}
        if n == 7:
            # s4 - a SECOND, distinct requirement (Jackson) on the SAME
            # owner pair.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "compile",
                    "reason": "missing Jackson dependency", "required_files": ["pom.xml", "App.java"],
                    "allowed_files": ["IntegrationTest.java"],
                },
            }
        if n == 8:
            (tmp_path / "pom.xml").write_text("<project><junit/><jackson/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 9:
            (tmp_path / "App.java").write_text("class App { /* junit fix */ /* jackson fix */ }")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        (tmp_path / "IntegrationTest.java").write_text("class IntegrationTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["IntegrationTest.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 10
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 4  # 2 groups x 2 distinct plans
    assert all(e["owner_recovery_passed"] is True for e in events)
    requirement_ids = {e["requirement_id"] for e in events}
    assert len(requirement_ids) == 4  # every group/plan combination gets its own id


@pytest.mark.asyncio
async def test_enforce_recovery_plan_runtime_verification_multi_owner(tmp_path):
    """spec §14 item 13: a VERIFICATION_CONTRACT_DEFECT-shaped (DENY_ALL)
    scope conflict spanning TWO owners must go through the same
    RecoveryExecutionPlan machinery as an ordinary PLAN_SCOPE_DEFECT one -
    not just the single-owner case Verification-Only Recovery Routing was
    originally proven against."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create App", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.MODIFY)]),
            Subtask(id="s3", description="create config", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="application.yml", action=FileAction.CREATE)]),
            Subtask(
                id="s4", description="run the application with sample input",
                execution_method=ExecutionMethod.MODEL, execution_role=ExecutionRole.VERIFICATION,
                depends_on=["s2", "s3"], planned_files=[],
                verification=[VerificationMethod(
                    type=VerificationMethodType.JUDGMENT,
                    description="run the application and confirm the transformed value is printed",
                    verifier_kind=VerifierKind.APPLICATION_RUNTIME,
                )],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "App.java").write_text("class App {}\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App { /* reads stdin, not argv */ }\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            (tmp_path / "application.yml").write_text("mode: default\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["application.yml"]}
        if n == 4:
            assert kwargs["write_scope_mode"] == WriteScopeMode.DENY_ALL
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "run_verification",
                    "reason": "app reads stdin but the runtime contract supplies argv, and the "
                              "configured mode disables argv entirely",
                    "required_files": ["App.java", "application.yml"], "allowed_files": [],
                },
            }
        if n in (5, 6):
            path = kwargs["allowed_write_relpaths"][0]
            if path == "App.java":
                (tmp_path / "App.java").write_text("class App { /* reads args[0] now */ }\n")
                return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
            (tmp_path / "application.yml").write_text("mode: argv\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["application.yml"]}
        assert kwargs["write_scope_mode"] == WriteScopeMode.DENY_ALL
        return {
            "status": "success", "quality_gates_passed": True, "files": [],
            "verification_results": [{
                "type": "judgment", "tool_name": None,
                "description": "run the application and confirm the transformed value is printed",
                "passed": True, "source": "run_verification",
            }],
        }

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 7
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 2
    assert {e["reopened_owner"] for e in events} == {"s2", "s3"}
    assert all(e["owner_recovery_passed"] is True for e in events)


@pytest.mark.asyncio
async def test_enforce_recovery_plan_read_context_artifact_never_enters_write_scope(tmp_path):
    """Spec_v1.0 T7/T8 (repo root, pre-existing doc found 2026-08-30):
    'unrelated model-proposed artifact' / 'read-context leakage'. The
    Developer's own FIX ANALYSIS text may mention a file that never appears
    in the scope_conflict's own deterministic required_files - that file
    must never become a recovery participant, never get write-authorized in
    ANY call, and its own on-disk content must remain untouched, while the
    ONE genuinely grounded artifact (pom.xml) still recovers normally."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="create App", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            # ONLY pom.xml is deterministically grounded (required_files) -
            # "reason" prose mentions App.java, but that must never make it
            # a participant.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "reason": "pom.xml is missing a dependency also referenced by App.java",
                    "required_files": ["pom.xml"],
                    "allowed_files": ["AppTest.java"],
                },
            }
        if n == 4:
            (tmp_path / "pom.xml").write_text("<project><fixed/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        (tmp_path / "AppTest.java").write_text("class AppTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["AppTest.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 5
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 1
    assert events[0]["reopened_owner"] == "s1"
    assert events[0]["required_repair_files"] == ["pom.xml"]
    # App.java (read context only) never appears in the write scope of any
    # RECOVERY-cycle call - s2's own ORDINARY initial execution (calls[1])
    # legitimately writes its own declared file, that's not what's under
    # test here; calls[3:] are the recovery cycle itself (s1's own
    # owner-recovery + s3's consumer retry), where App.java must never
    # surface at all.
    for call_kwargs in calls[3:]:
        assert "App.java" not in (call_kwargs.get("allowed_write_relpaths") or [])
    assert (tmp_path / "App.java").read_text() == "class App {}"  # untouched by recovery


@pytest.mark.asyncio
async def test_enforce_recovery_plan_group_two_fails_after_group_one_staged_success(tmp_path):
    """Spec_v1.0 T14: group 1 (s1/pom.xml) succeeds locally and stages a
    real fix; group 2 (s2/App.java) then fails locally. Neither group's
    work may become authoritative - the whole plan is atomic. Proven at two
    levels: (a) this test's own plan_recovery_events/subtask_results
    assertions for the recovery LOOP's own bookkeeping; (b) the pre-existing,
    UNCHANGED `all_completed` gate around `commit_revision_grounded_batch`
    (workflow_controller.py, terminal commit block) - a subtask short of
    COMPLETED (s2 here, marked NEEDS_REVIEW) makes `all_completed` False,
    so NO planned file of ANY subtask (including s1's own already-staged
    fix) is ever copied from plan_workspace_path into the real workspace.
    That specific atomicity boundary is exercised end-to-end (via a REAL
    separate worktree sandbox, not this test's own identity-shortcut) by
    the pre-existing test_enforce_discards_successful_earlier_subtask_when_
    plan_fails, confirmed still passing in this round's own regression
    sweep."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "required_files": ["pom.xml", "App.java"],
                    "allowed_files": ["AppTest.java"],
                },
            }
        if n == 4:
            # group.s1 (FIRST in dependency order) succeeds locally and
            # genuinely stages a real fix.
            (tmp_path / "pom.xml").write_text("<project><fixed/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        # group.s2 fails its own local gate - the plan must reject
        # atomically; group.s1's own already-staged fix must not become
        # authoritative as a result.
        return {"status": "failed", "quality_gates_passed": False, "files": []}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    assert len(calls) == 5
    events = result.legacy_result["plan_recovery_events"]
    # BOTH groups get recorded (invoked_group_records captures a group as
    # soon as its own owner-recovery call resolves, whether it then passes
    # or fails locally) - group.s1's own local gate DID pass, but since the
    # consumer was never reached this cycle (the plan failed before ever
    # getting there), BOTH owners are finalized with the SAME plan-level
    # outcome, atomically - matching the "neither becomes authoritative"
    # requirement, not just "the group that actually failed."
    assert len(events) == 2
    assert {e["reopened_owner"] for e in events} == {"s1", "s2"}
    assert all(e["owner_recovery_passed"] is False for e in events)
    for owner_id in ("s1", "s2"):
        owner_result = next(item for item in result.subtask_results if item.subtask_id == owner_id)
        assert owner_result.status == SubtaskStatus.NEEDS_REVIEW
        assert "PLAN_RECOVERY_OWNER_FAILED" in owner_result.reason_codes
    # group.s1's own genuinely-staged fix is real content in the shared
    # worktree (== tmp_path in this mocked harness) - but the pre-existing,
    # UNCHANGED `all_completed` gate around commit_revision_grounded_batch
    # (workflow_controller.py's terminal commit block) means this content
    # is never copied out as authoritative in a real (non-identity-mocked)
    # run, since s1 is not COMPLETED. See test_enforce_discards_successful_
    # earlier_subtask_when_plan_fails for that exact boundary proven with a
    # REAL separate worktree sandbox.
    assert (tmp_path / "pom.xml").read_text() == "<project><fixed/></project>"


# --- MUST_CHANGE vs VERIFY recovery disposition (PRV-11, 2026-08-30) - see
# RecoveryAction's own docstring (kriya/workflow/recovery_plan.py) for the
# live incident this closes: a downstream test failure self-diagnosed BOTH
# pom.xml (genuinely broken) and Customer.java (already correct) as
# required participants (Failure.type == "attribution_rejected" -
# authority="advisory" by construction, never a compiler/test locator).
# Regenerating both and rejecting the WHOLE plan when Customer.java came
# back byte-identical discarded pom.xml's own genuine fix along with it. ---

def test_derive_recovery_participants_model_naming_alone_cannot_promote_verify(tmp_path):
    """spec item 4 (the user's own requested test): the Developer's own
    free-text self-diagnosis (an attribution_rejected scope_conflict) must
    stay VERIFY no matter how prominently or how many times it names the
    file in its own 'reason' text - only genuinely grounded evidence
    (failure_type != 'attribution_rejected') or a real recurrence
    (generation > 0, via recovery_generation_by_key) can produce
    MUST_CHANGE. Text content itself has zero influence."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="create Customer", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="src/main/java/com/example/Customer.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1", "s2"],
                    planned_files=[PlannedFile(path="src/test/java/com/example/CustomerTest.java", action=FileAction.CREATE)]),
        ],
    )
    scope_conflict = {
        "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
        "failure_type": "attribution_rejected",
        # The model's own analysis names Customer.java repeatedly and
        # emphatically - this must have ZERO bearing on disposition.
        "reason": "Customer.java Customer.java Customer.java must change, this is definitely required",
        "required_files": ["pom.xml", "src/main/java/com/example/Customer.java"],
        "allowed_files": ["src/test/java/com/example/CustomerTest.java"],
    }
    participants = derive_recovery_participants(
        plan, scope_conflict, plan.subtask_by_id("s3"),
        recovery_generation_by_key={},  # generation 0 for everything - no prior cycle
    )
    assert {p.artifact: p.recovery_action for p in participants} == {
        "pom.xml": RecoveryAction.VERIFY,
        "src/main/java/com/example/Customer.java": RecoveryAction.VERIFY,
    }


# --- Recovery obligation authority (control-plane audit, 2026-08-30) ---
#
# Governing invariant: recovery obligation authority must never exceed the
# authority of the evidence it was constructed from. Found live during a
# static audit (not a PRV rerun): _get_or_create_cross_owner_obligation()
# stamped EVERY cross-owner obligation authority=DETERMINISTIC
# unconditionally - including one built from scope_conflict["reason"]
# originating in Failure.type=="attribution_rejected" (a Developer's own
# advisory, unverified self-diagnosis - the exact evidence shape §11.11's
# own PRV-11 incident already proved this codebase produces live). A
# mislabeled DETERMINISTIC record could then wrongly outrank a later,
# genuinely correct JUDGMENT-tier contradiction under ObligationLedger's
# own DETERMINISTIC > GROUNDED > JUDGMENT precedence rule.

def test_compiler_failure_cross_owner_obligation_is_deterministic():
    ledger = ObligationLedger()
    scope_conflict = {
        "failure_type": "compile",
        "attribution_tier": "locator",
        "reason": "pom.xml is missing the junit-jupiter dependency required by CustomerTest.java",
    }
    record = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s3", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=scope_conflict, generation=0, revision=0,
    )
    assert record.authority is ObligationAuthority.DETERMINISTIC


def test_deterministic_write_scope_evidence_cross_owner_obligation_is_deterministic():
    """The AuthorizedFileWriter write-scope-denial shape (PLAN_SCOPE_DEFECT,
    attribution_tier="architectural_owner") - the mechanism §11.9's own
    multi-owner recovery is built around."""
    ledger = ObligationLedger()
    scope_conflict = {
        "failure_type": "goal_spec_compliance",
        "attribution_tier": "architectural_owner",
        "reason": "AuthorizedFileWriter denied a write to CustomerService.java outside the validated subtask scope",
    }
    record = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s1", owner_subtask_id="s2",
        required_files=["CustomerService.java"], scope_conflict=scope_conflict, generation=0, revision=0,
    )
    assert record.authority is ObligationAuthority.DETERMINISTIC


def test_attribution_rejected_cross_owner_obligation_is_not_deterministic():
    """The exact PRV-11 live shape: a Developer's own free-text FIX ANALYSIS
    self-diagnosis, never independently verified by a compiler/test
    locator. Must never be stamped DETERMINISTIC - JUDGMENT, matching
    _participant_evidence_is_grounded's own precedent for this identical
    evidence shape."""
    ledger = ObligationLedger()
    scope_conflict = {
        "failure_type": "attribution_rejected",
        "attribution_tier": "self_diagnosis",
        "reason": "The Developer's own analysis claims Customer.java also needs a displayName field",
    }
    record = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s3", owner_subtask_id="s1",
        required_files=["Customer.java"], scope_conflict=scope_conflict, generation=0, revision=0,
    )
    assert record.authority is not ObligationAuthority.DETERMINISTIC
    assert record.authority is ObligationAuthority.JUDGMENT


def test_deterministic_evidence_outranks_a_prior_advisory_obligation():
    """Proves this isn't merely an enum-assignment change - it restores the
    MA8 evidence precedence semantics. An advisory (JUDGMENT) obligation is
    recorded VIOLATED first; a later, genuinely deterministic record for the
    SAME obligation id then reports SATISFIED - deterministic evidence must
    win, becoming the ledger's own authoritative current() state."""
    ledger = ObligationLedger()
    advisory_scope_conflict = {
        "failure_type": "attribution_rejected",
        "attribution_tier": "self_diagnosis",
        "reason": "The Developer's own analysis claims Customer.java also needs a displayName field",
    }
    advisory = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s3", owner_subtask_id="s1",
        required_files=["Customer.java"], scope_conflict=advisory_scope_conflict, generation=0, revision=0,
    )
    assert advisory.status == ObligationStatus.VIOLATED
    assert advisory.authority is ObligationAuthority.JUDGMENT

    ledger.record(ObligationRecord(
        id=advisory.id, kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
        status=ObligationStatus.SATISFIED, authority=ObligationAuthority.DETERMINISTIC,
        description=advisory.description, source="workflow_controller.owner_recovery",
        revision=1, evidence=advisory.evidence, owner_subtask_id=advisory.owner_subtask_id,
    ))

    current = ledger.current(advisory.id)
    assert current.status == ObligationStatus.SATISFIED
    assert current.authority is ObligationAuthority.DETERMINISTIC


def test_scope_conflict_evidence_authority_unrecognized_tier_fails_toward_judgment():
    """An attribution_tier this function doesn't recognize (e.g. a future
    addition to attribution.py) must never be assumed DETERMINISTIC."""
    assert _scope_conflict_evidence_authority(
        {"failure_type": "compile", "attribution_tier": "some_future_tier"}
    ) is ObligationAuthority.JUDGMENT
    assert _scope_conflict_evidence_authority(
        {"failure_type": "compile"}
    ) is ObligationAuthority.JUDGMENT


def test_derive_recovery_participants_grounded_evidence_is_must_change():
    """The mirror case: a scope_conflict NOT reached via self-diagnosis
    (any failure_type other than 'attribution_rejected') defaults to
    MUST_CHANGE - the pre-existing, only-ever behavior for every recovery
    scenario this codebase already had before PRV-11, unchanged."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="CustomerTest.java", action=FileAction.CREATE)]),
        ],
    )
    scope_conflict = {
        "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "compile",
        "required_files": ["pom.xml"], "allowed_files": ["CustomerTest.java"],
    }
    participants = derive_recovery_participants(plan, scope_conflict, plan.subtask_by_id("s3"))
    assert participants[0].recovery_action is RecoveryAction.MUST_CHANGE


@pytest.mark.asyncio
async def test_enforce_recovery_plan_must_change_and_verify_unchanged_consumer_passes(tmp_path):
    """spec item 1 (the user's own requested test): one MUST_CHANGE
    participant (pom.xml, promoted via a real prior recovery cycle - see
    below for why this is how a mixed disposition arises in this
    architecture, since one scope_conflict shares one failure_type across
    all its required_files) and one VERIFY participant (Customer.java,
    fresh attribution_rejected self-diagnosis) in the SAME
    RecoveryExecutionPlan. Customer.java must never be regenerated
    (zero Developer calls for it) and must not block the consumer's own
    retry from succeeding once pom.xml's real fix lands.

    Cycle 0: s3 fails with a GROUNDED, single-artifact conflict
    (pom.xml only, failure_type='compile') - ordinary MUST_CHANGE recovery,
    pom.xml genuinely fixed. Consumer retries and fails AGAIN, this time
    self-diagnosing BOTH pom.xml and Customer.java (attribution_rejected).
    Cycle 1: pom.xml's own (subtask,owner,files) tuple already went through
    one full cycle in cycle 0 (generation=1 now) -> promoted to MUST_CHANGE
    despite the ungrounded scope_conflict; Customer.java is seeing its
    first cycle (generation=0) -> stays VERIFY, carried forward untouched.
    """
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="create Customer", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="Customer.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1", "s2"],
                    planned_files=[PlannedFile(path="CustomerTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (tmp_path / "Customer.java").write_text("class Customer {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["Customer.java"]}
        if n == 3:
            # s3 attempt 1 - grounded, single-artifact conflict.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "compile",
                    "required_files": ["pom.xml"], "allowed_files": ["CustomerTest.java"],
                },
            }
        if n == 4:
            # cycle 0's MUST_CHANGE owner-recovery for pom.xml - genuine fix.
            (tmp_path / "pom.xml").write_text("<project><junit/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 5:
            # s3 consumer retry (cycle 0) - fails AGAIN, this time
            # self-diagnosing BOTH pom.xml and Customer.java.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "attribution_rejected",
                    "reason": "Developer self-diagnosis names both files",
                    "required_files": ["pom.xml", "Customer.java"], "allowed_files": ["CustomerTest.java"],
                },
            }
        if n == 6:
            # cycle 1's owner-recovery for pom.xml - promoted to MUST_CHANGE
            # via recurrence (generation=1), gets a REAL further fix.
            (tmp_path / "pom.xml").write_text("<project><junit/><surefire/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        # Customer.java's OWN owner-recovery must NEVER be called (VERIFY,
        # carried forward) - if this branch is ever reached, the test
        # fixture itself is wrong (there is no n==7 owner_recovery call for
        # s2 in the expected call sequence below).
        (tmp_path / "CustomerTest.java").write_text("class CustomerTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["CustomerTest.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    # 1(s1) + 1(s2) + 1(s3 fail#1) + 1(pom.xml recovery cycle0) +
    # 1(s3 consumer retry cycle0, fails again) + 1(pom.xml recovery cycle1)
    # + 1(s3 consumer retry cycle1, succeeds) = 7 - Customer.java's own
    # owner-recovery NEVER consumed a call.
    assert len(calls) == 7
    assert (tmp_path / "Customer.java").read_text() == "class Customer {}"  # untouched throughout
    assert (tmp_path / "pom.xml").read_text() == "<project><junit/><surefire/></project>"


@pytest.mark.asyncio
async def test_enforce_recovery_plan_verify_unchanged_then_consumer_fail_promotes_to_must_change(tmp_path):
    """spec item 2 (the user's own requested test): a VERIFY participant
    carried forward unconditionally, the consumer's own retry STILL fails
    (the requirement recurs), and on the NEXT generation that same
    (subtask, owner, file) triple is promoted to MUST_CHANGE - now getting
    a real Developer call - and the genuine fix lets the consumer pass."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build config", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="CustomerTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n in (2, 3):
            # n=2: s2's own first attempt. n=3: s2's consumer retry after
            # cycle 0's VERIFY carry-forward (pom.xml never touched) - fails
            # the SAME way both times, self-diagnosed, never grounded.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "attribution_rejected",
                    "required_files": ["pom.xml"], "allowed_files": ["CustomerTest.java"],
                },
            }
        if n == 4:
            # Cycle 1: pom.xml promoted to MUST_CHANGE (generation=1) - the
            # first REAL owner-recovery call for it, genuine fix.
            (tmp_path / "pom.xml").write_text("<project><junit/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        (tmp_path / "CustomerTest.java").write_text("class CustomerTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["CustomerTest.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    # 1(s1) + 1(s2 fail#1) + 1(s2 consumer retry cycle0, VERIFY carried
    # forward, fails again) + 1(pom.xml recovery cycle1, promoted) +
    # 1(s2 consumer retry cycle1, succeeds) = 5 - pom.xml's owner-recovery
    # was NEVER called during cycle 0.
    assert len(calls) == 5
    assert (tmp_path / "pom.xml").read_text() == "<project><junit/></project>"


@pytest.mark.asyncio
async def test_enforce_recovery_plan_must_change_unchanged_still_no_progress(tmp_path):
    """spec item 3 (the user's own requested test, regression proof): a
    genuinely GROUNDED (non-attribution_rejected) MUST_CHANGE participant
    that regenerates byte-identically must still trigger
    RECOVERY_NO_PROGRESS and reject the plan - VERIFY's new "carry forward,
    never reject" behavior must never leak into the MUST_CHANGE path."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="CustomerTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "compile",
                    "required_files": ["pom.xml"], "allowed_files": ["CustomerTest.java"],
                },
            }
        # Grounded MUST_CHANGE owner-recovery regenerates byte-identically.
        (tmp_path / "pom.xml").write_text("<project/>")
        return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    assert len(calls) == 3
    events = result.legacy_result["plan_recovery_events"]
    assert events[0]["owner_recovery_passed"] is False


def test_order_recovery_groups_fails_closed_on_cycle():
    """spec §14 item 15: _order_recovery_groups must fail closed (return
    None), never guess an order, when two participating owners are
    mutually upstream of each other - impossible to construct via the real
    Planner/validator, but the ordering function itself must not silently
    pick an arbitrary order if it ever sees one."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="A", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s2"],
                    planned_files=[PlannedFile(path="A.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="B", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="B.java", action=FileAction.CREATE)]),
        ],
    )
    participants = (
        RecoveryParticipant(
            artifact="A.java", role=RecoveryParticipantRole.REQUIRED_MUTATION,
            effective_owner_subtask_id="s1", owner_resolution_basis="dependency_ancestry",
            mutation_reason="fix A",
        ),
        RecoveryParticipant(
            artifact="B.java", role=RecoveryParticipantRole.REQUIRED_MUTATION,
            effective_owner_subtask_id="s2", owner_resolution_basis="dependency_ancestry",
            mutation_reason="fix B",
        ),
    )
    assert _order_recovery_groups(plan, participants) is None


# --- Targeted repair for grounded owner recovery (2026-08-30, PRV-06
# follow-up): _attempt_owner_recovery_self_correction() reuses the existing
# self_correction.py tool loop as a cheaper first attempt at an owner-
# recovery fix, but must NEVER let its own narrow (cross-owner-blind)
# compile check bypass the SAME downstream safety net the Developer-
# generation path already goes through. ---

def _fake_kernel(*, self_correction_enabled=True, max_turns=4):
    kernel = MagicMock()
    kernel.config.autonomy.self_correction_loop_enabled = self_correction_enabled
    kernel.config.autonomy.self_correction_loop_max_turns = max_turns
    return kernel


@pytest.mark.asyncio
async def test_attempt_owner_recovery_self_correction_returns_none_when_disabled(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    owner = Subtask(id="s1", description="build manifest", execution_method=ExecutionMethod.MODEL,
                     planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)])
    result = await _attempt_owner_recovery_self_correction(
        kernel=_fake_kernel(self_correction_enabled=False),
        developer_llm=MagicMock(),
        plan_workspace_path=str(tmp_path), workspace_path=str(tmp_path),
        owner=owner, required_owner_files=["pom.xml"],
        scope_conflict={"raw_evidence": "package org.junit.jupiter.api does not exist"},
    )
    assert result is None


@pytest.mark.asyncio
async def test_attempt_owner_recovery_self_correction_returns_none_without_raw_evidence(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    owner = Subtask(id="s1", description="build manifest", execution_method=ExecutionMethod.MODEL,
                     planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)])
    result = await _attempt_owner_recovery_self_correction(
        kernel=_fake_kernel(), developer_llm=MagicMock(),
        plan_workspace_path=str(tmp_path), workspace_path=str(tmp_path),
        owner=owner, required_owner_files=["pom.xml"],
        scope_conflict={},  # no raw_evidence to seed the loop
    )
    assert result is None


@pytest.mark.asyncio
async def test_attempt_owner_recovery_self_correction_rejects_trivial_resolve_with_no_real_change(tmp_path):
    """The critical safety property: self_correction_loop's own `resolved`
    flag comes from a compile check scoped to the OWNER's own files - the
    exact same cross-owner-blind signal that let the live PRV-06 defect
    slip past App.java's own Quality Gates. A "resolved" loop that never
    actually modified any file must NOT be trusted as a real fix."""
    (tmp_path / "App.java").write_text("class App { private static class InMemoryService {} }")
    owner = Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                     planned_files=[PlannedFile(path="App.java", action=FileAction.MODIFY)])
    trivial_resolve = SelfCorrectionResult(
        resolved=True, turns_used=1, final_compile_output="SUCCESS", modified_files={},
    )
    with patch(
        "kriya.workflow.self_correction.run_self_correction_loop",
        new=AsyncMock(return_value=trivial_resolve),
    ), patch("kriya.tools.validate.PolymorphicValidator"):
        result = await _attempt_owner_recovery_self_correction(
            kernel=_fake_kernel(), developer_llm=MagicMock(),
            plan_workspace_path=str(tmp_path), workspace_path=str(tmp_path),
            owner=owner, required_owner_files=["App.java"],
            scope_conflict={"raw_evidence": "App.InMemoryService has private access in App"},
        )
    assert result is None


@pytest.mark.asyncio
async def test_attempt_owner_recovery_self_correction_returns_candidate_on_real_fix(tmp_path):
    (tmp_path / "App.java").write_text("class App { static class InMemoryService {} }")
    owner = Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                     planned_files=[PlannedFile(path="App.java", action=FileAction.MODIFY)])
    real_resolve = SelfCorrectionResult(
        resolved=True, turns_used=2, final_compile_output="SUCCESS",
        modified_files={"App.java": "class App { static class InMemoryService {} }"},
    )
    with patch(
        "kriya.workflow.self_correction.run_self_correction_loop",
        new=AsyncMock(return_value=real_resolve),
    ), patch("kriya.tools.validate.PolymorphicValidator"):
        result = await _attempt_owner_recovery_self_correction(
            kernel=_fake_kernel(), developer_llm=MagicMock(),
            plan_workspace_path=str(tmp_path), workspace_path=str(tmp_path),
            owner=owner, required_owner_files=["App.java"],
            scope_conflict={"raw_evidence": "App.InMemoryService has private access in App"},
        )
    assert result == {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}


@pytest.mark.asyncio
async def test_enforce_recovery_plan_self_correction_produces_candidate_and_resumes_consumer(tmp_path):
    """Full-controller integration: self-correction produces a REAL fix for
    group.s1 - the owner-recovery loop must use it directly (no
    _invoke_bounded_subtask call spent on that owner) and the rest of the
    pipeline (established_file_context, consumer retry, success) must work
    completely unchanged, exactly as if a Developer-generation call had
    produced the identical content."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    we.kernel = _fake_kernel()
    we.developer.llm = MagicMock()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "compile",
                    "raw_evidence": "package org.junit.jupiter.api does not exist",
                    "required_files": ["pom.xml"], "allowed_files": ["AppTest.java"],
                },
            }
        # This is the CONSUMER's own resumed retry - the owner (s1) never
        # got a second run_generation_workflow call, since self-correction
        # (mocked below) already produced its candidate directly.
        (tmp_path / "AppTest.java").write_text("class AppTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["AppTest.java"]}

    we.run_generation_workflow = fake_run
    real_resolve = SelfCorrectionResult(
        resolved=True, turns_used=1, final_compile_output="SUCCESS",
        modified_files={"pom.xml": "<project><dependencies><junit/></dependencies></project>"},
    )

    def _fake_self_correction(*args, **kwargs):
        (tmp_path / "pom.xml").write_text(real_resolve.modified_files["pom.xml"])
        return real_resolve

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.self_correction.run_self_correction_loop",
        new=AsyncMock(side_effect=_fake_self_correction),
    ), patch("kriya.tools.validate.PolymorphicValidator"):
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    # 1 (s1 initial) + 1 (s2 fail) + 1 (consumer retry) = 3 - NOT 4, since
    # self-correction replaced what would otherwise have been a fourth
    # run_generation_workflow call for s1's own owner-recovery attempt.
    assert len(calls) == 3
    assert (tmp_path / "pom.xml").read_text() == "<project><dependencies><junit/></dependencies></project>"
    events = result.legacy_result["plan_recovery_events"]
    assert events[0]["owner_recovery_passed"] is True


@pytest.mark.asyncio
async def test_enforce_recovery_plan_self_correction_still_wrong_fix_caught_by_no_progress(tmp_path):
    """Safety proof: self-correction's own `resolved=True` must NOT bypass
    the existing RECOVERY_NO_PROGRESS/acceptance pipeline. Here it
    genuinely modifies the file (so the trivial-resolve guard doesn't catch
    it) but reproduces content byte-identical to the pre-recovery baseline -
    the SAME downstream fingerprint check that already protects the
    Developer-generation path must catch this too, with zero special-casing
    for where the candidate came from."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build config", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="build.config", action=FileAction.CREATE)]),
            Subtask(id="s2", description="create entrypoint", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="app_entrypoint.txt", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    we.kernel = _fake_kernel()
    we.developer.llm = MagicMock()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "build.config").write_text("entrypoint=OldMain")
            return {"status": "success", "quality_gates_passed": True, "files": ["build.config"]}
        return {
            "status": "failed", "quality_gates_passed": False, "files": [],
            "plan_scope_conflict": {
                "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "misdirected_edit",
                "raw_evidence": "entrypoint mismatch", "required_files": ["build.config"],
                "allowed_files": ["app_entrypoint.txt"],
            },
        }

    we.run_generation_workflow = fake_run
    # "Resolved" its own narrow check, DID write something (passes the
    # trivial-resolve guard) - but the content is byte-identical to the
    # pre-recovery baseline. Must still be rejected.
    still_wrong_resolve = SelfCorrectionResult(
        resolved=True, turns_used=1, final_compile_output="SUCCESS",
        modified_files={"build.config": "entrypoint=OldMain"},
    )

    def _fake_self_correction(*args, **kwargs):
        (tmp_path / "build.config").write_text(still_wrong_resolve.modified_files["build.config"])
        return still_wrong_resolve

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.self_correction.run_self_correction_loop",
        new=AsyncMock(side_effect=_fake_self_correction),
    ), patch("kriya.tools.validate.PolymorphicValidator"):
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 1
    assert events[0]["owner_recovery_passed"] is False
    # Only 2 real run_generation_workflow calls (s1 initial + s2 initial
    # fail) - self-correction's own no-progress candidate never triggered a
    # third (consumer was never resumed off the back of it).
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_enforce_recovery_plan_non_java_fixture(tmp_path):
    """spec §14 item 12 - genericity proof: a Python-shaped two-owner
    scenario (pyproject.toml owned by s1, service.py owned by s2), proving
    zero Java/Maven assumptions in _order_recovery_groups or
    build_recovery_execution_plan."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pyproject.toml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="write service", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="service.py", action=FileAction.CREATE)]),
            Subtask(id="s3", description="write tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="test_service.py", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["pyproject.toml"]}
        if n == 2:
            (tmp_path / "service.py").write_text("def run():\n    pass\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["service.py"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "required_files": ["pyproject.toml", "service.py"],
                    "allowed_files": ["test_service.py"],
                },
            }
        if n == 4:
            (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\ndependencies=['pytest']\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["pyproject.toml"]}
        if n == 5:
            (tmp_path / "service.py").write_text("def run():\n    return True\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["service.py"]}
        (tmp_path / "test_service.py").write_text("def test_run():\n    assert True\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["test_service.py"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 6
    events = result.legacy_result["plan_recovery_events"]
    assert {e["reopened_owner"] for e in events} == {"s1", "s2"}


@pytest.mark.asyncio
async def test_enforce_recovery_plan_atomic_rejection_one_group_corrupt(tmp_path):
    """spec §14 item 14: one group's owner writing an UNDECLARED file makes
    that group (and therefore the whole plan) fail locally - the SECOND
    group must never run, and no application file is ever committed to the
    real workspace."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="AppTest.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "required_files": ["pom.xml", "App.java"],
                    "allowed_files": ["AppTest.java"],
                },
            }
        # group.s1's own recovery writes an UNDECLARED extra file - locally
        # rejected, group.s2 (App.java) must never be invoked as a result.
        (tmp_path / "pom.xml").write_text("<project><fixed/></project>")
        (tmp_path / "Sneaky.java").write_text("class Sneaky {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml", "Sneaky.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    assert len(calls) == 4  # group.s2 (App.java) never ran
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 1
    assert events[0]["owner_recovery_passed"] is False
    s2_result = next(item for item in result.subtask_results if item.subtask_id == "s2")
    assert s2_result.status == SubtaskStatus.COMPLETED  # s2's OWN initial pass untouched - never reopened


@pytest.mark.asyncio
async def test_enforce_reopens_completed_predecessor_for_authoritative_deterministic_scope_conflict_instead_of_merging(tmp_path):
    """MA8 (spec §30, 'owner is a completed predecessor'): a DETERMINISTIC-
    authority scope conflict (attribution_tier="authoritative_deterministic"
    - currently only ever produced by the migration gate's
    failure.authoritative_files, see attribution.py) naming a file a real
    UPSTREAM subtask already owns must REOPEN that predecessor, not merge/
    steal its ownership into the failing subtask - even though
    classification == PLAN_SCOPE_DEFECT would otherwise route into the
    merge path (compare test_enforce_revises_service_scope_to_grounded_
    controller_and_continues just below, whose attribution_tier=
    "architectural_owner" case is deliberately left routing through the
    unchanged merge path)."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="remove old dependency", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.MODIFY)],
            ),
            Subtask(
                id="s2", description="migrate production usage", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/JsonService.java", action=FileAction.MODIFY)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "pom.xml").write_text("<project><dependencies><gson/></dependencies></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if len(calls) == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "classification": "PLAN_SCOPE_DEFECT",
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "migration_incomplete",
                    "required_files": ["pom.xml"],
                    "grounded_owner_files": [],
                    "attribution_tier": "authoritative_deterministic",
                    "allowed_files": ["src/JsonService.java"],
                },
            }
        if len(calls) == 3:
            (tmp_path / "pom.xml").write_text("<project><dependencies/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "JsonService.java").write_text("class JsonService {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["src/JsonService.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 4
    # s2 (the failing consumer) never got pom.xml merged into its own scope -
    # s1 was REOPENED and re-ran pom.xml itself.
    assert calls[1]["allowed_write_relpaths"] == ["src/JsonService.java"]
    assert calls[2]["allowed_write_relpaths"] == ["pom.xml"]
    assert calls[3]["allowed_write_relpaths"] == ["src/JsonService.java"]
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    approved_subtasks = approved["plan"]["subtasks"]
    # Ownership must remain exactly as originally planned - pom.xml still
    # belongs to s1, NOT stripped from it and merged into s2.
    assert any(
        item["id"] == "s1" and "pom.xml" in [pf["path"] for pf in item["planned_files"]]
        for item in approved_subtasks
    )
    assert not any(
        item["id"] == "s2" and "pom.xml" in [pf["path"] for pf in item["planned_files"]]
        for item in approved_subtasks
    )


@pytest.mark.asyncio
async def test_enforce_surfaces_unrelated_owner_scope_conflict_as_plan_revision_required_instead_of_merging(tmp_path):
    """MA8 (spec §30, 'owner is unrelated/parallel'): a DETERMINISTIC-
    authority scope conflict naming a file owned by a subtask that is
    NEITHER an upstream predecessor NOR unowned (s3 here has no
    dependency relationship with the failing s2 at all) must not be
    silently merged into the failing subtask (the architecture-discovery
    path) OR silently reopened (the completed-predecessor path only ever
    looks upstream) - it must surface as an unresolved PLAN_SCOPE_
    REVISION_REQUIRED result, per the spec's own 'treat as plan
    inconsistency... do not cross-edit silently' instruction. Compare
    test_enforce_reopens_completed_predecessor_for_authoritative_
    deterministic_scope_conflict_instead_of_merging just above (the
    PAST_ORDERED case, which DOES auto-resolve) and test_enforce_revises_
    service_scope_to_grounded_controller_and_continues just below (the
    architectural_owner-tier case, deliberately unaffected by this)."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="unrelated setup", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="src/A.java", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="consumer", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/B.java", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="unrelated parallel owner", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="src/C.java", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "A.java").write_text("class A {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["src/A.java"]}
        return {
            "status": "failed", "quality_gates_passed": False, "files": [],
            "plan_scope_conflict": {
                "classification": "PLAN_SCOPE_DEFECT",
                "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                "failure_type": "migration_incomplete",
                "required_files": ["src/C.java"],
                "grounded_owner_files": [],
                "attribution_tier": "authoritative_deterministic",
                "allowed_files": ["src/B.java"],
            },
        }

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    # Only s1 and s2's own (failed) call happened - no merge re-invoke, no
    # reopen re-invoke, and s3 (topologically free to run) never had to.
    assert len(calls) == 2
    assert result.legacy_result["status"] != "success"
    assert result.legacy_result.get("reason_codes") == ["PLAN_SCOPE_REVISION_REQUIRED"]
    assert result.legacy_result["plan_scope_conflict"]["required_files"] == ["src/C.java"]
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    approved_subtasks = approved["plan"]["subtasks"]
    # Ownership must remain exactly as originally planned - src/C.java
    # still belongs to s3, never merged into s2.
    assert any(
        item["id"] == "s3" and "src/C.java" in [pf["path"] for pf in item["planned_files"]]
        for item in approved_subtasks
    )
    assert not any(
        item["id"] == "s2" and "src/C.java" in [pf["path"] for pf in item["planned_files"]]
        for item in approved_subtasks
    )


@pytest.mark.asyncio
async def test_enforce_revises_service_scope_to_grounded_controller_and_continues(tmp_path):
    service = "src/CustomerService.java"
    controller = "src/CustomerController.java"
    test_file = "tests/CustomerControllerTest.java"
    for path in (service, controller, test_file):
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("baseline\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="existing response ownership is preserved")],
        subtasks=[
            Subtask(
                id="s2", description="update service", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=service, action=FileAction.MODIFY)],
                provides=["customer behavior"],
                relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="s3", description="compose response", execution_method=ExecutionMethod.MODEL,
                depends_on=["s2"],
                planned_files=[PlannedFile(path=controller, action=FileAction.MODIFY)],
                requires=["customer behavior"], provides=["response composed"],
                relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="s4", description="verify response", execution_method=ExecutionMethod.MODEL,
                depends_on=["s3"],
                planned_files=[PlannedFile(path=test_file, action=FileAction.MODIFY)],
                requires=["response composed"],
                relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "classification": "PLAN_SCOPE_DEFECT",
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "goal_spec_compliance",
                    "required_files": [controller],
                    "grounded_owner_files": [controller],
                    "attribution_tier": "architectural_owner",
                    "allowed_files": [service],
                },
            }
        for path in kwargs["allowed_write_relpaths"]:
            (tmp_path / path).write_text("modified\n")
        return {
            "status": "success", "quality_gates_passed": True,
            "files": kwargs["allowed_write_relpaths"],
        }

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "add displayName to the existing endpoint response",
            str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert calls[0]["allowed_write_relpaths"] == [service]
    assert calls[1]["allowed_write_relpaths"] == [service, controller]
    assert calls[1]["execution_scope"] == "subtask=s2 role=plan_scope_recovery"
    assert calls[2]["allowed_write_relpaths"] == [test_file]
    assert (tmp_path / controller).read_text() == "modified\n"
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    approved_subtasks = approved["plan"]["subtasks"]
    assert any(
        item["id"] == "s2"
        and controller in [pf["path"] for pf in item["planned_files"]]
        for item in approved_subtasks
    )
    assert not any(item["id"] == "s3" for item in approved_subtasks)
    assert all(item.status == SubtaskStatus.COMPLETED for item in result.subtask_results)


@pytest.mark.asyncio
async def test_enforce_reopens_owner_with_grounded_diagnosis_and_commits_plan_atomically(
    tmp_path, monkeypatch,
):
    source_path = "src/Formatter.java"
    test_path = "tests/FormatterTest.java"
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / source_path).write_text("incomplete\n")
    (tmp_path / test_path).write_text("test\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="output is normalized")],
        subtasks=[
            Subtask(
                id="implementation", description="fix formatter",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=source_path, action=FileAction.MODIFY)],
                provides=["formatter.fixed"], relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="tests", description="update formatter tests",
                execution_method=ExecutionMethod.MODEL, depends_on=["implementation"],
                planned_files=[PlannedFile(path=test_path, action=FileAction.MODIFY)],
                requires=["formatter.fixed"], relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    sandbox = tmp_path / "plan-sandbox"

    def create_plan_sandbox(workspace):
        shutil.copytree(workspace, sandbox, ignore=shutil.ignore_patterns(".kriya", "plan-sandbox"))
        return str(sandbox)

    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.create_git_worktree", create_plan_sandbox,
    )
    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.remove_git_worktree",
        lambda workspace, candidate: shutil.rmtree(candidate),
    )
    calls = []
    diagnosis = "Whitespace-only middle names still produce duplicate spaces"

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (sandbox / source_path).write_text("still incomplete\n")
            return {"status": "success", "quality_gates_passed": True, "files": [source_path]}
        if len(calls) == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "targeted_test", "reason": diagnosis,
                    "required_files": [source_path], "allowed_files": [test_path],
                },
            }
        if len(calls) == 3:
            assert diagnosis in kwargs["recovery_contract_block"]
            (sandbox / source_path).write_text("complete\n")
            return {"status": "success", "quality_gates_passed": True, "files": [source_path]}
        return {"status": "success", "quality_gates_passed": True, "files": [test_path]}

    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "fix formatter", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert (tmp_path / source_path).read_text() == "complete\n"
    assert not sandbox.exists()
    assert result.legacy_result["plan_recovery_events"][0]["owner_recovery_passed"] is True


@pytest.mark.asyncio
async def test_enforce_discards_successful_earlier_subtask_when_plan_fails(
    tmp_path, monkeypatch,
):
    source_path = "src/Formatter.java"
    test_path = "tests/FormatterTest.java"
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / source_path).write_text("original\n")
    (tmp_path / test_path).write_text("test\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="implementation", description="fix formatter",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=source_path, action=FileAction.MODIFY)],
            ),
            Subtask(
                id="tests", description="update tests", execution_method=ExecutionMethod.MODEL,
                depends_on=["implementation"],
                planned_files=[PlannedFile(path=test_path, action=FileAction.MODIFY)],
            ),
        ],
    )
    sandbox = tmp_path / "plan-sandbox"

    def create_plan_sandbox(workspace):
        shutil.copytree(workspace, sandbox, ignore=shutil.ignore_patterns(".kriya", "plan-sandbox"))
        return str(sandbox)

    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.create_git_worktree", create_plan_sandbox,
    )
    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.remove_git_worktree",
        lambda workspace, candidate: shutil.rmtree(candidate),
    )
    calls = 0

    async def fake_run(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            (sandbox / source_path).write_text("staged change\n")
            return {"status": "success", "quality_gates_passed": True, "files": [source_path]}
        return {"status": "failed", "quality_gates_passed": False, "files": []}

    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "fix formatter", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["quality_gates_passed"] is False
    assert (tmp_path / source_path).read_text() == "original\n"
    assert not sandbox.exists()
    persisted = load_control_state(str(tmp_path))
    assert persisted.subtask_states["implementation"] == "pending"
    assert persisted.subtask_written_files == {}


@pytest.mark.asyncio
async def test_enforce_terminal_commit_resolves_dependency_ordered_same_path_ownership(
    tmp_path, monkeypatch,
):
    """Regression test (2026-08-28): plan_validation.py's own
    AMBIGUOUS_PLANNED_FILE_OWNERSHIP check legitimately allows the same
    path to be owned by a dependency-ordered chain of subtasks (e.g. an
    "identify usages" stage and a later "migrate" stage both touching
    pom.xml). The terminal-commit assembly used to build one StagedFileWrite
    per (subtask, planned_file) pair, so a plan shaped exactly like this fed
    commit_revision_grounded_batch's own duplicate-target-path guard a
    genuine duplicate and crashed with BatchCommitError instead of
    completing. The fix resolves to exactly one write per unique path,
    sourced from the last owner in real dependency/execution order."""
    same_path = "pom.xml"
    (tmp_path / same_path).write_text("<project>original</project>\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="identify usages", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=same_path, action=FileAction.MODIFY)],
            ),
            Subtask(
                id="s2", description="migrate dependency", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path=same_path, action=FileAction.MODIFY)],
            ),
        ],
    )
    sandbox = tmp_path / "plan-sandbox"

    def create_plan_sandbox(workspace):
        shutil.copytree(workspace, sandbox, ignore=shutil.ignore_patterns(".kriya", "plan-sandbox"))
        return str(sandbox)

    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.create_git_worktree", create_plan_sandbox,
    )
    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.remove_git_worktree",
        lambda workspace, candidate: shutil.rmtree(candidate),
    )
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (sandbox / same_path).write_text("<project>s1 content</project>\n")
        else:
            (sandbox / same_path).write_text("<project>s2 final content</project>\n")
        return {"status": "success", "quality_gates_passed": True, "files": [same_path]}

    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "migrate dependency", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert result.legacy_result["quality_gates_passed"] is True
    assert (tmp_path / same_path).read_text() == "<project>s2 final content</project>\n"
    assert not sandbox.exists()


# --- enforce mode fully replaces legacy, never runs it too ---

@pytest.mark.asyncio
async def test_enforce_never_calls_run_generation_workflow_a_bonus_legacy_time(tmp_path):
    """Confirms enforce mode does NOT also invoke a separate 'legacy' pass
    the way shadow mode invokes shadow alongside legacy - enforce fully
    REPLACES the legacy call, it doesn't run alongside it."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "quality_gates_passed": True, "files": []}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    # exactly 2 calls (one per subtask) - not 3 (which would mean a bonus
    # whole-goal legacy call also happened)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_enforce_persists_control_state(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    state_file = tmp_path / ".kriya" / "control" / "state.json"
    assert state_file.is_file()
    assert result.control_state.current_plan_hash == plan.content_hash()


# --- MA7.9: real subtask retry-locality data ---

@pytest.mark.asyncio
async def test_enforce_aggregated_result_includes_real_subtask_retry_locality_data(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        return {
            "status": "success", "quality_gates_passed": True, "files": [],
            "generation_metrics": {"calls": n},  # subtask 1 -> 1 call, subtask 2 -> 2 calls
        }

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    locality = result.legacy_result["subtask_retry_locality"]
    assert locality == [
        {"subtask_id": "s1", "generation_calls": 1, "quality_gates_passed": True},
        {"subtask_id": "s2", "generation_calls": 2, "quality_gates_passed": True},
    ]


@pytest.mark.asyncio
async def test_enforce_retry_locality_data_present_even_on_a_failed_subtask(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "failed", "quality_gates_passed": False,
        "generation_metrics": {"calls": 4},
    })

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    locality = result.legacy_result["subtask_retry_locality"]
    assert len(locality) == 1  # only s1 ran before the stop
    assert locality[0]["subtask_id"] == "s1"
    assert locality[0]["generation_calls"] == 4
    assert locality[0]["quality_gates_passed"] is False


# --- MA7.10: real ArtifactRegistry derivation through an actual enforce run ---

@pytest.mark.asyncio
async def test_enforce_derives_and_persists_real_artifacts_on_full_success(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myproj"\nversion = "1.0.0"\n'
    )
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "success"
    derived = result.legacy_result.get("derived_artifacts")
    assert derived and derived[0]["ecosystem"] == "python"

    registry_file = tmp_path / ".kriya" / "control" / "artifacts.json"
    assert registry_file.is_file()


@pytest.mark.asyncio
async def test_enforce_global_final_state_check_downgrades_success_when_migration_incomplete(tmp_path):
    """Regression test for PRV-05 (2026-08-28, run 5): every subtask passing
    its own LOCAL Quality Gates is not sufficient for a migration - the
    goal explicitly authorized replacing Gson with Jackson, but the final
    applied workspace still has Gson declared/used. Even though every
    per-subtask run_generation_workflow() call here is mocked to report
    unconditional success (proving this is a NEW, additional check, not a
    side effect of the per-subtask one), the global final-state check must
    still downgrade the overall result."""
    (tmp_path / "pom.xml").write_text(
        "<project><dependencies>\n"
        "<dependency><groupId>com.google.code.gson</groupId><artifactId>gson</artifactId>"
        "<version>2.11.0</version></dependency>\n"
        "<dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId>"
        "<version>2.17.2</version></dependency>\n"
        "</dependencies></project>\n"
    )
    owner = tmp_path / "src/main/java/com/example/JsonService.java"
    owner.parent.mkdir(parents=True)
    owner.write_text(
        "package com.example;\n"
        "import com.google.gson.Gson;\n"
        "public class JsonService {\n"
        " private final Gson gson=new Gson();\n"
        " public String serialize(Object c){ return gson.toJson(c); }\n"
        "}\n"
    )
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="migrate JsonService.java to the new JSON library",
            execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="src/main/java/com/example/JsonService.java", action=FileAction.MODIFY)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute(
            "Replace the existing JSON serialization library with the JSON library already "
            "approved for this repository.",
            str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    assert result.legacy_result["quality_gates_passed"] is False
    assert "global_migration_gap" in result.legacy_result
    assert "SOURCE_DEPENDENCY_REMAINS" in result.legacy_result["global_migration_gap"]


@pytest.mark.asyncio
async def test_enforce_global_final_state_check_fails_closed_on_its_own_internal_error(tmp_path):
    """The global final-state check is authoritative and deterministic - an
    internal bug in IT must not silently fall back to trusting the
    per-subtask gates (the same silent-degrade shape as the false-PASS bug
    this check exists to close). Must downgrade to failure, not success,
    when resolve_migration_resolution itself raises."""
    (tmp_path / "pom.xml").write_text("<project></project>\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="migrate the library", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="pom.xml", action=FileAction.MODIFY)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.workflow_controller.resolve_migration_resolution",
        side_effect=RuntimeError("simulated validator bug"),
    ):
        controller = WorkflowController(we)
        result = await controller.execute(
            "Replace the existing JSON serialization library with the JSON library already "
            "approved for this repository.",
            str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    assert result.legacy_result["quality_gates_passed"] is False
    assert "INDETERMINATE" in result.legacy_result["global_migration_gap"]


@pytest.mark.asyncio
async def test_enforce_does_not_derive_artifacts_when_a_subtask_fails(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "myproj"\n')
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "failed", "quality_gates_passed": False})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert "derived_artifacts" not in result.legacy_result
    assert not (tmp_path / ".kriya" / "control" / "artifacts.json").exists()


@pytest.mark.asyncio
async def test_enforce_artifact_derivation_failure_fails_closed(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1",
            description="write a.py",
            execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.workflow_controller.ArtifactRegistry.derive_from_workspace",
        side_effect=RuntimeError("derivation exploded"),
    ):
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    # ArtifactRegistry is authoritative in enforce mode: repository success
    # without durable physical-linkage evidence requires review.
    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["quality_gates_passed"] is False
    assert result.legacy_result["artifact_error"] == "derivation exploded"
    assert "derived_artifacts" not in result.legacy_result


@pytest.mark.asyncio
async def test_enforce_aggregated_status_is_failed_if_only_some_subtasks_ran(tmp_path):
    """Belt-and-suspenders on the aggregation logic itself: if somehow
    fewer subtask_results exist than the plan's real subtask count, the
    aggregated status must never read as success."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "failed", "quality_gates_passed": False})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["quality_gates_passed"] is False
    assert len(result.legacy_result["subtask_results"]) == 1


# --- subtask-spanning resume (MA5.9 finally wired to something, 2026-08-24) ---

import subprocess

from kriya.control.persistence import load_control_state


def _init_git_repo(path):
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, capture_output=True)
    (path / "README.md").write_text("seed")
    subprocess.run(["git", "add", "README.md"], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=path, capture_output=True)


@pytest.mark.asyncio
async def test_enforce_persists_subtask_states_after_each_subtask(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    persisted = load_control_state(str(tmp_path))
    assert persisted is not None
    assert persisted.subtask_states == {"s1": "completed", "s2": "completed"}
    assert persisted.current_plan_hash == plan.content_hash()
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    assert approved is not None
    assert approved["plan_hash"] == plan.content_hash()
    assert approved["approval_status"] == "approved"
    assert approved["lifecycle_state"] == "completed"
    assert approved["stage_order"] == ["s1", "s2"]
    assert approved["stage_states"] == {"s1": "completed", "s2": "completed"}
    assert approved["plan"]["subtasks"][0]["planned_files"][0]["path"] == "a.py"


@pytest.mark.asyncio
async def test_enforce_persists_in_progress_stage_before_generation_call(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(side_effect=RuntimeError("process interrupted"))

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, pytest.raises(RuntimeError, match="process interrupted"):
        await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    assert approved is not None
    assert approved["lifecycle_state"] == "in_progress"
    assert approved["stage_states"] == {"s1": "in_progress"}


@pytest.mark.asyncio
async def test_enforce_without_resume_flag_never_skips_even_with_prior_state(tmp_path):
    """Resume is opt-in only, mirroring run_generation_workflow()'s own
    convention - a persisted ControlState from an earlier run must NOT be
    consulted at all unless resume/resume_id is explicitly passed."""
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        # "'s2'" only ever appears in s2's own goal text (its depends_on
        # header names it) - robust across however many execute() calls
        # happen in this test, unlike a bare len(calls) counter.
        path = "b.py" if "'s2'" in kwargs["goal"] else "a.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")
        # A second call, same workspace, NO resume flag - both subtasks must run again.
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert len(calls) == 4


@pytest.mark.asyncio
async def test_enforce_resume_skips_every_subtask_when_the_whole_plan_already_completed(tmp_path):
    """Both subtasks genuinely completed on the first run - a resume must
    make ZERO new real calls, restoring everything from disk."""
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        first = await controller.execute("goal", str(tmp_path), migration_mode="enforce")
        assert len(calls) == 2

        second = await controller.execute(
            "goal", str(tmp_path), migration_mode="enforce", resume=True,
        )

    assert len(calls) == 2, "nothing new should have run - both subtasks were already completed"
    assert second.legacy_result["quality_gates_passed"] is True
    assert [r["subtask_id"] for r in second.legacy_result["subtask_results"]] == ["s1", "s2"]
    assert second.legacy_result["subtask_results"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_enforce_resume_skips_only_the_subtasks_already_completed(tmp_path):
    """Subtask 2 failed on the first attempt (never got to run) - a resume
    must skip s1 (real) and still execute s2 for real."""
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()

    async def fake_run_first_attempt(**kwargs):
        (tmp_path / "a.py").write_text("# a.py")
        return {"status": "failed", "quality_gates_passed": False, "files": ["a.py"]} \
            if "'s2'" in kwargs["goal"] else {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}

    async def fake_run_second_attempt(**kwargs):
        (tmp_path / "b.py").write_text("# b.py")
        return {"status": "success", "quality_gates_passed": True, "files": ["b.py"]}

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        we.run_generation_workflow = fake_run_first_attempt
        first = await controller.execute("goal", str(tmp_path), migration_mode="enforce")
        assert first.legacy_result["quality_gates_passed"] is False

        we.run_generation_workflow = AsyncMock(side_effect=fake_run_second_attempt)
        second = await controller.execute(
            "goal", str(tmp_path), migration_mode="enforce", resume=True,
        )

    assert second.legacy_result["quality_gates_passed"] is True
    we.run_generation_workflow.assert_awaited_once()
    awaited_goal = we.run_generation_workflow.await_args.kwargs["goal"]
    assert "'s2'" in awaited_goal, "the one real call on resume must be for s2, not a re-run of s1"


@pytest.mark.asyncio
async def test_enforce_resume_refused_when_plan_hash_differs(tmp_path):
    _init_git_repo(tmp_path)
    plan_a = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) in (1, 3) else "b.py"
        (tmp_path / path).write_text(f"# {path} {len(calls)}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan_a)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")
    assert len(calls) == 2

    plan_b = EngineeringPlan(
        plan_id="run2", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="write a DIFFERENT a.py", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="write b.py", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"], planned_files=[PlannedFile(path="b.py", action=FileAction.CREATE)],
            ),
        ],
    )
    assert plan_b.content_hash() != plan_a.content_hash()

    p1, p2, p3 = _patched(plan_b)
    with p1, p2, p3:
        await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    # A different plan -> resume refused -> both subtasks run again for real.
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_enforce_resume_refused_quarantines_abandoned_plan_files(tmp_path):
    """Regression test for a real live bug, 2026-08-25 (protocol_encoder_java):
    an abandoned plan's own completed subtask had already applied a real
    file to the workspace; a later re-plan whose resume gets refused (a
    different plan hash) no longer referenced that file at all, but nothing
    ever cleaned it up - it just sat there, orphaned, alongside the new
    plan's own output. Confirms the abandoned file is moved into
    .kriya/abandoned_plan_files/ (never deleted, never left in the active
    tree) while a file BOTH plans still reference is left untouched."""
    _init_git_repo(tmp_path)
    plan_a = _two_subtask_plan()  # s1 -> a.py, s2 -> b.py
    we = _workflow_engine()
    calls = []

    async def fake_run_a(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run_a

    p1, p2, p3 = _patched(plan_a)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")
    assert (tmp_path / "b.py").exists()

    # A re-plan whose s2 writes c.py instead of b.py - a genuinely different
    # plan shape (content_hash() differs), not just an edited description.
    plan_b = EngineeringPlan(
        plan_id="run2", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="write c.py instead", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"], planned_files=[PlannedFile(path="c.py", action=FileAction.CREATE)],
            ),
        ],
    )
    assert plan_b.content_hash() != plan_a.content_hash()

    async def fake_run_b(**kwargs):
        calls.append(kwargs["goal"])
        path = "c.py" if "c.py" in kwargs["goal"] else "a.py"
        (tmp_path / path).write_text(f"# {path} v2")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run_b

    p1, p2, p3 = _patched(plan_b)
    with p1, p2, p3:
        await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    assert (tmp_path / "a.py").exists(), "still referenced by the new plan - must stay in place"
    assert (tmp_path / "c.py").exists()
    assert not (tmp_path / "b.py").exists(), "abandoned - must no longer sit in the active tree"
    quarantined = list((tmp_path / ".kriya" / "abandoned_plan_files").rglob("b.py"))
    assert len(quarantined) == 1


@pytest.mark.asyncio
async def test_enforce_resume_refused_when_workspace_has_drifted(tmp_path):
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) in (1, 3) else "b.py"
        (tmp_path / path).write_text(f"# {path} {len(calls)}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")
        assert len(calls) == 2

        # Real drift: an unrelated commit lands in the workspace between runs.
        (tmp_path / "unrelated.txt").write_text("someone else's change")
        subprocess.run(["git", "add", "unrelated.txt"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "drift"], cwd=str(tmp_path), capture_output=True)

        await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    assert len(calls) == 4


@pytest.mark.asyncio
async def test_enforce_resume_with_no_prior_state_behaves_like_a_fresh_run(tmp_path):
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    assert result.legacy_result["quality_gates_passed"] is True
    assert len(calls) == 2


# --- extension_points exemption during a real resume (2026-08-24 live-validation finding) ---

@pytest.mark.asyncio
async def test_enforce_resume_passes_own_progress_flag_to_validate_plan_when_a_subtask_already_completed(tmp_path):
    """Real live-validation bug, protocol_encoder_java: subtask s1
    genuinely completed (real file on disk), s2 exhausted its retries. On
    resume, the freshly re-planned goal correctly triggered extension_points
    validation (workspace is no longer empty) but the Planner wasn't
    prompted about continuation and didn't supply one - sending the
    resumed run down the legacy whole-goal fallback, which regenerated and
    broke s1's already-working file. WorkflowController must tell
    validate_plan this is its own established progress, not foreign
    existing work, whenever a real resume is in progress."""
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()

    async def fake_run_first(**kwargs):
        (tmp_path / "a.py").write_text("# a.py")
        return {"status": "failed", "quality_gates_passed": False, "files": ["a.py"]} \
            if "'s2'" in kwargs["goal"] else {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3 as mock_validate_plan:
        controller = WorkflowController(we)
        we.run_generation_workflow = fake_run_first
        first = await controller.execute("goal", str(tmp_path), migration_mode="enforce")
        assert first.legacy_result["quality_gates_passed"] is False
        assert mock_validate_plan.await_args.kwargs["resuming_own_established_progress"] is False, (
            "the first, non-resumed call must never claim established progress"
        )

        we.run_generation_workflow = AsyncMock(
            return_value={"status": "success", "quality_gates_passed": True, "files": ["b.py"]},
        )
        await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    assert mock_validate_plan.await_args.kwargs["resuming_own_established_progress"] is True, (
        "a resume with a real completed subtask must tell validate_plan so"
    )


@pytest.mark.asyncio
async def test_enforce_resume_does_not_claim_own_progress_when_nothing_completed_yet(tmp_path):
    """A resume request with no prior completed subtask (e.g. a
    misconfigured --resume on a workspace with real, genuinely foreign
    existing content) must NOT exempt extension_points - the exemption is
    earned by real completed progress, not merely by passing --resume."""
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(
        return_value={"status": "success", "quality_gates_passed": True, "files": ["a.py"]},
    )

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3 as mock_validate_plan:
        controller = WorkflowController(we)
        # No prior ControlState exists for this workspace at all.
        await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    assert mock_validate_plan.await_args.kwargs["resuming_own_established_progress"] is False


# --- MA7-C1 (2026-08-25 external review): bounded subtask execution ---

@pytest.mark.asyncio
async def test_enforce_passes_predetermined_plan_design_and_files_from_the_subtask(tmp_path):
    """The core MA7-C1 wiring: each subtask's own run_generation_workflow()
    call must receive predetermined_plan/predetermined_design/
    predetermined_architect_files derived from the ALREADY-VALIDATED
    Subtask, not leave run_generation_workflow() to re-plan from scratch."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert len(calls) == 2
    assert calls[0]["predetermined_plan"] == "Implement: write a.py"
    assert calls[0]["predetermined_architect_files"] == ["a.py"]
    assert calls[0]["predetermined_design"]  # non-empty, real content
    assert calls[0]["allowed_write_relpaths"] == ["a.py"]
    assert calls[1]["predetermined_architect_files"] == ["b.py"]
    assert calls[1]["allowed_write_relpaths"] == ["b.py"]


@pytest.mark.asyncio
async def test_enforce_rejects_a_subtask_that_writes_an_undeclared_file(tmp_path):
    """MA6 invariant 4, finally enforced: a subtask reporting
    quality_gates_passed=True must still be rejected if it wrote a file
    outside its own declared planned_files - a Quality-Gates pass cannot
    silently broaden the validated subtask's scope."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["a.py", "unexpected.py"],
    })

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["quality_gates_passed"] is False
    s1_result = result.subtask_results[0]
    assert s1_result.status == SubtaskStatus.FAILED
    assert s1_result.undeclared_files == ("unexpected.py",)
    assert "unexpected.py" in s1_result.error
    # A rejected s1 must stop the plan - s2 (which depends on s1) never runs.
    we.run_generation_workflow.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_accepts_a_subtask_that_stays_within_planned_files(tmp_path):
    """Sanity check: scope enforcement must not false-positive on the
    ordinary, correct case."""
    plan = _two_subtask_plan()
    we = _workflow_engine()

    async def fake_run(**kwargs):
        path = "a.py" if kwargs["predetermined_architect_files"] == ["a.py"] else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["quality_gates_passed"] is True
    assert all(r.undeclared_files == () for r in result.subtask_results)


@pytest.mark.asyncio
async def test_enforce_requires_authoritative_subtask_verification_evidence(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            verification=[VerificationMethod(
                type=VerificationMethodType.TOOL,
                tool_name="quality_gates",
                description="compile, tests, and regression pass",
            )],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["a.py"],
        "verification_results": [{
            "type": "tool", "tool_name": "quality_gates",
            "description": "compile, tests, and regression pass",
            "passed": True, "source": "existing_quality_gates",
        }],
    })
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )
    assert result.legacy_result["quality_gates_passed"] is True
    assert we.run_generation_workflow.await_args.kwargs["required_verification"][0]["tool_name"] == "quality_gates"


@pytest.mark.asyncio
async def test_enforce_marks_unresolved_subtask_verification_needs_review(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            verification=[VerificationMethod(
                type=VerificationMethodType.JUDGMENT,
                description="human confirms the behavior",
            )],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["a.py"],
        "verification_results": [{
            "type": "judgment", "tool_name": None,
            "description": "human confirms the behavior",
            "passed": None, "source": "unresolved",
        }],
    })
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )
    assert result.legacy_result["quality_gates_passed"] is False
    assert result.subtask_results[0].status == SubtaskStatus.NEEDS_REVIEW
    assert result.subtask_results[0].reason_codes == ("VERIFICATION_UNRESOLVED",)


@pytest.mark.asyncio
async def test_enforce_rejects_model_subtask_without_planned_files(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(id="s1", description="do something", execution_method=ExecutionMethod.MODEL)],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["whatever.py"],
    })

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["quality_gates_passed"] is False
    assert result.legacy_result["reason_codes"] == [
        "MODEL_SUBTASK_MISSING_PLANNED_FILES",
        "STRUCTURED_PLAN_REPAIR_EXHAUSTED",
        # MA8: the planner is mocked to return the identical broken plan on
        # every repair attempt, so the same obligation stays VIOLATED across
        # all 3 attempts (never oscillates through SATISFIED) - the
        # exhaustion branch correctly reports non-convergence, not
        # oscillation. Pre-existing test gap (this assertion predates MA8
        # v1's oscillation/non-convergence diagnostics), fixed while
        # verifying Batch 1 - not caused by Batch 1 itself.
        "PLAN_REPAIR_NON_CONVERGENCE",
    ]
    assert result.legacy_result["plan_repair_attempts"] == 2
    assert result.legacy_result["invalid_subtask_ids"] == ["s1"]
    assert we.planner.run.await_count == 3
    we.run_generation_workflow.assert_not_awaited()


# --- MA8.1 (PRV-06, 2026-08-29): Cross-Owner Requirement-Preserving Recovery
# - a live run proved the grounded reason for reopening an upstream owner
# (e.g. "pom.xml is missing the JUnit 5 dependency s4's tests need") was not
# surviving the handoff: the owner got reopened with a generic "preserve
# brownfield identity" framing, regenerated an equally incomplete file, and
# the same downstream failure recurred. A separate, real bug in the SAME
# incident: the "preferred" grounded-owner plan-revision path produced a
# cyclic subtask dependency graph when reopening an owner that had OTHER,
# earlier consumers upstream of the failing stage. ---

def _prv06_shaped_plan():
    """The exact shape of the live PRV-06 Hardened failure: a build
    manifest (s1) owned upstream of two parallel production subtasks (s2,
    s3), which a test-writing subtask (s4) depends on. s4 proves s1 is
    missing something it needs; s1 must be reopened WITHOUT inverting the
    s1 -> s2/s3 -> s4 dependency direction."""
    return EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="create App", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/main/java/App.java", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="create InMemoryService", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/main/java/InMemoryService.java", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s4", description="create tests", execution_method=ExecutionMethod.MODEL,
                depends_on=["s2", "s3"],
                planned_files=[
                    PlannedFile(path="src/test/java/AppTest.java", action=FileAction.CREATE),
                    PlannedFile(path="src/test/java/InMemoryServiceTest.java", action=FileAction.CREATE),
                ],
            ),
        ],
    )


def test_transitive_upstream_ids_finds_everything_a_subtask_depends_on():
    plan = _prv06_shaped_plan()
    assert _transitive_upstream_ids(plan, "s4") == {"s1", "s2", "s3"}
    assert _transitive_upstream_ids(plan, "s2") == {"s1"}
    assert _transitive_upstream_ids(plan, "s1") == set()


def test_revise_plan_for_grounded_scope_owner_does_not_invert_dependency_direction(tmp_path):
    """The exact live cyclic-DAG incident, reproduced directly: reopening
    s1 (pom.xml) for the failing s4 must not redirect s1's OTHER consumers
    (s2, s3 - both upstream of s4) to depend on s4 instead - that would
    invert s1 -> s2/s3 -> s4 into a cycle. Confirmed via
    _transitive_upstream_ids: no subtask may end up (transitively)
    depending on itself."""
    plan = _prv06_shaped_plan()
    (tmp_path / "pom.xml").write_text("<project/>")

    revised = revise_plan_for_grounded_scope_owner(plan, "s4", ["pom.xml"], str(tmp_path))

    # s1 is gone (merged into s4); s2 and s3 must NOT depend on s4.
    assert revised.subtask_by_id("s1") is None
    s2 = revised.subtask_by_id("s2")
    s3 = revised.subtask_by_id("s3")
    assert "s4" not in s2.depends_on
    assert "s4" not in s3.depends_on
    # No subtask transitively depends on itself - the direct cycle check.
    for subtask in revised.subtasks:
        assert subtask.id not in _transitive_upstream_ids(revised, subtask.id)
    # Must still validate cleanly as a real plan (acyclic, well-formed).
    EngineeringPlan.model_validate(revised.model_dump(mode="json"))


def test_cross_owner_obligation_id_stable_and_scoped():
    id1 = _cross_owner_obligation_id("s4", "s1", ["pom.xml"], "compile", 0)
    id2 = _cross_owner_obligation_id("s4", "s1", ["pom.xml"], "compile", 0)
    id3 = _cross_owner_obligation_id("s4", "s2", ["pom.xml"], "compile", 0)
    assert id1 == id2
    assert id1 != id3


def test_cross_owner_obligation_id_distinguishes_failure_family_and_generation():
    """Test C-equivalent: same origin/owner/files, different failure_family
    or generation, must NOT collide - this is what lets a genuinely new
    requirement coexist with (rather than silently overwrite) an earlier
    one on the same owner+artifact."""
    base = _cross_owner_obligation_id("s4", "s3", ["MainApplication.java"], "diagnosis_mismatch", 0)
    different_family = _cross_owner_obligation_id("s4", "s3", ["MainApplication.java"], "structural_corruption", 0)
    different_generation = _cross_owner_obligation_id("s4", "s3", ["MainApplication.java"], "diagnosis_mismatch", 1)
    assert base != different_family
    assert base != different_generation
    assert different_family != different_generation


def _scope_conflict_fixture(reason="grounded reason", raw_evidence="javac error", required_files=("pom.xml",)):
    required_files = list(required_files)
    return {
        "classification": "PLAN_SCOPE_DEFECT",
        "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
        "failure_type": "compile",
        "reason": reason,
        "required_files": required_files,
        "allowed_files": [],
        "attribution_tier": "architectural_owner",
        "grounded_owner_files": required_files,
        "raw_evidence": raw_evidence,
    }


def test_get_or_create_cross_owner_obligation_creates_with_real_evidence():
    ledger = ObligationLedger()
    scope_conflict = _scope_conflict_fixture(
        reason="pom.xml is missing the JUnit 5 dependency",
        raw_evidence="package org.junit.jupiter.api does not exist",
    )

    record = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s4", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=scope_conflict, generation=0, revision=1,
    )

    assert record is not None
    assert record.kind == ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT
    assert record.status == ObligationStatus.VIOLATED
    assert record.authority == ObligationAuthority.DETERMINISTIC
    assert record.owner_subtask_id == "s1"
    assert record.repair_scope == ("pom.xml",)
    assert "pom.xml is missing the JUnit 5 dependency" in record.evidence["must_fix"][0]
    assert record.evidence["raw_evidence"] == "package org.junit.jupiter.api does not exist"
    assert record.evidence["originating_subtask_id"] == "s4"
    assert ledger.current(record.id) is record


def test_get_or_create_cross_owner_obligation_is_sticky_across_a_failed_attempt():
    """A second call for the SAME requirement (same originating subtask,
    owner, required files, failure_family, AND generation) while it is
    still VIOLATED must reuse the exact same MUST_FIX/evidence CONTENT -
    even when the current scope_conflict's own evidence is weaker/
    different (e.g. a later static-rule violation instead of the original
    compiler error) - never silently regressing the text an owner-recovery
    prompt depends on. A fresh history entry IS still recorded each time
    (Test B-equivalent: recurrence must remain countable, not invisible -
    see the function's own docstring for why)."""
    ledger = ObligationLedger()
    strong_evidence = _scope_conflict_fixture(
        reason="pom.xml is missing the JUnit 5 dependency",
        raw_evidence="package org.junit.jupiter.api does not exist",
    )
    first = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s4", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=strong_evidence, generation=0, revision=1,
    )

    weaker_evidence = _scope_conflict_fixture(
        reason="unrelated static rule violation", raw_evidence="",
    )
    second = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s4", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=weaker_evidence, generation=0, revision=2,
    )

    assert second.id == first.id
    assert second.evidence["raw_evidence"] == "package org.junit.jupiter.api does not exist"
    assert len(ledger.history(first.id)) == 2


def test_get_or_create_cross_owner_obligation_no_ledger_returns_none():
    assert _get_or_create_cross_owner_obligation(
        None, originating_subtask_id="s4", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=_scope_conflict_fixture(), generation=0, revision=1,
    ) is None


def test_build_owner_recovery_context_surfaces_obligation_not_generic_framing():
    """§28.7-equivalent for MA8.1: the reopened owner's prompt must show
    the exact grounded MUST_FIX/MUST_PRESERVE/EVIDENCE/ACCEPTANCE, never
    fall back to the old generic 'preserve brownfield owner identity'
    framing, whenever an obligation is available."""
    obligation = ObligationRecord(
        id="recovery.s4.s1.pom.xml", kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
        status=ObligationStatus.VIOLATED, authority=ObligationAuthority.DETERMINISTIC,
        description="x", source="test", revision=1,
        evidence={
            "must_fix": ["provide the JUnit 5 dependency required by AppTest.java"],
            "must_preserve": ["existing project identity"],
            "raw_evidence": "package org.junit.jupiter.api does not exist",
            "acceptance_conditions": ["subtask s4 passes its own Quality Gates"],
        },
        owner_subtask_id="s1", repair_scope=("pom.xml",),
    )

    context = _build_owner_recovery_context(
        owner_id="s1", failed_subtask_id="s4", required_owner_files=["pom.xml"],
        cross_owner_obligation=obligation, scope_conflict=_scope_conflict_fixture(),
    )

    assert "MUST FIX" in context
    assert "provide the JUnit 5 dependency required by AppTest.java" in context
    assert "MUST PRESERVE" in context
    assert "existing project identity" in context
    assert "EVIDENCE" in context
    assert "package org.junit.jupiter.api does not exist" in context
    assert "ACCEPTANCE" in context
    assert "subtask s4 passes its own Quality Gates" in context


def test_build_owner_recovery_context_falls_back_without_ledger():
    context = _build_owner_recovery_context(
        owner_id="s1", failed_subtask_id="s4", required_owner_files=["pom.xml"],
        cross_owner_obligation=None,
        scope_conflict=_scope_conflict_fixture(reason="some diagnosis"),
    )
    assert "authoritative plan recovery" in context
    assert "some diagnosis" in context


@pytest.mark.asyncio
async def test_enforce_reopened_owner_receives_grounded_requirement_not_generic_framing(tmp_path):
    """End-to-end reproduction of the live PRV-06 shape (§28.2/28.7-
    equivalent): s4's compile failure grounds a requirement on s1
    (pom.xml); the reopened owner's own Developer call must receive the
    exact MUST_FIX/evidence text, not a generic framing - and the
    obligation is SATISFIED only once s4's own retry actually passes."""
    plan = _prv06_shaped_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n <= 3:
            # s1, s2, s3 all succeed cleanly on the first pass.
            for path in kwargs["allowed_write_relpaths"]:
                full = tmp_path / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text("baseline\n")
            return {"status": "success", "quality_gates_passed": True, "files": kwargs["allowed_write_relpaths"]}
        if n == 4:
            # s4 fails - grounded evidence points at pom.xml.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _scope_conflict_fixture(
                    reason="pom.xml is missing the JUnit 5 dependency required by the generated tests",
                    raw_evidence="package org.junit.jupiter.api does not exist",
                ),
            }
        if n == 5:
            # s1 owner_recovery - this time actually fixes it.
            for path in kwargs["allowed_write_relpaths"]:
                (tmp_path / path).write_text("<project><dependencies><junit/></dependencies></project>")
            return {"status": "success", "quality_gates_passed": True, "files": kwargs["allowed_write_relpaths"]}
        # s4 consumer_retry - now passes.
        for path in ["src/test/java/AppTest.java", "src/test/java/InMemoryServiceTest.java"]:
            full = tmp_path / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("class Test {}\n")
        return {"status": "success", "quality_gates_passed": True, "files": [
            "src/test/java/AppTest.java", "src/test/java/InMemoryServiceTest.java",
        ]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 6
    # The reopened owner (call index 4, s1's owner_recovery) must have
    # received the exact grounded requirement, not generic framing.
    owner_recovery_context = calls[4]["recovery_contract_block"]
    assert "MUST FIX" in owner_recovery_context
    assert "pom.xml is missing the JUnit 5 dependency" in owner_recovery_context
    assert "EVIDENCE" in owner_recovery_context
    assert "package org.junit.jupiter.api does not exist" in owner_recovery_context
    assert "ACTIVE CROSS-OWNER RECOVERY REQUIREMENT" in owner_recovery_context
    # And the ORIGINAL DAG must remain intact - no cyclic-DAG plan
    # revision was ever needed for this shape.
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    approved_ids = {item["id"] for item in approved["plan"]["subtasks"]}
    assert approved_ids == {"s1", "s2", "s3", "s4"}


@pytest.mark.asyncio
async def test_enforce_cross_owner_recovery_generic_non_maven_shape(tmp_path):
    """Genericity proof (mirrors MA9's own heterogeneous-artifact tests):
    the identical mechanism, with Python-flavored paths - pyproject.toml
    reopened because a downstream test subtask proves a dependency is
    missing. No Java/Maven-specific code path is exercised anywhere in
    workflow_controller.py's own logic; only the evidence/paths differ."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pyproject.toml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="create service", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="service.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                depends_on=["s2"],
                planned_files=[PlannedFile(path="test_service.py", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n <= 2:
            for path in kwargs["allowed_write_relpaths"]:
                full = tmp_path / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text("baseline\n")
            return {"status": "success", "quality_gates_passed": True, "files": kwargs["allowed_write_relpaths"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _scope_conflict_fixture(
                    reason="pyproject.toml is missing the pytest dependency required by the generated tests",
                    raw_evidence="ModuleNotFoundError: No module named 'pytest'",
                    required_files=["pyproject.toml"],
                ),
            }
        if n == 4:
            for path in kwargs["allowed_write_relpaths"]:
                (tmp_path / path).write_text("[tool.poetry.dependencies]\npytest = \"*\"\n")
            return {"status": "success", "quality_gates_passed": True, "files": kwargs["allowed_write_relpaths"]}
        (tmp_path / "test_service.py").write_text("def test_x(): pass\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["test_service.py"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 5
    owner_recovery_context = calls[3]["recovery_contract_block"]
    assert "MUST FIX" in owner_recovery_context
    assert "pytest dependency" in owner_recovery_context
    assert "ModuleNotFoundError" in owner_recovery_context


@pytest.mark.asyncio
async def test_enforce_permits_a_second_distinct_generation_recovery_on_the_same_owner(tmp_path):
    """MA8.1 completion, Test A: the exact live PRV-06 shape - s3's first
    recovery of s1 (pom.xml) succeeds, but s3's OWN retry then surfaces a
    SECOND, genuinely different requirement on the SAME owner+file (same
    required_files, but a different failure_type: "misdirected_edit" then
    "diagnosis_mismatch" - mirroring the real incident where both
    occurrences happened to share the same failure_type yet were still
    different requirements). A bare owner-id one-shot guard would silently
    block this second recovery; the requirement-scoped identity must
    permit it. Expected: two owner-recovery cycles run, both requirement
    ids differ (R1.id != R2.id) even though owner/originating-subtask/
    required_files are identical, and the run still finishes as an overall
    success once the second recovery actually resolves the requirement."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="create application", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/App.java", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    def _conflict(failure_type):
        return {
            "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
            "failure_type": failure_type,
            "required_files": ["pom.xml"],
            "allowed_files": ["src/App.java"],
        }

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            # s3 attempt 1 - first requirement.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _conflict("misdirected_edit"),
            }
        if n == 3:
            # s1 owner_recovery, generation 0.
            (tmp_path / "pom.xml").write_text("<project><dependencies/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 4:
            # s3 consumer_retry, generation 0 - passes its own gate for
            # requirement 1 but surfaces a SECOND, distinct requirement.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _conflict("diagnosis_mismatch"),
            }
        if n == 5:
            # s1 owner_recovery, generation 1 - the second, real fix.
            (tmp_path / "pom.xml").write_text("<project><dependencies><junit/></dependencies></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        # s3 consumer_retry, generation 1 - now genuinely passes.
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "App.java").write_text("class App {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["src/App.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert result.legacy_result["quality_gates_passed"] is True
    assert len(calls) == 6
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 2
    assert events[0]["generation"] == 0
    assert events[1]["generation"] == 1
    assert events[0]["requirement_id"] == "recovery.s3.s1.pom.xml.misdirected_edit.0"
    assert events[1]["requirement_id"] == "recovery.s3.s1.pom.xml.diagnosis_mismatch.1"
    # The defining claim of Test A: two genuinely different requirements
    # on the same owner get two genuinely different requirement ids.
    assert events[0]["requirement_id"] != events[1]["requirement_id"]
    assert events[0]["reopened_owner"] == events[1]["reopened_owner"] == "s1"


@pytest.mark.asyncio
async def test_enforce_bounds_recovery_attempts_per_downstream_owner_pair(tmp_path):
    """MA8.1 completion, Tests B/G: even though every completed recovery
    cycle advances the generation counter (so the requirement id keeps
    changing and never itself hits its own per-id cap), the SURROUNDING
    loop is still bounded by the existing, unchanged
    _MAX_PLAN_SCOPE_REVISION_ATTEMPTS=3 - the global recovery budget this
    module already enforced before MA8.1 ever existed. A requirement that
    keeps recurring (never actually gets fixed) must not reopen its owner
    forever."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="create application", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/App.java", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []
    conflict = {
        "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
        "failure_type": "compile",
        "required_files": ["pom.xml"],
        "allowed_files": ["src/App.java"],
    }

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n >= 3 and n % 2 == 1:
            # every owner_recovery call (n=3,5,7) "succeeds" its own narrow
            # gate but never actually resolves the downstream requirement -
            # each writes DISTINCT (not byte-identical) content so this
            # stays a pure test of the surrounding generation-count bound,
            # not of RECOVERY_NO_PROGRESS (see
            # test_enforce_owner_recovery_no_progress_blocks_completion_and_
            # never_resumes_consumer for that, dedicated, invariant).
            (tmp_path / "pom.xml").write_text(f"<project><attempt-{n}/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        # every s3 attempt - the initial one (n=2) and every consumer_retry
        # (n=4,6,8) - hits the same unresolved requirement, forever.
        return {
            "status": "failed", "quality_gates_passed": False, "files": [],
            "plan_scope_conflict": dict(conflict),
        }

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    # 1 (s1 initial) + 1 (s3's own first attempt) + 3 * (owner_recovery,
    # consumer_retry) = 8.
    assert len(calls) == 8
    assert result.legacy_result["status"] != "success"
    events = result.legacy_result["plan_recovery_events"]
    assert [event["generation"] for event in events] == [0, 1, 2]
    requirement_ids = {event["requirement_id"] for event in events}
    # Three distinct requirement ids (generation keeps advancing) - the
    # bound comes from the surrounding loop, not from any one id's own
    # history ever reaching the cap.
    assert len(requirement_ids) == 3


@pytest.mark.asyncio
async def test_enforce_second_owner_recovery_preserves_earlier_requirements_must_fix_text(tmp_path):
    """MA8.1 completion, Tests D/F: two DIFFERENT downstream subtasks (s3,
    s4) independently discover two DIFFERENT requirements on the SAME
    upstream owner (s1, pom.xml). Both requirements must be able to exist
    (Test D - simultaneously active, distinct ids scoped by originating
    subtask as well as failure_family/generation), and the SECOND owner
    recovery's own prompt must carry forward the FIRST requirement's
    MUST_FIX text as a MUST_PRESERVE entry (Test F / invariant 6 - "a
    later recovery of an owner must preserve corrections established by
    earlier active recovery requirements")."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="create App", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/App.java", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s4", description="create tests", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1", "s3"],
                planned_files=[PlannedFile(path="src/AppTest.java", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            # s1 initial.
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            # s3 attempt 1 - first requirement, grounded on JUnit.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "reason": "pom.xml is missing the JUnit 5 dependency required by App.java",
                    "raw_evidence": "package org.junit.jupiter.api does not exist",
                    "required_files": ["pom.xml"],
                    "allowed_files": ["src/App.java"],
                },
            }
        if n == 3:
            # s1 owner_recovery for s3's requirement - fixes it for real.
            (tmp_path / "pom.xml").write_text("<project><dependencies><junit/></dependencies></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 4:
            # s3 consumer_retry - passes.
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["src/App.java"]}
        if n == 5:
            # s4 attempt 1 - a DIFFERENT requirement, grounded on Jackson.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "reason": "pom.xml is missing the Jackson dependency required by AppTest.java",
                    "raw_evidence": "package com.fasterxml.jackson.databind does not exist",
                    "required_files": ["pom.xml"],
                    "allowed_files": ["src/AppTest.java"],
                },
            }
        if n == 6:
            # s1 owner_recovery for s4's requirement.
            (tmp_path / "pom.xml").write_text(
                "<project><dependencies><junit/><jackson/></dependencies></project>",
            )
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        # s4 consumer_retry - passes.
        (tmp_path / "src" / "AppTest.java").write_text("class AppTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["src/AppTest.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 7
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 2
    # Test D: two simultaneously-active requirements on the same owner,
    # from two different origins, get two distinct ids.
    assert events[0]["requirement_id"] == "recovery.s3.s1.pom.xml.compile.0"
    assert events[1]["requirement_id"] == "recovery.s4.s1.pom.xml.compile.0"
    assert events[0]["requirement_id"] != events[1]["requirement_id"]
    # Test F: the SECOND owner-recovery prompt (call index 5) must carry
    # forward the FIRST requirement's own MUST_FIX text as something to
    # preserve, not just its own new one.
    second_owner_recovery_context = calls[5]["recovery_contract_block"]
    assert "MUST FIX" in second_owner_recovery_context
    assert "Jackson dependency" in second_owner_recovery_context
    assert "MUST PRESERVE" in second_owner_recovery_context
    assert "JUnit 5 dependency required by App.java" in second_owner_recovery_context


def test_get_or_create_cross_owner_obligation_satisfying_one_generation_leaves_another_untouched():
    """MA8.1 completion, Test E: marking one requirement's obligation
    SATISFIED (the real acceptance signal is always the ORIGINATING
    subtask's own consumer_retry passing - see workflow_controller.py's
    own comment where this exact ObligationRecord construction lives)
    must not incidentally satisfy a DIFFERENT requirement that happens to
    share the same owner+artifact - satisfaction is per requirement id,
    never per owner."""
    ledger = ObligationLedger()
    first = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s3", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=_scope_conflict_fixture(reason="needs junit"),
        generation=0, revision=1,
    )
    second = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s4", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=_scope_conflict_fixture(reason="needs jackson"),
        generation=0, revision=1,
    )
    assert first.id != second.id

    # s3's own retry passes - only s3's requirement is satisfied.
    ledger.record(ObligationRecord(
        id=first.id, kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
        status=ObligationStatus.SATISFIED, authority=ObligationAuthority.DETERMINISTIC,
        description=first.description, source="test", revision=2,
        evidence=first.evidence, owner_subtask_id=first.owner_subtask_id,
        terminal_required=False, repair_scope=first.repair_scope,
    ))

    assert ledger.current(first.id).status == ObligationStatus.SATISFIED
    assert ledger.current(second.id).status == ObligationStatus.VIOLATED


@pytest.mark.asyncio
async def test_enforce_generic_non_maven_shape_permits_a_second_distinct_recovery(tmp_path):
    """MA8.1 completion, Test H: the genericity proof for the two-
    requirement shape (mirrors test_enforce_cross_owner_recovery_generic_
    non_maven_shape, extended to a second, distinct recovery cycle) -
    nothing about requirement-scoped identity or the bounded loop is
    Java/Maven-specific."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pyproject.toml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="create tests", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="test_service.py", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    def _conflict(failure_type):
        return {
            "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
            "failure_type": failure_type,
            "required_files": ["pyproject.toml"],
            "allowed_files": ["test_service.py"],
        }

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pyproject.toml").write_text("[project]\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["pyproject.toml"]}
        if n == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _conflict("missing_dependency"),
            }
        if n == 3:
            (tmp_path / "pyproject.toml").write_text("[project]\ndependencies = ['pytest']\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["pyproject.toml"]}
        if n == 4:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _conflict("import_error"),
            }
        if n == 5:
            (tmp_path / "pyproject.toml").write_text(
                "[project]\ndependencies = ['pytest', 'requests']\n",
            )
            return {"status": "success", "quality_gates_passed": True, "files": ["pyproject.toml"]}
        (tmp_path / "test_service.py").write_text("def test_x(): pass\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["test_service.py"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 6
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 2
    assert events[0]["requirement_id"] != events[1]["requirement_id"]
    assert events[0]["generation"] == 0
    assert events[1]["generation"] == 1


# --- MA8.1 completion v3 (2026-08-29): "Effective Artifact Ownership and
# Recovery Routing" - Kriya must be able to recover an artifact even when
# the subtask that produced its currently-visible revision is absent from
# the failing subtask's own declared depends_on. Live PRV-06 evidence: s1
# created App.java, s2 later successfully MODIFIED it, s3 (needing a
# further App.java fix) declared depends_on=['s1'] only - resolve_scope_
# conflict_owners (depends_on-only) could never find s2, so recovery fell
# through to the DAG-mutating merge path, which hit its own real
# ownership-uniqueness bug and the run terminated unresolved. See
# resolve_effective_artifact_owner's own docstring for the full authority
# order (execution provenance first, dependency ancestry as fallback).


def _owner_provenance_control_state(subtask_written_files: Dict[str, List[str]]) -> ControlState:
    return ControlState(schema_version=1, run_id="owner-provenance-test").with_updates(
        subtask_states={sid: "completed" for sid in subtask_written_files},
        subtask_written_files=dict(subtask_written_files),
    )


def test_resolve_effective_artifact_owner_latest_successful_modifier_wins():
    """Test A: s1 creates A, s2 later successfully modifies A - the most
    recent completed, real modifier (s2) wins, basis=
    LATEST_SUCCESSFUL_MODIFIER."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create A", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="A.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="modify A", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="A.java", action=FileAction.MODIFY)]),
            Subtask(id="s3", description="needs A", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="B.java", action=FileAction.CREATE)]),
        ],
    )
    order = ["s1", "s2", "s3"]
    control_state = _owner_provenance_control_state({"s1": ["A.java"], "s2": ["A.java"]})

    resolution = resolve_effective_artifact_owner(
        plan, "A.java", plan.subtask_by_id("s3"), order=order, control_state=control_state,
    )

    assert resolution.owner_subtask_id == "s2"
    assert resolution.resolution_basis == ArtifactOwnerResolutionBasis.LATEST_SUCCESSFUL_MODIFIER


def test_resolve_effective_artifact_owner_missing_declared_dependency_does_not_hide_real_owner():
    """Test B: the exact live PRV-06 shape - s3's OWN depends_on names s1
    but not s2, even though s2 is the artifact's real, already-completed
    modifier. Must still resolve to s2. The OLD dependency-only resolver
    (resolve_scope_conflict_owners) genuinely cannot - asserted directly to
    prove this is a real fix, not incidental."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create A", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="A.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="modify A", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="A.java", action=FileAction.MODIFY)]),
            Subtask(id="s3", description="needs A", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],  # deliberately NOT s2
                    planned_files=[PlannedFile(path="B.java", action=FileAction.CREATE)]),
        ],
    )
    order = ["s1", "s2", "s3"]
    control_state = _owner_provenance_control_state({"s1": ["A.java"], "s2": ["A.java"]})

    assert resolve_scope_conflict_owners(plan, ["A.java"], plan.subtask_by_id("s3")) == {}

    resolution = resolve_effective_artifact_owner(
        plan, "A.java", plan.subtask_by_id("s3"), order=order, control_state=control_state,
    )

    assert resolution.owner_subtask_id == "s2"
    assert resolution.resolution_basis == ArtifactOwnerResolutionBasis.LATEST_SUCCESSFUL_MODIFIER


def test_resolve_effective_artifact_owner_rejects_arbitrary_topological_predecessor():
    """Test C: s1 wrote B.txt, s2 wrote A.txt - both ran before s3, but
    only s2 ever actually wrote A.txt. s1 must never be selected merely
    because it executed earlier (PRV-06 §4/§23 invariant 2)."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create B", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="B.txt", action=FileAction.CREATE)]),
            Subtask(id="s2", description="create A", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="A.txt", action=FileAction.CREATE)]),
            Subtask(id="s3", description="needs A", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="C.txt", action=FileAction.CREATE)]),
        ],
    )
    order = ["s1", "s2", "s3"]
    control_state = _owner_provenance_control_state({"s1": ["B.txt"], "s2": ["A.txt"]})

    resolution = resolve_effective_artifact_owner(
        plan, "A.txt", plan.subtask_by_id("s3"), order=order, control_state=control_state,
    )

    assert resolution.owner_subtask_id == "s2"
    assert resolution.owner_subtask_id != "s1"


def test_resolve_effective_artifact_owner_multiple_historical_modifiers_latest_wins():
    """Test D: s1 writes A, s2 modifies A, s3 modifies A, s4 requires A -
    the LATEST (s3), not the first or a middle one, wins."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create A", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="A.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="modify A", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="A.java", action=FileAction.MODIFY)]),
            Subtask(id="s3", description="modify A again", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s2"],
                    planned_files=[PlannedFile(path="A.java", action=FileAction.MODIFY)]),
            Subtask(id="s4", description="needs A", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="B.java", action=FileAction.CREATE)]),
        ],
    )
    order = ["s1", "s2", "s3", "s4"]
    control_state = _owner_provenance_control_state({
        "s1": ["A.java"], "s2": ["A.java"], "s3": ["A.java"],
    })

    resolution = resolve_effective_artifact_owner(
        plan, "A.java", plan.subtask_by_id("s4"), order=order, control_state=control_state,
    )

    assert resolution.owner_subtask_id == "s3"
    assert resolution.evidence["prior_modifiers"] == ["s1", "s2", "s3"]


def test_resolve_effective_artifact_owner_failed_modifier_does_not_become_owner():
    """Test E: s1 writes A successfully; s2 attempts A but FAILS quality
    gates (still recorded a written-files entry, matching real
    ControlState behavior for a failed subtask); s3 requires A. A failed
    candidate must not acquire ownership - owner stays s1."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create A", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="A.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="modify A", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="A.java", action=FileAction.MODIFY)]),
            Subtask(id="s3", description="needs A", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="B.java", action=FileAction.CREATE)]),
        ],
    )
    order = ["s1", "s2", "s3"]
    control_state = ControlState(schema_version=1, run_id="t").with_updates(
        subtask_states={"s1": "completed", "s2": "failed"},
        subtask_written_files={"s1": ["A.java"], "s2": ["A.java"]},
    )

    resolution = resolve_effective_artifact_owner(
        plan, "A.java", plan.subtask_by_id("s3"), order=order, control_state=control_state,
    )

    assert resolution.owner_subtask_id == "s1"


def test_resolve_effective_artifact_owner_is_artifact_type_agnostic():
    """Test F: identical behavior for a non-source, non-Java artifact - no
    language/build-system special case anywhere in the resolver."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create manifest", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="pyproject.toml", action=FileAction.CREATE)]),
            Subtask(id="s2", description="modify manifest", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="pyproject.toml", action=FileAction.MODIFY)]),
            Subtask(id="s3", description="needs a further correction", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="test_service.py", action=FileAction.CREATE)]),
        ],
    )
    order = ["s1", "s2", "s3"]
    control_state = _owner_provenance_control_state({
        "s1": ["pyproject.toml"], "s2": ["pyproject.toml"],
    })

    resolution = resolve_effective_artifact_owner(
        plan, "pyproject.toml", plan.subtask_by_id("s3"), order=order, control_state=control_state,
    )

    assert resolution.owner_subtask_id == "s2"


@pytest.mark.asyncio
async def test_enforce_duplicate_type_shape_resolves_effective_owner_via_execution_provenance(tmp_path):
    """§14 (deterministic reproduction of the live PRV-06 Hardened shape)
    and Test I (the fallback actually runs owner recovery, proven by real
    _invoke_bounded_subtask calls - not just log text): s1 creates
    App.java, s2 later successfully modifies it, s3 (owning InMemoryService
    .java, depends_on=['s1'] only - s2 is deliberately NOT declared)
    encounters deterministic duplicate-type evidence requiring App.java to
    change. Must resolve owner=s2 via execution provenance, thread the
    exact grounded requirement to s2, cause NO plan DAG mutation, and
    resume s3 afterward."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create App", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.MODIFY)]),
            Subtask(id="s3", description="create InMemoryService", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],  # deliberately NOT s2 - the exact live gap
                    planned_files=[PlannedFile(path="InMemoryService.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "App.java").write_text("class App {}\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App {}\nclass InMemoryService {}\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "duplicate_type",
                    "reason": "InMemoryService is already declared in App.java",
                    "raw_evidence": "'InMemoryService' is already declared in App.java",
                    "required_files": ["App.java"],
                    "allowed_files": ["InMemoryService.java"],
                },
            }
        if n == 4:
            (tmp_path / "App.java").write_text("class App {}\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        (tmp_path / "InMemoryService.java").write_text("class InMemoryService {}\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["InMemoryService.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 5
    # calls[3] is s2's real owner-recovery call - proof the fallback
    # actually ran, not just logged.
    assert calls[3]["allowed_write_relpaths"] == ["App.java"]
    owner_recovery_context = calls[3]["recovery_contract_block"]
    assert "MUST FIX" in owner_recovery_context
    assert "InMemoryService is already declared in App.java" in owner_recovery_context
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 1
    assert events[0]["reopened_owner"] == "s2"
    # No plan DAG mutation occurred - the approved plan's own subtask set
    # and edges are exactly unchanged from the original.
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    approved_subtasks = {item["id"]: item for item in approved["plan"]["subtasks"]}
    assert set(approved_subtasks) == {"s1", "s2", "s3"}
    assert approved_subtasks["s3"]["depends_on"] == ["s1"]
    # And the OLD dependency-only resolver genuinely could not have found
    # this owner - confirming this is a real fix, not incidental.
    assert resolve_scope_conflict_owners(plan, ["App.java"], plan.subtask_by_id("s3")) == {}


@pytest.mark.asyncio
async def test_enforce_deny_all_runtime_verification_failure_routes_through_owner_recovery_and_resumes(tmp_path):
    """Verification-Only Recovery Routing, WHOLE-CHAIN proof (PRV-06,
    2026-08-29) - user's own explicit request: prove the two fixes
    together, not each in isolation. `s4` is a REAL files=[]/
    execution_role=VERIFICATION subtask whose sole verifier declares
    verifier_kind=application_runtime with requires_runtime_execution
    OMITTED at construction - the exact live incident shape. Its FIRST
    run_generation_workflow() call returns a DENY_ALL-shaped
    plan_scope_conflict (allowed_files=[] - the shape Fix 2 produces for a
    grounded runtime-verification failure discovered inside a non-
    mutating context) naming App.java, owned by s2 (execution provenance,
    not depends_on - s4 depends only on s2 here, but the mechanism under
    test is the SAME effective-owner resolution MA8.1 already uses).
    Must: (1) confirm Fix 1's Pydantic self-heal already normalized s4's
    own verifier before this test ever uses it; (2) resolve owner=s2;
    (3) schedule cross-owner recovery with ALLOWLIST(App.java), not
    DENY_ALL - the repair itself must run outside the verifier context;
    (4) resume s4 afterward with DENY_ALL restored; (5) reach overall
    success."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create App", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App logic", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.MODIFY)]),
            Subtask(
                id="s4", description="run the application with sample input",
                execution_method=ExecutionMethod.MODEL, execution_role=ExecutionRole.VERIFICATION,
                depends_on=["s2"], planned_files=[],
                verification=[VerificationMethod(
                    type=VerificationMethodType.JUDGMENT,
                    description="run the application and confirm the transformed value is printed",
                    verifier_kind=VerifierKind.APPLICATION_RUNTIME,
                    # requires_runtime_execution deliberately OMITTED - Fix 1's
                    # own model_validator must normalize this to True.
                )],
            ),
        ],
    )
    # (1) Fix 1 already fired on THIS plan's own s4 before it's used below -
    # not asserted only in isolation elsewhere.
    assert plan.subtask_by_id("s4").verification[0].requires_runtime_execution is True

    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "App.java").write_text("class App {}\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 2:
            (tmp_path / "App.java").write_text("class App { /* reads stdin, not argv */ }\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        if n == 3:
            # s4's own first attempt - direct runtime verification ran
            # under DENY_ALL, discovered a real defect, and Fix 2 produced
            # a DENY_ALL-shaped scope conflict.
            assert kwargs["write_scope_mode"] == WriteScopeMode.DENY_ALL
            assert kwargs["allowed_write_relpaths"] == []
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "run_verification",
                    "reason": "app reads stdin but the runtime contract supplies argv",
                    "raw_evidence": "Error reading input: No line found",
                    "required_files": ["App.java"],
                    "allowed_files": [],
                },
            }
        if n == 4:
            # (3) s2's real owner-recovery call.
            (tmp_path / "App.java").write_text("class App { /* reads args[0] now */ }\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        # (4) s4's resumed consumer_retry - runtime verification passes now.
        assert kwargs["write_scope_mode"] == WriteScopeMode.DENY_ALL
        return {
            "status": "success", "quality_gates_passed": True, "files": [],
            "verification_results": [{
                "type": "judgment", "tool_name": None,
                "description": "run the application and confirm the transformed value is printed",
                "passed": True, "source": "run_verification",
            }],
        }

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    # (5)
    assert result.legacy_result["status"] == "success"
    assert len(calls) == 5
    assert calls[3]["allowed_write_relpaths"] == ["App.java"]
    owner_recovery_context = calls[3]["recovery_contract_block"]
    assert "MUST FIX" in owner_recovery_context
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 1
    # (2)
    assert events[0]["reopened_owner"] == "s2"
    assert events[0]["failed_subtask"] == "s4"


def test_revise_plan_for_grounded_scope_owner_normalizes_multiple_active_owners(tmp_path):
    """Test G: App.java is declared by BOTH s1 (create) and s2 (modify) - a
    legitimate sequential ownership chain. Grounded revision moving it into
    s3 must leave EXACTLY ONE active owner, never the artifact declared on
    every original owner AND the failed stage at once (the live PRV-06
    "planned file ownership must be unique" validation failure)."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create App", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.MODIFY)]),
            Subtask(id="s3", description="needs App changed again", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="Other.java", action=FileAction.CREATE)]),
        ],
    )
    (tmp_path / "App.java").write_text("class App {}")

    revised = revise_plan_for_grounded_scope_owner(plan, "s3", ["App.java"], str(tmp_path))

    owners_of_app = [
        st.id for st in revised.subtasks if any(pf.path == "App.java" for pf in st.planned_files)
    ]
    assert owners_of_app == ["s3"]
    # Must still validate cleanly - unique ownership, well-formed plan.
    EngineeringPlan.model_validate(revised.model_dump(mode="json"))


def test_revise_plan_for_grounded_scope_owner_does_not_touch_execution_history(tmp_path):
    """Test H: plan responsibility (what the REVISED plan says an
    artifact's active owner is) and execution history (ControlState's own
    record of who actually wrote it) are separate concerns -
    revise_plan_for_grounded_scope_owner operates purely on the plan and
    must never require touching, and cannot affect, a separately-held
    ControlState's own historical record."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create App", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="wire App", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="App.java", action=FileAction.MODIFY)]),
            Subtask(id="s3", description="needs App changed again", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="Other.java", action=FileAction.CREATE)]),
        ],
    )
    (tmp_path / "App.java").write_text("class App {}")
    control_state = _owner_provenance_control_state({"s1": ["App.java"], "s2": ["App.java"]})

    revise_plan_for_grounded_scope_owner(plan, "s3", ["App.java"], str(tmp_path))

    assert control_state.subtask_written_files == {"s1": ["App.java"], "s2": ["App.java"]}
    assert control_state.subtask_states == {"s1": "completed", "s2": "completed"}


@pytest.mark.asyncio
async def test_enforce_owner_recovery_fails_closed_when_no_owner_is_resolvable(tmp_path):
    """Test J: when no subtask - by execution provenance OR dependency
    ancestry - can be defensibly resolved as a required repair file's
    owner, the owner-recovery loop must fail closed rather than guess
    (PRV-06 §5 step 4 / §23 invariant 8) - proven by confirming NO owner-
    recovery call happens at all, not merely by log text."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="create App", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)]),
            Subtask(id="s2", description="needs something unresolvable", execution_method=ExecutionMethod.MODEL,
                    depends_on=["s1"],
                    planned_files=[PlannedFile(path="Other.java", action=FileAction.CREATE)]),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["App.java"]}
        return {
            "status": "failed", "quality_gates_passed": False, "files": [],
            "plan_scope_conflict": {
                "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                "failure_type": "compile",
                "required_files": ["Ghost.java"],  # nobody in the plan ever declares this
                "allowed_files": ["Other.java"],
            },
        }

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    # Exactly the two real calls happened - s1's own, and s2's single
    # failing attempt. No owner-recovery call, no arbitrary owner guess.
    assert len(calls) == 2
    assert result.legacy_result.get("plan_recovery_events", []) == []


# --- Correctness Continuity Part C (PRV-06, 2026-08-29) -
# _evaluate_integration_obligations(): the deterministic evidence source
# that transitions a plan.integration.* obligation (seeded PENDING by
# plan_validation.validate_plan()) to SATISFIED or VIOLATED. Direct unit
# tests against the helper - matches this codebase's own precedent for
# every other retry/obligation helper (resolve_effective_artifact_owner
# etc. above), no need for the full WorkflowController pipeline to prove
# this deterministic reference check's own behavior. ---

def _integration_plan(participating_artifacts=()):
    s2 = Subtask(
        id="s2", description="App", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)],
    )
    s3 = Subtask(
        id="s3", description="Service", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="InMemoryService.java", action=FileAction.CREATE)],
    )
    rel_kwargs = dict(
        id="r1", kind="uses", producer_subtask_ids=["s3"], consumer_subtask_ids=["s2"],
        relationship_statement="App.java must use InMemoryService.java",
    )
    if participating_artifacts:
        rel_kwargs["participating_artifacts"] = list(participating_artifacts)
    from kriya.workflow.plan_schema import IntegrationRelationship
    return EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK, subtasks=[s2, s3],
        integration_relationships=[IntegrationRelationship(**rel_kwargs)],
    )


def _seeded_ledger(plan):
    import asyncio
    ledger = ObligationLedger()
    asyncio.run(validate_plan(plan, workspace_path="/tmp", obligation_ledger=ledger, revision=0))
    return ledger


def test_integration_reference_token_is_the_basename_stem():
    assert _integration_reference_token("src/main/java/com/example/InMemoryService.java") == "InMemoryService"
    assert _integration_reference_token("app/repository.py") == "repository"


def test_evaluate_integration_obligations_violated_when_consumer_never_references_producer():
    """PRV-06's own real live shape: s2 (App.java) and s3
    (InMemoryService.java) each pass their own local check, but App.java
    never mentions InMemoryService anywhere - both locally complete, not
    globally integrated."""
    plan = _integration_plan()
    ledger = _seeded_ledger(plan)
    established = {
        "InMemoryService.java": "public class InMemoryService { static void store(){} }",
        "App.java": "public class App { java.util.Map m = new java.util.HashMap(); }",
    }

    _evaluate_integration_obligations(plan, ledger, "s2", established, revision=1)

    rec = ledger.current("plan.integration.r1")
    assert rec.status == ObligationStatus.VIOLATED
    assert rec.evidence["missing_producer_references"] == ["InMemoryService.java"]
    assert "plan.integration.r1" in [r.id for r in ledger.unresolved_terminal_obligations()]


def test_evaluate_integration_obligations_satisfied_when_consumer_references_producer():
    plan = _integration_plan()
    ledger = _seeded_ledger(plan)
    established = {
        "InMemoryService.java": "public class InMemoryService { static void store(){} }",
        "App.java": "public class App { InMemoryService svc; }",
    }

    _evaluate_integration_obligations(plan, ledger, "s2", established, revision=1)

    rec = ledger.current("plan.integration.r1")
    assert rec.status == ObligationStatus.SATISFIED
    assert rec.evidence["missing_producer_references"] == []
    assert ledger.unresolved_terminal_obligations() == []


def test_evaluate_integration_obligations_generic_non_java_reference():
    """Genericity (Part E4): the same reference-token mechanism, unchanged,
    for a Python producer/consumer pair - no Java/Maven assumption anywhere
    in the checked logic."""
    s_main = Subtask(
        id="s_main", description="main", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="main.py", action=FileAction.CREATE)],
    )
    s_repo = Subtask(
        id="s_repo", description="repository", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="repository.py", action=FileAction.CREATE)],
    )
    from kriya.workflow.plan_schema import IntegrationRelationship
    plan = EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK, subtasks=[s_main, s_repo],
        integration_relationships=[IntegrationRelationship(
            id="r1", kind="uses", producer_subtask_ids=["s_repo"], consumer_subtask_ids=["s_main"],
            relationship_statement="main.py must use repository.py",
        )],
    )
    ledger = _seeded_ledger(plan)

    violated = {"repository.py": "class Repository:\n    pass\n", "main.py": "print('hello')\n"}
    _evaluate_integration_obligations(plan, ledger, "s_main", violated, revision=1)
    assert ledger.current("plan.integration.r1").status == ObligationStatus.VIOLATED

    ledger2 = _seeded_ledger(plan)
    satisfied = {
        "repository.py": "class Repository:\n    pass\n",
        "main.py": "from repository import Repository\nrepo = Repository()\n",
    }
    _evaluate_integration_obligations(plan, ledger2, "s_main", satisfied, revision=1)
    assert ledger2.current("plan.integration.r1").status == ObligationStatus.SATISFIED


def test_evaluate_integration_obligations_never_reevaluates_an_already_settled_relationship():
    """Part A's own evidence-monotonicity spirit applied to Part C: once a
    relationship leaves PENDING, a later call (e.g. the owner recovering
    and re-completing) must not flip it back and forth."""
    plan = _integration_plan()
    ledger = _seeded_ledger(plan)
    established_satisfied = {
        "InMemoryService.java": "public class InMemoryService {}",
        "App.java": "public class App { InMemoryService svc; }",
    }
    _evaluate_integration_obligations(plan, ledger, "s2", established_satisfied, revision=1)
    assert ledger.current("plan.integration.r1").status == ObligationStatus.SATISFIED

    # A later re-completion of s2 with content that would now look VIOLATED
    # must NOT flip an already-SATISFIED relationship.
    established_would_now_fail = {"InMemoryService.java": "public class InMemoryService {}", "App.java": "public class App {}"}
    _evaluate_integration_obligations(plan, ledger, "s2", established_would_now_fail, revision=2)
    assert ledger.current("plan.integration.r1").status == ObligationStatus.SATISFIED


def test_evaluate_integration_obligations_noop_for_unrelated_subtask_completion():
    plan = _integration_plan()
    ledger = _seeded_ledger(plan)
    _evaluate_integration_obligations(plan, ledger, "s3", {"InMemoryService.java": "x"}, revision=1)
    # s3 is the PRODUCER, not a consumer of r1 - evaluating its own
    # completion must not touch the relationship at all.
    assert ledger.current("plan.integration.r1").status == ObligationStatus.PENDING

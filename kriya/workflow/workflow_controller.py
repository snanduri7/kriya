"""WorkflowController - MA6.8/6.9/6.10/6.13/6.14 of the MA6 structured-
execution implementation plan. The shell that will eventually own
orchestration (MA6 invariant 10). Every ChangeKind still routes the REAL
outcome to the SAME place - the existing, untouched run_generation_workflow(),
called via _run_legacy_generation() (MA6.10's own explicit first step:
"WorkflowController -> LegacyGenerationAdapter -> run_generation_workflow(),
then responsibilities extracted gradually"). Does NOT rewrite
run_generation_workflow() (MA6 hard constraint) - the legacy call is
byte-for-byte what every existing caller already makes.

MA6.9 gives the four kinds real, DISTINCT control-plane bookkeeping ahead
of that call: MILESTONE attaches milestone_group_id/current_milestone_id;
REFACTOR captures a real git base_commit/tree_hash baseline. TASK/
ENHANCEMENT get no extra bookkeeping - genuinely differentiating their
SHAPE requires MA6.10's gradual internal extraction from
run_generation_workflow(), which has not happened.

MA6.10/6.13's remaining, real scope in this slice: `migration_mode="shadow"`
builds an actual EngineeringPlan (Planner -> parse_planner_structured_output,
MA6.3) validates it (plan_validation.validate_plan, MA6.2) and, if valid,
runs EVERY subtask through the real SubtaskExecutor (MA6.5) against a
ContextPackage assembled through the real ContextOrchestrator (MA5.7,
wired in MA7.2 - see _build_context) PLUS real on-disk content of the
plan's own planned files. Still NOT the hybrid vector/graph retrieval the
legacy path's Graph RAG stage does - _build_context's own docstring
explains why that stays out of scope here. Critically: this NEVER
replaces or influences the real outcome -
_run_legacy_generation still runs unconditionally and its result is what
WorkflowResult.legacy_result reports. Why the new path can't safely own
the real outcome yet: SubtaskExecutor stops at "get file content or a
tool result" - it does not itself apply edits, run compile/test
verification, or gate on approval, all of which still live only in
run_generation_workflow()'s own Quality Gates loop. Any exception in the
shadow path is caught and logged, never allowed to fail the real run.

MA7.8 (2026-08-24): `migration_mode="enforce"` is now real, explicitly
authorized code, not refused. kriya.config.config.WorkflowControllerConfig's
own validator no longer rejects it either - lifting that restriction was
its own deliberate decision, confirmed with the user directly, mirroring
how MA7.3 handled the analogous execution_policy.mode restriction (asked
first, never silently lifted). Still defaults to `mode: shadow`/
`enabled: false` in the packaged config - "enforce" only ever runs for a
project that explicitly opts in, matching every other "ship the mechanism,
default it off" precedent in this codebase (MA4.15/MA5/MA6 alike).

What "enforce" actually does (_run_structured_enforce): deliberately does
NOT reimplement edit-application/compile-test verification/approval
gating from scratch - SubtaskExecutor still only produces file content or
a tool result, exactly as before. Instead reuses the SAME real, mature
mechanism kriya/workflow/milestones.py::run_milestones() already uses for
the analogous problem (decomposing one big goal into smaller real
generation calls): calls the EXISTING, unmodified
run_generation_workflow() once PER SUBTASK (instead of once per
milestone), in dependency order, threading each subtask's real written
output forward via the same render_established_file_context/
project_implementation_source machinery run_milestones() already uses.
This genuinely delivers per-subtask retry locality (each subtask gets its
own independent Quality-Gates retry budget) without inventing a new,
unproven apply/verify/approve mechanism. See that method's own docstring
for the honest scope boundaries this first cut still has (TOOL-tagged
subtasks refused outright, MA6.7's per-subtask checkpoint not yet wired).

MA7.1: wired into `kriya generate`'s real call path (kriya/cli.py's
`_dispatch_generation` helper, all three run_generation_workflow call
sites in the `generate` command) - still gated by
`workflow_controller.enabled: false` by default in kriya.yaml, so a stock
install's behavior is byte-for-byte unchanged regardless of mode. `kriya
fix` and `kriya plan-milestones` are NOT wired - deliberately out of
MA7.1's scope, left for a later increment.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from kriya.agents.contracts import (
    parse_planner_structured_output,
)
from kriya.context.workspace_identity import json_document_is_ownerless
from kriya.control.artifacts import ArtifactRegistry
from kriya.control.commit_state import assess_workspace_commit_state
from kriya.control.contracts import ContractRegistry, ContractRegistryCorruptError
from kriya.control.decisions import (
    Decision,
    DecisionLedger,
    stamp_legacy_decision_ledger_ownership,
)
from kriya.control.persistence import (
    UnreadableRunRecordError,
    control_state_path,
    load_artifact_registry,
    load_contract_registry,
    load_control_state,
    load_control_state_run_reference,
    load_run_record,
    save_approved_plan,
    save_artifact_registry,
    save_control_state,
)
from kriya.control.run_coordinator import (
    annotate_run,
    authorize_candidate_workspace,
    coordinated_mutation,
    current_run_context,
    mark_run_stage,
)
from kriya.control.run_record import RunLifecycle
from kriya.control.state import ControlState
from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.core.llm import InferenceDeadlineError
from kriya.policy.filesystem import WriteScopeMode
from kriya.static_analysis.service import (
    StaticAnalysisCandidate,
    StaticAnalysisService,
    commit_guard,
    static_analysis_result_fields,
)
from kriya.workflow import owner_recovery, plan_revision, planning_request, resume_support, subtask_executor
from kriya.workflow.acceptance import goal_requires_runtime_behavior
from kriya.workflow.acceptance_approval import bound_approval
from kriya.workflow.acceptance_oracle import bound_acceptance
from kriya.workflow.attribution import DETERMINISTIC_ATTRIBUTION_TIERS
from kriya.workflow.checkpoint import (
    ResumeStatus,
    compute_base_commit,
    compute_registry_hash,
    compute_tree_hash,
    compute_workspace_content_hash,
    list_checkpoints,
    new_run_id,
    save_checkpoint,
    validate_resume_against_reality,
)
from kriya.workflow.commit_service import TerminalCommitRequest, commit_verified_candidate, plan_terminal_writes
from kriya.workflow.context_budget import (
    CandidatePrompts,
    candidate_model,
    fit_reference_section,
    fit_structural_evidence,
)
from kriya.workflow.context_orchestrator import ContextOrchestrator
from kriya.workflow.context_package import (
    ContextPackage,
    artifact_entry_from_record,
    contract_entry_from_record,
    make_context_item,
)
from kriya.workflow.context_projection import project_implementation_source, render_established_file_context
from kriya.workflow.contract_baseline import NoMutationRequired, VerificationAuthorityUnavailable
from kriya.workflow.control_context import WorkflowControlContext
from kriya.workflow.deterministic_failure_diagnostic import DeterministicFailureDiagnosticStore
from kriya.workflow.edit_feasibility import (
    EditFeasibility,
    Feasibility,
    assess_planned_edit,
    collect_unit_grounding,
    infeasible_results,
    model_capacities,
    repair_error_text,
)
from kriya.workflow.edit_safety import (
    read_file_revision,
)
from kriya.workflow.execution_plan import PlanSourceKind
from kriya.workflow.graph_retrieval import localization_candidates, render_localization_candidates
from kriya.workflow.migration import (
    MigrationResolution,
    MigrationResolutionStatus,
    find_migration_incomplete,
    resolve_migration_resolution,
)
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)

# E11 re-exports (tests/architecture/test_facades.py PARTIAL_FACADES): the recovery types and the gate-failure set moved
# to owner_recovery with the owner recovery, importable from here as before. Removed in P1.
from kriya.workflow.owner_recovery import _GATE_FAILURE_TYPES as _GATE_FAILURE_TYPES
from kriya.workflow.owner_recovery import ArtifactOwnerResolutionBasis as ArtifactOwnerResolutionBasis
from kriya.workflow.owner_recovery import OwnerResolution as OwnerResolution
from kriya.workflow.ownership_findings import (
    find_ownership_findings,
    findings_prompt_block,
    grounded_owner_candidates,
    owner_candidates_prompt_block,
    record_findings,
    settle_findings,
)
from kriya.workflow.plan_executor import WorkUnitInvocation
from kriya.workflow.plan_normalization import (
    coalesce_same_file_owners,
    derive_verification_contracts,
    redirect_immutable_test_edits,
    scope_verification_to_requirements,
)

# E11 re-export (PARTIAL_FACADES): the scope-revision reason code moved to plan_revision. Removed in P1.
from kriya.workflow.plan_revision import PLAN_SCOPE_REVISION_REQUIRED as PLAN_SCOPE_REVISION_REQUIRED
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    FileOwnershipRelation,
    Subtask,
    build_engineering_plan_from_planner_output,
)
from kriya.workflow.plan_targets import check_plan_targets
from kriya.workflow.plan_validation import canonicalize_planned_file_actions, validate_plan
from kriya.workflow.planner_repair import (
    STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS,
    build_structured_plan_repair_prompt,
    classify_planner_outcome,
    classify_structured_plan_parse_issue,
    planner_response_model,
    record_planner_outcome,
)
from kriya.workflow.planning_diagnostics import (
    bounded_repository_evidence,
    persist_planning_attempt_diagnostic,
)

# E11 re-exports (PARTIAL_FACADES): the planning-request constants and patterns moved to planning_request with the
# planning request, importable from here as before. Removed in P1.
from kriya.workflow.planning_request import _JAVA_CONSTRUCTOR_INSTANTIATION_RE as _JAVA_CONSTRUCTOR_INSTANTIATION_RE
from kriya.workflow.planning_request import _JAVA_TYPE_MENTION_RE as _JAVA_TYPE_MENTION_RE
from kriya.workflow.planning_request import _PRESERVED_REFERENCE_CONFLICT_RE as _PRESERVED_REFERENCE_CONFLICT_RE
from kriya.workflow.planning_request import _QUOTED_TOKEN_RE as _QUOTED_TOKEN_RE
from kriya.workflow.planning_request import _REQUIRES_PROVIDES_ERROR_KEYWORDS as _REQUIRES_PROVIDES_ERROR_KEYWORDS
from kriya.workflow.planning_request import _SUBTASK_MENTION_RE as _SUBTASK_MENTION_RE
from kriya.workflow.planning_request import AUTHORITATIVE_PLANNER_SYSTEM_PROMPT as AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
from kriya.workflow.planning_request import BOUNDED_SUBTASK_DESIGN as BOUNDED_SUBTASK_DESIGN
from kriya.workflow.planning_request import PLANNED_FILE_SOURCE_REASON as PLANNED_FILE_SOURCE_REASON
from kriya.workflow.recovery_plan import (
    RecoveryAction,
    RecoveryExecutionPlan,
    RecoveryExecutionPlanStatus,
    RecoveryOwnerGroup,
    RecoveryParticipant,
)
from kriya.workflow.requirement_contract import bound_requirement_contract, requirement_set_for
from kriya.workflow.requirements import (
    AdmissionRefusal,
    VerificationAuthorityRequired,
    blocking_requirements,
    requirement_evidence,
    requirement_outcomes,
    requirement_verdict_details,
    requirements_prompt_block,
    seed_requirement_obligations,
)
from kriya.workflow.retry_progress import VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
from kriya.workflow.static_checks import (
    derive_stack_contract,
    log_stack_contract_boundary,
    validate_stack_contract_artifacts,
)
from kriya.workflow.subtask_checkpoint import topological_subtask_order
from kriya.workflow.subtask_context_projection import project_for_subtask
from kriya.workflow.subtask_telemetry import (
    record_context_package_for_subtask,
    record_plan_created,
    record_subtask_attempt,
    record_undeclared_file_touch,
)
from kriya.workflow.terminal_gate_service import (
    TERMINAL_GATES_NOT_RUN,
    TerminalGateRequest,
    TerminalGateService,
    TerminalGateValidators,
    _verify_original_requirements,
    enforce_preserved_reference_terminal_integrity,
)
from kriya.workflow.triage import ChangeKind
from kriya.workflow.verification_report import build_verification_report
from kriya.workflow.workflow import _log_phase_banner
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus, VerificationReport, WorkflowResult
from kriya.workflow.worktree import WorktreeSyncError, create_git_worktree, remove_git_worktree


def _evaluate_subtask_verification(
    subtask: Subtask, call_result: Dict[str, Any],
) -> Tuple[SubtaskStatus, Optional[str], Tuple[str, ...]]:
    """The body is kriya/workflow/enforce_loop.py (E14), reached through the call-time accessor _enforce_loop()."""
    return _enforce_loop()._evaluate_subtask_verification(subtask, call_result)

logger = logging.getLogger(__name__)

_VALID_MIGRATION_MODES = ("legacy", "shadow", "enforce")

# MA7.8 - mirrors kriya/workflow/milestones.py's own
# _ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE exactly (same value, a local
# constant rather than importing that module's private name across a
# module boundary - the two orchestrators are siblings, not one built on
# the other).
_ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE = 4000

# PRV-03 hardened finding (2026-08-27): a grounded owner's own repair can
# surface a FURTHER grounded owner outside the just-revised scope (e.g.
# CustomerController's fix needs Customer.java, whose own fix needs
# CustomerService.java) - a chain, not a single hop. The recovery loop
# below re-attempts revise_plan_for_grounded_scope_owner for each new
# PLAN_SCOPE_DEFECT it sees, bounded by this constant so a pathological
# oscillating diagnosis can't loop forever; once exhausted it falls
# through to the existing upstream-owner fallback / termination path.
_MAX_PLAN_SCOPE_REVISION_ATTEMPTS = 3


def _enforce_contract_transition(
    terminal_writes: List[Any], *, workspace_path: str, goal: str, plan: Any, transaction_id: str,
    subtask_call_results: List[Dict[str, Any]],
) -> Callable[..., Any]:
    """The body is kriya/workflow/enforce_loop.py (E14), reached through the call-time accessor _enforce_loop()."""
    return _enforce_loop()._enforce_contract_transition(terminal_writes, workspace_path=workspace_path, goal=goal, plan=plan, transaction_id=transaction_id, subtask_call_results=subtask_call_results)


class WorkflowControllerConfigurationError(ValueError):
    pass


class _StructuredPlanUnavailable(Exception):
    """MA7.8, fixed 2026-08-24 after a real live-validation finding: raised
    ONLY by _run_structured_enforce's pre-execution steps (Stage A parse,
    zero-subtask output, a TOOL-tagged subtask, plan validation) - every
    one of those happens BEFORE any subtask has actually run, so zero real
    side effects exist yet. execute() catches this specifically and falls
    back to the legacy whole-goal path, restoring PlannerStructuredOutput's
    own original design contract (kriya/workflow/plan_schema.py's own
    docstring: "a malformed or missing structured block must never break
    generation; every extraction failure degrades to 'no structured plan
    available,' identical to today's prose-only behavior") - a contract
    the very first version of enforce mode violated: a single malformed
    subtask in an otherwise-fine Stage A JSON block (confirmed live,
    2026-08-24, protocol_encoder_java: a subtask claimed
    execution_method=tool with no tool_name, failing pydantic validation)
    made the WHOLE run produce zero files, even though the SAME goal would
    almost certainly have succeeded via the ordinary prose-based Planner/
    Architect/Developer path. Once the per-subtask loop has actually
    started (even one real run_generation_workflow() call has happened),
    a failure is NOT this exception - it's a genuine outcome that must
    stay a clean failure result, never silently retried via a different
    execution model that could double-generate or conflict with whatever
    was already applied to the real workspace."""


# PRD-018 role bucket of shadow-mode planning (never read by PRD-019 routing).
SHADOW_PLANNER_ROLE = "planner_shadow"


class _UnsafeStructuredPlan(Exception):
    """A parsed plan violates an authoritative safety boundary."""

    def __init__(
        self,
        message: str,
        *,
        reason_codes: Optional[List[str]] = None,
        invalid_subtask_ids: Optional[List[str]] = None,
        repair_attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.reason_codes = tuple(reason_codes or ["PLAN_VALIDATION_FAILED"])
        self.invalid_subtask_ids = tuple(invalid_subtask_ids or ())
        self.repair_attempts = repair_attempts


def _revision_deps() -> plan_revision.RevisionDeps:
    """The moved plan-revision code's collaborator (kriya/workflow/plan_revision.py), resolved through this module at
    call time."""
    return plan_revision.RevisionDeps(topological_subtask_order=topological_subtask_order)


def _owner_recovery_deps() -> owner_recovery.OwnerRecoveryDeps:
    """The moved owner-recovery code's collaborators (kriya/workflow/owner_recovery.py), resolved through this module at
    call time: the recovery and integration records keep logging through this module's ``logger``."""
    return owner_recovery.OwnerRecoveryDeps(
        logger=logger, DETERMINISTIC_ATTRIBUTION_TIERS=DETERMINISTIC_ATTRIBUTION_TIERS,
        load_run_self_correction_loop=_load_run_self_correction_loop,
    )


def _load_run_self_correction_loop() -> Any:
    # E11: _attempt_owner_recovery_self_correction's function-local import of kriya.workflow.self_correction
    # (all-import cycle), done when the body reaches it, exactly as before.
    from kriya.workflow.self_correction import run_self_correction_loop
    return run_self_correction_loop


def _planning_deps() -> planning_request.PlanningDeps:
    """The moved planning-request code's collaborators (kriya/workflow/planning_request.py), resolved through this
    module NOW (at call time), so a test patch of any of these names here (``model_capacities``,
    ``assess_planned_edit``, ``collect_unit_grounding``, ``logger``, ...) reaches the moved code exactly as it reached
    the bodies when they lived here."""
    return planning_request.PlanningDeps(
        logger=logger, model_capacities=model_capacities, assess_planned_edit=assess_planned_edit,
        collect_unit_grounding=collect_unit_grounding, EditFeasibility=EditFeasibility, Feasibility=Feasibility,
        topological_subtask_order=topological_subtask_order,
    )


def _resume_deps() -> resume_support.ResumeDeps:
    """The moved resume-support code's collaborators (kriya/workflow/resume_support.py), resolved through this module
    NOW (at call time), so a test patch of ``save_artifact_registry`` / ``save_control_state`` (or any other of these
    names) here reaches the moved migrations exactly as it reached them when they lived here."""
    return resume_support.ResumeDeps(
        logger=logger, ArtifactRegistry=ArtifactRegistry, ContractRegistry=ContractRegistry, ControlState=ControlState,
        DecisionLedger=DecisionLedger, stamp_legacy_decision_ledger_ownership=stamp_legacy_decision_ledger_ownership,
        save_artifact_registry=save_artifact_registry, save_control_state=save_control_state,
        list_checkpoints=list_checkpoints, save_checkpoint=save_checkpoint,
        load_milestone_run_state_store=_load_milestone_run_state_store,
    )


def _load_milestone_run_state_store() -> Any:
    # E11: _migrate_legacy_milestone_ownership's function-local import of kriya.workflow.milestones (all-import
    # cycle), done when the body reaches it (an ownerless milestone sidecar), exactly as before.
    from kriya.workflow.milestones import MilestoneRunState, save_milestone_run_state
    return MilestoneRunState, save_milestone_run_state


def _shadow_mode() -> Any:
    """kriya/workflow/shadow_mode.py, imported when a shadow run or an enforce trace first needs it, not when this module
    is imported: its two Deps dataclasses cost ~1 ms to create (E12 performance rule; the owner is outside every import
    cycle, so this is a plain call-time import, not a cycle break)."""
    from kriya.workflow import shadow_mode
    return shadow_mode


def _enforce_loop() -> Any:
    """kriya/workflow/enforce_loop.py, imported when an enforce run first needs it, not when this module is imported:
    creating the 174-field EnforceDeps dataclass costs ~5 ms (MEASURED, E12), which every ``import kriya.cli`` paid
    (OI-61). The owner is outside every import cycle; this is a plain call-time import, not a cycle break."""
    from kriya.workflow import enforce_loop
    return enforce_loop


def _shadow_deps(controller: WorkflowController) -> Any:
    """The moved shadow run's collaborators (kriya/workflow/shadow_mode.py), resolved through this module and the
    controller instance NOW (once, when ``WorkflowController._run_structured_shadow`` is entered), so a test patch
    of any of these names here (``parse_planner_structured_output``, ``build_engineering_plan_from_planner_output``,
    ``validate_plan``, ``record_plan_created``, ...) reaches the moved body exactly as it reached it here.
    """
    return _shadow_mode().ShadowDeps(
        workflow_engine=controller.workflow_engine, _build_context=controller._build_context,
        SHADOW_PLANNER_ROLE=SHADOW_PLANNER_ROLE, logger=logger,
        parse_planner_structured_output=parse_planner_structured_output, DecisionLedger=DecisionLedger,
        subtask_executor=subtask_executor, goal_requires_runtime_behavior=goal_requires_runtime_behavior,
        ExecutionMethod=ExecutionMethod,
        build_engineering_plan_from_planner_output=build_engineering_plan_from_planner_output,
        canonicalize_planned_file_actions=canonicalize_planned_file_actions, validate_plan=validate_plan,
        classify_planner_outcome=classify_planner_outcome,
        classify_structured_plan_parse_issue=classify_structured_plan_parse_issue,
        planner_response_model=planner_response_model, record_planner_outcome=record_planner_outcome,
        derive_stack_contract=derive_stack_contract, log_stack_contract_boundary=log_stack_contract_boundary,
        topological_subtask_order=topological_subtask_order, project_for_subtask=project_for_subtask,
        record_context_package_for_subtask=record_context_package_for_subtask, record_plan_created=record_plan_created,
        record_subtask_attempt=record_subtask_attempt, record_undeclared_file_touch=record_undeclared_file_touch,
        build_verification_report=build_verification_report, SubtaskResult=SubtaskResult, SubtaskStatus=SubtaskStatus,
    )


def _trace_deps(controller: WorkflowController) -> Any:
    """The moved enforce-trace writer's collaborators (kriya/workflow/shadow_mode.py), resolved once, when
    ``WorkflowController._write_enforce_trace`` is entered: the engine, this module's ``logger``, and the trace
    writer's call-time import.
    """
    return _shadow_mode().TraceDeps(
        workflow_engine=controller.workflow_engine, logger=logger, load_write_outcome_trace=_load_write_outcome_trace,
    )


def _load_write_outcome_trace() -> Any:
    # E12: the enforce trace's function-local import of kriya.workflow.run_trace (all-import cycle), done when the
    # body reaches it, exactly as before
    from kriya.workflow.run_trace import write_outcome_trace
    return write_outcome_trace


def _enforce_deps(controller: WorkflowController) -> Any:
    """The moved enforce loop's collaborators (kriya/workflow/enforce_loop.py), resolved through this module and the
    controller instance NOW (once, when ``WorkflowController._run_structured_enforce`` is entered, never per loop
    iteration), so a test patch of any of these names here (``parse_planner_structured_output``, ``validate_plan``,
    ``create_git_worktree``, ``remove_git_worktree``, ``subtask_executor``, ``StaticAnalysisService``,
    ``enforce_preserved_reference_terminal_integrity``, ...) reaches the moved body exactly as it reached it here.
    """
    return _enforce_loop().EnforceDeps(
        workflow_engine=controller.workflow_engine, _build_context=controller._build_context,
        EstablishedProvenance=EstablishedProvenance,
        _ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE=_ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE,
        _MAX_PLAN_SCOPE_REVISION_ATTEMPTS=_MAX_PLAN_SCOPE_REVISION_ATTEMPTS,
        _StructuredPlanUnavailable=_StructuredPlanUnavailable, _UnsafeStructuredPlan=_UnsafeStructuredPlan,
        _attempt_owner_recovery_self_correction=_attempt_owner_recovery_self_correction,
        _authoritative_planner_extension_candidates=_authoritative_planner_extension_candidates,
        _build_owner_recovery_context=_build_owner_recovery_context,
        _cross_owner_obligation_id=_cross_owner_obligation_id,
        _enforce_contract_transition=_enforce_contract_transition,
        _evaluate_integration_obligations=_evaluate_integration_obligations,
        _evaluate_recovery_acceptance_precheck=_evaluate_recovery_acceptance_precheck,
        _evaluate_subtask_verification=_evaluate_subtask_verification,
        _get_or_create_cross_owner_obligation=_get_or_create_cross_owner_obligation,
        _is_strict_regression=_is_strict_regression, _plan_scope_conflict_files=_plan_scope_conflict_files,
        _preserved_reference_must_preserve_lines=_preserved_reference_must_preserve_lines,
        _preserved_reference_pairs_mentioned=_preserved_reference_pairs_mentioned,
        _preserved_reference_regressions=_preserved_reference_regressions,
        _semantic_contract_must_preserve_lines=_semantic_contract_must_preserve_lines,
        _semantic_contract_regression_subtasks=_semantic_contract_regression_subtasks,
        _subtask_ids_mentioned=_subtask_ids_mentioned, all_subtasks_completed=all_subtasks_completed,
        build_approved_plan_document=build_approved_plan_document,
        build_authoritative_planner_request=build_authoritative_planner_request,
        build_planning_structural_evidence=build_planning_structural_evidence,
        build_recovery_execution_plan=build_recovery_execution_plan,
        build_subtask_constraint_context=build_subtask_constraint_context,
        build_subtask_goal_text=build_subtask_goal_text, build_subtask_plan_text=build_subtask_plan_text,
        build_subtask_semantic_context=build_subtask_semantic_context,
        compute_abandoned_plan_files=compute_abandoned_plan_files,
        evaluate_plan_edit_feasibility=evaluate_plan_edit_feasibility,
        exclude_tool_subtasks_from_resume=exclude_tool_subtasks_from_resume,
        find_missing_grounded_production_artifacts=find_missing_grounded_production_artifacts, logger=logger,
        nearest_mutating_upstream_owner=nearest_mutating_upstream_owner,
        quarantine_abandoned_plan_files=quarantine_abandoned_plan_files,
        resolve_effective_scope_conflict_owners=resolve_effective_scope_conflict_owners,
        resolve_python_module_to_candidate_artifact_path=resolve_python_module_to_candidate_artifact_path,
        resolve_runtime_plan_gap_owner=resolve_runtime_plan_gap_owner,
        revise_plan_for_grounded_scope_owner=revise_plan_for_grounded_scope_owner,
        revise_plan_for_planned_prerequisite=revise_plan_for_planned_prerequisite,
        revise_plan_for_runtime_plan_gap=revise_plan_for_runtime_plan_gap, split_planned_source=split_planned_source,
        subtask_scope_conflict_report=subtask_scope_conflict_report,
        transitive_subtask_dependents=transitive_subtask_dependents,
        verification_failure_evidence=verification_failure_evidence,
        verification_owner_recovery_context=verification_owner_recovery_context,
        parse_planner_structured_output=parse_planner_structured_output, DecisionLedger=DecisionLedger,
        UnreadableRunRecordError=UnreadableRunRecordError, load_artifact_registry=load_artifact_registry,
        load_control_state=load_control_state, load_control_state_run_reference=load_control_state_run_reference,
        load_run_record=load_run_record, save_approved_plan=save_approved_plan,
        save_artifact_registry=save_artifact_registry, save_control_state=save_control_state,
        annotate_run=annotate_run, authorize_candidate_workspace=authorize_candidate_workspace,
        mark_run_stage=mark_run_stage, RunLifecycle=RunLifecycle, attempt_evidence_scope=attempt_evidence_scope,
        InferenceDeadlineError=InferenceDeadlineError, WriteScopeMode=WriteScopeMode,
        StaticAnalysisCandidate=StaticAnalysisCandidate, StaticAnalysisService=StaticAnalysisService,
        commit_guard=commit_guard, static_analysis_result_fields=static_analysis_result_fields,
        subtask_executor=subtask_executor, goal_requires_runtime_behavior=goal_requires_runtime_behavior,
        bound_approval=bound_approval, bound_acceptance=bound_acceptance, ResumeStatus=ResumeStatus,
        compute_base_commit=compute_base_commit, compute_tree_hash=compute_tree_hash,
        compute_workspace_content_hash=compute_workspace_content_hash,
        validate_resume_against_reality=validate_resume_against_reality, TerminalCommitRequest=TerminalCommitRequest,
        commit_verified_candidate=commit_verified_candidate, plan_terminal_writes=plan_terminal_writes,
        CandidatePrompts=CandidatePrompts, candidate_model=candidate_model,
        fit_reference_section=fit_reference_section, fit_structural_evidence=fit_structural_evidence,
        project_implementation_source=project_implementation_source,
        render_established_file_context=render_established_file_context, NoMutationRequired=NoMutationRequired,
        VerificationAuthorityUnavailable=VerificationAuthorityUnavailable,
        DeterministicFailureDiagnosticStore=DeterministicFailureDiagnosticStore, infeasible_results=infeasible_results,
        repair_error_text=repair_error_text, read_file_revision=read_file_revision, PlanSourceKind=PlanSourceKind,
        localization_candidates=localization_candidates, render_localization_candidates=render_localization_candidates,
        MigrationResolution=MigrationResolution, MigrationResolutionStatus=MigrationResolutionStatus,
        find_migration_incomplete=find_migration_incomplete, resolve_migration_resolution=resolve_migration_resolution,
        ObligationAuthority=ObligationAuthority, ObligationKind=ObligationKind, ObligationLedger=ObligationLedger,
        ObligationRecord=ObligationRecord, ObligationStatus=ObligationStatus,
        find_ownership_findings=find_ownership_findings, findings_prompt_block=findings_prompt_block,
        grounded_owner_candidates=grounded_owner_candidates,
        owner_candidates_prompt_block=owner_candidates_prompt_block, record_findings=record_findings,
        settle_findings=settle_findings, WorkUnitInvocation=WorkUnitInvocation,
        coalesce_same_file_owners=coalesce_same_file_owners,
        derive_verification_contracts=derive_verification_contracts,
        redirect_immutable_test_edits=redirect_immutable_test_edits,
        scope_verification_to_requirements=scope_verification_to_requirements,
        PLAN_SCOPE_REVISION_REQUIRED=PLAN_SCOPE_REVISION_REQUIRED, ExecutionMethod=ExecutionMethod,
        ExecutionRole=ExecutionRole, FileAction=FileAction, FileOwnershipRelation=FileOwnershipRelation,
        build_engineering_plan_from_planner_output=build_engineering_plan_from_planner_output,
        check_plan_targets=check_plan_targets, canonicalize_planned_file_actions=canonicalize_planned_file_actions,
        validate_plan=validate_plan, STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS=STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS,
        build_structured_plan_repair_prompt=build_structured_plan_repair_prompt,
        classify_planner_outcome=classify_planner_outcome,
        classify_structured_plan_parse_issue=classify_structured_plan_parse_issue,
        planner_response_model=planner_response_model, record_planner_outcome=record_planner_outcome,
        bounded_repository_evidence=bounded_repository_evidence,
        persist_planning_attempt_diagnostic=persist_planning_attempt_diagnostic,
        AUTHORITATIVE_PLANNER_SYSTEM_PROMPT=AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
        BOUNDED_SUBTASK_DESIGN=BOUNDED_SUBTASK_DESIGN, RecoveryAction=RecoveryAction,
        RecoveryExecutionPlanStatus=RecoveryExecutionPlanStatus, bound_requirement_contract=bound_requirement_contract,
        requirement_set_for=requirement_set_for, VerificationAuthorityRequired=VerificationAuthorityRequired,
        blocking_requirements=blocking_requirements, requirement_evidence=requirement_evidence,
        requirement_outcomes=requirement_outcomes, requirement_verdict_details=requirement_verdict_details,
        requirements_prompt_block=requirements_prompt_block, seed_requirement_obligations=seed_requirement_obligations,
        VERIFICATION_RETRY_NO_CHANGE_POSSIBLE=VERIFICATION_RETRY_NO_CHANGE_POSSIBLE,
        derive_stack_contract=derive_stack_contract, log_stack_contract_boundary=log_stack_contract_boundary,
        validate_stack_contract_artifacts=validate_stack_contract_artifacts,
        topological_subtask_order=topological_subtask_order, project_for_subtask=project_for_subtask,
        record_plan_created=record_plan_created, record_subtask_attempt=record_subtask_attempt,
        TERMINAL_GATES_NOT_RUN=TERMINAL_GATES_NOT_RUN, TerminalGateRequest=TerminalGateRequest,
        TerminalGateService=TerminalGateService, TerminalGateValidators=TerminalGateValidators,
        _verify_original_requirements=_verify_original_requirements,
        enforce_preserved_reference_terminal_integrity=enforce_preserved_reference_terminal_integrity,
        build_verification_report=build_verification_report, _log_phase_banner=_log_phase_banner,
        SubtaskResult=SubtaskResult, SubtaskStatus=SubtaskStatus, WorktreeSyncError=WorktreeSyncError,
        create_git_worktree=create_git_worktree, remove_git_worktree=remove_git_worktree,
        load_contract_planning_constraints=_load_contract_planning_constraints, load_goal_facts=_load_goal_facts,
        load_run_contract=_load_run_contract, load_run_contract_baseline=_load_run_contract_baseline,
    )


def _load_contract_planning_constraints() -> Any:
    # E12: the enforce loop's function-local import of kriya.workflow.workflow (the facade), done when the body
    # reaches it, exactly as before
    from kriya.workflow.workflow import documentation_referent_paths, frozen_file_paths, immutable_test_files
    return documentation_referent_paths, frozen_file_paths, immutable_test_files


def _load_goal_facts() -> Any:
    # E12: the enforce loop's function-local import of kriya.workflow.goal_facts (all-import cycle), done when the
    # body reaches it, exactly as before
    from kriya.workflow.goal_facts import delegating_shim_targets, goal_facts_block
    return delegating_shim_targets, goal_facts_block


def _load_run_contract() -> Any:
    # E12: the enforce loop's function-local import of kriya.workflow.workflow (the facade: an owner module never
    # imports it), done when the body reaches it, exactly as before
    from kriya.workflow.workflow import _requirement_policy_blocks, compile_run_contract, seal_run_contract
    return _requirement_policy_blocks, compile_run_contract, seal_run_contract


def _load_run_contract_baseline() -> Any:
    # E12: the enforce loop's function-local import of kriya.workflow.workflow (the facade), done when the body
    # reaches it (a contract with authorities), exactly as before
    from kriya.workflow.workflow import run_contract_baseline
    return run_contract_baseline


def build_subtask_goal_text(
    subtask: Subtask,
    position: int,
    total: int,
    *,
    plan: Optional[EngineeringPlan] = None,
    grounding_goal: str = "",
) -> str:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request.build_subtask_goal_text(subtask, position, total, plan=plan, grounding_goal=grounding_goal)


async def evaluate_plan_edit_feasibility(
    config: Optional[Any], plan: EngineeringPlan, goal: str, workspace_path: str,
) -> List[EditFeasibility]:
    """The body is kriya/workflow/planning_request.py (E11); its collaborators are resolved here, at call time (_planning_deps)."""
    return await planning_request.evaluate_plan_edit_feasibility(_planning_deps(), config, plan, goal, workspace_path)


def split_planned_source(package: Any) -> Tuple[List[str], Any]:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request.split_planned_source(package)


def build_subtask_constraint_context(original_goal: str) -> str:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request.build_subtask_constraint_context(original_goal)


def build_subtask_semantic_context(plan: EngineeringPlan, subtask: Subtask) -> str:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request.build_subtask_semantic_context(plan, subtask)


def build_subtask_plan_text(subtask: Subtask) -> str:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request.build_subtask_plan_text(subtask)


def _authoritative_planner_extension_candidates(
    workspace_path: str, max_files: int = 100, *, goal: str = "",
) -> List[str]:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request._authoritative_planner_extension_candidates(workspace_path, max_files, goal=goal)


def _is_strict_regression(
    retained_reason_codes: Optional[frozenset], candidate_reason_codes: frozenset,
) -> bool:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request._is_strict_regression(retained_reason_codes, candidate_reason_codes)


def _preserved_reference_must_preserve_lines(obligation_ledger: ObligationLedger) -> List[str]:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request._preserved_reference_must_preserve_lines(obligation_ledger)


def _semantic_contract_must_preserve_lines(obligation_ledger: ObligationLedger) -> List[str]:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request._semantic_contract_must_preserve_lines(obligation_ledger)


def _subtask_ids_mentioned(texts: List[str]) -> Set[str]:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request._subtask_ids_mentioned(texts)


def _semantic_contract_regression_subtasks(
    obligation_ledger: ObligationLedger, regressions_before: int, implicated_subtask_ids: Set[str],
) -> List[str]:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request._semantic_contract_regression_subtasks(
        obligation_ledger, regressions_before, implicated_subtask_ids,
    )


def _preserved_reference_pairs_mentioned(texts: List[str]) -> Set[Tuple[str, str]]:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request._preserved_reference_pairs_mentioned(texts)


def _preserved_reference_regressions(
    obligation_ledger: ObligationLedger, regressions_before: int,
    implicated_pairs: Set[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request._preserved_reference_regressions(obligation_ledger, regressions_before, implicated_pairs)


# build_structured_plan_repair_prompt moved to kriya/workflow/planner_repair.py
# (PLANNER-ROBUST-001, 2026-09-19) - a shared Planner-boundary primitive both this
# module and the legacy kriya/workflow/workflow.py path now import, so the two
# runtime paths can never independently drift on structured-plan repair guidance.
# See that module's own docstring for the extraction rationale and import-direction
# note (workflow_controller.py already imports FROM workflow.py, so the reverse
# import would have been circular).


def build_authoritative_planner_request(
    goal: str,
    *,
    route_kind: Optional[ChangeKind] = None,
    extension_candidates: Optional[List[str]] = None,
    repository_candidates: Optional[List[str]] = None,
    structural_evidence: str = "",
) -> str:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request.build_authoritative_planner_request(
        goal, route_kind=route_kind, extension_candidates=extension_candidates,
        repository_candidates=repository_candidates, structural_evidence=structural_evidence,
    )


def build_planning_structural_evidence(
    workspace_path: str, candidate_paths: List[str],
) -> Tuple[str, Dict[str, List[str]]]:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request.build_planning_structural_evidence(workspace_path, candidate_paths)


def _transitive_depends_on(plan: EngineeringPlan, subtask_id: str) -> set:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request._transitive_depends_on(plan, subtask_id)


def find_missing_grounded_production_artifacts(
    plan: EngineeringPlan, resolved_edges: Dict[str, List[str]],
    *,
    workspace_path: Optional[str] = None,
    obligation_ledger: Optional[ObligationLedger] = None,
    revision: object = None,
) -> List[Dict[str, str]]:
    """The body is kriya/workflow/planning_request.py (E11)."""
    return planning_request.find_missing_grounded_production_artifacts(
        plan, resolved_edges, workspace_path=workspace_path, obligation_ledger=obligation_ledger, revision=revision,
    )


def build_approved_plan_document(
    plan: EngineeringPlan,
    *,
    plan_hash: str,
    repair_attempts: int,
    stage_states: Dict[str, str],
    lifecycle_state: str,
) -> Dict[str, Any]:
    """The body is kriya/workflow/planning_request.py (E11); its collaborators are resolved here, at call time (_planning_deps)."""
    return planning_request.build_approved_plan_document(
        _planning_deps(), plan, plan_hash=plan_hash, repair_attempts=repair_attempts, stage_states=stage_states,
        lifecycle_state=lifecycle_state,
    )


def transitive_subtask_dependents(plan: EngineeringPlan, subtask_id: str) -> List[str]:
    """The body is kriya/workflow/plan_revision.py (E11); its collaborators are resolved here, at call time (_revision_deps)."""
    return plan_revision.transitive_subtask_dependents(_revision_deps(), plan, subtask_id)


def resolve_scope_conflict_owners(
    plan: EngineeringPlan, required_files: List[str], failed_subtask: Subtask,
) -> Dict[str, List[str]]:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery.resolve_scope_conflict_owners(plan, required_files, failed_subtask)


def nearest_mutating_upstream_owner(
    plan: EngineeringPlan, subtask: Subtask, completed_ids: Sequence[str],
) -> Optional[Subtask]:
    """The body is kriya/workflow/plan_revision.py (E11)."""
    return plan_revision.nearest_mutating_upstream_owner(plan, subtask, completed_ids)


def verification_failure_evidence(call_result: Mapping[str, Any]) -> str:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery.verification_failure_evidence(call_result)


def verification_owner_recovery_context(
    *, verification_subtask_id: str, owner_id: str, owner_files: Sequence[str], evidence: str,
) -> str:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery.verification_owner_recovery_context(
        verification_subtask_id=verification_subtask_id, owner_id=owner_id, owner_files=owner_files, evidence=evidence,
    )


def _successful_modifiers_in_execution_order(
    path: str, order: List[str], control_state: ControlState,
) -> List[str]:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery._successful_modifiers_in_execution_order(path, order, control_state)


def resolve_effective_artifact_owner(
    plan: EngineeringPlan,
    path: str,
    failed_subtask: Subtask,
    order: Optional[List[str]] = None,
    control_state: Optional[ControlState] = None,
) -> OwnerResolution:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery.resolve_effective_artifact_owner(plan, path, failed_subtask, order, control_state)


def resolve_effective_scope_conflict_owners(
    plan: EngineeringPlan,
    required_files: List[str],
    failed_subtask: Subtask,
    order: Optional[List[str]] = None,
    control_state: Optional[ControlState] = None,
) -> Dict[str, List[str]]:
    """The body is kriya/workflow/owner_recovery.py (E11); its collaborators are resolved here, at call time (_owner_recovery_deps)."""
    return owner_recovery.resolve_effective_scope_conflict_owners(
        _owner_recovery_deps(), plan, required_files, failed_subtask, order, control_state,
    )


def _cross_owner_obligation_id(
    originating_subtask_id: str, owner_subtask_id: str, required_files: List[str],
    failure_family: str, generation: int,
) -> str:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery._cross_owner_obligation_id(
        originating_subtask_id, owner_subtask_id, required_files, failure_family, generation,
    )


def _prior_cross_owner_musts(
    ledger: Optional[ObligationLedger], *, owner_subtask_id: str, required_files: List[str],
) -> List[str]:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery._prior_cross_owner_musts(
        ledger, owner_subtask_id=owner_subtask_id, required_files=required_files,
    )


def _get_or_create_cross_owner_obligation(
    ledger: Optional[ObligationLedger],
    *,
    originating_subtask_id: str,
    owner_subtask_id: str,
    required_files: List[str],
    scope_conflict: Dict[str, Any],
    generation: int,
    revision: Any,
) -> Optional[ObligationRecord]:
    """The body is kriya/workflow/owner_recovery.py (E11); its collaborators are resolved here, at call time (_owner_recovery_deps)."""
    return owner_recovery._get_or_create_cross_owner_obligation(
        _owner_recovery_deps(), ledger, originating_subtask_id=originating_subtask_id,
        owner_subtask_id=owner_subtask_id, required_files=required_files, scope_conflict=scope_conflict,
        generation=generation, revision=revision,
    )


def _build_owner_recovery_context(
    *,
    owner_id: str,
    failed_subtask_id: str,
    required_owner_files: List[str],
    cross_owner_obligation: Optional[ObligationRecord],
    scope_conflict: Dict[str, Any],
) -> str:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery._build_owner_recovery_context(
        owner_id=owner_id, failed_subtask_id=failed_subtask_id, required_owner_files=required_owner_files,
        cross_owner_obligation=cross_owner_obligation, scope_conflict=scope_conflict,
    )


def _evaluate_recovery_acceptance_precheck(
    *,
    scope_conflict: Dict[str, Any],
    plan_workspace_path: str,
    required_owner_files: List[str],
) -> Optional[bool]:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery._evaluate_recovery_acceptance_precheck(
        scope_conflict=scope_conflict, plan_workspace_path=plan_workspace_path,
        required_owner_files=required_owner_files,
    )


def _participant_evidence_is_grounded(scope_conflict: Dict[str, Any]) -> bool:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery._participant_evidence_is_grounded(scope_conflict)


def _scope_conflict_evidence_authority(scope_conflict: Dict[str, Any]) -> ObligationAuthority:
    """The body is kriya/workflow/owner_recovery.py (E11); its collaborators are resolved here, at call time (_owner_recovery_deps)."""
    return owner_recovery._scope_conflict_evidence_authority(_owner_recovery_deps(), scope_conflict)


def derive_recovery_participants(
    plan: EngineeringPlan,
    scope_conflict: Dict[str, Any],
    failed_subtask: Subtask,
    order: Optional[List[str]] = None,
    control_state: Optional[ControlState] = None,
    recovery_generation_by_key: Optional[Dict[Tuple[str, str, Tuple[str, ...]], int]] = None,
) -> Tuple[RecoveryParticipant, ...]:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery.derive_recovery_participants(
        plan, scope_conflict, failed_subtask, order, control_state, recovery_generation_by_key,
    )


def _order_recovery_groups(
    plan: EngineeringPlan, participants: Tuple[RecoveryParticipant, ...],
) -> Optional[Tuple[RecoveryOwnerGroup, ...]]:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery._order_recovery_groups(plan, participants)


def build_recovery_execution_plan(
    plan: EngineeringPlan,
    scope_conflict: Dict[str, Any],
    failed_subtask: Subtask,
    *,
    order: Optional[List[str]] = None,
    control_state: Optional[ControlState] = None,
    plan_generation: int = 0,
    recovery_generation_by_key: Optional[Dict[Tuple[str, str, Tuple[str, ...]], int]] = None,
) -> Optional[RecoveryExecutionPlan]:
    """The body is kriya/workflow/owner_recovery.py (E11); its collaborators are resolved here, at call time (_owner_recovery_deps)."""
    return owner_recovery.build_recovery_execution_plan(
        _owner_recovery_deps(), plan, scope_conflict, failed_subtask, order=order, control_state=control_state,
        plan_generation=plan_generation, recovery_generation_by_key=recovery_generation_by_key,
    )


async def _attempt_owner_recovery_self_correction(
    *,
    kernel: Any,
    developer_llm: Any,
    plan_workspace_path: str,
    workspace_path: str,
    owner: Subtask,
    required_owner_files: List[str],
    scope_conflict: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """The body is kriya/workflow/owner_recovery.py (E11); its collaborators are resolved here, at call time (_owner_recovery_deps)."""
    return await owner_recovery._attempt_owner_recovery_self_correction(
        _owner_recovery_deps(), kernel=kernel, developer_llm=developer_llm, plan_workspace_path=plan_workspace_path,
        workspace_path=workspace_path, owner=owner, required_owner_files=required_owner_files,
        scope_conflict=scope_conflict,
    )


def _integration_reference_token(path: str) -> str:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery._integration_reference_token(path)


class EstablishedProvenance:
    """LR-R1-P5: which subtask established each artifact in this enforce run,
    at which raw bytes, in which (plan) workspace - recorded at the same
    sites that fill established_file_context (subtask completion, resume,
    owner recovery). The integration check reads it to judge a relationship
    on the artifacts that can legitimately satisfy it: provider artifacts
    established by a declared producer and still those bytes, and the
    consumer's CURRENT planned artifacts (written this run or unchanged)."""

    def __init__(self, workspace_path: str) -> None:
        self.workspace_path = workspace_path
        self.writer: Dict[str, str] = {}
        self.digest: Dict[str, Optional[str]] = {}

    def _raw_digest(self, path: str) -> Optional[str]:
        try:
            with open(os.path.join(self.workspace_path, path), "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()
        except OSError:
            return None

    def record(self, path: str, subtask_id: str) -> None:
        self.writer[path] = subtask_id
        self.digest[path] = self._raw_digest(path)

    def provider_state(self, path: str, producers: Iterable[str]) -> Tuple[str, Optional[str]]:
        """(state, establishing subtask) for a required provider artifact:
        "valid" (established by a declared producer and unchanged since),
        "not_established", "established_by_non_provider" or "invalidated"
        (removed or changed after it was established)."""
        writer = self.writer.get(path)
        if writer is None:
            return "not_established", None
        if writer not in set(producers):
            return "established_by_non_provider", writer
        current = self._raw_digest(path)
        if current is None or current != self.digest.get(path):
            return "invalidated", writer
        return "valid", writer

    def current_content(self, path: str) -> Optional[str]:
        """The artifact's current content in the workspace, projected exactly
        as established content is; None when it does not exist."""
        try:
            with open(os.path.join(self.workspace_path, path), "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except OSError:
            return None
        return project_implementation_source(
            content, path, _ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE,
            reason="established_by_earlier_subtask",
        ).content


def _integration_evidence(
    plan: EngineeringPlan, rel: Any, consumer_id: str, producer_paths: List[str], consumer_paths: List[str],
    established_file_context: Dict[str, str], provenance: EstablishedProvenance,
) -> Tuple[List[str], Dict[str, Any]]:
    """The body is kriya/workflow/owner_recovery.py (E11)."""
    return owner_recovery._integration_evidence(
        plan, rel, consumer_id, producer_paths, consumer_paths, established_file_context, provenance,
    )


def _evaluate_integration_obligations(
    plan: EngineeringPlan,
    obligation_ledger: Optional[ObligationLedger],
    completed_subtask_id: str,
    established_file_context: Dict[str, str],
    revision: Any,
    provenance: Optional[EstablishedProvenance] = None,
) -> None:
    """The body is kriya/workflow/owner_recovery.py (E11); its collaborators are resolved here, at call time (_owner_recovery_deps)."""
    return owner_recovery._evaluate_integration_obligations(
        _owner_recovery_deps(), plan, obligation_ledger, completed_subtask_id, established_file_context, revision,
        provenance,
    )


def _transitive_dependents(plan: EngineeringPlan, subtask_id: str) -> set[str]:
    """The body is kriya/workflow/plan_revision.py (E11)."""
    return plan_revision._transitive_dependents(plan, subtask_id)


def _transitive_upstream_ids(plan: EngineeringPlan, subtask_id: str) -> set[str]:
    """The body is kriya/workflow/plan_revision.py (E11)."""
    return plan_revision._transitive_upstream_ids(plan, subtask_id)


def subtask_scope_conflict_report(scope_conflict: Dict[str, Any]) -> Tuple[str, Tuple[str, ...]]:
    """The body is kriya/workflow/plan_revision.py (E11)."""
    return plan_revision.subtask_scope_conflict_report(scope_conflict)


def _plan_scope_conflict_files(scope_conflict: Optional[Dict[str, object]]) -> List[str]:
    """The body is kriya/workflow/plan_revision.py (E11)."""
    return plan_revision._plan_scope_conflict_files(scope_conflict)


def revise_plan_for_grounded_scope_owner(
    plan: EngineeringPlan,
    failed_subtask_id: str,
    grounded_files: List[str],
    workspace_path: str,
) -> EngineeringPlan:
    """The body is kriya/workflow/plan_revision.py (E11)."""
    return plan_revision.revise_plan_for_grounded_scope_owner(plan, failed_subtask_id, grounded_files, workspace_path)


def revise_plan_for_planned_prerequisite(
    plan: EngineeringPlan,
    *,
    consumer_subtask_id: str,
    owner_subtask_id: str,
    capability: str,
    consumer_artifacts: Iterable[str] = (),
) -> EngineeringPlan:
    """The body is kriya/workflow/plan_revision.py (E11)."""
    return plan_revision.revise_plan_for_planned_prerequisite(
        plan, consumer_subtask_id=consumer_subtask_id, owner_subtask_id=owner_subtask_id, capability=capability,
        consumer_artifacts=consumer_artifacts,
    )


def resolve_python_module_to_candidate_artifact_path(
    module_name: str, workspace_path: str,
) -> Optional[str]:
    """The body is kriya/workflow/plan_revision.py (E11)."""
    return plan_revision.resolve_python_module_to_candidate_artifact_path(module_name, workspace_path)


def resolve_runtime_plan_gap_owner(
    plan: EngineeringPlan, candidate_path: str, failed_subtask: Subtask,
) -> Optional[str]:
    """The body is kriya/workflow/plan_revision.py (E11)."""
    return plan_revision.resolve_runtime_plan_gap_owner(plan, candidate_path, failed_subtask)


def revise_plan_for_runtime_plan_gap(
    plan: EngineeringPlan, *, owner_subtask_id: str, artifact_path: str, reason: str,
) -> EngineeringPlan:
    """The body is kriya/workflow/plan_revision.py (E11)."""
    return plan_revision.revise_plan_for_runtime_plan_gap(
        plan, owner_subtask_id=owner_subtask_id, artifact_path=artifact_path, reason=reason,
    )


def exclude_tool_subtasks_from_resume(
    plan: EngineeringPlan, resumed_subtask_states: Dict[str, str],
) -> Tuple[Dict[str, str], List[str]]:
    """The body is kriya/workflow/resume_support.py (E11)."""
    return resume_support.exclude_tool_subtasks_from_resume(plan, resumed_subtask_states)


def all_subtasks_completed(
    current_plan_subtask_ids: Set[str], latest_status_by_subtask: Dict[str, SubtaskStatus],
) -> bool:
    """The body is kriya/workflow/resume_support.py (E11)."""
    return resume_support.all_subtasks_completed(current_plan_subtask_ids, latest_status_by_subtask)


def compute_abandoned_plan_files(
    prior_subtask_states: Dict[str, str],
    prior_subtask_written_files: Dict[str, List[str]],
    new_plan_files: Any,
) -> List[str]:
    """The body is kriya/workflow/resume_support.py (E11)."""
    return resume_support.compute_abandoned_plan_files(
        prior_subtask_states, prior_subtask_written_files, new_plan_files,
    )


def quarantine_abandoned_plan_files(
    workspace_path: str, abandoned_files: List[str], quarantine_subdir: str,
) -> List[str]:
    """The body is kriya/workflow/resume_support.py (E11); its collaborators are resolved here, at call time (_resume_deps)."""
    return resume_support.quarantine_abandoned_plan_files(
        _resume_deps(), workspace_path, abandoned_files, quarantine_subdir,
    )


def _migrate_legacy_milestone_ownership(
    workspace_path: str,
    run_state: Any,
    contract_registry: Any,
    artifact_registry: Any,
    *,
    prior_control_state_ownerless: bool,
) -> Tuple[str, ...]:
    """The body is kriya/workflow/resume_support.py (E11); its collaborators are resolved here, at call time (_resume_deps)."""
    return resume_support._migrate_legacy_milestone_ownership(
        _resume_deps(), workspace_path, run_state, contract_registry, artifact_registry,
        prior_control_state_ownerless=prior_control_state_ownerless,
    )


def _migrate_legacy_controller_ownership(workspace_path: str, run_id: str) -> Tuple[str, ...]:
    """The body is kriya/workflow/resume_support.py (E11); its collaborators are resolved here, at call time (_resume_deps)."""
    return resume_support._migrate_legacy_controller_ownership(_resume_deps(), workspace_path, run_id)


class WorkflowController:
    """Wraps an existing WorkflowEngine (kriya/workflow/workflow.py) -
    never constructs its own agents/LLM client, and deliberately reuses
    that engine's OWN `engineering_triage`/`planner`/`developer`/`kernel`
    rather than building second copies, so triage state/kernel wiring
    stays exactly what a real caller already set up."""

    def __init__(self, workflow_engine: Any) -> None:
        self.workflow_engine = workflow_engine

    def workspace_refusal_result(self, assessment: Any, arguments: Dict[str, Any]) -> WorkflowResult:
        """PRD-008: a fresh run refused by the RunCoordinator's commit-state
        gate, before triage/planning, so there is no route or control state."""
        run_id = arguments.get("run_id") or new_run_id()
        return WorkflowResult(
            run_id=run_id, control_state=None, route=None,
            legacy_result=assessment.to_payload(run_id=run_id),
        )

    @coordinated_mutation
    async def execute(
        self,
        goal: str,
        workspace_path: str,
        *,
        known_files: Optional[List[str]] = None,
        run_id: Optional[str] = None,
        milestone_group_id: Optional[str] = None,
        milestone_index: Optional[int] = None,
        milestone_total: Optional[int] = None,
        migration_mode: str = "legacy",
        **legacy_kwargs: Any,
    ) -> WorkflowResult:
        if migration_mode not in _VALID_MIGRATION_MODES:
            raise WorkflowControllerConfigurationError(
                f"migration_mode must be one of {_VALID_MIGRATION_MODES!r}, got {migration_mode!r}."
            )

        active_run = current_run_context()
        run_id = (
            run_id or legacy_kwargs.get("trace_id_override")
            or (active_run.run_id if active_run is not None else None)
            or new_run_id()
        )

        _log_phase_banner("REQUEST ANALYSIS")
        route = await self.workflow_engine.engineering_triage.classify(
            goal, workspace_path, known_files=known_files,
        )
        control_context = WorkflowControlContext.for_route(route)
        control_state = ControlState.new(
            run_id=run_id,
            engineering_route=route,
            process_profile=control_context.process_profile,
            milestone_group_id=milestone_group_id,
        )
        logger.info(
            "Run route: kind=%s risk=%s weight=%s",
            route.kind.value.upper(), route.max_observed_risk_class.name,
            route.execution_weight.value.upper(),
        )

        if route.kind == ChangeKind.MILESTONE:
            control_state = self._attach_milestone_metadata(
                control_state, milestone_group_id, milestone_index,
            )
        elif route.kind == ChangeKind.REFACTOR:
            control_state = self._attach_refactor_baseline(control_state, workspace_path)
        # TASK/ENHANCEMENT: no extra bookkeeping in this slice - see this
        # module's own docstring for why that's a deliberate scoping
        # decision, not an oversight.

        subtask_results: Tuple[SubtaskResult, ...] = ()
        decisions: Tuple[Decision, ...] = ()
        verification_report: Optional[VerificationReport] = None

        if migration_mode == "enforce":
            # PRD-005/007/008: never start or resume model work while the
            # workspace may hold a partial commit. A fresh run is already
            # refused by the RunCoordinator before its record exists; this
            # re-check (same assessment, excluding this run's own record)
            # covers a controller entered inside an already-owned run.
            active_run_id = active_run.run_id if active_run is not None else None
            commit_assessment = assess_workspace_commit_state(
                workspace_path, exclude_run_ids=(run_id, active_run_id),
            )
            if not commit_assessment.safe:
                return WorkflowResult(
                    run_id=run_id, control_state=control_state, route=route,
                    legacy_result=commit_assessment.to_payload(run_id=run_id),
                )
            kernel = getattr(self.workflow_engine, "kernel", None)
            if kernel is not None:
                _log_phase_banner("KNOWLEDGE GUARD")
                from kriya.tools.knowledge import KnowledgeGuard
                knowledge_config = kernel.config.knowledge
                cutoff = kernel.config.llm.knowledge_cutoff
                if knowledge_config.training_cutoff != "2023-12-01":
                    cutoff = knowledge_config.training_cutoff
                guard = KnowledgeGuard(
                    skills_dir=kernel.config.paths.skills,
                    cutoff_date_str=cutoff,
                    offline=knowledge_config.offline_mode,
                    memory_dir=kernel.config.paths.memory,
                    cache_ttl_days=knowledge_config.release_cache_ttl_days,
                )
                goal_gap_report = guard.check_goal(goal, workspace_path)
                resolved_coordinates = [gap["library"] for gap in goal_gap_report.gaps]
                if goal_gap_report.has_gaps and not legacy_kwargs.get("knowledge_risk_confirmed", False):
                    logger.warning(
                        "KnowledgeGuard requires resolution before structured planning: %s",
                        resolved_coordinates,
                    )
                    return WorkflowResult(
                        run_id=run_id, control_state=control_state, route=route,
                        legacy_result={
                            "status": "knowledge_gap",
                            "gap_report": goal_gap_report.to_dict(),
                            "goal": goal,
                            "workspace_path": workspace_path,
                            "run_id": run_id,
                        },
                    )
                logger.info(
                    "Knowledge resolution: %s",
                    "cleared" if not resolved_coordinates else f"acknowledged {resolved_coordinates}",
                )
                legacy_kwargs["resolved_knowledge_coordinates"] = resolved_coordinates

                # One registry load for the authoritative run. Individual
                # subtasks project relevant skills from this shared engine;
                # genuine skill writes still trigger their existing reloads.
                from kriya.skills.skill import SkillEngine
                shared_skill_engine = SkillEngine.from_config(
                    kernel.config, workspace_path=workspace_path,
                )
                shared_skill_engine.discover_and_load()
                legacy_kwargs["skill_engine_override"] = shared_skill_engine
            try:
                _migrate_legacy_controller_ownership(workspace_path, run_id)
            except Exception as exc:
                legacy_result = {
                    "status": "needs_review",
                    "quality_gates_passed": False,
                    "files": [],
                    "reason_codes": ["LEGACY_STATE_MIGRATION_FAILED"],
                    "error": str(exc),
                    "run_id": run_id,
                }
                return WorkflowResult(
                    run_id=run_id,
                    control_state=control_state,
                    route=route,
                    legacy_result=legacy_result,
                )
            # MA7.8 - deliberately NOT wrapped in the shadow path's own
            # broad try/except. Shadow must never take down the real run
            # because shadow ISN'T the real run - enforce mode IS, so a bug
            # here has to surface loudly (this codebase's own "fail loud,
            # never silently do something unsafe" convention), not be
            # mistaken for a different outcome. _run_structured_enforce
            # itself already degrades a genuine SUBTASK-LOOP failure into a
            # clean, normally-shaped failure result (same status/
            # quality_gates_passed shape run_generation_workflow's own
            # ordinary failures use) rather than raising for those - only a
            # real bug in this orchestration code itself propagates as an
            # uncaught exception.
            #
            # _StructuredPlanUnavailable remains a distinct pre-execution
            # outcome, but authoritative mode must never discard its safety
            # model by degrading to whole-goal legacy execution. Bounded
            # repair handles correctable structure; true unavailability is
            # surfaced for review below.
            plan: Optional[EngineeringPlan] = None
            enforce_started = time.time()
            try:
                legacy_result, plan, subtask_results, decisions, verification_report, control_state = (
                    await self._run_structured_enforce(
                        goal, workspace_path, route, run_id, control_context, control_state, legacy_kwargs,
                    )
                )
            except _UnsafeStructuredPlan as e:
                logger.error(f"WorkflowController enforce run {run_id!r}: unsafe structured plan: {e}")
                legacy_result = {
                    "status": "needs_review",
                    "quality_gates_passed": False,
                    "files": [],
                    "failure_type": "PLANNING_ERROR",
                    "recovery": "PLAN_REPAIR",
                    "reason_codes": list(e.reason_codes),
                    "invalid_subtask_ids": list(e.invalid_subtask_ids),
                    "plan_repair_attempts": e.repair_attempts,
                    "error": str(e),
                    "run_id": run_id,
                }
            except VerificationAuthorityUnavailable as stop:
                # VERIFICATION-CONTRACT-003 (D2): a bound oracle cannot execute under containment here -
                # typed stop before planning, nothing generated.
                logger.error(f"WorkflowController enforce run {run_id!r}: {stop.message}")
                legacy_result = {**stop.result(), "failure_type": "VERIFICATION_AUTHORITY_UNAVAILABLE", "run_id": run_id}
            except NoMutationRequired as decision:
                # VERIFICATION-CONTRACT-003: the baseline already satisfies every mandatory claim
                # under the sealed authorities - a success without a model call.
                logger.info(f"WorkflowController enforce run {run_id!r}: {decision}")
                legacy_result = {**decision.result(), "run_id": run_id}
            except AdmissionRefusal as e:
                # VERIFICATION-CONTRACT-003 (D3): GoalAdmissionError or VerificationAuthorityRequired,
                # both before any retrieval, planning or model call.
                logger.error(f"WorkflowController enforce run {run_id!r}: goal refused before any model call: {e}")
                legacy_result = {
                    "status": "failure",
                    "quality_gates_passed": False,
                    "files": [],
                    "failure_type": ("VERIFICATION_AUTHORITY" if isinstance(e, VerificationAuthorityRequired)
                                     else "GOAL_ADMISSION"),
                    "failure_category": e.failure_category,
                    "reason_codes": [e.reason_code],
                    "error": str(e),
                    "environment_failure": e.message,
                    "requirements_admission": e.to_dict(),
                    "authority_requests": [r.to_dict() for r in getattr(e, "authority_requests", ())],
                    "authority_requests_path": getattr(e, "authority_requests_path", None),
                    "run_id": run_id,
                    **({"verification_contract": e.report} if e.report is not None else {}),
                }
            except WorktreeSyncError as e:
                logger.error(f"WorkflowController enforce run {run_id!r}: worktree sync refused: {e}")
                legacy_result = {
                    "status": "failure",
                    "quality_gates_passed": False,
                    "files": [],
                    "failure_type": "WORKTREE_SYNC",
                    "failure_category": e.reason_code.lower(),
                    "reason_codes": [e.reason_code],
                    "error": str(e),
                    "run_id": run_id,
                }
            except _StructuredPlanUnavailable as e:
                logger.error(
                    f"WorkflowController enforce run {run_id!r}: structured plan unavailable "
                    f"({e}); authoritative mode will not degrade to legacy execution."
                )
                legacy_result = {
                    "status": "needs_review",
                    "quality_gates_passed": False,
                    "files": [],
                    "failure_type": "PLANNING_ERROR",
                    "recovery": "PLAN_REPAIR",
                    "reason_codes": ["STRUCTURED_PLAN_UNAVAILABLE"],
                    "error": str(e),
                    "run_id": run_id,
                }
            except BaseException as error:
                # A bug here still propagates (see above); its calls and the
                # exception are recorded first (MODEL-EVIDENCE-HARDENING-001).
                self._write_enforce_trace(run_id, goal, {
                    "status": "error", "failure_type": type(error).__name__, "files": [],
                    "error": str(error)[:2000],
                }, enforce_started)
                raise

            # MODEL-EVIDENCE-HARDENING-001: the enforce run's own trace row -
            # its terminal status, why planning failed, the original
            # requirement verdicts, and every model call no subtask row
            # reported (planning, the terminal verifier).
            self._write_enforce_trace(run_id, goal, legacy_result, enforce_started)

            # Enforce mode is authoritative: repository progress without a
            # durable matching ControlState must stop before another unit
            # can execute.
            save_control_state(workspace_path, control_state)

            return WorkflowResult(
                run_id=run_id, control_state=control_state, route=route,
                legacy_result=legacy_result, subtask_results=subtask_results,
                decisions=decisions, verification_report=verification_report,
            )

        if migration_mode == "shadow":
            try:
                plan, subtask_results, decisions, verification_report, notes = await self._run_structured_shadow(
                    goal, workspace_path, route, run_id, control_context, control_state,
                )
                if plan is not None:
                    control_state = control_state.with_updates(current_plan_hash=plan.content_hash())
                # Always logged, even with zero notes - a clean run (every
                # subtask COMPLETED, nothing to flag) is real, useful
                # evidence the shadow path executed at all, not an absence
                # of output. Discovered via live validation (MA7.1): the
                # previous `if notes:` gate meant a fully successful shadow
                # run left NO trace anywhere - the only way to confirm it
                # happened was inferring it from LLM call timing in the raw
                # log, which is not something a real operator should have
                # to do.
                summary = (
                    f"plan={plan.plan_id if plan else None} subtasks={len(subtask_results)} "
                    f"decisions={len(decisions)} verdict={verification_report.verdict.value if verification_report else None}"
                )
                if notes:
                    summary += f" notes={'; '.join(notes)}"
                logger.info(f"WorkflowController shadow run {run_id!r}: {summary}")
            except Exception as e:
                # The shadow path is strictly observational - see this
                # module's own docstring. A bug in Stage A parsing, plan
                # validation, or SubtaskExecutor must never take down the
                # real generation run underneath it.
                logger.warning(f"WorkflowController shadow run {run_id!r} failed (non-fatal): {e}")
                subtask_results = ()
                decisions = ()
                verification_report = None

        legacy_result = await self._run_legacy_generation(
            goal, workspace_path,
            milestone_group_id=milestone_group_id, milestone_index=milestone_index,
            milestone_total=milestone_total, **legacy_kwargs,
        )

        # MA7.2: ControlState is now durable, not just an in-memory return
        # value - a caller of THIS run can load it back
        # (kriya.control.persistence.load_control_state) after the process
        # exits. Written last, with every bookkeeping update (milestone/
        # refactor baseline, shadow's current_plan_hash) already folded in,
        # so a partial/interrupted run never persists a half-updated state.
        # Best-effort: a write failure here must not fail a real generation
        # run that otherwise succeeded.
        try:
            save_control_state(workspace_path, control_state)
        except Exception as e:
            logger.warning(f"WorkflowController run {run_id!r}: failed to persist ControlState (non-fatal): {e}")

        return WorkflowResult(
            run_id=run_id, control_state=control_state, route=route,
            legacy_result=legacy_result, subtask_results=subtask_results,
            decisions=decisions, verification_report=verification_report,
        )

    @coordinated_mutation
    async def execute_milestones(self, run_state: Any, workspace_path: str, **milestone_kwargs: Any) -> WorkflowResult:
        """MA7-C4 (2026-08-25 external review) - the milestone-DAG
        counterpart to execute() above: makes WorkflowController the
        single real composition root for ALL FOUR route kinds (TASK/
        ENHANCEMENT/REFACTOR via execute(), MILESTONE via this method),
        closing the architectural split where kriya/cli.py's
        `generate --from-milestones` called kriya/workflow/milestones.py::
        run_milestones() directly, bypassing WorkflowController - and
        therefore ControlState/the rest of the control plane - entirely.

        Deliberately does NOT reimplement or duplicate run_milestones()'s
        own topological DAG execution, dependency-regression checks, or
        established-file threading - same "reuse the mature mechanism,
        don't duplicate it" principle as execute()'s own
        migration_mode="enforce" path (MA7.8). Unlike execute(), there is
        no shadow/legacy distinction here: run_milestones() already IS the
        real, mature mechanism this wraps, not a new implementation being
        compared against an old one, so there is nothing to "observe only"
        or fall back to - this method is only ever called when
        workflow_controller.enabled is true (kriya/cli.py's own gate,
        mirroring _dispatch_generation's identical convention for the
        plain-goal path); when disabled, the CLI still calls
        run_milestones() directly, completely unaffected by this method's
        existence.

        run_state.group_id becomes both run_id and milestone_group_id on
        the resulting ControlState - a milestone sequence's real identity
        already IS its group_id (MilestoneRunState's own field), not
        something this method should mint independently. engineering_
        triage classification here is for the SEQUENCE as a whole (real
        observability parity with execute()'s own always-classify
        behavior) - each individual milestone's own run_generation_
        workflow() call already does its own per-milestone classification
        internally, unaffected either way."""
        route = await self.workflow_engine.engineering_triage.classify(run_state.original_goal, workspace_path)
        control_context = WorkflowControlContext.for_route(route)
        control_state = ControlState.new(
            run_id=run_state.group_id, engineering_route=route,
            process_profile=control_context.process_profile, milestone_group_id=run_state.group_id,
        )
        milestones = list(run_state.milestones)
        plan_blob = json.dumps(
            [milestone.model_dump(mode="json") for milestone in milestones],
            sort_keys=True,
            separators=(",", ":"),
        )
        prior_control_state_ownerless = json_document_is_ownerless(
            control_state_path(workspace_path),
        )
        if prior_control_state_ownerless:
            with open(control_state_path(workspace_path), "r", encoding="utf-8") as handle:
                ControlState.from_dict(json.load(handle))
        try:
            contract_registry = load_contract_registry(workspace_path)
        except ContractRegistryCorruptError as exc:
            # PRD-029: an unreadable registry is never treated as empty.
            return WorkflowResult(
                run_id=run_state.group_id, control_state=control_state, route=route,
                legacy_result={
                    "status": "needs_review", "group_id": run_state.group_id, "quality_gates_passed": False,
                    "reason_codes": [exc.reason_code], "persistence_store": "contract_registry",
                    "persistence_error": str(exc),
                },
            )
        artifact_registry = load_artifact_registry(workspace_path)
        # PRD-008 S4b: un-complete milestones whose completion proof fails
        # BEFORE milestone_states is derived from completed_milestone_ids,
        # so ControlState never marks a milestone done that will rerun.
        from kriya.workflow.milestones import revalidate_completed_milestones
        revalidate_completed_milestones(
            workspace_path, run_state,
            config=getattr(getattr(self.workflow_engine, "kernel", None), "config", None),
        )
        next_milestone_id = next(
            (
                milestone.id for milestone in milestones
                if milestone.id not in run_state.completed_milestone_ids
            ),
            None,
        )
        durable_control_state = load_control_state(workspace_path)
        if (
            durable_control_state is not None
            and durable_control_state.milestone_group_id == run_state.group_id
        ):
            control_state = durable_control_state
        control_state = control_state.with_updates(
            current_milestone_id=next_milestone_id,
            milestone_states={
                milestone.id: (
                    "stale" if milestone.id in run_state.stale_milestone_ids
                    else "done" if milestone.id in run_state.completed_milestone_ids
                    else "pending"
                )
                for milestone in milestones
            },
            current_plan_hash=hashlib.sha256(plan_blob.encode("utf-8")).hexdigest(),
            current_contract_hash=compute_registry_hash(contract_registry.to_dict()),
            current_artifact_registry_hash=compute_registry_hash(artifact_registry.to_dict()),
        )

        try:
            save_control_state(workspace_path, control_state)
        except Exception as exc:
            legacy_result = {
                "status": "needs_review",
                "group_id": run_state.group_id,
                "quality_gates_passed": False,
                "reason_codes": ["CONTROL_STATE_PERSISTENCE_FAILED"],
                "persistence_store": "control_state",
                "persistence_error": str(exc),
            }
            return WorkflowResult(
                run_id=run_state.group_id,
                control_state=control_state,
                route=route,
                legacy_result=legacy_result,
            )

        try:
            _migrate_legacy_milestone_ownership(
                workspace_path,
                run_state,
                contract_registry,
                artifact_registry,
                prior_control_state_ownerless=prior_control_state_ownerless,
            )
        except Exception as exc:
            legacy_result = {
                "status": "needs_review",
                "group_id": run_state.group_id,
                "quality_gates_passed": False,
                "reason_codes": ["LEGACY_STATE_MIGRATION_FAILED"],
                "persistence_store": "legacy_workspace_state",
                "persistence_error": str(exc),
            }
            return WorkflowResult(
                run_id=run_state.group_id,
                control_state=control_state,
                route=route,
                legacy_result=legacy_result,
            )

        from kriya.workflow.milestones import MilestonePersistenceError, run_milestones
        try:
            legacy_result = await run_milestones(
                self.workflow_engine,
                run_state,
                workspace_path,
                authoritative=True,
                **milestone_kwargs,
            )
        except MilestonePersistenceError as exc:
            legacy_result = exc.to_result(run_state.group_id)

        durable_control_state = load_control_state(workspace_path)
        if (
            durable_control_state is not None
            and durable_control_state.milestone_group_id == run_state.group_id
        ):
            control_state = durable_control_state
        control_state = control_state.with_updates(
            milestone_states={
                milestone.id: (
                    "stale" if milestone.id in run_state.stale_milestone_ids
                    else "done" if milestone.id in run_state.completed_milestone_ids
                    else "pending"
                )
                for milestone in milestones
            },
            current_contract_hash=compute_registry_hash(load_contract_registry(workspace_path).to_dict()),
            current_artifact_registry_hash=compute_registry_hash(load_artifact_registry(workspace_path).to_dict()),
        )

        try:
            save_control_state(workspace_path, control_state)
        except Exception as exc:
            legacy_result = {
                "status": "needs_review",
                "group_id": run_state.group_id,
                "quality_gates_passed": False,
                "reason_codes": ["CONTROL_STATE_PERSISTENCE_FAILED"],
                "persistence_store": "control_state",
                "persistence_error": str(exc),
            }

        return WorkflowResult(
            run_id=run_state.group_id, control_state=control_state, route=route, legacy_result=legacy_result,
        )

    def _attach_milestone_metadata(
        self, control_state: ControlState, milestone_group_id: Optional[str], milestone_index: Optional[int],
    ) -> ControlState:
        if milestone_group_id is None:
            return control_state
        current_milestone_id = (
            f"{milestone_group_id}:{milestone_index}" if milestone_index is not None else milestone_group_id
        )
        return control_state.with_updates(
            milestone_group_id=milestone_group_id, current_milestone_id=current_milestone_id,
        )

    def _attach_refactor_baseline(self, control_state: ControlState, workspace_path: str) -> ControlState:
        base_commit = compute_base_commit(workspace_path)
        tree_hash = compute_tree_hash(workspace_path)
        # STATE-001 (2026-09-14): the real working-tree-content-sensitive
        # identity, computed alongside tree_hash's own narrower committed-
        # tree meaning - see ControlState.workspace_content_hash's own
        # field docstring.
        workspace_content_hash = compute_workspace_content_hash(workspace_path)
        if base_commit is None and tree_hash is None and workspace_content_hash is None:
            return control_state
        return control_state.with_updates(
            base_commit=base_commit, tree_hash=tree_hash, workspace_content_hash=workspace_content_hash,
        )

    async def _run_structured_shadow(
        self, goal: str, workspace_path: str, route: Any, run_id: str,
        control_context: WorkflowControlContext, control_state: ControlState,
    ) -> Tuple[
        Optional[EngineeringPlan], Tuple[SubtaskResult, ...],
        Tuple[Decision, ...], Optional[VerificationReport], List[str],
    ]:
        """The body is kriya/workflow/shadow_mode.py (ShadowMode.run, E12); its collaborators are resolved here, once per
        entry (_shadow_deps)."""
        return await _shadow_mode().ShadowMode.run(
            _shadow_deps(self), goal, workspace_path, route, run_id, control_context, control_state,
        )

    def _write_enforce_trace(self, run_id: str, goal: str, result: Dict[str, Any], started_at: float) -> bool:
        """The body is kriya/workflow/shadow_mode.py (ShadowMode.write_enforce_trace, E12); its collaborators are resolved here, once per
        entry (_trace_deps)."""
        return _shadow_mode().ShadowMode.write_enforce_trace(_trace_deps(self), run_id, goal, result, started_at)

    async def _run_structured_enforce(
        self, goal: str, workspace_path: str, route: Any, run_id: str,
        control_context: WorkflowControlContext, control_state: ControlState,
        legacy_kwargs: Dict[str, Any],
    ) -> Tuple[
        Dict[str, Any], Optional[EngineeringPlan], Tuple[SubtaskResult, ...],
        Tuple[Decision, ...], Optional[VerificationReport], ControlState,
    ]:
        """The body is kriya/workflow/enforce_loop.py (EnforceLoop.run, E12); its collaborators are resolved here, once per
        entry (_enforce_deps)."""
        return await _enforce_loop().EnforceLoop.run(
            _enforce_deps(self), goal, workspace_path, route, run_id, control_context, control_state, legacy_kwargs,
        )

    async def _build_context(
        self, goal: str, plan: EngineeringPlan, workspace_path: str, route: Any,
        control_context: WorkflowControlContext, control_state: ControlState,
    ) -> ContextPackage:
        """MA7.2: routes through the real ContextOrchestrator (MA5.7) for
        strategy selection, token-budget-aware assembly, and real
        contract_entries/artifact_entries (loaded from whatever
        ContractRegistry/ArtifactRegistry already have persisted for this
        workspace - kriya/control/persistence.py, empty registries are a
        legitimate, honest result for a workspace with no prior milestone
        history, not an error). `milestone=None`: WorkflowController does
        not receive a real MilestoneV2 object today, only bare
        milestone_group_id/milestone_index identifiers (MA6.9) - passing
        None here rather than fabricating one is the same "don't guess"
        discipline as raw_rag_context=None below.

        What ContextOrchestrator.build() does NOT give this path: the
        actual current-on-disk content of files the plan's subtasks will
        touch - its own reuse boundary only accepts already-assembled
        `established_file_context` (labeled, by that parameter's real
        semantics, as content from an EARLIER COMPLETED MILESTONE) or an
        opaque `raw_rag_context` blob. Neither is an honest label for "this
        subtask's own planned file, read fresh off disk right now" -
        mislabeling it as established_file_context would corrupt exactly
        the kind of real provenance this whole control-plane initiative
        exists to guarantee (kriya/workflow/context_package.py's own
        CONTEXT_SOURCE_TYPES docstring). So that content is still read
        directly here, with the SAME correct
        source_type="named_in_request" labeling the pre-MA7.2 version of
        this method already used, and layered on top of
        ContextOrchestrator's own package via with_changes() rather than
        threaded through build() under a false pretense."""
        contract_registry = load_contract_registry(workspace_path)
        artifact_registry = load_artifact_registry(workspace_path)
        contract_entries = tuple(contract_entry_from_record(r) for r in contract_registry.all_records())
        artifact_entries = tuple(artifact_entry_from_record(r) for r in artifact_registry.all_records())

        base_package = await ContextOrchestrator().build(
            request=goal, route=route, profile=control_context.process_profile,
            workspace_path=workspace_path, milestone=None, control_state=control_state,
            contract_entries=contract_entries, artifact_entries=artifact_entries,
        )

        items = []
        seen = set()
        for subtask in plan.subtasks:
            for pf in subtask.planned_files:
                if pf.path in seen:
                    continue
                seen.add(pf.path)
                full_path = os.path.join(workspace_path, pf.path)
                if not os.path.isfile(full_path):
                    continue
                try:
                    with open(full_path, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except Exception as e:
                    logger.debug(f"WorkflowController shadow context: could not read {pf.path!r}: {e}")
                    continue
                items.append(make_context_item(
                    path=pf.path, content=content, reason=PLANNED_FILE_SOURCE_REASON,
                    source_type="named_in_request", trust_level="repository",
                ))

        return base_package.with_changes(relevant_files=base_package.relevant_files + tuple(items))

    async def _run_legacy_generation(self, goal: str, workspace_path: str, **legacy_kwargs: Any) -> Dict[str, Any]:
        """MA6.10's LegacyGenerationAdapter, in its very first, unmodified
        form: exactly the call every existing caller (kriya/cli.py's
        `generate` command) already makes, so this slice changes zero
        behavior of the actual generation pipeline - only wraps it with
        control-plane bookkeeping (and, in shadow mode, an observational
        run alongside it) around the outside."""
        return await self.workflow_engine.run_generation_workflow(goal, workspace_path, **legacy_kwargs)


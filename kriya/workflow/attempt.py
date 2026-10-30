"""One retry-iteration's happy-path body for the Developer + Quality Gates
loop - Developer invocation, file writes, the completeness check, compile/
test gates, and the Runtime Verification gate. Extracted verbatim from
kriya/workflow/workflow.py's run_generation_workflow() (2026-08-11,
Opportunity 2 Slice 2): mutates the passed-in GenerationState in place and
either returns normally (Quality Gates passed) or raises the same exceptions
the inline code always did - QualityGateFailure or IncompleteGenerationError
- letting them propagate to the caller's own except block exactly as before.
Everything after Quality Gates pass (checkpoint save, human approval, apply-
to-workspace, lesson extraction, the full regression suite) deliberately
stays in workflow.py - out of scope for this slice.
"""
import functools
import logging
import os
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from kriya.agents.agent import DeveloperAgent
from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.core.inference_settings import role_binding_for_model
from kriya.core.kernel import Kernel
from kriya.core.token_budget import OutputBudgetUnsatisfiableError
from kriya.policy import approval as source_review
from kriya.policy.approval import (
    RuntimeCommandSubject,
)
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import (
    WriteScopeMode,
    is_trusted_control_path,
    trusted_control_path_denial,
)
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult

# PolymorphicValidator, ContextItem and EngineeringPlan are annotation-only
# names here, imported at runtime so typing.get_type_hints() on this module's
# dataclasses and functions resolves (PRD-001; none of these modules imports
# attempt.py, so there is no cycle).
from kriya.tools import test_execution
from kriya.tools.service_runtime import (
    ManagedServiceVerificationSpec,
    ProbeSpec,
    ReadinessSpec,
    ServiceVerificationOutcomeKind,
    _prepare_required_artifact,
    run_managed_service_verification,
)
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import (
    candidate_transaction,
    contract_feedback,
    decision_evidence,
    fallback_decision,
    gate_orchestrator,
    generation_service,
    process_boundary,
    repair_orchestrator,
    runtime_verification,
    spec_compliance,
)
from kriya.workflow.attribution import (
    extract_self_diagnosed_files,
    find_edits_ignoring_own_diagnosis,
    find_edits_ignoring_reported_line,
    find_misdirected_edit_target,
    find_whole_response_no_op,
    resolve_fallback_model,
)
from kriya.workflow.authority_escalation import grant_member_hints

# E10 re-exports (tests/architecture/test_facades.py PARTIAL_FACADES): the edit-capability locus bound and the response-validity failure family moved to candidate_transaction, importable from here as before.
# Removed in P1.
from kriya.workflow.candidate_transaction import _RESPONSE_VALIDITY_FAILURES as _RESPONSE_VALIDITY_FAILURES
from kriya.workflow.candidate_transaction import MAX_SYMBOL_LOCI_PER_FILE as MAX_SYMBOL_LOCI_PER_FILE
from kriya.workflow.context_budget import (
    REASON_MINIMUM_AUTHORITY_UNFIT,
    DeveloperRequestFit,
    OptionalSection,
    _reserve_graph_context_budget,
    _reserve_sibling_content_budget,
    allocation_window,
    build_code_context,
    build_known_target_context,
    developer_reference,
    fenced_reference_section,
    investigation_evidence_char_budget,
    request_capacity,
    retry_evidence_char_budget,
)
from kriya.workflow.context_package import ContextItem, make_context_item
from kriya.workflow.context_source import (
    CurrentSourceResolver,
    SourceDerivationCache,
    evaluate_member_hints_from_search_evidence,
    member_boundaries_for,
    member_ids_matching_name,
    resolve_member_hints_from_failure_location,
)
from kriya.workflow.contract_authority import derive_direct_contract_authorizations
from kriya.workflow.dependency_invalidation import (
    dependent_closure,
)
from kriya.workflow.deterministic_failure_diagnostic import DeterministicFailureDiagnosticStore
from kriya.workflow.edit_capability import exact_window_reserve
from kriya.workflow.edit_safety import (
    StagedFileWrite,
    apply_anchored_edits,
)
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.failure_grounding import (
    _capture_failed_content,
    build_static_rule_failure,
)

# E6 re-exports (tests/architecture/test_facades.py PARTIAL_FACADES): moved to fallback_decision / generation_service,
# importable from here as before. Removed in P1.
from kriya.workflow.fallback_decision import _fallback_rejection as _fallback_rejection
from kriya.workflow.file_integrity import (
    VerificationTreeBinding,
    read_shown_text,
)
from kriya.workflow.file_resolution import (
    IncompleteGenerationError,
    _resolve_run_command,
    artifact_resolution_scope,
    extract_target_test,
    find_brownfield_public_api_changes,
    find_missing_expected_files,
    find_protected_api_reference_changes,
    find_runnable_test_files,
    find_unpreserved_test_obligation,
    find_unrequested_architectural_surfaces,
    find_unrestored_public_api_contracts,
    normalize_written_filepath,
    planned_new_artifact_paths,
    resolve_planned_artifact_owners,
)
from kriya.workflow.gate_orchestrator import (
    _PROCESS_BOUNDARY_RECURRENCE_ESCALATION as _PROCESS_BOUNDARY_RECURRENCE_ESCALATION,
)
from kriya.workflow.gate_orchestrator import _TEST_VERIFIERS as _TEST_VERIFIERS

# E8 re-exports (tests/architecture/test_facades.py PARTIAL_FACADES): the goal-spec gate's advisory statuses moved to
# gate_orchestrator with the gate, importable from here as before. Removed in P1.
from kriya.workflow.gate_orchestrator import SPEC_COMPLIANCE_MODEL_ADVISORY as SPEC_COMPLIANCE_MODEL_ADVISORY
from kriya.workflow.gate_orchestrator import SPEC_MODEL_INDETERMINATE as SPEC_MODEL_INDETERMINATE
from kriya.workflow.gate_orchestrator import SPEC_MODEL_REPORTED_MISSING as SPEC_MODEL_REPORTED_MISSING

# E6 re-exports (see above). Removed in P1.
from kriya.workflow.generation_service import _PLANNED_SOURCE_UNBOUNDED_TOKENS as _PLANNED_SOURCE_UNBOUNDED_TOKENS
from kriya.workflow.generation_service import PLANNED_SOURCE_HEADER as PLANNED_SOURCE_HEADER
from kriya.workflow.generation_service import _chain_binding as _chain_binding
from kriya.workflow.migration import (
    MigrationResolution,
)
from kriya.workflow.obligations import (
    ObligationLedger,
    ObligationRecord,
)
from kriya.workflow.operations import (
    CodeOperation,
    all_results_are_no_change,
    classify_result_operation,
    operation_for_attempt,
    operation_for_file,
    validate_operation_result,
)
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.process_boundary import (
    _INITIAL_TEST_PROCESS_BOUNDARY_CONSTRAINT as _INITIAL_TEST_PROCESS_BOUNDARY_CONSTRAINT,
)
from kriya.workflow.process_boundary import _NONZERO_PROCESS_EXIT_RE as _NONZERO_PROCESS_EXIT_RE
from kriya.workflow.process_boundary import _TERMINATING_CASE_MARKERS as _TERMINATING_CASE_MARKERS
from kriya.workflow.prompt_composition import prompt_composition
from kriya.workflow.repair_contract import (
    RepairContractStatus,
    build_repair_contract,
    derive_process_boundary_participants,
)

# E7 re-exports (tests/architecture/test_facades.py PARTIAL_FACADES): moved to repair_orchestrator, importable
# from here as before. Removed in P1.
from kriya.workflow.repair_orchestrator import (
    _DETERMINISTIC_VERDICT_REASON_CODES as _DETERMINISTIC_VERDICT_REASON_CODES,
)
from kriya.workflow.repair_orchestrator import _GOAL_CALL_NAME as _GOAL_CALL_NAME
from kriya.workflow.repair_orchestrator import _GOAL_MEMBER_HINTS_MAX as _GOAL_MEMBER_HINTS_MAX
from kriya.workflow.repair_orchestrator import RetryContextPreparation as RetryContextPreparation
from kriya.workflow.repair_orchestrator import (
    _classify_retry_target_source_origin as _classify_retry_target_source_origin,
)
from kriya.workflow.repair_orchestrator import (
    _compute_retry_evidence_fingerprint as _compute_retry_evidence_fingerprint,
)
from kriya.workflow.repair_orchestrator import _goal_named_member_candidates as _goal_named_member_candidates
from kriya.workflow.repair_orchestrator import _goal_named_members_of as _goal_named_members_of
from kriya.workflow.repair_orchestrator import _preserve_member_exact_precision as _preserve_member_exact_precision
from kriya.workflow.repair_orchestrator import _target_source_record as _target_source_record
from kriya.workflow.requirements import (
    RequirementSet,
)
from kriya.workflow.resume_fingerprints import ResumePlan
from kriya.workflow.retry_package import RetryPackage, build_retry_package
from kriya.workflow.retry_policy import (
    API_CONTRACT_RECOVERY_MAX_ATTEMPTS,
    RESERVED_FALLBACK_ALLOWANCE,
    RetryAction,
    decide_attempt_mode,
)
from kriya.workflow.retry_prompts import (
    _build_api_contract_recovery_prompt,
    _build_coordinated_retry_prompt,
    _build_full_set_retry_prompt,
    _build_missing_files_retry_prompt,
    _build_targeted_retry_prompt,
)
from kriya.workflow.run_events import EventAuthority, RunEvent

# E9 re-exports (tests/architecture/test_facades.py PARTIAL_FACADES): the runtime-verification constants moved to
# runtime_verification with the code, importable from here as before. Removed in P1.
from kriya.workflow.runtime_verification import _DISTRUST_DISCLOSURE_NOTE as _DISTRUST_DISCLOSURE_NOTE
from kriya.workflow.runtime_verification import (
    _MANAGED_SERVICE_FORBIDDEN_LEADING_EXECUTABLES as _MANAGED_SERVICE_FORBIDDEN_LEADING_EXECUTABLES,
)
from kriya.workflow.runtime_verification import _MANAGED_SERVICE_PROBE_KINDS as _MANAGED_SERVICE_PROBE_KINDS
from kriya.workflow.runtime_verification import _MANAGED_SERVICE_READINESS_KINDS as _MANAGED_SERVICE_READINESS_KINDS
from kriya.workflow.runtime_verification import _MANAGED_SERVICE_SHELL_MARKERS as _MANAGED_SERVICE_SHELL_MARKERS
from kriya.workflow.runtime_verification import (
    _RUNTIME_VERIFICATION_SYNTHETIC_INPUT as _RUNTIME_VERIFICATION_SYNTHETIC_INPUT,
)
from kriya.workflow.runtime_verification import _SHELL_INTERPRETER_EXECUTABLES as _SHELL_INTERPRETER_EXECUTABLES
from kriya.workflow.runtime_verification import _SUPPORTED_RUNTIME_EXECUTION_MODES as _SUPPORTED_RUNTIME_EXECUTION_MODES
from kriya.workflow.runtime_verification import (
    MANAGED_SERVICE_APPROVAL_UNSUPPORTED as MANAGED_SERVICE_APPROVAL_UNSUPPORTED,
)
from kriya.workflow.semantic_region_authority import AuthorizedSemanticRegion, find_unauthorized_semantic_changes
from kriya.workflow.skill_extraction import _skill_verification_context

# E10 re-exports (tests/architecture/test_facades.py PARTIAL_FACADES): the requirement-provenance word primitives moved to spec_compliance with the spec-compliance arbitration, importable from here as before.
# Removed in P1.
from kriya.workflow.spec_compliance import _REQUIREMENT_PROVENANCE_STOPWORDS as _REQUIREMENT_PROVENANCE_STOPWORDS
from kriya.workflow.spec_compliance import _REQUIREMENT_WORD_RE as _REQUIREMENT_WORD_RE
from kriya.workflow.state import (
    APIContractRecovery,
    APIContractRecoveryPhase,
    GenerationState,
    RecoveryPhaseAdvanced,
)
from kriya.workflow.static_checks import (
    derive_stack_contract,
    find_established_stack_drift,
    find_goal_stack_mismatch,
    log_stack_contract_boundary,
    run_static_checks,
    validate_stack_contract_artifacts,
)
from kriya.workflow.suite_attribution import (
    SCOPE_CANDIDATE_GATE,
    SCOPE_TEST_DELTA_COVERING_RUN,
    SCOPE_VERIFICATION_UNIT,
    attribute_suite_result,
    captured_baseline,
    post_environment_identity,
)
from kriya.workflow.toolchain import (
    _check_java_toolchain_mismatch,
    _resolve_java_home_override,
    toolchain_declaration_mutable,
)
from kriya.workflow.verification_contract import ContractVerdictState
from kriya.workflow.verification_coordinator import (
    # Re-exported: tests and older callers import these from attempt.
    _directly_executable_runtime_verifiers as _directly_executable_runtime_verifiers,
)
from kriya.workflow.verification_coordinator import (
    _directly_executable_verifiers as _directly_executable_verifiers,
)
from kriya.workflow.verification_coordinator import (
    is_verification_only_unit,
)
from kriya.workflow.verifier_evidence import (
    RetainedRuntimeEvidence,
    apply_runtime_disposition,
    runtime_evidence_outcome_fields,
)

logger = logging.getLogger(__name__)



def _apply_candidate_pom_corrections(
    state: GenerationState, ctx: "AttemptContext", compile_known_files: List[str],
) -> None:
    """Deterministic pom.xml corrections of the candidate's own pom (FILE-INTEGRITY-CONTRACT-001). The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return gate_orchestrator._apply_candidate_pom_corrections(_gate_deps(), state, ctx, compile_known_files)


def _bind_verification_tree(state: GenerationState, ctx: "AttemptContext") -> VerificationTreeBinding:
    """FILE-INTEGRITY-CONTRACT-001: the tree the attempt's gates verify, bound before the first gate. The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return gate_orchestrator._bind_verification_tree(_gate_deps(), state, ctx)


def _record_candidate_change(state: GenerationState, ctx: "AttemptContext") -> None:
    """LR-R1-M1 ``candidate.change`` (STAGED) for the candidate frozen for this attempt's gates. The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return gate_orchestrator._record_candidate_change(_gate_deps(), state, ctx)


def _require_worktree_matches_candidate(state: GenerationState, ctx: "AttemptContext") -> None:
    """FILE-INTEGRITY-CONTRACT-001: the sandbox holds exactly the staged candidate bytes. The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return gate_orchestrator._require_worktree_matches_candidate(_gate_deps(), state, ctx)


def _capture_original(state: GenerationState, ctx: "AttemptContext", relpath: str) -> None:
    """The body is kriya/workflow/candidate_transaction.py (E10)."""
    return candidate_transaction._capture_original(state, ctx, relpath)


def _baseline_restoration_write(state: GenerationState, target_path: str, relpath: str) -> StagedFileWrite:
    """The body is kriya/workflow/candidate_transaction.py (E14)."""
    return candidate_transaction._baseline_restoration_write(state, target_path, relpath)


def _file_integrity_stop(state: GenerationState, filepath: str, error: Any) -> "QualityGateFailure":
    """The body is kriya/workflow/candidate_transaction.py (E14)."""
    return candidate_transaction._file_integrity_stop(state, filepath, error)


def _target_exists(ctx: "AttemptContext", filepath: str) -> bool:
    """The body is kriya/workflow/candidate_transaction.py (E14)."""
    return candidate_transaction._target_exists(ctx, filepath)


def _brownfield_owner_contract_block(ctx: "AttemptContext", target_files: Optional[List[str]]) -> str:
    """CTX-001 P1 WP7 (A3): INSTRUCTION TEXT ONLY now - this function's own
    source-content responsibility (a naive 24,000-combined-char prefix cap,
    Architect-list order, silent drop of later files past the cap - CTX-001-
    P0's C1/F1/F2 finding) is RETIRED, not enlarged. Source content for
    known-target files now flows through build_known_target_context()
    (kriya/workflow/context_budget.py) instead - member-aware where
    possible, current-source-resolved (worktree-authoritative), priority-
    floored rather than unconditionally included, explicitly omitted rather
    than silently truncated. See run_attempt()'s own call site for how the
    two are combined: this instruction text always lands in task_desc
    unconditionally (its own token cost is negligible and constant, not
    budget-managed - architecture doc section 9), while the source content
    is budget-allocated separately and appended to active_code_context.

    A file that doesn't actually exist yet (a genuinely NEW file Architect
    listed as a "known target") produces no instruction text here - this
    contract is about PRESERVING an existing owner's identity, which has no
    meaning for a file with no existing owner yet."""
    existing = [
        filepath for filepath in target_files or []
        if os.path.isfile(os.path.join(ctx.worktree_path, filepath))
        or os.path.isfile(os.path.join(ctx.workspace_path, filepath))
    ]
    if not existing:
        return ""
    return (
        "\n\n=== AUTHORITATIVE BROWNFIELD OWNER CONTRACT ===\n"
        "The following existing file(s) are in-place repair targets: "
        + ", ".join(sorted(existing)) + ". Preserve their "
        "package/module identity, existing public type names, constructors, and public "
        "method signatures. Do not paste a planned replacement class into a resolved "
        "owner path. Existing tests and callers are contract evidence. Repair behavior "
        "behind the existing API, preferring private/internal changes. Their real current "
        "source, exactly as it exists now, is provided separately in the code context below."
    )


def _graph_context_exclusion_set(
    state: GenerationState, ctx: "AttemptContext", known_target_files: Optional[List[str]] = None,
) -> set:
    """CTX-001 P1 WP7 (DUPLICATE_SOURCE_CONTEXT_PATHS=0): a path already
    represented through a MORE authoritative, current-source producer
    (retry-time all_files_written content via retry_prompts.py's own
    renderers, or attempt-1's known-target/owner-contract evidence via
    build_known_target_context()) must be EXCLUDED from
    build_code_context()'s own matched/related candidate lists before
    they're rendered - never shown twice in the same prompt. Found live
    while implementing this package: retry_prompts.py's own
    _build_targeted_retry_prompt/_build_full_set_retry_prompt/
    _build_missing_files_retry_prompt all append every state.all_files_
    written path's FULL current content on top of active_code_context,
    which already contains build_code_context()'s own (possibly
    skeletonized) rendering of the SAME path whenever it also happens to be
    Graph-RAG matched/related - a real, pre-existing duplication this
    package closes as part of its own required no-duplicate-representation
    guarantee, not a new one it introduces.

    known_target_files is only non-empty for the attempt-1 owner-contract
    call site - excluded there for the same reason, in the OPPOSITE
    direction: a known-target path is excluded from the (possibly
    degraded) Graph-RAG rendering so build_known_target_context()'s own
    EXACT/priority-floored representation is the one that wins, never a
    silently-lower-fidelity duplicate alongside it."""
    exclusion = set(state.all_files_written) | set(ctx.established_files)
    if known_target_files:
        exclusion |= set(known_target_files)
    return exclusion


def _filtered_candidates(paths: Any, exclude: set) -> List[str]:
    return [p for p in (paths or []) if p not in exclude]


def _resolve_known_target_member_hints(
    ctx: "AttemptContext", known_target_files: List[str],
) -> Dict[str, List[str]]:
    """CTX-001 P1 C2: the run's own retrieval-grounded members of the known targets. The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return repair_orchestrator._resolve_known_target_member_hints(_repair_deps(), ctx, known_target_files)


def _goal_named_member_hints(ctx: "AttemptContext", known_target_files: List[str]) -> Dict[str, List[str]]:
    """KNOWN-TARGET-GOAL-NAMED-MEMBER-HINT-001: the goal's named members of the known targets. The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return repair_orchestrator._goal_named_member_hints(_repair_deps(), ctx, known_target_files)


def _merge_goal_named_member_hints(
    ctx: "AttemptContext", member_hints: Dict[str, List[str]], paths: List[str],
) -> bool:
    """RETRY-CONTEXT-GOAL-MEMBER-LOSS-001: the goal's named members merged into a retry's hints. The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return repair_orchestrator._merge_goal_named_member_hints(_repair_deps(), ctx, member_hints, paths)


def _validated_member_hints(ctx: "AttemptContext", candidates_by_path: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """candidate member names validated against the current member boundaries. The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return repair_orchestrator._validated_member_hints(_repair_deps(), ctx, candidates_by_path)


def _resolve_retry_member_hints(
    ctx: "AttemptContext", state: GenerationState, target_files: List[str],
) -> Dict[str, List[str]]:
    """CTX-001 P1 C2/C3: the failure-grounded member hints of a retry's authorized targets. The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return repair_orchestrator._resolve_retry_member_hints(_repair_deps(), ctx, state, target_files)


def _record_retry_projection_context_items(
    state: GenerationState, retry_package: Optional["RetryPackage"],
) -> None:
    """VAL-001 G1 D1: a retry package's projections recorded as context items. The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return repair_orchestrator._record_retry_projection_context_items(_repair_deps(), state, retry_package)


def _record_omitted_members(state: GenerationState, omitted: Iterable[Dict[str, Any]]) -> None:
    """The body is kriya/workflow/repair_orchestrator.py (E14)."""
    return repair_orchestrator._record_omitted_members(state, omitted)


def _record_known_target_package(state: GenerationState, items: Iterable[ContextItem]) -> None:
    """The body is kriya/workflow/repair_orchestrator.py (E14)."""
    return repair_orchestrator._record_known_target_package(state, items)


def exit_authority_text(ctx: "AttemptContext") -> str:
    """PRD-025: the text an expected nonzero exit may be declared in - the
    user's own request (exit_authority_goal, else grounding_goal, else the
    goal of a plain top-level run, which IS the user's). A milestone or
    subtask goal, a plan, a judge's criteria or a grader's reasoning is
    never consulted."""
    return ctx.exit_authority_goal or ctx.grounding_goal or ctx.goal


def _escalation_authorized_paths(ctx: "AttemptContext", target_files: Iterable[str]) -> Tuple[str, ...]:
    """The body is kriya/workflow/repair_orchestrator.py (E14)."""
    return repair_orchestrator._escalation_authorized_paths(ctx, target_files)


def _record_authority_expansions(state: GenerationState, expansions: Iterable[Any]) -> None:
    """The body is kriya/workflow/repair_orchestrator.py (E14)."""
    return repair_orchestrator._record_authority_expansions(state, expansions)


def _effective_retry_temperature(ctx: "AttemptContext", model_identity: str) -> Optional[float]:
    """the sampling temperature a Developer retry on ``model_identity`` is sent with. The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return repair_orchestrator._effective_retry_temperature(_repair_deps(), ctx, model_identity)


def _prepare_retry_context(
    state: GenerationState, ctx: "AttemptContext", *,
    target_files: List[str], prompt_window: int, model_identity: str,
    base_code_context: str = "", enable_no_progress_gate: bool = True,
) -> RetryContextPreparation:
    """VAL-001 G1-R3: the centralized retry-time source/member-hint preparation. The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return repair_orchestrator._prepare_retry_context(_repair_deps(), state, ctx, target_files=target_files, prompt_window=prompt_window, model_identity=model_identity, base_code_context=base_code_context, enable_no_progress_gate=enable_no_progress_gate)


def _record_all_files_written_as_exact_context(
    state: GenerationState, ctx: "AttemptContext", filepaths: Iterable[str],
) -> None:
    """VAL-001 G1 D1 (found via the real focused-suite regression after
    7bc52b5, 2026-09-18): a full-set retry (state.attempt_number > 1) and a
    missing-files retry do NOT go through build_known_target_context() at
    all - that call is gated on `state.attempt_number == 1` (see the
    known_target_files block above). Their own content-supply functions
    (_build_full_set_retry_prompt/_build_missing_files_retry_prompt,
    kriya/workflow/retry_prompts.py) instead read every file in
    all_files_written FRESH from ctx.worktree_path via a plain, unbounded
    `fh.read()` - real, complete, current content, exactly the same
    "files_with_current_content" guarantee _completeness_gated_operation()
    already trusts elsewhere (see kriya/agents/agent.py's own
    prefer_anchored_edit reasoning) - whenever `retry_package` is None (the
    branch _build_full_set_retry_prompt itself takes for this exact case).
    This function makes that same, already-real guarantee visible to
    _completeness_gated_operation(), mirroring _build_full_set_retry_prompt's
    OWN read (same path, same files, same error-tolerant skip) so the two
    can never disagree about what was actually shown.

    Only ever records tier="full"/is_exact=True/member_id=None - never a
    fabricated exactness claim: if the read fails, that path is silently
    skipped here exactly as it is in the prompt-builder itself (no entry
    recorded, so _completeness_gated_operation() correctly falls back to its
    own fail-closed default for that path)."""
    for filepath in filepaths:
        full_path = os.path.join(ctx.worktree_path, filepath)
        try:
            current_content, current_revision = read_shown_text(full_path)
        except OSError:
            continue
        state.known_target_context_items[filepath] = make_context_item(
            path=filepath, content=current_content, reason="all_files_written_current_source",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, revision=current_revision,
        )


def _completeness_gated_operation(
    filepath: str, base_operation: CodeOperation, *,
    file_exists: bool, ctx: "AttemptContext", state: Optional[GenerationState],
) -> Tuple[CodeOperation, bool]:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._completeness_gated_operation(_candidate_deps(), filepath, base_operation, file_exists=file_exists, ctx=ctx, state=state)


def _is_restore_public_contract_phase(state: Optional[GenerationState]) -> bool:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._is_restore_public_contract_phase(_candidate_deps(), state)


def _has_authoritative_full_source(
    filepath: str, ctx: "AttemptContext", state: Optional[GenerationState],
) -> bool:
    """The body is kriya/workflow/candidate_transaction.py (E10)."""
    return candidate_transaction._has_authoritative_full_source(filepath, ctx, state)


def _current_revision(filepath: str, ctx: "AttemptContext") -> Optional[str]:
    """The body is kriya/workflow/candidate_transaction.py (E10)."""
    return candidate_transaction._current_revision(filepath, ctx)


def _validate_actual_mutation_authority(
    filepath: str, actual_operation: CodeOperation, *,
    file_exists: bool, ctx: "AttemptContext", state: Optional[GenerationState],
) -> Optional[str]:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._validate_actual_mutation_authority(_candidate_deps(), filepath, actual_operation, file_exists=file_exists, ctx=ctx, state=state)


def _operation_map(
    ctx: "AttemptContext", filepaths: List[str], attempt_operation: CodeOperation,
    state: Optional[GenerationState] = None,
) -> Dict[str, CodeOperation]:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._operation_map(_candidate_deps(), ctx, filepaths, attempt_operation, state)


def _preserved_authoritative_locator_files(
    state: GenerationState, target_files: List[str], self_diagnosed: List[str],
) -> List[str]:
    """The body is kriya/workflow/candidate_transaction.py (E14)."""
    return candidate_transaction._preserved_authoritative_locator_files(state, target_files, self_diagnosed)


def _fallback_routing(state: GenerationState, ctx: "AttemptContext") -> Any:
    """LR-R1-P1: the shared routing view of the configured fallbacks
    (model_transition.fallback_routing_for_context) - never ``bool(ctx.chain)``."""
    from kriya.workflow.model_transition import fallback_routing_for_context

    return fallback_routing_for_context(state, ctx)


def _request_fallback_for_rejected_authoritative_target(
    state: GenerationState, ctx: "AttemptContext", preserved_files: List[str],
) -> None:
    """The body is kriya/workflow/candidate_transaction.py (E14)."""
    return candidate_transaction._request_fallback_for_rejected_authoritative_target(_candidate_deps(), state, ctx, preserved_files)


def _reject_explanatory_prose(
    state: GenerationState, filepath: str, content: str, baseline: Optional[str],
) -> None:
    """The body is kriya/workflow/candidate_transaction.py (E14)."""
    return candidate_transaction._reject_explanatory_prose(state, filepath, content, baseline)


def _run_contract_feedback_gate(state: GenerationState, ctx: "AttemptContext") -> None:
    """BACKEND-READINESS-V2 slot 3: after the compile gate, run the sealed goal-example cases that target a file this unit
    touched, once, on the coherent candidate. An observed contradiction is a typed RETRIABLE failure carrying the exact failed
    example, expected and observed; anything that observed nothing (runner unavailable) is recorded and never a violation.
    Feedback only: terminal acceptance re-evaluates every example on the final tree and nothing here is ledger evidence."""
    feedback = getattr(ctx, "contract_feedback", None)
    if feedback is None:
        return
    outcome = contract_feedback.evaluate(feedback, sorted(state.all_files_written))
    state.record_event(RunEvent(
        kind="contract_feedback.evaluated", attempt=state.attempt_number, source="attempt._run_contract_feedback_gate",
        authority=EventAuthority.AUXILIARY,
        message=f"intermediate contract check: {outcome.status} ({len(outcome.relevant)} of {outcome.compiled_examples} examples relevant)",
        details=outcome.summary(),
    ))
    if outcome.status != "VIOLATED":
        return
    digest = getattr(feedback.artifact, "digest", "")
    message = contract_feedback.violation_message(outcome, digest)
    files = sorted({v.example.source_path for v in outcome.violations if v.example.source_path})
    failure = Failure(
        type=contract_feedback.FAILURE_TYPE, message=message, raw_output=message,
        file_locations=[FileLocation(filepath=path) for path in files], likely_files=files,
        failed_content=_capture_failed_content(ctx.worktree_path, files), attempt=state.attempt_number,
        source="orchestrator", authority="deterministic",
        diagnostics=contract_feedback.violation_diagnostics(outcome, digest),
    )
    state.record_event(RunEvent(
        kind="contract_feedback.violation", attempt=state.attempt_number, source="attempt._run_contract_feedback_gate",
        authority=EventAuthority.ADVISORY, message=f"{contract_feedback.DETERMINISTIC_CONTRACT_VIOLATION}: "
        f"{len(outcome.violations)} goal example(s) contradicted", details=failure.diagnostics,
    ))
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _reject_exception_masking(
    state: GenerationState, filepath: str, content: str, baseline: Optional[str],
) -> None:
    """The body is kriya/workflow/candidate_transaction.py (E14)."""
    return candidate_transaction._reject_exception_masking(state, filepath, content, baseline)


def _preserved_attribution_diagnostics(preserved_files: List[str]) -> Optional[dict]:
    """The body is kriya/workflow/candidate_transaction.py (E14)."""
    return candidate_transaction._preserved_attribution_diagnostics(preserved_files)


def _retry_package_for_attempt(
    state: GenerationState,
    ctx: "AttemptContext",
    *,
    target_files: Optional[List[str]],
    prompt_window: int,
    exclude: Optional[Iterable[str]] = None,
) -> Optional[RetryPackage]:
    """the RetryPackage of this attempt's retry (None without a prior failure). The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return repair_orchestrator._retry_package_for_attempt(_repair_deps(), state, ctx, target_files=target_files, prompt_window=prompt_window, exclude=exclude)


def _estimated_generation_seconds(
    state: GenerationState, *, file_count: int, configured_per_file: int,
    active_model: Optional[str] = None,
) -> float:
    observed = [
        timing["duration_seconds"] / max(1, timing["file_count"])
        for timing in state.generation_timings
        if timing.get("duration_seconds", 0) > 0 and timing.get("file_count", 0) > 0
        and (active_model is None or timing.get("model") == active_model)
    ]
    per_file = statistics.median(observed) if observed else configured_per_file
    return per_file * max(1, file_count)


def _ensure_generation_time_budget(
    state: GenerationState, ctx: "AttemptContext", *, file_count: int,
    active_model: Optional[str] = None,
) -> None:
    autonomy = ctx.kernel.config.autonomy
    budget = autonomy.generation_time_budget_seconds
    if budget is None:
        return
    elapsed = time.monotonic() - state.budget_started_monotonic
    remaining = max(0.0, budget - elapsed)
    estimate = _estimated_generation_seconds(
        state,
        file_count=file_count,
        configured_per_file=autonomy.generation_seconds_per_file_estimate,
        active_model=active_model,
    )
    required = estimate + autonomy.generation_gate_reserve_seconds
    if remaining < required:
        failure = Failure(
            type="time_budget_exhausted",
            source="orchestrator",
            message=(
                "GENERATION TIME BUDGET EXHAUSTED: refusing to start a "
                f"{file_count}-file generation pass with {remaining:.1f}s remaining; "
                f"estimated generation plus gate reserve requires {required:.1f}s."
            ),
            raw_output=(
                f"remaining_seconds={remaining:.1f}; estimated_generation_seconds="
                f"{estimate:.1f}; gate_reserve_seconds="
                f"{autonomy.generation_gate_reserve_seconds}"
            ),
            attempt=state.attempt_number,
        )
        raise QualityGateFailure(failure)


async def _maybe_run_developer_investigation(
    state: GenerationState, ctx: "AttemptContext", kwargs: Dict[str, Any], active_model: str,
) -> None:
    """DEV-INV-001: the opt-in read-only investigation pre-pass. The body is
    kriya/workflow/generation_service.py::_maybe_run_developer_investigation (E6); its collaborators are resolved
    here, at call time (_generation_deps)."""
    return await generation_service._maybe_run_developer_investigation(_generation_deps(), state, ctx, kwargs, active_model)


# A full-file answer is expected to be about the current file's size plus
# room for the change itself.
_FULL_FILE_OUTPUT_GROWTH = 1.1
_FULL_FILE_OUTPUT_SLACK_TOKENS = 256


def _grounded_output_expectations(ctx: "AttemptContext", paths: Optional[Iterable[str]],
                                  binding: Any = None) -> Dict[str, Any]:
    """PRD-016: the output a full-file rewrite of each EXISTING target is
    expected to need, grounded in the file's current size (counted like the
    dispatch check counts, with the runtime's qualified ratio when known).
    New files have no grounding and get none. The Developer applies an
    expectation only to a full-file request, never to an anchored patch."""
    from kriya.core.token_budget import OutputExpectation, count_tokens

    ratio = None
    try:
        from kriya.core.inference_settings import binding_inference_settings
        from kriya.core.model_qualification import measured_limits_for
        from kriya.core.model_runtime import resolve_configured_model_runtime

        target = binding if binding is not None else ctx.kernel.config.llm
        fingerprint = resolve_configured_model_runtime(
            ctx.kernel.config, target.model, base_url=target.base_url, api_key=target.api_key,
            extra_body=target.extra_body or {},
        )
        ratio = measured_limits_for(
            fingerprint, ctx.kernel.config,
            settings=binding_inference_settings(ctx.kernel.config, "developer", target),
        ).get("bytes_per_token_floor")
    except Exception as error:  # the default bound is the conservative fallback
        logger.debug("Output expectations use the default token bound: %s", error)
    expectations: Dict[str, Any] = {}
    for path in paths or ():
        for root in (ctx.worktree_path, ctx.workspace_path):
            full = os.path.join(root, path) if root else None
            if full and os.path.isfile(full):
                break
        else:
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as stream:
                current = count_tokens(stream.read(), qualified_bytes_per_token=ratio).tokens
        except OSError:
            continue
        expected = int(current * _FULL_FILE_OUTPUT_GROWTH) + _FULL_FILE_OUTPUT_SLACK_TOKENS
        expectations[path] = OutputExpectation(
            expected, f"full-file rewrite of {path} (~{current} tokens now)",
        )
    return expectations


def _lower_output_protocol_retry(
    state: GenerationState, ctx: "AttemptContext", kwargs: Dict[str, Any], refusal: Exception,
    active_model: str,
) -> Optional[Dict[str, Any]]:
    """PRD-016 fallback when a full-file answer cannot fit any allowed output
    budget: ask for an anchored patch of that file instead (the existing
    D1 repair_with_patch operation), once. Only for an existing file and a
    model whose capability profile accepts patches; otherwise None (the
    typed OUTPUT_BUDGET_UNSATISFIABLE failure stands)."""
    from kriya.core.model_capabilities import capabilities_for_model

    path = getattr(refusal, "filepath", None)
    if not path or getattr(refusal, "anchored_edit", False) or not _target_exists(ctx, path):
        return None
    protocol = capabilities_for_model(ctx.kernel.config, active_model).preferred_edit_protocol
    if protocol in {"full_file", "full_file_text"}:
        return None
    operations = dict(kwargs.get("operation_by_file") or {})
    operations[path] = CodeOperation.REPAIR_WITH_PATCH
    expectations = dict(kwargs.get("expected_output_by_file") or {})
    expectations.pop(path, None)
    decision = getattr(refusal, "decision", None)
    state.record_event(RunEvent(
        kind="model.output_budget_protocol_fallback",
        attempt=state.attempt_number,
        source="attempt._run_developer_generation",
        authority=EventAuthority.ADVISORY,
        message=f"{path}: the full-file answer does not fit any allowed output budget; requesting an anchored patch.",
        details={"file": path, "reason_code": getattr(refusal, "reason_code", None),
                 "budget": decision.to_dict() if decision is not None else None},
    ))
    logger.warning("Developer: %s cannot be rewritten whole within the output budget; asking for an anchored patch.",
                   path)
    return {**kwargs, "operation_by_file": operations, "expected_output_by_file": expectations}


async def _as_developer(awaitable: Any) -> Any:
    """The body is kriya/workflow/gate_orchestrator.py (E14)."""
    return await gate_orchestrator._as_developer(_gate_deps(), awaitable)


def _developer_request_profile(ctx: "AttemptContext", model_override: Optional[str]) -> Any:
    """PRD-017: the request profile of a Developer call with ``model_override``
    (None = the primary). The body is kriya/workflow/generation_service.py (E6)."""
    return generation_service._developer_request_profile(_generation_deps(), ctx, model_override)


def _patch_required_files(state: GenerationState, ctx: "AttemptContext", kwargs: Dict[str, Any]) -> List[str]:
    """The body is kriya/workflow/candidate_transaction.py (E14)."""
    return candidate_transaction._patch_required_files(_candidate_deps(), state, ctx, kwargs)


def _record_fallback_selection(state: GenerationState, *, phase: str, requested: str, selected: Optional[str],
                               rejected: List[Dict[str, Any]], primary_route: bool = False) -> None:
    """PRD-017: the model.fallback_selection run event. The body is kriya/workflow/fallback_decision.py (E6)."""
    return fallback_decision._record_fallback_selection(_fallback_deps(), state, phase=phase, requested=requested, selected=selected,
                                                   rejected=rejected, primary_route=primary_route)


def _raise_fallback_incompatible(state: GenerationState, requested: str, rejected: List[Dict[str, Any]]) -> None:
    """The terminal typed FALLBACK_MODEL_INCOMPATIBLE failure. The body is kriya/workflow/fallback_decision.py (E6)."""
    return fallback_decision._raise_fallback_incompatible(_fallback_deps(), state, requested, rejected)


def _select_developer_fallback(state: GenerationState, ctx: "AttemptContext", retry_count: int, *,
                              targets: Iterable[str] = (), allow_primary: bool = False) -> Any:
    """PRD-017 / LR-R1-P1: the llm_chain fallback this attempt escalates to (None for the primary; the typed terminal
    failure when no configured fallback remains). The body is kriya/workflow/fallback_decision.py (E6); its
    collaborators are resolved here, at call time (_fallback_deps)."""
    return fallback_decision._select_developer_fallback(_fallback_deps(), state, ctx, retry_count, targets=targets,
                                                   allow_primary=allow_primary)


def _substitute_for_required_patch(state: GenerationState, ctx: "AttemptContext", kwargs: Dict[str, Any],
                                   profile: Any, reasons: List[str]) -> Tuple[Dict[str, Any], Any]:
    """PRD-017: the next configured fallback that can serve this call. The body is
    kriya/workflow/fallback_decision.py (E6)."""
    return fallback_decision._substitute_for_required_patch(_fallback_deps(), state, ctx, kwargs, profile, reasons)


def _enter_developer_model(state: GenerationState, ctx: "AttemptContext", kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """PRD-017: the model this Developer call goes to, its request profile and the call's kwargs. The body is
    kriya/workflow/generation_service.py (E6); its collaborators are resolved here, at call time."""
    return generation_service._enter_developer_model(_generation_deps(), state, ctx, kwargs)


def _require_qualified_retry_identity(state: GenerationState, ctx: "AttemptContext",
                                      kwargs: Dict[str, Any]) -> None:
    """MODEL-EVIDENCE-HARDENING-001: a production retry identity must be QUALIFIED. The body is
    kriya/workflow/generation_service.py (E6)."""
    return generation_service._require_qualified_retry_identity(_generation_deps(), state, ctx, kwargs)


def _planned_source_context(
    ctx: "AttemptContext", exclude: Any, budget: int = _PLANNED_SOURCE_UNBOUNDED_TOKENS,
) -> str:
    """GRAPHIFY-OVERSIZE-REQUEST-001: the current source of a bounded enforce subtask's planned files. The body is
    kriya/workflow/generation_service.py (E6)."""
    return generation_service._planned_source_context(_generation_deps(), ctx, exclude, budget)


def _developer_optional_sections(
    ctx: "AttemptContext", graph_context: str, graph_exclude: Any, learned_reference: str,
    planned_source: str = "",
) -> Tuple[OptionalSection, ...]:
    """DEVELOPER-PROMPT-FIT-001: the optional sections an attempt branch placed in its Developer context. The body
    is kriya/workflow/generation_service.py (E6)."""
    return generation_service._developer_optional_sections(_generation_deps(), ctx, graph_context, graph_exclude,
                                                       learned_reference, planned_source)


def _target_package_with_goal_member_fallback(
    ctx: "AttemptContext", paths: List[str], limit: int, prompt_window: int, member_hints: Dict[str, List[str]],
) -> Tuple[str, Any, Optional[Dict[str, Any]]]:
    """KNOWN-TARGET-GOAL-NAMED-MEMBER-HINT-001: the attempt-1 known-target package. The body is
    kriya/workflow/generation_service.py (E6)."""
    return generation_service._target_package_with_goal_member_fallback(_generation_deps(), ctx, paths, limit, prompt_window,
                                                                    member_hints)


def _target_package_with_window_reserve(
    ctx: "AttemptContext", paths: List[str], limit: int, prompt_window: int, member_hints: Dict[str, List[str]],
) -> Tuple[str, Any]:
    """CONTEXT-EDIT-PROTOCOL-001: the known-target package with the exact-window reserve held back when needed. The
    body is kriya/workflow/generation_service.py (E6)."""
    return generation_service._target_package_with_window_reserve(_generation_deps(), ctx, paths, limit, prompt_window,
                                                              member_hints)


def _code_intelligence_for(ctx: "AttemptContext") -> Any:
    """The body is kriya/workflow/candidate_transaction.py (E14)."""
    return candidate_transaction._code_intelligence_for(_candidate_deps(), ctx)


def _stop_on_environment_gate_result(state: GenerationState, result: Any, gate: str) -> None:
    """The body is kriya/workflow/gate_orchestrator.py (E14)."""
    return gate_orchestrator._stop_on_environment_gate_result(state, result, gate)


def _edit_capability_loci(
    state: GenerationState, ctx: "AttemptContext", path: str, lines: List[str], *,
    revision: Optional[str] = None, raw_digest: Optional[str] = None,
) -> List[int]:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._edit_capability_loci(_candidate_deps(), state, ctx, path, lines, revision=revision, raw_digest=raw_digest)


def _remember_anchor_loci(state: GenerationState, path: str, edits: List[Dict[str, str]], current: str) -> None:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._remember_anchor_loci(_candidate_deps(), state, path, edits, current)


def _declares_test_verification(required_verification: Iterable[Dict[str, Any]]) -> bool:
    """The body is kriya/workflow/gate_orchestrator.py (E14)."""
    return gate_orchestrator._declares_test_verification(required_verification)


def _only_response_validity_failures(state: GenerationState) -> bool:
    """The body is kriya/workflow/candidate_transaction.py (E10)."""
    return candidate_transaction._only_response_validity_failures(state)


def _record_authority_snapshot(state: GenerationState, ctx: "AttemptContext", kwargs: Dict[str, Any],
                               capabilities: Dict[str, Any], requested: Dict[str, Any], model: str) -> None:
    """LR-R1-M1 ``authority.snapshot`` of one Developer invocation. The body is kriya/workflow/decision_evidence.py (E10); its collaborators are resolved here, at call time (_evidence_deps)."""
    return decision_evidence._record_authority_snapshot(_evidence_deps(), state, ctx, kwargs, capabilities, requested, model)


def _decide_edit_capabilities(state: GenerationState, ctx: "AttemptContext", kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._decide_edit_capabilities(_candidate_deps(), state, ctx, kwargs)


def _structural_insertion_locus(state: GenerationState, ctx: "AttemptContext", path: str, content: str) -> Any:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._structural_insertion_locus(_candidate_deps(), state, ctx, path, content)


def _requested_operation(kwargs: Dict[str, Any], path: str) -> Optional[str]:
    """The body is kriya/workflow/candidate_transaction.py (E10)."""
    return candidate_transaction._requested_operation(kwargs, path)


def _current_edit_capability(state: Optional[GenerationState], path: str) -> Any:
    """The body is kriya/workflow/candidate_transaction.py (E10)."""
    return candidate_transaction._current_edit_capability(state, path)


def _record_edit_protocol_failure(state: GenerationState, path: str, family: str) -> None:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._record_edit_protocol_failure(_candidate_deps(), state, path, family)


def _authorize_anchors(state: GenerationState, path: str, edits: List[Dict[str, str]], current: str) -> None:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._authorize_anchors(_candidate_deps(), state, path, edits, current)


def _authorize_structural_insertion(
    state: GenerationState, capability: Any, path: str, index: int, edit: Dict[str, str], current: str,
) -> None:
    """The body is kriya/workflow/candidate_transaction.py (E10); its collaborators are resolved here, at call time (_candidate_deps)."""
    return candidate_transaction._authorize_structural_insertion(_candidate_deps(), state, capability, path, index, edit, current)


def _sent_exact_source_covers(state: GenerationState, capability: Any, path: str, search: str, current: str) -> bool:
    """The body is kriya/workflow/candidate_transaction.py (E10)."""
    return candidate_transaction._sent_exact_source_covers(state, capability, path, search, current)


async def _run_developer_generation(
    state: GenerationState, ctx: "AttemptContext", **kwargs,
) -> List[Dict[str, str]]:
    """Every Developer generation goes through here; its model calls are
    attributed to the developer role (PRD-018). The body is
    kriya/workflow/generation_service.py::_run_developer_generation (E6); its collaborators are resolved here, at call
    time (_generation_deps), so a patch of any of them on this module reaches the moved generation."""
    return await generation_service._run_developer_generation(_generation_deps(), state, ctx, **kwargs)


async def _run_developer_generation_as_developer(
    state: GenerationState, ctx: "AttemptContext", **kwargs,
) -> List[Dict[str, str]]:
    """The generation itself (inside the developer role). The body is kriya/workflow/generation_service.py (E6)."""
    return await generation_service._run_developer_generation_as_developer(_generation_deps(), state, ctx, **kwargs)


def _load_model_role() -> Any:
    # E6: generation_service._run_developer_generation's function-local import (core.role_metrics: all-import cycle,
    # non-public PROVIDER), done where the body imported it; the loaders below likewise.
    from kriya.core.role_metrics import model_role
    return model_role


def _load_resolve_request_profile() -> Any:
    from kriya.workflow.model_transition import resolve_request_profile
    return resolve_request_profile


def _load_model_entry() -> Tuple[Any, Any]:
    from kriya.workflow.model_transition import fallback_incompatibilities, profile_changes
    return fallback_incompatibilities, profile_changes


def _load_retry_identity() -> Tuple[Any, Any]:
    from kriya.core.model_qualification import QUALIFIED
    from kriya.workflow.model_transition import RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED
    return QUALIFIED, RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED


def _load_resolve_model_capability_profile() -> Any:
    from kriya.core.model_capabilities import resolve_model_capability_profile
    return resolve_model_capability_profile


def _load_investigation() -> Tuple[Any, Any, Any]:
    from kriya.workflow.investigation import (
        InvestigationDependencies,
        render_investigation_evidence,
        run_investigation_loop,
    )
    return InvestigationDependencies, render_investigation_evidence, run_investigation_loop


def _load_investigation_search() -> Tuple[Any, Any, Any, Any]:
    from kriya.memory.embedding import configured_client, run_deadline
    from kriya.memory.vector import LocalVectorStore
    from kriya.workflow.graph_retrieval import semantic_query_embedding
    return configured_client, run_deadline, LocalVectorStore, semantic_query_embedding


def _load_fallback_model_incompatible() -> str:
    from kriya.workflow.model_transition import FALLBACK_MODEL_INCOMPATIBLE
    return FALLBACK_MODEL_INCOMPATIBLE


def _load_fallback_selection() -> Tuple[Any, Any, Any]:
    from kriya.workflow.model_transition import (
        existing_target_sizes,
        fallback_incompatibilities,
        patch_only_files,
    )
    return existing_target_sizes, fallback_incompatibilities, patch_only_files


def _load_fallback_incompatibilities() -> Any:
    from kriya.workflow.model_transition import fallback_incompatibilities
    return fallback_incompatibilities


def _generation_deps() -> "generation_service.GenerationDeps":
    """The moved generation code's collaborators (kriya/workflow/generation_service.py), resolved through this
    module NOW (at call time), so a test patch of any of these names here (``_enter_developer_model``,
    ``_require_qualified_retry_identity``, ``_ensure_generation_time_budget``, ``_maybe_run_developer_investigation``,
    ``_resolve_known_target_member_hints``, ``_code_intelligence_for``, ``exact_window_reserve``, ``time``, ``logger``,
    ...) reaches the moved code exactly as it reached the bodies when they lived here."""
    return generation_service.GenerationDeps(
        logger=logger, time=time, DeveloperRequestFit=DeveloperRequestFit, OptionalSection=OptionalSection,
        allocation_window=allocation_window, investigation_evidence_char_budget=investigation_evidence_char_budget,
        build_code_context=build_code_context, fenced_reference_section=fenced_reference_section,
        REASON_MINIMUM_AUTHORITY_UNFIT=REASON_MINIMUM_AUTHORITY_UNFIT,
        _reserve_graph_context_budget=_reserve_graph_context_budget,
        build_known_target_context=build_known_target_context, grant_member_hints=grant_member_hints,
        exact_window_reserve=exact_window_reserve, OutputBudgetUnsatisfiableError=OutputBudgetUnsatisfiableError,
        prompt_composition=prompt_composition, _chain_binding=_chain_binding,
        _dependency_graph_db_path=_dependency_graph_db_path, _escalation_authorized_paths=_escalation_authorized_paths,
        _preserve_member_exact_precision=_preserve_member_exact_precision,
        _record_authority_expansions=_record_authority_expansions,
        _resolve_known_target_member_hints=_resolve_known_target_member_hints,
        _resolve_retry_member_hints=_resolve_retry_member_hints, _filtered_candidates=_filtered_candidates,
        _planned_source_context=_planned_source_context, _goal_named_member_hints=_goal_named_member_hints,
        _target_package_with_window_reserve=_target_package_with_window_reserve,
        _code_intelligence_for=_code_intelligence_for, _decide_edit_capabilities=_decide_edit_capabilities,
        _ensure_generation_time_budget=_ensure_generation_time_budget, _enter_developer_model=_enter_developer_model,
        _grounded_output_expectations=_grounded_output_expectations,
        _lower_output_protocol_retry=_lower_output_protocol_retry,
        _maybe_run_developer_investigation=_maybe_run_developer_investigation,
        _require_qualified_retry_identity=_require_qualified_retry_identity,
        _run_developer_generation_as_developer=_run_developer_generation_as_developer,
        _developer_request_profile=_developer_request_profile, _patch_required_files=_patch_required_files,
        _substitute_for_required_patch=_substitute_for_required_patch,
        load_model_role=_load_model_role, load_resolve_request_profile=_load_resolve_request_profile,
        load_model_entry=_load_model_entry, load_retry_identity=_load_retry_identity,
        load_resolve_model_capability_profile=_load_resolve_model_capability_profile,
        load_investigation=_load_investigation, load_investigation_search=_load_investigation_search,
    )


def _fallback_deps() -> "fallback_decision.FallbackDeps":
    """The moved fallback decisions' collaborators (kriya/workflow/fallback_decision.py), resolved through this
    module NOW (at call time), exactly as the bodies resolved them when they lived here."""
    return fallback_decision.FallbackDeps(
        logger=logger, resolve_fallback_model=resolve_fallback_model,
        _developer_request_profile=_developer_request_profile, _chain_binding=_chain_binding,
        _patch_required_files=_patch_required_files, _fallback_rejection=_fallback_rejection,
        _record_fallback_selection=_record_fallback_selection, _raise_fallback_incompatible=_raise_fallback_incompatible,
        load_fallback_model_incompatible=_load_fallback_model_incompatible,
        load_fallback_selection=_load_fallback_selection, load_fallback_incompatibilities=_load_fallback_incompatibilities,
    )


def _repair_deps() -> "repair_orchestrator.RepairDeps":
    """The moved retry/repair code's collaborators (kriya/workflow/repair_orchestrator.py), resolved through this
    module NOW (at call time), so a test patch of any of these names here (``_run_developer_generation``,
    ``_resolve_known_target_member_hints``, ``_developer_request_profile``, ``apply_anchored_edits``, ...) reaches the
    moved code exactly as it reached the bodies when they lived here."""
    return repair_orchestrator.RepairDeps(
        CurrentSourceResolver=CurrentSourceResolver, member_boundaries_for=member_boundaries_for,
        member_ids_matching_name=member_ids_matching_name,
        evaluate_member_hints_from_search_evidence=evaluate_member_hints_from_search_evidence,
        resolve_member_hints_from_failure_location=resolve_member_hints_from_failure_location,
        role_binding_for_model=role_binding_for_model, grant_member_hints=grant_member_hints,
        _reserve_graph_context_budget=_reserve_graph_context_budget, developer_reference=developer_reference,
        retry_evidence_char_budget=retry_evidence_char_budget,
        _reserve_sibling_content_budget=_reserve_sibling_content_budget, allocation_window=allocation_window,
        build_retry_package=build_retry_package, _build_coordinated_retry_prompt=_build_coordinated_retry_prompt,
        DeveloperAgent=DeveloperAgent, apply_anchored_edits=apply_anchored_edits,
        _validated_member_hints=_validated_member_hints, _goal_named_member_hints=_goal_named_member_hints,
        _merge_goal_named_member_hints=_merge_goal_named_member_hints,
        _resolve_retry_member_hints=_resolve_retry_member_hints,
        _resolve_known_target_member_hints=_resolve_known_target_member_hints,
        _record_retry_projection_context_items=_record_retry_projection_context_items,
        _preserve_member_exact_precision=_preserve_member_exact_precision,
        _target_source_record=_target_source_record,
        _compute_retry_evidence_fingerprint=_compute_retry_evidence_fingerprint,
        _effective_retry_temperature=_effective_retry_temperature,
        _escalation_authorized_paths=_escalation_authorized_paths,
        _record_authority_expansions=_record_authority_expansions,
        _record_known_target_package=_record_known_target_package, _record_omitted_members=_record_omitted_members,
        _target_package_with_goal_member_fallback=_target_package_with_goal_member_fallback,
        _target_package_with_window_reserve=_target_package_with_window_reserve,
        _developer_request_profile=_developer_request_profile, _retry_package_for_attempt=_retry_package_for_attempt,
        _materialize_candidate_content=_materialize_candidate_content, _operation_map=_operation_map,
        _run_developer_generation=_run_developer_generation,
    )


def _load_run_self_correction_loop() -> Any:
    # E8: gate_orchestrator's compile gate's function-local import (self_correction: all-import cycle), done where the
    # body imported it; the two loaders below likewise.
    from kriya.workflow.self_correction import run_self_correction_loop
    return run_self_correction_loop


def _load_run_repair_loop() -> Any:
    from kriya.workflow.self_correction import run_repair_loop
    return run_repair_loop


def _load_verification_inputs_digest() -> Any:
    from kriya.workflow.retry_strategy import verification_inputs_digest
    return verification_inputs_digest


def _gate_deps() -> "gate_orchestrator.GateDeps":
    """The moved gate code's collaborators (kriya/workflow/gate_orchestrator.py), resolved through this module NOW
    (at call time), so a test patch of any of these names here (``extract_target_test``, ``find_runnable_test_files``,
    ``_check_java_toolchain_mismatch``, ``_resolve_java_home_override``, ``_judge_candidate_suite``,
    ``_approve_and_execute_runtime_verification``, ``_execute_managed_service_verification``, ``time``, ...) reaches
    the moved gates exactly as it reached the bodies when they lived here."""
    return gate_orchestrator.GateDeps(
        logger=logger, time=time, SCOPE_CANDIDATE_GATE=SCOPE_CANDIDATE_GATE,
        SCOPE_VERIFICATION_UNIT=SCOPE_VERIFICATION_UNIT, request_capacity=request_capacity,
        load_run_self_correction_loop=_load_run_self_correction_loop, load_run_repair_loop=_load_run_repair_loop,
        load_verification_inputs_digest=_load_verification_inputs_digest,
        extract_target_test=extract_target_test, find_runnable_test_files=find_runnable_test_files,
        _check_java_toolchain_mismatch=_check_java_toolchain_mismatch,
        _resolve_java_home_override=_resolve_java_home_override, _judge_candidate_suite=_judge_candidate_suite,
        _approve_and_execute_runtime_verification=_approve_and_execute_runtime_verification,
        _execute_managed_service_verification=_execute_managed_service_verification,
        _PROCESS_BOUNDARY_RECURRENCE_ESCALATION=_PROCESS_BOUNDARY_RECURRENCE_ESCALATION, _as_developer=_as_developer,
        _build_java_main_class_map=_build_java_main_class_map, _build_java_package_map=_build_java_package_map,
        _build_python_runtime_grounding=_build_python_runtime_grounding,
        _build_workspace_type_index=_build_workspace_type_index,
        _close_requirements_by_migration_gate=_close_requirements_by_migration_gate,
        _ctx_toolchain_declaration_mutable=_ctx_toolchain_declaration_mutable,
        _declares_test_verification=_declares_test_verification,
        _downgrade_suppressed_requirement_claims=_downgrade_suppressed_requirement_claims,
        _execute_runtime_verification_directly=_execute_runtime_verification_directly,
        _goal_spec_evidence_fingerprint=_goal_spec_evidence_fingerprint,
        _goal_spec_requirement_obligation_id=_goal_spec_requirement_obligation_id,
        _migration_obligations_all_satisfied=_migration_obligations_all_satisfied,
        _raise_if_required_runtime_verification_declined=_raise_if_required_runtime_verification_declined,
        _raise_unexecuted_test_delta=_raise_unexecuted_test_delta,
        _raise_ungrounded_child_process_test_candidate=_raise_ungrounded_child_process_test_candidate,
        _raise_unsafe_process_boundary_test_candidate=_raise_unsafe_process_boundary_test_candidate,
        _record_original_requirement_verdicts=_record_original_requirement_verdicts,
        _record_process_boundary_obligation=_record_process_boundary_obligation,
        _record_self_correction_scope_conflict=_record_self_correction_scope_conflict,
        _required_runtime_verification_missing_message=_required_runtime_verification_missing_message,
        _resolve_execution_mode=_resolve_execution_mode, _run_contract_feedback_gate=_run_contract_feedback_gate,
        _run_verification_basis_hash=_run_verification_basis_hash,
        _runtime_verification_is_advisory_only=_runtime_verification_is_advisory_only,
        _settled_goal_spec_requirement=_settled_goal_spec_requirement,
        _spec_compliance_authoritative_context=_spec_compliance_authoritative_context,
        _spec_requirements_contradicting_authority=_spec_requirements_contradicting_authority,
        _spec_requirements_naming_planner_only_identifiers=_spec_requirements_naming_planner_only_identifiers,
        _stage_scoped_spec_compliance_goal=_stage_scoped_spec_compliance_goal,
        _stop_on_environment_gate_result=_stop_on_environment_gate_result,
        assert_candidate_unchanged_since_source_review=assert_candidate_unchanged_since_source_review,
        _apply_candidate_pom_corrections=_apply_candidate_pom_corrections,
        _record_candidate_change=_record_candidate_change,
        _require_worktree_matches_candidate=_require_worktree_matches_candidate,
        _run_pom_validation_gate=_run_pom_validation_gate, _new_candidate_validator=_new_candidate_validator,
        _resolve_candidate_toolchain=_resolve_candidate_toolchain,
        _run_executing_candidate_gates=_run_executing_candidate_gates,
        SCOPE_TEST_DELTA_COVERING_RUN=SCOPE_TEST_DELTA_COVERING_RUN, test_execution=test_execution,
        load_dependency_graph=_load_dependency_graph, load_model_role=_load_model_role,
        _attribute_candidate_suite=_attribute_candidate_suite, _changed_test_sources=_changed_test_sources,
        _dependency_graph_db_path=_dependency_graph_db_path,
        _extract_class_names_best_effort=_extract_class_names_best_effort,
        _process_boundary_obligation_id=_process_boundary_obligation_id,
        _runtime_contract_requirements=_runtime_contract_requirements,
        _sync_active_repair_contract=_sync_active_repair_contract,
        find_in_process_terminating_test_invocations=find_in_process_terminating_test_invocations,
        find_ungrounded_java_child_process_tests=find_ungrounded_java_child_process_tests,
    )


def _load_is_local_url() -> Any:
    # E9: runtime_verification's function-local imports (core.llm, skills.skill: all-import cycle), done where the bodies
    # imported them; the self-correction loop reuses E8's _load_run_self_correction_loop.
    from kriya.core.llm import is_local_url
    return is_local_url


def _load_held_skill_verification() -> Any:
    from kriya.skills.skill import HeldSkillVerification
    return HeldSkillVerification


def _runtime_deps() -> "runtime_verification.RuntimeDeps":
    """The moved runtime-verification code's collaborators (kriya/workflow/runtime_verification.py), resolved through this
    module NOW (at call time), so a test patch of any of these names here (``run_managed_service_verification``,
    ``_resolve_run_command``, ``_approve_runtime_commands``, ``_prepare_finite_command_runtime_artifacts``,
    ``_check_java_toolchain_mismatch``, ``_resolve_java_home_override``, ``time``, ``logger``, ...) reaches the moved code
    exactly as it reached the bodies when they lived here."""
    return runtime_verification.RuntimeDeps(
        logger=logger, time=time, run_managed_service_verification=run_managed_service_verification,
        _resolve_run_command=_resolve_run_command, _check_java_toolchain_mismatch=_check_java_toolchain_mismatch,
        _resolve_java_home_override=_resolve_java_home_override, _approve_runtime_commands=_approve_runtime_commands,
        _prepare_finite_command_runtime_artifacts=_prepare_finite_command_runtime_artifacts,
        _validate_and_convert_managed_service_contract=_validate_and_convert_managed_service_contract,
        _execute_managed_service_verification=_execute_managed_service_verification,
        ManagedServiceVerificationSpec=ManagedServiceVerificationSpec, ProbeSpec=ProbeSpec,
        ReadinessSpec=ReadinessSpec, ServiceVerificationOutcomeKind=ServiceVerificationOutcomeKind,
        _prepare_required_artifact=_prepare_required_artifact,
        _MANAGED_SERVICE_INFRASTRUCTURE_OUTCOMES=_MANAGED_SERVICE_INFRASTRUCTURE_OUTCOMES,
        _MANAGED_SERVICE_OUTCOMES_WITH_CAPTURED_APPLICATION_OUTPUT=_MANAGED_SERVICE_OUTCOMES_WITH_CAPTURED_APPLICATION_OUTPUT,
        _PYTHON_RUNTIME_GROUNDING_EXCLUDED_DIRS=_PYTHON_RUNTIME_GROUNDING_EXCLUDED_DIRS,
        RetainedRuntimeEvidence=RetainedRuntimeEvidence, apply_runtime_disposition=apply_runtime_disposition,
        runtime_evidence_outcome_fields=runtime_evidence_outcome_fields,
        _skill_verification_context=_skill_verification_context, request_capacity=request_capacity,
        load_is_local_url=_load_is_local_url, load_run_self_correction_loop=_load_run_self_correction_loop,
        load_held_skill_verification=_load_held_skill_verification,
        _apply_candidate_pom_corrections=_apply_candidate_pom_corrections, _as_developer=_as_developer,
        _bind_verification_tree=_bind_verification_tree, _build_java_main_class_map=_build_java_main_class_map,
        _record_self_correction_scope_conflict=_record_self_correction_scope_conflict,
        _stop_on_environment_gate_result=_stop_on_environment_gate_result,
        assert_candidate_unchanged_since_source_review=assert_candidate_unchanged_since_source_review,
        exit_authority_text=exit_authority_text,
        _apply_runtime_verification_contract=_apply_runtime_verification_contract,
        _build_python_runtime_grounding=_build_python_runtime_grounding,
        _classify_grounded_contract_verdict=_classify_grounded_contract_verdict,
        _collect_python_runtime_grounding_facts=_collect_python_runtime_grounding_facts,
        _derive_runtime_command_subject=_derive_runtime_command_subject,
        _deterministic_result_provenance_field=_deterministic_result_provenance_field,
        _looks_like_shell_compound_command=_looks_like_shell_compound_command,
        _managed_service_blocked_without_approval_subject=_managed_service_blocked_without_approval_subject,
        _raise_candidate_runtime_entrypoint_failure=_raise_candidate_runtime_entrypoint_failure,
        _raise_if_required_runtime_verification_declined=_raise_if_required_runtime_verification_declined,
        _raise_runtime_verification_infrastructure_failure=_raise_runtime_verification_infrastructure_failure,
        _reject_non_local_managed_service_host=_reject_non_local_managed_service_host,
        _required_runtime_verification_missing_message=_required_runtime_verification_missing_message,
        _resolve_execution_mode=_resolve_execution_mode,
        _resolve_runtime_verification_grade=_resolve_runtime_verification_grade,
        _run_verification_basis_hash=_run_verification_basis_hash,
        _runtime_commands_need_approval=_runtime_commands_need_approval,
        _runtime_verification_is_advisory_only=_runtime_verification_is_advisory_only,
        _validate_and_convert_probe=_validate_and_convert_probe,
        _validate_and_convert_readiness=_validate_and_convert_readiness,
    )


def _evidence_deps() -> "decision_evidence.EvidenceDeps":
    """The moved evidence recorders' collaborators (kriya/workflow/decision_evidence.py), resolved through this
    module NOW (at call time), so a test patch of ``logger`` or ``_record_obligations_snapshot`` here reaches the
    moved code exactly as it reached the bodies when they lived here."""
    return decision_evidence.EvidenceDeps(
        logger=logger, _record_obligations_snapshot=_record_obligations_snapshot,
    )


def _spec_deps() -> "spec_compliance.SpecDeps":
    """The moved spec-compliance code's collaborators (kriya/workflow/spec_compliance.py), resolved through this
    module NOW (at call time), so a test patch of any of these names here (``logger``, the moved siblings, the
    restoration write, ...) reaches the moved code exactly as it reached the bodies when they lived here."""
    return spec_compliance.SpecDeps(
        logger=logger, CodeOperation=CodeOperation, classify_result_operation=classify_result_operation,
        _extract_requirement_identifier_tokens=_extract_requirement_identifier_tokens,
        _identifier_local_terms=_identifier_local_terms,
        _migration_obligations_all_satisfied=_migration_obligations_all_satisfied,
        _structured_plan_paths=_structured_plan_paths, _baseline_restoration_write=_baseline_restoration_write,
    )


def _load_allocation_window() -> Any:
    # E10: candidate_transaction's function-local imports (edit_capability, context_budget, insertion_locus:
    # all-import cycle), done where the bodies imported them; the loaders below likewise.
    from kriya.workflow.context_budget import allocation_window
    return allocation_window


def _load_anchor_statuses() -> Any:
    from kriya.workflow.edit_capability import ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT, INSERTION_ONLY
    return ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT, INSERTION_ONLY


def _load_capability_decision() -> Any:
    from kriya.workflow.edit_capability import (
        ANCHOR_CONTEXT_NOT_ESCALATED,
        CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE,
        build_edit_capability,
        exact_window_reserve,
        render_exact_spans,
        shown_exact_texts,
    )
    return (
        ANCHOR_CONTEXT_NOT_ESCALATED, CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE, build_edit_capability, exact_window_reserve,
        render_exact_spans, shown_exact_texts,
    )


def _load_capability_loci() -> Any:
    from kriya.workflow.edit_capability import goal_code_fragments, locate_fragments
    return goal_code_fragments, locate_fragments


def _load_locate_search_text() -> Any:
    from kriya.workflow.edit_capability import locate_search_text
    return locate_search_text


def _load_resolve_insertion_locus() -> Any:
    from kriya.workflow.insertion_locus import resolve_insertion_locus
    return resolve_insertion_locus


def _load_verify_insertion() -> Any:
    from kriya.workflow.insertion_locus import verify_insertion
    return verify_insertion


def _load_open_code_intelligence() -> Any:
    # E14: candidate_transaction._code_intelligence_for's function-local import (graph_retrieval: all-import cycle),
    # done where the body imported it.
    from kriya.workflow.graph_retrieval import open_code_intelligence
    return open_code_intelligence


def _candidate_deps() -> "candidate_transaction.CandidateDeps":
    """The moved candidate-staging code's collaborators (kriya/workflow/candidate_transaction.py), resolved
    through this module NOW (at call time: a forwarder's call, each transaction phase), so a test patch of any of
    these names here (``apply_anchored_edits``, ``_reject_exception_masking``, ``_code_intelligence_for``,
    ``logger``, ...) reaches the moved code exactly as it reached the bodies when they lived here."""
    return candidate_transaction.CandidateDeps(
        logger=logger, _code_intelligence_for=_code_intelligence_for,
        _reject_exception_masking=_reject_exception_masking, apply_anchored_edits=apply_anchored_edits,
        APIContractRecoveryPhase=APIContractRecoveryPhase, CodeOperation=CodeOperation,
        find_edits_ignoring_own_diagnosis=find_edits_ignoring_own_diagnosis,
        find_edits_ignoring_reported_line=find_edits_ignoring_reported_line,
        find_misdirected_edit_target=find_misdirected_edit_target, find_whole_response_no_op=find_whole_response_no_op,
        load_allocation_window=_load_allocation_window, load_anchor_statuses=_load_anchor_statuses,
        load_capability_decision=_load_capability_decision, load_capability_loci=_load_capability_loci,
        load_locate_search_text=_load_locate_search_text, load_resolve_insertion_locus=_load_resolve_insertion_locus,
        load_verify_insertion=_load_verify_insertion, operation_for_file=operation_for_file,
        validate_operation_result=validate_operation_result, ActionRequest=ActionRequest, ActionType=ActionType,
        PolicyDecision=PolicyDecision, PolicyDeniedError=PolicyDeniedError, PolicyResult=PolicyResult,
        _authorize_anchors=_authorize_anchors, _authorize_structural_insertion=_authorize_structural_insertion,
        _capture_original=_capture_original, _completeness_gated_operation=_completeness_gated_operation,
        _current_edit_capability=_current_edit_capability, _current_revision=_current_revision,
        _edit_capability_loci=_edit_capability_loci, _has_authoritative_full_source=_has_authoritative_full_source,
        _is_restore_public_contract_phase=_is_restore_public_contract_phase,
        _record_edit_protocol_failure=_record_edit_protocol_failure, _remember_anchor_loci=_remember_anchor_loci,
        _requested_operation=_requested_operation, _sent_exact_source_covers=_sent_exact_source_covers,
        _structural_insertion_locus=_structural_insertion_locus,
        _validate_actual_mutation_authority=_validate_actual_mutation_authority,
        _build_workspace_type_index=_build_workspace_type_index, _chain_binding=_chain_binding,
        _dependency_graph_db_path=_dependency_graph_db_path,
        _diagnosis_mismatch_bypass_reason=_diagnosis_mismatch_bypass_reason,
        _extract_class_names_best_effort=_extract_class_names_best_effort, _file_integrity_stop=_file_integrity_stop,
        _preserved_attribution_diagnostics=_preserved_attribution_diagnostics,
        _preserved_authoritative_locator_files=_preserved_authoritative_locator_files,
        _record_authority_snapshot=_record_authority_snapshot, _reject_explanatory_prose=_reject_explanatory_prose,
        _request_fallback_for_rejected_authoritative_target=_request_fallback_for_rejected_authoritative_target,
        _target_exists=_target_exists,
        _fallback_routing=_fallback_routing, load_open_code_intelligence=_load_open_code_intelligence,
    )



def _load_dependency_graph() -> Any:
    # E14: process_boundary's / gate_orchestrator's function-local kriya.analyzer.graph import (a non-public RETRIEVAL
    # module: the allowlisted import-direction edge stays counted on attempt.py), done where the bodies imported it.
    from kriya.analyzer.graph import DependencyGraph
    return DependencyGraph


def _process_boundary_deps() -> "process_boundary.ProcessBoundaryDeps":
    """The moved process-boundary code's collaborators (kriya/workflow/process_boundary.py), resolved through this
    module NOW (at call time), so a test patch of any of these names here (``logger``, the moved siblings' forwarders)
    reaches the moved code exactly as it reached the bodies when they lived here."""
    return process_boundary.ProcessBoundaryDeps(
        logger=logger, load_dependency_graph=_load_dependency_graph,
        _dependency_graph_db_path=_dependency_graph_db_path, _enclosing_test_method=_enclosing_test_method,
        _enclosing_test_method_source=_enclosing_test_method_source,
        _find_java_main_class_best_effort=_find_java_main_class_best_effort,
        _required_process_terminating_cases=_required_process_terminating_cases,
        _resolve_java_main_argv_expression=_resolve_java_main_argv_expression,
        _runtime_contract_expects_numeric_input=_runtime_contract_expects_numeric_input,
        _terminating_case_from_argv_expression=_terminating_case_from_argv_expression,
    )


@dataclass
class AttemptContext:
    """The retry loop's read-only, loop-invariant closure captures - built
    once before the while loop starts (nothing in this range is reassigned
    across iterations), passed into run_attempt() (this module) and
    kriya.workflow.retry_strategy.handle_attempt_failure() alike - both
    operate on the same attempt/failure cycle, just its two different
    halves, so they share one context object rather than each inventing
    their own overlapping one."""

    goal: str
    plan: str
    design: str
    workspace_path: str
    worktree_path: str
    architect_files: List[str]
    resume_state: Optional[Dict[str, Any]]
    run_id: str
    skills_prompt: str
    learned_rag_context: str
    matched_files: Any
    related_files: Any
    ecosystem_invariant_block: str
    resource_lifecycle_block: str
    verification_contract_block: str
    # Recovery Execution Contract (PRV-06, 2026-08-29) - the MA8.1 owner-
    # recovery MUST_FIX/MUST_PRESERVE/EVIDENCE/ACCEPTANCE text (or the
    # simpler consumer_retry "upstream recovery completed" note), threaded
    # here as its OWN field rather than folded into skills_prompt/
    # supplementary_context - see run_generation_workflow's own
    # recovery_contract_block docstring (workflow.py) for the live incident
    # this closes: that shared accumulator is documented, deliberately, as
    # passive cross-agent reference material, exactly what a recovery
    # requirement is NOT. Empty string for every non-recovery invocation.
    recovery_contract_block: str
    required_files_prompt_block: str
    required_dependencies_prompt_block: str
    expected_files_upfront: List[str]
    architect_basename_to_path: Dict[str, str]
    chain: list
    targeted_max_retries: int
    stream_callback: Optional[Callable[[str, str], None]]
    approval_callback: Optional[Callable[[List[Dict[str, str]], str], Any]]
    active_skills: List[str]
    active_skill_rules_snapshot: Dict[str, Any]
    developer: DeveloperAgent
    run_verifier: Any
    spec_compliance: Any
    skill_engine: Any
    kernel: Kernel
    # web_lookup_query_callback/approve_web_lookup are only read by
    # handle_attempt_failure(), not run_attempt() itself; max_retries is read
    # by both (run_attempt()'s own decide_retry_action() mode-selection call,
    # and handle_attempt_failure()'s decide_for_state() continue/stop check) -
    # kept on the same context object anyway (see the class docstring) rather
    # than a second, mostly-overlapping dataclass.
    max_retries: int
    web_lookup_query_callback: Optional[Callable[[List[str], str], Any]]
    # A bound method (WorkflowEngine._approve_web_lookup), not a free
    # function - already carries its own `self` reference, so it's just
    # another callable from this module's perspective.
    approve_web_lookup: Callable[..., Any]
    # CORR-018-P1 (A3-bound slice, 2026-09-09): explicit, deterministic
    # semantic-region authority for Java files (kriya/workflow/
    # semantic_region_authority.py) - empty for every existing/non-A3
    # caller (default), which is a full no-op for that guard, byte-for-byte
    # identical to pre-CORR-018 behavior. Not yet populated by any real
    # caller (A3 promotion doesn't exist yet) - present so the guard is
    # wired and testable ahead of that wiring, per explicit instruction.
    authorized_semantic_regions: List[AuthorizedSemanticRegion] = field(default_factory=list)
    # path -> direct manifest dependencies, in generation order. Default keeps
    # isolated tests and old checkpoints backward compatible.
    generation_dependencies: Dict[str, List[str]] = field(default_factory=dict)
    # CTX-001 P1 C2 production integration: path -> candidate (unvalidated)
    # member/class NAMES parsed from this attempt's own attempt-1 Graph-RAG
    # vector hits (workflow.py's retrieval stage, via context_source.py::
    # parse_controlled_chunk_header_name - the controlled "Method: X"/
    # "Class: X" header chunk_file_with_metadata_headers() already writes
    # into every indexed chunk, previously discarded at the retrieval call
    # site). Deliberately CANDIDATES ONLY - run_attempt() is the one place
    # that validates a name against member_boundaries_for() on CURRENT
    # (CurrentSourceResolver-resolved) content before it is ever trusted as
    # a real member_hints entry (see docs/assurance/CTX_001_P1_ARCHITECTURE.
    # md section 25). Default empty dict keeps every existing test/caller
    # that doesn't know about this field unaffected.
    retrieval_member_hints: Dict[str, List[str]] = field(default_factory=dict)
    # CONTEXT-EDIT-PROTOCOL-LARGE-FILE-001: path -> [(declaration line, digest
    # of the bytes it was read from)] of the structural localization
    # candidates (graph_retrieval.GraphRetrievalResult.retrieval_symbol_loci,
    # adopted targets first). Edit loci for _decide_edit_capabilities while
    # the digest still matches the file; never write authority by itself.
    retrieval_symbol_loci: Dict[str, List[Tuple[int, str]]] = field(default_factory=dict)
    # CTX-001 P1 WP9: one small AttemptContext-lifetime cache for
    # deterministic source-derived artifacts (skeleton/signatures/member-
    # exact rendering + their own token estimates), keyed by
    # (path, member_id, tier, revision) - see context_source.py::
    # SourceDerivationCache's own docstring. Built fresh per AttemptContext
    # (default_factory, one instance per real run - never shared across
    # separate `generate` invocations, never persisted). Every context-
    # building call site in this module passes it through so unchanged
    # source units are reused across retries within the SAME attempt;
    # nothing about a cache hit vs. miss ever changes what content is
    # produced, only how much work it costs to produce it.
    source_cache: "SourceDerivationCache" = field(default_factory=SourceDerivationCache)
    # GRAPHIFY-OVERSIZE-REQUEST-001: a bounded enforce subtask's planned
    # files that exist on disk. Their current source reaches the Developer
    # only through _planned_source_context (budgeted, optional), never as
    # verbatim mandatory text. Empty for every other run.
    planned_source_files: Tuple[str, ...] = ()
    # Files known to exist from OUTSIDE this attempt's own generation - for a
    # milestone run, every file an earlier, already-completed milestone wrote
    # (kriya/workflow/milestones.py's MilestoneRunState.established_file_context
    # keys). Deliberately NOT merged into state.all_files_written itself - that
    # field is read by ~30 call sites across this module/workflow.py (compile/
    # test scope, file-count budgeting, final "files written" reporting,
    # missing-files detection...) where "written by THIS attempt" is the
    # correct, load-bearing meaning; widening it would be wrong for most of
    # those. This field exists ONLY to widen the "known files" candidate set
    # self-diagnosis/attribution matching uses (see its two read sites: this
    # module's extract_self_diagnosed_files() call, and retry_strategy.py's
    # attribute_failure() call) - found live, 2026-08-21 (ignite_qpid_protocol,
    # milestone 2/4): the Developer's own FIX ANALYSIS correctly, repeatedly
    # said "the fix requires adding public getter methods to the Protocol
    # class" (milestone 1's file), but Protocol.java was never a candidate for
    # redirect at all, since milestone 2's OWN state.all_files_written only
    # ever contains files ITS OWN attempts wrote (ProtocolParser.java) - a
    # correct diagnosis had structurally nowhere to go, and the retry loop
    # burned its full budget regenerating only ProtocolParser.java, 8 attempts
    # straight. Default empty list keeps plain (non-milestone) generate/fix
    # calls, and any old checkpoint, unaffected.
    established_files: List[str] = field(default_factory=list)
    # Workspace-relative path of the goal-source file supplied via `kriya
    # generate --file <path>` for this run, if any - see AuthorizedFileWriter's
    # own protected_relpaths docstring (kriya/policy/filesystem.py) for the
    # real live incident this exists to prevent. None (the default) means no
    # goal file was supplied this run (goal came from a positional arg,
    # stdin, or a milestone/subtask's own synthesized text) - nothing to
    # protect, matches every existing call site's behavior unchanged.
    protected_relpath: Optional[str] = None
    allowed_write_relpaths: List[str] = field(default_factory=list)
    # Explicit write-scope policy (kriya/policy/filesystem.py::WriteScopeMode)
    # - resolved ONCE, by run_generation_workflow(), before AttemptContext is
    # built, so every write-gate call site in this module reads the SAME
    # unambiguous value instead of each re-inferring its own meaning from
    # allowed_write_relpaths' truthiness (the exact ambiguity that let a
    # verification-only subtask's intended DENY_ALL silently degrade to
    # "no restriction" - found live, PRV-05, 2026-08-28). Defaults to
    # UNRESTRICTED, matching every plain (non-bounded-subtask) generate call
    # and every existing test's own expectations unchanged.
    write_scope_mode: WriteScopeMode = WriteScopeMode.UNRESTRICTED
    # The subtask's own declared verifiers (VerificationMethod.model_dump()
    # dicts) - only consulted when write_scope_mode == DENY_ALL, to execute
    # them directly instead of entering ordinary Developer generation (see
    # run_attempt()'s own verification-only branch, and _run_verification_
    # only_attempt's docstring, for the PRV-05, 2026-08-28 incident this
    # closes: a verification-only subtask used to still enter the Developer
    # mutation/retry pipeline, burning several attempts discovering it had
    # nothing to write before its own required verification ever ran).
    required_verification: List[Dict[str, Any]] = field(default_factory=list)
    # CANDIDATE-GATE-BASELINE-POLICY-001: the baseline's own verification
    # call (run_generation_workflow's baseline_suite_run) - replays the
    # untouched baseline when a disputed test's stability must be measured.
    # None (a caller without a baseline) keeps every suite gate's raw rule.
    baseline_suite_replay: Optional[Callable[[Optional[Tuple[str, ...]]], Dict[str, Any]]] = None
    # ENFORCE-IDENTICAL-WRITE-COMPLETION-001 (review F1): True when the caller
    # decides a zero-change result itself from deterministic evidence (the
    # milestone driver's no_change_verification). False - a direct goal -
    # means nothing downstream judges "changed nothing", so an attempt whose
    # every written file equals the baseline is a typed stop here.
    no_change_verifier_downstream: bool = False
    # BACKEND-READINESS-V2 slot 3: the run's sealed goal-example class and how to run its RELEVANT cases on this unit's
    # candidate (kriya/workflow/contract_feedback.py); None = no deterministic contract is bound to this run/unit.
    contract_feedback: Optional[Any] = None
    # The enforce controller's owner reopen (verification_owner_recovery): read by
    # workflow.py's no-change settlement (an identical regeneration is a stop).
    reopened_owner: bool = False
    runtime_verification_required: bool = False
    strict_spec_compliance: bool = False
    # Gap B (2026-10-15): see defer_execution_until_source_review. The caller that runs the post-approval phase sets the first; run_attempt fills the second (the held
    # approve-and-execute half); the caller sets the third after the person approves the source (True while the candidate bytes are still the approved ones).
    defer_execution_until_source_review_enabled: bool = False
    deferred_executing_gates: Optional[Callable[[], Any]] = None
    post_source_review_guard: Optional[Callable[[], bool]] = None
    # Human-readable identity for nested structured executions. Attempt
    # numbers are local to each bounded run and otherwise collide in logs.
    execution_scope: str = ""
    # Original/global goal used only for deterministic architectural owner
    # discovery when a bounded subtask's local wording omits that context.
    grounding_goal: str = ""
    # PRD-025: the user's own request text - the ONLY text that may declare
    # an expected nonzero exit (see exit_authority_text). Set by
    # run_generation_workflow from the unit invocation; never Planner or
    # MilestonePlanner restatement.
    exit_authority_goal: str = ""
    # The run's ONE, already-resolved MigrationObligation (or explicit
    # NOT_APPLICABLE/INDETERMINATE), resolved ONCE by the top-level caller
    # (run_generation_workflow for a plain/Legacy run, WorkflowController for
    # a Hardened enforce run - both against the immutable PRE-mutation
    # baseline workspace) and threaded through unchanged to every attempt -
    # never re-resolved here. See kriya/workflow/migration.py's own
    # docstring (PRV-05 run 6, 2026-08-28) for why re-deriving source/target
    # identity from whatever state the repository happens to be in at each
    # call site is itself the defect this field exists to close. None means
    # no caller has resolved anything yet (matches every existing test's/
    # checkpoint's default, same as required_verification's own default).
    migration_resolution: Optional["MigrationResolution"] = None
    # PRV-05 run 7 (2026-08-28) - the validated EngineeringPlan and the id of
    # the subtask THIS call is executing, threaded through unchanged from
    # WorkflowController's bounded-subtask execution (kriya/workflow/
    # workflow_controller.py's _invoke_bounded_subtask) so migration
    # validation (find_migration_incomplete's validation_scope=
    # CURRENT_SUBTASK) and retry attribution can both ask "who owns this
    # file, and are we there yet" via EngineeringPlan.classify_file_
    # ownership() - the same helper, one answer. Both None for every
    # non-MA6-structured caller (a plain Legacy run, or any pre-existing
    # test) - migration validation then degrades to its original
    # always-TERMINAL behavior, never silently permissive.
    structured_plan: Optional["EngineeringPlan"] = None
    current_subtask_id: Optional[str] = None
    # MA8 (PRV-05 run #8, 2026-08-28) - kriya/workflow/obligations.py. One
    # per-run ObligationLedger, threaded through unchanged from workflow.py's
    # run_generation_workflow() (which always resolves a real instance -
    # either the caller's shared one, or a fresh one for a plain Legacy
    # call - see that method's own "resolved_obligation_ledger" comment).
    # Optional/None here only so ad hoc AttemptContext construction in
    # existing tests (predating MA8) keeps working unchanged - every real
    # call site always supplies one.
    obligation_ledger: Optional["ObligationLedger"] = None
    completed_subtask_ids: FrozenSet[str] = frozenset()
    # PRV-17 (2026-09-08, P7 efficiency finding) - kriya/workflow/
    # deterministic_failure_diagnostic.py. One per-run store, threaded
    # through unchanged from workflow.py's run_generation_workflow() the
    # same way obligation_ledger already is above - deliberately a SEPARATE
    # object from obligation_ledger, not a new ObligationKind, so this
    # mechanism carries zero coupling to MA8's own regression-detection
    # semantics. None here only so ad hoc AttemptContext construction in
    # existing tests keeps working unchanged; every real call site supplies
    # one. See that module's own docstring for the incident this closes.
    deterministic_failure_diagnostics: Optional["DeterministicFailureDiagnosticStore"] = None
    # PRD-008: what this run reuses from a resumed checkpoint
    # (resume_fingerprints.ResumePlan). The only source of the candidate
    # reuse and gate-skip decisions: resume_state is checkpoint data and can
    # never grant either. None for every non-resumed run.
    resume_plan: Optional["ResumePlan"] = None
    # PRD-020 (kriya/workflow/requirements.py): the user's original
    # requirements when this attempt's spec-compliance check is their
    # verifier (a direct goal, a milestone plan's integration unit). None
    # for units that verify something narrower (a milestone, a subtask).
    requirement_set: Optional["RequirementSet"] = None


def _process_boundary_obligation_id(subtask_id: str) -> str:
    """The body is kriya/workflow/gate_orchestrator.py (E14)."""
    return gate_orchestrator._process_boundary_obligation_id(subtask_id)


def _record_process_boundary_obligation(
    ctx: "AttemptContext", state: "GenerationState", *, violated: bool, evidence: Dict[str, Any],
) -> bool:
    """The body is kriya/workflow/gate_orchestrator.py (E14)."""
    return gate_orchestrator._record_process_boundary_obligation(_gate_deps(), ctx, state, violated=violated, evidence=evidence)


def _sync_active_repair_contract(
    ctx: "AttemptContext", state: "GenerationState", obligation_record: ObligationRecord,
) -> None:
    """Called only on a VIOLATED process-boundary record (see
    _record_process_boundary_obligation above). Sticky: if a RepairContract
    is already ACTIVE for this EXACT obligation id, leaves it untouched
    (never rebuilds/replaces an in-progress coordinated repair just because
    the same conflict recurred again - see RepairContract's own docstring,
    "sticky across attempts"). Otherwise attempts to derive one from this
    attempt's raw failure output; on ambiguous/insufficient evidence,
    build_repair_contract() returns None and state.repair_contract is left
    exactly as it was (None on a first occurrence - ordinary LOCAL targeted
    retry continues unchanged)."""
    existing = state.repair_contract
    if (
        existing is not None
        and obligation_record.id in existing.source_obligation_ids
        and existing.status == RepairContractStatus.ACTIVE
    ):
        return
    raw_output = obligation_record.evidence.get("raw_output", "")
    if not raw_output:
        return
    known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
    evidence = derive_process_boundary_participants(raw_output, ctx.worktree_path, known_files)
    contract = build_repair_contract(
        obligation_record, evidence, created_attempt=state.attempt_number,
        authorized_write_scope=tuple(sorted(getattr(ctx, "allowed_write_relpaths", None) or ())),
    )
    if contract is None:
        return
    state.repair_contract = contract
    logger.info(
        "MA9: unambiguous evidence found - opening a COORDINATED RepairContract '%s' "
        "(participants: %s; groups: %s).", contract.id,
        ", ".join(contract.participating_artifacts),
        ", ".join(g.id for g in contract.repair_groups),
    )
    state.record_event(RunEvent(
        kind="repair_contract_created", attempt=state.attempt_number, source="attempt.run_attempt",
        authority=EventAuthority.ADVISORY,
        message=f"RepairContract '{contract.id}' opened ({contract.kind.value}).",
        details={
            "repair_contract_id": contract.id,
            "repair_kind": contract.kind.value,
            "source_obligation_ids": list(contract.source_obligation_ids),
            "participating_artifacts": list(contract.participating_artifacts),
            "authorized_write_scope": list(contract.authorized_write_scope),
            "repair_group_ids": [g.id for g in contract.repair_groups],
        },
    ))


def _materialize_candidate_content(
    ctx: "AttemptContext", filepath: str, file_obj: Dict[str, Any],
) -> Optional[str]:
    """MA9: one coordinated participant's resulting content (the candidate view). The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return repair_orchestrator._materialize_candidate_content(_repair_deps(), ctx, filepath, file_obj)


async def _run_coordinated_repair_generation(
    state: "GenerationState", ctx: "AttemptContext", contract: Any,
    base_code_context: str, stream_callback: Optional[Callable[[str], None]],
    attempt_operation: Any, optional_sections: Tuple[OptionalSection, ...] = (),
) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    """MA9: the coordinated multi-file repair generation (results, candidate view). The body is kriya/workflow/repair_orchestrator.py (E7); its
    collaborators are resolved here, at call time (_repair_deps)."""
    return await repair_orchestrator._run_coordinated_repair_generation(_repair_deps(), state, ctx, contract, base_code_context, stream_callback, attempt_operation, optional_sections)


def _classify_grounded_contract_verdict(
    output: str, worktree_path: str, files_written: List[str]
) -> Tuple[ContractVerdictState, Optional[Dict[str, Any]]]:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._classify_grounded_contract_verdict(_runtime_deps(), output, worktree_path, files_written)


async def _resolve_runtime_verification_grade(
    ctx: "AttemptContext", state: ContractVerdictState,
    contract_verdict: Optional[Dict[str, Any]], grade_kwargs: Dict[str, Any],
    *, run_result: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str]:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return await runtime_verification._resolve_runtime_verification_grade(_runtime_deps(), ctx, state, contract_verdict, grade_kwargs, run_result=run_result)


def _deterministic_result_provenance_field(verification_authority: str) -> Optional[str]:
    """The body is kriya/workflow/runtime_verification.py (E9)."""
    return runtime_verification._deterministic_result_provenance_field(verification_authority)


def _record_self_correction_scope_conflict(
    state: GenerationState, ctx: "AttemptContext", result: Any, failure_type: str,
) -> None:
    required = sorted(set(getattr(result, "scope_conflict_files", []) or []))
    if not required:
        return
    state.plan_scope_conflict = {
        "classification": "PLAN_SCOPE_DEFECT",
        "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
        "failure_type": failure_type,
        "required_files": required,
        "allowed_files": sorted(ctx.allowed_write_relpaths),
        "reason": "self-correction diagnosis requires a readable file outside approved write scope",
        "attribution_tier": "self_correction",
        "grounded_owner_files": [],
    }


def _initial_test_process_boundary_constraint(
    required_verification: List[Dict[str, Any]], target_files: Optional[List[str]],
) -> str:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._initial_test_process_boundary_constraint(_process_boundary_deps(), required_verification, target_files)


def _runtime_contract_requirements(ctx: "AttemptContext") -> List[Dict[str, Any]]:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._runtime_contract_requirements(ctx)


def _required_process_terminating_cases(
    required_verification: List[Dict[str, Any]],
) -> List[str]:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._required_process_terminating_cases(required_verification)


def _enclosing_test_method(source: str, offset: int) -> Optional[str]:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._enclosing_test_method(source, offset)


def _enclosing_test_method_source(source: str, offset: int) -> str:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._enclosing_test_method_source(source, offset)


def _resolve_java_main_argv_expression(method_source: str, call_expression: str) -> str:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._resolve_java_main_argv_expression(method_source, call_expression)


def _runtime_contract_expects_numeric_input(
    required_verification: List[Dict[str, Any]],
) -> bool:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._runtime_contract_expects_numeric_input(required_verification)


def _terminating_case_from_argv_expression(
    cases: List[str], argv_expression: str, *, numeric_input: bool,
) -> Optional[str]:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._terminating_case_from_argv_expression(cases, argv_expression, numeric_input=numeric_input)


def find_in_process_terminating_test_invocations(
    required_verification: List[Dict[str, Any]],
    worktree_path: str,
    test_files: List[str],
    application_entrypoints: List[str],
) -> List[Dict[str, Any]]:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary.find_in_process_terminating_test_invocations(_process_boundary_deps(), required_verification, worktree_path, test_files, application_entrypoints)


def _raise_unsafe_process_boundary_test_candidate(
    state: GenerationState, ctx: "AttemptContext", test_files: List[str],
) -> None:
    """The body is kriya/workflow/gate_orchestrator.py (E14)."""
    return gate_orchestrator._raise_unsafe_process_boundary_test_candidate(_gate_deps(), state, ctx, test_files)


def find_ungrounded_java_child_process_tests(
    required_verification: List[Dict[str, Any]],
    worktree_path: str,
    test_files: List[str],
    java_main_classes: Dict[str, str],
    known_files: List[str],
) -> List[Dict[str, Any]]:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary.find_ungrounded_java_child_process_tests(_process_boundary_deps(), required_verification, worktree_path, test_files, java_main_classes, known_files)


def _changed_test_sources(state: GenerationState, ctx: "AttemptContext") -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """FS-1A: every runnable test source the candidate changed, as
    (baseline text or None for a new file, candidate text or None when not
    strict UTF-8 - which the decision treats as indeterminate)."""
    def read(path: str) -> Tuple[bool, Optional[str]]:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except FileNotFoundError:
            return False, None
        try:
            return True, data.decode("utf-8")
        except UnicodeDecodeError:
            return True, None

    changed: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for path in find_runnable_test_files(state.all_files_written):
        exists, candidate = read(os.path.join(ctx.worktree_path, path))
        if not exists:
            continue
        _, baseline = read(os.path.join(ctx.workspace_path, path))
        if candidate is None or candidate != baseline:
            changed[path] = (baseline, candidate)
    return changed


def _raise_unexecuted_test_delta(
    state: GenerationState, ctx: "AttemptContext", validator: Any,
    accepted_result: Optional[Dict[str, Any]], selected_test: Optional[str],
) -> None:
    """The body is kriya/workflow/gate_orchestrator.py (E14)."""
    return gate_orchestrator._raise_unexecuted_test_delta(_gate_deps(), state, ctx, validator, accepted_result, selected_test)


def _raise_ungrounded_child_process_test_candidate(
    state: GenerationState, ctx: "AttemptContext", test_files: List[str],
) -> None:
    """The body is kriya/workflow/gate_orchestrator.py (E14)."""
    return gate_orchestrator._raise_ungrounded_child_process_test_candidate(_gate_deps(), state, ctx, test_files)


def _prepare_finite_command_runtime_artifacts(
    resolved_run_commands: List[List[str]],
    validator: "PolymorphicValidator",
    ctx: "AttemptContext",
    state: GenerationState,
) -> None:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._prepare_finite_command_runtime_artifacts(_runtime_deps(), resolved_run_commands, validator, ctx, state)


def _raise_runtime_verification_infrastructure_failure(
    state: GenerationState,
    run_result: Dict[str, Any],
    commands: List[List[str]],
) -> None:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._raise_runtime_verification_infrastructure_failure(_runtime_deps(), state, run_result, commands)


def _raise_candidate_runtime_entrypoint_failure(
    state: GenerationState, run_result: Dict[str, Any], commands: List[List[str]], entrypoint: Dict[str, Any],
) -> None:
    """The body is kriya/workflow/runtime_verification.py (E9)."""
    return runtime_verification._raise_candidate_runtime_entrypoint_failure(state, run_result, commands, entrypoint)


def _apply_runtime_verification_contract(
    commands: List[List[str]], input_channel: str,
) -> Tuple[List[List[str]], Optional[str], Optional[str]]:
    """The body is kriya/workflow/runtime_verification.py (E9)."""
    return runtime_verification._apply_runtime_verification_contract(commands, input_channel)


def _run_verification_basis_hash(ctx: "AttemptContext", state: GenerationState) -> str:
    """The body is kriya/workflow/runtime_verification.py (E9)."""
    return runtime_verification._run_verification_basis_hash(ctx, state)


# Retries whose originating failure came from a deterministic gate that runs again, on the whole candidate, after the write
# loop: the prose heuristic defers to that gate (see _diagnosis_mismatch_bypass_reason).
_DETERMINISTIC_GATE_RETRY_TYPES = frozenset({"compile", "pom_semantic_validation", "targeted_test", "static_rule_violation"})


def _diagnosis_mismatch_bypass_reason(
    state: GenerationState, ctx: "AttemptContext", filepath: str, candidate_content: str,
) -> Optional[str]:
    """Deterministic-validation-first override for the diagnosis-mismatch
    pre-flight check (find_edits_ignoring_own_diagnosis, kriya/workflow/
    attribution.py) - added 2026-08-17 after 5 independent false-positive
    prose shapes were found in that one check across a single night (cast-
    insertion, "instead of" ambiguity, self-referential filepath,
    verification-marker wrap, plus the original 2026-08-13 incident that
    motivated the check at all). Five bespoke signals in one function is a
    symptom of an unbounded problem, not a finite list of missing cases -
    verifying analysis PROSE against diff CONTENT via backtick-quote
    matching fights an inherently fuzzy correspondence that a new model
    response shape can always break differently. Rather than adding a 6th/
    7th/Nth signal for the next prose shape, this asks the real underlying
    question directly, two different ways depending on what's cheaply
    available:

    1. If this retry is responding to a static_rule_violation (a CHEAP, already-registered deterministic checker -
       kriya/workflow/static_checks.py), the same check re-runs on the WHOLE candidate right after the write loop, so the prose
       heuristic defers to it (D3, live Greenfield run 20261015T152407-5f87716a: the earlier branch here re-ran the check with only
       the current file overridden and the sibling files still stale on disk, and could never see a response that resolves the
       finding in another file). Earlier history, kept for the incident that motivated deterministic-validation-first: a correct
       fix wrapping a bare `[VERIFICATION] PASS` line in println(...) was rejected 3 times because the marker text was a substring
       of a larger search block; re-running BareVerificationMarkerCheck would have accepted it on the first try.
    2. If this retry is responding to a `compile` failure, there is no
       equally-cheap re-check available - the only authoritative answer is
       the real compiler, which runs immediately after this check anyway.
       Rather than keep guessing from prose, this check is bypassed
       entirely for compile-triggered retries and the real compile gate
       decides. This is an evidence-backed tradeoff, not a guess: 3 of the
       5 confirmed false positives above (cast-insertion, "instead of",
       self-referential filepath) were ALL compile-triggered, each
       rejecting an edit that would have compiled successfully; by
       contrast, this check's own ORIGINAL motivating incident (a compile
       error recurring verbatim across 3 retries because an edit never
       implemented its own analysis, at ANY line -
       attribution-fix-validation-3, 2026-08-13) would cost at most one
       extra wasted compile cycle if this check were entirely absent - the
       SAME repeated-failure signal this retry loop already detects and
       escalates from independently (state.budgets.last_failure_signature),
       with no dependency on prose-matching at all. Given real compile
       subprocess cost against the confirmed cost of wrongly rejecting a
       correct fix (3-6 wasted retries EACH, tonight), the evidence favors
       letting the compiler decide.

    pom_semantic_validation joined the compile bypass 2026-08-17 (see the
    inline comment on that branch below) - a real compile gate always runs
    right after this check regardless, so it's the same tradeoff, not a
    separate case. Every OTHER failure type (misdirected_edit,
    run_verification, general_error, etc.) is left exactly as-is - no
    evidence yet that this check misfires for them, and this is deliberately
    not a blanket "disable the check" change. (unaddressed_error_location -
    find_edits_ignoring_reported_line, the "Layer 1" check just above this
    one's call site - got its OWN, separately-scoped deterministic-
    validation-first bypass the same night this docstring line was found to
    be stale; see that check's own inline comment in run_attempt() for why it
    didn't need this function's signature-based dispatch at all.)

    Returns a human-readable bypass reason (for logging/observability - the
    diagnosis text stays useful there, just never a blocking gate for these
    two cases) if the check should be skipped for this edit; None if the
    existing diagnosis-mismatch check should run and decide as before."""
    # During owner restoration, prose-analysis agreement is advisory.  The
    # exact baseline signature inspection below is the sole authority; this
    # fuzzy heuristic must not veto a candidate before that inspection.
    recovery = state.api_contract_recovery or {}
    if recovery and recovery.phase is APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT:
        return (
            "RESTORE_PUBLIC_CONTRACT is decided by deterministic exact-signature "
            "inspection; diagnosis prose cannot veto owner restoration"
        )

    # Bounded-veto policy, checked before anything else and independent of
    # fail_type: this check can reject a given FILE'S edit at most once per
    # run. Found live, 2026-08-17 (ignite_qpid_person, run b-10o): the
    # pom_semantic_validation false positive above rejected the SAME correct
    # fix 4 CONSECUTIVE times before the model finally gave up and regenerated
    # from scratch on a fallback model - by which point the run had already
    # burned most of its wall-clock budget on one heuristic's repeated wrong
    # call. Every fail_type this check has EVER been extended to cover
    # (compile, static_rule_violation, pom_semantic_validation) was found
    # this same way: a real incident, after the fact. This is the safety net
    # for every fail_type NOT yet on that list, and for the next false-
    # positive shape this check will inevitably have that nobody has found
    # yet - a single wrongful rejection costs one retry; catching this
    # heuristic in a 4x-in-a-row loop costs the whole run.
    if state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) >= 1:
        return (
            "this check already rejected an edit to this file once this run - "
            "per the bounded-veto policy, it never rejects the same file twice "
            "regardless of failure type; the real downstream gate decides instead"
        )
    sig = state.budgets.last_failure_signature
    if not sig:
        return None
    fail_type = sig[0]
    if fail_type in _DETERMINISTIC_GATE_RETRY_TYPES:
        # targeted_test added 2026-08-17: run_tests(target_test=...) (kriya/
        # tools/validate.py) already scopes a targeted-test rerun to the ONE
        # test file extract_target_test() identified - not the whole
        # regression suite - and that real rerun happens immediately after
        # this per-file write loop regardless, in the SAME attempt (see the
        # targeted_test gate a few hundred lines below this check's own call
        # site). Same tradeoff as compile/pom_semantic_validation: bypassing
        # here costs nothing beyond what would happen anyway, and avoids
        # prose-matching a correct fix out from under a test that would have
        # passed.
        # pom_semantic_validation added 2026-08-17 after a live false positive
        # (ignite_qpid_person, run b-10o): a correct edit wrapped a bare
        # <dependency> fragment in a full, valid <project> POM structure -
        # exactly what its own analysis said - but the analysis quoted the
        # tag as `<project>` (no attributes) while the actual fix wrote
        # `<project xmlns="...">` (real Maven POMs always declare a
        # namespace), so a literal substring check for the bare quoted tag
        # never matched. Same tradeoff as compile, for the same reason: `mvn
        # validate` (the check that raised pom_semantic_validation) is a
        # cheap PRE-check specifically so a broken POM fails before the
        # expensive full `mvn compile` - but that real compile gate still
        # runs immediately after this per-file loop regardless, and would
        # catch a genuinely still-broken POM just as reliably, one step
        # later. No new prose-matching signal needed (the underlying gap is
        # XML-attribute-vs-bare-tag-name, not really about pom.xml
        # specifically - a 6th signal here would just be the next incident-
        # specific patch this function's whole redesign was meant to stop).
        # static_rule_violation joined this list on D3 (live Greenfield run 20261015T152407-5f87716a, attempt 6): the
        # pom.xml analysis quoted Maven coordinates (`org.junit.jupiter:junit-jupiter`), which no pom.xml contains literally,
        # so this prose heuristic vetoed pom.xml and discarded the whole candidate - with a verdict about the wrong file,
        # before the static rule (which judges the WHOLE candidate right after the write loop) could speak. The earlier
        # branch re-ran the static check with ONLY this file overridden and the not-yet-written sibling files still on
        # disk, so it could never see a response that resolves the finding in a different file. The real gate is the
        # authority for the finding being repaired and sees the complete candidate; a prose heuristic must not pre-empt it.
        return (
            "this retry responds to a failure of a deterministic gate that judges the whole candidate right after this loop "
            "(compile/POM-validation/targeted-test/static-rule) - deferring to that gate instead of prose-matching"
        )
    return None


def _dependency_graph_db_path(ctx: "AttemptContext") -> str:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._dependency_graph_db_path(ctx)


def _extract_class_names_best_effort(db_path: str, filepath: str, content: str) -> List[str]:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._extract_class_names_best_effort(_process_boundary_deps(), db_path, filepath, content)


def _find_java_main_class_best_effort(db_path: str, filepath: str, content: str) -> Optional[str]:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._find_java_main_class_best_effort(_process_boundary_deps(), db_path, filepath, content)


def _build_java_main_class_map(java_files: List[str], ctx: "AttemptContext") -> Dict[str, str]:
    """The body is kriya/workflow/process_boundary.py (E14)."""
    return process_boundary._build_java_main_class_map(_process_boundary_deps(), java_files, ctx)


# VER-005 implementation (2026-09-13): noise directories skipped while
# discovering real Python repository facts (see
# _build_python_runtime_grounding below) - dependency/build/cache
# artifacts, never a repository's own source, that a naive full-tree walk
# would otherwise scan (and could misidentify a vendored third-party
# script's own __main__ guard as a real repository entrypoint).
_PYTHON_RUNTIME_GROUNDING_EXCLUDED_DIRS = {
    ".git", ".kriya", "__pycache__", ".venv", "venv", "env", "node_modules",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".eggs", "site-packages",
}


def _collect_python_runtime_grounding_facts(root: str) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._collect_python_runtime_grounding_facts(_runtime_deps(), root)


def _build_python_runtime_grounding(root: str) -> Tuple[FrozenSet[str], FrozenSet[str], List[str]]:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._build_python_runtime_grounding(_runtime_deps(), root)


_JAVA_PACKAGE_DECL_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def _build_java_package_map(java_files: List[str], ctx: "AttemptContext") -> Dict[str, Optional[str]]:
    """{filepath: package_or_None} for every .java file given, read fresh
    (worktree first, workspace fallback, same lookup order as
    _build_java_main_class_map() above) - the I/O half of
    find_cross_package_symbol_mismatch()'s own deliberately pure/testable
    design (kriya/workflow/failure_grounding.py). None means the file has no
    package declaration (Java's default/unnamed package), which is a real,
    meaningful value here, not "unknown" - a file that can't be read at all
    is simply omitted from the returned dict rather than guessed at."""
    result: Dict[str, Optional[str]] = {}
    for filepath in java_files:
        full_path = os.path.join(ctx.worktree_path, filepath)
        if not os.path.exists(full_path):
            full_path = os.path.join(ctx.workspace_path, filepath)
        if not os.path.exists(full_path):
            continue
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as e:
            logger.debug(f"Java package detection: couldn't read {filepath}, skipping it: {e}")
            continue
        pkg_match = _JAVA_PACKAGE_DECL_RE.search(content)
        result[filepath] = pkg_match.group(1) if pkg_match else None
    return result


def _build_workspace_type_index(state: GenerationState, ctx: "AttemptContext") -> Dict[str, List[str]]:
    """The body is kriya/workflow/gate_orchestrator.py (E14)."""
    return gate_orchestrator._build_workspace_type_index(_gate_deps(), state, ctx)


def _runtime_verification_is_advisory_only(ctx: "AttemptContext", judgment: Dict[str, Any]) -> bool:
    """The body is kriya/workflow/runtime_verification.py (E9)."""
    return runtime_verification._runtime_verification_is_advisory_only(ctx, judgment)


def _derive_runtime_command_subject(ctx: "AttemptContext", judgment: Dict[str, Any], intro: str) -> RuntimeCommandSubject:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._derive_runtime_command_subject(_runtime_deps(), ctx, judgment, intro)


def _runtime_commands_need_approval(ctx: "AttemptContext", state: GenerationState, judgment: Dict[str, Any], autonomy_mode: str) -> bool:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._runtime_commands_need_approval(_runtime_deps(), ctx, state, judgment, autonomy_mode)


async def _approve_runtime_commands(ctx: "AttemptContext", state: GenerationState, judgment: Dict[str, Any], intro: str) -> bool:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return await runtime_verification._approve_runtime_commands(_runtime_deps(), ctx, state, judgment, intro)


def _required_runtime_verification_missing_message(judgment: Dict[str, Any]) -> str:
    """The body is kriya/workflow/runtime_verification.py (E9)."""
    return runtime_verification._required_runtime_verification_missing_message(judgment)


def _attribute_candidate_suite(
    state: GenerationState, ctx: "AttemptContext", result: Dict[str, Any], scope: str,
) -> Optional[Any]:
    """CANDIDATE-GATE-BASELINE-POLICY-001: this full-suite ``result`` judged
    against the frozen PRE-mutation baseline by the one attribution owner
    (kriya/workflow/suite_attribution.py) - the decision the terminal check
    makes, now made at every candidate gate that runs the repository suite.
    None when no baseline was captured (the raw rule stays) or the attempt
    has no replay for stability measurement."""
    baseline = captured_baseline(state.validation_baseline_full_regression)
    if baseline is None or ctx.baseline_suite_replay is None:
        return None
    attribution = attribute_suite_result(
        scope=scope, baseline=baseline, result=result,
        post_environment=post_environment_identity(
            baseline=baseline, workspace_path=ctx.workspace_path, worktree_path=ctx.worktree_path,
            autonomy_cfg=ctx.kernel.config.autonomy, goal=ctx.goal, written_files=state.all_files_written),
        stability_cache=state.pytest_stability_cache, replay=ctx.baseline_suite_replay,
        workspace_path=ctx.workspace_path,
    )
    state.record_event(RunEvent(
        kind="validation_baseline.candidate_gate_delta", attempt=state.attempt_number,
        source="attempt.candidate_gate", authority=EventAuthority.ADVISORY,
        message=f"Candidate-gate ({scope}) {attribution.message()}",
        details={"scope": scope, **attribution.event_details()},
    ))
    return attribution


def _judge_candidate_suite(
    state: GenerationState, ctx: "AttemptContext", result: Dict[str, Any], scope: str,
) -> Tuple[bool, str, Dict[str, Any]]:
    """The body is kriya/workflow/gate_orchestrator.py (E14)."""
    return gate_orchestrator._judge_candidate_suite(_gate_deps(), state, ctx, result, scope)


async def _run_verification_only_attempt(state: GenerationState, ctx: AttemptContext) -> None:
    """The verification-only attempt (a DENY_ALL unit's declared verifiers, VerificationCoordinator). The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return await gate_orchestrator._run_verification_only_attempt(_gate_deps(), state, ctx)


_MANAGED_SERVICE_INFRASTRUCTURE_OUTCOMES = frozenset({
    # Artifact Preparation (P6 production-validation, 2026-09-07): a
    # missing/unbuildable runnable artifact is exactly as much an
    # infrastructure condition as a service that never started - the
    # application itself was never even launched, so PROBE_FAILED-style
    # "eligible for normal Developer repair" treatment would be wrong here
    # too, same reasoning as every other member of this set.
    ServiceVerificationOutcomeKind.PREPARATION_FAILED,
    ServiceVerificationOutcomeKind.ARTIFACT_MATERIALIZATION_FAILED,
    ServiceVerificationOutcomeKind.SERVICE_START_FAILED,
    ServiceVerificationOutcomeKind.READINESS_TIMEOUT,
    ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY,
    ServiceVerificationOutcomeKind.CLEANUP_FAILED,
    ServiceVerificationOutcomeKind.VERIFICATION_INTERNAL_ERROR,
})

# Runtime-Evidence Plan Repair (PRV-17 Run 13, 2026-09-04): the subset of
# _MANAGED_SERVICE_INFRASTRUCTURE_OUTCOMES above whose `output` is the
# TARGET APPLICATION's own captured stdout/stderr - real text worth running
# through classify_environment_failure(), the same classifier compile/test
# failures already consult. CLEANUP_FAILED and VERIFICATION_INTERNAL_ERROR
# are deliberately excluded: both are Kriya's OWN process-management code
# failing (or an exception raised inside kriya.tools.service_runtime
# itself), never the target application's text - classifying an internal
# Kriya traceback via a heuristic built to read a DIFFERENT application's
# output would be exactly the "never designed to recognize an arbitrary
# internal traceback" mistake this module's own PRV-06 precedent (see
# retry_strategy.py's environment_failure bypass-set comment) already
# rejected once. SERVICE_START_FAILED is included even though it can also
# legitimately mean "no captured output at all" (e.g. the interpreter
# itself was never found) - classify_environment_failure/extract_missing_
# project_local_python_module both fail closed (return None) on text that
# doesn't match their own patterns, so an empty/unrelated `output` here
# simply falls through to the unchanged STOP_ENVIRONMENT path below, no
# special-casing required.
_MANAGED_SERVICE_OUTCOMES_WITH_CAPTURED_APPLICATION_OUTPUT = frozenset({
    # PREPARATION_FAILED's own `output` is the build tool's real stdout/
    # stderr (e.g. a genuine `mvn package` compile error) - real,
    # classifiable project text, same category as a compile-check failure
    # already routed through this classifier. ARTIFACT_MATERIALIZATION_
    # FAILED is deliberately excluded: that outcome's own stdout/stderr is
    # a build command that reported SUCCESS - nothing in it indicates a
    # code defect to classify, same reasoning CLEANUP_FAILED/VERIFICATION_
    # INTERNAL_ERROR already use to stay out of this set.
    ServiceVerificationOutcomeKind.PREPARATION_FAILED,
    ServiceVerificationOutcomeKind.SERVICE_START_FAILED,
    ServiceVerificationOutcomeKind.READINESS_TIMEOUT,
    ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY,
})

def _resolve_execution_mode(judgment: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
    """The body is kriya/workflow/runtime_verification.py (E9)."""
    return runtime_verification._resolve_execution_mode(judgment)


def _looks_like_shell_compound_command(command: Any) -> bool:
    """The body is kriya/workflow/runtime_verification.py (E9)."""
    return runtime_verification._looks_like_shell_compound_command(command)


def _reject_non_local_managed_service_host(field_name: str, host: str) -> Optional[str]:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._reject_non_local_managed_service_host(_runtime_deps(), field_name, host)


def _validate_and_convert_readiness(raw: Any) -> Tuple[Optional[ReadinessSpec], Optional[str]]:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._validate_and_convert_readiness(_runtime_deps(), raw)


def _validate_and_convert_probe(raw: Any) -> Tuple[Optional[ProbeSpec], Optional[str]]:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._validate_and_convert_probe(_runtime_deps(), raw)


def _validate_and_convert_managed_service_contract(
    managed_service: Any, worktree_path: str, validator: "PolymorphicValidator",
) -> Tuple[Optional[ManagedServiceVerificationSpec], Optional[str]]:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._validate_and_convert_managed_service_contract(_runtime_deps(), managed_service, worktree_path, validator)


def _managed_service_blocked_without_approval_subject(state: GenerationState, ctx: "AttemptContext") -> bool:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return runtime_verification._managed_service_blocked_without_approval_subject(_runtime_deps(), state, ctx)


async def _execute_managed_service_verification(
    state: GenerationState, ctx: "AttemptContext", judgment: Dict[str, Any], known_files: List[str],
    validator: "PolymorphicValidator",
) -> None:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return await runtime_verification._execute_managed_service_verification(_runtime_deps(), state, ctx, judgment, known_files, validator)


def _raise_if_required_runtime_verification_declined(state: GenerationState, ctx: "AttemptContext") -> None:
    """The body is kriya/workflow/runtime_verification.py (E9)."""
    return runtime_verification._raise_if_required_runtime_verification_declined(state, ctx)


async def _execute_runtime_verification_directly(
    state: GenerationState, ctx: "AttemptContext", validator: "PolymorphicValidator",
) -> None:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return await runtime_verification._execute_runtime_verification_directly(_runtime_deps(), state, ctx, validator)


def _spec_compliance_authoritative_context(ledger: Optional[ObligationLedger]) -> Optional[str]:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._spec_compliance_authoritative_context(ledger)


def _migration_obligations_all_satisfied(ledger: Optional[ObligationLedger]) -> bool:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._migration_obligations_all_satisfied(ledger)


def _spec_requirements_contradicting_authority(
    missing_requirements: List[str], ledger: Optional[ObligationLedger],
) -> Tuple[List[str], List[str]]:
    """The body is kriya/workflow/spec_compliance.py (E10); its collaborators are resolved here, at call time (_spec_deps)."""
    return spec_compliance._spec_requirements_contradicting_authority(_spec_deps(), missing_requirements, ledger)


def _identifier_local_terms(text: str, identifier: str, radius: int = 3) -> set[str]:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._identifier_local_terms(text, identifier, radius)


def _extract_requirement_identifier_tokens(requirement: str) -> List[str]:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._extract_requirement_identifier_tokens(requirement)


def _spec_requirements_naming_planner_only_identifiers(
    missing_requirements: List[str], goal_text: str,
) -> Tuple[List[str], List[str]]:
    """The body is kriya/workflow/spec_compliance.py (E10); its collaborators are resolved here, at call time (_spec_deps)."""
    return spec_compliance._spec_requirements_naming_planner_only_identifiers(_spec_deps(), missing_requirements, goal_text)


def _goal_spec_requirement_obligation_id(subtask_id: str) -> str:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._goal_spec_requirement_obligation_id(subtask_id)


def _stage_scoped_spec_compliance_goal(ctx: "AttemptContext") -> tuple[str, List[str]]:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._stage_scoped_spec_compliance_goal(ctx)


def _goal_spec_evidence_fingerprint(goal: str, file_contents: Dict[str, str]) -> str:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._goal_spec_evidence_fingerprint(goal, file_contents)


def _record_original_requirement_verdicts(
    state: GenerationState, ctx: "AttemptContext", spec_result: Dict[str, Any], fingerprint: str,
) -> None:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._record_original_requirement_verdicts(state, ctx, spec_result, fingerprint)


def _downgrade_suppressed_requirement_claims(
    state: GenerationState, ctx: "AttemptContext", suppressed: List[str], fingerprint: str,
) -> None:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._downgrade_suppressed_requirement_claims(state, ctx, suppressed, fingerprint)


def _close_requirements_by_migration_gate(
    state: GenerationState, ctx: "AttemptContext", fingerprint: str,
) -> None:
    """The body is kriya/workflow/spec_compliance.py (E10); its collaborators are resolved here, at call time (_spec_deps)."""
    return spec_compliance._close_requirements_by_migration_gate(_spec_deps(), state, ctx, fingerprint)


def _structured_plan_paths(ctx: "AttemptContext") -> set:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._structured_plan_paths(ctx)


def _stage_ownership_redirect_restoration(
    state: GenerationState, ctx: "AttemptContext", staged_writes: List[StagedFileWrite],
) -> None:
    """The body is kriya/workflow/spec_compliance.py (E10); its collaborators are resolved here, at call time (_spec_deps)."""
    return spec_compliance._stage_ownership_redirect_restoration(_spec_deps(), state, ctx, staged_writes)


async def _classify_and_escalate_contract_changes(
    state: GenerationState, ctx: "AttemptContext", violations: List[Dict[str, Any]],
    baseline_contents: Dict[str, str], candidate_contents: Dict[str, str],
    run_direct_authorizations: List[Any], authorized_changes: List[Dict[str, Any]] = (),
) -> Tuple[List[Dict[str, Any]], List[Any], Optional[str]]:
    """The body is kriya/workflow/spec_compliance.py (E10); its collaborators are resolved here, at call time (_spec_deps)."""
    return await spec_compliance._classify_and_escalate_contract_changes(_spec_deps(), state, ctx, violations, baseline_contents, candidate_contents, run_direct_authorizations, authorized_changes)


def _settled_goal_spec_requirement(
    ledger: Optional[ObligationLedger], obligation_id: Optional[str], fingerprint: str,
) -> Optional[ObligationRecord]:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._settled_goal_spec_requirement(ledger, obligation_id, fingerprint)


def _restore_api_contract_owners_deterministically(
    state: GenerationState, contract: APIContractRecovery,
) -> List[Dict[str, str]]:
    """The body is kriya/workflow/spec_compliance.py (E10)."""
    return spec_compliance._restore_api_contract_owners_deterministically(state, contract)


def _verified_no_change_proposal(
    state: GenerationState, ctx: "AttemptContext", files: List[Dict[str, Any]], missing_files: List[str],
) -> List[str]:
    """The body is kriya/workflow/spec_compliance.py (E10); its collaborators are resolved here, at call time (_spec_deps)."""
    return spec_compliance._verified_no_change_proposal(_spec_deps(), state, ctx, files, missing_files)


def _attempt_evidence_boundary(func):
    """LR-R1-M1: run one attempt inside its attempt-evidence scope. The body is kriya/workflow/decision_evidence.py (E10); its collaborators are resolved here, at call time (_evidence_deps)."""
    return decision_evidence._attempt_evidence_boundary(_evidence_deps, func)


def _record_obligations_snapshot(ctx: AttemptContext) -> None:
    """LR-R1-M1 ``obligations.snapshot`` at attempt close. The body is kriya/workflow/decision_evidence.py (E10); its collaborators are resolved here, at call time (_evidence_deps)."""
    return decision_evidence._record_obligations_snapshot(_evidence_deps(), ctx)


def defer_execution_until_source_review(state: GenerationState, ctx: "AttemptContext") -> bool:
    """Demo V2 owner decision (2026-10-15, Gap B): in human-in-the-loop mode no model-authored candidate content is executed - POM validation, build, tests, runtime verification -
    before the person has reviewed and approved its source. When this attempt wrote source (so the pre-apply source review WILL be asked), a caller that runs the post-approval phase
    (``ctx.defer_execution_until_source_review_enabled``, run_generation_workflow) has the executing gates held in ``ctx.deferred_executing_gates`` and run right after the approval.
    The non-executing checks (completeness, static rules, stack contract) still run first. Held even with NO approver: the review can then never succeed, so the run ends at the approval gate
    (approval_required) having executed nothing. Never deferred: other autonomy modes (their approval policy - sensitive paths, size, process profile - is unchanged and out of this decision's
    scope) and an attempt that wrote no source (a verification-only unit has no source review).

    The decision is AUTHORITY's (``kriya.policy.approval.defer_execution_until_source_review``, slice C3); this kernel adapter keeps the
    name and reads its three inputs from the attempt."""
    autonomy = ctx.kernel.config.autonomy
    return source_review.defer_execution_until_source_review(
        ctx.defer_execution_until_source_review_enabled, autonomy.mode, state.all_files_written)


def assert_candidate_unchanged_since_source_review(state: GenerationState, ctx: "AttemptContext") -> None:
    """Right before the reviewed commands execute: the candidate bytes must still be the ones the person approved. A change makes that approval stale - nothing executes and nothing
    is applied (a typed ``verification_infrastructure_failure``, stop-the-run; the ordinary retry loop is the only way to a new candidate and a new source review).

    Whether the approval is stale is AUTHORITY's decision (``kriya.policy.approval.stale``, slice C3, with the stop text); this kernel adapter
    keeps the name and turns a stale answer into the attempt's own typed failure."""
    if not source_review.stale(ctx.post_source_review_guard):
        return
    message = source_review.CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW_MESSAGE
    failure = Failure(type="verification_infrastructure_failure", message=message, raw_output=message, attempt=state.attempt_number)
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


async def _approve_and_execute_runtime_verification(
    state: GenerationState, ctx: "AttemptContext", validator: Any, active_code_context: str,
    judgment: Dict[str, Any], autonomy_cfg_rv: Any,
) -> None:
    """The body is kriya/workflow/runtime_verification.py (E9); its collaborators are resolved here, at call time (_runtime_deps)."""
    return await runtime_verification._approve_and_execute_runtime_verification(_runtime_deps(), state, ctx, validator, active_code_context, judgment, autonomy_cfg_rv)


def _run_pom_validation_gate(state: GenerationState, ctx: "AttemptContext", tree_binding: Any) -> None:
    """The POM semantic check (``mvn validate`` on the candidate's own pom.xml). The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return gate_orchestrator._run_pom_validation_gate(_gate_deps(), state, ctx, tree_binding)


def _new_candidate_validator(ctx: "AttemptContext") -> "PolymorphicValidator":
    """The candidate-gates validator for this attempt (a new one every attempt). The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return gate_orchestrator._new_candidate_validator(_gate_deps(), ctx)


def _resolve_candidate_toolchain(state: GenerationState, ctx: "AttemptContext", validator: "PolymorphicValidator") -> None:
    """The one-time toolchain preflight and the JAVA_HOME override, applied to this attempt's validator. The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return gate_orchestrator._resolve_candidate_toolchain(_gate_deps(), state, ctx, validator)


def apply_pre_review_candidate_corrections(state: GenerationState, ctx: "AttemptContext") -> None:
    """Held-execution mode: the candidate's pom corrections before the binding and the source review. The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return gate_orchestrator.apply_pre_review_candidate_corrections(_gate_deps(), state, ctx)


async def _run_executing_candidate_gates(
    state: GenerationState, ctx: "AttemptContext", skipping_candidate_gates: bool, tree_binding: Any, active_code_context: str,
) -> None:
    """Every candidate gate that executes the candidate and the gates that judge after them. The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return await gate_orchestrator._run_executing_candidate_gates(_gate_deps(), state, ctx, skipping_candidate_gates, tree_binding, active_code_context)


async def _run_deferred_executing_gates(
    state: GenerationState, ctx: "AttemptContext", skipping_candidate_gates: bool, tree_binding: Any, active_code_context: str,
) -> None:
    """The held half of run_attempt, after the source approval (POM validation, then the executing gates). The body is kriya/workflow/gate_orchestrator.py (E8); its collaborators are
    resolved here, at call time (_gate_deps)."""
    return await gate_orchestrator._run_deferred_executing_gates(_gate_deps(), state, ctx, skipping_candidate_gates, tree_binding, active_code_context)


@_attempt_evidence_boundary
async def run_attempt(state: GenerationState, ctx: AttemptContext) -> None:
    """Runs one Developer + Quality Gates attempt. Mutates state in place
    (files_written, gate_outcomes, model_hops, run_verification_*, etc.).
    Raises QualityGateFailure or IncompleteGenerationError on any gate
    failure; returns normally when Quality Gates (including Runtime
    Verification) pass."""
    ctx.deferred_executing_gates = None  # Gap B: a held execution belongs to exactly one candidate (the one this call is about to produce)
    if is_verification_only_unit(ctx.write_scope_mode, ctx.required_verification):
        # Verification-only subtask with at least one directly-executable
        # verifier (compile/test, or - PRV-06, 2026-08-28 - an explicit
        # application_runtime check) - take the whole rest of this function
        # out of the loop entirely (see _run_verification_only_attempt's own
        # docstring for why). Falls through to the ordinary path below ONLY
        # when NOTHING in required_verification is directly executable -
        # still fully protected by DENY_ALL either way, just not optimized
        # for that shape (a plan-repair/DAG defect, not a Kriya execution
        # gap, at that point).
        state.terminal_regression_succeeded = False
        state.overall_attempt_succeeded = False
        await _run_verification_only_attempt(state, ctx)
        return
    state.attempt_number += 1
    state.candidate_gates_succeeded = False
    state.terminal_regression_succeeded = False
    state.overall_attempt_succeeded = False
    state.no_change_proposal = []
    state.completion_kind = None
    # PLAT-039: a planned target that is repository metadata or Kriya control
    # state is refused before any Developer request - the same DENY the
    # writer gives, which the retry loop stops on at once.
    control_targets = [path for path in ctx.architect_files
                       if is_trusted_control_path(ctx.worktree_path, os.path.join(ctx.worktree_path, path))]
    if control_targets:
        state.rejected_generation_targets.extend(control_targets)
        denied_path = os.path.join(ctx.worktree_path, control_targets[0])
        raise PolicyDeniedError(request=ActionRequest(action_type=ActionType.WRITE_FILE, target=denied_path),
                                result=trusted_control_path_denial(denied_path))
    # Mode selection is retry_policy.decide_attempt_mode(): the same pure
    # decision and the same state inputs as the outer loop's
    # decide_for_state() (workflow.py), so "targeted beats missing_files
    # beats fallback_targeted beats full_set" and the API-recovery handback
    # are encoded in exactly one place. It leaves out the stop conditions:
    # the loop already decided them before this attempt began, and they must
    # not fire a second time here with a since-incremented attempt_number.
    retry_decision = decide_attempt_mode(
        state, max_retries=ctx.max_retries, targeted_max_retries=ctx.targeted_max_retries,
        has_fallback_model=_fallback_routing(state, ctx).available,
    )
    # Recorded now, not derived by the caller afterward - see the field's own
    # docstring in kriya/workflow/state.py for why that would be unsafe.
    # RetryAction.STOP_EXHAUSTED can't actually occur here (see the None
    # defaults above), but falls back to "full_set" rather than raising, to
    # stay inert if that ever changes.
    state.last_attempt_mode = (
        retry_decision.action.value
        if retry_decision.action in (
            RetryAction.API_CONTRACT_RECOVERY, RetryAction.TARGETED,
            RetryAction.MISSING_FILES, RetryAction.FALLBACK_TARGETED,
        )
        else "full_set"
    )
    # Downstream branches below key off these booleans (not the mode string
    # directly) since that predates this function's decide_retry_action()
    # consolidation - derived from the single state.last_attempt_mode value
    # above rather than re-testing the same conditions a second time.
    use_targeted = state.last_attempt_mode == "targeted"
    use_api_contract_recovery = state.last_attempt_mode == "api_contract_recovery"
    use_missing_files = state.last_attempt_mode == "missing_files"
    use_fallback_targeted = state.last_attempt_mode == "fallback_targeted"
    attempt_operation = operation_for_attempt(
        state.last_attempt_mode, has_prior_failure=bool(state.error_context),
        recovery_phase=(
            state.api_contract_recovery.phase if state.api_contract_recovery else None
        ),
    )
    state.record_event(RunEvent(
        kind="attempt.started",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.ADVISORY,
        operation=attempt_operation.value,
        details={"mode": state.last_attempt_mode},
    ))
    attempt_evidence_scope.attempt_opened({
        "mode": state.last_attempt_mode, "operation": attempt_operation.value,
        "attempts_by_mode_before": dict(state.attempts_by_mode),
        "reserved_fallback": bool(retry_decision.reserved_fallback),
    })
    state.attempts_by_mode[state.last_attempt_mode] = state.attempts_by_mode.get(state.last_attempt_mode, 0) + 1
    if retry_decision.reserved_fallback:
        # STATE-RESERVED-FALLBACK-001: only the fallback's own allowance in
        # the global ceiling remains, so this attempt is the fallback's.
        state.record_event(RunEvent(
            kind="retry.reserved_fallback",
            attempt=state.attempt_number,
            source="attempt.run_attempt",
            authority=EventAuthority.AUTHORITATIVE,
            message=retry_decision.reason,
            details={"reason_code": RESERVED_FALLBACK_ALLOWANCE, "mode": state.last_attempt_mode},
        ))

    # PRD-008: a reused candidate replaces generation on the first attempt
    # only; it is written into this attempt's fresh worktree through the
    # same write path as model output. Its candidate gates are skipped only
    # when the resume also kept their outcomes (every verification
    # fingerprint matched); terminal regression always runs.
    reusing_candidate = bool(
        ctx.resume_plan is not None
        and ctx.resume_plan.reuse_candidate
        and ctx.resume_state
        and state.attempt_number == 1
    )
    skipping_candidate_gates = bool(reusing_candidate and ctx.resume_plan.skip_candidate_gates)
    # PRV-06 completion (2026-08-29, "MA8.1 <-> MA9 composition and
    # AttemptContext correctness"): a defensive baseline, not the real fix
    # (see the MA9-coordinated branch below, which now assigns a real,
    # candidate-view-aware value) - every branch below is still expected to
    # set a MEANINGFUL active_code_context of its own. This exists only so
    # that a FUTURE branch that forgets to (the exact live-reproduced defect
    # this closes) degrades to "no shown-context grounding check" -
    # edit_safety.py::apply_anchored_edits already treats an empty
    # shown_context as exactly that, the same sentinel _materialize_
    # candidate_content already uses for its own, narrower equivalent case -
    # rather than raising UnboundLocalError the moment a response comes back
    # as anchored edits.
    active_code_context = ""

    if reusing_candidate:
        logger.info(
            f"Resuming checkpoint '{ctx.run_id}': rebuilding the saved candidate in a fresh worktree "
            "instead of generating; "
            + ("its candidate gates are skipped (all verification fingerprints matched)"
               if skipping_candidate_gates else "its candidate gates run again")
            + "; terminal regression remains required."
        )
        files = [
            {"filepath": fp, "content": content}
            for fp, content in (ctx.resume_state.get("final_files") or {}).items()
        ]
        if skipping_candidate_gates:
            state.restore_gate_outcomes(list(ctx.resume_state.get("gate_outcomes") or state.gate_outcomes),
                                        source="resume_checkpoint")
        state.model_hops = list(ctx.resume_state.get("model_hops") or state.model_hops)
        model_override = None
        base_url_override = None
        api_key_override = None
        extra_body_override = None
    elif use_targeted or use_api_contract_recovery:
        # Targeted retry: always the primary model, never escalated
        # (see the budget comment above) - so the context budget is
        # always the primary model's own window, not a fallback's.
        reference_window = allocation_window(ctx.kernel.config)
        learned_reference = developer_reference(
            reference_window, ctx.learned_rag_context, ctx.skills_prompt, ctx.design, ctx.plan,
        )
        current_limit = _reserve_graph_context_budget(
            reference_window, ctx.skills_prompt, learned_reference, ctx.design, ctx.plan
        )
        model_override = None
        base_url_override = None
        api_key_override = None
        extra_body_override = None

        # CTX-001 P1 WP4 (C3/F9 fix): ctx.worktree_path, not
        # ctx.workspace_path - by the time any retry runs, create_git_
        # worktree() has already returned, so the worktree is the current-
        # source-of-truth root for the rest of this run (see
        # CurrentSourceResolver's own docstring for the full invariant).
        # WP7 dedup: exclude any path retry_prompts.py's own targeted-retry
        # renderer will show fresh/full a moment later (see
        # _graph_context_exclusion_set's own docstring).
        _graph_exclude = _graph_context_exclusion_set(state, ctx)
        current_graph_context = build_code_context(
            _filtered_candidates(ctx.matched_files, _graph_exclude),
            _filtered_candidates(ctx.related_files, _graph_exclude),
            ctx.worktree_path, current_limit,
            cache=ctx.source_cache,
        )
        planned_source = _planned_source_context(ctx, _graph_exclude)
        base_code_context = ctx.skills_prompt + planned_source
        if current_graph_context:
            base_code_context += current_graph_context
        if learned_reference:
            base_code_context += learned_reference

        if use_api_contract_recovery:
            state.last_implicated_files = sorted({
                item["owner"] for item in state.api_contract_recovery["violations"]
            })

        # MA9 (2026-08-29): an ACTIVE coordinated RepairContract takes over
        # this entire targeted-retry branch for as long as it stays ACTIVE -
        # see repair_contract.py's own module docstring for the PRV-06
        # Bucket A finding this closes. Never active at the same time as
        # API_CONTRACT_RECOVERY (a different, unrelated sticky contract on
        # the same state object). Every other targeted-retry mechanic below
        # this if/else (budget accounting, model_hops, the shared staged-
        # write/atomic-commit pipeline further down run_attempt()) is
        # unchanged either way - this only changes WHAT gets asked for and
        # HOW it's framed.
        active_repair_contract = (
            state.repair_contract
            if (
                not use_api_contract_recovery
                and state.repair_contract is not None
                and state.repair_contract.status == RepairContractStatus.ACTIVE
            ) else None
        )

        if active_repair_contract is not None:
            logger.info(
                "Targeted retry %d/%d: COORDINATED repair '%s' - generating %s together.",
                state.budgets.targeted_retry_count + 1, ctx.targeted_max_retries,
                active_repair_contract.id, ", ".join(active_repair_contract.generation_order),
            )
            state.record_event(RunEvent(
                kind="repair_retry", attempt=state.attempt_number, source="attempt.run_attempt",
                authority=EventAuthority.ADVISORY,
                message=f"RepairContract '{active_repair_contract.id}' still ACTIVE on retry.",
                details={
                    "repair_contract_id": active_repair_contract.id,
                    "immediate_targets": list(active_repair_contract.immediate_correction_targets),
                    "contract_remains_active": True,
                },
            ))
            state.model_hops.append(ctx.kernel.config.llm.model)
            dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
            files, coordinated_candidate_view = await _run_coordinated_repair_generation(
                state, ctx, active_repair_contract, base_code_context, dev_stream, attempt_operation,
                optional_sections=_developer_optional_sections(
                    ctx, current_graph_context, _graph_exclude, learned_reference, planned_source),
            )
            # PRV-06 completion (2026-08-29): active_code_context was
            # previously left UNASSIGNED on this branch - live-reproduced
            # UnboundLocalError the moment a coordinated response came back
            # as anchored edits and reached the shared anchored-edit
            # application further down this function. Built from the SAME
            # base_code_context every coordinated participant's own prompt
            # was seeded from, PLUS each participant's real staged content
            # (candidate_view) - never the stale authoritative baseline
            # alone, so a later participant's edit whose search-block only
            # exists in an earlier participant's just-generated candidate
            # (not yet written to the worktree, per Rule 2A) is correctly
            # recognized as grounded rather than incorrectly rejected.
            active_code_context = base_code_context + "".join(
                f"\n\n=== Candidate (not yet committed) for {path} ===\n{content}"
                for path, content in sorted(coordinated_candidate_view.items())
            )
        else:
            # VAL-001 G1-R3: centralized retry-time source/member-hint
            # preparation (target reachability, C3 member escalation, no-
            # progress gate) - see _prepare_retry_context's own docstring.
            retry_prep = _prepare_retry_context(
                state, ctx,
                target_files=state.last_implicated_files,
                prompt_window=allocation_window(ctx.kernel.config),
                model_identity=ctx.kernel.config.llm.model,
                base_code_context=base_code_context,
                enable_no_progress_gate=not use_api_contract_recovery,
            )
            retry_package = retry_prep.retry_package
            retry_error_context = retry_prep.retry_error_context
            task_desc, active_code_context = _build_targeted_retry_prompt(
                ctx.goal, ctx.plan, state.error_context, state.last_implicated_files,
                state.all_files_written, ctx.worktree_path, base_code_context,
                ecosystem_invariant_block=ctx.ecosystem_invariant_block,
                resource_lifecycle_block=ctx.resource_lifecycle_block,
                verification_contract_block=ctx.verification_contract_block,
                retry_package=retry_package,
                recovery_contract_block=ctx.recovery_contract_block,
            )
            if use_api_contract_recovery:
                contract = state.api_contract_recovery
                # E7: the phase-specific prompt text (task, baseline owner sources, code context) is rendered by
                # retry_prompts; which phase applies stays decided here.
                task_desc, active_code_context, required, protected = _build_api_contract_recovery_prompt(
                    ctx.goal, contract, state.all_original_contents, base_code_context,
                    repair_behavior=contract.phase in (
                        APIContractRecoveryPhase.REPAIR_BEHAVIOR,
                        APIContractRecoveryPhase.AWAIT_TERMINAL_SUCCESS,
                    ),
                )
                retry_error_context = task_desc
                logger.info(
                    "API_CONTRACT_RECOVERY %s %d/%d: signatures=%s protected_evidence=%s",
                    contract.phase.value, state.budgets.api_contract_recovery_count + 1,
                    API_CONTRACT_RECOVERY_MAX_ATTEMPTS, required, protected,
                )
            else:
                logger.info(f"Targeted retry {state.budgets.targeted_retry_count + 1}/{ctx.targeted_max_retries}: focusing on {', '.join(state.last_implicated_files)}.")

            # CTX-001 P1 C2 production integration: only for the plain
            # targeted-retry flow (never API_CONTRACT_RECOVERY, which
            # overwrote active_code_context with baseline_owners above -
            # untouched here). _prepare_retry_context() already built and
            # recorded the member-hint package (if any); appending its
            # rendering here is the only thing this branch still needs to
            # do with it.
            if not use_api_contract_recovery and retry_prep.member_hint_rendered:
                active_code_context += retry_prep.member_hint_rendered

            if use_api_contract_recovery and contract.phase is APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT:
                # Deterministic, not generative (control-plane audit,
                # 2026-08-30) - see _restore_api_contract_owners_
                # deterministically's own docstring for the live incident
                # this closes and why no Developer call belongs here at
                # all: the exact answer is already known. state.model_hops
                # deliberately untouched - no model was invoked this
                # attempt, so nothing belongs in a MODEL escalation history.
                files = _restore_api_contract_owners_deterministically(state, contract)
                logger.info(
                    "API_CONTRACT_RECOVERY %s %d/%d (deterministic restore, no Developer "
                    "call): owners=%s",
                    contract.phase.value, state.budgets.api_contract_recovery_count + 1,
                    API_CONTRACT_RECOVERY_MAX_ATTEMPTS, contract.owner_files,
                )
            else:
                state.model_hops.append(ctx.kernel.config.llm.model)

                dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
                files = await _run_developer_generation(
                    state, ctx,
                    optional_sections=_developer_optional_sections(
                        ctx, current_graph_context, _graph_exclude, learned_reference, planned_source),
                    task_description=task_desc,
                    design_context=(task_desc if use_api_contract_recovery else ctx.design),
                    existing_code_context=active_code_context,
                    stream_callback=dev_stream,
                    model_override=model_override,
                    base_url_override=base_url_override,
                    api_key_override=api_key_override,
                    extra_body_override=extra_body_override,
                    known_target_files=state.last_implicated_files,
                    prior_error_context=retry_error_context or None,
                    implicated_files=state.last_implicated_files,
                    error_source_context=state.last_error_source_context or None,
                    retry_temperature=ctx.kernel.config.llm.retry_temperature,
                    extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE,
                    files_with_current_content=state.all_files_written,
                    sibling_content_budget=_reserve_sibling_content_budget(allocation_window(ctx.kernel.config)),
                    operation_by_file=_operation_map(
                        ctx, state.last_implicated_files, attempt_operation, state,
                    ),
                    default_operation=attempt_operation,
                )
    elif use_fallback_targeted:
        # One-shot targeted fix on the first fallback model (see
        # fallback_targeted_attempted's own docstring above) - same
        # narrow scope as a primary-model targeted retry (just the
        # implicated files, fix-analysis/anchored-edit preference),
        # just on a different model, before paying for a full-set
        # regeneration. Set the one-shot flag immediately, not after
        # a result is known, so a crash/exception mid-attempt can
        # never cause this to be retried in a loop.
        state.budgets.fallback_targeted_attempted = True
        state.budgets.fallback_targeted_requested = False
        fallback = _select_developer_fallback(state, ctx, 1, targets=state.last_implicated_files or ())
        state.budgets.fallback_attempts_used += 1
        reference_window = allocation_window(ctx.kernel.config, fallback)
        learned_reference = developer_reference(
            reference_window, ctx.learned_rag_context, ctx.skills_prompt, ctx.design, ctx.plan,
        )
        current_limit = _reserve_graph_context_budget(
            reference_window, ctx.skills_prompt, learned_reference, ctx.design, ctx.plan
        )
        model_override = fallback.model
        base_url_override = fallback.base_url
        api_key_override = fallback.api_key
        extra_body_override = fallback.extra_body
        logger.info(
            f"Trying ONE targeted fix on fallback model {model_override} before "
            "falling back to full-set regeneration."
        )

        # CTX-001 P1 WP4/WP7 - see the targeted-retry branch above for the
        # full rationale (worktree-authoritative source, dedup against
        # retry_prompts.py's own all_files_written rendering).
        _graph_exclude = _graph_context_exclusion_set(state, ctx)
        current_graph_context = build_code_context(
            _filtered_candidates(ctx.matched_files, _graph_exclude),
            _filtered_candidates(ctx.related_files, _graph_exclude),
            ctx.worktree_path, current_limit,
            cache=ctx.source_cache,
        )
        planned_source = _planned_source_context(ctx, _graph_exclude)
        base_code_context = ctx.skills_prompt + planned_source
        if current_graph_context:
            base_code_context += current_graph_context
        if learned_reference:
            base_code_context += learned_reference

        # VAL-001 G1-R3: centralized retry-time source/member-hint
        # preparation - previously this branch never called
        # _resolve_retry_member_hints() at all (a confirmed, real C3
        # branch-coverage gap), so C3 member escalation was structurally
        # unavailable for the one-shot fallback-model targeted attempt.
        retry_prep = _prepare_retry_context(
            state, ctx,
            target_files=state.last_implicated_files,
            prompt_window=allocation_window(ctx.kernel.config, fallback),
            model_identity=model_override,
            base_code_context=base_code_context,
        )
        retry_package = retry_prep.retry_package
        retry_error_context = retry_prep.retry_error_context
        task_desc, active_code_context = _build_targeted_retry_prompt(
            ctx.goal, ctx.plan, state.error_context, state.last_implicated_files,
            state.all_files_written, ctx.worktree_path, base_code_context,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
            retry_package=retry_package,
            recovery_contract_block=ctx.recovery_contract_block,
        )
        if retry_prep.member_hint_rendered:
            active_code_context += retry_prep.member_hint_rendered
        logger.info(f"Fallback-targeted retry: focusing on {', '.join(state.last_implicated_files)}.")

        state.model_hops.append(model_override)

        dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
        files = await _run_developer_generation(
            state, ctx,
            optional_sections=_developer_optional_sections(
                ctx, current_graph_context, _graph_exclude, learned_reference, planned_source),
            task_description=task_desc,
            design_context=ctx.design,
            existing_code_context=active_code_context,
            stream_callback=dev_stream,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            extra_body_override=extra_body_override,
            known_target_files=state.last_implicated_files,
            prior_error_context=retry_error_context or None,
            implicated_files=state.last_implicated_files,
            error_source_context=state.last_error_source_context or None,
            retry_temperature=ctx.kernel.config.llm.retry_temperature,
            extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE,
            files_with_current_content=state.all_files_written,
            sibling_content_budget=_reserve_sibling_content_budget(allocation_window(ctx.kernel.config, fallback)),
            operation_by_file=_operation_map(
                ctx, state.last_implicated_files, attempt_operation, state,
            ),
            default_operation=attempt_operation,
        )
    elif use_missing_files:
        # Missing-file recovery: same primary-model-only, non-escalating
        # budget as a targeted retry (see the comment on
        # last_missing_files above) - asks for exactly the file(s) the
        # completeness check found missing, instead of re-describing an
        # error or regenerating the whole file set.
        reference_window = allocation_window(ctx.kernel.config)
        learned_reference = developer_reference(
            reference_window, ctx.learned_rag_context, ctx.skills_prompt, ctx.design, ctx.plan,
        )
        current_limit = _reserve_graph_context_budget(
            reference_window, ctx.skills_prompt, learned_reference, ctx.design, ctx.plan
        )
        model_override = None
        base_url_override = None
        api_key_override = None
        extra_body_override = None

        # CTX-001 P1 WP4/WP7 - see the targeted-retry branch above for the
        # full rationale (worktree-authoritative source, dedup against
        # retry_prompts.py's own all_files_written rendering).
        _graph_exclude = _graph_context_exclusion_set(state, ctx)
        current_graph_context = build_code_context(
            _filtered_candidates(ctx.matched_files, _graph_exclude),
            _filtered_candidates(ctx.related_files, _graph_exclude),
            ctx.worktree_path, current_limit,
            cache=ctx.source_cache,
        )
        planned_source = _planned_source_context(ctx, _graph_exclude)
        base_code_context = ctx.skills_prompt + planned_source
        if current_graph_context:
            base_code_context += current_graph_context
        if learned_reference:
            base_code_context += learned_reference

        # last_missing_files (from find_missing_expected_files) is always
        # bare basenames (compared against written files by basename).
        # Resolve each to a real path via architect_basename_to_path (built
        # once from the Architect's already-resolved file list above) -
        # falls back to the bare basename itself for root-level files like
        # pom.xml, or if a basename genuinely isn't in the map. This lets
        # known_target_files be used safely: confirmed live (Qpid+Ignite
        # validation) that leaving this to the model's own file-list call -
        # even when explicitly told exactly which 1-4 files are missing -
        # reliably returns only ONE of them, silently dropping the rest and
        # burning the whole retry budget without ever recovering them.
        resolved_missing_files = [
            ctx.architect_basename_to_path.get(basename, basename) for basename in state.last_missing_files
        ]

        # VAL-001 G1 D1: _build_missing_files_retry_prompt reads every file in
        # all_files_written fresh from the worktree (real, complete, current) -
        # record that same real evidence before _operation_map() runs.
        _record_all_files_written_as_exact_context(state, ctx, state.all_files_written)
        task_desc, active_code_context = _build_missing_files_retry_prompt(
            ctx.goal, ctx.plan, ctx.design, resolved_missing_files,
            state.all_files_written, ctx.worktree_path, base_code_context,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
        )
        logger.info(f"Missing-file recovery retry {state.budgets.targeted_retry_count + 1}/{ctx.targeted_max_retries}: adding {', '.join(resolved_missing_files)}.")

        state.model_hops.append(ctx.kernel.config.llm.model)

        dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
        files = await _run_developer_generation(
            state, ctx,
            optional_sections=_developer_optional_sections(
                ctx, current_graph_context, _graph_exclude, learned_reference, planned_source),
            task_description=task_desc,
            design_context=ctx.design,
            existing_code_context=active_code_context,
            stream_callback=dev_stream,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            extra_body_override=extra_body_override,
            known_target_files=resolved_missing_files,
            sibling_content_budget=_reserve_sibling_content_budget(allocation_window(ctx.kernel.config)),
            operation_by_file=_operation_map(
                ctx, resolved_missing_files, attempt_operation, state,
            ),
            default_operation=attempt_operation,
        )
    else:
        # Re-run context budget allocator dynamically for escalated model context window size
        reference_window = allocation_window(ctx.kernel.config)
        learned_reference = developer_reference(
            reference_window, ctx.learned_rag_context, ctx.skills_prompt, ctx.design, ctx.plan,
        )
        current_limit = _reserve_graph_context_budget(
            reference_window, ctx.skills_prompt, learned_reference, ctx.design, ctx.plan
        )
        model_override = None
        base_url_override = None
        api_key_override = None
        extra_body_override = None

        active_prompt_window = allocation_window(ctx.kernel.config)
        fallback = _select_developer_fallback(state, ctx, state.budgets.retry_count,
                                              targets=ctx.architect_files, allow_primary=True)
        if fallback is not None:
            state.budgets.fallback_attempts_used += 1
            model_override = fallback.model
            base_url_override = fallback.base_url
            api_key_override = fallback.api_key
            extra_body_override = fallback.extra_body
            active_prompt_window = allocation_window(ctx.kernel.config, fallback)
            reference_window = active_prompt_window
            learned_reference = developer_reference(
                reference_window, ctx.learned_rag_context, ctx.skills_prompt, ctx.design, ctx.plan,
            )
            current_limit = _reserve_graph_context_budget(
                reference_window, ctx.skills_prompt, learned_reference, ctx.design, ctx.plan
            )
            logger.info(f"Escalating compilation attempt to fallback model: {model_override} (Limit: {current_limit} tokens)")

        # On the very first attempt only (never a full-set retry, which
        # already escalates through the fallback chain above and is
        # regenerating in response to a real compile/test/runtime error,
        # not a clean slate) - if the Architect's design yielded a
        # deterministic file manifest, use it directly instead of asking
        # the model to independently re-derive the same list. Confirmed
        # live: "INCOMPLETE GENERATION" (the design called for N files,
        # fewer were written) was one of the most common first-attempt
        # failures observed this session, each one costing a full extra
        # missing-file-recovery retry cycle - this prevents that failure
        # category outright on attempt 1 instead of only recovering from
        # it after the fact. Falls back to today's ask-the-model-for-a-
        # list behavior when the design didn't yield a usable list.
        # expected_files_upfront is already resolved to real paths by
        # this point (architect_files comes pre-resolved either from the
        # Architect's own structured JSON file list, or, in the fallback
        # case above, from _resolve_file_paths_from_design already) - no
        # separate resolution step needed here anymore.
        #
        # CTX-001 P1 WP7: moved BEFORE the build_code_context() call below
        # (was previously computed after it) - purely a reordering, same
        # logic, same inputs (none of which depend on current_graph_context/
        # retry_package/task_desc computed further down) - needed so a
        # known-target path can be excluded from the Graph-RAG matched/
        # related candidate set before it's rendered, rather than
        # potentially shown twice at two different fidelities
        # (DUPLICATE_SOURCE_CONTEXT_PATHS=0).
        known_target_files = None
        active_failure_signature = state.budgets.last_failure_signature
        if state.budgets.retry_count == 0 and ctx.expected_files_upfront and active_failure_signature is None:
            known_target_files = ctx.expected_files_upfront
        elif (
            active_failure_signature is not None
            and state.last_implicated_files
            and state.budgets.scoped_full_set_failure_signature != active_failure_signature
        ):
            # Each distinct, grounded validator failure gets exactly one
            # dependency-closure repair before broad regeneration, independent
            # of global retry_count consumed by earlier, unrelated failures.
            # Repetition of the SAME signature broadens after this one shot.
            known_target_files = dependent_closure(
                state.last_implicated_files, ctx.generation_dependencies,
            )
            state.budgets.scoped_full_set_failure_signature = active_failure_signature
            logger.info(
                f"First full-set attempt for this failure family has grounded implicated "
                f"file(s) - scoping to their dependency closure "
                f"{', '.join(known_target_files)} instead of the full file set."
            )

        # CTX-001 P1 WP4/WP7: ctx.worktree_path (current-source invariant),
        # matched/related filtered to exclude known-target paths (which get
        # their own, higher-fidelity representation below) and
        # already-written paths (which retry_prompts.py's own renderer will
        # show fresh/full a moment later) - see
        # _graph_context_exclusion_set's own docstring.
        _graph_exclude = _graph_context_exclusion_set(state, ctx, known_target_files)
        current_graph_context = build_code_context(
            _filtered_candidates(ctx.matched_files, _graph_exclude),
            _filtered_candidates(ctx.related_files, _graph_exclude),
            ctx.worktree_path, current_limit,
            cache=ctx.source_cache,
        )
        planned_source = _planned_source_context(ctx, _graph_exclude)
        active_code_context = ctx.skills_prompt + planned_source
        if current_graph_context:
            active_code_context += current_graph_context
        if learned_reference:
            active_code_context += learned_reference

        # VAL-001 G1-R3: centralized retry-time source/member-hint
        # preparation (target reachability, C3 member escalation, no-
        # progress gate) - see _prepare_retry_context's own docstring.
        # retry_package.target_projections is empty only when state.
        # last_failure is None (_retry_package_for_attempt's own early
        # return) - true on a clean first attempt (this branch's other real
        # caller, where state.last_implicated_files is necessarily still
        # empty too) - _prepare_retry_context/_resolve_retry_member_hints/
        # _retry_package_for_attempt/_record_retry_projection_context_items
        # all no-op cleanly for that case, exactly as the code this replaces
        # already did.
        retry_prep = _prepare_retry_context(
            state, ctx,
            target_files=state.last_implicated_files,
            prompt_window=active_prompt_window,
            model_identity=model_override or ctx.kernel.config.llm.model,
            base_code_context=active_code_context,
        )
        retry_package = retry_prep.retry_package
        retry_error_context = retry_prep.retry_error_context
        task_desc, active_code_context = _build_full_set_retry_prompt(
            ctx.goal, ctx.plan, state.error_context, ctx.required_files_prompt_block,
            state.all_files_written, ctx.worktree_path, active_code_context,
            ctx.required_dependencies_prompt_block,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
            retry_package=retry_package,
            recovery_contract_block=ctx.recovery_contract_block,
        )
        if retry_prep.member_hint_rendered:
            active_code_context += retry_prep.member_hint_rendered

        # Track model hops
        state.model_hops.append(model_override or ctx.kernel.config.llm.model)

        if state.budgets.retry_count == 0:
            process_boundary_constraint = _initial_test_process_boundary_constraint(
                _runtime_contract_requirements(ctx), known_target_files,
            )
            if process_boundary_constraint:
                task_desc += process_boundary_constraint

        if state.attempt_number == 1 and known_target_files:
            # CTX-001 P1 WP7 (A3): instruction text (unconditional, fixed
            # cost, stays in task_desc) is now fully separate from source
            # content (budget-allocated, member-aware where possible,
            # explicitly omitted rather than silently capped at 24,000
            # chars - build_known_target_context(), context_budget.py).
            owner_contract = _brownfield_owner_contract_block(ctx, known_target_files)
            if owner_contract:
                task_desc += owner_contract
            known_target_limit = _reserve_graph_context_budget(
                active_prompt_window, ctx.skills_prompt, learned_reference, ctx.design, ctx.plan, current_graph_context,
            )
            # CTX-001 P1 C2: a known-target file's member is only ever
            # supplied here when an INDEPENDENT retrieval signal grounded
            # it for THIS exact path (see _resolve_known_target_member_
            # hints' own docstring) - a known target with no such evidence
            # gets {} and build_known_target_context() falls back to its
            # existing file-level handling unchanged.
            known_target_member_hints = _resolve_known_target_member_hints(ctx, known_target_files)
            known_target_rendered, known_target_package, goal_member_fallback = _target_package_with_goal_member_fallback(
                ctx, known_target_files, known_target_limit, active_prompt_window, known_target_member_hints,
            )
            if goal_member_fallback:
                state.record_event(RunEvent(
                    kind="context.known_target_goal_member_fallback", attempt=state.attempt_number,
                    source="attempt.run_attempt", authority=EventAuthority.ADVISORY,
                    message="a known target's whole source did not fit the protected room; the goal's own named "
                            "members of that owner were added as member hints and the package rebuilt",
                    details=goal_member_fallback,
                ))
            if known_target_rendered:
                active_code_context += known_target_rendered
                logger.info(
                    "Attempt 1 known-target context: %d unit(s), %d omission(s) for %s "
                    "(see run trace for full detail).",
                    len(known_target_package.relevant_files), len(known_target_package.omitted),
                    ", ".join(known_target_files),
                )
            # VAL-001 G1 D1: record real provenance for _completeness_gated_operation()'s
            # own authorization check - never inferred from file size. This is the exact
            # context package run 8b6ee803's attempt 1 built (skeleton tier, body elided)
            # for graphify/extractors/engine.py; recording it here is what lets the
            # invariant see that a whole-file replacement was never authorized for it.
            #
            # VAL-001 G1-R3: same same-path precedence fix as _prepare_retry_
            # context's own identical update() - a grounded member_id entry
            # must never be silently overwritten by a broader, less precise
            # same-path overview merely because it happens to sort later.
            _record_known_target_package(state, known_target_package.relevant_files)
            _record_omitted_members(state, known_target_package.omitted)
            # Internal evidence (WP6/observability) - never the full source,
            # just enough to answer "what tier/omission did each known
            # target actually get" from the run trace alone.
            state.record_event(RunEvent(
                kind="context.known_target_package",
                attempt=state.attempt_number,
                source="attempt.run_attempt",
                authority=EventAuthority.ADVISORY,
                message="Known-target context package built for the attempt-1 owner-contract replacement.",
                details={
                    "known_target_files": list(known_target_files),
                    "unit_count": len(known_target_package.relevant_files),
                    "tiers": [
                        {"path": item.path, "member_id": item.member_id, "tier": item.tier}
                        for item in known_target_package.relevant_files
                    ],
                    "omitted": list(known_target_package.omitted),
                    "package_hash": known_target_package.package_hash,
                    # CTX-001 P1 C2 observability: WHICH known-target paths
                    # got a validated, production-derived member hint at
                    # all - never the candidate names or source text.
                    "member_hint_paths": sorted(known_target_member_hints.keys()),
                },
            ))

        # FILE-INTEGRITY-CONTRACT-001B: Planner prose is never repository
        # mutation authority. A fenced block in the plan is never written as
        # a file (the former attempt-1 "Planner reuse" committed a shell line
        # as config.yaml); every attempt's files come from a parsed Developer
        # response through the authorized writer.
        # Generate code files
        dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
        files = await _run_developer_generation(
            state, ctx,
            optional_sections=_developer_optional_sections(
                ctx, current_graph_context, _graph_exclude, learned_reference, planned_source),
            task_description=task_desc,
            design_context=ctx.design,
            existing_code_context=active_code_context,
            stream_callback=dev_stream,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            extra_body_override=extra_body_override,
            known_target_files=known_target_files,
            prior_error_context=retry_error_context or None,
            implicated_files=state.last_implicated_files,
            error_source_context=state.last_error_source_context or None,
            retry_temperature=ctx.kernel.config.llm.retry_temperature,
            extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE,
            files_with_current_content=state.all_files_written,
            sibling_content_budget=_reserve_sibling_content_budget(active_prompt_window),
            operation_by_file=(
                _operation_map(ctx, known_target_files, attempt_operation, state)
                if known_target_files else None
            ),
            default_operation=attempt_operation,
        )

    # Recorded now, not derived by the caller afterward - see the fields'
    # own docstring in kriya/workflow/state.py. Every branch above sets all
    # four of these (to None for the primary model, or a fallback's values).
    if state.last_developer_call_attempt != state.attempt_number:
        # No Developer call this attempt; otherwise _enter_developer_model
        # already recorded the model the call actually went to (PRD-017).
        state.last_model_override = model_override
        state.last_base_url_override = base_url_override
        state.last_api_key_override = api_key_override
        state.last_extra_body_override = extra_body_override

    # Normalize filepaths before anything downstream uses them - the
    # Developer Agent occasionally returns an absolute path instead of a
    # relative one, which os.path.join(base, filepath) would silently
    # resolve to just `filepath` (discarding `base`) in every loop below.
    normalized_files = []
    for file_obj in files:
        raw_filepath = file_obj.get("filepath", "")
        normalized = normalize_written_filepath(raw_filepath, ctx.workspace_path)
        if normalized is None:
            logger.warning(f"Developer Agent returned an unusable filepath '{raw_filepath}' (absolute path outside the workspace, or empty) - skipping this file.")
            continue
        if normalized != raw_filepath:
            logger.info(f"Normalized Developer Agent filepath '{raw_filepath}' -> '{normalized}'.")
        file_obj["filepath"] = normalized
        normalized_files.append(file_obj)
    files = normalized_files

    # Sticky existing-owner resolution, applied on EVERY attempt/retry - not
    # just the Architect's initial file list (see workflow.py's own call to
    # this SAME function for that first pass, before any Developer call has
    # happened at all). Found live, PRV-03 legacy (2026-08-27): the
    # Architect-stage resolution correctly mapped an invented
    # 'service/CustomerService.java' back to the real, existing
    # 'CustomerService.java' on attempt 1 - but a LATER full-set retry (the
    # Developer's own initiative, not the Architect's plan) reinvented the
    # exact same parallel-package path again. Nothing re-applied that same
    # resolution to a Developer response's own filepath choices on a later
    # attempt, so it compiled as a genuine second declaration (a real
    # "duplicate type" compile error), and every subsequent retry kept
    # targeting the invented path for repair instead of the real owner -
    # 5 further attempts burned entirely on malformed repair responses for
    # a path that should never have reached disk. Reusing the SAME
    # resolution function here (not a parallel duplicate implementation)
    # closes the loop generically: a model-invented parallel-package path
    # for an ALREADY-OWNED artifact is redirected back to the real owner
    # before it's ever written, treated as a compile target, or targeted
    # for repair - on every attempt, not just the first.
    #
    # Must exclude anything THIS RUN has already legitimately established
    # (state.all_files_written / ctx.established_files) before resolving -
    # prefer_existing_artifact_owners() only checks ctx.workspace_path (the
    # pristine ORIGINAL brownfield repo) to decide what already exists, so
    # a genuinely new file this run created on an earlier attempt (which
    # naturally doesn't exist in the pristine original) looks identical to
    # a truly invented duplicate. Found live, PRV-03 legacy (2026-08-27):
    # attempt 1 legitimately created a new dto/CustomerDto.java; attempt 5
    # edited that SAME already-established file again, and this check
    # (before this fix) mistook it for an invented duplicate and redirected
    # it onto CustomerController.java via the weakest, semantic-overlap-
    # only fallback tier - a real class merge, not a same-basename/same-
    # type collision. An existing MISDIRECTED EDIT gate happened to catch
    # the resulting bad anchored edit before it corrupted
    # CustomerController.java, but that's a lucky downstream catch, not a
    # guarantee - the redirect itself must never fire on a file this run
    # already owns.
    already_established = set(state.all_files_written) | set(ctx.established_files)
    candidate_paths = [file_obj["filepath"] for file_obj in files]
    paths_to_resolve = [path for path in candidate_paths if path not in already_established]
    resolution: Dict[str, str] = {}
    if paths_to_resolve:
        # FILE-RESOLUTION-SCOPE-ESCAPE-001: the unit's authorized files and
        # its planned new artifacts, read from THIS attempt's context (a
        # revised scope is honoured; a retry decides identically). An owner
        # outside the scope is refused and recorded - the write authority
        # decides the Developer's own path as it always has.
        artifact_resolution = resolve_planned_artifact_owners(
            paths_to_resolve, ctx.goal, ctx.workspace_path,
            authorized_paths=artifact_resolution_scope(ctx.write_scope_mode, ctx.allowed_write_relpaths),
            new_artifact_paths=planned_new_artifact_paths(ctx.structured_plan, ctx.current_subtask_id),
            declared_paths=state.all_files_written,   # W3-3 A2 follow-on: proven written by THIS run (path-exact, per-run object, not resumed)
        )
        resolution = dict(zip(paths_to_resolve, artifact_resolution.resolved, strict=True))  # one per path
        for indication in artifact_resolution.undeclared_owner_indications:
            state.record_event(RunEvent(
                kind="file_resolution.owner_undeclared", attempt=state.attempt_number,
                source="attempt.developer_file_resolution", authority=EventAuthority.ADVISORY,
                message=(f"{indication['reason_code']}: Developer path '{indication['planned']}' points at existing '{indication['owner']}', which is "
                         "untracked and was not written by this run; the path is kept"),
                details={**indication, "stage": "developer"},
            ))
        for refusal in artifact_resolution.refused_redirects:
            state.record_event(RunEvent(
                kind="file_resolution.redirect_refused", attempt=state.attempt_number,
                source="attempt.developer_file_resolution", authority=EventAuthority.AUTHORITATIVE,
                message=(f"{refusal['reason_code']}: Developer path '{refusal['planned']}' resolves to "
                         f"'{refusal['owner']}' outside this unit's authorized files; the path is kept"),
                details={**refusal, "authorized_paths": list(ctx.allowed_write_relpaths), "stage": "developer"},
            ))
    resolved_paths = [resolution.get(path, path) for path in candidate_paths]
    if resolved_paths != candidate_paths:
        for file_obj, resolved_path in zip(files, resolved_paths, strict=True):  # one path per file
            original_path = file_obj["filepath"]
            if resolved_path != original_path:
                logger.warning(
                    "Redirected Developer-invented parallel-owner path '%s' back to sticky "
                    "existing owner '%s' - the existing owner is authoritative; the invented "
                    "path is abandoned before ever reaching disk.", original_path, resolved_path,
                )
            file_obj["filepath"] = resolved_path
        # A redirect can now collide two entries onto the same real path
        # (an invented duplicate remapped onto an owner this SAME response
        # also wrote directly under its real name) - keep the LAST one,
        # matching this function's own natural "later entry wins" order.
        deduped: Dict[str, Dict[str, Any]] = {}
        for file_obj in files:
            deduped[file_obj["filepath"]] = file_obj
        files = list(deduped.values())

    # P9-R1 (P9/PRV-08, 2026-09-08): while API_CONTRACT_RECOVERY is active
    # (RESTORE_PUBLIC_CONTRACT AND REPAIR_BEHAVIOR both narrow the Developer's
    # own target scope to state.api_contract_recovery.owner_files ONLY - see
    # this attempt's known_target_files=state.last_implicated_files above,
    # and _restore_api_contract_owners_deterministically's own [owner]-only
    # return - confirmed against the real P9 log: the collision this fixes
    # actually fires during REPAIR_BEHAVIOR, attempts 3-4, not the single
    # RESTORE_PUBLIC_CONTRACT attempt itself, which transitions before ever
    # reaching quality gates), this attempt's own `files` legitimately omits
    # every OTHER expected file - the completeness check below has no way to
    # know that omission was deliberate and correct, not the Developer
    # silently under-delivering. For each expected file outside the active
    # recovery owner set that isn't already part of THIS attempt's own
    # `files`, fold in its own most-recent cumulative candidate content
    # (state.last_candidate_contents, updated below from every attempt's
    # own `files`, deliberately BEFORE quality-gate outcome is known - Case
    # A's own "changed A" survives even though the batch containing it was
    # ultimately rejected for an unrelated reason, exactly the real P9
    # shape). A file with NO cumulative entry (never generated in any
    # attempt) is left alone - still correctly reported missing (Case B);
    # never fabricated from baseline merely to satisfy completeness. This is
    # the SAME `files` list every downstream gate (brownfield check,
    # compile, staged writes, the completeness check itself) sees - no
    # second, shadow candidate representation.
    if state.api_contract_recovery is not None:
        recovery_owner_files = set(state.api_contract_recovery.owner_files)
        already_in_files = {file_obj["filepath"] for file_obj in files}
        for expected_path in ctx.architect_files:
            if expected_path in recovery_owner_files or expected_path in already_in_files:
                continue
            cumulative_content = state.last_candidate_contents.get(expected_path)
            if cumulative_content is not None:
                # VAL-001 G1 D1-A (2026-09-18): this entry is Kriya's OWN
                # continuity bookkeeping re-asserting a file's already-
                # tracked cumulative state so completeness holds - it is not
                # a mutation the Developer returned THIS attempt at all, so
                # it carries no "actual returned shape" for
                # _validate_actual_mutation_authority() to evaluate.
                # Unmarked, this synthetic entry would look identical to a
                # genuine unauthorized full-file replacement and be wrongly
                # rejected; marked, the per-file loop below exempts it the
                # same way it exempts a RESTORE_PUBLIC_CONTRACT restoration.
                files.append({
                    "filepath": expected_path, "content": cumulative_content,
                    "_kriya_carried_forward_content": True,
                })
    # VAL-001 G1 D1-B (2026-09-18): this cache is recorded further down,
    # inside the per-file operation-authority enforcement loop, ONLY after
    # that file's own actual returned mutation shape has cleared
    # _validate_actual_mutation_authority() (or was exempt as carried-
    # forward content already itself authorized in an earlier attempt) -
    # never unconditionally for every entry in `files` up front. Recording
    # unconditionally here (the original P9-R1 shape) would let a full-file
    # candidate this SAME attempt is about to reject on authority grounds
    # still land in the cache, ready to be silently resurrected as
    # `_kriya_carried_forward_content` on a LATER attempt that never asks
    # the Developer for this file at all - required authority case #6
    # ("failed/rejected candidate-derived full projection, later retry ->
    # REJECT"). See that loop's own comment for the unchanged Case A/B/C
    # recovery semantics this preserves exactly, just gated on authority.

    # Brownfield ownership is enforced before any candidate byte reaches the
    # sandbox. Path resolution alone is insufficient: a model can target the
    # correct existing pathname while pasting an invented replacement class
    # into it. Detect that contract removal now, not after compiler retries or
    # terminal regression.
    #
    # Runs on EVERY attempt, not just the first - found live, PRV-03
    # hardened (2026-08-27): the actual contract-breaking change (Customer's
    # record component list going from 4 to 5) was introduced by the model
    # on attempt 10, not attempt 1. The old `state.attempt_number == 1`
    # restriction meant this whole detection - and the sticky
    # api_contract_recovery repair flow it feeds - never even looked at
    # that attempt's own candidate. `not state.api_contract_recovery` still
    # guards against double-checking once a violation is already being
    # actively recovered (find_unrestored_public_api_contracts/find_
    # protected_api_reference_changes own that phase instead, further
    # below).
    if not state.api_contract_recovery:
        baseline_contents = {}
        candidate_contents = {}
        for file_obj in files:
            filepath = file_obj["filepath"]
            workspace_file = os.path.join(ctx.workspace_path, filepath)
            if not os.path.isfile(workspace_file) or file_obj.get("content") is None:
                continue
            try:
                with open(workspace_file, "r", encoding="utf-8", errors="replace") as handle:
                    baseline_contents[filepath] = handle.read()
            except OSError:
                continue
            candidate_contents[filepath] = file_obj["content"]
        # CORR-016 (P9/PRV-08, 2026-09-08, DIRECT-only): direct_contract_
        # authorizations() is a pure function of ctx.grounding_goal (the raw,
        # unmediated user request) and ctx.structured_plan - filtered here to
        # exactly this subtask's own legal_scope, never a wider allowlist.
        # See kriya/workflow/contract_authority.py's own module docstring.
        run_direct_authorizations = derive_direct_contract_authorizations(
            ctx.grounding_goal, ctx.structured_plan,
        )
        direct_authorizations = [
            authorization for authorization in run_direct_authorizations
            if authorization.legal_scope.get("subtask_id") == ctx.current_subtask_id
        ]
        # PRD-023: a human-approved change of this subtask's scope is
        # authorized exactly like a DIRECT one (same per owner/symbol/category
        # match inside the detector).
        human_authorizations = [
            authorization for authorization in state.human_contract_authorizations
            if authorization.legal_scope.get("subtask_id") == ctx.current_subtask_id
        ]
        early_api_violations = find_brownfield_public_api_changes(
            ctx.workspace_path, baseline_contents, candidate_contents, ctx.goal,
            direct_authorizations + human_authorizations,
        )
        contract_classifications: List[Any] = []
        escalation_reason_code: Optional[str] = None
        # PRD-023: the changes an authorization covered are classified too
        # (AUTHORIZED_DIRECT/AUTHORIZED_HUMAN evidence), found as the
        # difference against the same detector without authorizations.
        authorized_api_changes: List[Dict[str, Any]] = []
        if direct_authorizations or human_authorizations:
            remaining_keys = {(v["owner"], v["removed_signature"]) for v in early_api_violations}
            authorized_api_changes = [
                v for v in find_brownfield_public_api_changes(
                    ctx.workspace_path, baseline_contents, candidate_contents, ctx.goal, [],
                )
                if (v["owner"], v["removed_signature"]) not in remaining_keys
            ]
        if early_api_violations or authorized_api_changes:
            early_api_violations, contract_classifications, escalation_reason_code = (
                await _classify_and_escalate_contract_changes(
                    state, ctx, early_api_violations, baseline_contents, candidate_contents,
                    run_direct_authorizations, authorized_api_changes,
                )
            )
        if early_api_violations:
            state.all_original_contents.update(baseline_contents)
            for baseline_path in baseline_contents:
                _capture_original(state, ctx, baseline_path)
            for evidence_path in sorted({
                path
                for item in early_api_violations
                for path in item.get("evidence_files", [])
            }):
                if evidence_path not in state.all_original_contents:
                    _capture_original(state, ctx, evidence_path)
            failure = Failure(
                type="brownfield_public_api_changed",
                message=(
                    "BROWNFIELD PUBLIC API REJECTED BEFORE WRITE: the candidate would "
                    "replace or remove an established public signature. Restore the existing "
                    "owner contract before behavioral repair."
                ),
                raw_output=str(early_api_violations),
                source="ownership_gate", authority="deterministic",
                file_locations=[
                    FileLocation(filepath=item["owner"])
                    for item in early_api_violations
                ],
                likely_files=sorted({item["owner"] for item in early_api_violations}),
                diagnostics={
                    "api_contract_recovery": {"violations": early_api_violations},
                    # PRD-023: how each change was classified, and why no
                    # escalation authorized it.
                    "contract_classifications": [c.to_dict() for c in contract_classifications],
                    **({"reason_code": escalation_reason_code} if escalation_reason_code else {}),
                },
                attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # CORR-018-P1 (A3-bound slice, 2026-09-09): a SEPARATE, deterministic
        # check from the brownfield-API gate immediately above - that gate asks
        # "did a protected public signature change without authority"; this one
        # asks "did anything structurally material change outside explicitly
        # authorized regions", including inside a signature-preserving method
        # body (the exact P10 production gap - CustomerPrinter.print() kept its
        # signature while its body silently changed). Deliberately its own
        # module/function/reason-code space, never merged into
        # find_brownfield_public_api_changes() - see semantic_region_authority.py's
        # own module docstring. Per-region checking is a no-op for a file
        # absent from ctx.authorized_semantic_regions - unaffected by A3 or
        # by CORR-018's general-case closure (2026-09-13), which populates
        # this list automatically for ordinary generate/fix and structured-
        # plan calls, but ONLY when autonomy.semantic_region_enforcement_
        # required is True (see kriya/workflow/semantic_scope_derivation.py).
        # strict_existing_java_files below is what makes an UNLISTED
        # existing .java file's own real change a rejection rather than a
        # silent no-op, and is itself gated by that same flag - default
        # False preserves every existing caller's behavior exactly.
        semantic_violations = find_unauthorized_semantic_changes(
            baseline_contents, candidate_contents, ctx.authorized_semantic_regions,
            strict_existing_java_files=ctx.kernel.config.autonomy.semantic_region_enforcement_required,
        )
        if semantic_violations:
            evidence = "; ".join(
                f"file={v.relpath}, member={v.member_key}, reason={v.reason_code}: {v.detail}"
                for v in semantic_violations
            )
            failure = Failure(
                type="semantic_region_unauthorized",
                message=(
                    "SEMANTIC REGION REJECTED BEFORE WRITE: the candidate changes source "
                    "outside the explicitly authorized region(s) for this run. Only the "
                    "approved change, and its narrowly authorized supporting regions, may "
                    "differ from the baseline."
                ),
                raw_output=evidence,
                source="semantic_authority_gate", authority="deterministic",
                file_locations=[FileLocation(filepath=v.relpath) for v in semantic_violations],
                likely_files=sorted({v.relpath for v in semantic_violations}),
                diagnostics={
                    "semantic_region_authority": {
                        "violations": [
                            {"relpath": v.relpath, "member_key": v.member_key,
                             "reason_code": v.reason_code, "detail": v.detail}
                            for v in semantic_violations
                        ],
                    },
                },
                attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # UNREQUESTED_ARCHITECTURAL_SURFACE: verification strategy must not be
        # allowed to mutate the product's real architectural surface. Same
        # pre-write timing, same goal-explicit-request escape hatch, same
        # typed-Failure + likely_files targeted-retry pattern as the
        # brownfield-API check above (deliberately NOT the api_contract_
        # recovery phased state machine - see find_unrequested_architectural_
        # surfaces's own docstring for why this needs only a single-shot fix,
        # not a two-phase restore-then-repair). Found live, PRV-04
        # (2026-08-27): a runtime-verification pass added its own `public
        # static void main(...)` to a brand-new AppTest.java instead of
        # reusing the real, correctly-extended App.main(...) entrypoint.
        #
        # Deliberately NOT reusing baseline_contents/candidate_contents from
        # the brownfield-API check above: that loop skips any file that
        # doesn't already exist in the workspace (os.path.isfile(...) guard),
        # since a REMOVED/CHANGED public signature is only a meaningful
        # concept for a pre-existing owner. The confirmed PRV-04 incident is
        # the opposite shape - a brand-NEW file introducing an entrypoint -
        # so this needs every file_obj in the response, existing or not.
        surface_original_contents: Dict[str, str] = {}
        surface_candidate_contents: Dict[str, str] = {}
        for file_obj in files:
            filepath = file_obj["filepath"]
            if file_obj.get("content") is None:
                continue
            workspace_file = os.path.join(ctx.workspace_path, filepath)
            if os.path.isfile(workspace_file):
                try:
                    with open(workspace_file, "r", encoding="utf-8", errors="replace") as handle:
                        surface_original_contents[filepath] = handle.read()
                except OSError:
                    pass
            surface_candidate_contents[filepath] = file_obj["content"]
        surface_violations = find_unrequested_architectural_surfaces(
            ctx.workspace_path, surface_original_contents, surface_candidate_contents, ctx.goal,
        )
        if surface_violations:
            failure = Failure(
                type="unrequested_architectural_surface",
                message=(
                    "UNREQUESTED ARCHITECTURAL SURFACE REJECTED BEFORE WRITE: the candidate "
                    "introduces a new executable entrypoint (public static void main(...)) "
                    "that this repository's existing baseline entrypoint(s) "
                    f"({', '.join(sorted({e for v in surface_violations for e in v['baseline_entrypoints']}))}) "
                    "did not have, and the goal never asked for a second one. Verification "
                    "strategy must not alter persistent application architecture - remove this "
                    "entrypoint; if runtime verification needs to run something, invoke the "
                    "existing entrypoint, an existing test, or a harness-level command instead."
                ),
                raw_output=str(surface_violations),
                source="ownership_gate", authority="deterministic",
                file_locations=[
                    FileLocation(filepath=item["file"]) for item in surface_violations
                ],
                likely_files=sorted({item["file"] for item in surface_violations}),
                diagnostics={"reason_code": "UNREQUESTED_ARCHITECTURAL_SURFACE"},
                attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

    # E10: run_attempt's per-file loops (the operation contract, the originals capture, the staging loop, the batch
    # commit) run as one candidate transaction (kriya/workflow/candidate_transaction.py) with today's atomic
    # semantics: the first refusal refuses the whole attempt; the checks between its phases stay here.
    transaction = candidate_transaction.CandidateTransaction(_candidate_deps, state, ctx, files, attempt_operation)
    transaction.check_operation_contracts()

    # Captured here (before any write can raise) rather than after the write
    # loop below, so a self-diagnosis is never lost to an anchored-edit
    # exception on an unrelated file later in the same batch. Paired with
    # the failure signature THIS attempt was responding to - state.budgets.
    # last_failure_signature is still the PREVIOUS failure's signature at
    # this point (retry_strategy.py only overwrites it after the NEXT
    # failure is classified) - see kriya/workflow/attribution.py's
    # extract_self_diagnosed_files() and retry_strategy.py's signature-gated
    # consumption of this field.
    # Only overwritten when THIS attempt actually produced a fresh self-
    # diagnosis - left untouched otherwise, not reset to None.
    #
    # PRV-05 run 7 (2026-08-28): the THIRD tuple element (state.attempt_number,
    # i.e. THIS attempt) narrows this memory's original design, which relied
    # on signature equality ALONE to survive intervening attempts
    # indefinitely ("attempt N redirects, attempt N+1 returns no analysis,
    # the same signature recurs at attempt N+2 - the diagnosis must still be
    # there"). Live evidence proved that design unsafe: retry_strategy.py
    # deliberately COLLAPSES an edit-protocol failure's signature onto
    # whatever authoritative failure it's repairing (anchored_edit/
    # structural_corruption/etc - see _REPAIR_FEEDBACK_FAILURE_TYPES), so a
    # diagnosis captured mid-repair inherits that same collapsed signature -
    # and a GENUINELY NEW, later occurrence of the ORIGINAL authoritative
    # failure (not a continuation of the repair the diagnosis was about) can
    # recompute to the identical signature purely because the message text
    # matches. PRV-05 s1: a diagnosis captured responding to attempt 4's
    # structural_corruption ("the fix is really in JsonService.java, not
    # pom.xml") kept winning attribution for attempts 5, 6, 7, AND 8's
    # entirely fresh migration_incomplete failures, none of which could ever
    # be satisfied by re-editing JsonService.java again. Requiring the
    # diagnosis's own attempt number to equal the CURRENTLY-processed
    # failure's attempt number restricts reuse to "this attempt's own
    # outcome" - i.e. the diagnosis explains THIS failure, not some later,
    # merely-same-signature one. Signatures remain the right tool for BUDGET
    # grouping (retry_strategy.py's failure_family_changed); they are not,
    # by themselves, sufficient for diagnosis freshness.
    #
    # Unioned with ctx.established_files (see that field's own docstring) so a
    # correct diagnosis naming an EARLIER milestone's file - one THIS attempt
    # never wrote itself - is still a valid redirect candidate, not silently
    # unmatchable.
    self_diagnosed = extract_self_diagnosed_files(
        files, sorted(set(state.all_files_written) | set(ctx.established_files)),
    )
    if self_diagnosed:
        state.last_self_diagnosis = (
            state.budgets.last_failure_signature, self_diagnosed, state.attempt_number,
        )

    # Test-obligation preservation (2026-09-20): a planned-but-nonexistent
    # test artifact that got redirected onto an already-existing owner must
    # never have its acceptance obligation discharged by a bare NO CHANGE
    # NEEDED response for that owner - file/semantic similarity alone is not
    # evidence the goal's own test-coverage intent is actually satisfied.
    # Applies on EVERY attempt mode (not just targeted/fallback_targeted,
    # unlike the block below) - a fresh, first full-set attempt's own bare
    # NO CHANGE NEEDED response is the live incident this closes, not a
    # retry-only shape. See find_unpreserved_test_obligation()'s own
    # docstring (kriya/workflow/file_resolution.py) for the full check.
    # Reuses the ordinary QualityGateFailure/retry-loop machinery unchanged
    # - no new retry budget, no new control flow.
    _unpreserved_obligation = find_unpreserved_test_obligation(
        files, state.redirected_test_obligations, state.all_files_written, state.attempt_number,
    )
    if _unpreserved_obligation is not None:
        state.record_gate_outcome(_unpreserved_obligation.to_gate_outcome())
        raise QualityGateFailure(_unpreserved_obligation)

    # "NO CHANGE NEEDED" is useful negative attribution evidence, not a
    # successful repair. In a targeted attempt, rerunning compile/tests/runtime
    # after every returned target was explicitly left untouched wastes an
    # expensive gate and routes the same failure back to the same file. Turn it
    # into a retry signal before any write or gate. If FIX ANALYSIS names a
    # different known file, redirect there. Otherwise preserve a preceding
    # deterministic locator; only genuinely ungrounded scope widens.
    all_targets_rejected = all_results_are_no_change(files)
    if (state.last_attempt_mode in ("targeted", "fallback_targeted") and all_targets_rejected
            and not _only_response_validity_failures(state)):
        state.record_event(RunEvent(
            kind="operation.no_change",
            attempt=state.attempt_number,
            source="developer",
            authority=EventAuthority.ADVISORY,
            operation="no_change_assessment",
            message="Every targeted result rejected the selected file scope.",
        ))
        returned_targets = [f.get("filepath", "") for f in files if f.get("filepath")]
        # An advisory NO CHANGE response may redirect a weak/judgment-based
        # attribution, but it must not erase an authoritative deterministic
        # locator.  Preserve only the files that were both locator-backed and
        # actually in this response's target set; this cannot pull unrelated
        # stale evidence forward.  A grounded alternate named by the response
        # still wins because it is explicit current-turn root-cause evidence.
        preserved_locator_files = _preserved_authoritative_locator_files(
            state, returned_targets, self_diagnosed,
        )
        likely_files = list(self_diagnosed or preserved_locator_files)
        _request_fallback_for_rejected_authoritative_target(
            state, ctx, preserved_locator_files,
        )
        evidence = "\n\n".join(
            f"{f.get('filepath', '(unknown file)')}: "
            f"{f.get('analysis') or '(no FIX ANALYSIS supplied)'}" for f in files
        )
        if self_diagnosed:
            attribution_message = (
                f"Its own analysis instead names: {', '.join(self_diagnosed)}."
            )
        elif preserved_locator_files:
            attribution_message = (
                "The advisory response supplied no grounded alternate; retaining the "
                "preceding authoritative locator for: "
                f"{', '.join(preserved_locator_files)}."
            )
        else:
            attribution_message = (
                "No grounded alternate file or authoritative locator was available; "
                "widen the next attempt to the full file set."
            )
        failure = Failure(
            type="attribution_rejected",
            message=(
                "TARGET ATTRIBUTION REJECTED: the Developer reported NO CHANGE NEEDED "
                f"for every targeted file ({', '.join(returned_targets)}). "
                + attribution_message
            ),
            raw_output=evidence,
            source="developer",
            authority="advisory",
            file_locations=[FileLocation(filepath=f) for f in likely_files],
            likely_files=likely_files,
            diagnostics=_preserved_attribution_diagnostics(preserved_locator_files),
            attempt=state.attempt_number,
        )
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure)

    transaction.capture_originals()

    staged_writes = transaction.stage(active_code_context, self_diagnosed)

    # Protected callers/tests are contract evidence, not repair targets.  An
    # earlier rejected candidate may already have redirected them, so owner
    # recovery restores their exact baseline content deterministically rather
    # than asking the model to edit evidence.  This is deliberately limited to
    # RESTORE_PUBLIC_CONTRACT and does not compare formatting or line order.
    recovery = state.api_contract_recovery or {}
    if recovery and recovery.phase is APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT:
        staged_targets = {staged.target_path for staged in staged_writes}
        current_evidence = {}
        for evidence_path in recovery.protected_evidence_files:
            try:
                with open(
                    os.path.join(ctx.worktree_path, evidence_path),
                    "r", encoding="utf-8", errors="replace",
                ) as handle:
                    current_evidence[evidence_path] = handle.read()
            except OSError:
                current_evidence[evidence_path] = ""
        semantically_damaged = {
            item["evidence_file"]
            for item in find_protected_api_reference_changes(
                state.all_original_contents, current_evidence, recovery,
            )
        }
        for evidence_path in recovery.get("protected_evidence_files", []):
            baseline = state.all_original_contents.get(evidence_path)
            target_path = os.path.join(ctx.worktree_path, evidence_path)
            if (
                baseline is None or target_path in staged_targets
                or evidence_path not in semantically_damaged
            ):
                continue
            current = current_evidence[evidence_path]
            if current != baseline:
                staged_writes.append(_baseline_restoration_write(state, target_path, evidence_path))
                logger.info(
                    "RESTORE_PUBLIC_CONTRACT: deterministically restoring protected "
                    "baseline evidence %s", evidence_path,
                )

    _stage_ownership_redirect_restoration(state, ctx, staged_writes)

    transaction.commit(staged_writes)

    # A recovery candidate must restore its sticky baseline contract before
    # any compiler, test runner, or LLM-backed gate is allowed to consume it.
    if state.api_contract_recovery:
        recovery_contents = {}
        recovery_paths = {
            item["owner"]
            for item in state.api_contract_recovery.get("violations", [])
        } | set(state.api_contract_recovery.get("protected_evidence_files", []))
        for recovery_path in recovery_paths:
            try:
                with open(
                    os.path.join(ctx.worktree_path, recovery_path),
                    "r", encoding="utf-8", errors="replace",
                ) as handle:
                    recovery_contents[recovery_path] = handle.read()
            except OSError:
                recovery_contents[recovery_path] = ""
        unrestored = find_unrestored_public_api_contracts(
            recovery_contents, state.api_contract_recovery,
        )
        redirected = find_protected_api_reference_changes(
            state.all_original_contents, recovery_contents, state.api_contract_recovery,
        )
        if unrestored:
            required = "; ".join(
                f"{item['owner']}::{item['removed_signature']}"
                for item in unrestored
            )
            failure = Failure(
                type="api_contract_recovery_incomplete",
                message=(
                    "API_CONTRACT_RECOVERY INCOMPLETE: restore every authoritative "
                    f"baseline signature before quality gates ({required})."
                ),
                raw_output=str({"unrestored": unrestored}),
                source="ownership_gate", authority="deterministic",
                file_locations=[FileLocation(filepath=item["owner"]) for item in unrestored],
                likely_files=sorted({item["owner"] for item in unrestored}),
                diagnostics={"api_contract_recovery": state.api_contract_recovery.to_diagnostics()},
                attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        if redirected:
            failure = Failure(
                type="api_contract_evidence_restore_required",
                message=(
                    "API_CONTRACT_RECOVERY EVIDENCE RESTORE REQUIRED: owner signatures "
                    "are restored, but baseline callers/tests were redirected, removed, or "
                    "weakened. Restore their original contract calls and assertions before gates."
                ),
                raw_output=str({"redirected": redirected}),
                source="ownership_gate", authority="deterministic",
                file_locations=[
                    FileLocation(filepath=item["evidence_file"]) for item in redirected
                ],
                likely_files=sorted({item["evidence_file"] for item in redirected}),
                diagnostics={"api_contract_recovery": state.api_contract_recovery.to_diagnostics()},
                attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        if state.api_contract_recovery.phase is APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT:
            state.api_contract_recovery.owner_contract_restored()
            logger.info(
                "RESTORE_PUBLIC_CONTRACT pre-check passed; transitioning to "
                "REPAIR_BEHAVIOR before quality gates."
            )
            raise RecoveryPhaseAdvanced(
                APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT.value,
                APIContractRecoveryPhase.REPAIR_BEHAVIOR.value,
            )
        logger.info(
            "REPAIR_BEHAVIOR pre-check passed; baseline contract remains restored."
        )
        # Keep the contract sticky through candidate gates and terminal
        # regression.  workflow.py clears it only after terminal regression
        # passes; any intervening gate failure therefore returns to
        # REPAIR_BEHAVIOR with the exact signature still authoritative.

    # A POM has no dependency on sibling source files, but it is now validated
    # after the atomic candidate batch so no quality gate can observe a partial
    # model response.  This remains much cheaper than dependency resolution and
    # compilation, and a failed attempt is discarded by the worktree lifecycle.
    tree_binding = _bind_verification_tree(state, ctx)
    defer_executing_gates = defer_execution_until_source_review(state, ctx)
    if not defer_executing_gates:
        _run_pom_validation_gate(state, ctx, tree_binding)

    if not skipping_candidate_gates:
        # Completeness Check: catch the Developer Agent silently under-delivering
        # (e.g. only writing pom.xml when the Architect's design called for 7 files).
        # A trivially-passing compile on a near-empty sandbox would otherwise report
        # PASSED and get applied to the workspace despite the goal not being met.
        # Sourced from architect_files (the structured file list, or its heuristic
        # fallback - see the Architect call above) rather than re-deriving via a
        # second independent regex pass over the design's prose.
        # PRD-022: a parallel file a deterministic ownership violation
        # abandoned is no longer an expected output, whatever the design said.
        abandoned = (set(state.ownership_redirect_recovery.get("abandoned_candidates", []))
                     - _structured_plan_paths(ctx))
        expected_files = {os.path.basename(f) for f in ctx.architect_files if f not in abandoned}
        missing_files = find_missing_expected_files(expected_files, state.all_files_written, goal=ctx.goal)
        untouched_planned = _verified_no_change_proposal(state, ctx, files, missing_files)
        if untouched_planned and not state.all_files_written:
            # ENFORCE-VERIFIED-NO-CHANGE-001: a unit with ZERO effective
            # mutation - not success: the gates below and the terminal
            # regression run as always, then the workflow decides from
            # deterministic evidence (verified_no_change.py).
            state.no_change_proposal = untouched_planned
            state.record_event(RunEvent(
                kind="unit.no_change_proposed",
                attempt=state.attempt_number,
                source="developer",
                authority=EventAuthority.ADVISORY,
                details={"subtask": ctx.current_subtask_id, "paths": untouched_planned,
                         "identical_rewrites": sorted(set(untouched_planned) & state.identical_rewrites)},
            ))
        else:
            # ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001 (owner decision
            # 2026-10-09, C2-S2_A-final): a planned-file list is execution
            # intent, not verification authority. A unit that carries an
            # effective mutation delivers its untouched planned files
            # unchanged - never an obligation of their own to prove by
            # per-criterion coverage, which free-text criteria can never meet
            # - and is judged by its gates and the terminal authorities like
            # every other mutating unit. ENFORCE-IDENTICAL-WRITE-COMPLETION-
            # 001: outside the enforce contract too (no structured plan, or
            # another expected file genuinely missing) an identical rewrite
            # is DELIVERED, unchanged - never "never written". Neither is a
            # mutation (not in all_files_written, nothing to apply); a
            # milestone or direct run that changes nothing completes through
            # its own no-change verification (milestone_completion.py), never
            # through the write.
            missing_basenames = set(missing_files)
            delivered_unchanged = sorted(
                path for path in set(state.identical_rewrites) | set(untouched_planned)
                if os.path.basename(path) in missing_basenames)
            missing_files = find_missing_expected_files(
                expected_files, state.all_files_written | state.identical_rewrites | set(untouched_planned),
                goal=ctx.goal)
            if missing_files:
                delivered_identical = [path for path in delivered_unchanged if path in state.identical_rewrites]
                raise IncompleteGenerationError(
                    missing_files,
                    "INCOMPLETE GENERATION: The design called for the following files, but "
                    f"they were never written: {', '.join(missing_files)}. "
                    + (f"({', '.join(delivered_identical)}: returned byte-identical to the baseline - delivered, "
                       "not a change.) " if delivered_identical else "")
                    + "You must generate ALL files listed in the Architect Design Guidelines, "
                    "not just a subset."
                )
            if delivered_unchanged and state.all_files_written:
                state.record_event(RunEvent(
                    kind="unit.planned_files_delivered_unchanged",
                    attempt=state.attempt_number,
                    source="workflow",
                    authority=EventAuthority.ADVISORY,
                    details={"subtask": ctx.current_subtask_id, "paths": delivered_unchanged,
                             "identical_rewrites": [p for p in delivered_unchanged if p in state.identical_rewrites],
                             "answered_no_change": [p for p in delivered_unchanged if p not in state.identical_rewrites],
                             "written_paths": sorted(state.all_files_written)},
                ))

        # Static pre-check: deterministic, no-LLM scan for known anti-patterns already
        # documented in active skill rules (e.g. mixing Ignite's two startup mechanisms,
        # an unclosed Ignition.start()) - catches a mistake the model already had the
        # rule for, before the expensive compile+run cycle rather than after it.
        # Unioned with ctx.established_files (see that field's own docstring) -
        # a static rule violation (e.g. mixing Ignite's two startup mechanisms)
        # can span an established file plus one this attempt just wrote.
        static_check_known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
        static_violation = run_static_checks(ctx.worktree_path, static_check_known_files)
        if not static_violation:
            # MA7.5 - MA6 spec section 72's "Django doesn't drift to Spring,
            # Python doesn't invent Maven layout" permanent regression
            # category. Deliberately checked against state.all_files_written
            # ONLY (this attempt's own fresh writes), never the union with
            # ctx.established_files used above - established_files are
            # genuinely pre-existing (from an earlier milestone/attempt) and
            # must count as the ESTABLISHED baseline this check compares
            # against, not get counted as part of "what this attempt wrote."
            static_violation = find_established_stack_drift(ctx.worktree_path, state.all_files_written)
        if not static_violation:
            # 2026-08-25 (external review, P1) - MA7.5's own honest scope
            # note said a first-milestone goal-vs-generated-language
            # mismatch (nothing established yet to compare against) was
            # intentionally out of scope. This is that gap, closed: goal
            # text's own declared language family vs. this attempt's own
            # newly-written ecosystem marker, independent of established
            # history. Same all_files_written-only scoping as the check
            # above, same reasoning.
            static_violation = find_goal_stack_mismatch(ctx.goal, state.all_files_written)
        stack_contract = derive_stack_contract(ctx.grounding_goal or ctx.goal)
        stack_decision = validate_stack_contract_artifacts(
            stack_contract, state.all_files_written,
        )
        log_stack_contract_boundary("candidate", stack_contract, stack_decision)
        if not static_violation:
            # The logged REJECT/PASS decision above must actually gate the
            # candidate - matching the plan boundary (plan_validation.py)
            # and terminal boundary (workflow_controller.py), which both
            # already reject on this same check's non-None result.
            static_violation = stack_decision
        if static_violation:
            # _build_quality_gate_failure() (not a bare Failure(...)), matching the
            # SAME construction every other Quality Gate type already uses (compile/
            # test/run_verification/regression_test) - its extract_implicated_files()
            # call scopes likely_files to whichever known file(s) the violation TEXT
            # actually names, instead of defaulting to state.all_files_written (every
            # file the run has ever written). Found live, 2026-08-13
            # (ignite_qpid_protocol): every StaticCheck's own violation message
            # already names its implicated file(s) by exact known relative path (see
            # static_checks.py) - broadcasting to the whole file set discarded that
            # precision, sending a targeted retry's per-file "explain the fix"
            # instruction to every unrelated file too. Confirmed as a real, non-
            # theoretical cost, not just a wasted-call inefficiency: one of those
            # broadcast files applied a genuinely unrelated edit (an unprompted
            # config section) that had nothing to do with the actual violation. A
            # violation whose text names no known file (a hypothetical future check
            # with no per-file locality) falls through to extract_implicated_files()'s
            # own empty-list fallback, same honest degrade-to-full-set behavior every
            # other gate type already has - never a regression from today's blind
            # broadcast, just no longer the ONLY behavior available.
            failure = build_static_rule_failure(
                static_violation, ctx.worktree_path, static_check_known_files, state.attempt_number,
                writable_paths=(ctx.allowed_write_relpaths
                                if getattr(ctx, "write_scope_mode", None) == WriteScopeMode.ALLOWLIST else None),
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

    # Gap B (owner decision 2026-10-15, stronger form): in human-in-the-loop mode with an approver NO model-authored candidate content is executed - POM validation, build,
    # tests, runtime - before the source review has succeeded. The non-executing checks above/below still run now; the executing gates are held and run after the approval.
    ctx.deferred_executing_gates = None
    if defer_executing_gates:
        apply_pre_review_candidate_corrections(state, ctx)
        ctx.deferred_executing_gates = functools.partial(_run_deferred_executing_gates, state, ctx, skipping_candidate_gates, tree_binding, active_code_context)
    else:
        await _run_executing_candidate_gates(state, ctx, skipping_candidate_gates, tree_binding, active_code_context)


def _ctx_toolchain_declaration_mutable(ctx: "AttemptContext") -> bool:
    """PRD-011: this attempt's structured authority to change the toolchain
    declaration (its write scope and the approved plan)."""
    return toolchain_declaration_mutable(ctx.write_scope_mode, ctx.allowed_write_relpaths, ctx.structured_plan)

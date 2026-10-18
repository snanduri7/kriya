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
import asyncio
import functools
import hashlib
import logging
import os
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from kriya.agents.agent import DeveloperAgent
from kriya.agents.contracts import (
    AUTHORITATIVE_GOAL_SECTION_HEADER,
    PLANNED_IMPLEMENTATION_SECTION_HEADER,
)
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
    AuthorizedFileWriter,
    WriteScopeMode,
    is_trusted_control_path,
    normalize_workspace_relpath,
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
    contract_feedback,
    fallback_decision,
    gate_orchestrator,
    generation_service,
    repair_orchestrator,
    runtime_verification,
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
    invalidate_validated_revisions,
)
from kriya.workflow.deterministic_failure_diagnostic import DeterministicFailureDiagnosticStore
from kriya.workflow.edit_capability import exact_window_reserve
from kriya.workflow.edit_safety import (
    StagedFileWrite,
    apply_anchored_edits,
    content_revision,
    find_cross_file_type_conflict,
    find_structural_corruption,
    read_file_revision,
)
from kriya.workflow.exception_masking import find_unsafe_exception_masking
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.failure_grounding import (
    _build_test_quality_gate_failure,
    _capture_failed_content,
    build_static_rule_failure,
)

# E6 re-exports (tests/architecture/test_facades.py PARTIAL_FACADES): moved to fallback_decision / generation_service,
# importable from here as before. Removed in P1.
from kriya.workflow.fallback_decision import _fallback_rejection as _fallback_rejection
from kriya.workflow.file_integrity import (
    DETERMINISTIC_FILE_INTEGRITY_STOPS,
    FileIntegrityError,
    VerificationTreeBinding,
    display_text,
    file_raw_digest,
    keep_final_newline_state,
    load_snapshot,
    raw_digest,
    read_shown_text,
)
from kriya.workflow.file_resolution import (
    IncompleteGenerationError,
    _resolve_run_command,
    artifact_resolution_scope,
    build_grounded_java_launch_command,
    extract_target_test,
    find_brownfield_public_api_changes,
    find_explanatory_prose_contamination,
    find_missing_expected_files,
    find_protected_api_reference_changes,
    find_runnable_test_files,
    find_unpreserved_test_obligation,
    find_unrequested_architectural_surfaces,
    find_unrestored_public_api_contracts,
    is_runnable_test_file,
    normalize_written_filepath,
    planned_new_artifact_paths,
    resolve_planned_artifact_owners,
)

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
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.operations import (
    CodeOperation,
    all_results_are_no_change,
    classify_result_operation,
    operation_for_attempt,
    operation_for_file,
    validate_operation_result,
)
from kriya.workflow.plan_schema import EngineeringPlan, RequirementOwnershipRelation
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
    CLAIM_CONTRADICTS_STRONGER_AUTHORITY,
    RequirementOutcome,
    RequirementSet,
    record_requirement_closure,
    record_requirement_verdicts,
    requirement_obligation_id,
    requirement_outcomes,
    requirement_verdict_details,
    verifier_result_verdicts,
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
from kriya.workflow.test_delta import TEST_EXECUTION_EVIDENCE_INDETERMINATE, judge_test_delta
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
    """Record, once per run, the exact workspace bytes of ``relpath`` (None
    when absent) and their display text. The bytes are the commit base
    revision (FILE-INTEGRITY-CONTRACT-001) and what a restoration writes back."""
    if relpath in state.all_original_raw:
        return
    try:
        with open(os.path.join(ctx.workspace_path, relpath), "rb") as handle:
            data: Optional[bytes] = handle.read()
    except (FileNotFoundError, IsADirectoryError):
        data = None
    except OSError:
        return
    state.all_original_raw[relpath] = data
    state.all_original_contents.setdefault(relpath, "" if data is None else display_text(data))


def _baseline_restoration_write(state: GenerationState, target_path: str, relpath: str) -> StagedFileWrite:
    """Restore ``relpath`` to its exact original bytes, based on the target's
    current raw revision (re-checked by the batch commit)."""
    original = state.all_original_raw.get(relpath)
    data = original if original is not None else state.all_original_contents[relpath].encode("utf-8")
    return StagedFileWrite(
        target_path=target_path, content=display_text(data), base_path=target_path,
        expected_base_revision=file_raw_digest(target_path), content_bytes=data,
    )


def _file_integrity_stop(state: GenerationState, filepath: str, error: Any) -> "QualityGateFailure":
    """FILE-INTEGRITY-CONTRACT-001: a file the edit engine cannot mutate
    byte-exactly (not UTF-8, mixed line endings, a symbolic link). No
    Developer retry can change the file itself, so this is a deterministic
    stop (retry_strategy: ``file_integrity_unsupported``), never a retry."""
    message = str(error)
    failure = Failure(
        type="file_integrity_unsupported", message=message, raw_output=message,
        source="orchestrator", authority="deterministic", attempt=state.attempt_number,
        file_locations=[FileLocation(filepath=filepath)], likely_files=[filepath],
        diagnostics={"reason_code": getattr(error, "reason_code", None)},
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    return QualityGateFailure(failure)


def _target_exists(ctx: "AttemptContext", filepath: str) -> bool:
    return os.path.exists(os.path.join(ctx.worktree_path, filepath)) or os.path.exists(
        os.path.join(ctx.workspace_path, filepath)
    )


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
    """CONTEXT-EDIT-PROTOCOL-LARGE-FILE-001: the grounded members a package
    omitted, per path, with the lines and revision the entry recorded
    (make_omitted_entry); an entry without them records nothing. The record
    is replaced wholesale by every package build (a path shown whole in a
    later attempt has no omission any more)."""
    by_path: Dict[str, List[Tuple[int, int, str]]] = {}
    for entry in omitted:
        if entry.get("member_id") is None or not entry.get("revision") or entry.get("start_line") is None:
            continue
        boundary = (int(entry["start_line"]), int(entry["end_line"]), str(entry["revision"]))
        members = by_path.setdefault(entry["path"], [])
        if boundary not in members:
            members.append(boundary)
    state.known_target_omitted_members = by_path


def _record_known_target_package(state: GenerationState, items: Iterable[ContextItem]) -> None:
    """Record a known-target or retry-member package per path. A package
    can render several member_exact units for one file (every member
    localization grounded, every overload of an ambiguous name), listed in
    rank order: the path's record is its highest-ranked member - never
    whichever unit happened to be listed last - and every member unit is kept
    in ``known_target_member_items`` for edit authority. A path without
    member units records its last (broadest) item, as before."""
    by_path: Dict[str, List[ContextItem]] = {}
    for item in items:
        by_path.setdefault(item.path, []).append(item)
    for path, path_items in by_path.items():
        members = [item for item in path_items if item.tier == "member_exact" and item.member_id is not None]
        state.known_target_context_items[path] = members[0] if members else path_items[-1]
        if members:
            state.known_target_member_items[path] = members
        else:
            state.known_target_member_items.pop(path, None)


def exit_authority_text(ctx: "AttemptContext") -> str:
    """PRD-025: the text an expected nonzero exit may be declared in - the
    user's own request (exit_authority_goal, else grounding_goal, else the
    goal of a plain top-level run, which IS the user's). A milestone or
    subtask goal, a plan, a judge's criteria or a grader's reasoning is
    never consulted."""
    return ctx.exit_authority_goal or ctx.grounding_goal or ctx.goal


def _escalation_authorized_paths(ctx: "AttemptContext", target_files: Iterable[str]) -> Tuple[str, ...]:
    """PRD-028: the write scope a member-authority expansion must already
    sit inside - never widened by it. DENY_ALL authorizes nothing; an
    ALLOWLIST authorizes only its listed targets; UNRESTRICTED top-level
    generation authorizes the retry's own grounded targets."""
    targets = tuple(dict.fromkeys(target_files or ()))
    if ctx.write_scope_mode is WriteScopeMode.DENY_ALL:
        return ()
    if ctx.write_scope_mode is WriteScopeMode.ALLOWLIST:
        allowed = set(ctx.allowed_write_relpaths)
        return tuple(path for path in targets if path in allowed)
    return targets


def _record_authority_expansions(state: GenerationState, expansions: Iterable[Any]) -> None:
    for expansion in expansions:
        state.authority_expansions.append(expansion)
        state.record_event(RunEvent(
            kind="authority.expansion",
            attempt=state.attempt_number,
            source="authority_escalation",
            authority=EventAuthority.ADVISORY,
            message=(
                f"Member authority {expansion.outcome} for {expansion.path}::{expansion.member_id} "
                f"({expansion.reason_code}, origin {expansion.source_origin})."
            ),
            details=expansion.to_dict(),
        ))


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
    """VAL-001 G1 D1 (2026-09-18): `permitted mutation precision <=
    authoritative current-source precision available to the Developer`, for
    an EXISTING file. Whole-file replacement (CREATE_FULL_FILE/
    REPAIR_WITH_FULL_FILE - never anything else; a new file, a targeted
    patch, or a no-change assessment carry no such risk and pass through
    unchanged) is authorized only when the model was actually shown a
    ContextItem for this exact path that is tier="full" (never a member
    slice - `member_id is None` - "member_exact" is real, exact evidence
    for the MEMBER it covers, never authority to rewrite the surrounding
    file), is_exact=True, and whose recorded revision still matches the
    file's real current content.

    Absence of a recorded ContextItem is NOT evidence of exactness - it is
    evidence of nothing, and the invariant reads "requires authoritative
    complete exact current source", not "requires proof of its absence" -
    so a file with no known_target_context_items entry fails closed to
    REPAIR_WITH_PATCH exactly like a recorded skeleton/signatures/
    member_exact/stale entry does. REPAIR_WITH_PATCH is not a workaround:
    apply_anchored_edits (kriya/workflow/attempt.py's own edit-application
    path) already refuses a SEARCH: block that doesn't match the real
    current content exactly once, so downgrading to it never authorizes an
    ungrounded mutation - it either succeeds against real content or fails
    closed on its own, through the exact same failure-handling path any
    other Quality Gate rejection already uses.

    Returns (operation, mandatory). `mandatory=True` means this downgrade is
    the invariant speaking, not an ordinary retry preference - a caller MUST
    additionally reject (not merely allow validate_operation_result's own,
    intentionally more permissive, patch-may-fall-back-to-full-file
    transition) a result that still resolves to a full-file shape for this
    file. See run_attempt()'s own operation-contract enforcement block,
    which applies that additional check using this same function's output.

    EXEMPTION (found while implementing this design, against the real
    existing test suite - not merely theorized): RESTORE_PUBLIC_CONTRACT is
    deterministic, never a Developer/LLM call at all -
    _restore_api_contract_owners_deterministically() writes
    state.all_original_contents[owner] (the exact, already-known baseline)
    verbatim, and its own docstring is explicit that its output is shaped
    identically to real Developer output specifically so downstream
    consumers "are completely unaware this attempt never called the
    Developer at all." This invariant is about the risk of PROBABILISTIC
    generation from incomplete context - a risk that provably does not
    exist for a deterministic byte-for-byte restoration of content Kriya
    itself already held before the risky candidate was even sandboxed. Gating
    it here would have rejected Kriya's own correct, hard-won PRV-11 fix
    (state.py's own restoration mechanism, 2026-08-30) as if it were an
    unsafe model response.
    """
    if not file_exists or base_operation not in (
        CodeOperation.CREATE_FULL_FILE, CodeOperation.REPAIR_WITH_FULL_FILE,
    ):
        return base_operation, False

    if _is_restore_public_contract_phase(state):
        return base_operation, False

    if _has_authoritative_full_source(filepath, ctx, state):
        return base_operation, False

    return CodeOperation.REPAIR_WITH_PATCH, True


def _is_restore_public_contract_phase(state: Optional[GenerationState]) -> bool:
    """D2's own deterministic-restoration exemption (see _completeness_
    gated_operation's own docstring for the full rationale) - extracted so
    _validate_actual_mutation_authority() below can share the EXACT same
    exemption, never a second, independently-maintained copy of it."""
    return bool(
        state is not None
        and state.api_contract_recovery is not None
        and state.api_contract_recovery.phase is APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT
    )


def _has_authoritative_full_source(
    filepath: str, ctx: "AttemptContext", state: Optional[GenerationState],
) -> bool:
    """VAL-001 G1 D1 (2026-09-18, extracted from _completeness_gated_
    operation's own original body - identical logic, now shared with
    _validate_actual_mutation_authority() below so REQUESTED-operation
    gating and ACTUAL-returned-shape gating can never independently drift
    on what "authoritative" means). True only when state.known_target_
    context_items records a ContextItem for this exact path that is
    tier="full" (never a member slice - member_id is None), is_exact=True,
    and whose recorded revision still matches the file's real CURRENT
    content (read fresh, worktree first, workspace fallback - never
    trusted from a stale in-memory copy). CONTENT EXACTNESS (is_exact=True
    for whatever the item represents) is deliberately a narrower claim
    than SOURCE AUTHORITY (safe to authorize a WHOLE-FILE mutation from) -
    this function is the one place that gap is closed, by additionally
    requiring tier=="full" and a live revision match; a member_exact item,
    or a full/exact item recorded under a since-changed revision, both
    correctly return False here despite each having is_exact=True on its
    own ContextItem."""
    item = state.known_target_context_items.get(filepath) if state is not None else None
    if item is None or item.tier != "full" or not item.is_exact or item.member_id is not None:
        return False
    full_path = os.path.join(ctx.worktree_path, filepath)
    if not os.path.exists(full_path):
        full_path = os.path.join(ctx.workspace_path, filepath)
    try:
        current_revision = read_shown_text(full_path)[1]
    except OSError:
        current_revision = None
    # An item with no recorded revision at all predates CTX-001 P1 WP3
    # (LEGACY_COMPATIBILITY default, see ContextItem.from_dict) - treated
    # as current rather than stale, matching that field's own documented
    # additive/optional contract; every real construction site
    # (build_known_target_context()) always sets a real revision.
    return not item.revision or current_revision == item.revision


def _current_revision(filepath: str, ctx: "AttemptContext") -> Optional[str]:
    full_path = os.path.join(ctx.worktree_path, filepath)
    if not os.path.exists(full_path):
        full_path = os.path.join(ctx.workspace_path, filepath)
    try:
        return read_shown_text(full_path)[1]
    except OSError:
        return None


def _validate_actual_mutation_authority(
    filepath: str, actual_operation: CodeOperation, *,
    file_exists: bool, ctx: "AttemptContext", state: Optional[GenerationState],
) -> Optional[str]:
    """VAL-001 G1 D1-A (2026-09-18, closing the run d756a833/8cc2018a G1-R2
    incident): the authority decision for a whole-file mutation must be
    keyed on the operation the model ACTUALLY RETURNED (`actual_operation`,
    from validate_operation_result() - already parsed/interpreted), never
    on which operation was originally REQUESTED. "Requested operation
    cannot grant authority" - _completeness_gated_operation()'s own
    `mandatory_patch` output is about what SHOULD have been asked for, and
    is only ever True when the ATTEMPT's own base operation (attempt_
    operation, mode-derived) already happened to be full-file-shaped
    (CREATE_FULL_FILE/REPAIR_WITH_FULL_FILE) - a targeted/fallback_
    targeted-mode attempt's own base operation is UNCONDITIONALLY
    REPAIR_WITH_PATCH (operations.py::operation_for_attempt), so
    mandatory_patch is structurally always False for those modes,
    regardless of actual context completeness. The proven G1-R2 gap: a
    model in fallback_targeted mode that simply ignores the patch
    instruction and returns full file content anyway was NEVER checked
    against this invariant at all - mandatory_patch's own False value
    (about the REQUEST) was silently treated as license for the RESPONSE.

    This function is the independent, unconditional check on the RESPONSE
    itself - called for EVERY attempt, every mode, right after actual_
    operation is known, regardless of what mandatory_patch said. Shares
    the exact same _has_authoritative_full_source()/_is_restore_public_
    contract_phase() primitives _completeness_gated_operation() itself
    uses, so the two can never disagree about what "authoritative" means -
    only about WHEN they get to ask the question (request-time vs
    response-time).

    Returns None when authorized (not full-file-shaped at all; a genuinely
    new file with no existing source to be incomplete about; the
    deterministic RESTORE_PUBLIC_CONTRACT phase; or real, current,
    authoritative full source IS on record) - otherwise a rejection
    reason string, never raises itself (the caller decides how to turn
    this into a QualityGateFailure, matching every other gate in this
    module)."""
    if actual_operation not in (CodeOperation.CREATE_FULL_FILE, CodeOperation.REPAIR_WITH_FULL_FILE):
        return None
    if not file_exists:
        return None
    # CONTEXT-EDIT-PROTOCOL-001: the capability this invocation's contract
    # was built from decides, never a second evaluation - bound to the
    # revision it was decided for.
    capability = _current_edit_capability(state, filepath)
    if capability is not None:
        if capability.full_file and _current_revision(filepath, ctx) == capability.revision:
            return None
    elif _is_restore_public_contract_phase(state) or _has_authoritative_full_source(filepath, ctx, state):
        return None
    return (
        "a full-file replacement was returned, but this file's context this attempt was not "
        "authoritative, complete, exact current source (skeleton/signatures/member-only/stale "
        "revision/candidate-derived) - whole-file replacement requires authoritative pristine "
        "current source regardless of which operation was originally requested. Return an "
        "anchored SEARCH/REPLACE edit instead."
    )


def _operation_map(
    ctx: "AttemptContext", filepaths: List[str], attempt_operation: CodeOperation,
    state: Optional[GenerationState] = None,
) -> Dict[str, CodeOperation]:
    operations = {
        filepath: operation_for_file(
            attempt_operation, file_exists=_target_exists(ctx, filepath),
        )
        for filepath in filepaths
    }
    if state is not None:
        for filepath in filepaths:
            if (
                state.budgets.anchor_failure_counts.get(filepath, 0) >= 1
                and _target_exists(ctx, filepath)
            ):
                operations[filepath] = CodeOperation.REPAIR_WITH_FULL_FILE
    for filepath in filepaths:
        operations[filepath], _mandatory = _completeness_gated_operation(
            filepath, operations[filepath],
            file_exists=_target_exists(ctx, filepath), ctx=ctx, state=state,
        )
    return operations


def _preserved_authoritative_locator_files(
    state: GenerationState, target_files: List[str], self_diagnosed: List[str],
) -> List[str]:
    """Return current targets backed by the preceding authoritative locator.

    Developer analysis is advisory and cannot erase deterministic evidence.
    A different file explicitly named by current-turn analysis is allowed to
    redirect (root cause and surfacing location may differ), so preservation
    applies only when there is no such alternate.  Intersecting with this
    attempt's actual targets prevents stale locator evidence from widening or
    redirecting a later, unrelated repair.
    """
    prior_attribution = state.last_attribution
    prior_failure = state.last_failure
    if (
        self_diagnosed
        or state.last_attempt_mode not in ("targeted", "fallback_targeted")
        or prior_attribution is None
        or getattr(prior_attribution, "tier", None) != "locator"
        or prior_failure is None
        or getattr(prior_failure, "authority", None) != "authoritative"
    ):
        return []
    return [
        filepath for filepath in getattr(prior_attribution, "files", [])
        if filepath in target_files
    ]


def _fallback_routing(state: GenerationState, ctx: "AttemptContext") -> Any:
    """LR-R1-P1: the shared routing view of the configured fallbacks
    (model_transition.fallback_routing_for_context) - never ``bool(ctx.chain)``."""
    from kriya.workflow.model_transition import fallback_routing_for_context

    return fallback_routing_for_context(state, ctx)


def _request_fallback_for_rejected_authoritative_target(
    state: GenerationState, ctx: "AttemptContext", preserved_files: List[str],
) -> None:
    if (
        preserved_files
        and state.last_attempt_mode == "targeted"
        and _fallback_routing(state, ctx).available
        and not state.budgets.fallback_targeted_attempted
    ):
        state.budgets.fallback_targeted_requested = True


def _reject_explanatory_prose(
    state: GenerationState, filepath: str, content: str, baseline: Optional[str],
) -> None:
    """``baseline``: the file before this write (None for a new file); only
    what the candidate added is judged (P3-C)."""
    contamination = find_explanatory_prose_contamination(filepath, content, baseline)
    if not contamination:
        return
    failure = Failure(
        type="prose_contamination",
        message=(
            f"SOURCE PROSE CONTAMINATION in {filepath}: obvious explanatory text "
            "was emitted as executable source; return code only or use the "
            "language's comment syntax for genuine documentation."
        ),
        raw_output=contamination,
        file_locations=[FileLocation(filepath=filepath)],
        likely_files=[filepath], failed_content={filepath: content},
        attempt=state.attempt_number,
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


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
    """BACKEND-READINESS-V2 (S4_A): a candidate that newly catches/swallows ``Error``/``Throwable`` hides the crash it
    was asked to fix. ``baseline``: the file before this write; the pristine run-start content wins when known, so a
    masking clause an earlier attempt introduced is still the candidate's. Runs on every attempt, repairs included."""
    pristine = state.all_original_contents.get(filepath, baseline)
    masking = find_unsafe_exception_masking(filepath, content, pristine)
    if not masking:
        return
    guidance = (
        "Catching Error/Throwable hides the failure instead of fixing the code that raises it; fix the cause "
        "(for a StackOverflowError, the unbounded recursion or loop) and do not widen a catch to Error/Throwable."
    )
    failure = Failure(
        type="unsafe_exception_masking",
        message=f"UNSAFE EXCEPTION MASKING in {filepath}: {masking}. {guidance}",
        raw_output=f"{masking}. {guidance}",
        file_locations=[FileLocation(filepath=filepath)],
        likely_files=[filepath], failed_content={filepath: content},
        attempt=state.attempt_number,
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _preserved_attribution_diagnostics(preserved_files: List[str]) -> Optional[dict]:
    if not preserved_files:
        return None
    return {
        "preserved_prior_attribution": {
            "tier": "locator",
            "files": list(preserved_files),
        }
    }


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
    """Await ``awaitable`` with its model calls attributed to the developer
    role (PRD-018): the compile/test self-correction loop is the Developer's
    repair, run outside _run_developer_generation."""
    from kriya.core.role_metrics import model_role

    with model_role("developer"):
        return await awaitable


def _developer_request_profile(ctx: "AttemptContext", model_override: Optional[str]) -> Any:
    """PRD-017: the request profile of a Developer call with ``model_override``
    (None = the primary). The body is kriya/workflow/generation_service.py (E6)."""
    return generation_service._developer_request_profile(_generation_deps(), ctx, model_override)


def _patch_required_files(state: GenerationState, ctx: "AttemptContext", kwargs: Dict[str, Any]) -> List[str]:
    """Existing files this Developer call may only patch: the completeness
    gate (D1) downgraded their whole-file operation because no complete,
    exact current source was shown, so a whole-file answer would be rejected
    after the call."""
    required = []
    for path, operation in (kwargs.get("operation_by_file") or {}).items():
        if operation != CodeOperation.REPAIR_WITH_PATCH or not _target_exists(ctx, path):
            continue
        gated, mandatory = _completeness_gated_operation(
            path, CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        if mandatory and gated == CodeOperation.REPAIR_WITH_PATCH:
            required.append(path)
    return required


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
    """The repository's Code Intelligence index (read-only T1-T3 context),
    or None when there is none of the current parser identity."""
    from kriya.workflow.graph_retrieval import open_code_intelligence

    memory = getattr(getattr(getattr(ctx.kernel, "config", None), "paths", None), "memory", None)
    if not isinstance(memory, str):
        return None
    try:
        return open_code_intelligence(ctx.workspace_path, os.path.join(memory, "dependency_graph.db"))
    except Exception as error:  # read-only enrichment; T0 never depends on it
        logger.warning("Code Intelligence index unavailable for known-target context: %s", error)
        return None


# CONTEXT-EDIT-PROTOCOL-LARGE-FILE-001: how many structural localization
# candidates of one file may each open an exact window (the adopted targets
# first); the window budget is shared equally among all loci of the file.
MAX_SYMBOL_LOCI_PER_FILE = 2


def _stop_on_environment_gate_result(state: GenerationState, result: Any, gate: str) -> None:
    """GRADLE-WRAPPER-CONTAINMENT-001: a gate result whose verification tool
    could not start carries a typed ``environment_reason_code`` (the
    validator sets it from a structured outcome, never from text). It is
    raised as the existing ``verification_infrastructure_failure``
    (retry_strategy: STOP_ENVIRONMENT - no budget reset, no fallback
    escalation, the environment banner), never as a compile/test failure of
    the candidate: nothing about the candidate was observed."""
    code = result.get("environment_reason_code") if isinstance(result, dict) else None
    if not code:
        return
    output = str(result.get("output") or "")
    failure = Failure(
        type="verification_infrastructure_failure",
        message=(f"VERIFICATION_INFRASTRUCTURE_FAILURE: the {gate} gate's verification tool could not start "
                 f"({code}); nothing about the candidate was observed, so this is not a code failure.\n{output}"),
        raw_output=output, source="orchestrator", attempt=state.attempt_number,
        diagnostics={"reason_code": code, "gate": gate, "gradle_distribution": result.get("gradle_distribution")},
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _edit_capability_loci(
    state: GenerationState, ctx: "AttemptContext", path: str, lines: List[str], *,
    revision: Optional[str] = None, raw_digest: Optional[str] = None,
) -> List[int]:
    """Deterministic edit loci for ``path``: code the user's goal quotes,
    the lines the last failure names, where every rejected SEARCH block
    pointed in the real source (_remember_anchor_loci), and -
    CONTEXT-EDIT-PROTOCOL-LARGE-FILE-001 - the declaration lines of the
    structural localization candidates for this file (ctx.retrieval_symbol_loci,
    only those bound to ``raw_digest``, the digest of the current bytes) and
    the first line of every grounded member the known-target package could
    not show whole (state.known_target_omitted_members, only those of
    ``revision``, the current shown revision). A locus of another revision
    authorizes nothing: the file changed since it was read."""
    from kriya.workflow.edit_capability import goal_code_fragments, locate_fragments

    loci = locate_fragments(lines, goal_code_fragments(ctx.goal))
    failure = state.last_failure
    if failure is not None:
        loci.extend(loc.line for loc in failure.file_locations if loc.filepath == path and loc.line)
    loci.extend(state.edit_anchor_loci.get(path, ()))
    if raw_digest:
        current = [line for line, digest in ctx.retrieval_symbol_loci.get(path, ()) if digest == raw_digest]
        loci.extend(current[:MAX_SYMBOL_LOCI_PER_FILE])
    if revision:
        loci.extend(start for start, _end, member_revision in state.known_target_omitted_members.get(path, ())
                    if member_revision == revision)
    return loci


def _remember_anchor_loci(state: GenerationState, path: str, edits: List[Dict[str, str]], current: str) -> None:
    """Where a rejected SEARCH block points in the real source, kept for
    every later invocation (an intervening stop must not forget it, or the
    capability could fall back to one already tried)."""
    from kriya.workflow.edit_capability import locate_search_text

    lines = current.splitlines()
    located = {locus for edit in edits for locus in locate_search_text(lines, edit.get("search", ""))}
    if located:
        state.edit_anchor_loci[path] = sorted(set(state.edit_anchor_loci.get(path, [])) | located)


_TEST_VERIFIERS = frozenset({"test", "tests", "regression"})


def _declares_test_verification(required_verification: Iterable[Dict[str, Any]]) -> bool:
    """The stage's declared verification includes a test-suite verifier."""
    return any(item.get("type") == "tool" and item.get("tool_name") in _TEST_VERIFIERS
               for item in required_verification or ())


# Failures that judge only the Developer's RESPONSE (its shape, its anchors,
# its consistency) - never evidence that a file is the cause of anything.
_RESPONSE_VALIDITY_FAILURES = frozenset({
    "operation_contract", "anchored_edit", "structural_corruption", "no_op_edit", "diagnosis_mismatch",
})


def _only_response_validity_failures(state: GenerationState) -> bool:
    """Positive evidence that every failure of this run so far only judged a
    malformed or inconsistent Developer response (its shape, anchors,
    consistency) - never a gate, locator or misdirection that is evidence
    about a file. Then a targeted retry's targets are simply the stage's own
    planned files, and a well-formed NO CHANGE NEEDED is answered by the
    gates exactly as on a first attempt (live: spring-petclinic s3 - two
    protocol failures, then NO CHANGE on its own test file, was turned into
    an attribution dispute, re-attributed to another stage's file and
    stranded). Unknown provenance (no recorded failure) is not that evidence."""
    family = state.budgets.last_failure_signature  # the active family; repair feedback inherits it
    failed = [outcome for outcome in state.gate_outcomes if outcome.get("success") is False]
    if family is None and not failed:
        return False
    if family and family[0] not in _RESPONSE_VALIDITY_FAILURES:
        return False
    return all(outcome.get("type") in _RESPONSE_VALIDITY_FAILURES for outcome in failed)


def _record_authority_snapshot(state: GenerationState, ctx: "AttemptContext", kwargs: Dict[str, Any],
                               capabilities: Dict[str, Any], requested: Dict[str, Any], model: str) -> None:
    """LR-R1-M1 ``authority.snapshot``: what this Developer invocation was
    authorized to change and on what source, recorded before inference
    (observational; every value is already decided above)."""
    if attempt_evidence_scope.capture_mode() is None:
        return
    try:
        allowed = list(getattr(ctx, "allowed_write_relpaths", None) or [])
        mode = getattr(ctx, "write_scope_mode", None)
        targets = []
        for path, capability in capabilities.items():
            item = state.known_target_context_items.get(path)
            targets.append({
                **capability.summary(),
                "requested_operation": getattr(requested.get(path), "value", requested.get(path)),
                "whole_file_authority": capability.full_file,
                "shown_tier": getattr(item, "tier", None), "shown_member_id": getattr(item, "member_id", None),
                "shown_is_exact": getattr(item, "is_exact", None), "shown_revision": getattr(item, "revision", None),
                "member_units_shown": len(state.known_target_member_items.get(path, ())),
                "in_write_scope": (path in allowed) if allowed else None,
            })
        payload = {"model": model, "write_scope_mode": getattr(mode, "value", mode), "authorized_write_scope": allowed,
                   "targets": targets, "known_target_files": list(kwargs.get("known_target_files") or [])}
    except Exception as error:  # observational: never alters the attempt
        logger.warning("Attempt evidence: authority.snapshot not built (%s: %s)", type(error).__name__, error)
        return
    attempt_evidence_scope.emit("authority.snapshot", payload)


def _decide_edit_capabilities(state: GenerationState, ctx: "AttemptContext", kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """CONTEXT-EDIT-PROTOCOL-001: one EditCapability per existing target of
    this Developer invocation (kriya/workflow/edit_capability.py), decided
    before inference. Its exact windows join the mandatory context, the
    feasible operations go to the Developer contract, and the response
    validators read the same record. No feasible operation is a typed stop
    before inference; an edit-protocol failure that meets an unchanged
    capability is no progress."""
    from kriya.workflow.context_budget import allocation_window
    from kriya.workflow.edit_capability import (
        ANCHOR_CONTEXT_NOT_ESCALATED,
        CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE,
        build_edit_capability,
        exact_window_reserve,
        render_exact_spans,
        shown_exact_texts,
    )

    candidates = kwargs.get("known_target_files") or ctx.expected_files_upfront or []
    targets = []
    for path in dict.fromkeys(candidates):
        for root in (ctx.worktree_path, ctx.workspace_path):
            full = os.path.join(root, path) if root else None
            if full and os.path.isfile(full):
                targets.append((path, full))
                break
    if not targets:
        return {}
    binding = _chain_binding(ctx, kwargs.get("model_override"))
    model = kwargs.get("model_override") or ctx.kernel.config.llm.model
    # Only mandatory text is certain to be sent: an optional section may be
    # trimmed or dropped by the request fit, so it never makes source shown.
    mandatory_context = kwargs.get("existing_code_context") or ""
    for section in kwargs.get("optional_sections") or ():
        if section.text:
            mandatory_context = mandatory_context.replace(section.text, "", 1)
    budget_chars = exact_window_reserve(allocation_window(ctx.kernel.config, binding)) * 4 // len(targets)
    capabilities: Dict[str, Any] = {}
    for path, full in targets:
        content, content_shown_revision = read_shown_text(full)
        # Pieces already shown count only when Kriya's producer vouches for
        # their bytes (a member_exact slice; an implementation excerpt's head
        # and tail, exact by construction) and they are of the current revision.
        item = state.known_target_context_items.get(path)
        shown = (
            shown_exact_texts(item.tier, item.content)
            if item is not None and (item.is_exact or item.tier == "implementation_excerpt")
            and (not item.revision or item.revision == content_shown_revision) else []
        )
        # Whatever renderer put it there (a retry prompt's current content),
        # the whole current file present verbatim in this request's mandatory
        # text is exact anchor source - checked on the bytes, not on a label.
        # Whole-file replacement authority stays D1's alone.
        # Every other member unit rendered for the path (all members
        # localization grounded, every overload of an ambiguous name) counts
        # when it is of the current revision and its bytes are in this
        # request's mandatory text.
        for member in state.known_target_member_items.get(path, ()):
            if (member is not item and member.is_exact and member.content
                    and (not member.revision or member.revision == content_shown_revision)
                    and member.content in mandatory_context):
                shown.extend(shown_exact_texts(member.tier, member.content))
        if content.strip() and content in mandatory_context:
            shown = [("shown_full", content)]
        full_file = _is_restore_public_contract_phase(state) or _has_authoritative_full_source(path, ctx, state)
        capabilities[path] = build_edit_capability(
            path, content,
            full_file=full_file,
            loci=_edit_capability_loci(state, ctx, path, content.splitlines(), revision=content_shown_revision,
                                       raw_digest=file_raw_digest(full)),
            budget_chars=budget_chars,
            level=state.budgets.anchor_failure_counts.get(path, 0),
            shown=shown,
            insertion=None if full_file else _structural_insertion_locus(state, ctx, path, content),
        )
    if state.edit_capabilities_attempt != state.attempt_number:
        state.edit_capabilities = {}
        state.edit_capabilities_attempt = state.attempt_number
    state.edit_capabilities.update(capabilities)
    requested = {path: _requested_operation(kwargs, path) for path in capabilities}
    state.edit_capability_models.update({path: (model, requested[path]) for path in capabilities})
    state.record_event(RunEvent(
        kind="context.edit_capability",
        attempt=state.attempt_number,
        source="attempt._decide_edit_capabilities",
        authority=EventAuthority.ADVISORY,
        message="Mutation operations decided from this invocation's authoritative context.",
        details={"model": model, "budget_chars": budget_chars,
                 "targets": [capability.summary() for capability in capabilities.values()]},
    ))
    _record_authority_snapshot(state, ctx, kwargs, capabilities, requested, model)

    infeasible = [path for path, capability in capabilities.items() if not capability.feasible]
    if infeasible:
        detail = "; ".join(
            f"{path}: no authoritative full source, and no exact source for "
            + (f"located line(s) {list(capabilities[path].uncovered_loci)} within the window budget"
               if capabilities[path].uncovered_loci else "any located edit region")
            for path in infeasible
        )
        # PLAN-TARGET-LOCALIZATION-MISMATCH-001: before stopping on an editing error, ask the structural index where
        # the goal's named symbols are defined; a definition outside the approved write scope is a plan-scope
        # conflict with grounded locations (the existing plan revision + re-invocation mechanism takes it from here).
        from kriya.workflow.relocalization import relocalization_conflict

        conflict = relocalization_conflict(
            _code_intelligence_for(ctx), ctx.goal, infeasible_paths=infeasible, allowed_paths=ctx.allowed_write_relpaths,
            read_revision=lambda rel: read_file_revision(os.path.join(ctx.workspace_path, rel))
            if os.path.isfile(os.path.join(ctx.workspace_path, rel)) else None)
        if conflict is not None:
            state.plan_scope_conflict = conflict
            detail += "; " + conflict["reason"]
        message = (f"{CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE}: no mutation operation is feasible under the "
                   f"authoritative context of this Developer invocation - {detail}. Nothing was sent to the model.")
        logger.error(message)
        raise QualityGateFailure(Failure(
            type="context_edit_protocol_unsatisfiable", message=message, raw_output=message,
            source="orchestrator", attempt=state.attempt_number, likely_files=infeasible,
            file_locations=[FileLocation(filepath=loc["filepath"], line=loc["line"])
                            for loc in (conflict or {}).get("grounded_locations", ())],
            diagnostics={"reason_code": CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE,
                         "capabilities": [capabilities[path].summary() for path in infeasible],
                         "relocalization": (conflict or {}).get("relocalization")},
        ))

    model = kwargs.get("model_override") or ctx.kernel.config.llm.model
    for path, capability in capabilities.items():
        previous = state.edit_failure_capability.get(path)
        if previous is not None and previous[1:] == (capability.digest, (model, requested[path])):
            refusal = (path, capability.digest, (model, requested[path]))
            repeated = refusal in state.edit_refused_capabilities
            state.edit_refused_capabilities.add(refusal)
            message = (f"{ANCHOR_CONTEXT_NOT_ESCALATED}: the last {previous[0]} failure in {path} would be "
                       f"retried on {model} with the same authoritative context, the same feasible operations "
                       f"({', '.join(capability.operations)}) and the same requested operation "
                       f"({requested[path]}); nothing new can be shown within the budget.")
            raise QualityGateFailure(Failure(
                type="no_progress_retry", message=message, raw_output=f"edit_capability={capability.digest}",
                source="orchestrator", attempt=state.attempt_number, mode=state.last_attempt_mode,
                likely_files=[path], diagnostics={"reason_code": ANCHOR_CONTEXT_NOT_ESCALATED,
                                                  "refusal_repeated": repeated},
            ))

    windows = "".join(render_exact_spans(capability) for capability in capabilities.values())
    if windows:
        kwargs["existing_code_context"] = (kwargs.get("existing_code_context") or "") + windows
    kwargs["edit_operations"] = {path: capability.operations for path, capability in capabilities.items()}
    return capabilities


def _structural_insertion_locus(state: GenerationState, ctx: "AttemptContext", path: str, content: str) -> Any:
    """P3-D: the zero-width insertion locus for a new member the user's goal
    names (kriya/workflow/insertion_locus.py), decided from the bytes this
    invocation read and the members localization grounded - never from the
    plan or the model. None when there is none (recorded, never a guess)."""
    from kriya.workflow.insertion_locus import resolve_insertion_locus

    grounded = []
    for item in [state.known_target_context_items.get(path), *state.known_target_member_items.get(path, ())]:
        if item is not None:
            grounded.extend(key for key in (item.type_id, item.member_id) if key)
    locus, reason = resolve_insertion_locus(
        path, content, goal=ctx.grounding_goal or ctx.goal, revision=content_revision(content),
        grounded_keys=grounded)
    if locus is None and reason:
        logger.info("Structural insertion locus for %s: none (%s)", path, reason)
    return locus


def _requested_operation(kwargs: Dict[str, Any], path: str) -> Optional[str]:
    """The operation this invocation's contract requests for ``path``."""
    operation = (kwargs.get("operation_by_file") or {}).get(path, kwargs.get("default_operation"))
    return getattr(operation, "value", operation)


def _current_edit_capability(state: Optional[GenerationState], path: str) -> Any:
    """The EditCapability this attempt's Developer invocation decided for
    ``path``, or None (no invocation decided one this attempt)."""
    if state is None or state.edit_capabilities_attempt != state.attempt_number:
        return None
    return state.edit_capabilities.get(path)


def _record_edit_protocol_failure(state: GenerationState, path: str, family: str) -> None:
    """Remember the capability, model and requested operation an
    edit-protocol failure happened under; it stands until an edit to
    ``path`` is accepted."""
    capability = _current_edit_capability(state, path)
    if capability is not None:
        state.edit_failure_capability[path] = (family, capability.digest, state.edit_capability_models.get(path))


def _authorize_anchors(state: GenerationState, path: str, edits: List[Dict[str, str]], current: str) -> None:
    """The response side of the same capability: every SEARCH block must lie
    in the exact source this invocation was authorized to rely on. An anchor
    that is real but was never shown records its lines as loci for the next
    window; one that is not in the file at all is fabricated or stale."""
    from kriya.workflow.edit_capability import ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT, INSERTION_ONLY

    capability = _current_edit_capability(state, path)
    if capability is None:
        return
    for index, edit in enumerate(edits, 1):
        status = capability.anchor_status(edit.get("search", ""), current)
        if status is None:
            continue
        if status == INSERTION_ONLY:
            _authorize_structural_insertion(state, capability, path, index, edit, current)
            continue
        if status == ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT and _sent_exact_source_covers(
                state, capability, path, edit.get("search", ""), current):
            continue
        if status == ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT:
            raise ValueError(
                f"{status}: Anchor matching failed for edit #{index}: the search block is real source, but not "
                "source this attempt was authorized to rely on (it was not in the exact current source shown). "
                "Its location is now shown as exact source."
            )
        raise ValueError(
            f"{status}: Anchor matching failed for edit #{index}: the search block does not occur in the "
            "current file (fabricated or stale). Copy SEARCH text only from the EXACT CURRENT SOURCE shown."
        )


def _authorize_structural_insertion(
    state: GenerationState, capability: Any, path: str, index: int, edit: Dict[str, str], current: str,
) -> None:
    """P3-D: an edit anchored only on a structural insertion carrier is
    accepted only when, applied alone to the current bytes, it is a pure
    insertion at the locus of exactly this revision whose carrier the
    dispatched request carried (verify_insertion); anything else is refused
    with its typed reason and the existing source is never touched."""
    from kriya.workflow.insertion_locus import verify_insertion

    locus = capability.insertion
    after = apply_anchored_edits(current, [edit], "")
    problem = verify_insertion(locus, current, after, state.edit_capability_sent.get(path) or ())
    if problem is not None:
        raise ValueError(f"{problem} (edit #{index})")
    state.record_event(RunEvent(
        kind="context.structural_insertion_authorized", attempt=state.attempt_number,
        source="attempt._authorize_anchors", authority=EventAuthority.AUTHORITATIVE,
        message=f"edit #{index} in {path} is a pure insertion at the structural locus of {locus.owner_lookup_key}",
        details={"path": path, "locus": locus.summary(), "capability": capability.digest},
    ))


def _sent_exact_source_covers(state: GenerationState, capability: Any, path: str, search: str, current: str) -> bool:
    """P3-A: an anchor outside the capability's spans is still one the model
    relied on exact source for when the request this invocation actually
    dispatched (as fitted) carried, verbatim, the whole current file or a
    current exact member unit containing it - the byte rule
    _decide_edit_capabilities applies to mandatory text, applied to what was
    really sent (optional text the fit trimmed stays unseen). Only the
    capability's own revision qualifies, and it authorizes this anchor only:
    the operations offered, the write scope and full-file authority (D1's
    alone) are untouched."""
    from kriya.workflow.edit_safety import normalize_whitespace

    sent = state.edit_capability_sent.get(path) or ()
    revision = content_revision(current)
    if not sent or revision != capability.revision or not current.strip():
        return False
    pieces = [current] if any(current in prompt for prompt in sent) else []
    pieces.extend(
        member.content for member in state.known_target_member_items.get(path, ())
        if member.is_exact and member.content and (not member.revision or member.revision == revision)
        and any(member.content in prompt for prompt in sent))
    norm_search = normalize_whitespace(search)
    if not any(norm_search in normalize_whitespace(piece) for piece in pieces):
        return False
    state.record_event(RunEvent(
        kind="context.anchor_authorized_by_sent_request", attempt=state.attempt_number,
        source="attempt._authorize_anchors", authority=EventAuthority.AUTHORITATIVE,
        message=f"anchor in {path} lies in exact current source the dispatched request carried verbatim",
        details={"path": path, "revision": revision, "capability": capability.digest},
    ))
    return True


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
    )


def _load_is_local_url() -> Any:
    # E9: runtime_verification's function-local imports (core.llm, skills.skill: all-import cycle), done where the bodies
    # imported them; the self-correction loop reuses E8's _load_run_self_correction_loop.
    from kriya.core.llm import is_local_url
    return is_local_url


def _load_mark_rules_verified() -> Any:
    from kriya.skills.skill import mark_rules_verified
    return mark_rules_verified


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
        load_mark_rules_verified=_load_mark_rules_verified,
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
    return f"attempt.subtask.{subtask_id}.process_boundary_compatibility"


_PROCESS_BOUNDARY_RECURRENCE_ESCALATION = (
    "\n\nRECURRING FAILURE: this exact process-boundary obligation was already recorded "
    "VIOLATED on an earlier attempt for this subtask and is still unresolved. Toggling the "
    "same call between two states again (e.g. undoing a previous attempt's fix) will not "
    "resolve it - propose the structural separation (or out-of-process verification) "
    "described above instead of repeating a change already tried."
)


def _record_process_boundary_obligation(
    ctx: "AttemptContext", state: "GenerationState", *, violated: bool, evidence: Dict[str, Any],
) -> bool:
    """PRV-06 (2026-08-28): records ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY
    when a test_process_terminated failure fires (violated=True), and clears
    it back to SATISFIED the next time this subtask's test gate passes
    (violated=False) - the same VIOLATED->SATISFIED tracking shape already
    established for PLAN_STRUCTURAL_VALIDITY, giving this failure class real
    cross-attempt identity instead of looking like a fresh, unrelated defect
    every retry.

    Returns True when violated=True and this exact obligation was ALREADY on
    record as VIOLATED before this call - i.e. the SAME process-boundary
    conflict recurring on a later attempt for the SAME subtask, never a
    different subtask or an earlier-but-since-SATISFIED occurrence (prior
    must itself be VIOLATED, not merely "violated at some point in this
    obligation's history" - a subtask that fixed this and later broke it a
    different way starts a fresh, non-recurrent record; recurrence means
    "still unresolved between two consecutive checks," not "has a violated
    entry somewhere in its past"). Callers use this to escalate the next
    repair message with _PROCESS_BOUNDARY_RECURRENCE_ESCALATION instead of
    silently repeating the same generic guidance every attempt - this is
    what actually closes the oscillation PRV-06 hit live (the Developer
    flipped System.exit <-> return for 11 attempts because nothing
    distinguished "first occurrence" from "still unresolved"; recording the
    obligation alone would only have made that observable in the ledger, not
    prevented it). Recurrence is also written into the RECORD's own
    `evidence` (recurrence/prior_violation_attempt/current_attempt) - a
    durable control-plane fact inspectable from the ledger itself, not just
    baked into a transient message string; the escalation text is only how
    that fact gets exposed to the Developer, not the fact's only home.

    Deliberately scoped to a structured/enforce subtask only
    (ctx.current_subtask_id is the natural revision-tracked owner here, same
    reasoning as every other owner_subtask_id in this module family) - a
    plain Legacy run has no subtask id to anchor cross-attempt identity on
    and doesn't consume obligation tracking today either. A SATISFIED record
    is only written when this exact obligation was already VIOLATED for this
    subtask, so an ordinary subtask that never hit this failure never
    accumulates a needless record. Different subtasks get different
    obligation ids (the subtask id is baked into the id itself) and one
    ObligationLedger is scoped to one workflow run (see its own class
    docstring) - recurrence can never fire across an unrelated subtask or an
    unrelated run."""
    if ctx.obligation_ledger is None or not ctx.current_subtask_id:
        return False
    obligation_id = _process_boundary_obligation_id(ctx.current_subtask_id)
    prior = ctx.obligation_ledger.current(obligation_id)
    if not violated and prior is None:
        return False
    is_recurrence = violated and prior is not None and prior.status == ObligationStatus.VIOLATED
    record_evidence = dict(evidence)
    if violated:
        record_evidence["recurrence"] = is_recurrence
        record_evidence["current_attempt"] = state.attempt_number
        if is_recurrence:
            record_evidence["prior_violation_attempt"] = prior.revision
    record = ObligationRecord(
        id=obligation_id, kind=ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY,
        status=ObligationStatus.VIOLATED if violated else ObligationStatus.SATISFIED,
        authority=ObligationAuthority.DETERMINISTIC,
        description="subtask's test execution must not trigger process/fork termination "
                    "in already-written production code invoked in-process by a test",
        source="attempt.run_attempt", revision=state.attempt_number,
        evidence=record_evidence, owner_subtask_id=ctx.current_subtask_id, terminal_required=False,
    )
    ctx.obligation_ledger.record(record)
    # MA9 (2026-08-29): a VIOLATED record is the trigger to (re)derive a
    # coordinated RepairContract; a SATISFIED record closes whichever
    # contract this exact obligation opened, if any - see
    # kriya/workflow/repair_contract.py's own module docstring. Neither
    # branch does anything when build_repair_contract()/evidence extraction
    # can't unambiguously classify this - state.repair_contract simply stays
    # whatever it already was (None for every run that never hits this).
    if violated:
        _sync_active_repair_contract(ctx, state, record)
    elif state.repair_contract is not None and obligation_id in state.repair_contract.source_obligation_ids:
        state.repair_contract.status = RepairContractStatus.SATISFIED
        state.record_event(RunEvent(
            kind="repair_contract_satisfied", attempt=state.attempt_number, source="attempt.run_attempt",
            authority=EventAuthority.ADVISORY,
            message=f"RepairContract '{state.repair_contract.id}' satisfied - originating obligation resolved.",
            details={"repair_contract_id": state.repair_contract.id, "obligation_id": obligation_id},
        ))
    return is_recurrence


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


_NONZERO_PROCESS_EXIT_RE = re.compile(
    r"(?:non[ -]?zero(?:\s+[a-z]+){0,3}\s+exit|(?:process\s+)?exits?(?:\s+code)?\s+(?:is\s+|must\s+be\s+)?non[ -]?zero)",
    re.IGNORECASE,
)

_INITIAL_TEST_PROCESS_BOUNDARY_CONSTRAINT = (
    "\n\n=== Process-boundary test constraint ===\n"
    "The structured runtime/process-boundary contract explicitly requires a non-zero process exit. "
    "Tests must not invoke a process-terminating application path in the test runner's own process. "
    "Do not use SecurityManager or System.setSecurityManager to intercept termination. Preserve the "
    "required System.exit behavior. Verify exit code/stdout/stderr through a child process or leave "
    "that obligation to the declared application_runtime verifier. This constraint changes only the "
    "verification strategy; do not invent additional product behavior or edge cases (for example, "
    "overflow handling) that are absent from the authoritative goal."
)


def _initial_test_process_boundary_constraint(
    required_verification: List[Dict[str, Any]], target_files: Optional[List[str]],
) -> str:
    """Return a test-generation constraint only for an explicit runtime fact."""
    if not any(is_runnable_test_file(path) for path in (target_files or [])):
        return ""
    return (
        _INITIAL_TEST_PROCESS_BOUNDARY_CONSTRAINT
        if _required_process_terminating_cases(required_verification)
        else ""
    )


def _runtime_contract_requirements(ctx: "AttemptContext") -> List[Dict[str, Any]]:
    """Collect runtime/process-boundary facts available to this subtask."""
    requirements = list(ctx.required_verification)
    if ctx.structured_plan is not None:
        for subtask in ctx.structured_plan.subtasks:
            requirements.extend(
                verification.model_dump(mode="json")
                for verification in subtask.verification
            )
        current = ctx.structured_plan.subtask_by_id(ctx.current_subtask_id or "")
        relevant_ids = set(current.relevant_global_invariant_ids if current else [])
        for invariant in ctx.structured_plan.global_invariants:
            if invariant.id in relevant_ids:
                requirements.append({
                    "description": invariant.statement,
                    "process_boundary_authority": "relevant_global_invariant",
                    "invariant_id": invariant.id,
                })
    deduplicated: Dict[str, Dict[str, Any]] = {}
    for requirement in requirements:
        key = repr(sorted(requirement.items()))
        deduplicated[key] = requirement
    return list(deduplicated.values())


_TERMINATING_CASE_MARKERS = {
    "invalid": ("invalid input", "invalid value", "malformed input", "non-numeric input"),
    "missing": ("missing input", "no input", "missing argument", "no argument"),
}


def _required_process_terminating_cases(
    required_verification: List[Dict[str, Any]],
) -> List[str]:
    """Extract only case labels explicitly grounded by the runtime contract."""
    cases = set()
    for requirement in required_verification:
        runtime_verifier = (
            requirement.get("verifier_kind") == "application_runtime"
            and requirement.get("requires_runtime_execution") is True
        )
        relevant_global_invariant = (
            requirement.get("process_boundary_authority") == "relevant_global_invariant"
        )
        if not (runtime_verifier or relevant_global_invariant):
            continue
        description = str(requirement.get("description") or "")
        if not _NONZERO_PROCESS_EXIT_RE.search(description):
            continue
        lowered = description.lower()
        for case, phrases in _TERMINATING_CASE_MARKERS.items():
            if any(phrase in lowered for phrase in phrases):
                cases.add(case)
    return sorted(cases)


def _enclosing_test_method(source: str, offset: int) -> Optional[str]:
    prefix = source[:offset]
    matches = list(re.finditer(
        r"(?m)^\s*(?:public\s+|protected\s+|private\s+)?(?:static\s+)?"
        r"(?:void|[A-Za-z_$][\w.$<>\[\], ?]*)\s+([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*\{",
        prefix,
    ))
    return matches[-1].group(1) if matches else None


def _enclosing_test_method_source(source: str, offset: int) -> str:
    """Return the enclosing Java-like method body using bounded brace matching.

    This is deliberately a small source-shape helper, not a Java parser.  It is
    used only to resolve a local argv variable feeding a grounded main(...) call.
    """
    prefix = source[:offset]
    matches = list(re.finditer(
        r"(?m)^\s*(?:public\s+|protected\s+|private\s+)?(?:static\s+)?"
        r"(?:void|[A-Za-z_$][\w.$<>\[\], ?]*)\s+[A-Za-z_$][\w$]*\s*\([^;{}]*\)\s*\{",
        prefix,
    ))
    if not matches:
        return source
    start = matches[-1].start()
    open_brace = source.find("{", matches[-1].start(), offset + 1)
    if open_brace < 0:
        return source[start:]
    depth = 0
    for index in range(open_brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return source[start:]


def _resolve_java_main_argv_expression(method_source: str, call_expression: str) -> str:
    """Resolve a direct main(...) argument or a simple local String[] variable.

    Generated tests commonly use:
      String[] value = {"not-a-number"};
      App.main(value);
    The old gate inspected the method name, so this neutral-name form escaped.
    Resolve the local initializer instead; unresolved expressions remain as-is.
    """
    expression = call_expression.strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", expression):
        return expression
    variable = re.escape(expression)
    assignments = list(re.finditer(
        rf"(?:String\s*\[\s*\]|String\s+\[\s*\])\s*{variable}\s*=\s*"
        r"(?P<value>new\s+String\s*\[\s*\]\s*\{[^;]*\}|\{[^;]*\})\s*;",
        method_source,
        re.DOTALL,
    ))
    return assignments[-1].group("value").strip() if assignments else expression


def _runtime_contract_expects_numeric_input(
    required_verification: List[Dict[str, Any]],
) -> bool:
    """Whether authoritative runtime text explicitly identifies numeric input."""
    for requirement in required_verification:
        runtime_verifier = (
            requirement.get("verifier_kind") == "application_runtime"
            and requirement.get("requires_runtime_execution") is True
        )
        relevant_global_invariant = (
            requirement.get("process_boundary_authority") == "relevant_global_invariant"
        )
        if not (runtime_verifier or relevant_global_invariant):
            continue
        description = str(requirement.get("description") or "").lower()
        if re.search(r"\b(?:integer|numeric|number)\b", description):
            return True
    return False


def _terminating_case_from_argv_expression(
    cases: List[str], argv_expression: str, *, numeric_input: bool,
) -> Optional[str]:
    """Classify only from the actual argv expression, never the test method name."""
    compact = re.sub(r"\s+", "", argv_expression)
    if "missing" in cases and (
        re.fullmatch(r"newString\[0\]", compact)
        or re.fullmatch(r"newString\[\]\{\}", compact)
        or re.fullmatch(r"\{\}", compact)
    ):
        return "missing input"

    string_literals = re.findall(r'"((?:\\.|[^"\\])*)"', argv_expression)
    if "invalid" in cases and string_literals:
        lowered_literals = [literal.lower() for literal in string_literals]
        if any(
            marker in literal
            for literal in lowered_literals
            for marker in ("invalid", "malformed", "not-a-number", "not_a_number", "nonnumeric", "non-numeric")
        ):
            return "invalid input"
        if numeric_input:
            for literal in string_literals:
                try:
                    int(literal, 10)
                except ValueError:
                    return "invalid input"
    return None


def find_in_process_terminating_test_invocations(
    required_verification: List[Dict[str, Any]],
    worktree_path: str,
    test_files: List[str],
    application_entrypoints: List[str],
) -> List[Dict[str, Any]]:
    """Find test calls that contradict an explicit process-boundary contract.

    Source text never establishes that a path terminates. The structured
    application_runtime requirement establishes that fact and names its case;
    source inspection only connects that known case to an in-process call of a
    grounded entrypoint. Child-process tests contain no such direct call and
    therefore remain admissible.
    """
    cases = _required_process_terminating_cases(required_verification)
    if not cases or not application_entrypoints:
        return []
    entrypoint_names = sorted({
        name for fqcn in application_entrypoints
        for name in (fqcn, fqcn.rsplit(".", 1)[-1])
    }, key=len, reverse=True)
    call_re = re.compile(
        r"\b(?:" + "|".join(re.escape(name) for name in entrypoint_names)
        + r")\s*\.\s*main\s*\((?P<argv>[^;\n]*)\)",
    )
    numeric_input = _runtime_contract_expects_numeric_input(required_verification)
    findings: List[Dict[str, Any]] = []
    for filepath in sorted(test_files):
        full_path = os.path.join(worktree_path, filepath)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as handle:
                source = handle.read()
        except OSError:
            continue
        for match in call_re.finditer(source):
            method = _enclosing_test_method(source, match.start())
            method_source = _enclosing_test_method_source(source, match.start())
            argv_expression = _resolve_java_main_argv_expression(
                method_source, match.group("argv"),
            )
            matched_case = _terminating_case_from_argv_expression(
                cases, argv_expression, numeric_input=numeric_input,
            )
            if matched_case is None:
                continue
            findings.append({
                "test_file": filepath,
                "test_method": method,
                "line": source.count("\n", 0, match.start()) + 1,
                "entrypoint_call": match.group(0),
                "terminating_case": matched_case,
            })
    return findings


def _raise_unsafe_process_boundary_test_candidate(
    state: GenerationState, ctx: "AttemptContext", test_files: List[str],
) -> None:
    known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
    java_files = [path for path in known_files if path.endswith(".java")]
    entrypoints = sorted(set(_build_java_main_class_map(java_files, ctx).values()))
    findings = find_in_process_terminating_test_invocations(
        _runtime_contract_requirements(ctx), ctx.worktree_path, test_files, entrypoints,
    )
    if not findings:
        return
    offending_files = sorted({item["test_file"] for item in findings})
    detail_lines = [
        f"- {item['test_file']}:{item['line']}"
        + (f" method={item['test_method']}" if item.get("test_method") else "")
        + f" invokes {item['entrypoint_call']} for {item['terminating_case']}"
        for item in findings
    ]
    message = (
        "PROCESS_TERMINATING_BEHAVIOR_TESTED_IN_PROCESS: generated test code invokes an "
        "application entrypoint in the test runner process for behavior the structured "
        "runtime/process-boundary contract requires to terminate with a non-zero exit.\n"
        + "\n".join(detail_lines)
        + "\nPreserve the product's required process-terminating behavior. Repair the TEST only: "
        "remove the in-process case or execute it through a child process and assert exit "
        "code/stdout/stderr. The application_runtime verifier remains responsible for the "
        "required exit-code behavior."
    )
    failure = Failure(
        type="process_terminating_behavior_tested_in_process",
        message=message,
        raw_output=message,
        file_locations=[
            FileLocation(filepath=item["test_file"], line=item["line"])
            for item in findings
        ],
        likely_files=offending_files,
        failed_content=_capture_failed_content(ctx.worktree_path, offending_files),
        diagnostics={
            "reason_code": "PROCESS_TERMINATING_BEHAVIOR_TESTED_IN_PROCESS",
            "repair_owner": "test",
            "findings": findings,
        },
        attempt=state.attempt_number,
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def find_ungrounded_java_child_process_tests(
    required_verification: List[Dict[str, Any]],
    worktree_path: str,
    test_files: List[str],
    java_main_classes: Dict[str, str],
    known_files: List[str],
) -> List[Dict[str, Any]]:
    """Find invented Java child launch mechanics for a process-exit contract.

    This does not duplicate the in-process safety gate above: that gate decides
    whether a test crossed a process boundary at all.  This check verifies that
    an elected boundary uses the entrypoint and classpath Kriya can ground.
    """
    if not _required_process_terminating_cases(required_verification):
        return []
    if len(java_main_classes) != 1:
        return []
    entrypoint = next(iter(java_main_classes.values()))
    simple_name = entrypoint.rsplit(".", 1)[-1]
    if "pom.xml" in known_files:
        classes_dir = "target/classes"
    elif any(path.endswith(("build.gradle", "build.gradle.kts")) for path in known_files):
        classes_dir = "build/classes/java/main"
    else:
        classes_dir = ".kriya/runtime-verification/classes"
    expected = build_grounded_java_launch_command(entrypoint, ["<args>"], classes_dir)
    findings: List[Dict[str, Any]] = []
    for filepath in sorted(test_files):
        try:
            with open(
                os.path.join(worktree_path, filepath),
                encoding="utf-8", errors="replace",
            ) as handle:
                source = handle.read()
        except OSError:
            continue
        if "ProcessBuilder" not in source or ".start()" not in source:
            continue
        mentions_entrypoint = (
            f'"{entrypoint}"' in source or f'"{simple_name}"' in source
        )
        reasons = []
        if not mentions_entrypoint:
            reasons.append(f"does not launch grounded main class {entrypoint}")
        if 'System.getProperty("java.class.path")' in source:
            reasons.append("reuses the test-runner/Surefire classpath")
        if classes_dir not in source:
            reasons.append(f"does not use grounded application classpath {classes_dir}")
        if "waitFor()" not in source:
            reasons.append("does not capture the child exit code")
        if "getInputStream()" not in source and "getErrorStream()" not in source:
            reasons.append("does not capture child stdout/stderr")
        if reasons:
            findings.append({
                "test_file": filepath,
                "reasons": reasons,
                "entrypoint": entrypoint,
                "classes_dir": classes_dir,
                "expected_command": expected,
            })
    return findings


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
    """FS-1A test-execution integrity for a subtask explicitly responsible
    for tests (kriya/workflow/test_delta.py): its changed test sources must
    have produced test identities that executed and passed in the accepted
    gate invocation's own structured report. When that invocation did not
    run every changed test source (a single targeted file) or carries no
    structured report (a self-corrected result), one covering run supplies
    the evidence. TEST_NOT_EXECUTED / TEST_NOT_PASSED is a repairable
    test-acceptance failure naming the identities; INDETERMINATE evidence is
    a typed stop, never a pass."""
    changed = _changed_test_sources(state, ctx)
    if not changed:
        return
    covered = selected_test is None or set(changed) == {selected_test}
    result = accepted_result
    if not covered or test_execution.report_from_result(result) is None:
        targeted = validator.stack == "python"
        result = validator.run_tests(target_test=sorted(changed)) if targeted else validator.run_tests()
        _stop_on_environment_gate_result(state, result, "test")
        # A targeted run executes only the changed tests - its failures are the candidate's own; a full-suite
        # run is attributed against the frozen baseline (CANDIDATE-GATE-BASELINE-POLICY-001).
        blocking, evidence, extra = ((not result.get("success"), result.get("output", ""), {}) if targeted
                                     else _judge_candidate_suite(state, ctx, result, SCOPE_TEST_DELTA_COVERING_RUN))
        if blocking:
            failure = _build_test_quality_gate_failure(
                "test", f"TEST FAILURE:\n{evidence}",
                evidence, ctx.worktree_path, state.all_files_written, state.attempt_number,
            )
            failure.diagnostics = {**(failure.diagnostics or {}), **extra}
            state.record_gate_outcome({**failure.to_gate_outcome(), **extra})
            raise QualityGateFailure(failure)
    report = test_execution.report_from_result(result)
    verdict = judge_test_delta(changed, report)
    state.record_gate_outcome({
        "attempt": state.attempt_number, "type": "test_delta", "success": verdict.satisfied,
        "reason_code": verdict.reason_code, "test_delta": verdict.to_dict(),
        "test_execution": report.summary() if report is not None else None,
    })
    if verdict.satisfied:
        return
    if verdict.reason_code == TEST_EXECUTION_EVIDENCE_INDETERMINATE:
        failure = Failure(
            type="verification_infrastructure_failure",
            message=f"{TEST_EXECUTION_EVIDENCE_INDETERMINATE}: {verdict.detail}",
            raw_output=verdict.detail, likely_files=sorted(changed), attempt=state.attempt_number,
            diagnostics={"reason_code": verdict.reason_code, "test_delta": verdict.to_dict()},
        )
    else:
        failure = Failure(
            type="test_acceptance",
            message=(
                f"TEST ACCEPTANCE FAILURE ({verdict.reason_code}): the goal requires tests, and the "
                f"changed test code must run as tests and pass. {verdict.detail}. A test the runner "
                "does not execute (for example a method without its test annotation, or a function "
                "the runner does not collect) verifies nothing."
            ),
            raw_output=verdict.detail, likely_files=sorted(changed), attempt=state.attempt_number,
            diagnostics={"reason_code": verdict.reason_code, "test_delta": verdict.to_dict()},
        )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _raise_ungrounded_child_process_test_candidate(
    state: GenerationState, ctx: "AttemptContext", test_files: List[str],
) -> None:
    known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
    java_files = [path for path in known_files if path.endswith(".java")]
    findings = find_ungrounded_java_child_process_tests(
        _runtime_contract_requirements(ctx), ctx.worktree_path, test_files,
        _build_java_main_class_map(java_files, ctx), known_files,
    )
    if not findings:
        return
    offending = sorted({item["test_file"] for item in findings})
    details = "\n".join(
        f"- {item['test_file']}: " + "; ".join(item["reasons"])
        for item in findings
    )
    exemplar = findings[0]
    message = (
        "VERIFICATION_INFRASTRUCTURE_FAILURE: generated child-process verification "
        "does not use Kriya's grounded application launch.\n" + details
        + "\nRepair the TEST/verification mechanism only; preserve application behavior. "
        f"Use main class {exemplar['entrypoint']} with classpath "
        f"{exemplar['classes_dir']}, launch a distinct child, wait for it, and capture "
        "stdout/stderr/exit code. A child launch/setup failure is not evidence against "
        "the application source."
    )
    failure = Failure(
        type="test_verification_infrastructure_failure",
        message=message, raw_output=message,
        likely_files=offending, attempt=state.attempt_number,
        diagnostics={
            "reason_code": "UNGROUNDED_CHILD_PROCESS_LAUNCH",
            "grounded_entrypoint": exemplar["entrypoint"],
            "grounded_classpath": exemplar["classes_dir"],
        },
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


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
    return os.path.join(ctx.kernel.config.paths.memory, "dependency_graph.db")


def _extract_class_names_best_effort(db_path: str, filepath: str, content: str) -> List[str]:
    """Short-lived DependencyGraph open/query/close for one file - cheap
    (existing WAL-mode SQLite file, a handful of milliseconds) and avoids
    threading a long-lived connection through the entire per-file write loop
    below just to reuse extract_class_names(), which is an instance method,
    not static. Best-effort: any error (including a missing db_path, checked
    by the caller) degrades to no names extracted, never a false rejection."""
    try:
        from kriya.analyzer.graph import DependencyGraph
        graph = DependencyGraph(db_path)
        try:
            return graph.extract_class_names(filepath, content)
        finally:
            graph.close()
    except Exception as e:
        logger.debug(f"Skipping duplicate-type check for {filepath}: {e}")
        return []


def _find_java_main_class_best_effort(db_path: str, filepath: str, content: str) -> Optional[str]:
    """Same short-lived DependencyGraph open/query/close shape as
    _extract_class_names_best_effort() above, for
    DependencyGraph.find_java_main_class() instead - see that method's own
    docstring for what it detects and why (deterministic Java entrypoint
    resolution for a no-pom.xml project, ground_java_entrypoint_in_no_build_
    file_projects()'s call site below)."""
    try:
        from kriya.analyzer.graph import DependencyGraph
        graph = DependencyGraph(db_path)
        try:
            return graph.find_java_main_class(filepath, content)
        finally:
            graph.close()
    except Exception as e:
        logger.debug(f"Skipping Java entrypoint detection for {filepath}: {e}")
        return None


def _build_java_main_class_map(java_files: List[str], ctx: "AttemptContext") -> Dict[str, str]:
    """{filepath: entrypoint_class} for every .java file (already the
    established_files-inclusive union the caller passes in) that has a real
    `public static void main` - see find_java_main_class()'s own docstring.
    Reads each file's CURRENT content fresh (worktree first, workspace
    fallback - same lookup order used throughout this module), never cached
    across attempts unlike judge()'s own judgment: a retry can edit a file's
    content, and this determination must reflect what's actually on disk
    right now, not a stale snapshot from an earlier attempt."""
    db_path = _dependency_graph_db_path(ctx)
    result: Dict[str, str] = {}
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
            logger.debug(f"Java entrypoint detection: couldn't read {filepath}, skipping it: {e}")
            continue
        entrypoint_class = _find_java_main_class_best_effort(db_path, filepath, content)
        if entrypoint_class:
            result[filepath] = entrypoint_class
    return result


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
    """Workspace-wide simple-class-name -> [filepaths] index for the
    duplicate-type-across-files pre-flight check below and
    find_cross_package_symbol_mismatch() (see their own docstrings for the
    live incidents this exists to prevent). Two layers:

    1. DependencyGraph.get_class_symbol_locations() - the persisted baseline,
       covering pre-existing repo content and every earlier-completed
       milestone's already-applied output, PROVIDED `kriya analyze` has ever
       actually been run against this workspace. Found live, 2026-08-22
       (ignite_qpid_protocol): this baseline is NOT populated automatically
       by run_generation_workflow() at all - index_repository() (the only
       thing that ever writes real rows into dependency_graph.db's symbols
       table) is called exclusively from the explicit `kriya analyze`/
       `kriya analyze --vectors` CLI path (kriya/cli.py) - a milestone-
       decomposition project that never had that command run against it has
       a genuinely EMPTY persisted baseline for its entire lifetime, however
       many milestones have completed. Best-effort by construction regardless
       (no dependency_graph.db yet, or any DB error, degrades to an empty
       baseline for this layer) - this is exactly why layer 2 below is not
       optional supplementary coverage, it is THE primary coverage for any
       project that has never been explicitly `kriya analyze`-d.
    2. Every file in state.all_files_written UNION ctx.established_files,
       read fresh (worktree first, workspace fallback - same lookup order
       used throughout this module) and parsed via
       DependencyGraph.extract_class_names() (no DB write). Found live,
       2026-08-22, the SAME "established_files blind spot" class of bug
       already fixed three times this session at other call sites
       (RunVerifierAgent.judge()'s files_written, self-diagnosis
       attribution): this layer previously covered ONLY state.
       all_files_written (files THIS attempt itself wrote), so an earlier,
       already-completed milestone's file was invisible to both the
       duplicate-type gate and find_cross_package_symbol_mismatch() unless
       `kriya analyze` happened to have indexed it into layer 1 - for a
       project that never had that command run, EVERY earlier milestone's
       file was invisible to this whole index, silently, for this session's
       entire live-validation effort."""
    db_path = _dependency_graph_db_path(ctx)
    try:
        from kriya.analyzer.graph import DependencyGraph
        graph = DependencyGraph(db_path)
        try:
            index = graph.get_class_symbol_locations()
        finally:
            graph.close()
        for written_path in sorted(set(state.all_files_written) | set(ctx.established_files)):
            full_path = os.path.join(ctx.worktree_path, written_path)
            if not os.path.exists(full_path):
                full_path = os.path.join(ctx.workspace_path, written_path)
            if not os.path.exists(full_path):
                continue
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                written_content = fh.read()
            for name in _extract_class_names_best_effort(db_path, written_path, written_content):
                paths = index.setdefault(name, [])
                if written_path not in paths:
                    paths.append(written_path)
        return index
    except Exception as e:
        logger.debug(f"_build_workspace_type_index: skipping duplicate-type check for this attempt: {e}")
        return {}


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
    """(blocking, Developer-facing evidence, gate-outcome evidence) for one
    full-suite run: attributed against the frozen baseline when one was
    captured, else the raw ``success`` rule exactly as before."""
    attribution = _attribute_candidate_suite(state, ctx, result, scope)
    if attribution is None:
        return not result["success"], result.get("output", ""), {}
    evidence = attribution.evidence_text(state.validation_baseline_full_regression, result.get("output", ""),
                                         test_execution.failure_details(result))
    return attribution.blocking, evidence, {"regression_attribution": attribution.gate_evidence()}


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
    """MA8 (spec §31) - a short "AUTHORITATIVELY ESTABLISHED" prose block
    naming which MIGRATION_COMPLETION requirements are already
    DETERMINISTIC-authority SATISFIED, injected into SpecComplianceAgent's
    own prompt BEFORE the call - see SpecComplianceAgent.check()'s own
    docstring for why this is advisory only. The mandatory backstop
    remains _spec_requirements_contradicting_authority below, applied
    AFTER the model responds regardless of whether this context was
    honored.

    Same precondition as that function (only when EVERY current migration
    obligation is SATISFIED - a still-VIOLATED requirement is exactly what
    SpecCompliance should remain free to also flag), so the two never
    disagree about when authoritative context applies."""
    if not ledger:
        return None
    migration_records = ledger.current_by_kind(ObligationKind.MIGRATION_COMPLETION)
    if not migration_records or any(
        rec.status != ObligationStatus.SATISFIED for rec in migration_records
    ):
        return None
    lines = "\n".join(f"- {rec.description}" for rec in migration_records)
    return (
        "AUTHORITATIVELY ESTABLISHED (deterministic evidence - do not report these as "
        f"missing/incomplete):\n{lines}\n\n"
        "Evaluate only requirements not already covered by the above."
    )


def _migration_obligations_all_satisfied(ledger: Optional[ObligationLedger]) -> bool:
    """True only when the ledger has at least one current MIGRATION_
    COMPLETION obligation and every one of them is SATISFIED. Shared
    precondition for every SpecCompliance arbitration path below - a
    JUDGMENT-authority verdict may only ever be suppressed/overridden when
    the DETERMINISTIC migration gate has fully confirmed the fact it
    contradicts; a still-VIOLATED (or no-obligation-recorded-at-all) case
    means judgment and determinism simply agree, or there's nothing to
    arbitrate against, and the LLM verdict must stand."""
    if not ledger:
        return False
    migration_records = ledger.current_by_kind(ObligationKind.MIGRATION_COMPLETION)
    return bool(migration_records) and not any(
        rec.status == ObligationStatus.VIOLATED for rec in migration_records
    )


def _spec_requirements_contradicting_authority(
    missing_requirements: List[str], ledger: Optional[ObligationLedger],
) -> Tuple[List[str], List[str]]:
    """MA8 (PRV-05 run #8, 2026-08-28) - splits SpecComplianceAgent's own
    free-text missing_requirements into (kept, contradicted).

    An entry is "contradicted" when it mentions the migration's own
    source/target identity terms (e.g. "gson", "jackson-databind") AND the
    ledger's current MIGRATION_COMPLETION obligations are ALL SATISFIED -
    i.e. the DETERMINISTIC migration gate already confirmed this exact
    fact, so a JUDGMENT-authority claim to the contrary is a contradiction,
    not a second, independent finding. Deliberately does NOT try to give
    each free-text requirement its own stable ObligationRecord id (missing_
    requirements are reworded attempt to attempt by construction - exactly
    the "never derive an id from an LLM's own error string" case this
    module's own docstring warns about) - arbitration here is a one-shot
    text correlation against the migration ledger's OWN stable ids, not a
    new obligation-tracked kind.

    Never touches a requirement while ANY current migration obligation is
    still VIOLATED - that is judgment and determinism agreeing, not a
    contradiction to arbitrate away."""
    if not _migration_obligations_all_satisfied(ledger):
        return list(missing_requirements), []
    migration_records = ledger.current_by_kind(ObligationKind.MIGRATION_COMPLETION)
    # Tokenized (split on "-"/"_"), not the raw identity string as a single
    # substring: an artifactId like "jackson-databind" must still correlate
    # with prose that names the human-friendly library "Jackson" - found
    # live while verifying this fix, not assumed: a full-string match missed
    # the exact hallucinated text ("the code still uses Jackson library
    # components") this arbitration exists to catch. Short tokens (<3 chars)
    # are dropped to avoid trivial false positives.
    identity_terms = {
        token
        for rec in migration_records
        for value in (rec.evidence.get("source_identity"), rec.evidence.get("target_identity"))
        if value
        for token in re.split(r"[-_]", str(value).lower())
        if len(token) >= 3
    }
    if not identity_terms:
        return list(missing_requirements), []
    kept: List[str] = []
    contradicted: List[str] = []
    for requirement in missing_requirements:
        lowered = requirement.lower()
        matched = any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in identity_terms)
        (contradicted if matched else kept).append(requirement)
    return kept, contradicted


_REQUIREMENT_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_REQUIREMENT_PROVENANCE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "does",
    "for", "from", "has", "have", "in", "instead", "is", "it", "must",
    "named", "not", "of", "on", "or", "requires", "require", "required",
    "should", "that", "the", "this", "to", "uses", "using", "with",
}


def _identifier_local_terms(text: str, identifier: str, radius: int = 3) -> set[str]:
    """Return meaningful lexical terms close to ``identifier`` in ``text``.

    This is deliberately a provenance primitive, not a vocabulary of code
    shapes.  It knows nothing about fields, methods, routes, schemas, UI
    controls, or any particular programming language.  A term matters only
    because the Planner and the later judgment both placed it next to the
    same concrete identifier while the authoritative goal did not.
    """
    words = _REQUIREMENT_WORD_RE.findall(text)
    lowered_identifier = identifier.lower()
    terms: set[str] = set()
    for index, word in enumerate(words):
        if word.lower() != lowered_identifier:
            continue
        start = max(0, index - radius)
        end = min(len(words), index + radius + 1)
        for nearby in words[start:end]:
            normalized = nearby.lower()
            if (
                normalized != lowered_identifier
                and normalized not in _REQUIREMENT_PROVENANCE_STOPWORDS
                and len(normalized) >= 3
            ):
                terms.add(normalized)
    return terms


def _extract_requirement_identifier_tokens(requirement: str) -> List[str]:
    """Pulls candidate provenance-check terms out of a SpecCompliance
    missing_requirement string - reuses the SAME general word/stopword
    primitives _identifier_local_terms above already uses for nearby-term
    provenance, rather than a syntax-specific shape (camelCase, backtick/
    quote-wrapped, a dotted manifest filename, a version specifier, ...).

    PRV-17 (2026-09-03): an earlier version of this function matched only
    code-shaped identifiers via a hand-written regex, extended twice live
    to also catch a dotted manifest filename ("requirements.txt") and a
    PEP 508 version specifier ("python>=3.12") after each shape was found,
    separately, to slip through with zero extracted tokens. A THIRD shape
    ("requires-python = \">=3.12\"") and a fourth, pure-prose one ("minimum
    Python version 3.12") immediately proved this was an open-ended list,
    not a closed one - correctness must not depend on anticipating every
    way a Planner (or SpecComplianceAgent's own judgment) can phrase an
    invented detail. General word extraction needs no such list: _spec_
    requirements_naming_planner_only_identifiers' own section-provenance
    comparison (does this word, or its nearby context, appear only in
    Planned Implementation, never Authoritative Goal) already does the
    real work for ANY word shape, once given real candidate words to
    check - producing those candidates is this function's only job.
    Excludes short/stopword-only words the same way _identifier_local_
    terms already does, so a bare "Customer" or "The" isn't itself proof
    of anything (matched or not, the actual provenance decision is
    _spec_requirements_naming_planner_only_identifiers' own nearby-context
    comparison, not this function's word list)."""
    tokens: List[str] = []
    for word in _REQUIREMENT_WORD_RE.findall(requirement):
        if (
            len(word) >= 3
            and word.lower() not in _REQUIREMENT_PROVENANCE_STOPWORDS
            and word not in tokens
        ):
            tokens.append(word)
    return tokens


def _spec_requirements_naming_planner_only_identifiers(
    missing_requirements: List[str], goal_text: str,
) -> Tuple[List[str], List[str]]:
    """PRV-11 (2026-08-30) - the deterministic other half of build_subtask_
    goal_text()'s own authority-isolation split (kriya/workflow/
    workflow_controller.py). That function already labels a Planner-only
    identifier under PLANNED IMPLEMENTATION STRATEGY and SpecComplianceAgent's
    own system_prompt already instructs the model not to treat it as a
    requirement - but a prompt instruction alone is not a guarantee, the same
    "do not trust the prompt alone" principle _spec_requirements_
    contradicting_authority above already applies to the migration case, just
    never extended to this one. Live incident this closes: SpecCompliance's
    own judge repeatedly reported "the goal requires a displayName field",
    reconstructing the model's own worked counter-example from its system
    prompt almost verbatim, even though "displayName field" appears ONLY in
    the Planned Implementation Strategy section of the exact goal text it was
    given, never in the Authoritative Goal section.

    Splits missing_requirements into (kept, planner_only). An entry is
    planner_only in either of two text-grounded cases:

    * it names a concrete identifier found only in Planned Implementation; or
    * the identifier is authoritative, but the judgment attaches a nearby
      constraining term that is also attached to it in Planned Implementation
      and absent from Authoritative Goal.

    The second case closes a more subtle authority leak: a Planner can preserve
    the user's identifier while silently narrowing its representation. The
    implementation is intentionally language- and use-case-agnostic: it has no
    catalog of representation words. It correlates lexical provenance around
    the same identifier instead. Requirements with no identifier, behavioral
    terms grounded in the authoritative section, and judgment-only terms with
    no Planner provenance are always kept.

    A no-op (everything kept) when goal_text doesn't carry both section
    headers - every pre-MA6 caller, and every subtask goal without a real
    top-level goal to separate out, sees identical behavior to before this
    function existed."""
    if (
        AUTHORITATIVE_GOAL_SECTION_HEADER not in goal_text
        or PLANNED_IMPLEMENTATION_SECTION_HEADER not in goal_text
    ):
        return list(missing_requirements), []
    authoritative_text = goal_text.split(AUTHORITATIVE_GOAL_SECTION_HEADER, 1)[1].split(
        PLANNED_IMPLEMENTATION_SECTION_HEADER, 1,
    )[0]
    planned_text = goal_text.split(PLANNED_IMPLEMENTATION_SECTION_HEADER, 1)[1]
    kept: List[str] = []
    planner_only: List[str] = []
    for requirement in missing_requirements:
        tokens = _extract_requirement_identifier_tokens(requirement)
        if not tokens:
            kept.append(requirement)
            continue
        authoritative_tokens = [
            tok for tok in tokens
            if re.search(rf"\b{re.escape(tok)}\b", authoritative_text)
        ]
        planned_tokens = [
            tok for tok in tokens
            if re.search(rf"\b{re.escape(tok)}\b", planned_text)
        ]
        planner_only_identifier = bool(planned_tokens) and not authoritative_tokens
        planner_only_constraint = False
        for token in set(authoritative_tokens) & set(planned_tokens):
            requirement_terms = _identifier_local_terms(requirement, token)
            planned_terms = _identifier_local_terms(planned_text, token)
            authoritative_terms = _identifier_local_terms(authoritative_text, token)
            if (requirement_terms & planned_terms) - authoritative_terms:
                planner_only_constraint = True
                break
        (planner_only if planner_only_identifier or planner_only_constraint else kept).append(
            requirement
        )
    return kept, planner_only


def _goal_spec_requirement_obligation_id(subtask_id: str) -> str:
    return f"attempt.subtask.{subtask_id}.goal_spec_requirement"


def _stage_scoped_spec_compliance_goal(ctx: "AttemptContext") -> tuple[str, List[str]]:
    """Return only obligations due at the current structured-plan stage.

    The top-level goal remains the authority used to validate the plan.  Once
    that plan is approved, however, its acceptance-criterion ownership is the
    deterministic schedule for *when* each final requirement can be demanded.
    Feeding the whole final goal to every intermediate candidate gate made a
    project-scaffold stage fail for an endpoint explicitly owned by a later
    application stage.  Unstructured callers retain the historical behavior.

    Stage-projection ownership (PRV-17, 2026-09-03): `current`'s own
    acceptance_criteria_ids/relevant_global_invariant_ids are Planner-
    authored data this function used to trust in isolation. An earlier
    version of this fix deferred an id whenever ANY other pending subtask
    also claimed it - but bare duplicate occurrence is not proof of FUTURE
    ownership: an accidental/lazy Planner assignment onto an UNRELATED
    sibling subtask produces the exact same "also claimed elsewhere"
    signal a genuine future owner would, and silently deferring on that
    signal alone would just as easily hide a real, currently-due
    violation. This now asks the plan's own validated dependency DAG
    directly via EngineeringPlan.classify_requirement_ownership() (mirrors
    classify_file_ownership()'s existing PAST_ORDERED/CURRENT/
    FUTURE_ORDERED/UNRELATED reasoning, generalized to a claimant-id list
    since a criterion/invariant carries no file path to derive ownership
    from) - a FUTURE_ORDERED verdict requires a REAL, structurally later
    (not-yet-run, provably downstream) claimant, not just another mention.
    A genuinely stage-spanning claim with no provable DAG relationship
    (UNRELATED) falls back to this function's own pre-existing behavior
    (trust current's claim, evaluate now) rather than inventing a new
    resolution for an ambiguity the plan's own structure can't settle.
    """
    plan = ctx.structured_plan
    current_id = ctx.current_subtask_id
    if plan is None or not current_id:
        return ctx.goal, []
    current = plan.subtask_by_id(current_id)
    if current is None:
        return ctx.goal, []

    def _claimants(cid: str, field: str) -> List[str]:
        return [st.id for st in plan.subtasks if cid in getattr(st, field)]

    criteria = {criterion.id: criterion.description for criterion in plan.acceptance_criteria}
    due_acceptance_ids = [
        cid for cid in current.acceptance_criteria_ids
        if cid in criteria
        and plan.classify_requirement_ownership(current_id, _claimants(cid, "acceptance_criteria_ids"))
        != RequirementOwnershipRelation.FUTURE_ORDERED
    ]
    due = [criteria[cid] for cid in due_acceptance_ids]
    invariants = {invariant.id: invariant.statement for invariant in plan.global_invariants}
    due_invariant_ids = [
        iid for iid in current.relevant_global_invariant_ids
        if iid in invariants
        and plan.classify_requirement_ownership(current_id, _claimants(iid, "relevant_global_invariant_ids"))
        != RequirementOwnershipRelation.FUTURE_ORDERED
    ]
    due.extend(invariants[iid] for iid in due_invariant_ids)
    # Genuinely still-pending work only (claimed by a not-yet-completed
    # OTHER subtask) - pure observability, matching this field's own
    # pre-existing contract; an id only a COMPLETED subtask ever claimed is
    # done, not "future", regardless of whether current also excluded it.
    pending_subtask_ids = {
        st.id for st in plan.subtasks if st.id != current_id and st.id not in ctx.completed_subtask_ids
    }
    pending_acceptance_ids = {
        cid for st in plan.subtasks if st.id in pending_subtask_ids for cid in st.acceptance_criteria_ids
    }
    pending_invariant_ids = {
        iid for st in plan.subtasks if st.id in pending_subtask_ids for iid in st.relevant_global_invariant_ids
    }
    future_ids = sorted(
        (pending_acceptance_ids - set(due_acceptance_ids))
        | (pending_invariant_ids - set(due_invariant_ids))
    )
    scoped = (
        f"Current approved subtask: {current.id}\n"
        f"Current implementation obligation: {current.description}\n"
        "Acceptance requirements due at this stage:\n"
        + ("\n".join(f"- {item}" for item in due) if due else "- None beyond the current implementation obligation")
        + "\nOnly decide whether this current stage satisfies the requirements listed above. "
          "Requirements assigned to unfinished later subtasks are pending, not violated."
    )
    return scoped, future_ids


def _goal_spec_evidence_fingerprint(goal: str, file_contents: Dict[str, str]) -> str:
    """Correctness Continuity Part A (PRV-06, 2026-08-29) - a stable digest
    of exactly what a goal_spec_compliance verdict was based on: the
    requirement text plus every checked file's own content, byte for byte.
    Reuses edit_safety.content_revision (the same primitive the anchored-
    edit pipeline already trusts for content identity) rather than
    inventing a second hashing scheme. Two calls producing the same
    fingerprint mean the judge was shown literally the same requirement
    and the same code - the precondition evidence monotonicity requires
    before a settled verdict may be reused or a contradiction suppressed.
    A single byte of real change (either side) produces a different
    fingerprint and is always treated as genuinely new evidence, never
    "close enough" - see _settled_goal_spec_requirement's own docstring."""
    blob = goal + "\x00" + "\x00".join(
        f"{path}\x01{file_contents[path]}" for path in sorted(file_contents)
    )
    return content_revision(blob)


def _record_original_requirement_verdicts(
    state: GenerationState, ctx: "AttemptContext", spec_result: Dict[str, Any], fingerprint: str,
) -> None:
    """PRD-020: record the verifier's per-requirement outcome for the exact
    candidate it judged (the evidence id is the fingerprint of the goal and
    every checked file). Runs only here, after every deterministic gate of
    this attempt passed; a requirement without a readable verdict is
    UNKNOWN. The event is the run's durable lineage record."""
    requirements = ctx.requirement_set
    if requirements is None or ctx.obligation_ledger is None:
        return
    # MODEL-EVIDENCE-HARDENING-001: typed reasons and the verifier identity.
    verdicts, findings, missing_reason, missing_detail = verifier_result_verdicts(spec_result, requirements)
    outcomes = record_requirement_verdicts(
        ctx.obligation_ledger, requirements, verdicts,
        revision=state.attempt_number, evidence_fingerprint=fingerprint,
        source="attempt.goal_spec_compliance",
        gate_evidence=[f"attempt {state.attempt_number}: deterministic gates passed"],
        missing_reason=missing_reason, missing_detail=missing_detail,
        verifier=spec_result.get("verifier"),
    )
    details = requirement_verdict_details(ctx.obligation_ledger, requirements)
    state.record_event(RunEvent(
        kind="requirement.verdicts", attempt=state.attempt_number, source="attempt.goal_spec_compliance",
        authority=EventAuthority.ADVISORY,
        message="original requirement outcomes: " + ", ".join(
            f"{rid}={outcome.value}({details[rid]['reason_code']})" for rid, outcome in outcomes.items()),
        details={"requirement_set_digest": requirements.digest, "evidence_id": fingerprint,
                 "outcomes": {rid: outcome.value for rid, outcome in outcomes.items()},
                 "evidence": {rid: entry[1] for rid, entry in verdicts.items()},
                 "verdicts": {rid: details[rid] for rid in outcomes},
                 "findings": findings},
    ))


def _downgrade_suppressed_requirement_claims(
    state: GenerationState, ctx: "AttemptContext", suppressed: List[str], fingerprint: str,
) -> None:
    """PRD-020: a verifier claim that REQ-n is missing, suppressed because it
    contradicts stronger authority (a SATISFIED deterministic obligation, or
    an identifier only the Planner named), is not evidence either way: the
    requirement is recorded UNVERIFIED rather than left VIOLATED."""
    requirements = ctx.requirement_set
    if requirements is None or ctx.obligation_ledger is None:
        return
    claimed = {text.split(":", 1)[0].strip() for text in suppressed if ":" in text}
    ids = [rid for rid in requirements.ids if rid in claimed]
    if not ids:
        return
    verdicts = {rid: (RequirementOutcome.UNVERIFIED, "missing claim contradicts stronger authority; suppressed",
                      CLAIM_CONTRADICTS_STRONGER_AUTHORITY)
                for rid in ids}
    record_requirement_verdicts(
        ctx.obligation_ledger, requirements, verdicts, revision=state.attempt_number,
        evidence_fingerprint=fingerprint, source="attempt.goal_spec_compliance.arbitration", only=ids,
    )


def _close_requirements_by_migration_gate(
    state: GenerationState, ctx: "AttemptContext", fingerprint: str,
) -> None:
    """PRD-020: an UNVERIFIED requirement that states the migration itself -
    its own text names both the migration's source and its target identity -
    is positively verified by the DETERMINISTIC migration gate once every
    current migration obligation is SATISFIED for this candidate. The
    binding is the user's words, never the verifier's claim text; a
    requirement naming only one side (e.g. a detail of how the target
    library is used) is not the migration and stays UNVERIFIED."""
    requirements, ledger = ctx.requirement_set, ctx.obligation_ledger
    if requirements is None or ledger is None or not _migration_obligations_all_satisfied(ledger):
        return
    records = ledger.current_by_kind(ObligationKind.MIGRATION_COMPLETION)

    def _terms(key: str) -> set:
        return {token for rec in records for value in [rec.evidence.get(key)] if value
                for token in re.split(r"[-_]", str(value).lower()) if len(token) >= 3}

    source_terms, target_terms = _terms("source_identity"), _terms("target_identity")
    if not source_terms or not target_terms:
        return
    outcomes = requirement_outcomes(ledger, requirements)
    for requirement in requirements.requirements:
        if outcomes.get(requirement.id) is not RequirementOutcome.UNVERIFIED:
            continue
        record = ledger.current(requirement_obligation_id(requirement.id))
        if record is None or (record.evidence or {}).get("evidence_id") != fingerprint:
            continue
        lowered = requirement.text.lower()

        def _names(terms: set, text: str = lowered) -> bool:
            return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)

        if _names(source_terms) and _names(target_terms):
            record_requirement_closure(
                ledger, requirements, requirement.id, evidence_id=fingerprint, method="migration_gate",
                detail={"migration_obligations": sorted(rec.id for rec in records)},
                source="attempt.migration_gate", revision=state.attempt_number,
            )


def _structured_plan_paths(ctx: "AttemptContext") -> set:
    """Every path an approved structured plan declares (any subtask)."""
    plan = getattr(ctx, "structured_plan", None)
    return {pf.path for st in (getattr(plan, "subtasks", None) or []) for pf in (st.planned_files or [])}


def _stage_ownership_redirect_restoration(
    state: GenerationState, ctx: "AttemptContext", staged_writes: List[StagedFileWrite],
) -> None:
    """PRD-022: after a deterministic brownfield ownership violation
    (workflow.py), the corrective retry is scoped to the grounded owner, with
    patch authority over it only. The tests the rejected candidate redirected
    and the parallel file it created are not the retry's to repair, and left
    in place they would fail the same check whatever the owner fix, making
    the retry redundant by construction. Like the RESTORE_PUBLIC_CONTRACT
    evidence restoration above, they are restored deterministically in the
    same guarded batch: each redirected test back to its exact baseline, and
    each parallel file this run created removed - unless an approved
    structured plan declares that file (the enforce terminal commit
    materializes every planned path), in which case it stays and the
    restored test alone ends the redirect. Anything the Developer
    writes again in this attempt is left as written, so a repeated choice
    still recurs and still counts toward ARCHITECTURE_CHOICE_INVALIDATED."""
    recovery = state.ownership_redirect_recovery
    if not recovery:
        return
    staged_targets = {staged.target_path for staged in staged_writes}
    restored: List[str] = []
    for test_path in recovery.get("redirected_tests", []):
        baseline = state.all_original_contents.get(test_path)
        target = os.path.join(ctx.worktree_path, test_path)
        if not baseline or target in staged_targets:
            continue
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                current = handle.read()
        except OSError:
            current = ""
        if current != baseline:
            staged_writes.append(_baseline_restoration_write(state, target, test_path))
            restored.append(test_path)
    removed: List[str] = []
    for candidate in recovery.get("abandoned_candidates", []):
        target = os.path.join(ctx.worktree_path, candidate)
        if (target in staged_targets or state.all_original_contents.get(candidate)
                or candidate in _structured_plan_paths(ctx)
                or os.path.exists(os.path.join(ctx.workspace_path, candidate)) or not os.path.isfile(target)):
            continue  # written again this attempt, or not a file this run created
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            current = handle.read()
        staged_writes.append(StagedFileWrite(
            target_path=target, content="", base_path=target,
            expected_base_revision=file_raw_digest(target), delete=True,
        ))
        removed.append(candidate)
    if restored or removed:
        state.record_event(RunEvent(
            kind="ownership.redirect_restored", attempt=state.attempt_number, source="attempt.staged_writes",
            authority=EventAuthority.AUXILIARY,
            message=f"restored redirected tests {restored}; removed abandoned parallel files {removed}",
            details={"restored_tests": restored, "removed_candidates": removed,
                     "owners": list(recovery.get("owners", []))},
        ))


async def _classify_and_escalate_contract_changes(
    state: GenerationState, ctx: "AttemptContext", violations: List[Dict[str, Any]],
    baseline_contents: Dict[str, str], candidate_contents: Dict[str, str],
    run_direct_authorizations: List[Any], authorized_changes: List[Dict[str, Any]] = (),
) -> Tuple[List[Dict[str, Any]], List[Any], Optional[str]]:
    """PRD-023: classify every public contract change the detector reported,
    then offer only the evidence-backed POTENTIALLY_DERIVED/INDETERMINATE
    ones to a human - when ``autonomy.contract_change_escalation`` is
    ``human`` and a human-in-the-loop approval callback exists. An approval
    creates revision-bound authorizations and drops exactly those changes;
    otherwise every change stays a violation (fail closed). Returns the
    remaining violations, the classifications and the escalation reason code."""
    from kriya.workflow.contract_classification import (
        CONTRACT_ESCALATION_DECLINED,
        CONTRACT_ESCALATION_UNAVAILABLE,
        ESCALATABLE,
        classify_api_violations,
        escalation_prompt,
        human_authorization,
        record_classifications,
    )

    classifications = classify_api_violations(
        violations, original_contents=baseline_contents, final_contents=candidate_contents,
        run_authorizations=run_direct_authorizations,
        human_authorizations=state.human_contract_authorizations, authorized_changes=authorized_changes,
    )
    record_classifications(ctx.obligation_ledger, classifications, revision=state.attempt_number,
                           subtask_id=ctx.current_subtask_id)
    escalatable = [c for c in classifications if c.status in ESCALATABLE]
    autonomy = ctx.kernel.config.autonomy
    if not escalatable or autonomy.contract_change_escalation != "human":
        return violations, classifications, None
    if autonomy.mode != "human-in-the-loop" or not ctx.approval_callback:
        logger.warning("Contract change needs human authorization but none can be asked - blocked: %s",
                       [c.id for c in escalatable])
        return violations, classifications, CONTRACT_ESCALATION_UNAVAILABLE
    approved = ctx.approval_callback([], escalation_prompt(escalatable))
    if asyncio.iscoroutine(approved):
        approved = await approved
    if not approved:
        return violations, classifications, CONTRACT_ESCALATION_DECLINED
    plan_revision = (getattr(ctx.structured_plan, "plan_id", None)
                     or hashlib.sha256((ctx.grounding_goal or ctx.goal).encode("utf-8")).hexdigest())
    granted = [human_authorization(c, subtask_id=ctx.current_subtask_id, plan_revision=plan_revision)
               for c in escalatable]
    state.human_contract_authorizations.extend(granted)
    for authorization in granted:
        state.record_event(RunEvent(
            kind="contract.human_authorization", attempt=state.attempt_number, source="attempt.contract_gate",
            authority=EventAuthority.AUXILIARY,
            message=f"human authorized {authorization.affected_owner}::{authorization.affected_symbol} "
                    f"({authorization.allowed_change_category.value})",
            details={"authorization_id": authorization.authorization_id,
                     "plan_revision": authorization.plan_revision,
                     "legal_scope": authorization.legal_scope,
                     "evidence": authorization.derivation_evidence},
        ))
    approved_keys = {(c.owner, c.signature) for c in escalatable}
    remaining = [v for v in violations if (v["owner"], v["removed_signature"]) not in approved_keys]
    return remaining, classifications, None


def _settled_goal_spec_requirement(
    ledger: Optional[ObligationLedger], obligation_id: Optional[str], fingerprint: str,
) -> Optional[ObligationRecord]:
    """Correctness Continuity Part A (PRV-06, 2026-08-29) - MA8 evidence
    monotonicity applied to ObligationKind.GOAL_SPEC_REQUIREMENT (defined
    since MA8, never populated until now - see docs/design.md's own
    "defined for future use" note). Returns the ledger's current record for
    this obligation only when it is SATISFIED and its own recorded evidence
    fingerprint exactly matches `fingerprint` - i.e. the SAME requirement
    text and the SAME checked-file content already satisfied it once.

    Returns None whenever there is no prior record, the prior record isn't
    SATISFIED, or the fingerprint differs by even one byte - a changed
    fingerprint is always treated as genuinely new evidence, entitled to a
    full, independent re-judgment (Part A6: unchanged evidence cannot be
    destabilized by weaker judgment; new evidence must always be free to
    invalidate). This function only ever answers "is there settled,
    unchanged evidence to protect" - it never itself skips or overrides an
    LLM call; see run_attempt's own call site for how the answer is used to
    suppress a contradictory JUDGMENT-authority verdict without ever
    letting it become failure evidence or consume a Developer retry."""
    if ledger is None or not obligation_id:
        return None
    current = ledger.current(obligation_id)
    if current is None or current.status != ObligationStatus.SATISFIED:
        return None
    if current.evidence.get("fingerprint") != fingerprint:
        return None
    return current


def _restore_api_contract_owners_deterministically(
    state: GenerationState, contract: APIContractRecovery,
) -> List[Dict[str, str]]:
    """Control-plane audit (2026-08-30): RESTORE_PUBLIC_CONTRACT's own real
    objective - "restore these exact signatures, do not solve the
    behavioral issue yet" - is a FACT Kriya already has, not a generation
    task. `state.all_original_contents[owner]` is populated at violation-
    detection time (run_attempt's own "BEFORE WRITE" early-violation
    branch), before any byte of the offending candidate that triggered
    recovery ever reached the sandbox - it is the exact, authoritative
    pre-mutation content for every owner file this phase exists to restore.

    Live incident this closes (PRV-11, 2026-08-30): asking the Developer to
    reproduce this already-known content is not merely redundant, it is
    unreliable - a real run burned all 3 RESTORE_PUBLIC_CONTRACT attempts
    with the model repeatedly re-adding the very field whose presence broke
    the signature in the first place, despite a prompt that never once
    mentioned that field. This mirrors an ALREADY-EXISTING precedent one
    phase over: protected_evidence_files (damaged callers/tests) are
    already restored deterministically, never via the Developer, a few
    hundred lines below in run_attempt's own staged-write section - this
    closes the one remaining asymmetric case (the owner file itself).

    Returns the SAME [{"filepath": ..., "content": ...}, ...] shape
    _run_developer_generation would have returned, so every downstream
    consumer (the staged-write commit, find_unrestored_public_api_contracts,
    the RESTORE_PUBLIC_CONTRACT -> REPAIR_BEHAVIOR transition) is completely
    unaware this attempt never called the Developer at all - none of that
    machinery needed to change.

    Fails closed, not silently: a missing baseline for a real violating
    owner is a genuine internal-state defect (that owner's content should
    ALWAYS have been captured at violation-detection time) - raising here,
    rather than skipping that owner or silently falling back to asking the
    Developer anyway, surfaces the bug instead of masking it."""
    files: List[Dict[str, str]] = []
    for owner in contract.owner_files:
        baseline = state.all_original_contents.get(owner)
        if baseline is None:
            raise IncompleteGenerationError(
                [owner],
                f"API_CONTRACT_RECOVERY: no captured baseline content for owner "
                f"{owner!r} - cannot restore deterministically without it.",
            )
        files.append({"filepath": owner, "content": baseline})
    return files


def _verified_no_change_proposal(
    state: GenerationState, ctx: "AttemptContext", files: List[Dict[str, Any]], missing_files: List[str],
) -> List[str]:
    """ENFORCE-VERIFIED-NO-CHANGE-001 / ENFORCE-PARTIAL-NO-CHANGE-001 (OD-3)
    / ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001 (BACKEND-FINAL-
    CLOSURE-005): the planned paths of an enforce unit the Developer left
    untouched - a NO CHANGE answer or a byte-identical rewrite
    (ENFORCE-IDENTICAL-WRITE-COMPLETION-001, state.identical_rewrites) - when
    they explain exactly the expected files never written, else []. Only an
    existing file this unit never wrote can be untouched (a planned file still
    missing is a missing artifact; a file the unit wrote and now calls
    unchanged is not an assessment of the base); a protocol-error entry is a
    rejected response, never an assessment.

    What the untouched paths MEAN is the caller's decision, from the unit's
    effective mutation (state.all_files_written): a unit that changed nothing
    proposes them as a no-change the final attempt's deterministic gate
    evidence must verify (kriya/workflow/verified_no_change.py); a unit that
    changed another file delivers them unchanged and is judged like every
    other mutating unit. The answer is never success by itself."""
    if not missing_files or ctx.structured_plan is None or not ctx.current_subtask_id:
        return []
    subtask = ctx.structured_plan.subtask_by_id(ctx.current_subtask_id)
    planned_paths = {pf.path for pf in (subtask.planned_files if subtask is not None else [])}
    # Review F3: a restored unplanned path is never an untouched planned file.
    identical_proposals = set(state.identical_rewrites) & planned_paths
    no_change = sorted({
        f["filepath"] for f in files
        if f.get("filepath") and not f.get("protocol_error")
        and classify_result_operation(f) is CodeOperation.NO_CHANGE_ASSESSMENT
    } | identical_proposals)
    if not no_change or any(path in state.all_files_written for path in no_change):
        return []
    if not all(os.path.isfile(os.path.join(ctx.worktree_path, path)) for path in no_change):
        return []
    explained = {path for path in no_change if os.path.basename(path) in set(missing_files)}
    if not set(missing_files) <= {os.path.basename(path) for path in explained}:
        return []
    return sorted(explained)


def _attempt_evidence_boundary(func):
    """LR-R1-M1: run one attempt inside its attempt-evidence scope
    (observational). The attempt's number is read from
    ``state.attempt_number`` at each record - it is incremented first thing
    inside the attempt, never predicted here."""
    @functools.wraps(func)
    async def wrapper(state: GenerationState, ctx: AttemptContext) -> None:
        with attempt_evidence_scope.attempt_scope(lambda: state.attempt_number) as closing:
            try:
                await func(state, ctx)
                # run_attempt returned: generation and the candidate gates
                # completed. Whether the attempt as a whole succeeded is the
                # retry loop's attempt.concluded (later checks run there).
                closing.update(outcome="RETURNED", candidate_gates_passed=state.candidate_gates_succeeded)
            finally:
                _record_obligations_snapshot(ctx)
    return wrapper


def _record_obligations_snapshot(ctx: AttemptContext) -> None:
    """LR-R1-M1 ``obligations.snapshot`` at attempt close: the ledger's
    revision and digest and the unresolved terminal obligations by kind."""
    ledger = getattr(ctx, "obligation_ledger", None)
    if ledger is None or attempt_evidence_scope.capture_mode() is None:
        return
    try:
        revision, digest = ledger.fingerprint()
        unresolved: Dict[str, List[str]] = {}
        for record in ledger.unresolved_terminal_obligations():
            unresolved.setdefault(getattr(record.kind, "value", str(record.kind)), []).append(record.id)
        payload = {"ledger_revision": revision, "ledger_digest": digest,
                   "unresolved_terminal": {kind: sorted(ids) for kind, ids in sorted(unresolved.items())}}
    except Exception as error:  # observational: never alters the attempt
        logger.warning("Attempt evidence: obligations.snapshot not built (%s)", error)
        return
    attempt_evidence_scope.emit("obligations.snapshot", payload)


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
        )
        resolution = dict(zip(paths_to_resolve, artifact_resolution.resolved, strict=True))  # one per path
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

    # Enforce the selected response contract before attribution heuristics or
    # writes.  Classification is based on the target's real existence, so the
    # same full-content shape is CREATE for an absent file and REPAIR for an
    # existing one.  Repair fallbacks are explicit and observable; creation has
    # no permissive fallback that could turn a missing file into a silent no-op.
    for file_obj in files:
        filepath = file_obj["filepath"]
        file_exists = _target_exists(ctx, filepath)
        expected_operation, mandatory_patch = _completeness_gated_operation(
            filepath,
            operation_for_file(attempt_operation, file_exists=file_exists),
            file_exists=file_exists, ctx=ctx, state=state,
        )
        actual_operation, contract_error = validate_operation_result(
            file_obj,
            expected=expected_operation,
            file_exists=file_exists,
        )
        if contract_error:
            failure = Failure(
                type="operation_contract",
                message=(
                    f"OPERATION CONTRACT FAILURE in {filepath}: {contract_error}. "
                    f"Return exactly the {expected_operation.value} response shape requested."
                ),
                raw_output=contract_error,
                file_locations=[FileLocation(filepath=filepath)],
                likely_files=[filepath],
                attempted_edits=file_obj.get("edits") or [],
                # Typed only when the producer named the violation (e.g. a `[]`
                # file-list answer given as file content). Deliberately NOT in
                # _DETERMINISTIC_VERDICT_REASON_CODES: it is model output, and a
                # resampled retry can genuinely return real content.
                diagnostics=(
                    {"reason_code": file_obj["protocol_reason_code"]}
                    if file_obj.get("protocol_reason_code") else None
                ),
                attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        # VAL-001 G1 D1-A (2026-09-18): independent of `mandatory_patch`
        # above (which only ever reflects the REQUESTED operation - see
        # _validate_actual_mutation_authority()'s own docstring for the
        # full G1-R2 incident this closes) - this checks the ACTUAL
        # returned shape, unconditionally, for every mode. validate_
        # operation_result()'s own PATCH -> FULL_FILE transition is
        # intentionally permissive at THAT layer (an ordinary targeted
        # retry may legitimately decide a small patch isn't enough) - this
        # is the layer that must never be permissive about a full-file
        # response reaching the sandbox without real authority for it,
        # PRE-WRITE (this whole loop runs strictly before the batch commit
        # further down in this function - see AuthorizedFileWriter.
        # commit_batch()'s own call site).
        authority_rejection_reason = (
            None if file_obj.get("_kriya_carried_forward_content")
            else _validate_actual_mutation_authority(
                filepath, actual_operation, file_exists=file_exists, ctx=ctx, state=state,
            )
        )
        if authority_rejection_reason is not None:
            state.record_event(RunEvent(
                kind="operation_authority.rejected",
                attempt=state.attempt_number,
                source="attempt.run_attempt",
                authority=EventAuthority.AUTHORITATIVE,
                operation=actual_operation.value,
                message=f"{filepath}: whole-file authority rejected - {authority_rejection_reason}",
                details={
                    "filepath": filepath,
                    "requested_operation": expected_operation.value,
                    "actual_operation": actual_operation.value,
                    "mandatory_patch_from_request": mandatory_patch,
                    "known_context_tier": (
                        state.known_target_context_items[filepath].tier
                        if filepath in state.known_target_context_items else None
                    ),
                    "known_context_is_exact": (
                        state.known_target_context_items[filepath].is_exact
                        if filepath in state.known_target_context_items else None
                    ),
                    "known_context_member_id": (
                        state.known_target_context_items[filepath].member_id
                        if filepath in state.known_target_context_items else None
                    ),
                    "known_context_revision": (
                        state.known_target_context_items[filepath].revision
                        if filepath in state.known_target_context_items else None
                    ),
                },
            ))
            failure = Failure(
                type="operation_contract",
                message=f"OPERATION CONTRACT FAILURE in {filepath}: {authority_rejection_reason}",
                raw_output=f"actual returned operation {actual_operation.value} lacks whole-file authority",
                file_locations=[FileLocation(filepath=filepath)],
                likely_files=[filepath],
                attempted_edits=file_obj.get("edits") or [],
                diagnostics={"reason_code": "ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"},
                attempt=state.attempt_number,
            )
            _record_edit_protocol_failure(state, filepath, failure.type)
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        if actual_operation in (CodeOperation.CREATE_FULL_FILE, CodeOperation.REPAIR_WITH_FULL_FILE):
            state.edit_failure_capability.pop(filepath, None)  # an authorized whole-file edit was accepted
        # VAL-001 G1 D1-B: only reached once this file's actual mutation
        # shape has cleared authority - see this cache's own recording-site
        # comment above for why an unconditional/earlier recording would
        # reopen required authority case #6. A targeted/anchored-edit
        # response's own entries carry "edits", not "content" (resolved to
        # full content later by the anchored-edit pipeline further down) -
        # skipped here, not an error, exactly as the original P9-R1
        # recording did.
        _candidate_content = file_obj.get("content")
        if _candidate_content is not None:
            state.last_candidate_contents[filepath] = _candidate_content
        if actual_operation is not expected_operation:
            state.record_event(RunEvent(
                kind="operation.fallback",
                attempt=state.attempt_number,
                source="developer",
                authority=EventAuthority.ADVISORY,
                operation=actual_operation.value,
                message=(
                    f"{filepath}: accepted safe fallback from "
                    f"{expected_operation.value} to {actual_operation.value}."
                ),
                details={
                    "filepath": filepath,
                    "requested": expected_operation.value,
                    "returned": actual_operation.value,
                },
            ))

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

    # Read original file contents before overwriting (crucial for fallback mode diffs)
    for file_obj in files:
        filepath = file_obj.get("filepath", "")
        if not filepath:
            continue
        _capture_original(state, ctx, filepath)

    # Write files to worktree sandbox
    state.files_written = []
    staged_writes: List[StagedFileWrite] = []
    # Built once per attempt, not once per file - see _build_workspace_type_index's
    # own docstring. Updated in place below as each new file is accepted, so two
    # files in the SAME batch that collide with each other are caught too.
    workspace_type_index = _build_workspace_type_index(state, ctx)
    # PRV-17 (2026-09-03): normalized ONCE per attempt, not per file - the
    # plan-declared allowlist doesn't change mid-attempt. Canonicalizes
    # trailing-slash/'./' formatting so 'customers_project/' (as the Planner
    # wrote it) and 'customers_project' (as the Developer reported it) are
    # recognized as the same target - see normalize_workspace_relpath's own
    # docstring for the live incident this closes.
    normalized_allowed_write_relpaths = {
        normalize_workspace_relpath(p) for p in ctx.allowed_write_relpaths
    }
    for file_obj in files:
        filepath = file_obj.get("filepath", "")
        content = file_obj.get("content", "")
        edits = file_obj.get("edits", [])
        analysis = file_obj.get("analysis")

        if not filepath:
            continue

        if (
            ctx.write_scope_mode == WriteScopeMode.ALLOWLIST
            and ctx.allowed_write_relpaths
            and normalize_workspace_relpath(filepath) not in normalized_allowed_write_relpaths
        ):
            # Correctness Continuity Part B4 (PRV-06, 2026-08-29): rejected
            # HERE, before apply_anchored_edits or any write is attempted -
            # distinct from, and in addition to, AuthorizedFileWriter's own
            # final write-scope enforcement at commit time later in this
            # pipeline (both layers remain; see WriteScopeMode's own
            # docstring for that one). This layer exists so an unauthorized
            # target never even gets a wasted anchored-edit attempt against
            # it. Live incident this closes: an owner-recovery attempt for
            # s2 (authorized scope = App.java only) also generated a SEARCH/
            # REPLACE for InMemoryService.java (owned by a different
            # subtask, s3) - apply_anchored_edits() was invoked on it anyway
            # and failed the whole retry attempt on content that was never
            # legally writable by this attempt in the first place.
            #
            # Raises the SAME PolicyDeniedError/reason_code AuthorizedFile
            # Writer.authorize() would eventually produce for this exact
            # target, rather than silently dropping the file_obj and letting
            # the REST of the batch proceed - MA9's own atomic-rejection
            # invariant ("an unauthorized coordinated participant denies the
            # WHOLE batch") must hold here too. Found and fixed during this
            # same change's own regression sweep: an earlier draft silently
            # `continue`d past the unauthorized file, which let an
            # OTHERWISE-authorized sibling in the same batch (e.g. App.java)
            # commit anyway - exactly the bypass MA9's own tests
            # (test_run_attempt_coordinated_repair_denies_unauthorized_
            # participant_atomically) exist to catch. Nothing in this
            # attempt's `staged_writes` has reached disk yet at this point
            # (that only happens in the later, single atomic commit step),
            # so raising here still leaves every file at its pre-attempt
            # baseline, matching the final write-scope gate's own contract
            # exactly.
            state.rejected_generation_targets.append(filepath)
            full_path = os.path.join(ctx.worktree_path, filepath)
            logger.warning(
                "RECOVERY_GENERATION_TARGET_REJECTED filepath=%s reason=outside_authorized_scope "
                "allowed=%s", filepath, sorted(ctx.allowed_write_relpaths),
            )
            raise PolicyDeniedError(
                request=ActionRequest(action_type=ActionType.WRITE_FILE, target=full_path),
                result=PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason_code="FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE",
                    explanation=(
                        f"'{full_path}' is outside the validated subtask's allowed modification "
                        f"scope: {sorted(ctx.allowed_write_relpaths)!r}."
                    ),
                    matched_rule="filesystem.authorized_writer.validated_subtask_scope",
                ),
            )

        # FILE-INTEGRITY-CONTRACT-001: content and edits arrive here as
        # parsed protocol payload (kriya/agents/response_protocol.py) and are
        # never rewritten; the bytes written are the snapshot-bound result of
        # the one edit engine (kriya/workflow/file_integrity.py).
        full_path = os.path.join(ctx.worktree_path, filepath)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        if edits:
            current_file_path = os.path.join(ctx.worktree_path, filepath)
            if not os.path.exists(current_file_path):
                current_file_path = os.path.join(ctx.workspace_path, filepath)

            source_snapshot = load_snapshot(current_file_path)
            orig_text = display_text(source_snapshot.raw_bytes)
            try:
                orig_text = source_snapshot.text
                _authorize_anchors(state, filepath, edits, orig_text)
                new_content = apply_anchored_edits(orig_text, edits, active_code_context)
                new_bytes = source_snapshot.encode(new_content)
            except ValueError as anchor_ex:
                if getattr(anchor_ex, "reason_code", None) in DETERMINISTIC_FILE_INTEGRITY_STOPS:
                    raise _file_integrity_stop(state, filepath, anchor_ex) from anchor_ex
                # apply_anchored_edits() itself never receives a filepath, so its
                # raised ValueError never named one either - this failure class
                # always fell through to a blind full-set retry, unlike a compile
                # error (which self-names its file). filepath IS known right here,
                # in the caller's loop scope - capture it now instead of losing it.
                # orig_text (the real pre-edit content the SEARCH block was
                # supposed to match against) is already in memory - exactly what's
                # needed to debug an anchor mismatch, no disk re-read needed.
                # edits (the actual search/replace text that was attempted, already
                # sanitized) is captured alongside it as attempted_edits - together
                # both halves of "why didn't this match" are now persisted, not just
                # the generic "matched 0 times" message.
                #
                # Before accepting that generic explanation, check whether the
                # failed search block actually belongs to a DIFFERENT known file -
                # see find_misdirected_edit_target()'s own docstring for the real
                # incident (a-3, ignite_qpid_protocol, 2026-08-14) this closes: a
                # retry scoped to one file by a locator that could only ever name
                # the file where an explicit runtime check THREW, not the
                # different file whose method silently returned the wrong value,
                # produced a textbook-correct diagnosis whose edit could never
                # match the file it was constrained to. Reads every OTHER
                # already-written file straight from the worktree (nothing keeps
                # their content in memory this late in the per-file write loop).
                other_files: Dict[str, str] = {}
                # Unioned with ctx.established_files (see that field's own
                # docstring) - a misdirected edit could legitimately belong to
                # an established file from an earlier milestone, not just
                # another file this attempt itself wrote.
                for other_filepath in set(state.all_files_written) | set(ctx.established_files):
                    if other_filepath == filepath:
                        continue
                    other_full_path = os.path.join(ctx.worktree_path, other_filepath)
                    try:
                        with open(other_full_path, "r", encoding="utf-8", errors="replace") as fh:
                            other_files[other_filepath] = fh.read()
                    except OSError:
                        continue

                misdirected_target = find_misdirected_edit_target(edits, orig_text, other_files)
                if misdirected_target:
                    # raw_output (not just message) carries both filenames - it's
                    # what to_gate_outcome() persists as "output" (raw_output or
                    # message) and what a future post-mortem reads from traces.db,
                    # same forensics goal as failed_content/attempted_edits below.
                    misdirected_explanation = (
                        f"the search block for {filepath} matched 0 times against "
                        f"{filepath}'s content, but was found instead inside {misdirected_target}"
                    )
                    failure = Failure(
                        type="misdirected_edit",
                        message=(
                            f"MISDIRECTED EDIT: {misdirected_explanation}. The fix you "
                            f"diagnosed likely belongs in {misdirected_target}, not {filepath} - "
                            f"target {misdirected_target} in your next edit (and {filepath} too, "
                            f"only if it genuinely also needs its own companion change)."
                        ),
                        raw_output=misdirected_explanation,
                        file_locations=[
                            FileLocation(filepath=filepath),
                            FileLocation(filepath=misdirected_target),
                        ],
                        likely_files=[filepath, misdirected_target],
                        failed_content={filepath: orig_text, misdirected_target: other_files[misdirected_target]},
                        attempted_edits=edits,
                        attempt=state.attempt_number,
                    )
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure) from anchor_ex

                state.budgets.anchor_failure_counts[filepath] = (
                    state.budgets.anchor_failure_counts.get(filepath, 0) + 1
                )
                _record_edit_protocol_failure(state, filepath, "anchored_edit")
                _remember_anchor_loci(state, filepath, edits, orig_text)
                anchor_reason = str(anchor_ex).split(":", 1)[0]
                failure = Failure(
                    type="anchored_edit",
                    message=f"ANCHORED EDIT FAILURE in {filepath}: {anchor_ex}",
                    raw_output=str(anchor_ex),
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    diagnostics=({"reason_code": anchor_reason} if anchor_reason.isupper() else {}),
                    attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure) from anchor_ex

            state.budgets.anchor_failure_counts[filepath] = 0
            state.edit_failure_capability.pop(filepath, None)

            # Layer 0 pre-flight check (see find_whole_response_no_op's own
            # docstring): purely structural, no analysis text or fail_type
            # dispatch needed - unlike Layers 1/2 below, a response whose
            # every edit is a byte-identical no-op is objectively useless
            # regardless of what it claims, so this is never bypassed and
            # never counts against the Layer 2 bounded veto (a different
            # check, a different question). apply_anchored_edits() above
            # already confirmed every search block matched real content -
            # this only fires when the edit(s) that matched changed nothing.
            if find_whole_response_no_op(edits):
                preserved_locator_files = _preserved_authoritative_locator_files(
                    state, [filepath], self_diagnosed,
                )
                retry_files = list(self_diagnosed or preserved_locator_files) or [filepath]
                _request_fallback_for_rejected_authoritative_target(
                    state, ctx, preserved_locator_files,
                )
                failure = Failure(
                    type="no_op_edit",
                    message=(
                        f"NO-OP EDIT in {filepath}: every SEARCH/REPLACE pair in your response "
                        f"is byte-identical - this response changes nothing. If this file "
                        f"genuinely needs no change, use the response protocol's no-change outcome "
                        f"instead of an edit; otherwise your REPLACE text must actually differ "
                        f"from your SEARCH text."
                    ),
                    raw_output="every edit in the response was a no-op (search == replace)",
                    source="developer",
                    authority="advisory",
                    file_locations=[FileLocation(filepath=f) for f in retry_files],
                    likely_files=retry_files,
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    diagnostics=_preserved_attribution_diagnostics(preserved_locator_files),
                    attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Layer 1 pre-flight check (see find_edits_ignoring_reported_line's
            # own docstring): only meaningful when this attempt is itself
            # responding to a real prior error with a locatable line for this
            # file - error_context is "" on a clean first attempt, where
            # extract_error_source_locations() would find nothing anyway, but
            # the explicit guard avoids the line-matching work entirely there.
            #
            # Deterministic-validation-first (same principle as
            # _diagnosis_mismatch_bypass_reason, 2026-08-17): this check ONLY
            # ever fires when extract_error_source_locations() found a real
            # javac-style file:[line,col] locator for THIS file - i.e. it is
            # compile-scoped by construction, every single time, not just
            # sometimes (unlike diagnosis_mismatch, which fires for every
            # failure type). A real compile gate runs immediately after this
            # per-file write loop regardless, so it is always the cheaper,
            # authoritative answer to "does this edit actually still leave
            # the reported error in place" - no heuristic needed at all.
            # Confirmed both ways live via the corpus survey's replay of the
            # 2 DEBUG-capturable unaddressed_error_location occurrences
            # (b-10f, b-10n): b-10f was a FALSE positive - a companion edit
            # earlier in the same response fixed the bad import, which
            # transitively resolves the reported line's "cannot find symbol"
            # error too, but this check has no visibility into OTHER edits in
            # the same response and flagged the untouched (but now-harmless)
            # usage-site line anyway. b-10n was a genuine TRUE positive - the
            # model's own analysis said it would add a missing import and
            # never did - and bypassing here costs nothing there: the real
            # compile gate catches the exact same missing-import error one
            # step later, through the normal compile-failure retry path this
            # loop already handles well. Given every occurrence of this
            # check's own reported-line recurred identically across many
            # historical runs (10x/9x/6x for single line numbers - the same
            # small number of Ignite/Qpid goals hitting it over and over),
            # the false-positive cost of hard-blocking here was likely the
            # dominant driver of this failure category, not genuine misses.
            if state.error_context:
                ignored_lines = find_edits_ignoring_reported_line(
                    orig_text, edits, filepath, state.error_context
                )
                if ignored_lines:
                    lines_desc = ", ".join(str(n) for n in sorted(ignored_lines))
                    logger.info(
                        f"UNADDRESSED ERROR LOCATION pre-flight check bypassed for {filepath} "
                        f"(line(s) {lines_desc} of the reported error left unchanged): deferring "
                        f"to the real compiler instead of this heuristic, which cannot see whether "
                        f"a companion edit elsewhere in the same response already resolved it."
                    )

            # Structural corruption pre-flight check (see
            # find_structural_corruption's own docstring) - a cheap,
            # deterministic tripwire for the "obviously broken" shape
            # BOTH real corruption incidents this session actually had
            # (unbalanced braces from a folded-in duplicate class),
            # before the expensive compile gate spends itself
            # discovering the same thing.
            structural_problem = find_structural_corruption(filepath, new_content)
            if structural_problem:
                failure = Failure(
                    type="structural_corruption",
                    message=(
                        f"STRUCTURAL CORRUPTION in {filepath}: {structural_problem} "
                        f"This usually means an edit's replace text accidentally folded in "
                        f"extra, unrelated content (e.g. a redundant full-file dump appended "
                        f"after the intended change). Re-check your SEARCH/REPLACE blocks - "
                        f"the replace text for each pair should contain ONLY the corrected "
                        f"version of that pair's search text, nothing else."
                    ),
                    raw_output=structural_problem,
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Layer 2 pre-flight check (see find_edits_ignoring_own_diagnosis's own
            # docstring): the Layer 1 check above only catches an edit that left the
            # COMPILER's own reported line unchanged - it deliberately allows a fix
            # at a different, legitimate line (e.g. a declaration above the reported
            # use site). This catches the gap that leaves open: does the edit's own
            # content actually implement what its own FIX ANALYSIS just said, at ANY
            # line - found live, 2026-08-13, a real compile error recurred verbatim
            # across 3 targeted retries despite a textbook-correct analysis every time.
            diagnosis_mismatch = find_edits_ignoring_own_diagnosis(analysis, edits, None, orig_text)
            if diagnosis_mismatch:
                bypass_reason = _diagnosis_mismatch_bypass_reason(state, ctx, filepath, new_content)
                if bypass_reason:
                    logger.info(
                        f"DIAGNOSIS MISMATCH pre-flight check bypassed for {filepath}: {bypass_reason}."
                    )
                else:
                    # Counted here, at the actual rejection - see the bounded-
                    # veto policy's own comment in _diagnosis_mismatch_bypass_
                    # reason for why this exists.
                    state.budgets.diagnosis_mismatch_veto_counts[filepath] = (
                        state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) + 1
                    )
                    failure = Failure(
                        type="diagnosis_mismatch",
                        message=(
                            f"DIAGNOSIS MISMATCH in {filepath}: {diagnosis_mismatch}. "
                            f"Make the exact change your own analysis described - not a "
                            f"different or partial change."
                        ),
                        raw_output=diagnosis_mismatch,
                        file_locations=[FileLocation(filepath=filepath)],
                        likely_files=[filepath],
                        failed_content={filepath: orig_text},
                        attempted_edits=edits,
                        attempt=state.attempt_number,
                    )
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
            else:
                # This file's edit looked consistent with its own analysis -
                # a clean slate, so an unrelated mismatch found later in the
                # run for this same file still gets its own bounded veto.
                state.budgets.diagnosis_mismatch_veto_counts.pop(filepath, None)

            _reject_explanatory_prose(state, filepath, new_content, orig_text)
            _reject_exception_masking(state, filepath, new_content, orig_text)
            staged_writes.append(StagedFileWrite(
                target_path=full_path,
                content=new_content,
                base_path=current_file_path,
                expected_base_revision=source_snapshot.raw_sha256,
                content_bytes=new_bytes,
                mode=source_snapshot.file_mode,
            ))
        else:
            if content is None:
                continue

            current_file_path = os.path.join(ctx.worktree_path, filepath)
            if not os.path.exists(current_file_path):
                workspace_file_path = os.path.join(ctx.workspace_path, filepath)
                if os.path.exists(workspace_file_path):
                    current_file_path = workspace_file_path
            # FILE-INTEGRITY-CONTRACT-001: a whole-file replacement of an
            # existing file keeps its BOM, line-ending convention and final
            # newline state, and is refused for a file the edit engine cannot
            # represent exactly (never re-encoded from a lossy view).
            source_snapshot = load_snapshot(current_file_path)
            file_is_new = not source_snapshot.exists
            prior_content = display_text(source_snapshot.raw_bytes)
            if not file_is_new:
                try:
                    source_snapshot.require_mutable()
                except FileIntegrityError as integrity_ex:
                    raise _file_integrity_stop(state, filepath, integrity_ex) from integrity_ex
                content = keep_final_newline_state(source_snapshot, content)
            try:
                content_bytes = source_snapshot.encode(content)
            except FileIntegrityError as integrity_ex:
                failure = Failure(
                    type="file_integrity", message=f"FILE INTEGRITY FAILURE in {filepath}: {integrity_ex}",
                    raw_output=str(integrity_ex), file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath], diagnostics={"reason_code": integrity_ex.reason_code},
                    attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure) from integrity_ex

            structural_problem = find_structural_corruption(filepath, content)
            if structural_problem:
                failure = Failure(
                    type="structural_corruption",
                    message=f"STRUCTURAL CORRUPTION in {filepath}: {structural_problem}",
                    raw_output=structural_problem,
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: content},
                    attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Cross-file duplicate-type pre-flight check - only for a
            # genuinely NEW file (a REPAIR of a file that already legitimately
            # owns `filepath` is never a conflict, even if it re-declares its
            # own class). See find_cross_file_type_conflict's own docstring
            # for the live incident this prevents.
            candidate_type_names: List[str] = []
            if file_is_new and workspace_type_index:
                candidate_type_names = _extract_class_names_best_effort(
                    _dependency_graph_db_path(ctx), filepath, content,
                )
            if candidate_type_names:
                conflict = find_cross_file_type_conflict(filepath, candidate_type_names, workspace_type_index)
                if conflict:
                    # conflict[0] is an "ext:name" key (see extract_class_names'
                    # own docstring for why the extension is folded in) - strip
                    # it back to a plain class name for the human-facing message.
                    type_name = conflict[0].split(":", 1)[-1]
                    other_paths = conflict[1]
                    conflict_content: Dict[str, str] = {filepath: content}
                    for other_path in other_paths:
                        for base in (ctx.worktree_path, ctx.workspace_path):
                            other_full = os.path.join(base, other_path)
                            if os.path.exists(other_full):
                                with open(other_full, "r", encoding="utf-8", errors="replace") as fh:
                                    conflict_content[other_path] = fh.read()
                                break
                    failure = Failure(
                        type="duplicate_type_across_files",
                        message=(
                            f"DUPLICATE TYPE in {filepath}: '{type_name}' is already declared in "
                            f"{', '.join(other_paths)} (shown below). This is almost always a sign "
                            f"the existing file was never found or edited - target "
                            f"{other_paths[0]} directly instead of creating a new file. Only if "
                            f"'{type_name}' in {filepath} is genuinely a different, deliberately "
                            f"separate concept (rare) should you instead rename it to something "
                            f"unambiguous."
                        ),
                        raw_output=f"'{type_name}' declared in both {filepath} and {', '.join(other_paths)}",
                        file_locations=[FileLocation(filepath=filepath)] + [FileLocation(filepath=p) for p in other_paths],
                        likely_files=[filepath] + other_paths,
                        failed_content=conflict_content,
                        attempt=state.attempt_number,
                    )
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                for name in candidate_type_names:
                    paths = workspace_type_index.setdefault(name, [])
                    if filepath not in paths:
                        paths.append(filepath)

            # Layer 2 pre-flight check - same rationale as the anchored-edit branch
            # above, applied to a full-file regeneration instead. Only reads the
            # file's current on-disk content when there's actually an analysis to
            # check against, to avoid the extra I/O on the common (no prior error)
            # case.
            if analysis:
                diagnosis_mismatch = find_edits_ignoring_own_diagnosis(analysis, None, content, prior_content)
                if diagnosis_mismatch:
                    bypass_reason = _diagnosis_mismatch_bypass_reason(state, ctx, filepath, content)
                    if bypass_reason:
                        logger.info(
                            f"DIAGNOSIS MISMATCH pre-flight check bypassed for {filepath}: {bypass_reason}."
                        )
                    else:
                        state.budgets.diagnosis_mismatch_veto_counts[filepath] = (
                            state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) + 1
                        )
                        failure = Failure(
                            type="diagnosis_mismatch",
                            message=(
                                f"DIAGNOSIS MISMATCH in {filepath}: {diagnosis_mismatch}. "
                                f"Make the exact change your own analysis described - not a "
                                f"different or partial change."
                            ),
                            raw_output=diagnosis_mismatch,
                            file_locations=[FileLocation(filepath=filepath)],
                            likely_files=[filepath],
                            failed_content={filepath: prior_content},
                            attempt=state.attempt_number,
                        )
                        state.record_gate_outcome(failure.to_gate_outcome())
                        raise QualityGateFailure(failure)
                else:
                    state.budgets.diagnosis_mismatch_veto_counts.pop(filepath, None)

            # A new file's prior content is empty: all of it is the candidate's.
            _reject_explanatory_prose(state, filepath, content, prior_content)
            _reject_exception_masking(state, filepath, content, prior_content)
            staged_writes.append(StagedFileWrite(
                target_path=full_path,
                content=content,
                base_path=current_file_path,
                expected_base_revision=source_snapshot.raw_sha256,
                content_bytes=content_bytes,
                mode=source_snapshot.file_mode,
            ))

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

    # Nothing reaches the sandbox until every candidate has passed its cheap
    # deterministic checks.  The batch commit re-checks all source revisions
    # before the first write and rolls back already-written targets if an OS
    # error or last-moment revision conflict interrupts the commit.
    # MA4.16 - AuthorizedFileWriter really enforces (raises PolicyDeniedError,
    # not audit-only) workspace containment + a narrow sensitive-path check
    # BEFORE any write in the batch, using the real ctx.worktree_path this
    # call site has always had in scope - propagates uncaught, same as
    # FileRevisionConflict/BatchCommitError already do from this call.
    AuthorizedFileWriter(
        ctx.worktree_path,
        protected_relpaths=(ctx.protected_relpath,) if ctx.protected_relpath else (),
        allowed_relpaths=ctx.allowed_write_relpaths,
        write_scope_mode=ctx.write_scope_mode,
    ).commit_batch(staged_writes)
    for staged in staged_writes:
        state.candidate_digests[os.path.relpath(staged.target_path, ctx.worktree_path)] = (
            None if staged.delete else raw_digest(
                staged.content_bytes if staged.content_bytes is not None else staged.content.encode("utf-8"))
        )
    changed_files = [
        os.path.relpath(staged.target_path, ctx.worktree_path)
        for staged in staged_writes
    ]
    invalidated = invalidate_validated_revisions(
        state.validated_file_revisions,
        changed_files,
        ctx.generation_dependencies,
    )
    if invalidated:
        state.record_event(RunEvent(
            kind="validation.invalidated",
            attempt=state.attempt_number,
            source="workflow",
            authority=EventAuthority.ADVISORY,
            message=(
                "Candidate changes invalidated compiled revisions for: "
                + ", ".join(invalidated)
            ),
            details={"changed_files": changed_files, "invalidated_files": invalidated},
        ))
    for staged in staged_writes:
        filepath = os.path.relpath(staged.target_path, ctx.worktree_path)
        if staged.delete:
            # PRD-022: an abandoned run-created parallel file is gone from
            # the candidate, so no later gate reads it.
            state.all_files_written.discard(filepath)
            state.identical_rewrites.discard(filepath)
            logger.info(f"Removed abandoned candidate from sandbox: {filepath}")
            continue
        staged_bytes = staged.content_bytes if staged.content_bytes is not None else staged.content.encode("utf-8")
        if staged_bytes == state.all_original_raw.get(filepath):
            # ENFORCE-IDENTICAL-WRITE-COMPLETION-001 (BACKEND-FINAL-CLOSURE-005
            # cohort 2, C2-S4_B): the bytes equal the captured baseline bytes
            # (an identical rewrite, or this attempt restoring an earlier
            # attempt's change) - not a change. The path is not written for
            # this unit: nothing to apply, no gate reads it as the candidate's
            # own, and a planned one is settled by the verified no-change
            # contract from deterministic evidence (never by the write).
            state.all_files_written.discard(filepath)
            state.identical_rewrites.add(filepath)
            logger.info(f"Candidate for {filepath} is byte-identical to the baseline: not a change.")
            continue
        state.identical_rewrites.discard(filepath)
        state.files_written.append(filepath)
        state.all_files_written.add(filepath)
        logger.info(f"Committed generated/edited candidate to sandbox: {filepath}")

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

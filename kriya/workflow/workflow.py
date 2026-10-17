import asyncio
import dataclasses
import hashlib
import json
import logging
import os
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.agents.agent import (
    ArchitectAgent,
    DeveloperAgent,
    MilestonePlannerAgent,
    PlannerAgent,
    ReviewerAgent,
    RunVerifierAgent,
    SkillGapAgent,
    SpecComplianceAgent,
)
from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.code_intel.model import CALLABLE_KINDS

# `name as name` marks an explicit re-export: the helper moved to its own module
# during modularization, and callers (tests, review_context.py, spikes) still
# import it from here. Ruff keeps these; remove one only with its last caller.
from kriya.config.environment import expand_user
from kriya.control.launch_approval import REVIEW_SECTION_HEADER
from kriya.control.persistence import UnreadableRunRecordError, load_run_record
from kriya.control.run_coordinator import (
    annotate_run,
    coordinated_mutation,
    mark_run_stage,
    owning_run_work_unit,
)
from kriya.control.run_record import RunLifecycle
from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.core.kernel import Kernel
from kriya.core.llm import InferenceDeadlineError, LLMClient
from kriya.core.model_routing import resume_routes_from
from kriya.core.state_paths import trace_db_path
from kriya.core.token_budget import ContextBudgetUnsatisfiableError
from kriya.platform.filesystem_semantics import PathRelation, path_relation
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.filesystem import WriteScopeMode
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.policy.telemetry import build_decision_record
from kriya.static_analysis.service import (
    StaticAnalysisCandidate,
    StaticAnalysisService,
    banner,
    commit_guard,
    static_analysis_result_fields,
)
from kriya.tools import test_execution
from kriya.tools.validate import PolymorphicValidator, execution_evidence
from kriya.workflow import requirement_closers as closers
from kriya.workflow.acceptance import goal_requires_runtime_behavior, output_confirms_nonzero_test_execution
from kriya.workflow.acceptance_approval import bound_approval
from kriya.workflow.acceptance_oracle import bound_acceptance
from kriya.workflow.architectural_choice import (
    architecture_choice_invalidated_message,
    classify_ownership_violations,
)
from kriya.workflow.attempt import (
    AttemptContext,
    _stop_on_environment_gate_result,
    assert_candidate_unchanged_since_source_review,
    run_attempt,
)
from kriya.workflow.attribution import (
    FutureOwnerVerificationDeferral,
    resolve_future_owner_verification_deferral,
)
from kriya.workflow.attribution import (
    _detect_missing_build_manifest as _detect_missing_build_manifest,
)
from kriya.workflow.attribution import (
    find_edits_ignoring_own_diagnosis as find_edits_ignoring_own_diagnosis,
)
from kriya.workflow.attribution import (
    find_edits_ignoring_reported_line as find_edits_ignoring_reported_line,
)
from kriya.workflow.attribution import (
    find_misdirected_edit_target as find_misdirected_edit_target,
)
from kriya.workflow.attribution import (
    find_whole_response_no_op as find_whole_response_no_op,
)
from kriya.workflow.banners import log_quality_gate_banner
from kriya.workflow.best_of_n import BestOfNFailureRecorded
from kriya.workflow.checkpoint import (
    ResumeAction,
    ResumeStatus,
    compute_config_fingerprint,
    compute_workspace_content_hash,
    compute_workspace_fingerprint,
    delete_checkpoint,
    find_latest_checkpoint,
    load_checkpoint,
    new_run_id,
    save_checkpoint,
    validate_resume_against_reality,
)
from kriya.workflow.compile_regression import attribute_compile_regression
from kriya.workflow.context_budget import (
    CandidatePrompts,
    RetrievalLimits,
    _reserve_graph_context_budget,
    agent_request_capacity,
    allocation_window,
    candidate_model,
    fit_planner_request,
    retrieval_limits_for,
    review_requests,
)
from kriya.workflow.context_budget import (
    _reserve_sibling_content_budget as _reserve_sibling_content_budget,
)
from kriya.workflow.context_budget import (
    build_code_context as build_code_context,
)
from kriya.workflow.context_budget import (
    estimate_tokens as estimate_tokens,
)
from kriya.workflow.context_budget import (
    skeletonize_braced_code as skeletonize_braced_code,
)
from kriya.workflow.context_budget import (
    skeletonize_code as skeletonize_code,
)
from kriya.workflow.context_budget import (
    skeletonize_python as skeletonize_python,
)
from kriya.workflow.contract_authority import derive_direct_contract_authorizations
from kriya.workflow.contract_lifecycle import derive_contract_transition
from kriya.workflow.control_context import WorkflowControlContext
from kriya.workflow.deterministic_failure_diagnostic import DeterministicFailureDiagnosticStore
from kriya.workflow.edit_safety import (
    apply_anchored_edits as apply_anchored_edits,
)
from kriya.workflow.edit_safety import (
    atomic_write_file as atomic_write_file,  # E4: the abort path moved to stage_apply; tests import it from here
)
from kriya.workflow.edit_safety import (
    commit_revision_grounded_file,
    content_revision,
    read_file_revision,
)
from kriya.workflow.edit_safety import (
    find_structural_corruption as find_structural_corruption,
)
from kriya.workflow.egress_authority import egress_authority_details
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.failure_grounding import (
    _build_error_source_context as _build_error_source_context,
)
from kriya.workflow.failure_grounding import (
    _build_quality_gate_failure,
)
from kriya.workflow.failure_grounding import (
    _normalize_error_for_repeat_detection as _normalize_error_for_repeat_detection,
)
from kriya.workflow.failure_grounding import (
    _resolve_file_locations as _resolve_file_locations,
)
from kriya.workflow.failure_grounding import (
    classify_environment_failure as classify_environment_failure,
)
from kriya.workflow.failure_grounding import (
    extract_error_search_terms as extract_error_search_terms,
)
from kriya.workflow.failure_grounding import (
    extract_error_source_locations as extract_error_source_locations,
)
from kriya.workflow.failure_grounding import (
    extract_implicated_files as extract_implicated_files,
)
from kriya.workflow.file_integrity import (
    display_text,
    raw_digest,
)
from kriya.workflow.file_resolution import (
    IncompleteGenerationError as IncompleteGenerationError,
)

# E3: _resolve_file_paths_from_design, classify_plan_completeness, extract_expected_files and _java_toolchain_fact
# moved with the design stages; re-exported here because tests import them from this module.
from kriya.workflow.file_resolution import (
    _resolve_file_paths_from_design as _resolve_file_paths_from_design,
)
from kriya.workflow.file_resolution import (
    _resolve_maven_main_class as _resolve_maven_main_class,
)
from kriya.workflow.file_resolution import (
    _resolve_run_command as _resolve_run_command,
)
from kriya.workflow.file_resolution import (
    check_plan_completeness as check_plan_completeness,
)
from kriya.workflow.file_resolution import (
    classify_plan_completeness as classify_plan_completeness,
)
from kriya.workflow.file_resolution import (
    downgrade_ungrounded_goal_explicit_commands as downgrade_ungrounded_goal_explicit_commands,
)
from kriya.workflow.file_resolution import (
    extract_expected_files as extract_expected_files,
)
from kriya.workflow.file_resolution import (
    find_brownfield_public_api_changes,
    find_brownfield_test_redirections,
    include_response_construction_owners,
)
from kriya.workflow.file_resolution import (
    find_missing_expected_files as find_missing_expected_files,
)
from kriya.workflow.file_resolution import (
    normalize_written_filepath as normalize_written_filepath,
)
from kriya.workflow.goal_facts import developer_goal_facts
from kriya.workflow.graph_retrieval import retrieve_graph_context
from kriya.workflow.live_lookup import (
    _augment_error_with_live_lookup as _augment_error_with_live_lookup,
)
from kriya.workflow.localization_decision import decide as decide_localization
from kriya.workflow.lsp_integration import (
    _build_lsp_diagnostics_context as _build_lsp_diagnostics_context,
)
from kriya.workflow.lsp_integration import (
    _get_or_start_jdtls_client as _get_or_start_jdtls_client,
)
from kriya.workflow.migration import MigrationResolution, resolve_migration_resolution
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.outcome import (
    WORKSPACE_COMMIT_NOT_COMPLETED,
    completed_result,
    failure_report_dicts,
    terminal_failure_category,
)
from kriya.workflow.ownership_findings import (
    find_ownership_findings,
    findings_prompt_block,
    grounded_owner_candidates,
    owner_candidates_prompt_block,
    ownership_review_evidence,
    parse_ownership_justifications,
    record_findings,
    settle_findings,
)
from kriya.workflow.plan_executor import WorkUnitInvocation
from kriya.workflow.plan_schema import BUILTIN_QUALITY_GATE_VERIFIERS, EngineeringPlan
from kriya.workflow.planner_repair import (
    STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS,
    build_structured_plan_repair_prompt,
    classify_structured_plan_parse_issue,
    planner_outcome_for_completeness,
    planner_response_model,
    record_planner_outcome,
)
from kriya.workflow.pytest_stability import (
    classify_with_baseline_stability,
    record_regression_decision,
)
from kriya.workflow.pytest_stability import decision_summary as pytest_decision_summary

# E4: the closers that need no facade collaborator, re-exported here (kriya/, tests and the remaining contract
# compilation import them from this module); the others are forwarders below.
from kriya.workflow.requirement_closers import SUITE_BASELINE_FROM_RUN as SUITE_BASELINE_FROM_RUN
from kriya.workflow.requirement_closers import SUITE_BASELINE_LAZY_CAPTURE as SUITE_BASELINE_LAZY_CAPTURE
from kriya.workflow.requirement_closers import _candidate_test_files as _candidate_test_files
from kriya.workflow.requirement_closers import _tracked_workspace_paths as _tracked_workspace_paths
from kriya.workflow.requirement_closers import _workspace_file_reader as _workspace_file_reader
from kriya.workflow.requirement_closers import _workspace_head as _workspace_head
from kriya.workflow.requirement_closers import (
    close_requirements_by_documentation as close_requirements_by_documentation,
)
from kriya.workflow.requirement_closers import documentation_referent_paths as documentation_referent_paths
from kriya.workflow.requirement_closers import frozen_file_paths as frozen_file_paths
from kriya.workflow.requirement_closers import named_preserved_test_files as named_preserved_test_files
from kriya.workflow.requirement_contract import bound_requirement_contract, requirement_set_for
from kriya.workflow.requirements import (
    REQUIREMENTS_UNRESOLVED,
    VerificationAuthorityRequired,
    blocking_requirements,
    cited_requirement_ids,
    requirement_lineage,
    seed_requirement_obligations,
    statement_origins,
)
from kriya.workflow.resume_fingerprints import (
    CANDIDATE_HASH_KEY,
    EFFECTIVE_LEDGER_KEY,
    Fingerprint,
    ResumePlan,
    build_resume_plan,
    candidate_snapshot_digest,
    fingerprint_block,
    generation_resume_fingerprints,
    ledger_fingerprint,
    restore_effective_ledger,
    workspace_fingerprint,
)
from kriya.workflow.resume_fingerprints import (
    CHECKPOINT_KEY as RESUME_FINGERPRINTS_KEY,
)
from kriya.workflow.retry_prompts import (
    RESOURCE_LIFECYCLE_HEADER,
    VERIFICATION_CONTRACT_HEADER,
    _build_ecosystem_invariant_block,
)
from kriya.workflow.retry_prompts import (
    _build_full_set_retry_prompt as _build_full_set_retry_prompt,
)
from kriya.workflow.retry_prompts import (
    _build_missing_files_retry_prompt as _build_missing_files_retry_prompt,
)
from kriya.workflow.retry_prompts import (
    _build_targeted_retry_prompt as _build_targeted_retry_prompt,
)
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.review_context import (
    build_candidate_diff_context,
    build_reviewer_verified_evidence,
)
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.run_lifecycle import (
    RunTrace,
    TerminalTrace,
    TraceDetail,
    open_run_state,
    persist_run_trace,
    record_run_exception,
    trace_run_events,
)
from kriya.workflow.run_trace import note_run_trace, record_run_exceptions
from kriya.workflow.semantic_region_authority import AuthorizedSemanticRegion, find_unauthorized_semantic_changes
from kriya.workflow.semantic_scope_derivation import derive_semantic_authority_for_run
from kriya.workflow.skill_extraction import (
    _filter_misattributed_extraction,
    _sanitize_for_flat_file_line,
    _scoped_skill_gap_description,
    _skill_staleness_warning,
    _split_rules_by_verification,
    _stage_skill_conflicts,
    _write_skill_extraction,
)
from kriya.workflow.skill_extraction import (
    _is_near_duplicate_rule as _is_near_duplicate_rule,
)
from kriya.workflow.skill_extraction import (
    _likely_misattributed_sibling as _likely_misattributed_sibling,
)
from kriya.workflow.stage_apply import (
    apply_terminal_candidate,
    approval_gate,
    extract_lessons,
    prepare_lessons,
    run_semantic_safety_gate,
    run_static_analysis_gate,
)
from kriya.workflow.stage_context import StageContext, StageDeps
from kriya.workflow.stage_review import final_review
from kriya.workflow.state import GenerationState, RecoveryPhaseAdvanced
from kriya.workflow.suite_attribution import (
    SCOPE_FULL_REGRESSION,
    attribute_suite_result,
    captured_baseline,
    post_environment_identity,
)
from kriya.workflow.terminal_commit import (
    CandidateFile,
    CandidateMaterializationError,
    commit_terminal_candidate,
    materialize_candidate,
)
from kriya.workflow.toolchain import (
    _check_java_toolchain_mismatch,
    _resolve_java_home_override,
    toolchain_declaration_mutable,
)
from kriya.workflow.toolchain import (
    _goal_or_repo_targets_java as _goal_or_repo_targets_java,
)
from kriya.workflow.toolchain import (
    _java_toolchain_fact as _java_toolchain_fact,
)
from kriya.workflow.toolchain import (
    _pin_exec_plugin_executable_to_resolved_jdk as _pin_exec_plugin_executable_to_resolved_jdk,
)
from kriya.workflow.toolchain import (
    _resolve_jdk_home_for_version as _resolve_jdk_home_for_version,
)
from kriya.workflow.toolchain import (
    _strip_jdk_incompatible_jvm_flags as _strip_jdk_incompatible_jvm_flags,
)
from kriya.workflow.triage import EngineeringRoute, EngineeringTriageService
from kriya.workflow.validation_baseline import (
    DeltaClassification,
    build_validation_outcome,
    capture_brownfield_baselines,
    regression_unattributed_diagnosis,
    render_blocking_regression_evidence,
)
from kriya.workflow.verification_binding import bind_candidate
from kriya.workflow.verification_contract import extract_contract_verdict as extract_contract_verdict
from kriya.workflow.verification_contract import pass_verdict_is_grounded as pass_verdict_is_grounded
from kriya.workflow.work_unit_runner import run_design_stages
from kriya.workflow.worktree import (
    WorktreeSyncError,
    create_git_worktree,
)
from kriya.workflow.worktree import (
    remove_git_worktree as remove_git_worktree,  # E4: its call sites moved to stage_apply; tests import it from here
)

logger = logging.getLogger(__name__)

# A REFUSE resume decision's machine-readable reason, keyed by the decision's
# fingerprint (only commit_state refuses; everything else invalidates).
_RESUME_REFUSAL_REASON_CODES = {"commit_state": "UNCERTAIN_COMMIT_STATE"}

_PHASE_BANNER_WIDTH = 70


_UNRESOLVED_GATE_MARKERS = (
    "quality gate skipped", "not confirmed", "no compile check available",
    "no test runner available", "no java test config found",
)


def _record_review_fit(state: Any, stage: str, fit: Any) -> None:
    """A ``context.request_fit`` event when a Reviewer request could not
    carry every file whole (PROMPT-BUDGET-FIT-001B): what was cut or left
    out, and the room the request had."""
    batches, truncated = fit.value
    if fit.omitted or truncated or fit.builds > 1:
        state.record_event(RunEvent(
            kind="context.request_fit", attempt=state.attempt_number, source="workflow",
            authority=EventAuthority.ADVISORY,
            message=f"{stage} review: {len(truncated)} file(s) cut or left out to fit the request",
            details={"request": f"reviewer.{stage}", "batches": len(batches),
                     "truncated_files": list(truncated), **fit.to_dict()},
        ))


def _review_refit_recorder(state: Any, stage: str, config: Any) -> Any:
    """``review_requests``' on_refit: a ``context.request_fit`` event when a
    role-chain fallback's review request carries less of a batch than the
    first candidate's did (PROMPT-FIT-ROLE-CHAIN-001)."""
    def record(candidate: Any, batch: int, details: Dict[str, Any]) -> None:
        state.record_event(RunEvent(
            kind="context.request_fit", attempt=state.attempt_number, source="workflow",
            authority=EventAuthority.ADVISORY,
            message=f"{stage} review batch {batch}: refitted for {candidate_model(config, candidate)}",
            details={"request": f"reviewer.{stage}", "batch": batch,
                     "model": candidate_model(config, candidate), **details},
        ))
    return record


# FINAL-REVIEW-BACKEND-ERROR-001: the reason code of a final-review failure
# that carries none of its own: a transport/provider/protocol error is
# FINAL_REVIEW_BACKEND_ERROR; any other exception type (a Kriya coding error
# inside the review path, review F3) is FINAL_REVIEW_INTERNAL_ERROR and says so.
FINAL_REVIEW_BACKEND_ERROR = "FINAL_REVIEW_BACKEND_ERROR"
FINAL_REVIEW_INTERNAL_ERROR = "FINAL_REVIEW_INTERNAL_ERROR"


def _final_review_error_code(error: BaseException) -> Tuple[str, str]:
    """(reason code, message) for an exception out of the final review request."""
    import httpx

    from kriya.core.completion import CompletionProtocolError
    from kriya.core.llm import EgressViolationError, StructuredOutputUnsupportedError
    from kriya.core.model_capabilities import ModelCapabilityError
    from kriya.core.provider_contract import ProviderContractError

    code = getattr(error, "reason_code", None)
    if isinstance(code, str) and code:
        return code, "the final review request was refused by the model backend"
    backend = (httpx.HTTPError, ProviderContractError, CompletionProtocolError, EgressViolationError,
               StructuredOutputUnsupportedError, ModelCapabilityError, ConnectionError, TimeoutError)
    if isinstance(error, backend):
        return FINAL_REVIEW_BACKEND_ERROR, "the final review request failed in the model backend"
    return FINAL_REVIEW_INTERNAL_ERROR, "the final review request failed inside Kriya's review path (internal error)"


def _record_run_exception(engine: Any, trace: Dict[str, Any], error: BaseException) -> None:
    """MODEL-EVIDENCE-HARDENING-001: the ``<trace_id>.exception`` row of a run
    that raises (kriya/workflow/run_lifecycle.py::record_run_exception), with
    this module's logger and the run_trace writer resolved at call time
    (``run_trace.record_run_exceptions`` imports this name lazily)."""
    from kriya.workflow.run_trace import write_outcome_trace

    record_run_exception(engine, trace, error, logger=logger, write_outcome_trace=write_outcome_trace)


def _role_metrics_of(client: Any) -> Any:
    """The client's PRD-018 RoleMetrics, or None (a test double)."""
    from kriya.core.role_metrics import RoleMetrics

    metrics = getattr(client, "role_metrics", None)
    return metrics if isinstance(metrics, RoleMetrics) else None


def _record_post_generation_ownership_findings(
    state: GenerationState, owner_candidates: List[Any], ledger: ObligationLedger, worktree_path: str,
    goal: str, subtask_id: Optional[str], justifications: Dict[str, str],
) -> None:
    """PRD-022: every file this candidate created (new in this run) checked
    against the grounded owners found before planning, using what the file
    actually contains as its work text. Findings not already recorded for
    the plan are recorded (GROUNDED, non-terminal) and kept for review."""
    created = [path for path in sorted(state.all_files_written) if not state.all_original_contents.get(path)]
    work = []
    for path in created:
        try:
            with open(os.path.join(worktree_path, path), "r", encoding="utf-8", errors="replace") as fh:
                work.append((path, subtask_id, fh.read()))
        except OSError:
            continue
    known = {finding.id for finding in state.ownership_findings}
    fresh = [
        finding for finding in settle_findings(
            find_ownership_findings(work, owner_candidates,
                                    touched_paths=[p for p in state.all_files_written if p not in created]),
            goal=goal, justifications=justifications,
        )
        if finding.id not in known
    ]
    if not fresh:
        return
    record_findings(ledger, fresh, revision=f"post_generation:{state.attempt_number}",
                    source="workflow.post_generation")
    state.ownership_findings.extend(fresh)
    for finding in fresh:
        state.record_event(RunEvent(
            kind="ownership.finding", attempt=state.attempt_number, source="workflow.post_generation",
            authority=EventAuthority.ADVISORY,
            message=f"{finding.planned_path} may duplicate {finding.candidate_owner} ({finding.status})",
            details=finding.to_dict(),
        ))


def _role_independence_details(cfg: Any) -> Dict[str, Any]:
    """PRD-018: each role's exact runtime, the roles grouped by shared
    runtime, and the independence policy with any violation."""
    from kriya.core.role_metrics import independence_violations, role_runtimes, shared_runtime_groups

    runtimes = role_runtimes(cfg)
    return {
        "roles": {role: {"model": model, "runtime_digest": digest, "runtime_exact": exact}
                  for role, (model, digest, exact) in sorted(runtimes.items())},
        "groups": shared_runtime_groups(runtimes),
        "independent_roles_required": list(cfg.model_policy.independent_roles),
        "violations": independence_violations(cfg, runtimes),
        "second_opinion_is_verification": False,
    }


async def _primary_model_runtime_details(cfg: Any) -> Dict[str, Any]:
    """PRD-013/014: the primary model's runtime fingerprint and the
    Developer role's qualification assessment, for the ``model.runtime``
    run event. Evidence only; never blocks a run."""
    import asyncio

    from kriya.core.inference_settings import role_inference_settings
    from kriya.core.model_qualification import assess, policy_digest_for, required_capabilities
    from kriya.core.model_runtime import resolve_configured_model_runtime

    fingerprint = await asyncio.to_thread(resolve_configured_model_runtime, cfg)
    settings = role_inference_settings(cfg, "developer", cfg.llm.model)
    qualification = assess(fingerprint, required_capabilities(cfg, "developer", cfg.llm.model), settings=settings,
                           policy_digest=policy_digest_for(cfg))
    return {"fingerprint": fingerprint.to_dict(), "developer_qualification": qualification.to_dict()}


def _gate_outcome_proven(outcome: Optional[Dict[str, Any]]) -> Optional[bool]:
    """True only for a gate that actually executed and passed; None for a
    skipped/unconfirmed one (e.g. an "unknown" stack's pass-through)."""
    if outcome is None:
        return None
    if outcome.get("success") is False:
        return False
    output = str(outcome.get("output", "")).lower()
    if any(marker in output for marker in _UNRESOLVED_GATE_MARKERS):
        return None
    return True if outcome.get("success") is True else None


_DETERMINISTIC_GATE_TYPES = ("compile", "test", "targeted_test", "regression_test", "run_verification")
_TEST_GATE_TYPES = frozenset({"test", "targeted_test", "regression_test"})


def deterministic_gate_evidence(
    gate_outcomes: Optional[List[Dict[str, Any]]], attempt: Optional[int],
) -> List[Dict[str, Any]]:
    """PRD-008 S4c: the latest outcome of each deterministic gate type IN
    ``attempt`` (the run's final attempt), as evidence a caller can bind to
    (e.g. a milestone that committed nothing). A gate that passed in an
    earlier attempt ran against a different candidate and is never included;
    neither are model verdicts. ``status`` is PASS_WITH_TESTS (a test gate
    that ran tests), PASSED, FAILED, NO_TESTS_EXECUTED (exited 0, ran
    nothing) or UNAVAILABLE (skipped/unconfirmed); ``passed`` is True only
    for the first two - the only positive evidence."""
    if attempt is None:
        return []
    final = [outcome for outcome in (gate_outcomes or []) if outcome.get("attempt") == attempt]
    evidence = []
    for gate_type in _DETERMINISTIC_GATE_TYPES:
        latest = next(
            (outcome for outcome in reversed(final) if outcome.get("type") == gate_type), None,
        )
        if latest is None:
            continue
        proven = _gate_outcome_proven(latest)
        if proven is False:
            status = "FAILED"
        elif proven is None:
            status = "UNAVAILABLE"
        elif gate_type not in _TEST_GATE_TYPES:
            status = "PASSED"
        elif output_confirms_nonzero_test_execution(str(latest.get("output", ""))):
            status = "PASS_WITH_TESTS"
        else:
            # "collected 0 items" passes vacuously; it proves nothing.
            status, proven = "NO_TESTS_EXECUTED", None
        evidence.append({"type": gate_type, "passed": proven, "status": status, "attempt": latest.get("attempt")})
    return evidence


def _stop_unverified_no_change(state: GenerationState) -> None:
    """ENFORCE-IDENTICAL-WRITE-COMPLETION-001 (review F1, second repair cycle):
    a direct goal whose every file came back byte-identical passed every
    deterministic gate on an unchanged tree, and nothing decides "already
    satisfied" - the enforce contract (the no-change proposal above) and the
    milestone driver (no_change_verification) judge that from deterministic
    evidence; here nothing would. Model content alone never completes a run:
    a typed stop through the ordinary repair path, after the gates (a more
    specific deterministic failure - migration, compile, test - speaks first)."""
    from kriya.workflow.verified_no_change import NO_CHANGE_UNVERIFIED

    unchanged = sorted(state.identical_rewrites)
    message = (f"{NO_CHANGE_UNVERIFIED}: the Developer returned {', '.join(unchanged)} byte-identical to the "
               "baseline (an identical rewrite is not a change) and nothing verifies that the goal is already "
               "satisfied. Make the change the goal requires.")
    failure = Failure(
        type="unverified_no_change", message=message, raw_output=message, source="completeness",
        authority="deterministic", file_locations=[FileLocation(filepath=path) for path in unchanged],
        likely_files=unchanged,
        diagnostics={"reason_code": NO_CHANGE_UNVERIFIED, "identical_rewrites": unchanged},
        attempt=state.attempt_number,
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _settle_no_change_proposal(
    state: GenerationState, structured_plan: Any, subtask_id: Optional[str], worktree_path: str, *,
    reopened_owner: bool = False,
) -> None:
    """ENFORCE-VERIFIED-NO-CHANGE-001: decide a unit's NO CHANGE proposal
    from the final attempt's deterministic evidence, after every normal gate
    and the terminal regression ran on the current candidate. Accepted: the
    unit completes as VERIFIED_NO_CHANGE when it wrote nothing (nothing
    authorized), or - ENFORCE-PARTIAL-NO-CHANGE-001, owner decision OD-3 - as
    an ordinary mutation of the files it did write with the proposed files
    verified unchanged (``partial``). Refused: a typed deterministic gate
    failure through the ordinary repair path - never success, and the
    attempt's own writes are not applied either."""
    from kriya.workflow.milestone_completion import workspace_evidence_hash
    from kriya.workflow.verified_no_change import (
        VERIFIED_NO_CHANGE,
        VERIFIED_NO_CHANGE_REFUSED,
        unit_coverage_items,
        verify_no_change_unit,
    )

    attempt = state.attempt_number
    identical = sorted(set(state.no_change_proposal) & state.identical_rewrites)
    if reopened_owner and identical and not state.all_files_written:
        # ENFORCE-IDENTICAL-WRITE-COMPLETION-001 x the owner reopen: the
        # controller accepts a reopened owner only on a byte change, so a
        # regeneration that changed NOTHING can never be the repair (a mixed
        # regeneration - one file changed, one identical - never reaches
        # here: attempt.py proposes no identical path for a reopened owner
        # that wrote, and the controller's own rule decides it) - the typed
        # no-progress stop the verification-only
        # admission already uses (one attempt, the verification failure
        # stands), never repair retries on the same identical answer.
        from kriya.workflow.retry_progress import VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
        from kriya.workflow.retry_strategy import NO_PROGRESS_STOP_KEY

        message = (f"{VERIFICATION_RETRY_NO_CHANGE_POSSIBLE}: the reopened owner regenerated {', '.join(identical)} "
                   "byte-identical to the current content - not a repair; the verification failure stands.")
        failure = Failure(
            type="verified_no_change_refused", message=message, raw_output=message, source="verified_no_change",
            authority="deterministic", file_locations=[FileLocation(filepath=path) for path in identical],
            likely_files=identical,
            # The retry strategy owns the no-progress terminal; this failure declares it (applied after the
            # workspace-progress classification, which would otherwise reset the flag).
            diagnostics={"reason_code": VERIFICATION_RETRY_NO_CHANGE_POSSIBLE, "identical_rewrites": identical,
                         NO_PROGRESS_STOP_KEY: VERIFICATION_RETRY_NO_CHANGE_POSSIBLE},
            attempt=attempt,
        )
        state.record_gate_outcome(failure.to_gate_outcome())
        state.record_event(RunEvent(
            kind="unit.verified_no_change_refused", attempt=attempt, source="workflow",
            authority=EventAuthority.AUTHORITATIVE,
            details={"subtask": subtask_id, "paths": list(state.no_change_proposal),
                     "refusal": {"code": VERIFICATION_RETRY_NO_CHANGE_POSSIBLE, "detail": message,
                                 "identical_rewrites": identical, "reopened_owner": True}},
        ))
        raise QualityGateFailure(failure)
    result = {
        "deterministic_gate_evidence": deterministic_gate_evidence(state.gate_outcomes, attempt),
        "acceptance_coverage": unit_coverage_items(structured_plan, subtask_id, state.gate_outcomes, attempt),
    }
    binding, refusal = verify_no_change_unit(structured_plan, subtask_id, result)
    if refusal is not None:
        identical = sorted(set(state.no_change_proposal) & state.identical_rewrites)
        answered = [path for path in state.no_change_proposal if path not in state.identical_rewrites]
        proposed = "; ".join(filter(None, (
            f"the Developer proposed NO CHANGE for {', '.join(answered)}" if answered else "",
            # ENFORCE-IDENTICAL-WRITE-COMPLETION-001: an identical rewrite is the same proposal, said as such.
            f"the Developer returned {', '.join(identical)} byte-identical to the baseline (an identical rewrite "
            "is not a change)" if identical else "",
        )))
        failure = Failure(
            type="verified_no_change_refused",
            message=(f"{VERIFIED_NO_CHANGE_REFUSED}: {proposed}, but {refusal['detail']} ({refusal['code']}). "
                     "A planned file completes without a change only when every acceptance criterion of the "
                     "unit is verified by deterministic evidence; otherwise make the change the unit requires."),
            raw_output=str(refusal), source="verified_no_change", authority="deterministic",
            file_locations=[FileLocation(filepath=path) for path in state.no_change_proposal],
            likely_files=list(state.no_change_proposal),
            diagnostics={"reason_code": VERIFIED_NO_CHANGE_REFUSED, "verified_no_change": refusal},
            attempt=attempt,
        )
        state.record_gate_outcome(failure.to_gate_outcome())
        state.record_event(RunEvent(
            kind="unit.verified_no_change_refused", attempt=attempt, source="workflow",
            authority=EventAuthority.AUTHORITATIVE,
            details={"subtask": subtask_id, "paths": list(state.no_change_proposal), "refusal": refusal},
        ))
        raise QualityGateFailure(failure)
    written = sorted(state.all_files_written)
    if not written:
        state.completion_kind = VERIFIED_NO_CHANGE
    state.record_event(RunEvent(
        kind="unit.verified_no_change", attempt=attempt, source="workflow",
        authority=EventAuthority.AUTHORITATIVE,
        details={**binding, "subtask": subtask_id, "paths": list(state.no_change_proposal),
                 "partial": bool(written), "written_paths": written,
                 "workspace_content_hash": workspace_evidence_hash(worktree_path)},
    ))


def _build_required_verification_evidence(
    requirements: Optional[List[Dict[str, Any]]], quality_gates_passed: bool,
    gate_outcomes: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Resolve only requirements the mature execution core can prove."""
    outcomes_supplied = gate_outcomes is not None
    outcomes = gate_outcomes or []

    def _latest_outcome(types: set[str]) -> Optional[Dict[str, Any]]:
        return next(
            (outcome for outcome in reversed(outcomes) if outcome.get("type") in types),
            None,
        )

    _outcome_passed = _gate_outcome_proven

    evidence: List[Dict[str, Any]] = []
    for requirement in requirements or []:
        item = {
            "type": requirement.get("type"),
            "description": requirement.get("description", ""),
            "tool_name": requirement.get("tool_name"),
            "passed": None,
            "source": "unresolved",
        }
        if (
            requirement.get("type") == "tool"
            and requirement.get("tool_name") in BUILTIN_QUALITY_GATE_VERIFIERS
        ):
            tool_name = requirement.get("tool_name")
            outcome_types = {
                "compile": {"compile"},
                "test": {"test", "targeted_test", "regression_test"},
                "tests": {"test", "targeted_test", "regression_test"},
                "regression": {"regression_test"},
                "quality_gates": {"compile", "test", "targeted_test", "regression_test"},
            }[tool_name]
            if outcomes_supplied:
                if tool_name == "quality_gates":
                    applicable = [
                        outcome for outcome in outcomes
                        if outcome.get("type") in outcome_types
                    ]
                    statuses = [_outcome_passed(outcome) for outcome in applicable]
                    item["passed"] = (
                        False if False in statuses
                        else None if not statuses or None in statuses
                        else True
                    )
                else:
                    item["passed"] = _outcome_passed(_latest_outcome(outcome_types))
            else:
                # Compatibility for callers that only have the historical
                # aggregate result and no per-gate evidence.
                item["passed"] = bool(quality_gates_passed)
            item["source"] = (
                "authoritative_gate_outcome"
                if item["passed"] is not None and outcomes_supplied
                else "existing_quality_gates" if not outcomes_supplied
                else "unresolved"
            )
        elif (
            requirement.get("type") == "judgment"
            and requirement.get("requires_runtime_execution") is True
            and outcomes_supplied
        ):
            # A run-verification pass whose concrete command happens to be
            # a test-runner invocation (kriya/workflow/attempt.py's own
            # `gate_type = "test" if command_verification_kind == "test"
            # else "run_verification"`) is deliberately tagged type="test",
            # not "run_verification" - so the SAME outcome also satisfies
            # an ordinary tool_name="test" requirement via the branch
            # above. Searching only {"run_verification"} here made a
            # genuine, already-PASSING runtime-verification outcome
            # produced by exactly that path invisible to this lookup.
            # Found live, P2 production-validation run 4 (2026-09-06,
            # spring-ignite-demo): the auto-approved command was `mvn
            # test`, it ran via run_app_sequence() and passed, and Kriya's
            # own generated success criteria ("Running `mvn test` exits
            # with code 0 ... confirming the salary cap logic and save
            # invariant are correctly implemented") already declared that
            # sufficient - yet this requirement still reported REQUIRED
            # VERIFICATION UNRESOLVED. Widened to also accept a "test"/
            # "targeted_test"/"regression_test"-typed outcome, but ONLY
            # when it carries the "commands" key - set exclusively by
            # run_app_sequence()'s own two success-path appends (attempt.py)
            # and never by an ordinary compile/test Quality Gate outcome -
            # so a ordinary test run that never went through real runtime
            # verification can never satisfy a runtime-execution
            # requirement it was never produced to prove.
            item["passed"] = _outcome_passed(next(
                (
                    outcome for outcome in reversed(outcomes)
                    if outcome.get("type") in {"run_verification", "test", "targeted_test", "regression_test"}
                    and outcome.get("commands")
                ),
                None,
            ))
            item["source"] = (
                "authoritative_runtime_verification"
                if item["passed"] is not None else "unresolved"
            )
        evidence.append(item)
    return evidence


async def _ensure_repository_indexed(cfg: Any, workspace_path: str) -> bool:
    """Runs a one-time index_repository() pass (dependency_graph.db's symbol
    tables + vector_index.db's code embeddings) for `workspace_path`, but only
    when it has never been indexed at all. Closes a real gap: index_repository()
    is otherwise called EXCLUSIVELY from the `kriya analyze` CLI command, never
    from run_generation_workflow() - a repo a user never explicitly analyzed has
    an empty persisted graph for the life of every generate/fix call against it.
    See autonomy.auto_index_missing_dependency_graph's own docstring
    (kriya/config/config.py) for the full rationale and why this is opt-in.

    Callers gate this behind that config flag AND must call it before
    GenerationState is constructed (state.generation_started_monotonic starts
    generation_time_budget_seconds' clock at construction) - see the call site
    in run_generation_workflow() for why. Kept as a standalone module-level
    function (not inlined there) specifically so it's unit-testable without
    mocking the entire multi-agent pipeline.

    Never changed=True: that flag scopes indexing to files `git diff`/`git
    status` reports as modified/staged/untracked - on a fully-committed
    pre-existing repo (exactly the case this exists to cover) that would
    silently index nothing at all.

    Swallows and logs any failure (embedding endpoint down, model not pulled,
    etc.) rather than raising - generation must proceed exactly as it does
    today with an empty graph, never be blocked by this."""
    try:
        from kriya.analyzer.graph import DependencyGraph
        graph_db_path = os.path.join(cfg.paths.memory, "dependency_graph.db")
        probe = DependencyGraph(graph_db_path)
        try:
            already_indexed = probe.has_indexed_files()
        finally:
            probe.close()
        if already_indexed:
            return True
        logger.info(
            f"No indexed dependency graph found for '{workspace_path}' - running a "
            "one-time repository index before generation starts (autonomy."
            "auto_index_missing_dependency_graph is enabled)."
        )
        await RepositoryAnalyzer(workspace_path).index_repository(cfg)
        return True
    except Exception as index_ex:
        logger.warning(
            f"Auto-index of '{workspace_path}' failed, continuing without a populated "
            f"dependency graph (same as today's default behavior): {index_ex}"
        )
        return False


def _log_phase_banner(title: str) -> None:
    """Logs a full-width, solid-line-bordered banner announcing a new top-
    level pipeline phase (Planning/Architecture/Development/Review). A single
    logger.info() call reaches both the console and kriya.log identically -
    configure_logging() (kriya/cli.py) attaches a console StreamHandler and an
    optional file FileHandler to the SAME root logger with the SAME
    formatter, so there is no separate console-only/file-only rendering path
    to keep in sync. Purely cosmetic (log-scanning aid for a human watching
    a run) - never gates or changes control flow."""
    bar = "=" * _PHASE_BANNER_WIDTH
    logger.info(f"\n{bar}\n{title.center(_PHASE_BANNER_WIDTH)}\n{bar}")


def unresolved_knowledge_report(report: Any, resolved_coordinates: Optional[List[str]]) -> Any:
    """Remove only run-level acknowledged coordinates from a subtask report.

    ``None`` preserves the legacy boolean-confirmation contract. An explicit
    list, including an empty list, activates coordinate-scoped resolution so a
    dependency first introduced by a bounded subtask remains a real gap.
    """
    if resolved_coordinates is None:
        return report
    from kriya.tools.knowledge import GapReport
    resolved = set(resolved_coordinates)
    unresolved = GapReport()
    unresolved.gaps = [gap for gap in report.gaps if gap["library"] not in resolved]
    return unresolved


def _resolve_protected_relpath(workspace_path: str, protected_source_file: Optional[str]) -> Optional[str]:
    """Workspace-relative form of the goal-source file supplied via `kriya
    generate --file <path>`, for AuthorizedFileWriter's protected_relpaths
    (kriya/policy/filesystem.py) to deny a generated write against - see that
    class's own docstring for the real live incident (ignite_qpid_protocol,
    2026-08-25) this exists to prevent. Returns None when there's nothing to
    protect: no file was supplied, or it resolves outside workspace_path
    entirely (a write can never land there via a workspace-relative path
    anyway, so there's nothing this specific mechanism needs to guard)."""
    if not protected_source_file:
        return None
    try:
        abs_target = os.path.realpath(expand_user(protected_source_file))
        abs_workspace = os.path.realpath(expand_user(workspace_path))
    except Exception:
        return None
    if path_relation(abs_workspace, abs_target) is not PathRelation.WITHIN:
        return None
    return os.path.normpath(os.path.relpath(abs_target, abs_workspace))


def _record_future_owner_verification_deferred(
    ledger: ObligationLedger,
    deferral: FutureOwnerVerificationDeferral,
    *,
    observed_by_subtask_id: str,
    raw_output: str,
) -> ObligationRecord:
    """Records (or idempotently re-records - ObligationRecord.id is stable
    per (future_owner, capability), so a LATER subtask deferring the same
    still-pending requirement just appends another PENDING entry to the
    same obligation's history, never a duplicate obligation) a PENDING,
    terminal_required obligation for the not-yet-executed subtask this
    regression failure is already covered by - see ObligationKind.
    FUTURE_OWNER_VERIFICATION's own docstring (kriya/workflow/
    obligations.py) for the live incident. terminal_required=True is the
    entire safety net: workflow_controller.py's own global terminal-
    obligation aggregation check (already wired for ANY terminal_required
    kind, no new gate needed there) fails the whole run if this is still
    PENDING or VIOLATED once every subtask has finished - so a deferred
    requirement that never actually gets resolved can never silently
    produce a false "success"."""
    obligation_id = (
        f"future_owner_verification.{deferral.future_owner_id}."
        f"{deferral.required_capability}"
    )
    record = ObligationRecord(
        id=obligation_id,
        kind=ObligationKind.FUTURE_OWNER_VERIFICATION,
        status=ObligationStatus.PENDING,
        authority=ObligationAuthority.DETERMINISTIC,
        description=(
            f"Regression evidence in {deferral.evidence_path!r} (owned by verification "
            f"subtask {deferral.verification_subtask_id!r}) requires capability "
            f"{deferral.required_capability!r}, provided by not-yet-executed subtask "
            f"{deferral.future_owner_id!r} - deferred pending that subtask's own execution."
        ),
        source="future_owner_verification_deferral",
        revision=observed_by_subtask_id,
        evidence={
            "verification_subtask_id": deferral.verification_subtask_id,
            "evidence_path": deferral.evidence_path,
            "required_capability": deferral.required_capability,
            "observed_by_subtask_id": observed_by_subtask_id,
            "raw_output": raw_output[:4000],
        },
        owner_subtask_id=deferral.future_owner_id,
        terminal_required=True,
    )
    ledger.record(record)
    return record


def _settle_future_owner_verification_obligations(
    ledger: ObligationLedger, subtask_id: str, *, satisfied: bool,
) -> List[ObligationRecord]:
    """Called from BOTH the regression-success path and the regression-
    failure-with-no-deferral path (see this module's own call sites) for
    the CURRENTLY executing subtask - settles every still-PENDING
    FUTURE_OWNER_VERIFICATION obligation this subtask itself owns.
    Bookkeeping only, never control flow: the ordinary success/failure
    handling already does the right thing on its own (a real pass is a
    real pass; a real failure already falls through to this subtask's own
    normal retry/attribution path, which IS "recovering against the now-
    responsible owner" - see ObligationKind.FUTURE_OWNER_VERIFICATION's own
    docstring for why no separate recheck mechanism exists or is needed:
    resolve_future_owner_verification_deferral() refuses self-deferral, so
    THIS subtask's own regression check is never deferred again once it's
    the one actually executing)."""
    settled: List[ObligationRecord] = []
    for record in ledger.current_by_kind(ObligationKind.FUTURE_OWNER_VERIFICATION):
        if record.owner_subtask_id != subtask_id or record.status != ObligationStatus.PENDING:
            continue
        settled_record = ObligationRecord(
            id=record.id,
            kind=record.kind,
            status=ObligationStatus.SATISFIED if satisfied else ObligationStatus.VIOLATED,
            authority=ObligationAuthority.DETERMINISTIC,
            description=record.description,
            source="future_owner_verification_settle",
            revision=subtask_id,
            evidence=record.evidence,
            owner_subtask_id=record.owner_subtask_id,
            terminal_required=record.terminal_required,
        )
        ledger.record(settled_record)
        settled.append(settled_record)
    return settled


def immutable_test_files(contract: Any, workspace_path: str) -> Optional[List[str]]:
    """The body is ``kriya/workflow/requirement_closers.py::immutable_test_files`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.immutable_test_files(_closer_deps(), contract, workspace_path)


def _reference_test_files(candidate_root: str, workspace_path: str) -> Optional[List[str]]:
    """The body is ``kriya/workflow/requirement_closers.py::_reference_test_files`` (E4); its collaborators
    are resolved here, at call time."""
    return closers._reference_test_files(_closer_deps(), candidate_root, workspace_path)


def close_requirements_with_acceptance_tests(
    autonomy_cfg: Any, ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    acceptance: Any, modified: Iterable[str], revision: Any, toolchain_declaration_mutable: bool,
    tree_binding: Any = None, approval: Any = None, contract: Any = None, companion: Any = None,
    source: str = "requirement_closure.acceptance",
) -> List[Dict[str, Any]]:
    """The body is ``kriya/workflow/requirement_closers.py::close_requirements_with_acceptance_tests`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.close_requirements_with_acceptance_tests(
        _closer_deps(), autonomy_cfg, ledger, requirement_set, candidate_root, workspace_path,
        acceptance=acceptance, modified=modified, revision=revision,
        toolchain_declaration_mutable=toolchain_declaration_mutable, tree_binding=tree_binding, approval=approval,
        contract=contract, companion=companion, source=source
    )


def close_requirements_with_derived_examples(
    autonomy_cfg: Any, ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    engine: Any, modified: Iterable[str], revision: Any, toolchain_declaration_mutable: bool,
    tree_binding: Any = None, contract: Any = None,
) -> List[Dict[str, Any]]:
    """The body is ``kriya/workflow/requirement_closers.py::close_requirements_with_derived_examples`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.close_requirements_with_derived_examples(
        _closer_deps(), autonomy_cfg, ledger, requirement_set, candidate_root, workspace_path, engine=engine,
        modified=modified, revision=revision, toolchain_declaration_mutable=toolchain_declaration_mutable,
        tree_binding=tree_binding, contract=contract
    )


def _run_contract_acceptance(
    artifact: Any, touched: Sequence[str], *, candidate_root: str, workspace_path: str, autonomy_cfg: Any,
) -> Any:
    """BACKEND-READINESS-V2 slot 3: ONE run of the sealed goal-example class on the unit's candidate, through the same JVM
    acceptance runner and trust-surface rules as the terminal closer (a Kriya-owned copy of the candidate; the unit worktree is
    never touched). Module-level so tests can substitute the runner."""
    from kriya.workflow.acceptance_jvm import run_java_acceptance

    def export_validator(root: str) -> PolymorphicValidator:
        return PolymorphicValidator(root, original_workspace_path=workspace_path, autonomy_cfg=autonomy_cfg)

    return run_java_acceptance(
        artifact, candidate_root, candidate_paths=list(touched),
        base_revision=_oracle_base_revision(candidate_root, workspace_path), validator_factory=export_validator)


def build_contract_feedback(engine: Any, candidate_root: str, workspace_path: str, autonomy_cfg: Any) -> Any:
    """The run's sealed goal-example class as intermediate repair feedback for the current unit (None when the run binds no
    Java example class). Bound once per unit from the engine's contract compilation; never recompiled here."""
    from kriya.workflow.contract_feedback import ContractFeedback, contract_examples
    from kriya.workflow.example_oracle import bound_derived_examples

    artifact = bound_derived_examples(engine)
    report = getattr(engine, "derived_examples_report", None)
    if artifact is None or artifact.language != "java" or not isinstance(report, dict):
        return None
    examples = contract_examples(artifact, report, candidate_root)
    if not examples:
        return None
    return ContractFeedback(
        artifact=artifact, examples=examples, candidate_root=candidate_root,
        execute=lambda art, touched: _run_contract_acceptance(
            art, touched, candidate_root=candidate_root, workspace_path=workspace_path, autonomy_cfg=autonomy_cfg))


def _requirement_policy_blocks(autonomy: Any) -> bool:
    """Whether a requirement without accepted evidence blocks success under this
    configuration (the production preset seals both policies to ``block``). Only
    then is a requirement with no deterministic closer unverifiable in effect,
    so only then is a goal refused at admission."""
    return getattr(autonomy, "requirement_unverified_policy", "record") == "block"


def _migration_identities(goal: str, workspace_path: str) -> List[Tuple[str, str]]:
    """(source, target) of the dependency migration the goal states, when the
    repository resolves it (the migration gate's closer binds to it); empty
    otherwise or when resolution fails."""
    try:
        resolution = resolve_migration_resolution(goal, workspace_path)
    except Exception:
        return []
    obligation = getattr(resolution, "obligation", None)
    if obligation is None:
        return []
    return [(obligation.source_identity, obligation.target_identity)]


def _acceptance_requirement_ids(acceptance: Any) -> List[str]:
    """The requirement ids an operator acceptance artifact covers (none without one)."""
    return sorted(getattr(acceptance, "requirement_ids", ()) or ()) if acceptance is not None else []


_PYTHON_MANIFESTS = frozenset({"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"})
_JVM_MANIFESTS = frozenset({"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"})


def _project_language(tracked_paths: Iterable[str]) -> Optional[str]:
    """"python" / "java" / None from the repository's own manifests, else its
    sources (VERIFICATION-CONTRACT-003: which API-preservation predicate, if
    any, the contract may bind). Deterministic repository evidence only."""
    paths = list(tracked_paths)
    names = {path.rsplit("/", 1)[-1] for path in paths}
    if names & _JVM_MANIFESTS:
        return "java"
    if names & _PYTHON_MANIFESTS:
        return "python"
    if any(path.endswith((".java", ".kt")) for path in paths):
        return "java"
    if any(path.endswith(".py") for path in paths):
        return "python"
    return None


def compile_run_contract(engine: Any, requirement_goal: Optional[str], goal: str, workspace_path: str,
                         requirement_set: Any) -> Any:
    """VERIFICATION-CONTRACT-003: compile the run's verification contract
    (kriya/workflow/contract_compilation.py) from the authoritative
    requirement set, the goal's own structure (statement origins; an
    operator contract's statements are sentences), the repository's test
    files and tracked paths, the resolved migration, the bound B2 artifact,
    B3 approval and external authorities, the project language and the
    workspace's HEAD. Shared by the direct and the enforce path; pure given
    the workspace, never a model call."""
    from kriya.core.state_paths import resolve_state_directory
    from kriya.workflow.contract_compilation import bound_external_authorities, compile_verification_contract
    from kriya.workflow.example_oracle import derive_example_artifact, example_authority
    from kriya.workflow.requirement_disposition import bound_dispositions

    tracked = _tracked_workspace_paths(workspace_path)
    origins = statement_origins(requirement_goal) if (requirement_goal and requirement_set.contract_digest is None) else {}
    authorities = list(bound_external_authorities(engine))
    # The goal's own example lines, compiled deterministically into a sealed
    # acceptance module (EXACT behaviour authority; B2-COV applies). Bound
    # once here, before any model call, on both paths.
    config = getattr(getattr(engine, "kernel", None), "config", None)
    engine.derived_examples = None
    engine.derived_examples_report = None
    language = _project_language(tracked)
    if requirement_goal and config is not None and language not in ("python", "java"):
        engine.derived_examples_report = {"refusal": {"reason_code": "EXAMPLE_COMPILER_NOT_APPLICABLE",
                                                      "message": f"the example compiler supports Python and Java projects; "
                                                                 f"this project is {language or 'of unknown language'}"}}
    elif requirement_goal and config is not None:
        # JAVA-EXAMPLE-COMPILER-001: the Java form compiles into a sealed B2-c class the same way.
        from kriya.workflow.example_oracle_java import derive_java_example_artifact

        derive = derive_java_example_artifact if language == "java" else derive_example_artifact
        try:
            artifact, report = derive(requirement_goal, requirement_set,
                                      state_root=resolve_state_directory(config)[0],
                                      candidate_root=workspace_path)
        except Exception as exc:  # a compiler defect is recorded, never a silent loss of authority
            artifact, report = None, {"refusal": {"reason_code": "EXAMPLE_COMPILER_ERROR",
                                                  "message": f"{type(exc).__name__}: {exc}"}}
        engine.derived_examples = artifact
        engine.derived_examples_report = report
        if artifact is not None:
            authorities.append(example_authority(artifact, report))
    return compile_verification_contract(
        requirement_set, origins=origins, test_files=_candidate_test_files(workspace_path), tracked_paths=tracked,
        migration_identities=_migration_identities(goal, workspace_path),
        acceptance=bound_acceptance(engine), approval=bound_approval(engine),
        external_authorities=authorities, project_language=language,
        tracked_file_reader=_workspace_file_reader(workspace_path), base_revision=_workspace_head(workspace_path),
        dispositions=bound_dispositions(engine),
    )


def seal_run_contract(config: Any, contract: Any) -> Optional[str]:
    """Store the compiled contract content-addressed in Kriya's state directory
    (never the workspace); None when no state directory can be resolved."""
    from kriya.core.state_paths import resolve_state_directory
    from kriya.workflow.contract_compilation import seal_verification_contract

    try:
        return seal_verification_contract(contract, resolve_state_directory(config)[0])
    except Exception as exc:  # the contract still governs the run; the store is evidence
        logger.warning(f"Verification contract could not be stored: {type(exc).__name__}: {exc}")
        return None


def close_requirements_by_file_immutability(
    ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    candidate_paths: Iterable[str], revision: Any, contract: Any = None,
) -> List[Dict[str, Any]]:
    """The body is ``kriya/workflow/requirement_closers.py::close_requirements_by_file_immutability`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.close_requirements_by_file_immutability(
        _closer_deps(), ledger, requirement_set, candidate_root, workspace_path, candidate_paths=candidate_paths,
        revision=revision, contract=contract
    )


def close_requirements_by_test_immutability(
    ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    candidate_paths: Iterable[str], revision: Any, contract: Any = None,
) -> List[Dict[str, Any]]:
    """The body is ``kriya/workflow/requirement_closers.py::close_requirements_by_test_immutability`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.close_requirements_by_test_immutability(
        _closer_deps(), ledger, requirement_set, candidate_root, workspace_path, candidate_paths=candidate_paths,
        revision=revision, contract=contract
    )


def run_contract_baseline(engine: Any, contract: Any, requirement_set: Any, workspace_path: str, *,
                          autonomy_cfg: Any, config: Any) -> Any:
    """VERIFICATION-CONTRACT-003: run every bound behaviour authority against
    the untouched baseline (kriya/workflow/contract_baseline.py) through the
    same producers the pre-apply boundary uses, before the first model call.
    Returns the BaselineAuthorityReport; never raises for an authority that
    cannot run (its verdict is INDETERMINATE there)."""
    import shutil
    import uuid

    from kriya.core.state_paths import resolve_state_directory
    from kriya.tools import test_execution
    from kriya.workflow.acceptance_jvm import run_java_acceptance
    from kriya.workflow.acceptance_oracle import judge_acceptance, run_acceptance
    from kriya.workflow.authority_bundle import AUTHORITY_RUNS_DIR, bound_authority_bundle, run_authority_bundle
    from kriya.workflow.contract_baseline import run_baseline_authorities, suite_verdict_from_report
    from kriya.workflow.example_oracle import bound_derived_examples

    base_revision = _workspace_head(workspace_path)
    state_root = resolve_state_directory(config)[0] if config is not None else None
    if state_root is None:
        raise RuntimeError("no state directory: the baseline authority run needs a Kriya-owned scratch")
    # Review VC3-R8: every baseline producer runs on a Kriya-owned export of the
    # untouched tree under the state root, never inside the real workspace.
    scratch = os.path.join(state_root, AUTHORITY_RUNS_DIR, "baseline-" + uuid.uuid4().hex)
    export = os.path.join(scratch, "workspace")

    def validator_for(root: str) -> PolymorphicValidator:
        return PolymorphicValidator(root, original_workspace_path=workspace_path, autonomy_cfg=autonomy_cfg)

    def exported() -> str:
        if not os.path.isdir(export):
            os.makedirs(scratch, exist_ok=True)
            shutil.copytree(workspace_path, export, symlinks=True,
                            ignore=shutil.ignore_patterns(".kriya", "target", "build", ".gradle", "node_modules",
                                                          "__pycache__", ".pytest_cache"))
        return export

    def judge(artifact: Any) -> Callable[[], Mapping[str, Any]]:
        def run() -> Mapping[str, Any]:
            root = exported()
            if artifact.language == "java":
                executed = run_java_acceptance(artifact, root, candidate_paths=(), base_revision=base_revision,
                                               validator_factory=validator_for)
            else:
                executed = run_acceptance(artifact, root, candidate_paths=(), validator_factory=lambda: validator_for(root))
            return judge_acceptance(artifact, executed)
        return run

    def judge_suite() -> Any:
        # CANDIDATE-GATE-BASELINE-POLICY-001 (D1b): a complete per-case report decides; failures of the untouched
        # base are recorded as pre-existing (kriya/workflow/contract_baseline.py suite_verdict_from_report).
        result = validator_for(exported()).run_tests()
        return suite_verdict_from_report(result, test_execution.report_from_result(result))

    bundle = bound_authority_bundle(engine)
    acceptance = bound_acceptance(engine)
    examples = bound_derived_examples(engine)
    try:
        return run_baseline_authorities(
            contract, requirement_set, base_revision=base_revision,
            run_bundle=(lambda: run_authority_bundle(bundle, workspace_path, state_root=state_root, validator_factory=validator_for))
            if bundle is not None else None,
            bundle_digest=bundle.digest if bundle is not None else None,
            judge_acceptance=judge(acceptance) if acceptance is not None else None,
            acceptance_digest=acceptance.digest if acceptance is not None else None,
            judge_examples=judge(examples) if examples is not None else None,
            examples_digest=examples.digest if examples is not None else None,
            judge_suite=judge_suite,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def close_requirements_with_authority_bundle_evidence(
    autonomy_cfg: Any, ledger: Any, requirement_set: Any, contract: Any, candidate_root: str, workspace_path: str, *,
    engine: Any, revision: Any, toolchain_declaration_mutable: bool, tree_binding: Any = None,
) -> List[Dict[str, Any]]:
    """The body is ``kriya/workflow/requirement_closers.py::close_requirements_with_authority_bundle_evidence`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.close_requirements_with_authority_bundle_evidence(
        _closer_deps(), autonomy_cfg, ledger, requirement_set, contract, candidate_root, workspace_path,
        engine=engine, revision=revision, toolchain_declaration_mutable=toolchain_declaration_mutable,
        tree_binding=tree_binding
    )


def close_requirements_by_api_preservation(
    ledger: Any, requirement_set: Any, contract: Any, candidate_root: str, workspace_path: str, *,
    candidate_paths: Iterable[str], revision: Any,
) -> List[Dict[str, Any]]:
    """The body is ``kriya/workflow/requirement_closers.py::close_requirements_by_api_preservation`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.close_requirements_by_api_preservation(
        _closer_deps(), ledger, requirement_set, contract, candidate_root, workspace_path,
        candidate_paths=candidate_paths, revision=revision
    )


def close_requirements_by_test_addition(
    ledger: Any, requirement_set: Any, contract: Any, candidate_root: str, workspace_path: str, *,
    base_test_identities: Optional[Iterable[str]] = None, candidate_test_identities: Optional[Iterable[str]] = None,
    revision: Any,
) -> List[Dict[str, Any]]:
    """The body is ``kriya/workflow/requirement_closers.py::close_requirements_by_test_addition`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.close_requirements_by_test_addition(
        _closer_deps(), ledger, requirement_set, contract, candidate_root, workspace_path,
        base_test_identities=base_test_identities, candidate_test_identities=candidate_test_identities,
        revision=revision
    )


def close_requirements_by_suite_preservation(
    autonomy_cfg: Any, ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    revision: Any, toolchain_declaration_mutable: bool, baseline_source: str, candidate_paths: Iterable[str] = (),
    java_home_override: Optional[str] = None, tree_binding: Any = None, contract: Any = None,
    goal: str = "", suite_baseline: Any = None, stability_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    baseline_suite_run: Optional[Callable[[Optional[Tuple[str, ...]]], Dict[str, Any]]] = None,
    run_baseline_policy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """The body is ``kriya/workflow/requirement_closers.py::close_requirements_by_suite_preservation`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.close_requirements_by_suite_preservation(
        _closer_deps(), autonomy_cfg, ledger, requirement_set, candidate_root, workspace_path, revision=revision,
        toolchain_declaration_mutable=toolchain_declaration_mutable, baseline_source=baseline_source,
        candidate_paths=candidate_paths, java_home_override=java_home_override, tree_binding=tree_binding,
        contract=contract, goal=goal, suite_baseline=suite_baseline, stability_cache=stability_cache,
        baseline_suite_run=baseline_suite_run, run_baseline_policy=run_baseline_policy
    )


def close_requirements_with_named_tests(
    autonomy_cfg: Any, ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    modified: Iterable[str], revision: Any, toolchain_declaration_mutable: bool,
    java_home_override: Optional[str] = None, tree_binding: Any = None, contract: Any = None,
) -> List[Dict[str, Any]]:
    """The body is ``kriya/workflow/requirement_closers.py::close_requirements_with_named_tests`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.close_requirements_with_named_tests(
        _closer_deps(), autonomy_cfg, ledger, requirement_set, candidate_root, workspace_path, modified=modified,
        revision=revision, toolchain_declaration_mutable=toolchain_declaration_mutable,
        java_home_override=java_home_override, tree_binding=tree_binding, contract=contract
    )


def _seed_grounded_loci(
    state: GenerationState, worktree_path: str, grounded_locations: Sequence[Mapping[str, Any]],
) -> None:
    """P2: the edit loci of a grounded failure carried into a plan-scope
    re-invocation (a fresh GenerationState) - each only when the file's raw
    revision in this sandbox equals the one the locator was observed on. A
    locus only decides which exact lines are shown (CONTEXT-EDIT-PROTOCOL
    windows); write authority stays the validated plan's."""
    seeded: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for location in grounded_locations:
        path = str(location.get("filepath") or "")
        line = location.get("line")
        normalized = os.path.normpath(path) if path else ""
        full = os.path.join(worktree_path, normalized)
        current = None
        if (normalized and not os.path.isabs(normalized) and not normalized.startswith("..")
                and isinstance(line, int) and line > 0 and os.path.isfile(full)):
            try:
                current = read_file_revision(full)
            except OSError:
                current = None
        entry = {"filepath": path, "line": line}
        if current is None or current != location.get("revision"):
            dropped.append(entry)
            continue
        state.edit_anchor_loci[normalized] = sorted(set(state.edit_anchor_loci.get(normalized, [])) | {line})
        seeded.append(entry)
    state.record_event(RunEvent(
        kind="recovery.grounded_loci", attempt=state.attempt_number, source="workflow.run_generation_workflow",
        authority=EventAuthority.AUTHORITATIVE,
        message=f"plan-scope recovery: {len(seeded)} grounded locus/loci carried, {len(dropped)} stale dropped",
        details={"seeded": seeded, "dropped": dropped},
    ))


def _oracle_base_revision(candidate_root: str, workspace_path: str) -> Optional[str]:
    """The body is ``kriya/workflow/requirement_closers.py::_oracle_base_revision`` (E4); its collaborators
    are resolved here, at call time."""
    return closers._oracle_base_revision(_closer_deps(), candidate_root, workspace_path)


def _run_committed_paths(workspace_path: str) -> Tuple[Optional[str], Optional[str], List[str]]:
    """(run_id, base revision, every path the owning run has committed to the
    real workspace so far), from the RunRecord's settled COMMITTED cycles and
    each transaction's own commit evidence (byte state before != after) -
    never from a workflow's reported file list. Raises when a committed
    cycle's evidence cannot be read: the history is then unknown."""
    from kriya.control.run_coordinator import owning_run
    from kriya.control.run_record import COMMIT_COMMITTED
    from kriya.workflow.edit_safety import commit_evidence_dir, load_commit_evidence

    context = owning_run(workspace_path)
    if context is None or context._lease.record is None:
        return None, None, []
    paths: List[str] = []
    for cycle in context._lease.record.commits:
        if cycle.get("result") != COMMIT_COMMITTED:
            continue
        evidence = load_commit_evidence(os.path.join(
            commit_evidence_dir(workspace_path), f"{cycle['transaction_id']}.json"))
        for operation in evidence.operations:
            if operation.get("before") != operation.get("after") and operation["target_path"] not in paths:
                paths.append(operation["target_path"])
    return context.run_id, context.base_revision, paths


def _load_baseline_environment_identity() -> Callable[..., Any]:
    # The suite-preservation closer's function-local import (kriya.workflow.baseline_policy, in the kernel's
    # all-import cycle), made where the body made it.
    from kriya.workflow.baseline_policy import baseline_environment_identity

    return baseline_environment_identity


def _load_authority_bundle() -> Tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any]]:
    # The authority-bundle closer's function-local import (a non-public AUTHORITY module), made where the body made it.
    from kriya.workflow.authority_bundle import (
        bound_authority_bundle,
        close_requirements_with_authority_bundle,
        run_authority_bundle,
    )

    return bound_authority_bundle, close_requirements_with_authority_bundle, run_authority_bundle


def _closer_deps() -> "closers.CloserDeps":
    """The requirement closers' collaborators (kriya/workflow/requirement_closers.py), resolved through this module NOW
    (at call time), so a test patch of _run_committed_paths, compute_workspace_content_hash or logger here
    reaches the moved closers."""
    return closers.CloserDeps(
        logger=logger, run_committed_paths=_run_committed_paths,
        compute_workspace_content_hash=compute_workspace_content_hash, captured_baseline=captured_baseline,
        attribute_suite_result=attribute_suite_result, post_environment_identity=post_environment_identity,
        load_baseline_environment_identity=_load_baseline_environment_identity,
        load_authority_bundle=_load_authority_bundle,
    )


def close_requirements_by_mutation_scope(
    ledger: Any, requirement_set: Any, candidate_root: str, workspace_path: str, *,
    candidate_paths: Iterable[str], revision: Any,
) -> List[Dict[str, Any]]:
    """The body is ``kriya/workflow/requirement_closers.py::close_requirements_by_mutation_scope`` (E4); its collaborators
    are resolved here, at call time."""
    return closers.close_requirements_by_mutation_scope(
        _closer_deps(), ledger, requirement_set, candidate_root, workspace_path, candidate_paths=candidate_paths,
        revision=revision
    )


def _terminal_contract_authorizations(
    grounding_goal: str, structured_plan: Any, current_subtask_id: Optional[str], state: GenerationState,
) -> List[Any]:
    """The contract authorizations in force for this subtask at the terminal
    boundary: DIRECT ones derived from the raw goal (CORR-016; none for a
    plain legacy run) plus PRD-023 HUMAN approvals. The terminal API recheck
    and the PRD-029 registry transition use exactly this list."""
    return [
        authorization
        for authorization in derive_direct_contract_authorizations(grounding_goal, structured_plan)
        if authorization.legal_scope.get("subtask_id") == current_subtask_id
    ] + [
        # PRD-023: a human-approved change passed the per-attempt gate with
        # the same authority; never re-rejected here.
        authorization for authorization in state.human_contract_authorizations
        if authorization.legal_scope.get("subtask_id") == current_subtask_id
    ]


def _restore_original_bytes(path: str, data: bytes) -> None:
    """Atomically put a file back to its exact original bytes (the abort path
    without worktree isolation), keeping its mode."""
    commit_revision_grounded_file(path, display_text(data), read_file_revision(path), content_bytes=data)


def _original_revision(state: GenerationState, filepath: str) -> str:
    """The raw-byte revision the run read ``filepath`` at (FILE-INTEGRITY-
    CONTRACT-001); a path captured only as text (equal for valid UTF-8 LF
    files) falls back to its text revision."""
    if filepath in state.all_original_raw:
        return raw_digest(state.all_original_raw[filepath] or b"")
    return content_revision(state.all_original_contents.get(filepath, ""))


def _direct_terminal_writes(worktree_path: str, workspace_path: str, state: GenerationState) -> List[Any]:
    """The direct terminal batch, exactly as the terminal apply materializes it."""
    return materialize_candidate(worktree_path, workspace_path, [
        CandidateFile(
            relpath=filepath,
            expected_base_revision=_original_revision(state, filepath),
        )
        for filepath in sorted(state.all_files_written)
    ])


def _bind_direct_candidate(worktree_path: str, workspace_path: str, state: GenerationState) -> Optional[Any]:
    """The binding of the direct terminal batch; None when it cannot be
    materialized (the commit then refuses it)."""
    try:
        return bind_candidate(_direct_terminal_writes(worktree_path, workspace_path, state), workspace_path)
    except CandidateMaterializationError:
        return None


def _captured_originals(state: GenerationState) -> Dict[str, Optional[bytes]]:
    """relpath -> the bytes of every written file as captured before the
    attempt wrote it (None: the file did not exist), from the attempt's
    raw capture first (FILE-INTEGRITY-CONTRACT-001), else its text capture."""
    originals: Dict[str, Optional[bytes]] = {}
    for relpath in sorted(state.all_files_written):
        if relpath in state.all_original_raw:
            # The raw capture is exact: None = did not exist, b"" = existed and
            # was empty (an __init__.py; review F1 - never folded into None).
            originals[relpath] = state.all_original_raw[relpath]
        else:
            # The text capture alone cannot tell an empty file from a missing
            # one ("" for both): only a non-empty text is a known original.
            text = state.all_original_contents.get(relpath)
            originals[relpath] = text.encode("utf-8") if text else None
    return originals


def _run_static_analysis_gate(
    cfg: Any, state: GenerationState, *, worktree_path: str, workspace_path: str, run_id: str, unit_id: str,
) -> None:
    """PRD-031A at the direct/milestone pre-apply boundary: the same
    StaticAnalysisService the enforce TerminalGateService uses."""
    # E4: the body is kriya/workflow/stage_apply.py::run_static_analysis_gate; this module's collaborators are passed
    # at call time, so a patch of a name here (``_direct_terminal_writes``, ...) reaches it.
    run_static_analysis_gate(
        cfg, state, worktree_path=worktree_path, workspace_path=workspace_path, run_id=run_id, unit_id=unit_id,
        StaticAnalysisService=StaticAnalysisService, StaticAnalysisCandidate=StaticAnalysisCandidate, banner=banner,
        _direct_terminal_writes=_direct_terminal_writes, _captured_originals=_captured_originals,
    )


def _run_semantic_safety_gate(state: GenerationState, *, worktree_path: str, workspace_path: str) -> None:
    """BACKEND-READINESS-V2 at the direct/milestone pre-apply boundary: the exception-masking rule on the exact batch
    the terminal apply commits (the enforce terminal's ``semantic_safety`` gate, same decision), against the bytes the
    run first read. It judges the final state, so a masking result is refused whichever route left it in the candidate.
    A finding is a deterministic stop of the same kind as the staging guard's: fix the cause, do not widen a catch."""
    # E4: the body is kriya/workflow/stage_apply.py::run_semantic_safety_gate (collaborators passed at call time).
    run_semantic_safety_gate(
        state, worktree_path=worktree_path, workspace_path=workspace_path, _direct_terminal_writes=_direct_terminal_writes,
    )


# PRD-032: WORKSPACE_COMMIT_NOT_COMPLETED (kriya/workflow/outcome.py) is the
# environment_failure prefix of a terminal commit that did not commit
# (failure_category ``workspace_commit_failed``).


def _raise_terminal_commit_stop(state: GenerationState, outcome: Any) -> None:
    """PRD-032: a verified candidate whose terminal commit did not commit
    (revision conflict, I/O failure rolled back, uncertain, refused static-
    analysis evidence, unpersisted intent) is a deterministic stop, never a
    Developer retry: regeneration cannot change the workspace's revision, the
    commit evidence or a refused guard. The enforce terminal reports the same
    payload (commit_service.commit_verified_candidate)."""
    payload = outcome.failure_payload()
    reason = payload["reason_code"] or "WORKSPACE_COMMIT_FAILED"
    message = f"{WORKSPACE_COMMIT_NOT_COMPLETED}: {reason} (workspace {outcome.workspace_state}): {outcome.error}"
    state.environment_failure = message
    state.terminal_commit_failure = payload
    state.record_event(RunEvent(
        kind="workspace_commit.failed", attempt=state.attempt_number, source="terminal_commit",
        authority=EventAuthority.AUTHORITATIVE, message=f"the verified candidate was not committed: {reason}",
        details={"reason_code": reason, "workspace_state": outcome.workspace_state,
                 "commit_transaction_id": outcome.transaction_id},
    ))
    failure = Failure(
        type="workspace_commit", message=message, raw_output=message,
        source="terminal_commit", authority="deterministic", attempt=state.attempt_number,
        diagnostics={"reason_code": reason, "workspace_state": outcome.workspace_state,
                     "commit_transaction_id": outcome.transaction_id},
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _record_approval_decision(state: GenerationState, outcome: str, triggers: List[str]) -> None:
    """PRD-033: one typed record of a human-approval decision (approved,
    rejected, or unavailable when no approval callback exists)."""
    state.record_event(RunEvent(
        kind="approval.decision", attempt=state.attempt_number, source="workflow.approval_gate",
        authority=EventAuthority.AUTHORITATIVE, message=f"human approval: {outcome}",
        details={"outcome": outcome, "triggers": list(triggers)},
    ))


def _raise_contract_registry_stop(state: GenerationState, outcome: Any) -> None:
    """PRD-029: a contract transition that cannot be made exactly is a
    deterministic stop, never a retryable generation failure."""
    reason = outcome.reason_code
    message = f"{reason}: {outcome.error}" if outcome.error is not None else reason
    state.environment_failure = message
    failure = Failure(
        type="contract_registry", message=message, raw_output=message,
        source="contract_registry_gate", authority="deterministic",
        attempt=state.attempt_number, diagnostics={"reason_code": reason},
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _load_full_suite_evidence_for_reuse() -> Callable[..., Any]:
    # The terminal apply's function-local import (kriya.workflow.baseline_policy, in the kernel's all-import cycle),
    # made where the body made it (kriya/workflow/stage_apply.py::apply_terminal_candidate).
    from kriya.workflow.baseline_policy import full_suite_evidence_for_reuse

    return full_suite_evidence_for_reuse


def _load_lesson_extraction() -> Tuple[Any, Any, Any]:
    # The lesson extraction's function-local imports (kriya.knowledge.staging and the live-failure channel: all-import
    # cycle, non-public KNOWLEDGE), made where the body made them (kriya/workflow/stage_apply.py::extract_lessons).
    from kriya.knowledge import staging as knowledge_staging
    from kriya.knowledge.channels.live_failure import LiveFailureChannel, LiveFailureContext

    return knowledge_staging, LiveFailureChannel, LiveFailureContext


class WorkflowEngine:
    """Orchestrates multi-agent pipelines and auto-debugging loops (Quality Gates)."""

    def __init__(self, kernel: Kernel, llm_client: LLMClient) -> None:
        self.kernel = kernel
        self.llm = llm_client
        # FS-1C2 B2-a: the operator's acceptance file for this run, bound
        # before generation (`kriya generate --acceptance`); None = none.
        self.acceptance: Any = None
        # FS-1C2 B3: the operator's approval of that suite for GENERAL
        # requirements (`--acceptance-approval`), bound at the same time.
        self.acceptance_approval: Any = None
        # GR-R1A: the operator's explicit requirement contract, bound by the CLI
        # before any model call (kriya/workflow/requirement_contract.py).
        self.requirement_contract: Any = None
        # BACKEND-READINESS-004 (D3): the operator's sealed requirement dispositions
        # (`--requirement-disposition`), bound by the CLI before any model call
        # (kriya/workflow/requirement_disposition.py); None = none.
        self.requirement_dispositions: Any = None
        # PRD-024: the last applied candidate's terminal full-suite result
        # (full_suite_evidence_for_reuse), offered to the next run as its
        # baseline; reused only if it describes that run's exact start.
        self._prior_full_suite_evidence: Optional[Dict[str, Any]] = None
        # Per-role model config (kriya/config/config.py::AgentRolesConfig) - each
        # optional, defaulting to None (LLMClient's own primary model) when a project
        # never configures agent_llms, so this is a zero-behavior-change default.
        # Developer deliberately isn't here - it stays on the top-level llm/llm_chain,
        # escalated by the existing quality-gate retry loop below, not this mechanism.
        roles = kernel.config.agent_llms
        # Planner max-token residual (2026-09-19): BaseAgent.max_output_tokens
        # (unset here previously) is exactly the existing "stage-specific
        # configured budget" mechanism - passing it once, at construction,
        # fixes every self.planner.run(...) call site at once (this class's
        # own plain/Legacy call below AND WorkflowController's structured-
        # planning calls, kriya/workflow/workflow_controller.py, which
        # already separately derive and pass config.llm.planner_max_tokens
        # as an explicit per-call max_tokens_override - that explicit
        # override still takes precedence unchanged, per BaseAgent.run()'s
        # own `max_tokens_override if max_tokens_override is not None else
        # self.max_output_tokens` precedence). Without this, an ordinary
        # (non-WorkflowController) Planner call fell through to LLMClient.
        # complete()'s own general config.llm.max_tokens instead of the
        # Planner-specific budget - a real, silent mismatch between the
        # configured stage-specific value and what actually reached the
        # model. MilestonePlannerAgent below deliberately keeps its own
        # existing, separately-documented "no dedicated agent_llms entry,
        # falls back to LLMClient's own default" behavior unchanged - it is
        # a distinct agent, not the ordinary Planner path this fixes.
        self.planner = PlannerAgent(
            "planner", llm_client, roles.planner.llm, roles.planner.llm_chain,
            max_output_tokens=roles.planner.max_output_tokens or kernel.config.llm.planner_max_tokens,
        )
        # No dedicated agent_llms entry (unlike the roles above) - this agent is
        # new (kriya/workflow/milestones.py's orchestrator), and adding a config
        # schema field is out of scope for this feature; falls back to
        # LLMClient's own default model like DeveloperAgent already does below.
        self.milestone_planner = MilestonePlannerAgent("milestone_planner", llm_client)
        self.architect = ArchitectAgent("architect", llm_client, roles.architect.llm, roles.architect.llm_chain,
            max_output_tokens=roles.architect.max_output_tokens)
        self.developer = DeveloperAgent("developer", llm_client)
        self.reviewer = ReviewerAgent("reviewer", llm_client, roles.reviewer.llm, roles.reviewer.llm_chain,
            max_output_tokens=roles.reviewer.max_output_tokens)
        self.run_verifier = RunVerifierAgent("run_verifier", llm_client, roles.run_verifier.llm, roles.run_verifier.llm_chain,
            max_output_tokens=roles.run_verifier.max_output_tokens)
        self.skill_gap_agent = SkillGapAgent("skill_gap", llm_client, roles.skill_gap.llm, roles.skill_gap.llm_chain,
            max_output_tokens=roles.skill_gap.max_output_tokens)
        self.spec_compliance = SpecComplianceAgent("spec_compliance", llm_client, roles.spec_compliance.llm, roles.spec_compliance.llm_chain,
            max_output_tokens=roles.spec_compliance.max_output_tokens)
        # MA1 of the control-plane implementation plan (kriya/workflow/triage.py) -
        # deliberately not constructed alongside the roles above: this isn't an
        # "agent" (no LLM call happens in it yet, see EngineeringTriageService's
        # own docstring), it's a deterministic classifier kept in shadow mode
        # (kriya/config/config.py::EngineeringTriageConfig) until MA2 lets its
        # result start influencing anything.
        self.engineering_triage = EngineeringTriageService(kernel=kernel)
        # MA4.9 (control-plane implementation plan) - audit-only. See
        # _audit_approval_rules below; never consulted for enforcement.
        # MA4.15 - hands in the real AutonomyConfig.sensitive_paths instead
        # of ExecutionPolicy's hardcoded default, the one real caller with
        # config access naturally in scope (see execution.py's own
        # docstring for why the other 5 real callers still don't).
        self.execution_policy = ExecutionPolicy(sensitive_path_patterns=kernel.config.autonomy.sensitive_paths)

    def _trace_run_events(self, state: GenerationState) -> List[Dict[str, Any]]:
        """The run's events for the trace, after moving any automatic budget
        expansion recorded by a model client into them (PRD-016;
        kriya/workflow/run_lifecycle.py::trace_run_events)."""
        return trace_run_events(
            state, model_clients=(self.llm, self.planner.llm, self.architect.llm, self.developer.llm, self.reviewer.llm),
            developer_llm=self.developer.llm, role_metrics_of=_role_metrics_of,
        )

    def _stage_deps(self) -> StageDeps:
        """The extracted stages' collaborators, resolved through this module NOW (at call time), so a test patch
        of a name here (``logger``, ...) reaches the moved code (kriya/workflow/stage_context.py)."""
        return StageDeps(
            logger=logger, config=self.kernel.config, new_state=GenerationState,
            egress_authority_details=egress_authority_details,
            primary_model_runtime_details=_primary_model_runtime_details,
            role_independence_details=_role_independence_details,
            trace_events=self._trace_run_events, static_analysis_fields=static_analysis_result_fields,
            required_verification_evidence=_build_required_verification_evidence,
            deterministic_gate_evidence=deterministic_gate_evidence,
        )

    def _work_unit_deps(self) -> StageDeps:
        """``_stage_deps()`` plus the design stages' collaborators (kriya/workflow/work_unit_runner.py), all resolved
        NOW (at call time): this engine's agents and helpers, and this module's names, so a test patch of
        ``RepositoryAnalyzer``, ``retrieve_graph_context``, ``include_response_construction_owners`` or ``logger``
        here reaches the moved stages. The names the stage bodies imported lazily (non-public or all-import-cycle
        modules a stage module may not import) are imported here, from the same modules."""
        from kriya.memory.embedding import configured_client, run_deadline
        from kriya.memory.vector import LocalVectorStore
        from kriya.skills.skill import (
            SkillEngine,
            fact_match,
            find_conflict_resolution,
            is_accidental_shared_skills_write,
            is_version_supported,
            mentions_term,
            record_conflict_resolution,
        )
        from kriya.tools.knowledge import KnowledgeGuard, extract_library_versions
        from kriya.tools.web import fetch_url_text

        return dataclasses.replace(
            self._stage_deps(),
            registry=self.kernel.registry, planner=self.planner, architect=self.architect,
            skill_gap_agent=self.skill_gap_agent, llm=self.llm, engineering_triage=self.engineering_triage,
            approve_web_lookup=self._approve_web_lookup, authorize_action=self._authorize_action,
            RepositoryAnalyzer=RepositoryAnalyzer, retrieve_graph_context=retrieve_graph_context,
            include_response_construction_owners=include_response_construction_owners,
            unresolved_knowledge_report=unresolved_knowledge_report, _log_phase_banner=_log_phase_banner,
            KnowledgeGuard=KnowledgeGuard, extract_library_versions=extract_library_versions, SkillEngine=SkillEngine,
            is_accidental_shared_skills_write=is_accidental_shared_skills_write, fact_match=fact_match,
            mentions_term=mentions_term, is_version_supported=is_version_supported,
            find_conflict_resolution=find_conflict_resolution, record_conflict_resolution=record_conflict_resolution,
            fetch_url_text=fetch_url_text, configured_client=configured_client, run_deadline=run_deadline,
            LocalVectorStore=LocalVectorStore,
            CandidatePrompts=CandidatePrompts, RetrievalLimits=RetrievalLimits,
            _reserve_graph_context_budget=_reserve_graph_context_budget, agent_request_capacity=agent_request_capacity,
            allocation_window=allocation_window, candidate_model=candidate_model,
            fit_planner_request=fit_planner_request, retrieval_limits_for=retrieval_limits_for,
            STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS=STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS,
            build_structured_plan_repair_prompt=build_structured_plan_repair_prompt,
            classify_structured_plan_parse_issue=classify_structured_plan_parse_issue,
            planner_outcome_for_completeness=planner_outcome_for_completeness,
            planner_response_model=planner_response_model, record_planner_outcome=record_planner_outcome,
            _filter_misattributed_extraction=_filter_misattributed_extraction,
            _sanitize_for_flat_file_line=_sanitize_for_flat_file_line,
            _scoped_skill_gap_description=_scoped_skill_gap_description,
            _skill_staleness_warning=_skill_staleness_warning,
            _split_rules_by_verification=_split_rules_by_verification,
            _stage_skill_conflicts=_stage_skill_conflicts, _write_skill_extraction=_write_skill_extraction,
            decide_localization=decide_localization, developer_goal_facts=developer_goal_facts,
            find_ownership_findings=find_ownership_findings, findings_prompt_block=findings_prompt_block,
            grounded_owner_candidates=grounded_owner_candidates,
            owner_candidates_prompt_block=owner_candidates_prompt_block,
            parse_ownership_justifications=parse_ownership_justifications, settle_findings=settle_findings,
            CALLABLE_KINDS=CALLABLE_KINDS, InferenceDeadlineError=InferenceDeadlineError,
            ActionRequest=ActionRequest, ActionType=ActionType, PolicyDecision=PolicyDecision,
            PolicyDeniedError=PolicyDeniedError,
        )

    def _late_stage_deps(self) -> StageDeps:
        """``_stage_deps()`` plus the review, approval/apply and lesson stages' collaborators (kriya/workflow/
        stage_review.py, stage_apply.py), all resolved NOW (at call time): this engine's agents and helpers and this
        module's names, so a test patch of ``delete_checkpoint``, ``commit_terminal_candidate``,
        ``derive_contract_transition``, ``_direct_terminal_writes`` or ``logger`` here reaches the moved stages. The
        names the stage bodies imported lazily from all-import-cycle modules are loaders, called where the bodies
        imported them."""
        return dataclasses.replace(
            self._stage_deps(),
            llm=self.llm, _log_phase_banner=_log_phase_banner, InferenceDeadlineError=InferenceDeadlineError,
            reviewer=self.reviewer, developer=self.developer, audit_approval_rules=self._audit_approval_rules,
            keep_full_suite_evidence=self._keep_full_suite_evidence,
            load_full_suite_evidence_for_reuse=_load_full_suite_evidence_for_reuse,
            load_lesson_extraction=_load_lesson_extraction,
            REVIEW_SECTION_HEADER=REVIEW_SECTION_HEADER, banner=banner, commit_guard=commit_guard,
            review_requests=review_requests, build_reviewer_verified_evidence=build_reviewer_verified_evidence,
            build_candidate_diff_context=build_candidate_diff_context,
            ownership_review_evidence=ownership_review_evidence,
            ContextBudgetUnsatisfiableError=ContextBudgetUnsatisfiableError,
            _final_review_error_code=_final_review_error_code, _record_review_fit=_record_review_fit,
            _review_refit_recorder=_review_refit_recorder, _record_approval_decision=_record_approval_decision,
            _restore_original_bytes=_restore_original_bytes, _direct_terminal_writes=_direct_terminal_writes,
            _terminal_contract_authorizations=_terminal_contract_authorizations,
            _raise_contract_registry_stop=_raise_contract_registry_stop,
            _raise_terminal_commit_stop=_raise_terminal_commit_stop, delete_checkpoint=delete_checkpoint,
            commit_terminal_candidate=commit_terminal_candidate, derive_contract_transition=derive_contract_transition,
        )

    def _keep_full_suite_evidence(self, evidence: Optional[Dict[str, Any]]) -> None:
        """PRD-024: what the terminal apply keeps for the next run's baseline (kriya/workflow/stage_apply.py)."""
        self._prior_full_suite_evidence = evidence

    def _audit_approval_rules(
        self, files_written: Iterable[str], workspace_path: str,
        control: Optional[WorkflowControlContext],
    ) -> None:
        """MA4.9 - audit-only ExecutionPolicy consultation, mirroring every
        prior MA4 integration exactly: can never affect the real
        need_human_approval decision computed right after this call (MA2's
        own logic, completely untouched) - any exception raised here is
        caught and logged, never propagated, and the decision is only
        logged, never branched on.

        This is the one real call site where a WorkflowControlContext
        (pairing a real EngineeringRoute with its resolved ProcessProfile)
        is already in scope, so kriya/policy/execution.py's stage 7
        (_check_approval_rules) has real, non-None input to reason about -
        every other real MA4 caller (validate.py, edit_safety.py, web.py,
        worktree.py) constructs an ActionRequest with no engineering_route/
        process_profile at all, so stage 7 is structurally inert for them.

        Deliberately does NOT pass workspace_path on this ActionRequest,
        even though it's available - stage 2 (_check_filesystem, MA4.5)
        runs BEFORE stage 7 in the fixed order and would immediately ALLOW
        any in-workspace WRITE_FILE via workspace-containment, starving
        stage 7 of ever actually running for this call. Filesystem-
        containment auditing is already covered independently at MA4.5's
        own real call site (edit_safety.py's atomic_write_file); this call
        exists specifically to give stage 7 real signal, not to duplicate
        stage 2's.

        One representative WRITE_FILE request per file actually written
        this attempt (not the whole batch collapsed into one call) - keeps
        the audit signal attributable per file, mirroring how MA2's own
        sensitive-path check above already loops `state.all_files_written`
        the same way.

        MA4.15 - gated on execution_policy.enabled (default True, matching
        this call's exact behavior before this flag existed) so a project
        can turn this specific audit signal off without needing config it
        doesn't otherwise use."""
        if control is None or not self.kernel.config.execution_policy.enabled:
            return
        for filepath in files_written:
            try:
                full_path = os.path.join(workspace_path, filepath)
                result = self.execution_policy.evaluate(ActionRequest(
                    action_type=ActionType.WRITE_FILE, target=full_path,
                    engineering_route=control.engineering_route, process_profile=control.process_profile,
                ))
                logger.debug(
                    "MA4 policy audit (not enforced): WRITE_FILE (approval-rules pass) '%s' -> %s (%s)",
                    filepath, result.decision.value, result.reason_code,
                )
            except Exception as e:
                logger.debug("MA4 policy audit call failed (ignored, audit-only): %s", e)

    async def _authorize_action(
        self,
        request: ActionRequest,
        *,
        diffs_to_show: Optional[List[Dict[str, str]]] = None,
        approval_callback: Optional[Callable[[List[Dict[str, str]], str], Any]] = None,
        enforce: bool = False,
    ) -> Optional[PolicyResult]:
        """MA4.13 - the general-purpose policy consultation helper (design
        doc's "_authorize_action" / "PolicyDeniedError"), distinct from
        MA4.9's narrower, single-purpose _audit_approval_rules above.

        `enforce` defaults to False and EVERY real call site in today's
        codebase calls this with enforce left at its default - identical in
        effect to every prior MA4.3-4.12 integration: evaluate, log,
        return, never branch on the result, never raise. MA4.15's config
        wiring is what will eventually let a real caller pass enforce=True;
        until then this method's enforce=True branch is real, working code
        exercised only by this module's own unit tests, not by any live
        Kriya run - built now, deliberately dormant, so it doesn't need to
        be invented under pressure once enforcement is actually turned on
        somewhere.

        When enforce=True: DENY raises PolicyDeniedError immediately (no
        callback - DENY means no, not "ask a human"). REQUIRE_APPROVAL
        invokes `approval_callback` using the EXACT shape Kriya's one real
        approval mechanism already uses everywhere else in this file
        (`Callable[[List[Dict[str, str]], str], Any]`, awaited only if it
        returns a coroutine - see run_generation_workflow's own
        `approved = approval_callback(...); if asyncio.iscoroutine(approved): ...`
        a few hundred lines below) and raises PolicyDeniedError if not
        approved or if no callback was supplied at all. ALLOW and
        ALLOW_SANDBOXED never raise - ALLOW_SANDBOXED's actual sandboxing
        is ProcessController's job (defense in depth), not this method's.

        A broken/misconfigured policy engine (evaluate() itself raising)
        never blocks the caller regardless of `enforce` - that failure mode
        is about this new subsystem being broken, not about a real policy
        decision, and is logged and treated as "no verdict available"
        (returns None) rather than either failing open or closed on
        something that isn't actually a decision."""

        try:
            result = self.execution_policy.evaluate(request)
        except Exception as e:
            logger.debug("MA4.13 _authorize_action: policy evaluation failed (ignored): %s", e)
            return None

        # MA4.14 - structured telemetry (kriya/policy/telemetry.py) in place
        # of a plain debug string: same log level and audit-only intent,
        # but a stable, redacted shape a future consumer can key off by
        # field instead of parsing text. Building the record itself is
        # exception-safe on its own terms - a redaction bug must never be
        # the reason a real policy decision fails to be logged, let alone
        # the reason _authorize_action's own caller sees an error.
        try:
            logger.debug("MA4 policy decision: %s", build_decision_record(request, result, enforced=enforce).to_json())
        except Exception as e:
            logger.debug("MA4.14 telemetry record build failed (ignored): %s", e)

        if not enforce:
            return result

        if result.decision == PolicyDecision.DENY:
            raise PolicyDeniedError(request=request, result=result)

        if result.decision == PolicyDecision.REQUIRE_APPROVAL:
            if not approval_callback:
                raise PolicyDeniedError(request=request, result=result)
            approved = approval_callback(diffs_to_show or [], result.explanation)
            if asyncio.iscoroutine(approved):
                approved = await approved
            if not approved:
                raise PolicyDeniedError(request=request, result=result)

        return result

    async def _approve_web_lookup(
        self, terms: List[str], base_url: str,
        web_lookup_query_callback: Optional[Callable[[List[str], str], Any]]
    ) -> bool:
        """Gates every outbound live-lookup search behind explicit authorization -
        either the persistent autonomy.web_lookup_auto_approve opt-in, or a
        real-time callback shown the exact terms and base_url about to be
        searched. Fails closed: no callback and no opt-in means the query never
        fires at all. A query's CONTENT is already hard-restricted elsewhere
        (bare technology-name strings only, never goal/design/code/error text) -
        this is a separate, additional gate on WHEN it's allowed to leave the
        machine at all. Confirmed live during a fresh-install investigation that,
        before this gate existed, a non-interactive (-y) run already fired the
        outbound query and then silently discarded the result - the query had
        already left the machine with zero human visibility, for zero benefit."""
        if self.kernel.config.autonomy.web_lookup_auto_approve:
            from kriya.workflow.outbound_lookup import (
                UnsafeLookupTerm,
                is_known_public_term,
            )
            try:
                all_terms_are_public = all(is_known_public_term(
                    term, self.kernel.config.search.public_terms,
                ) for term in terms)
            except UnsafeLookupTerm as exc:
                logger.warning(f"Unattended web lookup blocked by egress sanitizer: {exc}")
                return False
            if all_terms_are_public:
                return True
            logger.warning(
                "Unattended web lookup blocked: at least one term is not in Kriya's "
                "public technology catalog or search.public_terms. Explicit per-query "
                "approval is required."
            )
        if not web_lookup_query_callback:
            return False
        try:
            approved = web_lookup_query_callback(terms, base_url)
            if asyncio.iscoroutine(approved):
                approved = await approved
            return bool(approved)
        except Exception as ex:
            logger.warning(f"web_lookup_query_callback failed, skipping live lookup: {ex}")
            return False

    def workspace_refusal_result(self, assessment: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """PRD-008: the structured result when a fresh run is refused because
        the workspace's prior commit state is uncertain (see
        kriya/control/commit_state.py). No model call has happened."""
        return assessment.to_payload(run_id=arguments.get("trace_id_override"))

    @coordinated_mutation
    @record_run_exceptions
    async def run_generation_workflow(
        self, 
        goal: str, 
        workspace_path: str,
        step_callback: Optional[Callable[[str, str], None]] = None,
        approval_callback: Optional[Callable[[List[Dict[str, str]], str], Any]] = None,
        stream_callback: Optional[Callable[[str, str], None]] = None,
        error_context: Optional[str] = None,
        knowledge_risk_confirmed: bool = False,
        skill_gap_callback: Optional[Callable[[str, List[str]], Any]] = None,
        skill_conflict_callback: Optional[Callable[[str, str, str, str, str], Any]] = None,
        web_lookup_callback: Optional[Callable[[List[Dict[str, str]]], Any]] = None,
        web_lookup_query_callback: Optional[Callable[[List[str], str], Any]] = None,
        resume: bool = False,
        resume_id: Optional[str] = None,
        trace_id_override: Optional[str] = None,
        milestone_group_id: Optional[str] = None,
        milestone_index: Optional[int] = None,
        milestone_total: Optional[int] = None,
        # TRACE-ENFORCE-SUBTASK-LINKAGE-001: the enforce run this call is a
        # subtask of (<run_id>.enforce), recorded on its traces.db row.
        enforce_run_id: Optional[str] = None,
        supplementary_context: str = "",
        planned_source_files: Optional[Sequence[str]] = None,
        reference_context: str = "",
        recovery_contract_block: str = "",
        grounded_locations: Optional[Sequence[Mapping[str, Any]]] = None,
        established_files: Optional[List[str]] = None,
        predetermined_plan: Optional[str] = None,
        predetermined_design: Optional[str] = None,
        predetermined_architect_files: Optional[List[str]] = None,
        protected_source_file: Optional[str] = None,
        allowed_write_relpaths: Optional[List[str]] = None,
        authorized_semantic_regions: Optional[List["AuthorizedSemanticRegion"]] = None,
        write_scope_mode: Optional[WriteScopeMode] = None,
        required_verification: Optional[List[Dict[str, Any]]] = None,
        runtime_verification_required: Optional[bool] = None,
        strict_spec_compliance: bool = False,
        strict_dependency_index: bool = False,
        resolved_knowledge_coordinates: Optional[List[str]] = None,
        skill_engine_override: Optional[Any] = None,
        execution_scope: str = "",
        grounding_goal: str = "",
        # ENFORCE-IDENTICAL-WRITE-COMPLETION-001: True for an upstream owner the
        # enforce controller reopened for a failed verification unit; its
        # acceptance needs a byte change (workflow_controller.py owner_accepted),
        # so an identical regeneration is a typed no-progress stop, never a
        # repairable no-change refusal.
        reopened_owner: bool = False,
        migration_resolution: Optional["MigrationResolution"] = None,
        structured_plan: Optional["EngineeringPlan"] = None,
        current_subtask_id: Optional[str] = None,
        obligation_ledger: Optional["ObligationLedger"] = None,
        completed_subtask_ids: Optional[FrozenSet[str]] = None,
        deterministic_failure_diagnostics: Optional["DeterministicFailureDiagnosticStore"] = None,
        work_unit: Optional[WorkUnitInvocation] = None,
        requirements_from_goal: bool = True,
    ) -> Dict[str, Any]:
        """Runs the complete Planner -> Architect -> Developer -> Quality Gates -> Reviewer loop (supporting streaming).

        requirements_from_goal (PRD-020): False when ``goal`` is Kriya's own
        wording rather than the user's (``kriya fix``); no original
        requirement set is derived then.

        work_unit (PRD-008A): None means this call is a new user intent - a
        direct goal - so it becomes a one-unit ExecutionPlan and runs through
        kriya/workflow/plan_executor.py's execute_plan(), which then calls
        back here with the unit's WorkUnitInvocation. A caller executing a
        unit of an outer plan (the milestone driver, the enforce subtask
        loop) passes its WorkUnitInvocation and this method runs exactly that
        unit. Either way the pipeline below is the same generation primitive.

        planned_source_files: GRAPHIFY-OVERSIZE-REQUEST-001 - a bounded enforce
        subtask's planned files that exist on disk. Their current source is
        rendered by the attempt as a budgeted, optional Developer section
        (attempt.py::_planned_source_context), never folded verbatim into
        supplementary_context (mandatory text that no request fit can
        shrink).

        supplementary_context: raw text folded into convention_prompt BEFORE
        skills/RAG content is appended (see the `convention_prompt = ""` init
        below), so it reaches Planner (plan_prompt), Architect (design_prompt),
        AND Developer's initial full-set generation (ctx.skills_prompt) alike -
        the one accumulator all three actually share. Appending only to `goal`
        does NOT achieve this: Architect only ever sees Planner's own `plan`
        output, never the raw goal again (design_prompt = f"Plan:\n{plan}\n\n..."),
        so anything Planner doesn't choose to transcribe into its own plan text
        is invisible to Architect - confirmed live as the actual mechanism
        behind a milestone-decomposition failure where Architect explicitly
        wrote "I'll assume there's an existing protocol class that has
        encode/decode methods" instead of using the real, already-built one.
        Exists for kriya/workflow/milestones.py's run_milestones(), which needs
        to ground a later milestone on files earlier milestones already built,
        regardless of whether Graph RAG's own relevance scoring surfaces them.
        Empty string (the default) preserves today's exact behavior for every
        other caller.

        reference_context: learned reference knowledge (``kriya learn``),
        retrieved once by the caller from the user's own words
        (kriya/memory/learned_knowledge.py; the ``generate`` CLI, direct and
        milestone plans alike, and forwarded to enforce subtasks). Shown to
        Planner, Architect and Developer inside the untrusted-reference
        fence, and never joined to `goal`:
        the goal is the user's words, the only source of requirement,
        mutation-scope, contract and expected-exit authority
        (AUTH-GOAL-CONTAMINATION-001). Not part of any resume fingerprint.

        recovery_contract_block: Recovery Execution Contract (PRV-06,
        2026-08-29) - deliberately NOT folded into supplementary_context
        above, even though both eventually reach the Developer. A live
        incident traced exactly why: supplementary_context's own documented
        contract (this docstring, unchanged above) is "raw text folded into
        convention_prompt... the one accumulator Planner/Architect/Developer
        all share" - i.e. passive, cross-cutting reference material,
        exactly what workflow_controller.py's own MA8.1 owner-recovery
        MUST_FIX/MUST_PRESERVE/EVIDENCE/ACCEPTANCE text is NOT. A recovery
        requirement is the CURRENT GOVERNING INSTRUCTION for this specific
        invocation, not code context, a coding convention, or repository
        history. Threaded onto AttemptContext.recovery_contract_block and,
        from there, into retry_prompts.py's task-description builders
        directly (prepended to the returned task_desc with its own
        authoritative framing) - reaching the Developer as part of WHAT TO
        DO, never folded into "existing code context" the model is free to
        treat as passive background. Empty string (the default) is a
        complete no-op for every caller outside owner-recovery.

        milestone_group_id/milestone_index/milestone_total: pure passthrough to
        every trace_logger.log_run() call below, no other effect on this
        method's own behavior - kriya/workflow/milestones.py's orchestrator
        mints one milestone_group_id per decomposed goal and passes it (plus
        this call's position/total within that sequence) so `kriya milestones`
        can later group/order what would otherwise be N indistinguishable,
        unrelated traces.db rows (run_id is that table's PRIMARY KEY). None
        (the default) preserves today's exact behavior for every other caller.

        resume=True resumes the most recently saved checkpoint for this workspace;
        resume_id resumes a specific one. Checkpoints are stage-level (post-Plan,
        post-Design, post-Developer-quality-gates) and only survive a crash/kill -
        a normal completion always deletes its own checkpoint. Any drift in the
        workspace git state, resolved config, or goal/error text since the
        checkpoint was saved invalidates it (strict - falls back to a fresh run
        with a warning, never a partial/best-effort resume).

        established_files: filepaths known to exist from OUTSIDE this call's own
        generation - for kriya/workflow/milestones.py's run_milestones(), every
        file an earlier, already-completed milestone wrote (the same set
        supplementary_context's rendered content comes from - see
        MilestoneRunState.established_file_context). Threaded onto
        AttemptContext.established_files and unioned into the "known files"
        candidate set self-diagnosis/attribution matching uses (kriya/workflow/
        attempt.py's extract_self_diagnosed_files() call and retry_strategy.py's
        attribute_failure() call) - WITHOUT touching state.all_files_written
        itself, which ~30 other call sites read as "written by THIS attempt"
        and must stay that way. Exists because supplementary_context alone
        only fixes what the model ASSUMES about an earlier file's shape; it
        does nothing for a LATER compile/runtime failure whose real fix
        requires editing that earlier file - found live, 2026-08-21
        (ignite_qpid_protocol): the Developer's own FIX ANALYSIS correctly,
        repeatedly said an earlier milestone's file needed a change, but that
        file was never a valid redirect target, since only files THIS attempt
        itself wrote were ever considered "known". None (the default)
        preserves today's exact behavior for every other caller.

        resolved_knowledge_coordinates carries a run-level, coordinate-scoped
        KnowledgeGuard acknowledgement into bounded execution. Only matching
        libraries are suppressed; a new dependency remains a real gap.

        skill_engine_override lets an authoritative controller reuse one loaded
        registry across its bounded subtasks. None preserves per-call discovery.

        web_lookup_query_callback gates every outbound live-lookup search (goal-
        stage, design-stage, and the retry loop's repeated-failure lookup alike)
        BEFORE it fires - see _approve_web_lookup(). Separate from web_lookup_callback,
        which only gates whether already-fetched results get used.

        trace_id_override: reuse a specific traces.db run_id instead of generating a
        fresh one. Exists for kriya/cli.py's generate command specifically: a
        knowledge_gap gate makes THIS method return early (writing its own trace row)
        and then, if the CLI auto-confirms/re-invokes with knowledge_risk_confirmed=True,
        call it again from scratch - two logically-connected calls with no shared state
        otherwise. Confirmed live, 2026-08-07: an eval-harness batch's own
        --timeout-per-goal killed the SECOND call (a genuine ~20-minute run) before it
        ever reached its own trace_logger.log_run(), leaving only the harmless first
        row (status=knowledge_gap, <1s) behind - traces.db/report.py had no way to tell
        that apart from a run that genuinely stopped at the gate. Passing the first
        call's own returned run_id back in as trace_id_override on the retry means the
        SAME primary key gets used for both calls' trace rows - runs.run_id is the
        table's PRIMARY KEY and log_run() already does `INSERT OR REPLACE`, so the
        second call's own eventual final status cleanly supersedes the first row
        instead of leaving two independent rows behind. If the second call is itself
        killed before writing, the original (now genuinely accurate, since the run
        really did make it that far before being killed externally) row is simply what
        remains - never worse than today's behavior, and correct whenever the retried
        call actually finishes. None (the default) preserves today's exact behavior for
        every other caller - a fresh run_id every time.

        predetermined_plan/predetermined_design/predetermined_architect_files
        (MA7-C1, 2026-08-25 external review): when ALL THREE are supplied,
        stages 2 (Plan) and 3 (Architect) below are skipped entirely - no
        self.planner.run()/self.architect.run_with_file_list() call happens,
        the supplied values are used as-is - mirroring the EXISTING
        resume_state-driven bypass immediately below each stage (same shape,
        deliberately a SEPARATE, dedicated mechanism rather than overloading
        resume_state itself: resume_state also flips knowledge_risk_confirmed
        and participates in checkpoint/fingerprint-drift semantics that have
        nothing to do with "the caller already knows the plan/design", and
        reusing it here would silently pull in those unrelated effects).
        Every OTHER stage (KnowledgeGuard, repository analysis, skill
        matching/gap/conflict detection, Graph RAG retrieval, learned-
        knowledge RAG, the Developer/Quality-Gates retry loop itself,
        approval, worktree-apply, Reviewer, trace logging) is completely
        unmodified and still runs in full - this is specifically NOT a new,
        smaller execution path; it is this exact same method with its two
        planning LLM calls swapped out for values the caller already has.
        Built for WorkflowController._run_structured_enforce (kriya/workflow/
        workflow_controller.py) to stop re-planning an already-validated
        Subtask from scratch inside each subtask's own call, while keeping
        every other real guarantee (retry budget, failure grounding,
        attribution, repair, approval, full regression Quality Gates)
        completely intact and un-duplicated. Partially supplying only one or
        two of the three raises ValueError immediately - an all-or-nothing
        contract, never a caller bug silently degrading into "use only
        SOME predetermined values." None for all three (the default)
        preserves today's exact behavior for every other caller."""
        if work_unit is None:
            call_args = {name: value for name, value in locals().items() if name not in ("self", "work_unit")}
            from kriya.workflow.plan_executor import execute_direct_goal
            return await execute_direct_goal(self.run_generation_workflow, call_args)
        if (predetermined_plan is not None or predetermined_design is not None
                or predetermined_architect_files is not None) and not (
                    predetermined_plan is not None and predetermined_design is not None
                    and predetermined_architect_files is not None
                ):
            raise ValueError(
                "predetermined_plan/predetermined_design/predetermined_architect_files must be "
                "supplied together or not at all - got a partial combination."
            )
        # CORR-018 general-case closure (2026-09-13): auto-derive
        # authorized_semantic_regions for ordinary generate/fix and
        # structured-plan MODEL-subtask calls, ONLY when the caller did not
        # already supply its own list (`is None` - A3's proposal-promotion
        # path always passes a real, non-empty list here and is therefore
        # completely untouched by this branch, preserving Invariant 13
        # exactly) AND the opt-in flag is set (default False - zero
        # behavior change for every existing deployment). Deliberately
        # BEFORE the resume-checkpoint drift check below, so a resumed run
        # under strict mode always re-derives fresh from the current
        # grounding_goal/structured_plan/workspace content rather than
        # trusting anything checkpoint-carried (mirrors TOOL-001's own
        # "always revalidate fresh on resume" precedent).
        if (
            self.kernel.config.autonomy.semantic_region_enforcement_required
            and authorized_semantic_regions is None
        ):
            authorized_semantic_regions = derive_semantic_authority_for_run(
                grounding_goal, structured_plan, workspace_path,
            )
        # Deliberately BEFORE state is constructed below - state.generation_
        # started_monotonic (kriya/workflow/state.py) defaults to time.monotonic()
        # AT CONSTRUCTION, and that's the real clock generation_time_budget_seconds
        # is measured against (kriya/workflow/attempt.py's elapsed = time.monotonic()
        # - state.generation_started_monotonic). Calling this here means a one-time
        # index pass costs real wall-clock time but is structurally EXCLUDED from
        # that budget for every caller alike, not just milestone-decomposition -
        # moving this below the state = GenerationState(...) line would silently
        # start eating the same budget a slow model's Planner/Architect/Developer
        # calls already exhaust on their own. See _ensure_repository_indexed's own
        # docstring (above) for the full gap this closes and why it's opt-in.
        if (
            self.kernel.config.autonomy.auto_index_missing_dependency_graph
            or strict_dependency_index
        ):
            index_available = await _ensure_repository_indexed(self.kernel.config, workspace_path)
            if strict_dependency_index and not index_available:
                return {
                    "status": "needs_review",
                    "quality_gates_passed": False,
                    "files": [],
                    "failure_type": "VERIFICATION_INFRASTRUCTURE_FAILURE",
                    "reason_codes": ["DEPENDENCY_INDEX_UNAVAILABLE"],
                    "error": "Authoritative dependency-aware context could not be indexed.",
                }

        # MA1.3 - engineering triage, shadow mode (kriya/workflow/triage.py).
        # Placed AFTER the optional auto-index above, same time-budget-exclusion
        # reasoning as that block's own comment - and, not incidentally, so a
        # freshly-built dependency graph is available to the triage classifier's
        # own best-effort DependencyGraph signals. Placed BEFORE state =
        # GenerationState(...) below purely to keep this near the top of the
        # method, matching the design's own "triage runs before any of the rest
        # of this document engages" ordering. A classification failure is
        # caught and logged, never allowed to fail a real generation run.
        #
        # MA2.3 - control (kriya/workflow/control_context.py) pairs
        # engineering_route with its resolved ProcessProfile, kept together
        # so they can't drift out of sync once MA2.4 starts recomputing risk
        # after Architect. Still shadow-mode as of MA2.3: nothing below this
        # block reads `control` for a real decision yet - that starts at
        # MA2.5/MA2.6. control stays a local variable, not stored on state -
        # see WorkflowControlContext's own docstring for why.
        engineering_route: Optional[EngineeringRoute] = None
        control: Optional[WorkflowControlContext] = None
        if self.kernel.config.engineering_triage.enabled:
            try:
                engineering_route = await self.engineering_triage.classify(
                    goal, workspace_path, known_files=established_files,
                )
                control = WorkflowControlContext.for_route(engineering_route)
                triage_label = (
                    "[Subtask Triage]" if predetermined_plan is not None else "[Engineering Triage]"
                )
                logger.info(
                    f"{triage_label} "
                    f"kind={engineering_route.kind.value} "
                    f"risk={engineering_route.max_observed_risk_class.name} "
                    f"weight={engineering_route.execution_weight.value} "
                    f"verification_tier={control.process_profile.verification_tier.value} "
                    f"shadow_mode={self.kernel.config.engineering_triage.shadow_mode} "
                    f"reasons={engineering_route.reason_codes}"
                )
            except Exception as e:
                logger.warning(f"Engineering triage classification failed, continuing without it: {e}")

        # Constructed once, right at the top, so every stage of the method -
        # including the pre-loop checkpoint fingerprinting and Planner prompt
        # below, not just the retry loop - reads/writes through one consistent
        # object instead of the bare `error_context` parameter early on and
        # `state.error_context` later. See kriya/workflow/state.py for the
        # rationale behind every other field.
        # kriya/workflow/run_lifecycle.py::open_run_state: the run's clock
        # claim (PROVIDER-CONTRACT-001A), the state, and the run-level identity
        # events (egress authority, attempt evidence store, model runtime, role
        # independence, applied routes).
        state = await open_run_state(
            self._stage_deps(), error_context=error_context, engineering_route=engineering_route,
            process_profile=control.process_profile if control is not None else None,
        )
        # PRD-020: the user's original requirements, fixed once from the goal
        # this unit verifies (kriya/workflow/requirements.py). None for a
        # unit that verifies something narrower (a milestone, a subtask).
        # requirements_from_goal=False: the goal is Kriya's own wording (e.g.
        # `kriya fix`'s "Fix compilation/test failure" around an error log),
        # not the user's statements - there is nothing to fix as a
        # requirement.
        requirement_goal = getattr(work_unit, "requirement_goal", None) if requirements_from_goal else None
        # GR-R1A: the operator's explicit, closed requirement contract when one
        # is bound (never merged with requirements derived from the goal).
        requirement_set = (requirement_set_for(requirement_goal, bound_requirement_contract(self))
                           if requirement_goal else None)
        # VERIFICATION-CONTRACT-003: the run's verification contract, compiled
        # from the set and the repository before any model call; its digest
        # joins the resume identity, its refusal is decided below.
        verification_contract = (compile_run_contract(self, requirement_goal, goal, workspace_path, requirement_set)
                                 if requirement_set is not None else None)
        if requirement_set is not None:
            state.record_event(RunEvent(
                kind="requirement.derived", attempt=0, source="workflow.run_generation_workflow",
                authority=EventAuthority.AUXILIARY,
                message=f"{len(requirement_set.requirements)} original requirement(s): "
                        + ", ".join(requirement_set.ids),
                details=requirement_set.to_dict(),
            ))
            contract_report = verification_contract.report()
            state.record_event(RunEvent(
                kind="verification_contract.compiled", attempt=0, source="workflow.run_generation_workflow",
                authority=EventAuthority.AUXILIARY,
                message=(f"verification contract {verification_contract.digest[:12]}: {contract_report['admission']}; "
                         + ", ".join(f"{k}={v}" for k, v in contract_report["totals"].items())),
                details=contract_report,
            ))

        def _record_requirement_lineage(stage: str, text: Any) -> None:
            if requirement_set is None:
                return
            lineage = requirement_lineage(
                requirement_set, stage, cited_requirement_ids(str(text or ""), requirement_set))
            state.record_event(RunEvent(
                kind="requirement.lineage", attempt=0, source="workflow.run_generation_workflow",
                authority=EventAuthority.AUXILIARY,
                message=f"{stage} cites {lineage['cited']}; omitted (still active) {lineage['omitted']}",
                details=lineage,
            ))

        generation_budget = self.kernel.config.autonomy.generation_time_budget_seconds
        if generation_budget is None:
            logger.info(
                "Internal generation time budget: inactive (no "
                "autonomy.generation_time_budget_seconds configured)."
            )
        else:
            logger.info(
                f"Internal generation time budget: active ({generation_budget}s, with "
                f"{self.kernel.config.autonomy.generation_gate_reserve_seconds}s reserved "
                "for gates/review)."
            )

        # PRD-008: one computation of every resume fingerprint, used both
        # when validating a checkpoint (below) and when saving one
        # (_save_stage_checkpoint), so the two can never be derived
        # differently. Every input here is fixed for the whole call.
        # The caller's ledger is the object this run grows into its
        # effective ledger, so its input fingerprint is fixed here, at entry.
        entry_obligation_fingerprint = ledger_fingerprint(obligation_ledger)

        def _resume_fingerprints(
            effective_obligation_ledger: Any, workspace: Any = None,
            effective_obligation_fingerprint: Optional[Fingerprint] = None,
            candidate_files: Optional[Dict[str, str]] = None,
        ) -> Dict[str, Any]:
            # candidate_files: a candidate checkpoint's exact files, so the
            # toolchain fingerprint describes the toolchain its gates ran
            # under (PRD-011: the target of an authorized migration).
            return generation_resume_fingerprints(
                self.kernel.config, workspace_path, workspace=workspace, candidate_files=candidate_files,
                input_obligation_fingerprint=entry_obligation_fingerprint,
                effective_obligation_fingerprint=effective_obligation_fingerprint,
                goal=goal, error_context=state.error_context,
                supplementary_context=supplementary_context,
                recovery_contract_block=recovery_contract_block,
                execution_scope=execution_scope, grounding_goal=grounding_goal,
                established_files=established_files,
                predetermined_plan=predetermined_plan, predetermined_design=predetermined_design,
                predetermined_architect_files=predetermined_architect_files,
                structured_plan=structured_plan, current_subtask_id=current_subtask_id,
                completed_subtask_ids=completed_subtask_ids,
                obligation_ledger=obligation_ledger,
                effective_obligation_ledger=effective_obligation_ledger,
                skill_engine_override=skill_engine_override,
                allowed_write_relpaths=allowed_write_relpaths,
                authorized_semantic_regions=authorized_semantic_regions,
                write_scope_mode=write_scope_mode, protected_source_file=protected_source_file,
                required_verification=required_verification,
                runtime_verification_required=runtime_verification_required,
                strict_spec_compliance=strict_spec_compliance,
                strict_dependency_index=strict_dependency_index,
                acceptance_digest=getattr(bound_acceptance(self), "digest", None),
                approval_digest=getattr(bound_approval(self), "digest", None),
                requirement_contract_digest=getattr(bound_requirement_contract(self), "digest", None),
                verification_contract_digest=(verification_contract.digest if verification_contract is not None
                                              else None),
            )

        # Resume resolution (opt-in only - no auto-detection from goal-text matching)
        run_id = None
        resume_state: Optional[Dict[str, Any]] = None
        # PRD-008 S3: what the resumed run reuses, decided once, here, from
        # the validation result; attempt.py reads its reuse flags from this,
        # never from the checkpoint file.
        resume_plan: Optional[ResumePlan] = None
        resume_restored_ledger: Any = None
        if resume or resume_id:
            target_id = resume_id or find_latest_checkpoint(workspace_path)
            if not target_id:
                logger.warning("No saved checkpoint found for this workspace - starting a fresh run instead.")
            else:
                candidate = load_checkpoint(workspace_path, target_id)
                if not candidate:
                    logger.warning(f"Checkpoint '{target_id}' not found or unreadable - starting a fresh run instead.")
                else:
                    # PRD-008: the checkpoint is judged only by
                    # validate_resume_against_reality(). Workspace content,
                    # config, goal and the authorized-semantic-region
                    # boundary (now the authority_context fingerprint, which
                    # also catches a CHANGED boundary, not only a dropped
                    # one) are all fingerprints there; an applicable one that
                    # is missing or UNAVAILABLE is UNVERIFIED, never a match.
                    # A candidate is reused together with the effective
                    # obligation ledger it was verified against; that
                    # ledger is restored from the checkpoint, so its
                    # current value is the restored snapshot's (UNAVAILABLE
                    # if there is none - never an empty ledger).
                    try:
                        resume_restored_ledger, ledger_problem = restore_effective_ledger(candidate)
                        current_resume_fingerprints = _resume_fingerprints(
                            obligation_ledger,
                            candidate_files=(
                                candidate.get("final_files")
                                if isinstance(candidate.get("final_files"), dict) else None
                            ),
                            effective_obligation_fingerprint=(
                                ledger_fingerprint(resume_restored_ledger)
                                if resume_restored_ledger is not None
                                else Fingerprint.unavailable(ledger_problem or "no ledger snapshot")
                            ),
                        )
                    except Exception as error:
                        # Checkpointing is a convenience and never fails the
                        # run; an unverifiable checkpoint is simply not used.
                        logger.warning(
                            f"Could not compute resume fingerprints ({type(error).__name__}: {error}) - "
                            f"not resuming checkpoint '{target_id}'; starting a fresh run instead."
                        )
                        current_resume_fingerprints = None
                    if current_resume_fingerprints is not None:
                        run_reference = candidate.get("_run_record")
                        prior_run_id = (
                            str(run_reference["run_id"])
                            if isinstance(run_reference, dict) and run_reference.get("run_id") else None
                        )
                        prior_run_record = None
                        prior_run_record_error = None
                        prior_run_record_missing = None
                        if prior_run_id is not None:
                            try:
                                prior_run_record = load_run_record(workspace_path, prior_run_id)
                            except (UnreadableRunRecordError, ValueError) as error:
                                prior_run_record_error = str(error)
                            else:
                                if prior_run_record is None:
                                    prior_run_record_missing = prior_run_id
                        resume_validation = validate_resume_against_reality(
                            candidate,
                            workspace_path,
                            current_resume_fingerprints=current_resume_fingerprints,
                            run_record=prior_run_record,
                            run_record_error=prior_run_record_error,
                            run_record_missing=prior_run_record_missing,
                        )
                        logger.info(
                            "Resume fingerprints for checkpoint '%s': %s", target_id,
                            json.dumps({
                                item.name: item.status.value
                                for item in resume_validation.fingerprint_comparisons
                            }, sort_keys=True),
                        )
                        if resume_validation.decisions:
                            logger.warning(
                                "Structured resume decisions: %s",
                                json.dumps([item.to_dict() for item in resume_validation.decisions], sort_keys=True),
                            )
                        if resume_validation.status == ResumeStatus.REFUSED:
                            return {
                                "status": "resume_refused",
                                "quality_gates_passed": False,
                                "files": [],
                                "reason_codes": sorted({
                                    _RESUME_REFUSAL_REASON_CODES[item.fingerprint]
                                    for item in resume_validation.decisions
                                    if item.action == ResumeAction.REFUSE
                                }),
                                "resume_decisions": [
                                    item.to_dict() for item in resume_validation.decisions
                                ],
                                "run_id": prior_run_id,
                            }
                        # PRD-008 S3: keep only what no invalidated stage
                        # touches (resume_fingerprints.build_resume_plan).
                        resume_plan = build_resume_plan(
                            target_id, candidate, resume_validation.invalidated_stages,
                        )
                        logger.info("Resume decision: %s", json.dumps(resume_plan.to_dict(), sort_keys=True))
                        annotation_error = annotate_run(workspace_path, resume_decision=resume_plan.to_dict())
                        if annotation_error:
                            logger.warning(f"Could not record the resume decision on the run record: {annotation_error}")
                        if not resume_plan.resumes:
                            logger.warning(
                                f"Refusing to resume checkpoint '{target_id}' (invalidated stages: "
                                f"{', '.join(resume_validation.invalidated_stages)}): "
                                f"{'; '.join(resume_validation.mismatches)}. Starting a fresh run instead."
                            )
                        else:
                            run_id = target_id
                            resume_state = resume_plan.state
                            discarded = sorted(resume_plan.offered - resume_plan.reused)
                            if discarded:
                                logger.warning(
                                    f"Resuming checkpoint '{run_id}' partially: reusing "
                                    f"{', '.join(sorted(resume_plan.reused))}; discarding "
                                    f"{', '.join(discarded)} (invalidated stages: "
                                    f"{', '.join(resume_validation.invalidated_stages)}): "
                                    f"{'; '.join(resume_validation.mismatches)}."
                                )
                            else:
                                logger.info(
                                    f"Resuming checkpoint '{run_id}' at stage '{candidate.get('stage')}'."
                                )
        if run_id is None:
            run_id = new_run_id()

        # Trace id/start time are established here, before any early-return gate,
        # so every terminal state of a run (including knowledge_gap and human
        # rejection below) is captured in traces.db, not just the ordinary
        # completed-loop path.
        import time
        import uuid
        trace_id = trace_id_override or str(uuid.uuid4())[:8]
        start_time = time.time()
        # MODEL-EVIDENCE-HARDENING-001: what the exception guard needs to
        # attribute this run's calls if it raises (_record_run_exception).
        note_run_trace(trace_id=trace_id, goal=goal, started_at=start_time, milestone_group_id=milestone_group_id,
                       enforce_run_id=enforce_run_id)
        run_trace = RunTrace(trace_id=trace_id, goal=goal, started_at=start_time, milestone_group_id=milestone_group_id,
                             milestone_index=milestone_index, milestone_total=milestone_total,
                             enforce_run_id=enforce_run_id)

        # REQUIREMENT-CLOSURE-PLAIN-GOAL-001: before any model call, every
        # mandatory requirement must have a deterministic closer; a goal that
        # cannot be verified is refused here, naming what and why.
        if verification_contract is not None and _requirement_policy_blocks(self.kernel.config.autonomy):
            from kriya.control.trace import TraceLogger

            admission = verification_contract.refusal()
            if admission is not None:
                # VERIFICATION-CONTRACT-003 (D3): GOAL_INSUFFICIENT_FOR_VERIFICATION (the
                # goal does not establish what to verify) or VERIFICATION_AUTHORITY_REQUIRED
                # (clear, but a mandatory claim has no bound authority) - both typed, both
                # before any model call, nothing generated, a sealed trace.
                logger.error(admission.message)
                state.record_event(RunEvent(
                    kind=("requirement.authority_required" if isinstance(admission, VerificationAuthorityRequired)
                          else "requirement.admission_refused"),
                    attempt=0, source="workflow.run_generation_workflow",
                    authority=EventAuthority.AUTHORITATIVE, message=admission.message[:600],
                    details=admission.to_dict(),
                ))
                requests_path = None
                if isinstance(admission, VerificationAuthorityRequired) and admission.authority_requests:
                    # BACKEND-READINESS-004 (D2): the typed requests, sealed under the state root and recorded.
                    from kriya.workflow.authority_request import seal_requests_for_refusal

                    requests_path = seal_requests_for_refusal(self.kernel.config, verification_contract, admission)
                    state.record_event(RunEvent(
                        kind="verification_contract.authority_requested", attempt=0,
                        source="workflow.run_generation_workflow", authority=EventAuthority.AUTHORITATIVE,
                        message=f"{len(admission.authority_requests)} authority request(s) for "
                                f"{len({r.requirement_id for r in admission.authority_requests})} requirement(s); "
                                "a request proposes authority, only the operator seals it",
                        details={"contract_digest": verification_contract.digest, "stored_path": requests_path,
                                 "sealing_error": getattr(admission, "authority_requests_sealing_error", None),
                                 "requests": [r.to_dict() for r in admission.authority_requests]},
                    ))
                try:
                    TraceLogger(trace_db_path(self.kernel.config)).log_run(
                        run_id=trace_id, goal=goal, duration_sec=time.time() - start_time,
                        attempts=0, status="failure", files_modified=[],
                        failure_category=admission.failure_category,
                        milestone_group_id=milestone_group_id, milestone_index=milestone_index,
                        milestone_total=milestone_total, enforce_run_id=enforce_run_id, run_events=self._trace_run_events(state),
                    )
                except Exception as trace_ex:
                    logger.warning(f"Failed to write run trace: {trace_ex}")
                return {
                    "status": "failure", "failure_category": admission.failure_category,
                    "reason_codes": [admission.reason_code], "error": admission.message,
                    "environment_failure": admission.message, "goal": goal, "workspace_path": workspace_path,
                    "run_id": trace_id, "files": [], "quality_gates_passed": False,
                    "requirements_admission": admission.to_dict(),
                    "authority_requests": [r.to_dict() for r in getattr(admission, "authority_requests", ())],
                    "authority_requests_path": requests_path,
                    **({"verification_contract": admission.report} if admission.report is not None else {}),
                }
        if verification_contract is not None:
            # Admitted (or not blocking under the record policy): seal the
            # contract - stored content-addressed, bound to this run's identity.
            sealed_path = seal_run_contract(self.kernel.config, verification_contract)
            state.record_event(RunEvent(
                kind="verification_contract.sealed", attempt=0, source="workflow.run_generation_workflow",
                authority=EventAuthority.AUTHORITATIVE,
                message=f"verification contract {verification_contract.digest[:12]} sealed before the first model call",
                details={"digest": verification_contract.digest, "stored_path": sealed_path,
                         "admission": verification_contract.report()["admission"],
                         "totals": verification_contract.report()["totals"],
                         "authorities": [a.to_dict() for a in verification_contract.authorities],
                         "visibility": verification_contract.visibility(),
                         "dispositions": verification_contract.dispositions},
            ))
            if verification_contract.dispositions:
                # BACKEND-READINESS-004 (D3): audit event - which statements/claims the operator dispositioned.
                dispositioned = [{"requirement_id": e.requirement_id, "status": e.status,
                                  "claims": list(e.dispositioned_claims), "disposition": e.disposition}
                                 for e in verification_contract.dispositioned_entries()]
                state.record_event(RunEvent(
                    kind="verification_contract.dispositioned", attempt=0, source="workflow.run_generation_workflow",
                    authority=EventAuthority.AUTHORITATIVE,
                    message=f"operator disposition {str(verification_contract.dispositions.get('digest'))[:12]}: "
                            f"{len(dispositioned)} statement(s) dispositioned before the first model call",
                    details={"dispositions": verification_contract.dispositions, "entries": dispositioned},
                ))
            # The baseline authority run: deterministic evidence before any
            # model call; a baseline that already satisfies every mandatory
            # claim is NO_MUTATION_REQUIRED (a success without a model).
            if verification_contract.authorities and verification_contract.refusal() is None:
                from kriya.control.trace import TraceLogger
                from kriya.workflow.contract_baseline import BaselineAuthorityReport, NoMutationRequired

                try:
                    baseline_report = run_contract_baseline(
                        self, verification_contract, requirement_set, workspace_path,
                        autonomy_cfg=self.kernel.config.autonomy, config=self.kernel.config)
                except Exception as exc:
                    # Review VC3-R8 / D2: a baseline that could not run is an
                    # environment outcome - typed stop, never a silent proceed.
                    logger.warning(f"Baseline authority run unavailable: {type(exc).__name__}: {exc}")
                    baseline_report = BaselineAuthorityReport(base_revision=_workspace_head(workspace_path))
                    baseline_report.authorities_run.append({
                        "kind": "baseline_run", "digest": None, "reason_code": "AUTHORITY_EXECUTION_UNAVAILABLE",
                        "reason": f"the baseline authority run raised {type(exc).__name__}: {exc}"})
                if baseline_report is not None:
                    state.record_event(RunEvent(
                        kind="verification_contract.baseline", attempt=0, source="workflow.run_generation_workflow",
                        authority=EventAuthority.AUTHORITATIVE,
                        message=(f"baseline authority run: no_mutation_required={baseline_report.no_mutation_required}, "
                                 f"discriminating={baseline_report.discriminating}"),
                        details=baseline_report.to_dict(),
                    ))
                    if baseline_report.unavailable_authorities():
                        # D2: an oracle that cannot execute here can never judge a
                        # candidate - stop before any model call, typed.
                        from kriya.workflow.contract_baseline import VerificationAuthorityUnavailable

                        stop = VerificationAuthorityUnavailable(verification_contract, baseline_report)
                        logger.error(stop.message)
                        state.record_event(RunEvent(
                            kind="verification_contract.refused", attempt=0, source="workflow.run_generation_workflow",
                            authority=EventAuthority.AUTHORITATIVE, message=stop.message[:600],
                            details={"reason_code": stop.reason_code, "unavailable": baseline_report.unavailable_authorities()},
                        ))
                        try:
                            TraceLogger(trace_db_path(self.kernel.config)).log_run(
                                run_id=trace_id, goal=goal, duration_sec=time.time() - start_time,
                                attempts=0, status="failure", files_modified=[], failure_category=stop.failure_category,
                                milestone_group_id=milestone_group_id, milestone_index=milestone_index,
                                milestone_total=milestone_total, enforce_run_id=enforce_run_id, run_events=self._trace_run_events(state),
                            )
                        except Exception as trace_ex:
                            logger.warning(f"Failed to write run trace: {trace_ex}")
                        return {**stop.result(), "goal": goal, "workspace_path": workspace_path, "run_id": trace_id}
                    if baseline_report.no_mutation_required:
                        decision = NoMutationRequired(verification_contract, baseline_report)
                        logger.info(str(decision))
                        state.record_event(RunEvent(
                            kind="verification_contract.no_mutation_required", attempt=0,
                            source="workflow.run_generation_workflow", authority=EventAuthority.AUTHORITATIVE,
                            message=str(decision), details=baseline_report.to_dict(),
                        ))
                        try:
                            TraceLogger(trace_db_path(self.kernel.config)).log_run(
                                run_id=trace_id, goal=goal, duration_sec=time.time() - start_time,
                                attempts=0, status="success", files_modified=[],
                                milestone_group_id=milestone_group_id, milestone_index=milestone_index,
                                milestone_total=milestone_total, enforce_run_id=enforce_run_id, run_events=self._trace_run_events(state),
                            )
                        except Exception as trace_ex:
                            logger.warning(f"Failed to write run trace: {trace_ex}")
                        return {**decision.result(), "goal": goal, "workspace_path": workspace_path, "run_id": trace_id}

        protected_relpath = _resolve_protected_relpath(workspace_path, protected_source_file)

        # Stages 0-3 of this work unit (full modularization W4, E3): KnowledgeGuard, analysis + skills + Graph RAG +
        # the learned reference, the Plan, the Architect incl. Stage 2A/2B, then the active-skill snapshot - driven by
        # kriya/workflow/work_unit_runner.py over one StageContext, with this engine's collaborators in StageDeps
        # (_work_unit_deps, resolved through this module at call time). A stage's early result (knowledge_gap, a
        # Planner rejection, architect_file_list_rejected) is this method's result, unchanged.
        def _open_stage_checkpoints() -> Callable[..., None]:
            # Called by the runner between analysis and planning: exactly where the checkpoint fingerprints were
            # always computed (after any stage-1 skill bootstrap, before the Planner).
            # Fingerprints for any checkpoint saved during this run - computed once,
            # goal/workspace/config are all fixed for the remainder of the call.
            # Safe to compute once (not per-checkpoint-save): every checkpoint
            # this method saves happens BEFORE the real workspace is ever
            # touched by this run (candidate writes stay confined to
            # worktree_path until the final, post-checkpoint apply step) -
            # traced explicitly for this fix (STATE-001) rather than assumed.
            checkpoint_ws_fp = compute_workspace_fingerprint(workspace_path)
            # STATE-001 (2026-09-14): the real, working-tree-content-sensitive
            # identity - see compute_workspace_content_hash()'s own docstring
            # (kriya/workflow/checkpoint.py) for the full mechanism and why
            # checkpoint_ws_fp above cannot distinguish two different dirty
            # contents at the same HEAD.
            checkpoint_content_hash = compute_workspace_content_hash(workspace_path)
            checkpoint_cfg_fp = compute_config_fingerprint(self.kernel.config.model_dump())
            checkpoint_goal_fp = hashlib.sha256(f"{goal}\x00{state.error_context or ''}".encode("utf-8")).hexdigest()
            # PRD-008: the workspace fingerprint is fixed here for the same
            # reason; the rest are recomputed per save (skills may be bootstrapped
            # before planning, and the effective obligation ledger grows).
            checkpoint_workspace_identity = workspace_fingerprint(workspace_path)

            def _save_stage_checkpoint(
                stage: str, *, effective_obligation_ledger: Any = None, **extra: Any,
            ) -> None:
                # Checkpointing never fails the run (save_checkpoint swallows its
                # own errors too). Without the block the checkpoint is simply
                # UNVERIFIED on resume, never trusted.
                try:
                    resume_fingerprint_block: Optional[Dict[str, Any]] = fingerprint_block(_resume_fingerprints(
                        effective_obligation_ledger, workspace=checkpoint_workspace_identity,
                        candidate_files=extra.get("final_files") if isinstance(extra.get("final_files"), dict) else None,
                    ))
                except Exception as error:
                    logger.warning(
                        f"Could not compute resume fingerprints for the '{stage}' checkpoint "
                        f"({type(error).__name__}: {error}) - it will not be resumable."
                    )
                    resume_fingerprint_block = None
                save_checkpoint(workspace_path, run_id, {
                    "stage": stage,
                    RESUME_FINGERPRINTS_KEY: resume_fingerprint_block,
                    # PRD-019: the routes this run uses, so a resume reuses them
                    # (kriya/cli.py::_workflow_config) instead of re-routing on
                    # a metrics table that changed since; None without routing.
                    "model_routes": resume_routes_from(getattr(self.kernel.config, "_routing_plan", None)),
                    "workspace_fingerprint": checkpoint_ws_fp,
                    "workspace_content_hash": checkpoint_content_hash,
                    "config_fingerprint": checkpoint_cfg_fp,
                    "goal_fingerprint": checkpoint_goal_fp,
                    "milestone_group_id": milestone_group_id,
                    "milestone_index": milestone_index,
                    # PRD-008 S4c: which unit of work this checkpoint belongs
                    # to (the owning run's active milestone, set by the milestone
                    # driver), so milestone resume selects by identity, never by
                    # "newest in the workspace".
                    "work_unit": owning_run_work_unit(workspace_path),
                    # A3-P2 marker, audit only since PRD-008: the resume check
                    # is now the authority_context fingerprint, which binds the
                    # regions themselves (a changed boundary, not only a
                    # dropped one, blocks reuse).
                    "had_authorized_semantic_regions": bool(authorized_semantic_regions),
                    # VAL-001 brownfield validation baselining - None for any
                    # checkpoint saved before baseline capture runs (the "plan"/
                    # "design" stage checkpoints above) or for any run that
                    # never configured baselining at all; a real dict once
                    # capture_brownfield_baselines() has run, letting a later
                    # resume reuse it (see capture_brownfield_baselines' own
                    # resume_baseline_targeted/resume_baseline_full_regression
                    # parameters).
                    "validation_baseline_targeted": (
                        state.validation_baseline_targeted.to_dict()
                        if state.validation_baseline_targeted is not None else None
                    ),
                    "validation_baseline_full_regression": (
                        state.validation_baseline_full_regression.to_dict()
                        if state.validation_baseline_full_regression is not None else None
                    ),
                    # REG-R1: untouched-baseline field stability measured so far,
                    # keyed by binding digest (pytest_stability.stability_binding).
                    "validation_baseline_pytest_stability": dict(state.pytest_stability_cache),
                    **extra,
                })
            return _save_stage_checkpoint

        stage_ctx = StageContext(
            goal=goal, workspace_path=workspace_path, step_callback=step_callback, approval_callback=approval_callback,
            stream_callback=stream_callback, knowledge_risk_confirmed=knowledge_risk_confirmed,
            skill_gap_callback=skill_gap_callback, skill_conflict_callback=skill_conflict_callback,
            web_lookup_callback=web_lookup_callback, web_lookup_query_callback=web_lookup_query_callback,
            supplementary_context=supplementary_context, reference_context=reference_context,
            resolved_knowledge_coordinates=resolved_knowledge_coordinates, skill_engine_override=skill_engine_override,
            predetermined_plan=predetermined_plan, predetermined_design=predetermined_design,
            predetermined_architect_files=predetermined_architect_files, allowed_write_relpaths=allowed_write_relpaths,
            write_scope_mode=write_scope_mode, structured_plan=structured_plan, current_subtask_id=current_subtask_id,
            milestone_group_id=milestone_group_id, milestone_index=milestone_index, milestone_total=milestone_total,
            enforce_run_id=enforce_run_id, state=state, trace_id=trace_id, start_time=start_time, run_id=run_id,
            resume_state=resume_state, requirement_set=requirement_set, engineering_route=engineering_route,
            control=control, record_requirement_lineage=_record_requirement_lineage,
        )
        stage_stop = await run_design_stages(stage_ctx, self._work_unit_deps(), open_checkpoints=_open_stage_checkpoints)
        if stage_stop is not None:
            return stage_stop
        # What the Developer loop, the approval and the review read from the design stages, under their names.
        _save_stage_checkpoint = stage_ctx.save_stage_checkpoint
        engineering_route, control = stage_ctx.engineering_route, stage_ctx.control
        guard, repo_model, repo_slug, se = stage_ctx.guard, stage_ctx.repo_model, stage_ctx.repo_slug, stage_ctx.se
        active_skills, skill_staleness_warnings = stage_ctx.active_skills, stage_ctx.skill_staleness_warnings
        unresolved_skill_gap_names, skills_prompt = stage_ctx.unresolved_skill_gap_names, stage_ctx.skills_prompt
        retrieved_chunks, matched_files, related_files = stage_ctx.retrieved_chunks, stage_ctx.matched_files, stage_ctx.related_files
        retrieval_member_hints, retrieval_symbol_loci = stage_ctx.retrieval_member_hints, stage_ctx.retrieval_symbol_loci
        learned_rag_context = stage_ctx.learned_rag_context
        plan, plan_prompt, owner_candidates = stage_ctx.plan, stage_ctx.plan_prompt, stage_ctx.owner_candidates
        design, architect_files, ownership_findings = stage_ctx.design, stage_ctx.architect_files, stage_ctx.ownership_findings
        active_skill_rules_snapshot = stage_ctx.active_skill_rules_snapshot
        active_skill_manifest = stage_ctx.active_skill_manifest

        # 4. Developer & Quality Gates (Auto-debugging loop)
        _log_phase_banner("DEVELOPMENT & QUALITY GATES")
        logger.info("Developer Agent implementing source files...")
        chain = self.kernel.config.llm_chain
        max_retries = max(4, 1 + len(chain)) if chain else 4
        # See GenerationState/RetryBudgets in kriya/workflow/state.py for the
        # rationale behind each of these counters/flags/caches - state itself
        # was already constructed at the top of this method.
        TARGETED_MAX_RETRIES = 3

        # Rendered once (design does not change across retries) and appended to the
        # full-set task description on every attempt, so the Developer sees an
        # explicit, unambiguous checklist of what the design requires BEFORE
        # generating - not just a punitive check afterward. This is the prevention
        # half of the completeness fix; the missing-file recovery retry below is the
        # cheaper, targeted recovery half for when prevention still doesn't work.
        from kriya.workflow.generation_manifest import build_generation_manifest

        generation_manifest = build_generation_manifest(architect_files)
        required_files_prompt_block = generation_manifest.render_prompt()
        _expected_files_upfront = generation_manifest.ordered_paths
        # basename -> full path, built once from the already-resolved architect_files
        # list (see the Architect call above) so a missing-file recovery retry (below)
        # can resolve a bare basename back to its real path via a simple lookup instead
        # of re-scanning the design's prose with a regex. First occurrence wins on a
        # basename collision across two different directories - a deterministic,
        # rare-edge-case tie-break, not expected to matter in practice.
        _architect_basename_to_path: Dict[str, str] = {}
        for _f in architect_files:
            _architect_basename_to_path.setdefault(os.path.basename(_f), _f)
        # Same prevention-over-punishment pattern as required_files_prompt_block
        # above, for a different completeness failure: a full-set regeneration of
        # pom.xml naturally rewrites it to match the current goal, and can
        # silently drop an existing, goal-irrelevant dependency from an earlier
        # milestone in the process. The reactive "Dependency regression" compile
        # check (PolymorphicValidator.run_compile_check) already catches this
        # after the fact, and _build_full_set_retry_prompt already shows the
        # model its own prior attempt's file content as reference - confirmed
        # live that neither of those was sufficient on their own: the tug-of-war
        # recurred and even worsened across repeated full-set retries in the
        # golden Ignite+Qpid validation (M2 attempts 2 and 4). Computed once from
        # workspace_path's pom.xml (the same stable "original" reference the
        # regression check itself compares against, via original_workspace_path)
        # rather than the worktree's possibly-already-regressed prior attempt.
        # Computed once, before the loop, and threaded into every prompt builder
        # (generation and all three retry modes alike, since they all funnel
        # through the same task_desc string) - a standing invariant, not a
        # reactive one, unlike required_dependencies_prompt_block below which
        # only has something concrete to preserve once a project exists.
        ecosystem_invariant_block = _build_ecosystem_invariant_block(repo_model)

        # Stack-agnostic generalization of skills/ignite-java17/rules.txt's own
        # start-once/reuse/close-once rule, which only ever fires for an
        # Ignite-tagged goal. A goal using any other stateful resource (a JDBC
        # connection, a broker client, a thread pool) gets no equivalent
        # instruction cold-start, with no skill yet to learn it from. Threaded
        # through every prompt builder the same way ecosystem_invariant_block is -
        # a standing invariant present on every attempt, not a reactive one.
        resource_lifecycle_block = RESOURCE_LIFECYCLE_HEADER

        # Threaded through every prompt builder alongside ecosystem_invariant_block/
        # resource_lifecycle_block - see VERIFICATION_CONTRACT_HEADER's own rationale
        # comment in kriya/workflow/retry_prompts.py for why this exists.
        verification_contract_block = VERIFICATION_CONTRACT_HEADER

        required_dependencies_prompt_block = ""
        _original_pom_path = os.path.join(workspace_path, "pom.xml")
        if os.path.exists(_original_pom_path):
            from kriya.tools.validate import get_pom_dependencies
            _existing_dependencies = get_pom_dependencies(_original_pom_path)
            if _existing_dependencies:
                required_dependencies_prompt_block = (
                    "\n\nExisting Maven dependencies (already present in pom.xml before this goal - "
                    "you must preserve ALL of these even if the current goal doesn't need them; "
                    "do not silently drop any while editing pom.xml):\n"
                    + "\n".join(f"- {d}" for d in _existing_dependencies)
                    + "\n\nBefore adding ANY new dependency, check whether the package/class you need "
                    "is already used successfully in the Existing Code Base Context shown below - if an "
                    "import already appears there and that code is known-working, it is ALREADY resolvable "
                    "through one of the dependencies listed above (often transitively) and you must NOT add "
                    "a new, separate dependency for it. Confirmed live as a real bug: a retry added an "
                    "explicit javax.jms:jms:1.1 dependency (which doesn't exist on Maven Central) for a "
                    "'javax.jms.*' import that was already compiling successfully via the existing "
                    "qpid-jms-client dependency alone - the addition was both wrong and unnecessary."
                )

        # VAL-001 brownfield validation baselining (2026-09-18,
        # kriya/workflow/validation_baseline.py) - deliberately BEFORE the
        # sandbox worktree is created a few lines below and before any
        # Developer call anywhere in this method: the ONE point in this
        # method where `workspace_path` is still guaranteed pristine
        # (candidate writes only ever reach `worktree_path`, confirmed
        # untouched at this line - see this same ordering guarantee already
        # documented a few hundred lines above for checkpoint fingerprinting).
        # Opt-in only (autonomy.brownfield_baseline_target_test /
        # brownfield_full_regression_baseline_policy, both default to a
        # complete no-op) - a run that doesn't configure either behaves
        # byte-identically to before this package existed (proven by
        # capture_brownfield_baselines() itself invoking neither injected
        # callable at all when both are unset). brownfield_baseline_
        # target_test may be a single string or an ordered list (VAL-001
        # G1-R3: several specific targets at once) - capture_brownfield_
        # baselines() normalizes it once; this call site passes it straight
        # through unmodified either way.
        autonomy_baseline_cfg = self.kernel.config.autonomy
        # PRD-024 (kriya/workflow/baseline_policy.py): `auto` is decided here,
        # deterministically, from the route, the planned files and the
        # repository's tests - before the first Developer mutation. A
        # triggered `auto` is as binding as `required`.
        from kriya.workflow.baseline_policy import (
            baseline_environment_identity,
            decide_auto_baseline,
            effective_baseline_policy,
        )
        configured_baseline_policy = autonomy_baseline_cfg.brownfield_full_regression_baseline_policy
        auto_baseline_decision = None
        if configured_baseline_policy == "auto":
            try:
                auto_baseline_decision = await asyncio.to_thread(
                    decide_auto_baseline, engineering_route, architect_files or [], workspace_path,
                    workspace_revision=compute_workspace_content_hash(workspace_path),
                )
            except Exception as exc:
                logger.warning(f"Brownfield baseline auto policy could not be evaluated: {exc}")
        full_regression_policy = effective_baseline_policy(configured_baseline_policy, auto_baseline_decision)
        state.validation_baseline_policy = full_regression_policy
        state.record_event(RunEvent(
            kind="validation_baseline.policy", attempt=0, source="workflow.run_generation_workflow",
            authority=EventAuthority.AUXILIARY,
            message=f"brownfield full-regression baseline: {configured_baseline_policy} -> {full_regression_policy}",
            details={"configured": configured_baseline_policy, "effective": full_regression_policy,
                     "auto_decision": auto_baseline_decision.to_dict() if auto_baseline_decision else None},
        ))
        baseline_environment = None
        baseline_runner = None
        if full_regression_policy == "required" or autonomy_baseline_cfg.brownfield_baseline_target_test:
            try:
                baseline_environment = baseline_environment_identity(
                    workspace_path, autonomy_baseline_cfg, goal=goal)
            except Exception as exc:
                logger.warning(f"Baseline environment identity unavailable: {exc}")
            # REG-R1: a pytest baseline must carry per-test evidence; one that
            # does not (an older checkpoint) is re-established, never reused.
            baseline_runner = PolymorphicValidator(
                workspace_path, original_workspace_path=workspace_path, autonomy_cfg=autonomy_baseline_cfg,
            ).test_runner()
        # The baseline's verification call. REG-R1: the same call replays the
        # untouched baseline for stability, so it runs in the same context.
        def baseline_suite_run(target_test):
            return PolymorphicValidator(
                workspace_path, original_workspace_path=workspace_path, autonomy_cfg=autonomy_baseline_cfg,
            ).run_tests(target_test=target_test)

        baseline_capture = capture_brownfield_baselines(
            run_id=run_id,
            target_test=autonomy_baseline_cfg.brownfield_baseline_target_test,
            full_regression_policy=full_regression_policy,
            environment_identity=baseline_environment,
            run_validator=baseline_suite_run,
            compute_revision=lambda: compute_workspace_content_hash(workspace_path),
            resume_baseline_targeted=(resume_state or {}).get("validation_baseline_targeted"),
            resume_baseline_full_regression=(resume_state or {}).get("validation_baseline_full_regression"),
            prior_full_regression=self._prior_full_suite_evidence,
            require_pytest_evidence=baseline_runner == "pytest",
        )
        for _rejection in baseline_capture.reuse_rejections:
            state.record_event(RunEvent(
                kind="validation_baseline.reuse_rejected", attempt=0,
                source="workflow.run_generation_workflow", authority=EventAuthority.AUXILIARY,
                message=f"baseline not reused ({_rejection}); re-established by a fresh capture",
                details={"reason": _rejection},
            ))
        _resumed_stability = (resume_state or {}).get("validation_baseline_pytest_stability")
        if isinstance(_resumed_stability, dict):
            # Each entry is keyed by its own binding; a stale one simply never matches.
            state.pytest_stability_cache.update(_resumed_stability)
        if baseline_capture.full_regression_source is not None:
            state.record_event(RunEvent(
                kind="validation_baseline.full_regression_source", attempt=0,
                source="workflow.run_generation_workflow", authority=EventAuthority.AUXILIARY,
                message=f"full-regression baseline: {baseline_capture.full_regression_source}",
                details={"source": baseline_capture.full_regression_source,
                         "baseline_run_id": getattr(baseline_capture.full_regression, "run_id", None),
                         "workspace_revision": getattr(
                             baseline_capture.full_regression, "workspace_revision", None)},
            ))
        state.validation_baseline_targeted = baseline_capture.targeted
        state.validation_baseline_full_regression = baseline_capture.full_regression
        if baseline_capture.targeted is not None:
            if baseline_capture.targeted.status == "captured":
                logger.info(
                    "Brownfield PRE-mutation targeted validation baseline captured "
                    f"(success={baseline_capture.targeted.outcome.success})."
                )
            else:
                logger.warning(
                    "Brownfield PRE-mutation targeted validation baseline is INDETERMINATE: "
                    f"{baseline_capture.targeted.indeterminate_reason}"
                )
        if baseline_capture.hard_stop_reason is not None and auto_baseline_decision is not None:
            # PRD-024: say which deterministic signals made the baseline required.
            baseline_capture = dataclasses.replace(baseline_capture, hard_stop_reason=(
                "the 'auto' baseline policy required a pristine PRE baseline for this change ("
                + "; ".join(auto_baseline_decision.reasons) + ") - "
                + baseline_capture.hard_stop_reason
            ))
        if baseline_capture.hard_stop_reason is not None:
            # FAILURE_BEHAVIOR: never silently proceed assuming a green
            # baseline - an explicit, distinct terminal status, same shape
            # as the existing knowledge_gap/planner_output_incomplete early
            # returns just above in this same method.
            logger.error(f"Brownfield validation baseline REQUIRED but indeterminate: {baseline_capture.hard_stop_reason}")
            try:
                from kriya.control.trace import TraceLogger
                trace_db = trace_db_path(self.kernel.config)
                TraceLogger(trace_db).log_run(
                    run_id=trace_id, goal=goal, duration_sec=time.time() - start_time,
                    attempts=0, status="baseline_indeterminate", files_modified=[],
                    failure_category="baseline_indeterminate",
                    milestone_group_id=milestone_group_id, milestone_index=milestone_index,
                    milestone_total=milestone_total, enforce_run_id=enforce_run_id,
                    run_events=self._trace_run_events(state),
                )
            except Exception as trace_ex:
                logger.warning(f"Failed to write run trace: {trace_ex}")
            return {
                "status": "baseline_indeterminate",
                "reason": baseline_capture.hard_stop_reason,
                "plan": plan,
                "design": design,
                "goal": goal,
                "workspace_path": workspace_path,
                "run_id": trace_id,
                "quality_gates_passed": False,
            }

        # Create isolated git worktree sandbox
        worktree_path = workspace_path
        try:
            worktree_path = create_git_worktree(workspace_path)
            if os.path.realpath(worktree_path) == os.path.realpath(workspace_path):
                # BACKEND-READINESS-V2 (D0): the invariant create_git_worktree already keeps (a sandbox path or an error),
                # asserted where the direct path depends on it. In-place generation could not even commit (the terminal
                # revision guard sees its own writes as a conflict) and left unsafe bytes behind on a refused run.
                raise RuntimeError("the isolated generation sandbox resolves to the application workspace itself")
            logger.info(f"Isolated sandbox worktree created at: {worktree_path}")
            mark_run_stage(workspace_path, RunLifecycle.CANDIDATE)
        except WorktreeSyncError as sync_error:
            # FILE-INTEGRITY-CONTRACT-001: a sandbox that does not hold the
            # workspace's exact bytes is a typed deterministic stop, never a
            # generic sandbox error and never verified against.
            logger.error(f"Worktree sync refused: {sync_error}")
            category = sync_error.reason_code.lower()
            try:
                from kriya.control.trace import TraceLogger
                TraceLogger(trace_db_path(self.kernel.config)).log_run(
                    run_id=trace_id, goal=goal, duration_sec=time.time() - start_time,
                    attempts=0, status="failure", files_modified=[], failure_category=category,
                    milestone_group_id=milestone_group_id, milestone_index=milestone_index,
                    milestone_total=milestone_total, enforce_run_id=enforce_run_id, run_events=self._trace_run_events(state),
                )
            except Exception as trace_ex:
                logger.warning(f"Failed to write run trace: {trace_ex}")
            return {
                "status": "failure", "failure_category": category, "reason_codes": [sync_error.reason_code],
                "error": str(sync_error), "plan": plan, "design": design, "goal": goal,
                "workspace_path": workspace_path, "run_id": trace_id, "files": [],
                "quality_gates_passed": False,
            }
        except Exception as e:
            raise RuntimeError(
                f"Failed to create an isolated generation sandbox: {e}. "
                "Refusing to generate directly in the application workspace."
            ) from e

        # P2: a plan-scope re-invocation keeps the grounded loci of the
        # failure that widened its scope (revision-bound; a stale one is dropped).
        if grounded_locations:
            _seed_grounded_loci(state, worktree_path, grounded_locations)

        # Resolved ONCE, here, against workspace_path BEFORE any Developer
        # write happens (worktree_path above is an isolated copy - writes
        # never touch workspace_path directly until the approval/copy-back
        # step at the very end of this call) - mirrors write_scope_mode's own
        # "resolved once, read everywhere" pattern below. A caller that
        # already resolved this identity once for the whole run (e.g.
        # WorkflowController, across several bounded-subtask calls) passes
        # migration_resolution directly so every subtask's own attempt reuses
        # the SAME resolution rather than each one re-deriving its own from
        # its own (already-progressed) plan_workspace_path. See kriya/
        # workflow/migration.py's own docstring (PRV-05 run 6, 2026-08-28)
        # for why re-resolving per call site is itself the defect this
        # closes.
        resolved_migration_resolution = (
            migration_resolution if migration_resolution is not None
            else resolve_migration_resolution(goal, workspace_path)
        )
        # MA8 (PRV-05 run #8, 2026-08-28) - kriya/workflow/obligations.py.
        # A caller that already created one per-run ledger (WorkflowController,
        # across several bounded-subtask calls) passes it through so every
        # subtask's obligations accumulate into the SAME instance; a plain
        # Legacy call gets its own fresh one, mirroring migration_resolution's
        # own "resolved once, reused, or created fresh for this call" pattern.
        resolved_obligation_ledger = obligation_ledger if obligation_ledger is not None else ObligationLedger()
        # PRD-008: a reused candidate comes with the effective ledger it was
        # verified against (its fingerprint matched the checkpoint's). The
        # input ledger matched too, so this only adds what that run recorded;
        # restored in place because callers share one ledger object.
        if resume_plan is not None and resume_plan.reuse_candidate and resume_restored_ledger is not None:
            resolved_obligation_ledger.restore_from(resume_restored_ledger)
        if requirement_set is not None:
            # PRD-020: every original requirement is tracked (PENDING until the
            # verifier records an outcome); idempotent over a restored ledger.
            seed_requirement_obligations(resolved_obligation_ledger, requirement_set)
            if verification_contract is not None:
                # VERIFICATION-CONTRACT-003 (D1): the contract's structural
                # non-claims need no closure; recorded once, visible everywhere.
                from kriya.workflow.contract_compilation import record_contract_dispositions, record_contract_non_claims

                record_contract_non_claims(resolved_obligation_ledger, requirement_set, verification_contract,
                                           source="workflow.verification_contract")
                # BACKEND-READINESS-004 (D3): the operator's sealed dispositions, recorded once as audit.
                record_contract_dispositions(resolved_obligation_ledger, requirement_set, verification_contract,
                                             source="workflow.verification_contract")
        if ownership_findings:
            record_findings(resolved_obligation_ledger, ownership_findings, revision=0,
                            source="workflow.architect_file_resolution")
        # PRV-17 (2026-09-08, P7 efficiency finding) - kriya/workflow/
        # deterministic_failure_diagnostic.py. Same "resolved once, reused
        # across every bounded-subtask call, or created fresh for a plain
        # Legacy call" pattern as resolved_obligation_ledger immediately
        # above - deliberately a separate store, not folded into the
        # ObligationLedger itself (see that module's own docstring for why).
        resolved_deterministic_failure_diagnostics = (
            deterministic_failure_diagnostics if deterministic_failure_diagnostics is not None
            else DeterministicFailureDiagnosticStore()
        )

        # Loop-invariant - nothing in this object is reassigned across retry
        # attempts, so it's built once here rather than reconstructed per
        # iteration. See kriya/workflow/attempt.py for what actually happens
        # inside a single attempt.
        attempt_ctx = AttemptContext(
            goal=goal,
            plan=plan,
            design=design,
            workspace_path=workspace_path,
            worktree_path=worktree_path,
            architect_files=architect_files,
            resume_state=resume_state,
            run_id=run_id,
            skills_prompt=skills_prompt,
            learned_rag_context=learned_rag_context,
            matched_files=matched_files,
            related_files=related_files,
            planned_source_files=tuple(planned_source_files or ()),
            ecosystem_invariant_block=ecosystem_invariant_block,
            resource_lifecycle_block=resource_lifecycle_block,
            verification_contract_block=verification_contract_block,
            recovery_contract_block=recovery_contract_block,
            required_files_prompt_block=required_files_prompt_block,
            required_dependencies_prompt_block=required_dependencies_prompt_block,
            expected_files_upfront=_expected_files_upfront,
            architect_basename_to_path=_architect_basename_to_path,
            chain=chain,
            targeted_max_retries=TARGETED_MAX_RETRIES,
            stream_callback=stream_callback,
            approval_callback=approval_callback,
            defer_execution_until_source_review_enabled=True,
            active_skills=active_skills,
            active_skill_rules_snapshot=active_skill_rules_snapshot,
            developer=self.developer,
            run_verifier=self.run_verifier,
            spec_compliance=self.spec_compliance,
            skill_engine=se,
            kernel=self.kernel,
            max_retries=max_retries,
            web_lookup_query_callback=web_lookup_query_callback,
            approve_web_lookup=self._approve_web_lookup,
            generation_dependencies={
                entry.path: list(entry.depends_on)
                for entry in generation_manifest.entries
            },
            established_files=established_files or [],
            retrieval_member_hints=retrieval_member_hints,
            retrieval_symbol_loci=retrieval_symbol_loci,
            protected_relpath=protected_relpath,
            allowed_write_relpaths=list(allowed_write_relpaths or []),
            authorized_semantic_regions=list(authorized_semantic_regions or []),
            required_verification=list(required_verification or []),
            contract_feedback=build_contract_feedback(
                self, worktree_path, workspace_path, self.kernel.config.autonomy),
            # CANDIDATE-GATE-BASELINE-POLICY-001: the baseline's own
            # verification call, for the candidate gates' attribution.
            baseline_suite_replay=baseline_suite_run,
            # Review F1 / MILESTONE-ZERO-COMMIT-COMPLETION-001: a milestone's zero-change result is decided by the
            # milestone driver (kriya/workflow/milestones.py _complete_milestone: a deterministic no-change proof
            # completes it, a refusal fails it typed); the integration pass is decided by the plan-level original-
            # requirement verification and its gates (PRD-020 - a VIOLATED requirement always blocks; an unverified
            # one blocks under the "block" requirement policies the production profile seals or, whatever the policy,
            # when the verifier reported it missing - GR-R0; INTEGRATION-PASS-REQUIREMENT-POLICY-001). A direct goal has no
            # such decider: the typed stop below is its own.
            no_change_verifier_downstream=milestone_group_id is not None,
            # Resolved ONCE, here, mirroring AuthorizedFileWriter's own
            # backward-compatible inference (kriya/policy/filesystem.py) so
            # every write-gate call site downstream (kriya/workflow/
            # attempt.py) reads the SAME unambiguous value instead of each
            # re-deriving it from allowed_write_relpaths' truthiness - see
            # WriteScopeMode's own docstring for the PRV-05 incident this
            # closes. A caller that already knows its scope (e.g.
            # WorkflowController's bounded-subtask execution, for a
            # verification-role subtask) passes write_scope_mode directly;
            # every other caller keeps today's behavior exactly.
            write_scope_mode=(
                write_scope_mode if write_scope_mode is not None
                else (WriteScopeMode.ALLOWLIST if allowed_write_relpaths else WriteScopeMode.UNRESTRICTED)
            ),
            runtime_verification_required=(
                goal_requires_runtime_behavior(goal)
                if runtime_verification_required is None
                else runtime_verification_required
            ),
            strict_spec_compliance=strict_spec_compliance,
            execution_scope=execution_scope,
            reopened_owner=reopened_owner,
            grounding_goal=grounding_goal,
            exit_authority_goal=(
                (work_unit.authoritative_goal if work_unit is not None else None) or grounding_goal or ""
            ),
            migration_resolution=resolved_migration_resolution,
            structured_plan=structured_plan,
            current_subtask_id=current_subtask_id,
            obligation_ledger=resolved_obligation_ledger,
            completed_subtask_ids=completed_subtask_ids or frozenset(),
            deterministic_failure_diagnostics=resolved_deterministic_failure_diagnostics,
            resume_plan=resume_plan,
            requirement_set=requirement_set,
        )

        # What the review, approval/apply and lesson stages (E4) read besides the design stages' outputs.
        stage_ctx.worktree_path, stage_ctx.resolved_obligation_ledger = worktree_path, resolved_obligation_ledger
        stage_ctx.grounding_goal, stage_ctx.work_unit = grounding_goal, work_unit
        stage_ctx.run_trace = run_trace

        from kriya.workflow.model_transition import fallback_routing_for_state
        from kriya.workflow.retry_policy import decide_for_state
        while decide_for_state(
            state, max_retries=max_retries,
            targeted_max_retries=TARGETED_MAX_RETRIES,
            has_fallback_model=fallback_routing_for_state(state, self.kernel.config, chain, worktree_path).available,
        ).should_continue:
            # Reset once per loop iteration, unconditionally - NOT just inside the "4.5"
            # section below. Independent review (2026-08-15) found the narrower reset
            # placement genuinely insufficient: run_attempt() (called just below) raises
            # QualityGateFailure directly from many sites (compile-check, targeted-test,
            # anchored-edit failures) that jump straight to this loop's `except` handler,
            # skipping "4.5" entirely for that iteration. If THAT failure is what
            # ultimately ends the loop (an immediate environment_failure break, or
            # ordinary retry-budget exhaustion), a stale review from an EARLIER,
            # now-superseded attempt that DID reach "4.5" would otherwise survive and get
            # returned as if it described the final (failing) attempt's content - a
            # concretely reproduced bug, not a theoretical one. Resetting here, before
            # run_attempt() is even called, guarantees no exception path can skip it.
            state.pre_approval_review = None
            state.verified_candidate_binding = None
            attempt_ctx.deferred_executing_gates = None
            attempt_ctx.post_source_review_guard = None
            # LR-R1-M1: the whole iteration is the attempt's evidence identity
            # (observational; reset in this try's finally).
            attempt_evidence_token = attempt_evidence_scope.enter_attempt_iteration(lambda: state.attempt_number)
            try:
                # Best-of-N only ever applies to the very first attempt of a run
                # (state.attempt_number == 0 going in - resumed checkpoints also
                # start here, but short-circuit to a trivial success on the first
                # call, so they're unaffected), and only when a real isolated
                # worktree sandbox actually exists (worktree_path != workspace_path)
                # - without one, "discarding a candidate" has nothing to reset and
                # would leave that candidate's files on the real project.
                best_of_n = self.kernel.config.autonomy.best_of_n_first_attempt
                if state.attempt_number == 0 and best_of_n > 1 and worktree_path != workspace_path:
                    from kriya.workflow.best_of_n import run_attempt_with_best_of_n
                    await run_attempt_with_best_of_n(state, attempt_ctx, n=best_of_n)
                else:
                    await run_attempt(state, attempt_ctx)
                # CANDIDATE-VERIFIED-DIGEST-BINDING-001: the candidate gates
                # passed on exactly these bytes. Everything after this
                # (requirements, static analysis, approval, terminal
                # regression) judges the same candidate, and the terminal
                # commit refuses any batch that differs from this binding.
                if state.verification_tree_binding is not None:
                    state.verification_tree_binding.check("terminal_binding")
                state.verified_candidate_binding = _bind_direct_candidate(worktree_path, workspace_path, state)

                # Authoritative subtask verification is part of the pre-apply
                # success boundary.  A skipped/unavailable check or unresolved
                # judgment must never reach approval or copy sandbox files into
                # the real workspace merely because the legacy aggregate gate
                # boolean remained true.
                def _raise_if_required_verification_unresolved(execution_pending: bool = False) -> None:
                    # execution_pending: the executing gates are held until the source is approved (Gap B), so only the requirements that those gates resolve (the builtin
                    # compile/test tools, a judgment that needs runtime execution) wait for them; every other unresolved requirement still stops the run before any approval.
                    checkable = [
                        item for item in required_verification or []
                        if not (execution_pending and (
                            (item.get("type") == "tool" and item.get("tool_name") in BUILTIN_QUALITY_GATE_VERIFIERS)
                            or (item.get("type") == "judgment" and item.get("requires_runtime_execution") is True)))
                    ]
                    required_evidence = _build_required_verification_evidence(
                        checkable,
                        quality_gates_passed=True,
                        gate_outcomes=state.gate_outcomes,
                    )
                    unresolved_requirements = [
                        item.get("description", "") for item in required_evidence
                        if item.get("passed") is not True
                    ]
                    if unresolved_requirements:
                        message = (
                            "REQUIRED VERIFICATION UNRESOLVED: no authoritative passing evidence "
                            f"was produced for {unresolved_requirements!r}; refusing pre-apply success."
                        )
                        failure = Failure(
                            type="verification_infrastructure_failure",
                            message=message,
                            raw_output=message,
                            attempt=state.attempt_number,
                        )
                        state.record_gate_outcome(failure.to_gate_outcome())
                        raise QualityGateFailure(failure)

                # Gap B: when the runtime execution is held until the person has approved the source (human-in-the-loop), the requirements that need it cannot be judged yet;
                # the complete check runs again right after that deferred step (below), before anything is applied.
                _raise_if_required_verification_unresolved(execution_pending=attempt_ctx.deferred_executing_gates is not None)

                # PRD-022: near-duplicates the candidate actually created,
                # against the grounded owners PRD-021 found before planning.
                # Advisory evidence for review and approval - never a gate.
                if owner_candidates:
                    try:
                        _record_post_generation_ownership_findings(
                            state, owner_candidates, resolved_obligation_ledger, worktree_path, goal,
                            current_subtask_id, parse_ownership_justifications(str(design or "")),
                        )
                    except Exception as exc:
                        logger.warning(f"Post-generation ownership findings unavailable: {exc}")

                async def _close_and_check_requirements() -> None:
                    """PRD-020 closers + the blocking decision. They EXECUTE the candidate's tests (named tests, acceptance, suite preservation), so in human-in-the-loop mode they run after
                    the source approval together with the other executing gates (Gap B); otherwise at their original place."""
                    # PRD-020: the user's original requirements are part of the
                    # pre-apply success boundary. Only the verifier's recorded
                    # outcomes count (never plan/design/review prose); what a
                    # non-satisfied outcome does is the requirement policy's call.
                    if requirement_set is not None:
                        autonomy_policy = self.kernel.config.autonomy
                        # "Do not modify any other file": decided from what the run
                        # actually changed (this candidate plus the run's committed
                        # history) against the files the goal itself names.
                        try:
                            scope_closures = await asyncio.to_thread(
                                close_requirements_by_mutation_scope, resolved_obligation_ledger, requirement_set,
                                worktree_path, workspace_path, candidate_paths=state.all_files_written,
                                revision=state.attempt_number,
                            )
                        except Exception as exc:
                            scope_closures = []
                            logger.warning(f"Requirement mutation-scope evidence unavailable: {exc}")
                        # FS-1C2 B2-a: the operator's acceptance file judges the
                        # behaviour claims it covers on this candidate.
                        try:
                            acceptance_closures = await asyncio.to_thread(
                                close_requirements_with_acceptance_tests, self.kernel.config.autonomy,
                                resolved_obligation_ledger, requirement_set, worktree_path, workspace_path,
                                acceptance=bound_acceptance(self), approval=bound_approval(self),
                                modified=state.all_files_written, revision=state.attempt_number,
                                toolchain_declaration_mutable=toolchain_declaration_mutable(
                                    write_scope_mode, allowed_write_relpaths, structured_plan,
                                ),
                                contract=verification_contract,
                            )
                        except Exception as exc:
                            acceptance_closures = []
                            logger.warning(f"Requirement acceptance evidence unavailable: {exc}")
                        # VERIFICATION-CONTRACT-003: the goal's compiled examples, the
                        # API predicate, the test-addition record and the documentation
                        # referent - each closer bound by the sealed contract.
                        contract_closures: List[Dict[str, Any]] = []
                        for closer_name, closer_call in (
                            ("external authority", lambda: close_requirements_with_authority_bundle_evidence(
                                self.kernel.config.autonomy, resolved_obligation_ledger, requirement_set,
                                verification_contract, worktree_path, workspace_path, engine=self,
                                revision=state.attempt_number,
                                toolchain_declaration_mutable=toolchain_declaration_mutable(
                                    write_scope_mode, allowed_write_relpaths, structured_plan))),
                            ("derived examples", lambda: close_requirements_with_derived_examples(
                                self.kernel.config.autonomy, resolved_obligation_ledger, requirement_set, worktree_path,
                                workspace_path, engine=self, modified=state.all_files_written,
                                revision=state.attempt_number, contract=verification_contract,
                                toolchain_declaration_mutable=toolchain_declaration_mutable(
                                    write_scope_mode, allowed_write_relpaths, structured_plan))),
                            ("API preservation", lambda: close_requirements_by_api_preservation(
                                resolved_obligation_ledger, requirement_set, verification_contract, worktree_path,
                                workspace_path, candidate_paths=state.all_files_written, revision=state.attempt_number)),
                            ("test addition", lambda: close_requirements_by_test_addition(
                                resolved_obligation_ledger, requirement_set, verification_contract, worktree_path,
                                workspace_path, revision=state.attempt_number)),
                            ("documentation", lambda: close_requirements_by_documentation(
                                resolved_obligation_ledger, requirement_set, verification_contract, worktree_path,
                                revision=state.attempt_number)),
                        ):
                            try:
                                contract_closures += await asyncio.to_thread(closer_call)
                            except Exception as exc:
                                logger.warning(f"Requirement {closer_name} evidence unavailable: {exc}")
                        # An UNVERIFIED (cannot confirm from code) requirement whose
                        # own text names existing tests is closed only by running
                        # exactly those tests on this candidate.
                        try:
                            closures = await asyncio.to_thread(
                                close_requirements_with_named_tests, self.kernel.config.autonomy,
                                resolved_obligation_ledger, requirement_set, worktree_path, workspace_path,
                                modified=state.all_files_written, revision=state.attempt_number,
                                toolchain_declaration_mutable=toolchain_declaration_mutable(
                                    write_scope_mode, allowed_write_relpaths, structured_plan,
                                ),
                                java_home_override=state.java_home_override, contract=verification_contract,
                            )
                        except Exception as exc:
                            closures = []
                            logger.warning(f"Requirement closure by named tests unavailable: {exc}")
                        # REQUIREMENT-CLOSURE-PLAIN-GOAL-001: the run's mutation
                        # record decides "do not change any existing test"; the
                        # candidate's own complete, green full suite closes
                        # "every existing test must keep passing".
                        try:
                            immutability_closures = await asyncio.to_thread(
                                close_requirements_by_test_immutability, resolved_obligation_ledger, requirement_set,
                                worktree_path, workspace_path, candidate_paths=state.all_files_written,
                                revision=state.attempt_number, contract=verification_contract,
                            )
                        except Exception as exc:
                            immutability_closures = []
                            logger.warning(f"Requirement test-immutability evidence unavailable: {exc}")
                        # OD-3: the files the goal froze by name, judged from the same mutation record.
                        try:
                            frozen_closures = await asyncio.to_thread(
                                close_requirements_by_file_immutability, resolved_obligation_ledger, requirement_set,
                                worktree_path, workspace_path, candidate_paths=state.all_files_written,
                                revision=state.attempt_number, contract=verification_contract,
                            )
                        except Exception as exc:
                            frozen_closures = []
                            logger.warning(f"Requirement file-immutability evidence unavailable: {exc}")
                        try:
                            suite_closures = await asyncio.to_thread(
                                close_requirements_by_suite_preservation, self.kernel.config.autonomy,
                                resolved_obligation_ledger, requirement_set, worktree_path, workspace_path,
                                revision=state.attempt_number, candidate_paths=state.all_files_written,
                                toolchain_declaration_mutable=toolchain_declaration_mutable(
                                    write_scope_mode, allowed_write_relpaths, structured_plan,
                                ),
                                java_home_override=state.java_home_override, contract=verification_contract,
                                # SUITE-PRESERVATION-CLOSURE-BASELINE-ATTRIBUTION-001: the run's own captured
                                # baseline, stability cache and replay - no second baseline run on this path.
                                goal=goal, suite_baseline=state.validation_baseline_full_regression,
                                stability_cache=state.pytest_stability_cache, baseline_suite_run=baseline_suite_run,
                                baseline_source=SUITE_BASELINE_FROM_RUN,
                                run_baseline_policy=state.validation_baseline_policy,
                            )
                        except Exception as exc:
                            suite_closures = []
                            logger.warning(f"Requirement suite-preservation evidence unavailable: {exc}")
                        closures = (scope_closures + acceptance_closures + contract_closures + closures
                                    + immutability_closures + frozen_closures + suite_closures)
                        if closures:
                            state.record_event(RunEvent(
                                kind="requirement.closure", attempt=state.attempt_number,
                                source="workflow.requirement_closure", authority=EventAuthority.AUTHORITATIVE,
                                message="unverified requirement closure: " + ", ".join(
                                    f"{c['requirement']}={'closed' if c['closed'] else 'open'}" for c in closures),
                                details={"requirement_set_digest": requirement_set.digest, "closures": closures},
                            ))
                        blocking = blocking_requirements(
                            resolved_obligation_ledger, requirement_set,
                            unknown_policy=autonomy_policy.requirement_unknown_policy,
                            unverified_policy=autonomy_policy.requirement_unverified_policy,
                        )
                        if blocking:
                            detail = "; ".join(
                                f"{req.id} ({outcome.value}): {req.text}" for req, outcome in blocking)
                            message = (
                                f"{REQUIREMENTS_UNRESOLVED}: original requirement(s) without accepted "
                                f"evidence - {detail}"
                            )
                            failure = Failure(
                                type="requirements_unresolved", message=message, raw_output=message,
                                source="orchestrator", attempt=state.attempt_number,
                                diagnostics={
                                    "reason_code": REQUIREMENTS_UNRESOLVED,
                                    "requirement_set_digest": requirement_set.digest,
                                    "blocking": {req.id: outcome.value for req, outcome in blocking},
                                },
                            )
                            state.record_gate_outcome(failure.to_gate_outcome())
                            raise QualityGateFailure(failure)

                if attempt_ctx.deferred_executing_gates is None:
                    await _close_and_check_requirements()


                # PRD-031A: the static-analysis gate, on the exact batch the
                # terminal apply below commits (it re-materializes it, and the
                # commit guard proves the two are byte-identical). Before the
                # approval gate, so a human sees the outcome. A non-permitting
                # result is a deterministic stop, never a Developer retry, and
                # its message carries no scanner text.
                _run_static_analysis_gate(
                    self.kernel.config, state, worktree_path=worktree_path, workspace_path=workspace_path,
                    run_id=run_id,
                    unit_id=f"{work_unit.work_unit_id if work_unit is not None else 'direct'}-attempt{state.attempt_number}",
                )

                # BACKEND-READINESS-V2: the exception-masking rule on the same final batch.
                _run_semantic_safety_gate(state, worktree_path=worktree_path, workspace_path=workspace_path)

                # Candidate-only checkpoint: generation and inner gates passed, but
                # terminal regression and application have not. Its name and payload
                # preserve that distinction for resume and external inspection.
                # PRD-008: the candidate is reusable only if every written
                # file was captured exactly (strict UTF-8, no newline
                # translation); otherwise no digest is stored and a resume
                # regenerates it instead of rebuilding a partial candidate.
                final_files_for_checkpoint = {}
                candidate_snapshot_complete = True
                for filepath in state.all_files_written:
                    try:
                        with open(os.path.join(worktree_path, filepath), "rb") as fh:
                            final_files_for_checkpoint[filepath] = fh.read().decode("utf-8")
                    except Exception as ex:
                        candidate_snapshot_complete = False
                        logger.warning(
                            f"Candidate file '{filepath}' could not be captured exactly for the "
                            f"checkpoint ({type(ex).__name__}: {ex}); a resume will regenerate the candidate."
                        )
                candidate_checkpoint_fields = {
                    CANDIDATE_HASH_KEY: (
                        candidate_snapshot_digest(final_files_for_checkpoint)
                        if candidate_snapshot_complete else None
                    ),
                    EFFECTIVE_LEDGER_KEY: resolved_obligation_ledger.to_snapshot(),
                }
                def _save_candidate_gates_checkpoint(checkpoint_fields: Dict[str, Any], checkpoint_files: Dict[str, str]) -> None:
                    _save_stage_checkpoint(
                        "candidate_gates_passed",
                        effective_obligation_ledger=resolved_obligation_ledger,
                        **checkpoint_fields,
                        plan=plan,
                        design=design,
                        final_files=checkpoint_files,
                        original_files=state.all_original_contents,
                        gate_outcomes=state.gate_outcomes,
                        model_hops=state.model_hops,
                        retry_count=state.budgets.retry_count,
                        targeted_retry_count=state.budgets.targeted_retry_count,
                        candidate_gates_passed=True,
                        terminal_regression_passed=False,
                        overall_attempt_passed=False,
                    )

                # Gap B: "candidate gates passed" is only true once the held runtime verification has also run, so a resume can never skip it. Saved after that step when held.
                if attempt_ctx.deferred_executing_gates is None:
                    _save_candidate_gates_checkpoint(candidate_checkpoint_fields, final_files_for_checkpoint)

                # 4.5. Pre-Apply Human Approval Gate (kriya/workflow/stage_apply.py::approval_gate, full modularization E4):
                # the change set, the approval triggers, the pre-approval review and the request; an abort (the person
                # rejected the change, or approval was required and nobody can be asked) is this method's result.
                approval_stop, need_human_approval = await approval_gate(
                    stage_ctx, self._late_stage_deps(), attempt_ctx=attempt_ctx,
                )
                if approval_stop is not None:
                    return approval_stop

                # Gap B (owner decision 2026-10-15, stronger form): in human-in-the-loop mode every gate that EXECUTES the candidate (POM validation, build, tests, runtime
                # verification) and the requirement closers that run its tests were held back by run_attempt until the person approved the source (a source approval here means
                # "this exact candidate may keep being verified"; nothing is applied to the real workspace until the terminal gate). They run now, against the same bytes: the
                # byte binding taken when the candidate gates were reached must still hold before each execution boundary and after the last, the self-correction micro-loops
                # are off, and a failure re-enters the ordinary retry loop whose new candidate is reviewed again.
                deferred_runtime = attempt_ctx.deferred_executing_gates
                attempt_ctx.deferred_executing_gates = None
                if deferred_runtime is not None:
                    if not (need_human_approval and approval_callback):
                        message = (
                            "EXECUTION_DEFERRED_WITHOUT_SOURCE_REVIEW: verification was held for a source review that was not asked. "
                            "Nothing was run and nothing was applied."
                        )
                        failure = Failure(
                            type="verification_infrastructure_failure", message=message, raw_output=message, attempt=state.attempt_number,
                        )
                        state.record_gate_outcome(failure.to_gate_outcome())
                        raise QualityGateFailure(failure)
                    reviewed_binding = state.verified_candidate_binding

                    def _candidate_is_the_reviewed_one(reviewed: Any = reviewed_binding) -> bool:
                        current_binding = _bind_direct_candidate(worktree_path, workspace_path, state)
                        return reviewed is not None and current_binding is not None and current_binding.digest == reviewed.digest

                    attempt_ctx.post_source_review_guard = _candidate_is_the_reviewed_one
                    try:
                        assert_candidate_unchanged_since_source_review(state, attempt_ctx)
                        await deferred_runtime()
                        assert_candidate_unchanged_since_source_review(state, attempt_ctx)
                        _raise_if_required_verification_unresolved()
                        await _close_and_check_requirements()      # it runs the candidate's tests: still under the guard, checked again after it
                        assert_candidate_unchanged_since_source_review(state, attempt_ctx)
                    except Exception:
                        # The review shown with the final failure must describe what finally failed, not the pre-build review the person approved from: drop it so the rejected-
                        # candidate diagnostic review (Demo-01 Finding 3) is produced, exactly as for a failure inside run_attempt.
                        state.pre_approval_review = None
                        raise
                    finally:
                        attempt_ctx.post_source_review_guard = None
                    _save_candidate_gates_checkpoint(candidate_checkpoint_fields, final_files_for_checkpoint)

                # Worktree cleanup deliberately does NOT happen here anymore - see the
                # real live incident this fix closes, 2026-08-22 (ignite_qpid_protocol
                # milestone 2/3): compile+run-verification passing is not the same as
                # "done" - the regression test suite below still has to pass too, and
                # a regression failure sends the loop back for another Developer/
                # Quality-Gates retry that needs a working worktree. Resetting the
                # worktree here (git checkout -f HEAD + git clean -fd) wiped every
                # established-but-never-committed file (pom.xml, and milestone 1's own
                # Protocol.java/ProtocolParser.java/ProtocolTest.java) the instant a
                # regression failure occurred, with no re-sync step before the next
                # attempt - only files a subsequent retry happened to rewrite (e.g.
                # App.java) ever reappeared. A file nobody touched again (pom.xml
                # itself, once a targeted retry answered "NO CHANGE NEEDED" for it)
                # stayed missing, so `run_compile_check()`'s `os.path.exists(pom.xml)`
                # silently went False and fell through to the raw-javac fallback,
                # which then failed with a confusing "file not found: Protocol.java" -
                # a build-lifecycle bug that looked exactly like a code/context defect.
                # Moved to fire only once the regression suite has ALSO passed, right
                # before quality_gates_succeeded is actually set - the worktree now
                # stays alive for as long as any retry can still need it, matching how
                # every other retry in this loop already treats worktree lifetime
                # (kept until final success or final budget exhaustion via
                # retry_strategy.py's own remove_git_worktree call).

                # Stage 7, before the terminal regression (kriya/workflow/stage_apply.py::prepare_lessons): the
                # dependency-skill auto-accrual and the hard-won-knowledge trigger.
                self_corrected_outcome, should_extract_hard_won_knowledge = prepare_lessons(
                    stage_ctx, self._late_stage_deps(),
                )

                # If targeted gates passed, run the full regression suite against
                # the still-isolated candidate. The real application workspace is
                # deliberately untouched until this terminal gate passes.
                logger.info("Quality Gates: Running full test suite regression check...")
                validator = PolymorphicValidator(
                    worktree_path, original_workspace_path=workspace_path,
                    autonomy_cfg=self.kernel.config.autonomy,
                    toolchain_declaration_mutable=toolchain_declaration_mutable(
                        write_scope_mode, allowed_write_relpaths, structured_plan,
                    ),
                )
                validator.tree_binding = state.verification_tree_binding

                if not state.toolchain_checked:
                    state.toolchain_checked = True
                    state.toolchain_warning = _check_java_toolchain_mismatch(validator.stack)
                    if state.toolchain_warning:
                        logger.warning(f"Toolchain preflight: {state.toolchain_warning}")
                    if validator.stack == "java":
                        state.java_home_override = _resolve_java_home_override(goal)
                        if state.java_home_override:
                            logger.warning(
                                "JVM toolchain enforcement: forcing Maven subprocess calls to "
                                f"run under JAVA_HOME={state.java_home_override} - the goal-stated Java "
                                "version doesn't match what 'mvn' resolves to by default here."
                            )
                validator.java_home_override = state.java_home_override

                full_test_res = validator.run_tests()
                _stop_on_environment_gate_result(state, full_test_res, "regression_test")
                state.terminal_full_suite_result = full_test_res

                # VAL-001 brownfield validation baselining - ONLY changes
                # the blocking decision when a full-regression baseline was
                # actually captured (autonomy.brownfield_full_regression_
                # baseline_policy == "required"); otherwise
                # `_regression_should_block` is byte-identical to the
                # pre-existing `not full_test_res["success"]` check, so a
                # run that never opted in behaves exactly as before this
                # package existed. A captured baseline lets a genuinely
                # PRE-EXISTING failure (present before Kriya ever touched
                # this brownfield repository) pass through without
                # blocking candidate acceptance - invariant 4 - while
                # NEW_FAILURE/CHANGED_FAILURE/an unexplained disappearance
                # of previously-executed validation still blocks exactly
                # as strictly as before (invariants 5/"fail conservatively").
                _regression_should_block = not full_test_res["success"]
                _baseline_delta_result = None
                # CANDIDATE-GATE-BASELINE-POLICY-001: the one attribution
                # owner (kriya/workflow/suite_attribution.py) - the same
                # decision the candidate gates now make.
                _full_baseline = captured_baseline(state.validation_baseline_full_regression)
                if _full_baseline is not None:
                    _full_attribution = attribute_suite_result(
                        scope=SCOPE_FULL_REGRESSION, baseline=_full_baseline, result=full_test_res,
                        post_environment=post_environment_identity(
                            baseline=_full_baseline, workspace_path=workspace_path, worktree_path=worktree_path,
                            autonomy_cfg=self.kernel.config.autonomy, goal=goal, written_files=state.all_files_written),
                        stability_cache=state.pytest_stability_cache, replay=baseline_suite_run,
                        workspace_path=workspace_path,
                    )
                    _baseline_delta_result = _full_attribution.delta
                    _regression_should_block = _baseline_delta_result.blocking
                    state.record_event(RunEvent(
                        kind="validation_baseline.full_regression_delta",
                        attempt=state.attempt_number,
                        source="workflow.run_generation_workflow",
                        authority=EventAuthority.ADVISORY,
                        message=f"Full-regression {_full_attribution.message()}",
                        details=_full_attribution.event_details(),
                    ))

                # VAL-001 G1-R3 (2026-09-18): the targeted baseline's own
                # POST comparison - separate from, and additive to, the
                # full-regression one immediately above. A no-op (zero extra
                # subprocess calls, _targeted_regression_should_block stays
                # False) whenever brownfield_baseline_target_test was never
                # set, matching this package's own established "opt-in,
                # complete no-op otherwise" precedent exactly.
                #
                # POST_REPLAY invariant: the target(s) re-run here are read
                # from `state.validation_baseline_targeted.invocation.
                # target_test` - the frozen, structural selection PRE
                # capture itself used - NEVER re-read from
                # `self.kernel.config.autonomy.brownfield_baseline_target_
                # test` fresh. The two are the same value for an ordinary,
                # non-resumed run, but a resumed run could in principle carry
                # a checkpointed PRE baseline captured under a DIFFERENT
                # config than the one active now - replaying the frozen
                # invocation, not re-deriving from current config/workspace
                # state, is what makes "PRE and POST validation command/
                # selection identities are frozen and equivalent" true by
                # construction rather than by coincidence.
                _targeted_test_res = None
                _targeted_regression_should_block = False
                _targeted_baseline_delta_result = None
                if (
                    state.validation_baseline_targeted is not None
                    and state.validation_baseline_targeted.status == "captured"
                ):
                    _frozen_targets = state.validation_baseline_targeted.invocation.target_test
                    _targeted_test_res = validator.run_tests(target_test=_frozen_targets)
                    _stop_on_environment_gate_result(state, _targeted_test_res, "targeted_test")
                    _post_targeted_outcome = build_validation_outcome(_targeted_test_res)
                    _targeted_baseline_delta_result, _targeted_stability = classify_with_baseline_stability(
                        baseline=state.validation_baseline_targeted, post=_post_targeted_outcome,
                        post_environment=None, cache=state.pytest_stability_cache,
                        replay=baseline_suite_run,
                        current_revision=lambda: compute_workspace_content_hash(workspace_path),
                        working_directory=os.path.realpath(workspace_path),
                    )
                    record_regression_decision("targeted", state.validation_baseline_targeted,
                                               _post_targeted_outcome, _targeted_baseline_delta_result,
                                               _targeted_stability)
                    _targeted_regression_should_block = _targeted_baseline_delta_result.blocking
                    state.record_event(RunEvent(
                        kind="validation_baseline.targeted_delta",
                        attempt=state.attempt_number,
                        source="workflow.run_generation_workflow",
                        authority=EventAuthority.ADVISORY,
                        message=(
                            f"Targeted PRE/POST delta ({_frozen_targets}): "
                            f"level1={_targeted_baseline_delta_result.level1.classification.value} "
                            f"blocking={_targeted_regression_should_block}"
                        ),
                        details={
                            "target_test": list(_frozen_targets) if _frozen_targets else None,
                            "level1_classification": _targeted_baseline_delta_result.level1.classification.value,
                            "level2": {k: v.value for k, v in _targeted_baseline_delta_result.level2.items()},
                            "aggregate_drop_detected": _targeted_baseline_delta_result.aggregate_drop_detected,
                            "blocking": _targeted_regression_should_block,
                            "blocking_reasons": list(_targeted_baseline_delta_result.blocking_reasons),
                            **pytest_decision_summary(_targeted_baseline_delta_result, _targeted_stability),
                        },
                    ))
                    if not _targeted_regression_should_block and not _targeted_test_res["success"]:
                        logger.info(
                            "Targeted baseline suite failed, but every failure is classified "
                            "PRE_EXISTING_FAILURE relative to the captured PRE-mutation baseline - "
                            "not attributed to this candidate, not blocking."
                        )

                _regression_should_block = _regression_should_block or _targeted_regression_should_block

                # Combined failure evidence: the full-regression output is
                # always included (unchanged from before this package
                # existed); the targeted suite's own output is appended,
                # clearly labeled, whenever it was actually run AND is
                # itself a genuine cause (failed outright, or its own delta
                # blocked) - never omitted when it's the reason blocking
                # fired, never included as noise when it isn't.
                _regression_failure_output = full_test_res.get("output", "")
                if _targeted_test_res is not None and (
                    not _targeted_test_res["success"] or _targeted_regression_should_block
                ):
                    _regression_failure_output = (
                        f"{_regression_failure_output}\n\n"
                        f"=== TARGETED BASELINE SUITE ({state.validation_baseline_targeted.invocation.target_test}) ===\n"
                        f"{_targeted_test_res.get('output', '')}"
                    )

                # VAL-001 G1-DEVINV2 (2026-09-20): a captured full-regression
                # baseline means classify_baseline_delta() already computed,
                # per test, whether each failure is PRE_EXISTING_FAILURE
                # (never candidate-caused) or NEW_FAILURE/CHANGED_FAILURE
                # (blocking, and attributable). Before this fix, the
                # Developer-facing failure text above ignored that structure
                # entirely and used the RAW full-suite output - in a real
                # brownfield repo with substantial pre-existing test debt
                # (live-confirmed: 140 pre-existing failures alongside 0-1
                # genuinely new ones), that floods the repair prompt with
                # noise the classification above already had the information
                # to exclude, and was the proximate cause of a real G1 run
                # (qwen3.8:27b, 2026-09-19/20) discarding an independently-
                # verified CORRECT Attempt-1 candidate after 6 further
                # attempts of cascading malformed repair responses across
                # both the primary and fallback model. This ONLY changes
                # what evidence is shown for an ALREADY-blocking regression -
                # `_regression_should_block` above (the actual accept/reject
                # decision) is completely untouched, preserving "full
                # regression remains the final acceptance gate" exactly.
                # Byte-identical to before this fix whenever a full-
                # regression baseline was never captured at all (the
                # overwhelming majority of runs, which never opted into
                # `brownfield_full_regression_baseline_policy: "required"`).
                _confirmed_regression_ids: List[str] = []
                # True only when NOTHING attributable was found anywhere -
                # neither the full baseline (after ambiguous-entry replay
                # resolution) nor the targeted baseline (a small, already-
                # trustworthy 76-test signal with no flooding concern, whose
                # own independent block is never second-guessed here). When
                # the targeted baseline itself blocks, this stays False and
                # the pre-existing raw-output-plus-targeted-section text
                # built above is used completely unchanged - this fix only
                # ever narrows what's shown for the FULL-suite signal, never
                # touches the targeted one.
                _full_regression_unattributed = False
                # P2: files the compiler's errors name, when a test-compilation
                # failure is attributed to the candidate (compile_regression.py).
                _regression_known_files: List[str] = list(state.all_files_written)
                if _regression_should_block and _baseline_delta_result is not None:
                    from kriya.workflow.regression_attribution import confirm_ambiguous_regressions
                    _resolved_level2, _replay_evidence = confirm_ambiguous_regressions(
                        _baseline_delta_result.level2,
                        pristine_workspace_path=workspace_path,
                        candidate_workspace_path=worktree_path,
                        autonomy_cfg=self.kernel.config.autonomy,
                    )
                    if _replay_evidence:
                        state.record_event(RunEvent(
                            kind="validation_baseline.ambiguous_regression_replay",
                            attempt=state.attempt_number,
                            source="workflow.run_generation_workflow",
                            authority=EventAuthority.ADVISORY,
                            message=(
                                f"Isolated-pristine replay resolved {len(_replay_evidence)} "
                                "ambiguous (NOT_COMPARABLE) full-regression test(s)."
                            ),
                            details={"replay_evidence": _replay_evidence},
                        ))
                    _confirmed_regression_ids = sorted(
                        test_id for test_id, cls in _resolved_level2.items()
                        if cls in (DeltaClassification.NEW_FAILURE, DeltaClassification.CHANGED_FAILURE)
                    )
                    if _confirmed_regression_ids:
                        _regression_failure_output = render_blocking_regression_evidence(
                            baseline=state.validation_baseline_full_regression,
                            confirmed_test_ids=_confirmed_regression_ids,
                            raw_output=full_test_res.get("output", ""),
                            failure_details=test_execution.failure_details(full_test_res),
                        )
                        if _targeted_test_res is not None and (
                            not _targeted_test_res["success"] or _targeted_regression_should_block
                        ):
                            _regression_failure_output = (
                                f"{_regression_failure_output}\n\n"
                                f"=== TARGETED BASELINE SUITE ({state.validation_baseline_targeted.invocation.target_test}) ===\n"
                                f"{_targeted_test_res.get('output', '')}"
                            )
                    elif not _targeted_regression_should_block:
                        # P2: no per-test evidence at all (a test-compilation
                        # failure runs no test) is not "every failure is
                        # pre-existing": under a green, comparable PRE the
                        # compiler's own diagnostics attribute it.
                        _compile_regression = attribute_compile_regression(
                            _baseline_delta_result, full_test_res.get("output", ""), worktree_path,
                        )
                        if _compile_regression is None:
                            _full_regression_unattributed = True
                        else:
                            _regression_known_files = sorted(
                                set(state.all_files_written) | set(_compile_regression.files))
                            state.record_event(RunEvent(
                                kind="validation_baseline.compile_regression_attributed",
                                attempt=state.attempt_number,
                                source="workflow.run_generation_workflow",
                                authority=EventAuthority.AUTHORITATIVE,
                                message=("Full-regression compilation failure attributed to the candidate "
                                         "from compiler diagnostics: " + ", ".join(_compile_regression.files)),
                                details=_compile_regression.as_dict(),
                            ))

                if _regression_should_block:
                    # PRV-11 (2026-08-30): before treating this as an ordinary
                    # regression failure for the CURRENTLY executing subtask,
                    # check whether the approved plan's own provides/requires
                    # graph already proves this exact evidence is covered by
                    # unfinished, approved work - see ObligationKind.FUTURE_
                    # OWNER_VERIFICATION's own docstring (obligations.py) and
                    # resolve_future_owner_verification_deferral's own
                    # docstring (attribution.py) for the full live incident
                    # and fail-closed resolution rules. None whenever plan/
                    # subtask/ledger context is absent (every pre-MA6 caller)
                    # or any link in the chain is ambiguous - falls through
                    # to the unchanged ordinary path below.
                    deferral = (
                        resolve_future_owner_verification_deferral(
                            structured_plan, current_subtask_id,
                            full_test_res.get("output", ""), completed_subtask_ids or frozenset(),
                        )
                        if structured_plan is not None and current_subtask_id and obligation_ledger is not None
                        else None
                    )
                    if deferral is None:
                        if obligation_ledger is not None and current_subtask_id:
                            # This subtask was itself the future owner some
                            # earlier subtask deferred to, and its own real
                            # regression check still fails - settle VIOLATED
                            # for observability only. Control flow is
                            # unaffected: the raise below already routes
                            # this through the subtask's own ordinary retry/
                            # attribution path, which IS "recover against
                            # the now-responsible owner."
                            _settle_future_owner_verification_obligations(
                                obligation_ledger, current_subtask_id, satisfied=False,
                            )
                        if _full_regression_unattributed:
                            # VAL-001 G1-DEVINV2 (2026-09-20): the full-
                            # regression gate still BLOCKS (safety unchanged
                            # - `_regression_should_block` above is never
                            # weakened by this branch), but nothing could be
                            # confirmed candidate-attributable even after
                            # isolated-pristine replay of every ambiguous
                            # entry - only a LEVEL1-only aggregate delta with
                            # zero attributable per-test evidence. Feeding
                            # this into the ordinary Developer repair retry
                            # loop (either the raw full-suite dump, or an
                            # empty/uninformative filtered context) is
                            # exactly what produced a cascading malformed-
                            # response failure across both the primary and
                            # fallback model in a real live G1 run - not
                            # developer-fixable, so this reuses the SAME
                            # state.environment_failure/STOP_ENVIRONMENT
                            # mechanism containment_setup_failed/internal_
                            # framework_error/time_budget_exhausted already
                            # use for "no amount of Developer regeneration
                            # can fix this" (kriya/workflow/retry_strategy.py),
                            # rather than a bare "regression_test" failure
                            # that would re-enter the ordinary repair loop.
                            # REGRESSION-UNATTRIBUTED-PROSE-001: the stop
                            # reason states what the delta established
                            # (incomplete evidence, an aggregate change, or
                            # unattributable per-test entries) from its own
                            # blocking reasons - never an aggregate change
                            # the fingerprints do not show.
                            _attribution, _unattributed_message = regression_unattributed_diagnosis(
                                _baseline_delta_result)
                            failure = Failure(
                                type="regression_unattributed",
                                message=_unattributed_message,
                                raw_output=full_test_res.get("output", ""),
                                source="orchestrator", attempt=state.attempt_number,
                                diagnostics={
                                    # the typed code itself is set by its
                                    # producer (retry_strategy, LR-R1-M1 D7)
                                    # from this message's prefix
                                    "reason_code": "REGRESSION_UNATTRIBUTED",
                                    "attribution": _attribution,
                                    "blocking_reasons": list(_baseline_delta_result.blocking_reasons),
                                    "pytest_evidence_status": _baseline_delta_result.pytest_evidence_status,
                                },
                            )
                        else:
                            failure = _build_quality_gate_failure(
                                "regression_test", f"REGRESSION TEST SUITE FAILURE:\n{_regression_failure_output}",
                                _regression_failure_output, worktree_path,
                                _regression_known_files, state.attempt_number,
                            )
                        state.record_gate_outcome(failure.to_gate_outcome())
                        raise QualityGateFailure(failure)
                    _record_future_owner_verification_deferred(
                        obligation_ledger, deferral,
                        observed_by_subtask_id=current_subtask_id,
                        raw_output=full_test_res.get("output", ""),
                    )
                    logger.info(
                        "FUTURE_OWNER_VERIFICATION: regression evidence in %s (owned by "
                        "verification subtask %s) requires capability %s from not-yet-executed "
                        "subtask %s - deferring rather than retrying %s; continuing plan "
                        "execution.",
                        deferral.evidence_path, deferral.verification_subtask_id,
                        deferral.required_capability, deferral.future_owner_id, current_subtask_id,
                    )
                    state.record_gate_outcome({
                        "attempt": state.attempt_number,
                        "type": "regression_test",
                        "success": True,
                        "output": full_test_res.get("output", ""),
                        "deferred_to_future_owner": deferral.future_owner_id,
                        **execution_evidence(full_test_res),
                    })
                    # Falls through to the same post-regression checks and
                    # success path below, exactly as a genuinely passing
                    # full_test_res would - this IS "allow the current
                    # subtask to complete."

                final_candidate_contents = {}
                for filepath in sorted(state.all_files_written):
                    with open(
                        os.path.join(worktree_path, filepath),
                        "r", encoding="utf-8", errors="replace",
                    ) as fh:
                        final_candidate_contents[filepath] = fh.read()
                ownership_violations = find_brownfield_test_redirections(
                    workspace_path,
                    state.all_original_contents,
                    final_candidate_contents,
                )
                if ownership_violations:
                    evidence = "; ".join(
                        f"owner={item['existing_owner']}, candidate={item['new_candidate']}, "
                        f"test={item['redirected_test']}"
                        for item in ownership_violations
                    )
                    message = (
                        "BROWNFIELD OWNERSHIP REJECTED: existing behavioral ownership "
                        "cannot be transferred to a newly created parallel implementation "
                        "by redirecting established tests. Modify the existing owner and "
                        "retain its API; update tests only to extend expectations or coverage."
                    )
                    # MA8 (spec §35-38, kriya/workflow/architectural_choice.py):
                    # a single occurrence is ordinary quality-gate failure -
                    # likely_files already redirects the next attempt at the
                    # grounded owner below. Only once the SAME candidate file
                    # recurs despite that redirect, AND the goal never
                    # explicitly asked for a new artifact by that name, is the
                    # underlying architectural choice (not just this attempt)
                    # confirmed wrong.
                    new_architectural_changes, architecture_diagnostics = classify_ownership_violations(
                        ownership_violations, goal, state.attempt_number, state.architectural_changes,
                    )
                    state.architectural_changes.extend(new_architectural_changes)
                    diagnostics = dict(architecture_diagnostics or {})
                    if architecture_diagnostics:
                        message = architecture_choice_invalidated_message(architecture_diagnostics)
                    failure = Failure(
                        type="brownfield_ownership_redirect",
                        message=message,
                        raw_output=evidence,
                        source="ownership_gate",
                        authority="deterministic",
                        file_locations=[
                            FileLocation(filepath=item["redirected_test"])
                            for item in ownership_violations
                        ],
                        likely_files=sorted({
                            item["existing_owner"] for item in ownership_violations
                        }),
                        attempt=state.attempt_number,
                    )
                    if diagnostics:
                        failure.diagnostics = {**(failure.diagnostics or {}), **diagnostics}
                    # PRD-022: the redirected tests are evidence and the
                    # parallel file is abandoned; the next attempt restores
                    # both deterministically (attempt.py) so the retry only
                    # has to change the grounded owner it is scoped to.
                    for key, values in (
                        ("redirected_tests", [item["redirected_test"] for item in ownership_violations]),
                        ("abandoned_candidates", [item["new_candidate"] for item in ownership_violations]),
                        ("owners", [item["existing_owner"] for item in ownership_violations]),
                    ):
                        merged = state.ownership_redirect_recovery.setdefault(key, [])
                        merged.extend(v for v in values if v not in merged)
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                # CORR-016 (P9/PRV-08, 2026-09-08, DIRECT-only) - same
                # authorization the per-attempt pre-write gate in
                # run_attempt() already applies (kriya/workflow/attempt.py),
                # computed identically here so a candidate that passed that
                # gate is never re-rejected at this terminal regression
                # check. [] for every plain Legacy run (structured_plan is
                # None there).
                terminal_direct_authorizations = _terminal_contract_authorizations(
                    grounding_goal, structured_plan, current_subtask_id, state,
                )
                api_violations = find_brownfield_public_api_changes(
                    workspace_path,
                    state.all_original_contents,
                    final_candidate_contents,
                    goal,
                    terminal_direct_authorizations,
                )
                if api_violations:
                    evidence = "; ".join(
                        f"owner={item['owner']}, removed={item['removed_signature']}, "
                        f"references={item['evidence_files']}"
                        for item in api_violations
                    )
                    failure = Failure(
                        type="brownfield_public_api_changed",
                        message=(
                            "BROWNFIELD PUBLIC API REJECTED: an established public signature "
                            "referenced by existing tests or call sites was removed or renamed. "
                            "Preserve the public API and repair private/internal implementation "
                            "unless the goal explicitly requests an API migration."
                        ),
                        raw_output=evidence,
                        source="ownership_gate",
                        authority="deterministic",
                        file_locations=[
                            FileLocation(filepath=item["owner"])
                            for item in api_violations
                        ],
                        likely_files=sorted({item["owner"] for item in api_violations}),
                        diagnostics={
                            "api_contract_recovery": {"violations": api_violations},
                        },
                        attempt=state.attempt_number,
                    )
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                # CORR-018-P1 (A3-bound slice, 2026-09-09) - same terminal
                # re-check discipline as the brownfield-API gate immediately
                # above (never re-rejects a candidate that already passed the
                # per-attempt gate in attempt.py, computed identically here),
                # applied to a SEPARATE, deterministic question - see
                # semantic_region_authority.py's own module docstring and
                # attempt.py's early gate for the full rationale. No-op unless
                # a caller actually populated authorized_semantic_regions.
                terminal_semantic_violations = find_unauthorized_semantic_changes(
                    state.all_original_contents, final_candidate_contents, authorized_semantic_regions or [],
                    strict_existing_java_files=self.kernel.config.autonomy.semantic_region_enforcement_required,
                )
                if terminal_semantic_violations:
                    evidence = "; ".join(
                        f"file={v.relpath}, member={v.member_key}, reason={v.reason_code}: {v.detail}"
                        for v in terminal_semantic_violations
                    )
                    failure = Failure(
                        type="semantic_region_unauthorized",
                        message=(
                            "SEMANTIC REGION REJECTED: the candidate changes source outside the "
                            "explicitly authorized region(s) for this run."
                        ),
                        raw_output=evidence,
                        source="semantic_authority_gate", authority="deterministic",
                        file_locations=[FileLocation(filepath=v.relpath) for v in terminal_semantic_violations],
                        likely_files=sorted({v.relpath for v in terminal_semantic_violations}),
                        diagnostics={
                            "semantic_region_authority": {
                                "violations": [
                                    {"relpath": v.relpath, "member_key": v.member_key,
                                     "reason_code": v.reason_code, "detail": v.detail}
                                    for v in terminal_semantic_violations
                                ],
                            },
                        },
                        attempt=state.attempt_number,
                    )
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                state.record_gate_outcome({
                    "attempt": state.attempt_number,
                    "type": "regression_test",
                    "success": True,
                    "output": full_test_res.get("output", ""),
                    **execution_evidence(full_test_res),
                })
                if state.no_change_proposal:
                    _settle_no_change_proposal(state, structured_plan, current_subtask_id, worktree_path,
                                               reopened_owner=attempt_ctx.reopened_owner)
                elif (state.identical_rewrites and not state.all_files_written
                      and not attempt_ctx.no_change_verifier_downstream):
                    _stop_unverified_no_change(state)
                state.terminal_regression_succeeded = True
                if obligation_ledger is not None and current_subtask_id:
                    # This subtask's own full regression genuinely passed
                    # (deferred or not) - settle SATISFIED any still-PENDING
                    # FUTURE_OWNER_VERIFICATION obligation this subtask
                    # itself owns (i.e. an earlier subtask deferred to it,
                    # and it just proved the deferred requirement for real).
                    # A no-op when none exist.
                    _settle_future_owner_verification_obligations(
                        obligation_ledger, current_subtask_id, satisfied=True,
                    )
                if state.api_contract_recovery:
                    state.api_contract_recovery.terminal_succeeded()
                    logger.info(
                        "API_CONTRACT_RECOVERY complete: deterministic contract checks, "
                        "candidate gates, and terminal regression all passed; transitions=%s",
                        state.api_contract_recovery.transition_history,
                    )
                    state.api_contract_recovery = None
                state.record_event(RunEvent(
                    kind="terminal_regression.passed",
                    attempt=state.attempt_number,
                    source="workflow",
                    authority=EventAuthority.AUTHORITATIVE,
                    details={"passed": True, "terminal": True},
                ))
                log_quality_gate_banner(
                    "PASSED", state.attempt_number,
                    scope=attempt_ctx.execution_scope,
                )

                # A semantically final-success checkpoint is legal only after
                # every required terminal gate has passed.
                candidate_checkpoint_fields[EFFECTIVE_LEDGER_KEY] = resolved_obligation_ledger.to_snapshot()
                _save_stage_checkpoint(
                    "developer_success",
                    effective_obligation_ledger=resolved_obligation_ledger,
                    **candidate_checkpoint_fields,
                    plan=plan,
                    design=design,
                    final_files=final_files_for_checkpoint,
                    original_files=state.all_original_contents,
                    gate_outcomes=state.gate_outcomes,
                    model_hops=state.model_hops,
                    retry_count=state.budgets.retry_count,
                    targeted_retry_count=state.budgets.targeted_retry_count,
                    candidate_gates_passed=True,
                    terminal_regression_passed=True,
                    overall_attempt_passed=False,
                )

                # Stage 6, terminal apply (kriya/workflow/stage_apply.py::apply_terminal_candidate): the verified candidate
                # as one revision-grounded batch through the shared commit seam, the attempt's success record, and the
                # sandbox released before any optional learning call.
                apply_terminal_candidate(
                    stage_ctx, self._late_stage_deps(), attempt_ctx=attempt_ctx,
                    final_candidate_contents=final_candidate_contents,
                )

                # Stage 7 (kriya/workflow/stage_apply.py::extract_lessons): advisory learning after authoritative success.
                await extract_lessons(
                    stage_ctx, self._late_stage_deps(), should_extract_hard_won_knowledge=should_extract_hard_won_knowledge,
                    self_corrected_outcome=self_corrected_outcome,
                )

                break

            except RecoveryPhaseAdvanced as transition:
                # A verified state-machine transition is progress, not a failed
                # quality-gate attempt. It still consumes the dedicated model
                # attempt budget, but must not enter generic failure routing or
                # append a historical FAILED outcome.
                state.budgets.api_contract_recovery_count += 1
                state.record_event(RunEvent(
                    kind="api_contract_recovery.phase_advanced",
                    attempt=state.attempt_number,
                    source="ownership_gate",
                    authority=EventAuthority.AUTHORITATIVE,
                    message=str(transition),
                    details={
                        "source_phase": transition.source,
                        "target_phase": transition.target,
                        "passed": True,
                    },
                ))
                logger.info(
                    "API_CONTRACT_RECOVERY TRANSITION PASSED - Attempt %d: %s",
                    state.attempt_number, transition,
                )
                continue
            except BestOfNFailureRecorded as recorded:
                # Already recorded by best-of-N (STATE-BEST-OF-N-HANDOFF-001):
                # act on its decision, never record the failure again.
                if recorded.stop_loop:
                    break
            except Exception as e:
                if await handle_attempt_failure(state, attempt_ctx, e):
                    break
            finally:
                attempt_evidence_scope.exit_attempt_iteration(
                    attempt_evidence_token, succeeded=state.overall_attempt_succeeded)

        # Intermediate trace checkpoint (2026-08-15, found while forensically
        # investigating a real live run): the ONLY trace_logger.log_run() call
        # for a normal (non-early-exit) run used to happen after the Reviewer
        # call below - a single LLM completion that can itself hang or get
        # killed (by an external timeout, Ctrl-C, etc.). When that happens,
        # state.gate_outcomes - the full per-attempt forensic history
        # (compile/test/run_verification results, which retry mode, which
        # files were implicated) that already exists in memory at this exact
        # point, quality gates having fully concluded either way - was
        # entirely lost; the trace row stayed at whatever status Ancient
        # history (or "in_progress" with zero data) it started at, giving a
        # future investigation nothing to work with beyond raw log-scraping.
        # `runs.run_id` is the table's PRIMARY KEY and log_run() always does
        # `INSERT OR REPLACE`, so writing here is a safe, idempotent
        # checkpoint - the real, final call after Reviewer completes still
        # runs as before and simply replaces this row with the complete
        # record (including the review text's downstream effects). If
        # Reviewer never finishes, this checkpoint is what survives, already
        # carrying the same gate_outcomes/model_hops a post-mortem needs.
        persist_run_trace(
            self._stage_deps(), state, run_trace, TraceDetail(retrieved_chunks, active_skills, plan_prompt),
            status="in_progress", failure_note="Failed to write intermediate trace checkpoint (pre-Reviewer)",
        )

        # 5. Reviewer (kriya/workflow/stage_review.py::final_review, full modularization E4)
        review = await final_review(stage_ctx, self._late_stage_deps())

        # Final status is derived from terminal state, not from append-only
        # failure history. An intermediate failed recovery attempt remains in
        # diagnostics without poisoning an authoritative recovered PASS.
        quality_passed = state.final_workflow_quality_passed()
        # A stable, short string identifying WHY this run failed, so a caller
        # (human or script) can always ask "why did this fail" the same way
        # rather than having to know which of several differently-shaped
        # fields to check. Scoped to the failure modes reachable from THIS
        # retry loop (None on success, "environment_failure", or
        # "quality_gates_exhausted" for an ordinary exhausted-retry-budget
        # code failure). The other two terminal states Kriya can end a run
        # in - knowledge-gap and human-rejected-approval - are separate
        # early-return dicts (above and below this point respectively) that
        # log their own failure_category directly at their own return site.
        failure_category: Optional[str] = None
        if not quality_passed:
            failure_category = terminal_failure_category(state)  # kriya/workflow/outcome.py
        # MA7.6: what KIND of thing kept failing (kriya/workflow/outcome.py).
        failure_report = failure_report_dicts(state)

        # Write persistent trace log (kriya/workflow/run_lifecycle.py)
        persist_run_trace(
            self._stage_deps(), state, run_trace, TraceDetail(retrieved_chunks, active_skills, plan_prompt),
            status="success" if quality_passed else "failure",
            terminal=TerminalTrace(failure_category, failure_report), recorded_note=True,
        )

        if quality_passed or (state.final_review_refusal is not None and state.quality_gates_succeeded):
            # Full success, or an applied candidate whose final review was
            # refused - nothing left a resumed run would need to redo.
            delete_checkpoint(workspace_path, run_id)
        else:
            # Deliberately just the total (state.attempt_number), not a
            # full-set/targeted breakdown: state.budgets.targeted_retry_count
            # (and .fallback_targeted_attempted) get reset to 0/False every
            # time retry_strategy.py sees a new failure family, while
            # attempt_number never does - live-confirmed the two can diverge
            # badly enough that "full-set X, targeted Y" adds up to far less
            # than the real total (a live run: attempt_number=8, but
            # retry_count=1 + targeted_retry_count=2 after two mid-run
            # resets). Same lesson as banners.py's dropped "N/M": don't
            # juxtapose numbers whose relationship isn't actually guaranteed.
            logger.info(
                f"Quality Gates never passed after {state.attempt_number} attempt(s) - "
                f"checkpoint '{run_id}' left on disk in case a later `--resume-id` run wants to skip "
                "Plan/Design and retry Developer."
            )

        if state.jdtls_client is not None:
            try:
                await state.jdtls_client.shutdown()
            except Exception as ex:
                logger.debug(f"jdtls shutdown failed (non-fatal): {ex}")

        return completed_result(
            self._stage_deps(), state, quality_passed=quality_passed, failure_category=failure_category,
            failure_report_dicts=failure_report, plan=plan, design=design, review=review,
            workspace_path=workspace_path, run_id=run_id, required_verification=required_verification,
            unresolved_skill_gap_names=unresolved_skill_gap_names, skill_staleness_warnings=skill_staleness_warnings,
            active_skill_manifest=active_skill_manifest, requirement_set=requirement_set,
            resolved_obligation_ledger=resolved_obligation_ledger,
        )

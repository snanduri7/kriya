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
    owning_run_work_unit,
)
from kriya.core.kernel import Kernel
from kriya.core.llm import InferenceDeadlineError, LLMClient
from kriya.core.model_routing import resume_routes_from
from kriya.core.token_budget import ContextBudgetUnsatisfiableError
from kriya.platform.filesystem_semantics import PathRelation, path_relation
from kriya.policy.approval import resolve_approval, resolve_approval_result
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.filesystem import WriteScopeMode
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.policy.telemetry import build_decision_record
from kriya.repository.bound_tree import BoundTreeError, admit_workspace_for_mutation
from kriya.static_analysis.service import (
    StaticAnalysisCandidate,
    StaticAnalysisService,
    banner,
    commit_guard,
    static_analysis_result_fields,
)
from kriya.tools import test_execution
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import requirement_closers as closers
from kriya.workflow.acceptance import output_confirms_nonzero_test_execution
from kriya.workflow.acceptance_approval import bound_approval
from kriya.workflow.acceptance_oracle import bound_acceptance
from kriya.workflow.architectural_choice import (
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
from kriya.workflow.development_loop import run_development
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
    find_missing_expected_files as find_missing_expected_files,
)
from kriya.workflow.file_resolution import (
    include_response_construction_owners,
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
    completed_result,
    deadline_stop_result,
    failure_report_dicts,
    terminal_failure_category,
    workspace_commit_failure_message,
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

# E4: the closers that need no facade collaborator, re-exported here (kriya/, tests and the remaining contract
# compilation import them from this module); the others are forwarders below.
from kriya.workflow.requirement_closers import SUITE_BASELINE_FROM_RUN as SUITE_BASELINE_FROM_RUN
from kriya.workflow.requirement_closers import SUITE_BASELINE_LAZY_CAPTURE as SUITE_BASELINE_LAZY_CAPTURE
from kriya.workflow.requirement_closers import _candidate_test_files as _candidate_test_files
from kriya.workflow.requirement_closers import _tracked_workspace_paths as _tracked_workspace_paths
from kriya.workflow.requirement_closers import _workspace_file_reader as _workspace_file_reader
from kriya.workflow.requirement_closers import _workspace_head as _workspace_head
from kriya.workflow.requirement_closers import (
    authoritative_tracked_workspace_paths as authoritative_tracked_workspace_paths,
)
from kriya.workflow.requirement_closers import (
    close_requirements_by_documentation as close_requirements_by_documentation,
)
from kriya.workflow.requirement_closers import documentation_referent_paths as documentation_referent_paths
from kriya.workflow.requirement_closers import frozen_file_paths as frozen_file_paths
from kriya.workflow.requirement_closers import named_preserved_test_files as named_preserved_test_files
from kriya.workflow.requirement_contract import bound_requirement_contract, requirement_set_for
from kriya.workflow.requirements import (
    VerificationAuthorityRequired,
    cited_requirement_ids,
    requirement_lineage,
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
    persist_early_exit_trace,
    persist_run_trace,
    record_run_exception,
    trace_run_events,
)
from kriya.workflow.run_trace import note_run_trace, record_run_exceptions
from kriya.workflow.semantic_region_authority import AuthorizedSemanticRegion
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
    _check_java_toolchain_mismatch as _check_java_toolchain_mismatch,  # E5: the terminal regression moved; tests import it from here
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
    _resolve_java_home_override as _resolve_java_home_override,  # E5: the terminal regression moved; tests import it from here
)
from kriya.workflow.toolchain import (
    _resolve_jdk_home_for_version as _resolve_jdk_home_for_version,
)
from kriya.workflow.toolchain import (
    _strip_jdk_incompatible_jvm_flags as _strip_jdk_incompatible_jvm_flags,
)
from kriya.workflow.triage import EngineeringRoute, EngineeringTriageService
from kriya.workflow.validation_baseline import (
    capture_brownfield_baselines,
)
from kriya.workflow.verification_binding import bind_candidate
from kriya.workflow.verification_contract import extract_contract_verdict as extract_contract_verdict
from kriya.workflow.verification_contract import pass_verdict_is_grounded as pass_verdict_is_grounded
from kriya.workflow.work_unit_runner import run_design_stages
from kriya.workflow.worktree import (
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
    except BoundTreeError:
        raise  # W3-3 C1: the membership authority is unavailable - never "no migration" (the entrypoints stop typed)
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


def admission_tracked_workspace_paths(workspace_path: str) -> List[str]:
    """The tracked set the CLI's verification-authority admission binds the project language from: ``authoritative_tracked_workspace_paths``, with the repository
    layer's ``BoundTreeError`` mapped to the admission's own typed refusal (``AuthorityBundleError``, ``AUTHORITY_EXECUTION_UNAVAILABLE`` - the code the run itself
    ends with). No authority is admitted against a membership Git could not give."""
    from kriya.workflow.authority_bundle import AUTHORITY_EXECUTION_UNAVAILABLE, AuthorityBundleError

    try:
        return authoritative_tracked_workspace_paths(workspace_path)
    except BoundTreeError as error:
        raise AuthorityBundleError(AUTHORITY_EXECUTION_UNAVAILABLE, f"the workspace's tracked files cannot be listed ({error})") from error


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

    tracked = authoritative_tracked_workspace_paths(workspace_path)
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
        except BoundTreeError:
            raise  # W3-3 B: the membership authority is unavailable - never recorded as a compiler defect and continued (the entrypoints stop typed)
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
    from kriya.repository.bound_tree import copy_bound_tree
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
            copy_bound_tree(workspace_path, export)   # R5: exactly the canonical bound tree (a BoundTreeError is the caller's typed AUTHORITY_EXECUTION_UNAVAILABLE stop)
            # The export keeps the repository's own Git metadata (run_java_acceptance reads the base revision from the tree it is given); that metadata is
            # not part of the bound set, so it is copied as itself, as it always was.
            git_path = os.path.join(workspace_path, ".git")
            if os.path.isdir(git_path) and not os.path.islink(git_path):
                shutil.copytree(git_path, os.path.join(export, ".git"), symlinks=True)
            elif os.path.lexists(git_path):
                shutil.copy2(git_path, os.path.join(export, ".git"), follow_symlinks=False)
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
            commit_evidence_dir(workspace_path), f"{cycle['transaction_id']}.json"), workspace_path=workspace_path)
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
    message = workspace_commit_failure_message({**payload, "workspace_state": outcome.workspace_state, "error": outcome.error})   # W3-6: the one owner of this text (outcome.py)
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


def _record_approval_decision(state: GenerationState, outcome: str, triggers: List[str], reason_code: Optional[str] = None) -> None:
    """PRD-033: one typed record of a human-approval decision (approved,
    rejected, or unavailable when no approval callback exists or no decision could be obtained).
    ``reason_code`` is the approval outcome's code when no decision could be obtained (UNAVAILABLE); an approval or a decline is fully named by ``outcome``, and the key is absent
    there (and when there was no callback to ask), so the recorded shape of those events is unchanged."""
    details: Dict[str, Any] = {"outcome": outcome, "triggers": list(triggers)}
    if reason_code is not None:
        details["reason_code"] = reason_code
    state.record_event(RunEvent(
        kind="approval.decision", attempt=state.attempt_number, source="workflow.approval_gate",
        authority=EventAuthority.AUTHORITATIVE, message=f"human approval: {outcome}",
        details=details,
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


def _load_baseline_policy() -> Tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any]]:
    """E5: the Developer loop's function-local import of kriya.workflow.baseline_policy (all-import cycle), done where
    the loop imported it: (baseline_environment_identity, decide_auto_baseline, effective_baseline_policy)."""
    from kriya.workflow.baseline_policy import (
        baseline_environment_identity,
        decide_auto_baseline,
        effective_baseline_policy,
    )
    return baseline_environment_identity, decide_auto_baseline, effective_baseline_policy


def _load_fallback_routing() -> Callable[..., Any]:
    """E5: the loop's function-local import of model_transition.fallback_routing_for_state (all-import cycle)."""
    from kriya.workflow.model_transition import fallback_routing_for_state
    return fallback_routing_for_state


def _load_best_of_n() -> Callable[..., Any]:
    """E5: the loop's function-local import of best_of_n.run_attempt_with_best_of_n (all-import cycle), done only when
    the first attempt runs Best-of-N, as before."""
    from kriya.workflow.best_of_n import run_attempt_with_best_of_n
    return run_attempt_with_best_of_n


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

    def _development_deps(self) -> StageDeps:
        """``_stage_deps()`` plus the Developer loop's collaborators (kriya/workflow/development_loop.py), all resolved
        NOW (when stage 4 begins): this engine's agents and helpers and this module's names, so a test patch of
        ``run_attempt``, ``handle_attempt_failure``, ``create_git_worktree``, ``capture_brownfield_baselines``,
        ``compute_workspace_content_hash``, a closer forwarder or ``logger`` here reaches the moved loop. The E4 stages
        the loop calls get ``_late_stage_deps()`` built at each call, as before. The loop's function-local imports from
        all-import-cycle modules are loaders, called where it imported them."""
        return dataclasses.replace(
            self._stage_deps(),
            developer=self.developer, approve_web_lookup=self._approve_web_lookup, _log_phase_banner=_log_phase_banner,
            parse_ownership_justifications=parse_ownership_justifications,
            _terminal_contract_authorizations=_terminal_contract_authorizations,
            engine=self, kernel=self.kernel, run_verifier=self.run_verifier, spec_compliance=self.spec_compliance,
            late_stage_deps=self._late_stage_deps, prior_full_suite_evidence=lambda: self._prior_full_suite_evidence,
            load_baseline_policy=_load_baseline_policy, load_fallback_routing=_load_fallback_routing,
            load_best_of_n=_load_best_of_n,
            run_attempt=run_attempt, handle_attempt_failure=handle_attempt_failure,
            create_git_worktree=create_git_worktree, capture_brownfield_baselines=capture_brownfield_baselines,
            compute_workspace_content_hash=compute_workspace_content_hash,
            assert_candidate_unchanged_since_source_review=assert_candidate_unchanged_since_source_review,
            classify_ownership_violations=classify_ownership_violations,
            resolve_future_owner_verification_deferral=resolve_future_owner_verification_deferral,
            _run_static_analysis_gate=_run_static_analysis_gate,
            close_requirements_by_mutation_scope=close_requirements_by_mutation_scope,
            close_requirements_with_acceptance_tests=close_requirements_with_acceptance_tests,
            close_requirements_with_authority_bundle_evidence=close_requirements_with_authority_bundle_evidence,
            close_requirements_with_derived_examples=close_requirements_with_derived_examples,
            close_requirements_by_api_preservation=close_requirements_by_api_preservation,
            close_requirements_by_test_addition=close_requirements_by_test_addition,
            close_requirements_by_documentation=close_requirements_by_documentation,
            close_requirements_with_named_tests=close_requirements_with_named_tests,
            close_requirements_by_test_immutability=close_requirements_by_test_immutability,
            close_requirements_by_file_immutability=close_requirements_by_file_immutability,
            close_requirements_by_suite_preservation=close_requirements_by_suite_preservation,
            _seed_grounded_loci=_seed_grounded_loci, build_contract_feedback=build_contract_feedback,
            _bind_direct_candidate=_bind_direct_candidate,
            _record_post_generation_ownership_findings=_record_post_generation_ownership_findings,
            _run_semantic_safety_gate=_run_semantic_safety_gate,
            _settle_future_owner_verification_obligations=_settle_future_owner_verification_obligations,
            _record_future_owner_verification_deferred=_record_future_owner_verification_deferred,
            _settle_no_change_proposal=_settle_no_change_proposal, _stop_unverified_no_change=_stop_unverified_no_change,
            AttemptContext=AttemptContext, RecoveryPhaseAdvanced=RecoveryPhaseAdvanced,
            BestOfNFailureRecorded=BestOfNFailureRecorded,
            _stop_on_environment_gate_result=_stop_on_environment_gate_result,
            _build_ecosystem_invariant_block=_build_ecosystem_invariant_block,
            RESOURCE_LIFECYCLE_HEADER=RESOURCE_LIFECYCLE_HEADER, VERIFICATION_CONTRACT_HEADER=VERIFICATION_CONTRACT_HEADER,
            record_findings=record_findings, CANDIDATE_HASH_KEY=CANDIDATE_HASH_KEY,
            EFFECTIVE_LEDGER_KEY=EFFECTIVE_LEDGER_KEY, candidate_snapshot_digest=candidate_snapshot_digest,
            captured_baseline=captured_baseline, attribute_suite_result=attribute_suite_result,
            post_environment_identity=post_environment_identity, SCOPE_FULL_REGRESSION=SCOPE_FULL_REGRESSION,
            bound_acceptance=bound_acceptance, bound_approval=bound_approval, test_execution=test_execution,
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
        returns an awaitable - resolved by the one approval owner,
        kriya.policy.approval.resolve_approval) and raises PolicyDeniedError if not
        approved (declined, or no decision obtainable) or if no callback was supplied at all. ALLOW and
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
            if not (await resolve_approval(approval_callback, diffs_to_show or [], result.explanation)).approved:
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
            return (await resolve_approval_result(web_lookup_query_callback(terms, base_url))).approved
        except Exception as ex:
            logger.warning(f"web_lookup_query_callback failed, skipping live lookup: {ex}")
            return False

    def workspace_refusal_result(self, assessment: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """PRD-008: the structured result when a fresh run is refused because
        the workspace's prior commit state is uncertain (see
        kriya/control/commit_state.py). No model call has happened."""
        return assessment.to_payload(run_id=arguments.get("trace_id_override"))

    def _membership_authority_stop(self, error: BoundTreeError, state: GenerationState, run_trace: Optional[Any], goal: str, workspace_path: str,
                                   trace_id: Optional[str], *, stage: str) -> Dict[str, Any]:
        """W3-3 (D-001): a ``BoundTreeError`` (the repository layer's internal exception) never leaves a production entrypoint as a raw traceback. The run ends as the
        existing typed ``AUTHORITY_EXECUTION_UNAVAILABLE`` outcome with the repository layer's detail; nothing was applied, nothing is retried or continued."""
        from kriya.workflow.authority_bundle import (
            AUTHORITY_EXECUTION_UNAVAILABLE,
            membership_authority_unavailable_result,
        )

        result = membership_authority_unavailable_result(error)
        logger.error(result["error"])
        state.record_event(RunEvent(
            kind="membership_authority.unavailable", attempt=state.attempt_number, source="workflow.run_generation_workflow",
            authority=EventAuthority.AUTHORITATIVE, message=result["error"][:600],
            details={"reason_code": AUTHORITY_EXECUTION_UNAVAILABLE, "membership_reason_code": error.reason_code, "stage": stage},
        ))
        if run_trace is not None:       # the contract-compilation stop precedes the run's trace (opened below it): its events stay on ``state``
            persist_early_exit_trace(self._stage_deps(), state, run_trace, status="failure", failure_category=result["failure_category"])
        return {**result, "goal": goal, "workspace_path": workspace_path, "run_id": trace_id}

    def _design_stage_deadline_stop(self, error: InferenceDeadlineError, state: GenerationState, run_trace: Optional[Any], goal: str, workspace_path: str,
                                    trace_id: Optional[str]) -> Dict[str, Any]:
        """W3-5 (STAGE-DEADLINE-RAW-ESCAPE-PLANNER-ARCHITECT-001): the run's generation deadline stopped a model call (or a retrieval embedding) in the design stages
        (KnowledgeGuard, analysis, Planner, Architect). It never leaves ``run_generation_workflow`` as a raw ``InferenceDeadlineError``: the run ends as the EXISTING
        failed result with the deadline's own typed reason code and the same ``generation_budget_exhausted`` category the Developer loop (recovery_coordinator) and the
        pre-generation budget check use. No candidate exists to blame, nothing is retried or written, and any stage checkpoint already saved is left as it is."""
        result = deadline_stop_result(error, goal=goal, workspace_path=workspace_path, run_id=trace_id)
        logger.error(result["error"])
        state.record_event(RunEvent(
            kind="design_stage.deadline", attempt=0, source="workflow.run_generation_workflow", authority=EventAuthority.AUTHORITATIVE, message=result["error"][:600],
            details={"reason_code": error.reason_code, **{key: value for key, value in error.details.items() if isinstance(value, (str, int, float, bool, type(None)))}},
        ))
        if run_trace is not None:
            persist_early_exit_trace(self._stage_deps(), state, run_trace, status="failure", failure_category=result["failure_category"])
        return result

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
        try:
            # W3-3 U4: the production admission of a workspace with no Git repository - before the contract (a unit that compiles none, `kriya fix`, is admitted here too).
            admit_workspace_for_mutation(workspace_path)
            verification_contract = (compile_run_contract(self, requirement_goal, goal, workspace_path, requirement_set)
                                     if requirement_set is not None else None)
        except BoundTreeError as error:
            return self._membership_authority_stop(error, state, None, goal, workspace_path, trace_id_override, stage="contract")
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
                persist_early_exit_trace(self._stage_deps(), state, run_trace, status="failure",
                                         failure_category=admission.failure_category)
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
                        persist_early_exit_trace(self._stage_deps(), state, run_trace, status="failure",
                                                 failure_category=stop.failure_category)
                        return {**stop.result(), "goal": goal, "workspace_path": workspace_path, "run_id": trace_id}
                    if baseline_report.no_mutation_required:
                        decision = NoMutationRequired(verification_contract, baseline_report)
                        logger.info(str(decision))
                        state.record_event(RunEvent(
                            kind="verification_contract.no_mutation_required", attempt=0,
                            source="workflow.run_generation_workflow", authority=EventAuthority.AUTHORITATIVE,
                            message=str(decision), details=baseline_report.to_dict(),
                        ))
                        persist_early_exit_trace(self._stage_deps(), state, run_trace, status="success")
                        return {**decision.result(), "goal": goal, "workspace_path": workspace_path, "run_id": trace_id}

        protected_relpath = _resolve_protected_relpath(workspace_path, protected_source_file)

        # Stages 0-3 of this work unit (full modularization W4, E3): KnowledgeGuard, analysis + skills + Graph RAG +
        # the learned reference, the Plan, the Architect incl. Stage 2A/2B, then the active-skill snapshot - driven by
        # kriya/workflow/work_unit_runner.py over one StageContext, with this engine's collaborators in StageDeps
        # (_work_unit_deps, resolved through this module at call time). A stage's early result (knowledge_gap, a
        # Planner rejection, architect_file_list_rejected, a Stage 2A dependency approval that is declined or unavailable: human_rejected / approval_required)
        # is this method's result, unchanged.
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
            # read only by the Developer loop (E5) and the E4 stages it calls
            run_trace=run_trace, grounding_goal=grounding_goal, work_unit=work_unit,
            verification_contract=verification_contract, resume_plan=resume_plan,
            resume_restored_ledger=resume_restored_ledger, protected_relpath=protected_relpath,
            planned_source_files=planned_source_files, recovery_contract_block=recovery_contract_block,
            grounded_locations=grounded_locations, established_files=established_files,
            authorized_semantic_regions=authorized_semantic_regions, required_verification=required_verification,
            runtime_verification_required=runtime_verification_required, strict_spec_compliance=strict_spec_compliance,
            execution_scope=execution_scope, reopened_owner=reopened_owner, migration_resolution=migration_resolution,
            obligation_ledger=obligation_ledger, completed_subtask_ids=completed_subtask_ids,
            deterministic_failure_diagnostics=deterministic_failure_diagnostics,
        )
        try:
            stage_stop = await run_design_stages(stage_ctx, self._work_unit_deps(), open_checkpoints=_open_stage_checkpoints)
        except BoundTreeError as error:
            return self._membership_authority_stop(error, state, run_trace, goal, workspace_path, trace_id, stage="design")
        except InferenceDeadlineError as error:
            return self._design_stage_deadline_stop(error, state, run_trace, goal, workspace_path, trace_id)
        if stage_stop is not None:
            return stage_stop
        # 4. Developer & Quality Gates (kriya/workflow/development_loop.py, full modularization E5): the prelude (prompt
        # blocks, the brownfield baselines, the sandbox, the obligation ledger, the AttemptContext) and the retry loop
        # with the candidate's path to the terminal apply. Its collaborators arrive in StageDeps (_development_deps,
        # resolved through this module when stage 4 begins); a prelude stop or the approval gate's abort is this
        # method's result, unchanged.
        try:
            development_stop = await run_development(stage_ctx, self._development_deps())
        except BoundTreeError as error:
            return self._membership_authority_stop(error, state, run_trace, goal, workspace_path, trace_id, stage="development")
        if development_stop is not None:
            return development_stop
        # What the trace checkpoint, the final review and the outcome read from the design stages and the loop.
        retrieved_chunks, active_skills, plan_prompt = stage_ctx.retrieved_chunks, stage_ctx.active_skills, stage_ctx.plan_prompt
        plan, design, skill_staleness_warnings = stage_ctx.plan, stage_ctx.design, stage_ctx.skill_staleness_warnings
        unresolved_skill_gap_names, active_skill_manifest = stage_ctx.unresolved_skill_gap_names, stage_ctx.active_skill_manifest
        resolved_obligation_ledger = stage_ctx.resolved_obligation_ledger

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

        # P-B: the skill verifications this run's passing Runtime Verification earned are persisted only now, after the
        # candidate's terminal apply, the final trace and the checkpoint removal - persisting commits into the git work tree
        # holding the skill, which must not move the repository under a gate still verifying the candidate. A run owned
        # by an authoritative controller (skill_engine_override: the shared engine of the enforce run's subtasks) only
        # hands them to that engine; the controller persists them after ITS terminal commit. A run that did not pass
        # persists nothing. A persistence failure is surfaced and never rewrites the verification result.
        skill_engine = stage_ctx.se
        if quality_passed and skill_engine is not None:
            skill_engine.hold_verifications(state.pending_skill_verifications.values())
            if skill_engine_override is None:
                state.post_commit_persistence_errors.extend(skill_engine.apply_held_verifications())
        state.pending_skill_verifications.clear()

        return completed_result(
            self._stage_deps(), state, quality_passed=quality_passed, failure_category=failure_category,
            failure_report_dicts=failure_report, plan=plan, design=design, review=review,
            workspace_path=workspace_path, run_id=run_id, required_verification=required_verification,
            unresolved_skill_gap_names=unresolved_skill_gap_names, skill_staleness_warnings=skill_staleness_warnings,
            active_skill_manifest=active_skill_manifest, requirement_set=requirement_set,
            resolved_obligation_ledger=resolved_obligation_ledger,
        )

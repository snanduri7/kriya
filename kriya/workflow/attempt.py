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
import hashlib
import logging
import os
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.agents.agent import DeveloperAgent
from kriya.agents.contracts import (
    AUTHORITATIVE_GOAL_SECTION_HEADER,
    PLANNED_IMPLEMENTATION_SECTION_HEADER,
)
from kriya.core.kernel import Kernel
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import AuthorizedFileWriter, WriteScopeMode
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.workflow.edit_safety import (
    StagedFileWrite,
    apply_anchored_edits,
    content_revision,
    find_cross_file_type_conflict,
    find_structural_corruption,
    read_file_revision,
)
from kriya.workflow.dependency_invalidation import (
    dependent_closure,
    invalidate_validated_revisions,
)
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.failure_grounding import _build_quality_gate_failure, _build_test_quality_gate_failure, _capture_failed_content, build_cross_package_mismatch_message, find_cross_package_symbol_mismatch, find_locator_files_outside_known_scope, resolve_repository_locator_files
from kriya.workflow.file_resolution import IncompleteGenerationError, _resolve_run_command, build_grounded_java_launch_command, correct_exec_main_class_property, discover_response_construction_owners, downgrade_ungrounded_goal_explicit_commands, ensure_maven_covers_nonconventional_java_files, extract_jvm_module_flags, extract_planner_code_blocks, extract_target_test, find_brownfield_public_api_changes, find_explanatory_prose_contamination, find_missing_expected_files, find_protected_api_reference_changes, find_runnable_test_files, find_unrequested_architectural_surfaces, find_unrestored_public_api_contracts, ground_java_entrypoint_in_no_build_file_projects, is_runnable_test_file, normalize_written_filepath, prefer_existing_artifact_owners, strip_package_declaration_matching_source_root
from kriya.workflow.context_budget import (
    _reserve_graph_context_budget,
    _reserve_sibling_content_budget,
    build_code_context,
)
from kriya.workflow.retry_prompts import _build_coordinated_retry_prompt, _build_full_set_retry_prompt, _build_missing_files_retry_prompt, _build_targeted_retry_prompt
from kriya.workflow.retry_package import RetryPackage, build_retry_package
from kriya.workflow.retry_policy import API_CONTRACT_RECOVERY_MAX_ATTEMPTS, RetryAction, decide_retry_action
from kriya.workflow.skill_extraction import _skill_verification_context
from kriya.workflow.state import (
    APIContractRecovery,
    APIContractRecoveryPhase,
    GenerationState,
    RecoveryPhaseAdvanced,
)
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.operations import (
    CodeOperation,
    all_results_are_no_change,
    operation_for_attempt,
    operation_for_file,
    validate_operation_result,
)
from kriya.workflow.static_checks import find_established_stack_drift, find_goal_stack_mismatch, run_static_checks
from kriya.workflow.attribution import extract_self_diagnosed_files, find_edits_ignoring_own_diagnosis, find_edits_ignoring_reported_line, find_misdirected_edit_target, find_whole_response_no_op, resolve_fallback_model
from kriya.workflow.banners import log_gate_banner
from kriya.workflow.acceptance import (
    goal_explicitly_requires_tests,
    output_confirms_nonzero_test_execution,
    runtime_application_step_started,
    runtime_verification_infrastructure_reason,
    subtask_owns_test_obligation,
)
from kriya.workflow.toolchain import _check_java_toolchain_mismatch, _pin_exec_plugin_executable_to_resolved_jdk, _resolve_java_home_override, _strip_jdk_incompatible_jvm_flags
from kriya.workflow.verification_contract import extract_contract_verdict, pass_verdict_is_grounded
from kriya.workflow.verification_authority import deterministic_sequence_kind, deterministic_verification_kind
from kriya.workflow.migration import MigrationResolution, MigrationResolutionStatus, MigrationValidationScope, find_migration_incomplete
from kriya.workflow.obligations import ObligationAuthority, ObligationKind, ObligationLedger, ObligationRecord, ObligationStatus
from kriya.workflow.repair_contract import RepairContractStatus, build_repair_contract, derive_process_boundary_participants
from kriya.workflow.worktree import clean_untracked_files_since, snapshot_untracked_files

logger = logging.getLogger(__name__)


def _target_exists(ctx: "AttemptContext", filepath: str) -> bool:
    return os.path.exists(os.path.join(ctx.worktree_path, filepath)) or os.path.exists(
        os.path.join(ctx.workspace_path, filepath)
    )


def _brownfield_owner_contract_block(
    ctx: "AttemptContext", target_files: Optional[List[str]], *, max_chars: int = 24000,
) -> str:
    """Expose exact existing-owner identity before first generation."""
    sections = []
    remaining = max_chars
    for filepath in target_files or []:
        source_path = os.path.join(ctx.workspace_path, filepath)
        if not os.path.isfile(source_path):
            continue
        try:
            with open(source_path, "r", encoding="utf-8", errors="replace") as handle:
                source = handle.read()
        except OSError:
            continue
        if remaining <= 0:
            break
        excerpt = source[:remaining]
        remaining -= len(excerpt)
        sections.append(f"=== EXISTING OWNER: {filepath} ===\n{excerpt}")
    if not sections:
        return ""
    return (
        "\n\n=== AUTHORITATIVE BROWNFIELD OWNER CONTRACT ===\n"
        "Every file below already exists and is an in-place repair target. Preserve its "
        "package/module identity, existing public type names, constructors, and public "
        "method signatures. Do not paste a planned replacement class into the resolved "
        "owner path. Existing tests and callers are contract evidence. Repair behavior "
        "behind the existing API, preferring private/internal changes.\n\n"
        + "\n\n".join(sections)
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


def _request_fallback_for_rejected_authoritative_target(
    state: GenerationState, ctx: "AttemptContext", preserved_files: List[str],
) -> None:
    if (
        preserved_files
        and state.last_attempt_mode == "targeted"
        and ctx.chain
        and not state.budgets.fallback_targeted_attempted
    ):
        state.budgets.fallback_targeted_requested = True


def _reject_explanatory_prose(
    state: GenerationState, filepath: str, content: str,
) -> None:
    contamination = find_explanatory_prose_contamination(filepath, content)
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
    state.gate_outcomes.append(failure.to_gate_outcome())
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
    context_window: int,
) -> Optional[RetryPackage]:
    if state.last_failure is None:
        return None
    # Reserve at most a bounded fraction of the model window for retry source
    # evidence.  Four characters/token is only an estimate; 1.5 chars per
    # advertised token deliberately leaves ample room for goal, plan, design,
    # skills, instructions, and output on local models with smaller windows.
    max_chars = max(6000, min(48000, int(context_window * 1.5)))
    return build_retry_package(
        failure=state.last_failure,
        worktree_path=ctx.worktree_path,
        # Unioned with ctx.established_files (see that field's own docstring) -
        # a retry package's content candidates should include files earlier
        # milestones wrote too, not just this attempt's own writes.
        all_files=sorted(set(state.all_files_written) | set(ctx.established_files)),
        target_files=target_files,
        source_context=state.last_error_source_context,
        max_chars=max_chars,
        # A file's entry here is only trustworthy while unchanged since the
        # post-compile snapshot that produced it - true here, since nothing
        # between that snapshot and a later gate's failure writes to the
        # worktree. Files outside that snapshot (e.g. genuinely still-missing
        # ones) simply fall back to a fresh hash inside project_implementation_source.
        known_revisions=state.validated_file_revisions,
        advisory_context=(
            state.error_context[
                state.error_context.index("=== Reference material found"):
            ]
            if "\n\n=== Reference material found" in state.error_context
            else ""
        ),
    )


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
    elapsed = time.monotonic() - state.generation_started_monotonic
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


async def _run_developer_generation(
    state: GenerationState, ctx: "AttemptContext", **kwargs,
) -> List[Dict[str, str]]:
    targets = kwargs.get("known_target_files")
    file_count = len(targets or ctx.expected_files_upfront or state.all_files_written or [None])
    active_model = kwargs.get("model_override") or ctx.kernel.config.llm.model
    _ensure_generation_time_budget(
        state, ctx, file_count=file_count, active_model=active_model,
    )
    started = time.monotonic()
    succeeded = False
    try:
        result = await ctx.developer.run_generation(**kwargs)
        succeeded = True
        return result
    finally:
        duration = time.monotonic() - started
        state.generation_timings.append({
            "duration_seconds": duration,
            "file_count": file_count,
            "succeeded": succeeded,
            "model": active_model,
        })
        state.record_event(RunEvent(
            kind="generation.completed" if succeeded else "generation.failed",
            attempt=state.attempt_number,
            source="developer",
            authority=EventAuthority.ADVISORY,
            message=(
                f"Developer generation {'completed' if succeeded else 'failed'} "
                f"for {file_count} file(s) in {duration:.2f}s."
            ),
            details={
                "duration_seconds": duration,
                "file_count": file_count,
                "model": active_model,
            },
        ))


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
    # path -> direct manifest dependencies, in generation order. Default keeps
    # isolated tests and old checkpoints backward compatible.
    generation_dependencies: Dict[str, List[str]] = field(default_factory=dict)
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
    runtime_verification_required: bool = False
    strict_spec_compliance: bool = False
    # Human-readable identity for nested structured executions. Attempt
    # numbers are local to each bounded run and otherwise collide in logs.
    execution_scope: str = ""
    # Original/global goal used only for deterministic architectural owner
    # discovery when a bounded subtask's local wording omits that context.
    grounding_goal: str = ""
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
    """Best-effort materialization of one Developer response's real resulting
    content, for the coordinated candidate-view (see
    _run_coordinated_repair_generation's "Rule 2A" note below) - NOT the
    authoritative write path (that's the existing staged_writes loop further
    down run_attempt(), which re-applies these same edits independently and
    remains the only place that can raise on a genuine anchor mismatch).
    Returns None (candidate view falls back to that file's prior/baseline
    content for the next participant) whenever there is nothing to
    materialize - a NO CHANGE NEEDED response (no content, no edits) or an
    edit whose SEARCH block doesn't match; a real anchor-mismatch failure
    still surfaces correctly, just later, through the authoritative staging
    loop's own error handling, not duplicated here."""
    content = file_obj.get("content")
    if content:
        return content
    edits = file_obj.get("edits") or []
    if not edits:
        return None
    current_file_path = os.path.join(ctx.worktree_path, filepath)
    if not os.path.exists(current_file_path):
        current_file_path = os.path.join(ctx.workspace_path, filepath)
    orig_text = ""
    if os.path.exists(current_file_path):
        with open(current_file_path, "r", encoding="utf-8", errors="replace") as fh:
            orig_text = fh.read()
    try:
        return apply_anchored_edits(orig_text, edits, "")
    except ValueError:
        return None


async def _run_coordinated_repair_generation(
    state: "GenerationState", ctx: "AttemptContext", contract: Any,
    base_code_context: str, stream_callback: Optional[Callable[[str], None]],
    attempt_operation: Any,
) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    """MA9 (2026-08-29): the coordinated counterpart to a single
    _run_developer_generation(known_target_files=state.last_implicated_files)
    call - one sequential Developer call per contract.generation_order entry,
    every call sharing the SAME RepairContract framing (via
    _build_coordinated_retry_prompt) instead of _build_targeted_retry_prompt's
    single-file "focus your fix there" wording. See repair_contract.py's own
    module docstring for why this exists (PRV-06 Bucket A).

    Deliberately NOT a single known_target_files=[A, B] call: DeveloperAgent.
    _fill_missing_content's own within-batch sibling section only shows an
    earlier participant's content when that participant returned FULL file
    content (entry["content"]), never when it returned an anchored edit
    (entry["edits"], content left None) - so a batch call would silently fail
    "Rule 2A" (below) exactly whenever the model preferred a small patch over
    a full rewrite for the first participant, which this codebase's own
    REPAIR mode prompt explicitly asks it to prefer. Sequential calls, each
    orchestrated here, close that gap by materializing every participant's
    real resulting content (via _materialize_candidate_content, applying
    anchored edits the same way the authoritative staging loop does) into an
    explicit `candidate_view` dict BEFORE building the next participant's
    prompt.

    Rule 2A (2026-08-29 design review - "probably the single most load-bearing
    implementation rule in MA9"): candidate_view accumulates in memory only;
    nothing here writes to ctx.worktree_path or ctx.workspace_path. The
    authoritative worktree stays exactly as it was before this attempt began
    until the existing staged_writes/AuthorizedFileWriter pipeline further
    down run_attempt() atomically accepts (or, on any gate failure, discards)
    the WHOLE returned list - every participant, across every group, lands
    together or none does, the same all-or-nothing behavior that pipeline
    already gives any multi-file `files` list, coordinated or not.

    Group-aware (2026-08-29 v2 design review, §11/§19): walks
    contract.repair_groups in order (never a flat generation_order directly -
    that field is only the flattening of every group's own order, kept on
    the contract for convenience/backward-compat), setting
    contract.active_group_id per group purely for observability
    (developer_repair_call/repair_group_started events below) - it plays no
    role in candidate-view visibility, which already spans every group
    generated so far regardless of which one is "active." No per-group
    lightweight validation is run here (§19 explicitly calls that optional -
    "acceptable," not required); the correctness invariant this module
    guarantees is the harder one anyway: no PARTIAL commit ever happens,
    since nothing here writes to the worktree at all until the full,
    all-groups candidate passes every existing gate further down
    run_attempt().

    Returns (results, candidate_view) - PRV-06 completion (2026-08-29, "MA8.1
    <-> MA9 composition and AttemptContext correctness"): the caller's own
    shared post-generation pipeline (anchored-edit application further down
    run_attempt()) needs a `shown_context` for its own grounding check
    (edit_safety.py::apply_anchored_edits's third argument) that reflects
    this SAME coordinated candidate state - not the authoritative baseline
    workspace, which Rule 2A above guarantees is still stale at this point.
    Previously this function returned only `results`, silently discarding
    the very candidate_view its own docstring calls "probably the single
    most load-bearing implementation rule in MA9" the moment control
    returned to the caller - the caller then had nothing but an unassigned
    local variable (see run_attempt's own `active_code_context`), a real,
    live-reproduced UnboundLocalError."""
    candidate_view: Dict[str, str] = {}
    results: List[Dict[str, str]] = []
    for group in contract.repair_groups:
        contract.active_group_id = group.id
        state.record_event(RunEvent(
            kind="repair_group_started", attempt=state.attempt_number, source="attempt.run_attempt",
            authority=EventAuthority.ADVISORY,
            message=f"RepairContract '{contract.id}' entering group '{group.id}'.",
            details={
                "repair_contract_id": contract.id, "group_id": group.id,
                "artifacts": list(group.artifacts), "depends_on_group_ids": list(group.depends_on_group_ids),
            },
        ))
        for filepath in group.generation_order:
            task_desc, active_code_context = _build_coordinated_retry_prompt(
                ctx.goal, ctx.plan, state.error_context, contract, filepath,
                state.all_files_written, ctx.worktree_path, base_code_context,
                candidate_view=candidate_view,
                ecosystem_invariant_block=ctx.ecosystem_invariant_block,
                resource_lifecycle_block=ctx.resource_lifecycle_block,
                verification_contract_block=ctx.verification_contract_block,
                # Only the currently-active group's own members are exempt
                # from this budget (see _build_coordinated_retry_prompt's own
                # docstring) - harmless/inert for today's 2-participant case
                # (both nearly always land in the same or immediately
                # adjacent group, well under budget), load-bearing for a
                # future 6-10 participant repair.
                participant_content_budget=_reserve_sibling_content_budget(ctx.kernel.config.llm.context_window),
            )
            state.record_event(RunEvent(
                kind="developer_repair_call", attempt=state.attempt_number, source="attempt.run_attempt",
                authority=EventAuthority.ADVISORY,
                message=f"Coordinated Developer call for '{filepath}' (group '{group.id}').",
                details={
                    "repair_contract_id": contract.id, "active_group": group.id,
                    "immediate_target": filepath, "participant_count": len(contract.participating_artifacts),
                    "candidate_revision": len(candidate_view),
                },
            ))
            file_results = await _run_developer_generation(
                state, ctx,
                task_description=task_desc,
                design_context=ctx.design,
                existing_code_context=active_code_context,
                stream_callback=stream_callback,
                model_override=None, base_url_override=None, api_key_override=None, extra_body_override=None,
                known_target_files=[filepath],
                prior_error_context=state.error_context or None,
                # Every participant, always - never narrowed to just this call's
                # own filepath. See RepairContract's own docstring: an active
                # coordinated contract must never let single-file attribution
                # collapse it back to narrow targeting; implicated_files=[filepath]
                # here would also silently disable the NO CHANGE NEEDED option
                # for every participant the CURRENT failure doesn't specifically
                # name (_fill_missing_content only offers it when
                # apply_fix_analysis is True, which requires filepath to be IN
                # implicated_files).
                implicated_files=list(contract.participating_artifacts),
                error_source_context=state.last_error_source_context or None,
                retry_temperature=ctx.kernel.config.llm.retry_temperature,
                extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE,
                files_with_current_content=state.all_files_written,
                sibling_content_budget=_reserve_sibling_content_budget(ctx.kernel.config.llm.context_window),
                operation_by_file=_operation_map(ctx, [filepath], attempt_operation, state),
                default_operation=attempt_operation,
            )
            for entry in file_results:
                if entry.get("filepath") == filepath:
                    materialized = _materialize_candidate_content(ctx, filepath, entry)
                    if materialized is not None:
                        candidate_view[filepath] = materialized
                        state.record_event(RunEvent(
                            kind="candidate_edit_staged", attempt=state.attempt_number,
                            source="attempt.run_attempt", authority=EventAuthority.ADVISORY,
                            message=f"Candidate staged for '{filepath}' (group '{group.id}').",
                            details={"repair_contract_id": contract.id, "artifact": filepath},
                        ))
                results.append(entry)
    return results, candidate_view


def _extract_grounded_contract_verdict(
    output: str, worktree_path: str, files_written: List[str]
) -> Optional[Dict[str, Any]]:
    """Wraps extract_contract_verdict() with the independent grounding check
    from verification_contract.py::pass_verdict_is_grounded() - see that
    function's own docstring for the full reasoning (independent brutal
    review finding #4, 2026-08-15: a PASS marker is self-reported by the
    same model that wrote the implementation, with nothing else checking it
    really branches on anything). A single shared helper, not three copies
    of the same logic - used identically at all three of run_attempt()'s
    run_res-outcome branches (clean run / timed out / plain nonzero exit) so
    a PASS verdict's trust is checked consistently everywhere, not just
    wherever someone happened to add it first."""
    verdict = extract_contract_verdict(output)
    if verdict is None or not verdict["passed"]:
        return verdict
    files_content = []
    for filepath in files_written:
        full_path = os.path.join(worktree_path, filepath)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                files_content.append(f.read())
        except Exception:
            continue
    if not pass_verdict_is_grounded(files_content):
        logger.info(
            "Runtime verification: a deterministic '[VERIFICATION] PASS' marker was found, but "
            "none of the written files contain a '[VERIFICATION] FAIL' string anywhere - the "
            "check doesn't look like it actually branches on anything. Not trusting it; falling "
            "back to LLM grading instead."
        )
        return None
    return verdict


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


_RUNTIME_VERIFICATION_SYNTHETIC_INPUT = "kriya-verification-input"

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
    state.gate_outcomes.append(failure.to_gate_outcome())
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
    state.gate_outcomes.append(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _raise_runtime_verification_infrastructure_failure(
    state: GenerationState,
    run_result: Dict[str, Any],
    commands: List[List[str]],
) -> None:
    """Stop verifier-owned launch failures before grading/source repair."""
    reason = runtime_verification_infrastructure_reason(run_result)
    if reason is None:
        return
    state.cached_run_verification_judgment = None
    message = (
        "VERIFICATION_INFRASTRUCTURE_FAILURE: runtime behavior was not observed because "
        f"the verifier infrastructure failed: {reason}.\n\nCaptured output:\n"
        f"{run_result.get('output', '')}"
    )
    failure = Failure(
        type="verification_infrastructure_failure", message=message,
        raw_output=run_result.get("output", ""), attempt=state.attempt_number,
    )
    outcome = failure.to_gate_outcome()
    outcome.update({"commands": commands, "steps": run_result.get("steps", [])})
    state.gate_outcomes.append(outcome)
    raise QualityGateFailure(failure)


def _apply_runtime_verification_contract(
    commands: List[List[str]], input_channel: str,
) -> Tuple[List[List[str]], Optional[str], Optional[str]]:
    """Runtime Verification Contract (PRV-06, 2026-08-29). RunVerifierAgent.
    judge() now states input_channel ("argv"/"stdin"/"none") as an explicit
    fact about what the goal requires, independent of whatever literal
    command it happened to return - this is the deterministic enforcement
    layer that makes that fact actually reach the real invocation, closing
    a live incident where the judge's own success_criteria correctly named
    "the command line argument" but its own run_commands never supplied
    one, and the app's own correct "no input provided" response was then
    misdiagnosed as an application defect for 9 wasted attempts.

    Returns (corrected_commands, stdin_payload, incomplete_reason).
    incomplete_reason is None whenever the contract was successfully
    satisfied (including "none", and "argv"/"stdin" already supplied) -
    non-None means the caller must raise RUNTIME_VERIFICATION_CONTRACT_
    INCOMPLETE and never launch the process (see this module's own call
    sites). The synthetic value is a single, stable, Kriya-owned constant
    (never asked of an LLM, matching the same discipline as every other
    typed value this codebase generates deterministically) - it is not
    goal-specific text, so it carries no proprietary/external data and is
    safe to record verbatim in verification evidence.

    Deliberately narrow, evidence-bounded shape detection (only the two
    proven-live invocation forms, plus one generic fallback that covers
    every other interpreter+target language without any per-ecosystem
    branching): a Maven exec:java invocation (recognized by an "exec:java"
    token or an already-present "-Dexec.mainClass=" token - exec:exec is
    deliberately NOT matched here, since its own argument-passing mechanism
    is pom.xml-configured, not a "-Dexec.args=" command-line property) gets
    "-Dexec.args=<value>" appended; any other "mvn"/"gradle"/"./gradlew"
    invocation is left unrecognized rather than guessed at - a bare
    trailing token there is parsed as an additional lifecycle phase/goal,
    not a program argument, and would fail the build outright. Any other
    exactly-two-token command (["java","Main"], ["python","app.py"],
    ["node","app.js"], ...) gets the value appended as a third, positional
    token. A Java launch with JVM/classpath options is parsed through its
    entrypoint token so a missing application argument is appended after the
    class rather than confused with JVM options. Other commands already
    carrying more than two tokens, or an exec:java command that already sets
    "-Dexec.args=", are assumed to already supply their own value and are left
    untouched. Only applied to the LAST command in the sequence - the one
    that actually exercises the application's behavior in every real
    sequence this codebase produces (see run_app_sequence's own docstring
    for the multi-invocation case, e.g. "add an item, then list items",
    where every earlier step is its own already-fully-specified
    invocation, not something this function is meant to touch)."""
    if not commands or input_channel not in ("argv", "stdin"):
        return commands, None, None
    if input_channel == "stdin":
        return commands, _RUNTIME_VERIFICATION_SYNTHETIC_INPUT, None
    *prefix, last = commands
    # Build/test commands report their own authoritative process verdict and
    # are not application invocations. An application argv contract is simply
    # inapplicable to them; never turn the input value into a lifecycle goal,
    # test selector, or package-manager argument.
    if deterministic_verification_kind(last) is not None:
        return commands, None, None
    if last and os.path.basename(last[0]).lower() == "java":
        options_with_values = {
            "-cp", "-classpath", "--class-path", "-p", "--module-path",
            "--upgrade-module-path", "--add-modules", "--limit-modules",
            "--add-reads", "--add-exports", "--add-opens", "--patch-module",
        }
        target_index: Optional[int] = None
        skip_next = False
        for index, token in enumerate(last[1:], start=1):
            if skip_next:
                skip_next = False
                continue
            if token in options_with_values:
                skip_next = True
                continue
            if token.startswith("-"):
                continue
            target_index = index
            break
        if target_index is not None and target_index == len(last) - 1:
            return prefix + [last + [_RUNTIME_VERIFICATION_SYNTHETIC_INPUT]], None, None
        if target_index is not None:
            return commands, None, None
    is_maven_exec_java = "exec:java" in last or any(
        tok.startswith("-Dexec.mainClass=") for tok in last
    )
    if is_maven_exec_java:
        if any(tok.startswith("-Dexec.args=") for tok in last):
            return commands, None, None
        return prefix + [last + [f"-Dexec.args={_RUNTIME_VERIFICATION_SYNTHETIC_INPUT}"]], None, None
    # A bare trailing token is a valid program argument for a plain
    # interpreter invocation (java/python/node/...), but NOT for "mvn"/
    # "gradle" (a bare token there is parsed as an additional lifecycle
    # phase/goal to run, e.g. "mvn exec:exec kriya-verification-input"
    # would fail Maven outright with an unknown-phase error) - any other
    # mvn/gradle shape besides the exec:java one already handled above is
    # left unrecognized rather than guessed at.
    if last and last[0] in ("mvn", "gradle", "./gradlew", "gradlew"):
        return commands, None, (
            f"input_channel=argv but the final command ({last!r}) is a build-tool "
            "invocation this deterministic injector doesn't know how to pass an "
            "argument to safely (only mvn exec:java's -Dexec.args= is supported)."
        )
    if len(last) == 2:
        return prefix + [last + [_RUNTIME_VERIFICATION_SYNTHETIC_INPUT]], None, None
    if len(last) > 2:
        return commands, None, None
    return commands, None, (
        f"input_channel=argv but the final command's shape ({last!r}) isn't one this "
        "deterministic injector recognizes - refusing to guess where to place a "
        "command-line argument rather than silently running an under-specified invocation."
    )


def _run_verification_basis_hash(ctx: "AttemptContext", state: GenerationState) -> str:
    """Fingerprint the real invocation-affecting basis for cached judgment."""
    digest = hashlib.sha256()
    digest.update(ctx.goal.encode("utf-8", errors="replace"))
    digest.update(ctx.design.encode("utf-8", errors="replace"))
    for filepath in sorted(set(state.all_files_written) | set(ctx.established_files)):
        digest.update(filepath.encode("utf-8", errors="replace"))
        full_path = os.path.join(ctx.worktree_path, filepath)
        try:
            with open(full_path, "rb") as fh:
                # Repair-envelope parsing may add or remove only terminal
                # whitespace while preserving the executable file.  Do not
                # spend another judgment call for that serialization detail;
                # substantive source/config changes still alter this basis.
                content = fh.read().replace(b"\r\n", b"\n").rstrip()
                digest.update(content)
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()


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

    1. If this retry is responding to a static_rule_violation (a CHEAP,
       already-registered deterministic checker - kriya/workflow/
       static_checks.py), just re-run that SAME check against the proposed
       content. If the specific violation it originally flagged is gone,
       that's authoritative - no prose-matching needed at all. Confirmed
       live, 2026-08-17 (ignite_qpid_person, run b-10m): a correct fix
       wrapping a bare `[VERIFICATION] PASS` line in println(...) was
       rejected 3 times because the marker text was a substring of a
       larger search block, not signal (b)'s required whole-block match -
       re-running BareVerificationMarkerCheck against the candidate would
       have accepted it on the first try, with zero new signal needed.
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
    if fail_type in ("compile", "pom_semantic_validation", "targeted_test"):
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
        return "this retry responds to a compile/POM-validation/targeted-test failure - deferring to the real gate instead of prose-matching"
    if fail_type == "static_rule_violation":
        from kriya.workflow.static_checks import run_static_checks
        # Unioned with ctx.established_files (see that field's own docstring) -
        # a static rule can span an established file plus the one just edited.
        still_violates = run_static_checks(
            ctx.worktree_path, sorted(set(state.all_files_written) | set(ctx.established_files)),
            overrides={filepath: candidate_content},
        )
        if not still_violates:
            return "the static check that originally flagged this file no longer flags the proposed content"
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


def _required_runtime_verification_missing_message(judgment: Dict[str, Any]) -> str:
    """PRV-06 (2026-08-28): observability fix, not a behavioral one - the
    judge's own stated reasoning (RunVerifierAgent.judge()'s `reasoning`
    field) is never consulted by any should_run/run_commands decision
    anywhere in this codebase, only surfaced here so a
    REQUIRED_RUNTIME_VERIFICATION_MISSING failure's persisted gate_outcome/
    traces.db record carries the judge's own explanation instead of a bare
    boolean with no way to tell a genuine "no runtime behavior to check"
    call apart from a judgment mistake. Found live, PRV-06 (2026-08-28):
    a Legacy run hit this exact failure after 8 prior compile/test-gate
    failures (the judge is called at most once per subtask, only once
    compile+test finally pass), and there was no way to tell whether the
    single should_run=False verdict was a genuine call or a mistake -
    `reasoning` may still be empty (the model can omit it despite the
    prompt asking for it; never fabricated here), but when present this
    closes that exact gap."""
    reasoning = judgment.get("reasoning") or "(no reasoning field returned by the judge)"
    return (
        "REQUIRED_RUNTIME_VERIFICATION_MISSING: the declared verification contract requires "
        "observable runtime behavior, but no executable verification sequence was produced.\n"
        f"Judge's own reasoning: {reasoning}"
    )


def _directly_executable_verifiers(required_verification: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """type=tool verifiers naming a BUILTIN_QUALITY_GATE_VERIFIERS tool_name
    (compile/test/tests/regression/quality_gates) - the ones
    _run_verification_only_attempt can execute directly via
    PolymorphicValidator, the same deterministic gate every ordinary
    implementation-subtask attempt already uses. See
    _directly_executable_runtime_verifiers below for the sibling case
    (a runtime-execution verifier) - kept as a separate function rather
    than folded in here since the two are executed through genuinely
    different machinery (PolymorphicValidator vs RunVerifierAgent)."""
    from kriya.workflow.plan_schema import BUILTIN_QUALITY_GATE_VERIFIERS
    return [
        requirement for requirement in required_verification
        if requirement.get("type") == "tool"
        and requirement.get("tool_name") in BUILTIN_QUALITY_GATE_VERIFIERS
    ]


def _directly_executable_runtime_verifiers(required_verification: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """PRV-06 (2026-08-28, real live-validation finding): a verification-
    only subtask (write_scope_mode=DENY_ALL, planned_files=[]) declaring
    ONLY an application_runtime verifier used to fall through to the
    ordinary Developer/Quality-Gates loop anyway (this exact case is what
    _directly_executable_verifiers' own docstring flagged as "a larger,
    separate lift" not yet built) - the Developer, asked to generate files
    for a subtask that legitimately has none, reliably invented a
    duplicate entrypoint every attempt. DENY_ALL correctly rejected each
    one before it ever reached disk (zero corruption, confirmed live), but
    the subtask still burned its entire retry budget on candidates that
    could never be written, because nothing short-circuited BEFORE the
    Developer call for this specific verifier shape.

    Deliberately narrow, matching _directly_executable_verifiers' own
    discipline: only matches an entry whose `verifier_kind` is EXPLICITLY
    "application_runtime" AND `requires_runtime_execution` is True - not
    just "files == []" alone (a malformed/under-specified plan could
    accidentally have zero planned_files for a genuinely mutating subtask;
    that must still enter ordinary validation/repair, never be silently
    reinterpreted as verification-only). `type` is intentionally NOT
    checked here (a validated plan's application_runtime verifier is
    normally type=judgment/tool_name=None, but the CALLER already only
    reaches this function under write_scope_mode=DENY_ALL, which is itself
    gated on execution_role=verification/planned_files=[] upstream - the
    verifier_kind+requires_runtime_execution pair is what actually
    identifies "this is the runtime-execution check," not the type tag)."""
    return [
        requirement for requirement in required_verification
        if requirement.get("verifier_kind") == "application_runtime"
        and requirement.get("requires_runtime_execution") is True
    ]


async def _run_verification_only_attempt(state: GenerationState, ctx: AttemptContext) -> None:
    """First-class execution path for a verification-only subtask
    (ctx.write_scope_mode == WriteScopeMode.DENY_ALL): executes its own
    declared deterministic verifier(s) directly against the existing
    worktree content - no Developer invocation, no candidate write attempt,
    no retry loop of its own. Raises QualityGateFailure (the same typed
    shape an ordinary compile/test failure already raises) the moment any
    verifier fails, so the EXISTING failure-attribution/recovery machinery
    handles it exactly as it would any other subtask failure - this
    function does not invent a new recovery path.

    Found live, PRV-05 (2026-08-28): before this existed, a verification-
    only subtask (files=[], DENY_ALL) still entered the ordinary Developer
    mutation/retry pipeline. Every attempt's Developer response inevitably
    tried to write SOMETHING (it has no other protocol), DENY_ALL correctly
    rejected each one, and the loop burned 6 attempts (escalating to the
    fallback model) before a runtime-verification side effect happened to
    produce the exact evidence a direct verifier call would have produced
    on attempt 1. Confirmed live evidence, same run: real `mvn -e test`
    exit-0 evidence WAS produced, but under gate_outcome type="run_verification"
    (the runtime-verification path's own type) while the plan's declared
    requirement was type=tool/tool_name=test - workflow.py's
    _build_required_verification_evidence() only matches a tool/test
    requirement against type in {"test","targeted_test","regression_test"}
    outcomes, so the real evidence was never connected to the obligation.
    Running the DECLARED verifier directly here (via the same
    PolymorphicValidator.run_tests()/run_compile_check() every ordinary
    attempt already uses) records the gate_outcome under the SAME type the
    requirement itself expects, closing that mismatch as a side effect of
    fixing the real problem (verification-only subtasks running the wrong
    execution path) rather than patching the symptom (evidence-matching
    logic) directly."""
    from kriya.tools.validate import PolymorphicValidator

    state.attempt_number += 1
    state.candidate_gates_succeeded = False
    validator = PolymorphicValidator(
        ctx.worktree_path, original_workspace_path=ctx.workspace_path,
        autonomy_cfg=ctx.kernel.config.autonomy,
    )
    known_files = sorted(set(ctx.established_files))

    for requirement in _directly_executable_verifiers(ctx.required_verification):
        tool_name = requirement.get("tool_name")
        if tool_name == "compile":
            result = validator.run_compile_check(known_files)
            outcome_type = "compile"
        else:
            # test/tests/regression/quality_gates all ultimately mean "run
            # the test suite" for this direct-execution path - quality_gates
            # additionally implies compile must pass first.
            if tool_name == "quality_gates":
                compile_result = validator.run_compile_check(known_files)
                state.gate_outcomes.append({
                    "attempt": state.attempt_number, "type": "compile",
                    "success": compile_result["success"], "output": compile_result.get("output", ""),
                })
                if not compile_result["success"]:
                    failure = Failure(
                        type="compile",
                        message=f"COMPILATION FAILURE (verification-only subtask):\n{compile_result.get('output', '')}",
                        raw_output=compile_result.get("output", ""), attempt=state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
            result = validator.run_tests()
            outcome_type = "test"
        state.gate_outcomes.append({
            "attempt": state.attempt_number, "type": outcome_type,
            "success": result["success"], "output": result.get("output", ""),
        })
        if not result["success"]:
            failure = Failure(
                type=outcome_type,
                message=(
                    f"{outcome_type.upper()} FAILURE (verification-only subtask):\n"
                    f"{result.get('output', '')}"
                ),
                raw_output=result.get("output", ""), attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

    if _directly_executable_runtime_verifiers(ctx.required_verification):
        await _execute_runtime_verification_directly(state, ctx, validator)

    state.candidate_gates_succeeded = True
    state.record_event(RunEvent(
        kind="candidate_gates.passed",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.AUTHORITATIVE,
        details={"passed": True, "terminal": False, "verification_only": True},
    ))
    log_gate_banner(
        "CANDIDATE GATES", "PASSED", state.attempt_number,
        scope=ctx.execution_scope,
    )


async def _execute_runtime_verification_directly(
    state: GenerationState, ctx: "AttemptContext", validator: "PolymorphicValidator",
) -> None:
    """PRV-06 (2026-08-28): the application_runtime half of
    _run_verification_only_attempt's direct-execution path - judge, correct,
    (approve), execute, grade, raise-or-return, with NO Developer
    invocation anywhere in this function. Deliberately reuses every
    existing sub-helper by reference (RunVerifierAgent.judge()/grade(),
    _resolve_run_command, ground_java_entrypoint_in_no_build_file_projects,
    the JDK/JVM-flag preflight corrections, run_app_sequence,
    deterministic_sequence_kind, _extract_grounded_contract_verdict,
    _build_quality_gate_failure) - none of their own internal logic is
    reimplemented here, only the SEQUENCE in which a verification-only
    subtask needs to call them is new.

    Deliberately narrower than the mutating path's inline runtime-
    verification block (kriya/workflow/attempt.py's own "Quality Gates:
    Runtime Verification" section) in two respects, both intentional:
    - No self-correction micro-loop. That loop exists to patch
      infrastructure/classpath issues in code a Developer just wrote in
      THIS attempt - a verification-only subtask writes nothing, so
      self_correction_loop's own writable_files would already collapse to
      [] under DENY_ALL, making it a pure no-op burn of one extra LLM call.
      "Verification does not generate code. Failure recovery may generate
      code" (this fix's own design principle) - on failure, this function
      raises the same typed Failure the mutating path already raises, and
      the EXISTING outer retry/recovery machinery (which CAN re-enter a
      mutating context for a different subtask/attempt) takes over from
      there, unchanged.
    - No SpecCompliance goal-check. That check verifies concrete literal
      requirements (exact field/method/class names) in code just written -
      irrelevant for a subtask that writes no new code; whatever
      established files it's verifying already passed that check when an
      earlier, mutating subtask wrote them.

    Judgment caching (state.cached_run_verification_judgment) is preserved
    across this subtask's own retry attempts, exactly like the mutating
    path - a repeat attempt after a transient failure re-judges only when
    the workspace's invocation-affecting content actually changed."""
    autonomy_cfg_rv = ctx.kernel.config.autonomy
    if ctx.runtime_verification_required and not autonomy_cfg_rv.run_verification_enabled:
        message = (
            "REQUIRED_RUNTIME_VERIFICATION_DISABLED: the declared verification contract "
            "requires observable execution, but runtime verification is disabled."
        )
        failure = Failure(
            type="verification_infrastructure_failure", message=message,
            raw_output=message, attempt=state.attempt_number,
        )
        state.gate_outcomes.append(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    if ctx.runtime_verification_required and state.run_verification_declined:
        message = (
            "REQUIRED_RUNTIME_VERIFICATION_DECLINED: the declared runtime check was not "
            "authorized, so correctness remains unverified."
        )
        failure = Failure(
            type="verification_infrastructure_failure", message=message,
            raw_output=message, attempt=state.attempt_number,
        )
        state.gate_outcomes.append(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    if not autonomy_cfg_rv.run_verification_enabled or state.run_verification_declined:
        return

    if not state.toolchain_checked:
        state.toolchain_checked = True
        state.toolchain_warning = _check_java_toolchain_mismatch(validator.stack)
        if state.toolchain_warning:
            logger.warning(f"Toolchain preflight: {state.toolchain_warning}")
        if validator.stack == "java":
            state.java_home_override = _resolve_java_home_override(ctx.goal)
            if state.java_home_override:
                logger.warning(
                    "JVM toolchain enforcement: forcing Maven subprocess calls to "
                    f"run under JAVA_HOME={state.java_home_override} - the goal-stated Java "
                    "version doesn't match what 'mvn' resolves to by default here."
                )
    validator.java_home_override = state.java_home_override

    known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
    current_judgment_basis = _run_verification_basis_hash(ctx, state)
    if (
        state.cached_run_verification_judgment is not None
        and state.cached_run_verification_basis_hash != current_judgment_basis
    ):
        logger.info(
            "Invocation-affecting workspace content changed - invalidating cached "
            "runtime-verification judgment."
        )
        state.cached_run_verification_judgment = None
    if state.cached_run_verification_judgment is None:
        pom_content_for_judge = None
        try:
            with open(os.path.join(ctx.worktree_path, "pom.xml"), "r", encoding="utf-8") as f:
                pom_content_for_judge = f.read()
        except Exception as e:
            logger.debug(f"No pom.xml available for run-verification judgment: {e}")
        raw_judgment = await ctx.run_verifier.judge(
            goal=ctx.goal, design=ctx.design,
            files_written=known_files, build_file_content=pom_content_for_judge,
        )
        if raw_judgment.get("infrastructure_error") and ctx.runtime_verification_required:
            message = (
                "VERIFICATION INFRASTRUCTURE FAILURE: runtime behavior is required, but "
                f"the runtime-verification judge was unavailable: {raw_judgment['infrastructure_error']}"
            )
            failure = Failure(
                type="verification_infrastructure_failure", message=message,
                raw_output=message, attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        state.cached_run_verification_judgment = downgrade_ungrounded_goal_explicit_commands(
            raw_judgment, ctx.goal
        )
        state.cached_run_verification_basis_hash = current_judgment_basis
    else:
        logger.debug("Reusing cached run-verification judgment from an earlier attempt in this run.")
    judgment = state.cached_run_verification_judgment

    if ctx.runtime_verification_required and not judgment.get("should_run"):
        message = _required_runtime_verification_missing_message(judgment)
        failure = Failure(
            type="verification_infrastructure_failure", message=message,
            raw_output=message, attempt=state.attempt_number,
        )
        state.gate_outcomes.append(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    if not judgment.get("should_run"):
        return
    if deterministic_sequence_kind(judgment.get("run_commands") or []) == "build":
        message = (
            "BEHAVIORAL_GOAL_WITH_BUILD_ONLY_VERIFICATION: observable runtime behavior "
            "is required, but the inferred sequence contains only build commands."
        )
        failure = Failure(
            type="verification_infrastructure_failure", message=message,
            raw_output=message, attempt=state.attempt_number,
        )
        state.gate_outcomes.append(failure.to_gate_outcome())
        raise QualityGateFailure(failure)

    if judgment.get("run_commands"):
        pom_content_for_correction = None
        try:
            with open(os.path.join(ctx.worktree_path, "pom.xml"), "r", encoding="utf-8") as f:
                pom_content_for_correction = f.read()
        except Exception:
            pass
        java_files = [f for f in known_files if f.endswith(".java")]
        if java_files:
            corrected_commands = ground_java_entrypoint_in_no_build_file_projects(
                judgment["run_commands"], judgment["command_source"], known_files,
                _build_java_main_class_map(java_files, ctx),
                extract_jvm_module_flags(ctx.skills_prompt), pom_content_for_correction,
                prefer_grounded_runtime=True,
            )
            if corrected_commands is None:
                logger.info(
                    "Deterministic Java entrypoint resolution: no pom.xml/build.gradle found "
                    "and no known .java file has a main() method - overriding should_run to "
                    "False instead of executing a command that targets a nonexistent entrypoint class."
                )
                judgment = dict(judgment)
                judgment["should_run"] = False
                judgment["run_commands"] = None
            elif corrected_commands != judgment["run_commands"]:
                judgment = dict(judgment)
                judgment["run_commands"] = corrected_commands

    if not judgment.get("should_run"):
        return

    proceed_with_run = True
    if judgment["command_source"] == "inferred" and not state.run_verification_confirmed:
        if autonomy_cfg_rv.mode == "human-in-the-loop":
            commands_desc = "\n".join(
                f"    {i}. {' '.join(cmd)}" for i, cmd in enumerate(judgment["run_commands"], 1)
            )
            confirm_reason = (
                "Kriya judged that this goal describes runtime behavior compile/test checks "
                "can't verify, and wants to actually run the generated app (verification-only "
                "subtask):\n"
                f"  Command(s):\n{commands_desc}\n"
                f"  Looking for: {judgment['success_criteria']}\n"
                "Allow Kriya to execute these command(s) inside the sandboxed worktree?"
            )
            if ctx.approval_callback:
                approved = ctx.approval_callback([], confirm_reason)
                if asyncio.iscoroutine(approved):
                    approved = await approved
                proceed_with_run = bool(approved)
            else:
                logger.warning(
                    "Runtime verification warrants human approval but no approval_callback "
                    "is available. Proceeding under default policy."
                )
        if not proceed_with_run:
            state.run_verification_declined = True
    if not proceed_with_run:
        return

    state.run_verification_confirmed = True
    resolved_run_commands = [_resolve_run_command(cmd, ctx.worktree_path) for cmd in judgment["run_commands"]]
    input_channel = judgment.get("input_channel") or "none"
    resolved_run_commands, stdin_payload, contract_incomplete_reason = _apply_runtime_verification_contract(
        resolved_run_commands, input_channel,
    )
    logger.info(
        "RUNTIME_VERIFICATION_CONTRACT input_channel=%s argument_count=%d stdin_present=%s",
        input_channel, len(resolved_run_commands[-1]) if resolved_run_commands else 0,
        bool(stdin_payload),
    )
    if contract_incomplete_reason:
        message = f"RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE: {contract_incomplete_reason}"
        logger.warning(message)
        failure = Failure(
            type="verification_infrastructure_failure", message=message,
            raw_output=message, attempt=state.attempt_number,
        )
        state.gate_outcomes.append(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    jvm_flag_correction = _strip_jdk_incompatible_jvm_flags(ctx.worktree_path, state.java_home_override)
    if jvm_flag_correction:
        state.toolchain_warning = (
            f"{state.toolchain_warning} {jvm_flag_correction}" if state.toolchain_warning else jvm_flag_correction
        )
    exec_pin_correction = _pin_exec_plugin_executable_to_resolved_jdk(ctx.worktree_path, state.java_home_override)
    if exec_pin_correction:
        state.toolchain_warning = (
            f"{state.toolchain_warning} {exec_pin_correction}" if state.toolchain_warning else exec_pin_correction
        )
    logger.info(
        "Quality Gates: Running runtime verification (verification-only subtask): "
        + " && ".join(" ".join(cmd) for cmd in resolved_run_commands)
    )
    pre_run_untracked = snapshot_untracked_files(ctx.worktree_path)
    run_res = validator.run_app_sequence(
        resolved_run_commands, timeout=autonomy_cfg_rv.run_verification_timeout_seconds,
        stdin_payload=stdin_payload,
    )
    clean_untracked_files_since(ctx.worktree_path, pre_run_untracked)
    _raise_runtime_verification_infrastructure_failure(
        state, run_res, resolved_run_commands,
    )

    gate_type = "run_verification"
    verification_authority = "llm"
    if run_res["timed_out"]:
        contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, known_files)
        if contract_verdict is not None:
            verification_authority = "contract"
            grade = contract_verdict
        else:
            grade = await ctx.run_verifier.grade(
                goal=ctx.goal, success_criteria=judgment["success_criteria"],
                output=run_res["output"], returncode=run_res["returncode"],
                files_written=known_files, timed_out=True,
            )
        timeout_s = autonomy_cfg_rv.run_verification_timeout_seconds
        if grade["passed"]:
            gate_type = "run_verification_hung"
            grade["reasoning"] = (
                f"The goal's described output WAS produced correctly, but the process never "
                f"exited on its own and had to be killed after {timeout_s}s. This is still a "
                "real defect - almost always an unclosed resource keeping the process alive "
                f"after all application logic already finished. Grader's evidence: {grade['reasoning']}"
            )
        else:
            grade["reasoning"] = (
                f"Run timed out after {timeout_s}s, and the output captured before the forced "
                f"kill does not show the goal was achieved either: {grade['reasoning']}"
            )
        grade["passed"] = False
    elif not run_res["success"]:
        deterministic_kind = deterministic_sequence_kind(resolved_run_commands)
        if deterministic_kind is not None:
            verification_authority = "process_exit"
            grade = {
                "passed": False,
                "reasoning": (
                    f"One or more deterministic {deterministic_kind} verification commands "
                    "returned a non-zero process status."
                ),
                "likely_files": [],
            }
        else:
            contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, known_files)
            if contract_verdict is not None:
                verification_authority = "contract"
                grade = contract_verdict
            else:
                grade = await ctx.run_verifier.grade(
                    goal=ctx.goal, success_criteria=judgment["success_criteria"],
                    output=run_res["output"], returncode=run_res["returncode"],
                    files_written=known_files,
                )
        if grade.get("passed") and not runtime_application_step_started(run_res):
            grade["passed"] = False
            grade["reasoning"] = (
                "Observed output appeared semantically correct, but one or more required "
                "verification setup steps failed or the application was never shown to have "
                f"started. Semantic evidence: {grade.get('reasoning', '')}"
            )
    else:
        deterministic_kind = deterministic_sequence_kind(resolved_run_commands)
        if deterministic_kind is not None:
            verification_authority = "process_exit"
            grade = {
                "passed": True,
                "reasoning": (
                    f"All deterministic {deterministic_kind} verification commands completed "
                    "successfully (exit code 0)."
                ),
                "likely_files": [],
            }
        else:
            contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, known_files)
            if contract_verdict is not None:
                verification_authority = "contract"
                grade = contract_verdict
            else:
                grade = await ctx.run_verifier.grade(
                    goal=ctx.goal, success_criteria=judgment["success_criteria"],
                    output=run_res["output"], returncode=run_res["returncode"],
                    files_written=known_files,
                )

    if not grade["passed"]:
        message = (
            f"RUNTIME VERIFICATION FAILURE (verification-only subtask): {grade['reasoning']}"
            f"\n\nCaptured output:\n{run_res['output']}"
        )
        enriched_output = run_res["output"] + f"\n\n[Grader reasoning]: {grade['reasoning']}"
        failure = _build_quality_gate_failure(
            gate_type, message, enriched_output, ctx.worktree_path, known_files,
            state.attempt_number, extra_likely_files=grade.get("likely_files") or [],
        )
        failure_outcome = failure.to_gate_outcome()
        failure_outcome.update({
            "graded_by": verification_authority, "commands": resolved_run_commands,
            "steps": run_res.get("steps", []),
        })
        state.gate_outcomes.append(failure_outcome)
        raise QualityGateFailure(failure)

    state.gate_outcomes.append({
        "attempt": state.attempt_number, "type": "run_verification", "success": True,
        "output": run_res["output"] + f"\n\n[Grader reasoning]: {grade['reasoning']}",
        "graded_by": verification_authority, "commands": resolved_run_commands,
        "steps": run_res.get("steps", []),
        "deterministic_result": "PASS" if verification_authority == "process_exit" else None,
    })


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


_REQUIREMENT_IDENTIFIER_TOKEN_RE = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_]*)`"
    r"|'([A-Za-z_][A-Za-z0-9_]*)'"
    r"|\"([A-Za-z_][A-Za-z0-9_]*)\""
    r"|\b([a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+)\b"
)


def _extract_requirement_identifier_tokens(requirement: str) -> List[str]:
    """Pulls concrete, code-shaped identifier tokens out of a SpecCompliance
    missing_requirement string (e.g. "displayName field" -> ["displayName"])
    - backtick/quote-wrapped names and camelCase names only, deliberately
    NOT every capitalized/plain word (a bare "Customer" or "The" would be a
    false positive). Used only by _spec_requirements_naming_planner_only_
    identifiers below to decide whether THIS specific identifier is the
    Planner's own word choice - never to guess a requirement's meaning."""
    tokens: List[str] = []
    for match in _REQUIREMENT_IDENTIFIER_TOKEN_RE.finditer(requirement):
        token = next((g for g in match.groups() if g), None)
        if token and len(token) >= 3 and token not in tokens:
            tokens.append(token)
    return tokens


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


async def run_attempt(state: GenerationState, ctx: AttemptContext) -> None:
    """Runs one Developer + Quality Gates attempt. Mutates state in place
    (files_written, gate_outcomes, model_hops, run_verification_*, etc.).
    Raises QualityGateFailure or IncompleteGenerationError on any gate
    failure; returns normally when Quality Gates (including Runtime
    Verification) pass."""
    if ctx.write_scope_mode == WriteScopeMode.DENY_ALL and (
        _directly_executable_verifiers(ctx.required_verification)
        or _directly_executable_runtime_verifiers(ctx.required_verification)
    ):
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
    # Mode selection delegates to retry_policy.decide_retry_action() - the
    # same pure decision function the outer while loop (workflow.py) and the
    # post-failure budget bookkeeping (retry_strategy.py) already call to
    # decide whether to continue at all, so "targeted beats missing_files
    # beats fallback_targeted beats full_set" is encoded in exactly one
    # place instead of being independently hand-rolled here too (found by
    # code review as unintentional duplication - the two copies happened to
    # agree, but nothing enforced that). environment_failure/attempt_number
    # are deliberately left at their None defaults: this call is scoped to
    # MODE selection only - the STOP_ENVIRONMENT/STOP_EXHAUSTED stop
    # conditions those two params drive are already handled one level up, by
    # the while loop's own decide_for_state() check before run_attempt() is
    # ever invoked for this iteration, so they must not fire a second time
    # here with a since-incremented attempt_number.
    retry_decision = decide_retry_action(
        retry_count=state.budgets.retry_count,
        max_retries=ctx.max_retries,
        targeted_retry_count=state.budgets.targeted_retry_count,
        targeted_max_retries=ctx.targeted_max_retries,
        has_implicated_files=bool(state.last_implicated_files),
        has_missing_files=bool(state.last_missing_files),
        has_fallback_model=bool(ctx.chain),
        fallback_targeted_attempted=state.budgets.fallback_targeted_attempted,
        environment_failure=None,
        fallback_targeted_requested=state.budgets.fallback_targeted_requested,
        has_api_contract_recovery=bool(state.api_contract_recovery),
        api_contract_recovery_count=state.budgets.api_contract_recovery_count,
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
    )
    state.record_event(RunEvent(
        kind="attempt.started",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.ADVISORY,
        operation=attempt_operation.value,
        details={"mode": state.last_attempt_mode},
    ))
    # Needed unconditionally below (both the normal compile/test gate
    # path and the always-run full regression check use it) - imported
    # here rather than only inside the skippable gate block so a
    # resumed candidate-gates checkpoint iteration (which skips
    # that block entirely) still has it in scope.
    from kriya.tools.validate import PolymorphicValidator

    # A candidate-gates checkpoint means generation plus the inner checks
    # passed, but terminal regression did not. The legacy developer_success
    # name is accepted conservatively as the same pre-regression boundary;
    # neither form may bypass the terminal suite.
    resuming_candidate_stage = bool(
        ctx.resume_state
        and ctx.resume_state.get("stage") in {"candidate_gates_passed", "developer_success"}
        and state.attempt_number == 1
    )
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

    if resuming_candidate_stage:
        logger.info(
            f"Resuming checkpoint '{ctx.run_id}': using saved candidate output and skipping "
            "generation plus candidate gates; terminal regression remains required."
        )
        files = [
            {"filepath": fp, "content": content}
            for fp, content in ctx.resume_state.get("final_files", {}).items()
        ]
        state.gate_outcomes = ctx.resume_state.get("gate_outcomes", state.gate_outcomes)
        state.model_hops = ctx.resume_state.get("model_hops", state.model_hops)
        model_override = None
        base_url_override = None
        api_key_override = None
        extra_body_override = None
    elif use_targeted or use_api_contract_recovery:
        # Targeted retry: always the primary model, never escalated
        # (see the budget comment above) - so the context budget is
        # always the primary model's own window, not a fallback's.
        current_limit = _reserve_graph_context_budget(
            ctx.kernel.config.llm.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = None
        base_url_override = None
        api_key_override = None
        extra_body_override = None

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        base_code_context = ctx.skills_prompt
        if current_graph_context:
            base_code_context += current_graph_context
        if ctx.learned_rag_context:
            base_code_context += ctx.learned_rag_context

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
            retry_package = _retry_package_for_attempt(
                state, ctx,
                target_files=state.last_implicated_files,
                context_window=ctx.kernel.config.llm.context_window,
            )
            retry_error_context = (
                retry_package.authoritative_error if retry_package else state.error_context
            )
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
                required = ", ".join(
                    f"{item['owner']}::{item['removed_signature']}"
                    for item in contract["violations"]
                )
                protected = ", ".join(contract["protected_evidence_files"])
                baseline_owners = "\n\n".join(
                    f"=== EXACT BASELINE OWNER SOURCE: {owner} ===\n"
                    + state.all_original_contents.get(owner, "<baseline source unavailable>")[:12000]
                    for owner in sorted({item["owner"] for item in contract["violations"]})
                )
                if contract.phase in (
                    APIContractRecoveryPhase.REPAIR_BEHAVIOR,
                    APIContractRecoveryPhase.AWAIT_TERMINAL_SUCCESS,
                ):
                    task_desc = (
                        "=== API_CONTRACT_RECOVERY: REPAIR_BEHAVIOR ===\n"
                        f"The following restored public contracts are immutable: {required}.\n"
                        f"Protected baseline callers/tests are immutable evidence: {protected}.\n"
                        "Now repair the requested behavior behind the existing public contract. "
                        "Prefer private/internal helper changes. Do not rename, remove, replace, "
                        "or redirect the public API.\n\n"
                        f"Original goal:\n{ctx.goal}\n\n{baseline_owners}"
                    )
                else:
                    task_desc = (
                        "=== API_CONTRACT_RECOVERY: RESTORE_PUBLIC_CONTRACT ===\n"
                        "This is the only objective for this phase.\n"
                        f"Restore these exact public signatures in their existing owners: {required}.\n"
                        "Do not solve the behavioral issue yet. Do not rename or replace a method. "
                        "Do not modify protected callers/tests. The candidate is invalid unless "
                        "every exact signature exists after this edit.\n"
                        f"Protected contract evidence: {protected}.\n\n{baseline_owners}"
                    )
                active_code_context = base_code_context + "\n\n" + baseline_owners
                retry_error_context = task_desc
                logger.info(
                    "API_CONTRACT_RECOVERY %s %d/%d: signatures=%s protected_evidence=%s",
                    contract.phase.value, state.budgets.api_contract_recovery_count + 1,
                    API_CONTRACT_RECOVERY_MAX_ATTEMPTS, required, protected,
                )
            else:
                logger.info(f"Targeted retry {state.budgets.targeted_retry_count + 1}/{ctx.targeted_max_retries}: focusing on {', '.join(state.last_implicated_files)}.")

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
                    sibling_content_budget=_reserve_sibling_content_budget(ctx.kernel.config.llm.context_window),
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
        fallback = ctx.chain[0]
        current_limit = _reserve_graph_context_budget(
            fallback.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = fallback.model
        base_url_override = fallback.base_url
        api_key_override = fallback.api_key
        extra_body_override = fallback.extra_body
        logger.info(
            f"Trying ONE targeted fix on fallback model {model_override} before "
            "falling back to full-set regeneration."
        )

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        base_code_context = ctx.skills_prompt
        if current_graph_context:
            base_code_context += current_graph_context
        if ctx.learned_rag_context:
            base_code_context += ctx.learned_rag_context

        retry_package = _retry_package_for_attempt(
            state, ctx,
            target_files=state.last_implicated_files,
            context_window=fallback.context_window,
        )
        retry_error_context = (
            retry_package.authoritative_error if retry_package else state.error_context
        )
        task_desc, active_code_context = _build_targeted_retry_prompt(
            ctx.goal, ctx.plan, state.error_context, state.last_implicated_files,
            state.all_files_written, ctx.worktree_path, base_code_context,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
            retry_package=retry_package,
            recovery_contract_block=ctx.recovery_contract_block,
        )
        logger.info(f"Fallback-targeted retry: focusing on {', '.join(state.last_implicated_files)}.")

        state.model_hops.append(model_override)

        dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
        files = await _run_developer_generation(
            state, ctx,
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
            sibling_content_budget=_reserve_sibling_content_budget(fallback.context_window),
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
        current_limit = _reserve_graph_context_budget(
            ctx.kernel.config.llm.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = None
        base_url_override = None
        api_key_override = None
        extra_body_override = None

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        base_code_context = ctx.skills_prompt
        if current_graph_context:
            base_code_context += current_graph_context
        if ctx.learned_rag_context:
            base_code_context += ctx.learned_rag_context

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
            task_description=task_desc,
            design_context=ctx.design,
            existing_code_context=active_code_context,
            stream_callback=dev_stream,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            extra_body_override=extra_body_override,
            known_target_files=resolved_missing_files,
            sibling_content_budget=_reserve_sibling_content_budget(ctx.kernel.config.llm.context_window),
            operation_by_file=_operation_map(
                ctx, resolved_missing_files, attempt_operation, state,
            ),
            default_operation=attempt_operation,
        )
    else:
        # Re-run context budget allocator dynamically for escalated model context window size
        current_limit = _reserve_graph_context_budget(
            ctx.kernel.config.llm.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = None
        base_url_override = None
        api_key_override = None
        extra_body_override = None

        active_context_window = ctx.kernel.config.llm.context_window
        fallback = resolve_fallback_model(state.budgets.retry_count, ctx.chain)
        if fallback is not None:
            model_override = fallback.model
            base_url_override = fallback.base_url
            api_key_override = fallback.api_key
            extra_body_override = fallback.extra_body
            active_context_window = fallback.context_window
            current_limit = _reserve_graph_context_budget(
                fallback.context_window, ctx.skills_prompt, ctx.learned_rag_context
            )
            logger.info(f"Escalating compilation attempt to fallback model: {model_override} (Limit: {current_limit} tokens)")

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        active_code_context = ctx.skills_prompt
        if current_graph_context:
            active_code_context += current_graph_context
        if ctx.learned_rag_context:
            active_code_context += ctx.learned_rag_context

        retry_package = _retry_package_for_attempt(
            state, ctx,
            target_files=state.last_implicated_files,
            context_window=active_context_window,
        )
        retry_error_context = (
            retry_package.authoritative_error if retry_package else state.error_context
        )
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

        # Track model hops
        state.model_hops.append(model_override or ctx.kernel.config.llm.model)

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

        if state.budgets.retry_count == 0:
            process_boundary_constraint = _initial_test_process_boundary_constraint(
                _runtime_contract_requirements(ctx), known_target_files,
            )
            if process_boundary_constraint:
                task_desc += process_boundary_constraint

        if state.attempt_number == 1 and known_target_files:
            owner_contract = _brownfield_owner_contract_block(ctx, known_target_files)
            if owner_contract:
                task_desc += owner_contract
                active_code_context += owner_contract
                logger.info(
                    "Attempt 1 brownfield owner contract: preserving existing identity/API "
                    "for %s.",
                    ", ".join(
                        path for path in known_target_files
                        if os.path.isfile(os.path.join(ctx.workspace_path, path))
                    ),
                )

        # PlannerAgent's own prompt never asks for full code, but models
        # routinely over-deliver it anyway in fenced blocks inside the plan
        # text - Architect explicitly discards it, and Developer previously
        # always regenerated every file from scratch regardless, paying a
        # full completion per file for work already done. On attempt 1 only
        # (never a retry - a plan that already led to a failure isn't a
        # trustworthy source for a fresh attempt), if the Planner's own text
        # already has usable code for EVERY expected file, use it directly
        # instead of asking Developer to redo it - still subject to the
        # exact same compile/test/Runtime-Verification gates as any other
        # attempt, so a wrong or incomplete Planner draft costs at most one
        # gate cycle before falling through to a real Developer generation
        # on the next attempt, the same downside a bad first Developer
        # attempt would already have. Deliberately all-or-nothing: a partial
        # match (some but not all expected files present) is NOT reused, to
        # avoid a third, harder-to-verify code path that mixes Planner and
        # Developer output for the same attempt.
        reused_files = None
        has_brownfield_targets = any(
            os.path.isfile(os.path.join(ctx.workspace_path, path))
            for path in (known_target_files or [])
        )
        if (
            state.budgets.retry_count == 0 and ctx.expected_files_upfront
            and not has_brownfield_targets
        ):
            planner_blocks = extract_planner_code_blocks(ctx.plan, ctx.expected_files_upfront)
            if set(planner_blocks.keys()) == set(ctx.expected_files_upfront):
                reused_files = [{"filepath": fp, "content": content} for fp, content in planner_blocks.items()]
                logger.info(
                    f"Planner's own plan already contains complete code for all "
                    f"{len(reused_files)} expected file(s) - reusing it directly instead of "
                    "a fresh Developer generation call, subject to the same Quality Gates "
                    "as any other attempt."
                )

        # Logged symmetrically on BOTH branches (previously only the reused
        # branch logged anything) so a run's log alone - via the same grep-
        # based analysis this session has used all day - can already answer
        # "did attempt 1 use Planner-reused content or fresh Developer
        # generation" without needing new tooling. See
        # state.planner_reuse_used_attempt1's own docstring for why this is
        # worth tracking at all: an external review raised, and two of the
        # same day's live incidents supported, the hypothesis that reused
        # Planner content correlates with more first-attempt failures than
        # fresh Developer generation - this makes that measurable from
        # ordinary run logs instead of argued from a handful of anecdotes.
        if state.budgets.retry_count == 0:
            state.planner_reuse_used_attempt1 = reused_files is not None
            if reused_files is None:
                logger.info(
                    "Attempt 1: Planner's plan did not contain usable code for every expected "
                    "file (or none was expected upfront) - using a fresh Developer generation "
                    "call for all files."
                )

        if reused_files is not None:
            files = reused_files
        else:
            # Generate code files
            dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
            files = await _run_developer_generation(
                state, ctx,
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
                sibling_content_budget=_reserve_sibling_content_budget(active_context_window),
                operation_by_file=(
                    _operation_map(ctx, known_target_files, attempt_operation, state)
                    if known_target_files else None
                ),
                default_operation=attempt_operation,
            )

    # Recorded now, not derived by the caller afterward - see the fields'
    # own docstring in kriya/workflow/state.py. Every branch above sets all
    # four of these (to None for the primary model, or a fallback's values).
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
    resolution = dict(zip(
        paths_to_resolve,
        prefer_existing_artifact_owners(paths_to_resolve, ctx.goal, ctx.workspace_path),
    )) if paths_to_resolve else {}
    resolved_paths = [resolution.get(path, path) for path in candidate_paths]
    if resolved_paths != candidate_paths:
        for file_obj, resolved_path in zip(files, resolved_paths):
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
        early_api_violations = find_brownfield_public_api_changes(
            ctx.workspace_path, baseline_contents, candidate_contents, ctx.goal,
        )
        if early_api_violations:
            state.all_original_contents.update(baseline_contents)
            for evidence_path in sorted({
                path
                for item in early_api_violations
                for path in item.get("evidence_files", [])
            }):
                if evidence_path in state.all_original_contents:
                    continue
                try:
                    with open(
                        os.path.join(ctx.workspace_path, evidence_path),
                        "r", encoding="utf-8", errors="replace",
                    ) as handle:
                        state.all_original_contents[evidence_path] = handle.read()
                except OSError:
                    pass
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
                },
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
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
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

    # Enforce the selected response contract before attribution heuristics or
    # writes.  Classification is based on the target's real existence, so the
    # same full-content shape is CREATE for an absent file and REPAIR for an
    # existing one.  Repair fallbacks are explicit and observable; creation has
    # no permissive fallback that could turn a missing file into a silent no-op.
    for file_obj in files:
        filepath = file_obj["filepath"]
        file_exists = _target_exists(ctx, filepath)
        expected_operation = operation_for_file(
            attempt_operation, file_exists=file_exists,
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
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
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

    # "NO CHANGE NEEDED" is useful negative attribution evidence, not a
    # successful repair. In a targeted attempt, rerunning compile/tests/runtime
    # after every returned target was explicitly left untouched wastes an
    # expensive gate and routes the same failure back to the same file. Turn it
    # into a retry signal before any write or gate. If FIX ANALYSIS names a
    # different known file, redirect there. Otherwise preserve a preceding
    # deterministic locator; only genuinely ungrounded scope widens.
    all_targets_rejected = all_results_are_no_change(files)
    if state.last_attempt_mode in ("targeted", "fallback_targeted") and all_targets_rejected:
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
        state.gate_outcomes.append(failure.to_gate_outcome())
        raise QualityGateFailure(failure)

    # Read original file contents before overwriting (crucial for fallback mode diffs)
    for file_obj in files:
        filepath = file_obj.get("filepath", "")
        if not filepath:
            continue
        if filepath not in state.all_original_contents:
            actual_file = os.path.join(ctx.workspace_path, filepath)
            if os.path.exists(actual_file):
                with open(actual_file, "r", encoding="utf-8", errors="replace") as fh:
                    state.all_original_contents[filepath] = fh.read()
            else:
                state.all_original_contents[filepath] = ""

    # Write files to worktree sandbox
    state.files_written = []
    staged_writes: List[StagedFileWrite] = []
    # Built once per attempt, not once per file - see _build_workspace_type_index's
    # own docstring. Updated in place below as each new file is accepted, so two
    # files in the SAME batch that collide with each other are caught too.
    workspace_type_index = _build_workspace_type_index(state, ctx)
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
            and filepath not in ctx.allowed_write_relpaths
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

        # Single choke point every content path (batch JSON, iterative
        # per-file, a full-set retry) converges through before a byte
        # reaches disk - closes a real gap the per-path fixes upstream
        # (DeveloperAgent.sanitize_generated_content) don't: a batch JSON
        # response's content/edits fields are consumed directly from
        # parsed JSON and never passed through any sanitization at all
        # before this point. Idempotent/harmless to re-apply to content
        # that already went through it upstream.
        if edits:
            edits = [
                {
                    **e,
                    "search": DeveloperAgent.sanitize_generated_content(e.get("search", "")),
                    "replace": DeveloperAgent.sanitize_generated_content(e.get("replace", "")),
                }
                for e in edits
            ]
        elif content is not None:
            content = DeveloperAgent.sanitize_generated_content(content)

        full_path = os.path.join(ctx.worktree_path, filepath)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        if edits:
            current_file_path = os.path.join(ctx.worktree_path, filepath)
            if not os.path.exists(current_file_path):
                current_file_path = os.path.join(ctx.workspace_path, filepath)

            orig_text = ""
            if os.path.exists(current_file_path):
                with open(current_file_path, "r", encoding="utf-8", errors="replace") as fh:
                    orig_text = fh.read()

            try:
                new_content = apply_anchored_edits(orig_text, edits, active_code_context)
            except ValueError as anchor_ex:
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
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure) from anchor_ex

                state.budgets.anchor_failure_counts[filepath] = (
                    state.budgets.anchor_failure_counts.get(filepath, 0) + 1
                )
                failure = Failure(
                    type="anchored_edit",
                    message=f"ANCHORED EDIT FAILURE in {filepath}: {anchor_ex}",
                    raw_output=str(anchor_ex),
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure) from anchor_ex

            state.budgets.anchor_failure_counts[filepath] = 0

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
                        f"genuinely needs no change, write \"NO CHANGE NEEDED:\" instead of a "
                        f"SEARCH/REPLACE block; otherwise your REPLACE text must actually differ "
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
                state.gate_outcomes.append(failure.to_gate_outcome())
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
                state.gate_outcomes.append(failure.to_gate_outcome())
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
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
            else:
                # This file's edit looked consistent with its own analysis -
                # a clean slate, so an unrelated mismatch found later in the
                # run for this same file still gets its own bounded veto.
                state.budgets.diagnosis_mismatch_veto_counts.pop(filepath, None)

            _reject_explanatory_prose(state, filepath, new_content)
            staged_writes.append(StagedFileWrite(
                target_path=full_path,
                content=new_content,
                base_path=current_file_path,
                expected_base_revision=content_revision(orig_text),
            ))
        else:
            if content is None:
                continue

            current_file_path = os.path.join(ctx.worktree_path, filepath)
            if not os.path.exists(current_file_path):
                workspace_file_path = os.path.join(ctx.workspace_path, filepath)
                if os.path.exists(workspace_file_path):
                    current_file_path = workspace_file_path
            file_is_new = not os.path.exists(current_file_path)
            prior_content = ""
            if os.path.exists(current_file_path):
                with open(current_file_path, "r", encoding="utf-8", errors="replace") as fh:
                    prior_content = fh.read()

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
                state.gate_outcomes.append(failure.to_gate_outcome())
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
                    state.gate_outcomes.append(failure.to_gate_outcome())
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
                        state.gate_outcomes.append(failure.to_gate_outcome())
                        raise QualityGateFailure(failure)
                else:
                    state.budgets.diagnosis_mismatch_veto_counts.pop(filepath, None)

            _reject_explanatory_prose(state, filepath, content)
            staged_writes.append(StagedFileWrite(
                target_path=full_path,
                content=content,
                base_path=current_file_path,
                expected_base_revision=content_revision(prior_content),
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
                staged_writes.append(StagedFileWrite(
                    target_path=target_path,
                    content=baseline,
                    base_path=target_path,
                    expected_base_revision=content_revision(current),
                ))
                logger.info(
                    "RESTORE_PUBLIC_CONTRACT: deterministically restoring protected "
                    "baseline evidence %s", evidence_path,
                )

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
            state.gate_outcomes.append(failure.to_gate_outcome())
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
            state.gate_outcomes.append(failure.to_gate_outcome())
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
    pom_files = [
        filepath for filepath in state.files_written
        if os.path.basename(filepath) == "pom.xml"
    ]
    if pom_files:
        pom_validator = PolymorphicValidator(
            ctx.worktree_path, autonomy_cfg=ctx.kernel.config.autonomy,
        )
        pom_validate_res = pom_validator.run_pom_validate()
        if not pom_validate_res["success"]:
            filepath = pom_files[0]
            full_path = os.path.join(ctx.worktree_path, filepath)
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                failed_pom_content = fh.read()
            failure = Failure(
                type="pom_semantic_validation",
                message=f"POM VALIDATION FAILED for {filepath}: {pom_validate_res['output']}",
                raw_output=pom_validate_res["output"],
                file_locations=[FileLocation(filepath=filepath)],
                likely_files=[filepath],
                failed_content={filepath: failed_pom_content},
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

    if not resuming_candidate_stage:
        # Completeness Check: catch the Developer Agent silently under-delivering
        # (e.g. only writing pom.xml when the Architect's design called for 7 files).
        # A trivially-passing compile on a near-empty sandbox would otherwise report
        # PASSED and get applied to the workspace despite the goal not being met.
        # Sourced from architect_files (the structured file list, or its heuristic
        # fallback - see the Architect call above) rather than re-deriving via a
        # second independent regex pass over the design's prose.
        expected_files = {os.path.basename(f) for f in ctx.architect_files}
        missing_files = find_missing_expected_files(expected_files, state.all_files_written, goal=ctx.goal)
        if missing_files:
            raise IncompleteGenerationError(
                missing_files,
                "INCOMPLETE GENERATION: The design called for the following files, but "
                f"they were never written: {', '.join(missing_files)}. "
                f"You must generate ALL files listed in the Architect Design Guidelines, "
                f"not just a subset."
            )

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
            failure = _build_quality_gate_failure(
                type_="static_rule_violation",
                message=f"STATIC RULE VIOLATION: {static_violation}",
                raw_output=static_violation,
                worktree_path=ctx.worktree_path,
                known_files=static_check_known_files,
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # Quality Gates: Polymorphic compile & test checks inside sandbox
        logger.info("Quality Gates: Running polymorphic compiler and test checks...")
        validator = PolymorphicValidator(
            ctx.worktree_path, original_workspace_path=ctx.workspace_path,
            autonomy_cfg=ctx.kernel.config.autonomy,
            # PRV-05 (2026-08-28, run 5): the dependency-preservation check
            # below used to unconditionally reject ANY pom.xml dependency
            # removal, restoring Gson every time the subtask that owns
            # pom.xml tried to remove it - even though the top-level goal
            # explicitly authorizes replacing it. PRV-05 run 6 (2026-08-28):
            # re-resolving authorization from CURRENT (possibly already-
            # mutated) workspace state per attempt was itself timing-
            # sensitive - read from the run's ONE already-resolved
            # migration_resolution instead (see AttemptContext.migration_
            # resolution's own docstring), never re-derived here.
            authorized_dependency_removals=(
                set(ctx.migration_resolution.obligation.source_artifacts)
                if ctx.migration_resolution is not None
                and ctx.migration_resolution.status == MigrationResolutionStatus.RESOLVED
                and ctx.migration_resolution.obligation is not None
                else set()
            ),
        )

        if not state.toolchain_checked:
            state.toolchain_checked = True
            state.toolchain_warning = _check_java_toolchain_mismatch(validator.stack)
            if state.toolchain_warning:
                logger.warning(f"Toolchain preflight: {state.toolchain_warning}")
            if validator.stack == "java":
                state.java_home_override = _resolve_java_home_override(ctx.goal)
                if state.java_home_override:
                    logger.warning(
                        "JVM toolchain enforcement: forcing Maven subprocess calls to "
                        f"run under JAVA_HOME={state.java_home_override} - the goal-stated Java "
                        "version doesn't match what 'mvn' resolves to by default here."
                    )
        # Constructed fresh above (a new validator every attempt) - re-apply
        # the one-time-resolved override every time, not just when it was
        # just computed.
        validator.java_home_override = state.java_home_override

        # Unioned with ctx.established_files (see that field's own docstring) -
        # for a no-build-file Java project, this list is extended DIRECTLY into
        # the raw javac fallback's command line (kriya/tools/validate.py); an
        # established file not passed explicitly is only found via javac's
        # fragile implicit sourcepath auto-discovery, which silently breaks
        # whenever the established file's real location doesn't mirror its
        # package path relative to the workspace root - exactly the layout
        # mismatch this session's live incident already proved can happen.
        compile_known_files = sorted(set(state.all_files_written) | set(ctx.established_files))

        # Deterministic pom.xml sourceDirectory correction - found live,
        # 2026-08-22 (ignite_qpid_protocol milestone 3/4): see
        # ensure_maven_covers_nonconventional_java_files()'s own docstring
        # (kriya/workflow/file_resolution.py) for the full incident. Runs
        # every attempt, unconditionally, whenever a pom.xml exists in the
        # worktree - cheap to detect "already customized" and no-op, and
        # correctly re-applies if a retry rewrites pom.xml back to a plain
        # Maven-convention shape.
        pom_path = os.path.join(ctx.worktree_path, "pom.xml")
        if os.path.exists(pom_path):
            try:
                with open(pom_path, "r", encoding="utf-8", errors="replace") as fh:
                    pom_content = fh.read()
            except OSError:
                pom_content = None
            if pom_content is not None:
                skills_relpath = os.path.relpath(ctx.kernel.config.paths.skills, ctx.workspace_path)
                corrected_pom = ensure_maven_covers_nonconventional_java_files(
                    pom_content, compile_known_files, skills_relpath,
                )
                if corrected_pom is not None:
                    logger.info(
                        "pom.xml doesn't cover the actual location of known .java files under "
                        "Maven's default sourceDirectory - deterministically widening it to the "
                        "workspace root (excluding the skills and .kriya directories)."
                    )
                    pom_content = corrected_pom
                    with open(pom_path, "w", encoding="utf-8") as fh:
                        fh.write(corrected_pom)

                # Deterministic exec.mainClass property correction - found live,
                # 2026-08-22 (ignite_qpid_protocol): see
                # correct_exec_main_class_property()'s own docstring
                # (kriya/workflow/file_resolution.py) for the full incident -
                # every active skill's own example pom.xml sets a plausible-
                # looking but arbitrary default (com.example.App and siblings)
                # for this property, which the Developer can copy verbatim
                # even though the real class it generated lives in the
                # default package. compile succeeds either way (this property
                # has no bearing on what compiles) - `mvn exec:exec` only
                # fails at RUNTIME with "Could not find or load main class
                # App", the exact failure this closes.
                compile_java_files = [f for f in compile_known_files if f.endswith(".java")]
                corrected_exec_main_class = correct_exec_main_class_property(
                    pom_content, _build_java_main_class_map(compile_java_files, ctx),
                )
                if corrected_exec_main_class is not None:
                    logger.info(
                        "pom.xml's <exec.mainClass> property doesn't match the real class Kriya "
                        "actually generated (likely copied verbatim from a skill's example) - "
                        "deterministically correcting it."
                    )
                    with open(pom_path, "w", encoding="utf-8") as fh:
                        fh.write(corrected_exec_main_class)

        # Deterministic package-declaration correction - found live,
        # 2026-08-22 (ignite_qpid_protocol milestone 3/4): see
        # strip_package_declaration_matching_source_root()'s own docstring
        # (kriya/workflow/file_resolution.py) for the full incident. Runs
        # every attempt, unconditionally, over every known .java file - cheap
        # to no-op for the (overwhelmingly common) case where a file either
        # isn't directly under src/main/java/ or already has no package.
        for java_relpath in (f for f in compile_known_files if f.endswith(".java")):
            java_abs_path = os.path.join(ctx.worktree_path, java_relpath)
            if not os.path.exists(java_abs_path):
                continue
            try:
                with open(java_abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    java_content = fh.read()
            except OSError:
                continue
            corrected_java = strip_package_declaration_matching_source_root(java_relpath, java_content)
            if corrected_java is not None:
                logger.info(
                    f"{java_relpath} sits directly under src/main/java/ with no subdirectory "
                    "nesting, so its package declaration is unconditionally invalid per Java's "
                    "own rules - deterministically stripping it to the default package."
                )
                with open(java_abs_path, "w", encoding="utf-8") as fh:
                    fh.write(corrected_java)

        compile_res = validator.run_compile_check(compile_known_files)
        if not compile_res["success"]:
            self_correction_result = None
            if ctx.kernel.config.autonomy.self_correction_loop_enabled:
                from kriya.workflow.self_correction import run_self_correction_loop
                logger.info(
                    "Compile gate failed - attempting bounded self-correction "
                    "micro-loop before raising QualityGateFailure."
                )
                self_correction_result = await run_self_correction_loop(
                    llm=ctx.developer.llm,
                    worktree_path=ctx.worktree_path,
                    validator=validator,
                    files_in_scope=compile_known_files,
                    # DENY_ALL means never writable here either - a bug in
                    # this recovery/fallback loop must not be able to bypass
                    # the same invariant AuthorizedFileWriter enforces at the
                    # real write gate below (see WriteScopeMode's own
                    # docstring for why "recovery paths can accidentally
                    # bypass otherwise correct invariants" is a confirmed,
                    # not hypothetical, risk here).
                    writable_files=(
                        [] if ctx.write_scope_mode == WriteScopeMode.DENY_ALL
                        else (ctx.allowed_write_relpaths or compile_known_files)
                    ),
                    compile_error_output=compile_res["output"],
                    active_code_context=active_code_context,
                    max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                )
                _record_self_correction_scope_conflict(
                    state, ctx, self_correction_result, "compile",
                )
                for incident in getattr(self_correction_result, "incidents", []):
                    state.record_event(RunEvent(
                        kind="auxiliary.failed",
                        attempt=state.attempt_number,
                        source=incident["source"],
                        authority=EventAuthority.AUXILIARY,
                        message=incident["message"],
                        failure_type=incident["type"],
                        operation="repair_with_patch",
                    ))

            if self_correction_result and self_correction_result.resolved:
                logger.info(
                    "Self-correction micro-loop resolved the compile failure in "
                    f"{self_correction_result.turns_used} turn(s)."
                )
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "compile",
                    "success": True,
                    "output": self_correction_result.final_compile_output,
                    "self_corrected": True,
                    "self_correction_turns": self_correction_result.turns_used,
                    "self_correction_transcript": self_correction_result.transcript,
                })
            else:
                # Deterministic cross-milestone Java package-mismatch check -
                # found live, 2026-08-22 (ignite_qpid_protocol milestone
                # 3/4): a fresh milestone's Architect chose a Maven-
                # conventional package for its own new file while an earlier
                # milestone's established file stayed in the default
                # package - a `cannot find symbol` error that recurred
                # BYTE-FOR-BYTE IDENTICAL across 3+ retries, because a class
                # in one named package can never reference a class in a
                # different (or default) package under any circumstances -
                # not a missing import, a genuine language-level
                # incompatibility no amount of prose-level retrying can
                # resolve. See find_cross_package_symbol_mismatch()'s own
                # docstring (kriya/workflow/failure_grounding.py) for the
                # full incident, including how the Developer's own reasoning
                # actually diagnosed this correctly, more than once, then
                # talked itself out of fixing it every time. Checked only
                # after self-correction's own micro-loop (if enabled)
                # already had its chance and didn't resolve it, so this
                # never competes with or short-circuits that separate
                # recovery path.
                cross_package_failure = None
                java_files_for_mismatch = sorted(
                    f for f in set(state.all_files_written) | set(ctx.established_files)
                    if f.endswith(".java")
                )
                if java_files_for_mismatch:
                    mismatch = find_cross_package_symbol_mismatch(
                        compile_res.get("output", ""),
                        _build_workspace_type_index(state, ctx),
                        _build_java_package_map(java_files_for_mismatch, ctx),
                    )
                    if mismatch:
                        missing_symbol, referencing_path, candidate_path = mismatch
                        java_packages_for_message = _build_java_package_map(
                            [referencing_path, candidate_path], ctx
                        )
                        message = build_cross_package_mismatch_message(
                            missing_symbol, referencing_path,
                            java_packages_for_message.get(referencing_path),
                            candidate_path, java_packages_for_message.get(candidate_path),
                        )
                        cross_package_failure = Failure(
                            type="cross_package_symbol_mismatch",
                            message=message,
                            raw_output=compile_res.get("output", ""),
                            file_locations=[FileLocation(filepath=referencing_path), FileLocation(filepath=candidate_path)],
                            likely_files=[referencing_path, candidate_path],
                            failed_content=_capture_failed_content(
                                ctx.worktree_path, [referencing_path, candidate_path]
                            ),
                            attempt=state.attempt_number,
                        )
                compile_message = f"COMPILATION FAILURE:\n{compile_res['output']}"
                if not cross_package_failure:
                    # See find_locator_files_outside_known_scope()'s own docstring
                    # for the real live incident this closes, 2026-08-22
                    # (ignite_qpid_protocol): a workspace reused across two
                    # unrelated runs left stale files on disk that the compiler's
                    # own precise locator named but this run's tracking never
                    # heard of - surfacing that plainly here, rather than letting
                    # it silently degrade to "no known file implicated," turns 8
                    # wasted retries into one clear, actionable diagnostic.
                    unrecognized = find_locator_files_outside_known_scope(
                        compile_res.get("output", ""), compile_known_files,
                    )
                    if unrecognized:
                        # Found live, PRV-03 hardened (2026-08-27): "not
                        # tracked by this run" does NOT mean stale - a real,
                        # currently-existing brownfield sibling (e.g.
                        # CustomerService.java, broken because THIS
                        # candidate changed the Customer record's own
                        # constructor arity) is exactly as "unrecognized"
                        # to compile_known_files as genuine leftover cruft
                        # from an unrelated run would be. The two are only
                        # distinguishable by checking whether the file
                        # actually exists in the real worktree - resolve
                        # that before asserting either story, rather than
                        # defaulting to the "stale" framing that's actively
                        # wrong for the common brownfield case.
                        real_siblings = resolve_repository_locator_files(
                            compile_res.get("output", ""), ctx.worktree_path, compile_known_files,
                        )
                        genuinely_unrecognized = sorted(
                            set(unrecognized) - {os.path.basename(p) for p in real_siblings}
                        )
                        if real_siblings:
                            compile_message += (
                                "\n\nNOTE: this error also references existing repository "
                                f"file(s) this run's own tracking never declared as scope: "
                                f"{', '.join(sorted(real_siblings))}. These are REAL, CURRENT "
                                "files, not stale content - if they only started failing to "
                                "compile because of THIS candidate's own change (e.g. an "
                                "existing type's constructor/signature changed), that is a "
                                "regression this candidate caused. Prefer preserving the "
                                "existing contract over expanding scope to repair every caller "
                                "it breaks."
                            )
                        if genuinely_unrecognized:
                            compile_message += (
                                "\n\nNOTE: this error also references file(s) not tracked by "
                                f"this run at all and not found anywhere in the current "
                                f"workspace either: {', '.join(genuinely_unrecognized)}. These "
                                "are likely stale/leftover content from an earlier, unrelated "
                                "run or attempt - not something the current milestone's own "
                                "files can fix. If they don't belong, they should be removed "
                                "from the workspace rather than repeatedly retried against."
                            )
                failure = cross_package_failure or _build_quality_gate_failure(
                    "compile", compile_message,
                    compile_res.get("output", ""), ctx.worktree_path, compile_known_files, state.attempt_number,
                )
                if self_correction_result is not None:
                    # The loop ran but didn't resolve it within budget - persist
                    # what it tried (its transcript) instead of silently
                    # discarding it, so a real exhaustion is diagnosable from
                    # gate_outcomes/traces.db afterward rather than only visible
                    # in the process's own (possibly-rotated) log file.
                    failure.self_correction_attempt = {
                        "turns_used": self_correction_result.turns_used,
                        "transcript": self_correction_result.transcript,
                        "final_compile_output": self_correction_result.final_compile_output,
                    }
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
        else:
            state.gate_outcomes.append({
                "attempt": state.attempt_number,
                "type": "compile",
                "success": True,
                "output": compile_res.get("output", "")
            })

        # A successful real compile is the authority for cross-file signature,
        # import, classpath, and build consistency. Record exact revisions only
        # after that gate; heuristics never mark a file validated.
        state.validated_file_revisions = {
            filepath: read_file_revision(os.path.join(ctx.worktree_path, filepath))
            for filepath in state.all_files_written
        }

        accepted_test_output: Optional[str] = None
        runnable_test_files = find_runnable_test_files(state.all_files_written)
        _raise_unsafe_process_boundary_test_candidate(
            state, ctx, runnable_test_files,
        )
        _raise_ungrounded_child_process_test_candidate(
            state, ctx, runnable_test_files,
        )
        target_test = extract_target_test(state.error_context, list(state.all_files_written))
        if target_test:
            logger.info(f"Quality Gates: Running targeted tests: {target_test}")
            test_repair_result = None
            test_res = validator.run_tests(target_test=target_test)
            if not output_confirms_nonzero_test_execution(test_res.get("output", "")):
                # A targeted command that collected zero tests says the selector
                # did not identify an executable test; it says nothing about the
                # generated application.  Do not route this orchestration error to
                # the Developer as a source repair.  Retry once with the runner's
                # native suite discovery, which is the authoritative fallback.
                logger.warning(
                    "Quality Gates: Targeted test selection collected zero tests for "
                    f"{target_test}; retrying the full suite before attributing a code failure."
                )
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "test_selection",
                    "success": False,
                    "output": test_res.get("output", ""),
                    "target_test": target_test,
                    "recovered_by": "full_suite",
                })
                target_test = None
                test_res = validator.run_tests()
                if not test_res["success"]:
                    failure = _build_test_quality_gate_failure(
                        "test", f"TEST FAILURE:\n{test_res['output']}",
                        test_res.get("output", ""), ctx.worktree_path,
                        state.all_files_written, state.attempt_number,
                    )
                    if failure.type == "test_process_terminated":
                        if _record_process_boundary_obligation(
                            ctx, state, violated=True,
                            evidence={"detected_via": "test_selection_fallback", "raw_output": failure.raw_output},
                        ):
                            failure.message += _PROCESS_BOUNDARY_RECURRENCE_ESCALATION
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                _record_process_boundary_obligation(ctx, state, violated=False, evidence={})
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "test",
                    "success": True,
                    "output": test_res.get("output", ""),
                    "selection_fallback": True,
                })
                accepted_test_output = test_res.get("output", "")

            if target_test and not test_res["success"]:
                if ctx.kernel.config.autonomy.self_correction_loop_enabled:
                    from kriya.workflow.self_correction import run_repair_loop
                    test_repair_result = await run_repair_loop(
                        llm=ctx.developer.llm,
                        worktree_path=ctx.worktree_path,
                        validator=validator,
                        files_in_scope=list(state.all_files_written),
                        # See the compile-gate self-correction call site's own
                        # comment (above, this module) for why DENY_ALL is
                        # checked explicitly here too.
                        writable_files=(
                            [] if ctx.write_scope_mode == WriteScopeMode.DENY_ALL
                            else (ctx.allowed_write_relpaths or list(state.all_files_written))
                        ),
                        compile_error_output=test_res["output"],
                        active_code_context=active_code_context,
                        max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                        failure_type="targeted_test",
                        target_test=target_test,
                    )
                    _record_self_correction_scope_conflict(
                        state, ctx, test_repair_result, "targeted_test",
                    )
                    for incident in getattr(test_repair_result, "incidents", []):
                        state.record_event(RunEvent(
                            kind="auxiliary.failed", attempt=state.attempt_number,
                            source=incident["source"], authority=EventAuthority.AUXILIARY,
                            message=incident["message"], failure_type=incident["type"],
                            operation="repair_with_patch",
                        ))
                if test_repair_result and test_repair_result.resolved:
                    _record_process_boundary_obligation(ctx, state, violated=False, evidence={})
                    state.gate_outcomes.append({
                        "attempt": state.attempt_number,
                        "type": "targeted_test",
                        "success": True,
                        "output": test_repair_result.final_compile_output,
                        "self_corrected": True,
                        "self_correction_turns": test_repair_result.turns_used,
                        "self_correction_transcript": test_repair_result.transcript,
                    })
                    test_res = {"success": True, "output": test_repair_result.final_compile_output}
                else:
                    failure = _build_test_quality_gate_failure(
                        "targeted_test", f"TARGETED TEST FAILURE:\n{test_res['output']}",
                        test_res.get("output", ""), ctx.worktree_path, state.all_files_written, state.attempt_number,
                    )
                    if test_repair_result is not None:
                        failure.self_correction_attempt = {
                            "turns_used": test_repair_result.turns_used,
                            "transcript": test_repair_result.transcript,
                            "final_validation_output": test_repair_result.final_compile_output,
                        }
                    if failure.type == "test_process_terminated":
                        if _record_process_boundary_obligation(
                            ctx, state, violated=True,
                            evidence={"detected_via": "targeted_test", "raw_output": failure.raw_output},
                        ):
                            failure.message += _PROCESS_BOUNDARY_RECURRENCE_ESCALATION
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
            if target_test and not (test_repair_result and test_repair_result.resolved):
                _record_process_boundary_obligation(ctx, state, violated=False, evidence={})
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "targeted_test",
                    "success": True,
                    "output": test_res.get("output", "")
                })
            if target_test:
                accepted_test_output = test_res.get("output", "")
        else:
            if runnable_test_files:
                logger.info(f"Quality Gates: Executing tests for {validator.stack} stack...")
                test_res = validator.run_tests()
                if not test_res["success"]:
                    failure = _build_test_quality_gate_failure(
                        "test", f"TEST FAILURE:\n{test_res['output']}",
                        test_res.get("output", ""), ctx.worktree_path, state.all_files_written, state.attempt_number,
                    )
                    if failure.type == "test_process_terminated":
                        if _record_process_boundary_obligation(
                            ctx, state, violated=True,
                            evidence={"detected_via": "full_suite", "raw_output": failure.raw_output},
                        ):
                            failure.message += _PROCESS_BOUNDARY_RECURRENCE_ESCALATION
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                _record_process_boundary_obligation(ctx, state, violated=False, evidence={})
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "test",
                    "success": True,
                    "output": test_res.get("output", "")
                })
                accepted_test_output = test_res.get("output", "")

        # PRV-11 (2026-08-30): obligation/ownership-aware, not a blind scan
        # over ctx.goal - see subtask_owns_test_obligation's own docstring
        # for the live incident (a FUTURE_ORDERED test obligation genuinely
        # owned by a LATER subtask was being treated as CURRENT for every
        # OTHER subtask, merely because the full top-level goal - correctly,
        # since the authority-isolation fix - is visible in every subtask's
        # own ctx.goal, not because anything actually reassigned ownership).
        this_subtask_owns_tests = subtask_owns_test_obligation(
            ctx.grounding_goal or ctx.goal, ctx.structured_plan, ctx.current_subtask_id,
        )
        if accepted_test_output is None and this_subtask_owns_tests:
            failure = Failure(
                type="test_acceptance",
                message=(
                    "TEST ACCEPTANCE FAILURE: the goal explicitly requires tests, "
                    "but no runnable test module was generated. Package initializers, "
                    "test-runner configuration, fixtures, and support files do not count as tests."
                ),
                raw_output="runnable_test_files=[]",
                likely_files=sorted(state.all_files_written),
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # The same acceptance rule applies whether extract_target_test() chose
        # one test or the validator ran the suite. Test-selection strategy must
        # never change an explicit user contract.
        if (
            accepted_test_output is not None
            and this_subtask_owns_tests
            and not output_confirms_nonzero_test_execution(accepted_test_output)
        ):
            failure = Failure(
                type="test_acceptance",
                message=(
                    "TEST ACCEPTANCE FAILURE: the goal explicitly requires tests, "
                    "but the configured test runner reported that zero tests executed."
                ),
                raw_output=accepted_test_output,
                likely_files=[
                    path for path in state.all_files_written
                    if "test" in path.lower() or "spec" in path.lower()
                ],
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # Quality Gates: Runtime Verification. Compiling and passing whatever tests
        # exist only proves the code is valid - it says nothing about whether it does
        # what the goal actually asked for, which matters most for goals with no test
        # suite at all. Judgment decides per-attempt whether this goal describes
        # self-terminating runtime behavior worth actually running and checking.
        autonomy_cfg_rv = ctx.kernel.config.autonomy
        if ctx.runtime_verification_required and not autonomy_cfg_rv.run_verification_enabled:
            message = (
                "REQUIRED_RUNTIME_VERIFICATION_DISABLED: the declared verification contract "
                "requires observable execution, but runtime verification is disabled."
            )
            failure = Failure(
                type="verification_infrastructure_failure", message=message,
                raw_output=message, attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        if ctx.runtime_verification_required and state.run_verification_declined:
            message = (
                "REQUIRED_RUNTIME_VERIFICATION_DECLINED: the declared runtime check was not "
                "authorized, so correctness remains unverified."
            )
            failure = Failure(
                type="verification_infrastructure_failure", message=message,
                raw_output=message, attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        if autonomy_cfg_rv.run_verification_enabled and not state.run_verification_declined:
            current_judgment_basis = _run_verification_basis_hash(ctx, state)
            if (
                state.cached_run_verification_judgment is not None
                and state.cached_run_verification_basis_hash != current_judgment_basis
            ):
                logger.info(
                    "Invocation-affecting workspace content changed - invalidating cached "
                    "runtime-verification judgment."
                )
                state.cached_run_verification_judgment = None
            if state.cached_run_verification_judgment is None:
                pom_content_for_judge = None
                try:
                    with open(os.path.join(ctx.worktree_path, "pom.xml"), "r", encoding="utf-8") as f:
                        pom_content_for_judge = f.read()
                except Exception as e:
                    logger.debug(f"No pom.xml available for run-verification judgment: {e}")
                raw_judgment = await ctx.run_verifier.judge(
                    goal=ctx.goal,
                    design=ctx.design,
                    # Merge in ctx.established_files (see its own docstring) -
                    # same "known_files should span the whole workspace, not
                    # just this attempt's own writes" gap as the self-
                    # diagnosis attribution fix above, just at a different
                    # call site. Found live, 2026-08-21 (ignite_qpid_protocol
                    # milestone 3/4): App.java (this milestone's own file)
                    # imports Protocol and needs ProtocolParser, both written
                    # by EARLIER milestones - judge() only ever saw
                    # state.all_files_written (this milestone's own writes:
                    # applicationContext.xml, App.java), so it had no way to
                    # know those two files existed at all, and inferred a
                    # bare `java -cp . App` assuming pre-compiled classes
                    # (or, on a pom.xml-less project, a Maven-classpath
                    # command that also doesn't apply) instead of a real
                    # `javac App.java Protocol.java ProtocolParser.java &&
                    # java App` - 6 attempts straight rewrote perfectly
                    # correct application code chasing a ClassNotFoundException
                    # that was never a code bug, until the run's time budget
                    # was exhausted.
                    files_written=sorted(set(state.all_files_written) | set(ctx.established_files)),
                    build_file_content=pom_content_for_judge,
                )
                if raw_judgment.get("infrastructure_error") and ctx.runtime_verification_required:
                    message = (
                        "VERIFICATION INFRASTRUCTURE FAILURE: runtime behavior is required, but "
                        f"the runtime-verification judge was unavailable: "
                        f"{raw_judgment['infrastructure_error']}"
                    )
                    failure = Failure(
                        type="verification_infrastructure_failure",
                        message=message,
                        raw_output=message,
                        attempt=state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                # Independent brutal review finding #2 (2026-08-15): don't trust
                # command_source="goal_explicit" as self-reported - verify it's
                # actually grounded in the goal text before it gets cached (and
                # before the approval-gate check below ever sees it), same
                # "never trust an LLM claim without an independent check" pattern
                # already applied to grade()'s likely_files. See
                # downgrade_ungrounded_goal_explicit_commands()'s own docstring
                # for the full reasoning.
                state.cached_run_verification_judgment = downgrade_ungrounded_goal_explicit_commands(
                    raw_judgment, ctx.goal
                )
                state.cached_run_verification_basis_hash = current_judgment_basis
            else:
                logger.debug("Reusing cached run-verification judgment from an earlier attempt in this run.")
            judgment = state.cached_run_verification_judgment
            if ctx.runtime_verification_required and not judgment.get("should_run"):
                message = _required_runtime_verification_missing_message(judgment)
                failure = Failure(
                    type="verification_infrastructure_failure", message=message,
                    raw_output=message, attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
            if (
                ctx.runtime_verification_required
                and judgment.get("should_run")
                and deterministic_sequence_kind(judgment.get("run_commands") or []) == "build"
            ):
                message = (
                    "BEHAVIORAL_GOAL_WITH_BUILD_ONLY_VERIFICATION: observable runtime behavior "
                    "is required, but the inferred sequence contains only build commands."
                )
                failure = Failure(
                    type="verification_infrastructure_failure", message=message,
                    raw_output=message, attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
            # Deterministic Java entrypoint resolution - applied fresh every
            # attempt (never cached alongside judge()'s own should_run/
            # success_criteria decision above, unlike everything else on
            # this dict): a retry can edit file content between attempts, so
            # which file has the real entrypoint must be re-checked against
            # what's actually on disk right now, not a stale snapshot. See
            # ground_java_entrypoint_in_no_build_file_projects()'s own
            # docstring (kriya/workflow/file_resolution.py) for the full
            # incident and design - this replaces a free-form LLM guess with
            # a real javac+java sequence for a Java project with no pom.xml/
            # build.gradle, the one shape PolymorphicValidator's stack
            # detection has zero deterministic compile capability for today.
            # Applied here, BEFORE the human-in-the-loop approval display
            # below builds commands_desc from judgment["run_commands"] -
            # never correct AFTER a human already approved a different,
            # uncorrected command than what would actually execute.
            if judgment["should_run"] and judgment.get("run_commands"):
                pom_content_for_correction = None
                try:
                    with open(os.path.join(ctx.worktree_path, "pom.xml"), "r", encoding="utf-8") as f:
                        pom_content_for_correction = f.read()
                except Exception:
                    pass
                entrypoint_known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
                java_files = [f for f in entrypoint_known_files if f.endswith(".java")]
                if java_files and not pom_content_for_correction:
                    corrected_commands = ground_java_entrypoint_in_no_build_file_projects(
                        judgment["run_commands"],
                        judgment["command_source"],
                        entrypoint_known_files,
                        _build_java_main_class_map(java_files, ctx),
                        extract_jvm_module_flags(ctx.skills_prompt),
                        pom_content_for_correction,
                    )
                    if corrected_commands is None:
                        # ground_java_entrypoint_in_no_build_file_projects() returns None
                        # (distinct from "unchanged") only when it's already checked and
                        # confirmed zero of files_written has a real main() method, yet the
                        # judged command still tries to `java <SomeClass>` anyway - that
                        # class name is provably fabricated, not merely unverified, so
                        # trusting it would just repeat the exact live incident (a
                        # hallucinated "ProtocolParserTest" class) that motivated this
                        # fix. Force should_run False rather than execute a command known
                        # in advance to fail - matches judge()'s own system-prompt rule
                        # for a pure-library milestone with no runnable entrypoint at all.
                        logger.info(
                            "Deterministic Java entrypoint resolution: no pom.xml/build.gradle "
                            "found and no known .java file has a main() method - overriding "
                            "should_run to False instead of executing a command that targets "
                            "a nonexistent entrypoint class."
                        )
                        judgment = dict(judgment)
                        judgment["should_run"] = False
                        judgment["run_commands"] = None
                    elif corrected_commands != judgment["run_commands"]:
                        logger.info(
                            "Deterministic Java entrypoint resolution: no pom.xml/build.gradle "
                            "found and exactly one real entrypoint was detected - overriding the "
                            f"inferred run command(s) with {corrected_commands} instead of "
                            "trusting the model's own guess."
                        )
                        judgment = dict(judgment)
                        judgment["run_commands"] = corrected_commands
            if judgment["should_run"]:
                proceed_with_run = True
                if judgment["command_source"] == "inferred" and not state.run_verification_confirmed:
                    if autonomy_cfg_rv.mode == "human-in-the-loop":
                        commands_desc = "\n".join(
                            f"    {i}. {' '.join(cmd)}" for i, cmd in enumerate(judgment["run_commands"], 1)
                        )
                        confirm_reason = (
                            "Kriya judged that this goal describes runtime behavior compile/test "
                            "checks can't verify, and wants to actually run the generated app:\n"
                            f"  Command(s):\n{commands_desc}\n"
                            f"  Looking for: {judgment['success_criteria']}\n"
                            "Allow Kriya to execute these command(s) inside the sandboxed worktree?"
                        )
                        if ctx.approval_callback:
                            approved = ctx.approval_callback([], confirm_reason)
                            if asyncio.iscoroutine(approved):
                                approved = await approved
                            proceed_with_run = bool(approved)
                        else:
                            logger.warning("Runtime verification warrants human approval but no approval_callback is available. Proceeding under default policy.")
                    if not proceed_with_run:
                        state.run_verification_declined = True
                if proceed_with_run:
                    state.run_verification_confirmed = True
                    resolved_run_commands = [_resolve_run_command(cmd, ctx.worktree_path) for cmd in judgment["run_commands"]]
                    if resolved_run_commands != judgment["run_commands"]:
                        logger.info(
                            "One or more inferred run commands aren't resolvable as given here - "
                            "substituted Kriya's own interpreter/PATH-resolved equivalents."
                        )
                    rv_input_channel = judgment.get("input_channel") or "none"
                    resolved_run_commands, rv_stdin_payload, rv_contract_incomplete_reason = (
                        _apply_runtime_verification_contract(resolved_run_commands, rv_input_channel)
                    )
                    logger.info(
                        "RUNTIME_VERIFICATION_CONTRACT input_channel=%s argument_count=%d stdin_present=%s",
                        rv_input_channel, len(resolved_run_commands[-1]) if resolved_run_commands else 0,
                        bool(rv_stdin_payload),
                    )
                    if rv_contract_incomplete_reason:
                        rv_message = f"RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE: {rv_contract_incomplete_reason}"
                        logger.warning(rv_message)
                        rv_failure = Failure(
                            type="verification_infrastructure_failure", message=rv_message,
                            raw_output=rv_message, attempt=state.attempt_number,
                        )
                        state.gate_outcomes.append(rv_failure.to_gate_outcome())
                        raise QualityGateFailure(rv_failure)
                    jvm_flag_correction = _strip_jdk_incompatible_jvm_flags(ctx.worktree_path, state.java_home_override)
                    if jvm_flag_correction:
                        logger.warning(f"JVM flag preflight: {jvm_flag_correction}")
                        state.toolchain_warning = (
                            f"{state.toolchain_warning} {jvm_flag_correction}"
                            if state.toolchain_warning else jvm_flag_correction
                        )
                    exec_pin_correction = _pin_exec_plugin_executable_to_resolved_jdk(ctx.worktree_path, state.java_home_override)
                    if exec_pin_correction:
                        logger.warning(f"JVM executable preflight: {exec_pin_correction}")
                        state.toolchain_warning = (
                            f"{state.toolchain_warning} {exec_pin_correction}"
                            if state.toolchain_warning else exec_pin_correction
                        )
                    logger.info(
                        "Quality Gates: Running runtime verification: "
                        + " && ".join(" ".join(cmd) for cmd in resolved_run_commands)
                    )
                    # Snapshot/clean around the actual execution, not just once
                    # per attempt - see clean_untracked_files_since()'s own
                    # docstring for the real incident (python_task_tracker,
                    # b-6/b-7) this closes: the worktree is deliberately reused
                    # across retry attempts (compile caches), but nothing ever
                    # cleaned up runtime state a PRIOR attempt's own verification
                    # run wrote to disk (a JSON store, a database) - so attempt
                    # N's run started from attempt N-1's leftover state instead
                    # of a fresh one, producing task-ID drift and "not found"
                    # failures that had nothing to do with the generated code.
                    pre_run_untracked = snapshot_untracked_files(ctx.worktree_path)
                    run_res = validator.run_app_sequence(
                        resolved_run_commands,
                        timeout=autonomy_cfg_rv.run_verification_timeout_seconds,
                        stdin_payload=rv_stdin_payload,
                    )
                    clean_untracked_files_since(ctx.worktree_path, pre_run_untracked)
                    _raise_runtime_verification_infrastructure_failure(
                        state, run_res, resolved_run_commands,
                    )
                    gate_type = "run_verification"
                    # Set unconditionally (only ever reassigned in the "plain
                    # nonzero exit, no hang" branch below - see its own
                    # comment for why the other two branches are deliberately
                    # NOT self-corrected) so the shared failure-raising block
                    # further down can attach it to the Failure regardless of
                    # which branch actually ran, mirroring the compile gate's
                    # own self_correction_attempt pattern above.
                    self_correction_result = None
                    verification_authority = "llm"
                    if run_res["timed_out"]:
                        # _run_cmd_with_timeout still reaps and captures whatever
                        # stdout/stderr the process produced before being killed (see
                        # kriya/tools/validate.py) - a forced kill does NOT mean nothing
                        # happened. Grading that captured output, same as a clean run,
                        # instead of short-circuiting straight to a flat "timed out"
                        # message, is what lets a genuinely-non-binary outcome surface.
                        # Confirmed live, 2026-08-04: a real Ignite/Qpid run printed its
                        # correct final "[RESULT]" output, then hung (an unclosed Ignite
                        # node's background threads kept the JVM alive) - the old flat
                        # message gave the retry loop zero signal, so every attempt kept
                        # trying timeout-tuning fixes that could never fix a genuine
                        # resource leak, burning the whole retry budget on the wrong
                        # class of change.
                        contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                        if contract_verdict is not None:
                            verification_authority = "contract"
                            logger.info(
                                "Runtime verification: using deterministic verification-contract "
                                "marker instead of LLM grading (timed-out run)."
                            )
                            grade = contract_verdict
                        else:
                            grade = await ctx.run_verifier.grade(
                                goal=ctx.goal,
                                success_criteria=judgment["success_criteria"],
                                output=run_res["output"],
                                returncode=run_res["returncode"],
                                files_written=list(state.all_files_written),
                                timed_out=True,
                            )
                        timeout_s = autonomy_cfg_rv.run_verification_timeout_seconds
                        if grade["passed"]:
                            # The goal's described behavior WAS genuinely produced -
                            # this is a categorically different defect than "wrong
                            # behavior": a self-terminating entrypoint that doesn't
                            # terminate is still a real bug (still fails this gate,
                            # still needs a retry), but the fix is almost always the
                            # resource lifecycle (see RESOURCE_LIFECYCLE_HEADER above),
                            # not the application logic that already produced the
                            # correct result - pointing the retry there directly,
                            # rather than at a generic timeout message, is the entire
                            # point of grading the captured output instead of skipping
                            # straight to a synthetic failure.
                            gate_type = "run_verification_hung"
                            grade["reasoning"] = (
                                f"The goal's described output WAS produced correctly, but the "
                                f"process never exited on its own and had to be killed after "
                                f"{timeout_s}s. This is still a real defect, not a false alarm - "
                                "almost always an unclosed resource (a connection, broker client, "
                                "executor, or similar) keeping the process alive after all "
                                "application logic already finished. Fix the resource lifecycle "
                                f"(see Resource Lifecycle above), not the application logic, which "
                                f"already works. Grader's evidence: {grade['reasoning']}"
                            )
                        else:
                            grade["reasoning"] = (
                                f"Run timed out after {timeout_s}s, and the output captured before "
                                f"the forced kill does not show the goal was achieved either: "
                                f"{grade['reasoning']}"
                            )
                        # A hang is always disqualifying regardless of what the
                        # captured-output grade concluded - only the message/gate_type
                        # above differ based on it.
                        grade["passed"] = False
                    elif not run_res["success"]:
                        # A non-final step failing can still leave the LAST step's
                        # returncode at 0 (every command runs regardless of an
                        # earlier step's exit code) - success reflects the whole
                        # sequence, not just the last command, so check that instead.
                        #
                        # Independent-review finding (2026-08-15): this branch used to
                        # short-circuit straight to a synthetic "one or more steps
                        # failed" message, never checking extract_contract_verdict()
                        # or calling grade() at all - the ONLY one of the three outcome
                        # branches (clean run / timed out / plain nonzero exit) that
                        # skipped both. A plain nonzero exit with no hang is plausibly
                        # the single most common real failure shape, and it's exactly
                        # the case VERIFICATION_CONTRACT_HEADER's marker convention is
                        # for (an entrypoint that detects its own failure and exits
                        # nonzero after printing "[VERIFICATION] FAIL: <reason>") - that
                        # rich, deterministic diagnosis was being silently discarded in
                        # favor of a generic "exit code 1", and likely_files was always
                        # empty here, giving the retry loop zero file-attribution signal
                        # for this failure class specifically (the exact gap grade()
                        # exists to close - see its own docstring). Fixed by mirroring
                        # the clean-run branch below exactly: check the deterministic
                        # marker first, only fall back to the LLM grader if the
                        # generated program didn't comply with the contract.
                        deterministic_kind = deterministic_sequence_kind(resolved_run_commands)
                        if deterministic_kind is not None:
                            contract_verdict = None
                            verification_authority = "process_exit"
                            grade = {
                                "passed": False,
                                "reasoning": (
                                    f"One or more deterministic {deterministic_kind} verification "
                                    "commands returned a non-zero process status."
                                ),
                                "likely_files": [],
                            }
                        else:
                            contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                        if deterministic_kind is None and contract_verdict is not None:
                            verification_authority = "contract"
                            logger.info(
                                "Runtime verification: using deterministic verification-contract "
                                "marker instead of LLM grading (non-zero exit, no hang)."
                            )
                            grade = contract_verdict
                        elif deterministic_kind is None:
                            grade = await ctx.run_verifier.grade(
                                goal=ctx.goal,
                                success_criteria=judgment["success_criteria"],
                                output=run_res["output"],
                                returncode=run_res["returncode"],
                                files_written=list(state.all_files_written),
                            )

                        # A semantic expected-failure verdict is admissible
                        # only when every setup step succeeded and the final
                        # application process actually launched. JVM/module/
                        # executable launch failures were already classified
                        # as verifier infrastructure above and never reach
                        # this branch.
                        if grade.get("passed") and not runtime_application_step_started(run_res):
                            grade["passed"] = False
                            grade["reasoning"] = (
                                "Observed output appeared semantically correct, but a required "
                                "verification setup step failed or the application was never "
                                "shown to have started. "
                                f"Semantic evidence: {grade.get('reasoning', '')}"
                            )

                        # Bounded self-correction, widened 2026-08-22 from
                        # compile-only to also cover THIS specific run-
                        # verification shape - found live (ignite_qpid_protocol):
                        # a plain nonzero exit with no hang ("Could not find or
                        # load main class App") is very often an
                        # infrastructure/classpath/build-layout problem, not
                        # application logic - exactly the class of thing
                        # inspect_class/list_dependencies/list_compiled_output
                        # (kriya/workflow/self_correction.py) exist to ground a
                        # fix in, instead of the Developer chasing phantom
                        # import/package theories across several full-set
                        # retries (5+ attempts, observed live). Deliberately
                        # NOT applied to the timed-out branch (a hang is a
                        # resource-lifecycle bug in application logic, not
                        # something inspect_class/list_dependencies can help
                        # diagnose) or the clean-run branch (exit 0 but wrong
                        # output is almost always an application-logic defect
                        # too) - see run_self_correction_loop()'s own
                        # docstring for the same scoping rationale.
                        if not grade["passed"] and ctx.kernel.config.autonomy.self_correction_loop_enabled:
                            from kriya.workflow.self_correction import run_self_correction_loop
                            logger.info(
                                "Runtime verification failed with a plain nonzero exit (no hang) - "
                                "attempting bounded self-correction micro-loop before raising "
                                "QualityGateFailure."
                            )
                            run_verification_known_files = sorted(
                                set(state.all_files_written) | set(ctx.established_files)
                            )
                            self_correction_result = await run_self_correction_loop(
                                llm=ctx.developer.llm,
                                worktree_path=ctx.worktree_path,
                                validator=validator,
                                files_in_scope=run_verification_known_files,
                                # See the compile-gate self-correction call
                                # site's own comment above for why DENY_ALL is
                                # checked explicitly here too.
                                writable_files=(
                                    [] if ctx.write_scope_mode == WriteScopeMode.DENY_ALL
                                    else (ctx.allowed_write_relpaths or list(state.all_files_written))
                                ),
                                compile_error_output=(
                                    f"RUNTIME VERIFICATION FAILURE (plain nonzero exit): {grade['reasoning']}"
                                    f"\n\nCaptured output:\n{run_res['output']}"
                                ),
                                active_code_context=active_code_context,
                                max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                                failure_type="run_verification",
                            )
                            _record_self_correction_scope_conflict(
                                state, ctx, self_correction_result, "run_verification",
                            )
                            for incident in getattr(self_correction_result, "incidents", []):
                                state.record_event(RunEvent(
                                    kind="auxiliary.failed",
                                    attempt=state.attempt_number,
                                    source=incident["source"],
                                    authority=EventAuthority.AUXILIARY,
                                    message=incident["message"],
                                    failure_type=incident["type"],
                                    operation="repair_run_verification",
                                ))
                            if self_correction_result.resolved:
                                # Self-correction only ever validates via
                                # recompile (did the INFRASTRUCTURE issue get
                                # fixed) - it never re-runs the generated
                                # application itself (see the module's own
                                # docstring). Re-run the real run-verification
                                # sequence exactly once here to confirm actual
                                # behavior, reassigning run_res/grade/
                                # contract_verdict so the SHARED pass/fail
                                # handling below (and the success bookkeeping
                                # further down, reached only when grade
                                # ["passed"] is True) needs no duplication.
                                logger.info(
                                    "Self-correction micro-loop resolved the run-verification "
                                    f"infrastructure issue in {self_correction_result.turns_used} "
                                    "turn(s) - re-running the actual application to confirm."
                                )
                                pre_run_untracked_after_repair = snapshot_untracked_files(ctx.worktree_path)
                                run_res = validator.run_app_sequence(
                                    resolved_run_commands,
                                    timeout=autonomy_cfg_rv.run_verification_timeout_seconds,
                                )
                                clean_untracked_files_since(ctx.worktree_path, pre_run_untracked_after_repair)
                                repaired_deterministic_kind = deterministic_sequence_kind(
                                    resolved_run_commands
                                )
                                if repaired_deterministic_kind is not None:
                                    contract_verdict = None
                                    verification_authority = "process_exit"
                                    repaired_reasoning = (
                                        f"All deterministic {repaired_deterministic_kind} "
                                        "verification commands completed successfully (exit code 0)."
                                        if run_res["success"] else
                                        f"One or more deterministic {repaired_deterministic_kind} "
                                        "verification commands still returned a non-zero process "
                                        "status after repair."
                                    )
                                    grade = {
                                        "passed": bool(run_res["success"]),
                                        "reasoning": repaired_reasoning,
                                        "likely_files": [],
                                    }
                                else:
                                    contract_verdict = _extract_grounded_contract_verdict(
                                        run_res["output"], ctx.worktree_path, list(state.all_files_written),
                                    )
                                if repaired_deterministic_kind is None and contract_verdict is not None:
                                    verification_authority = "contract"
                                    grade = contract_verdict
                                elif repaired_deterministic_kind is None:
                                    grade = await ctx.run_verifier.grade(
                                        goal=ctx.goal,
                                        success_criteria=judgment["success_criteria"],
                                        output=run_res["output"],
                                        returncode=run_res["returncode"],
                                        files_written=list(state.all_files_written),
                                    )
                    else:
                        deterministic_kind = deterministic_sequence_kind(resolved_run_commands)
                        if deterministic_kind is not None:
                            # An allowlisted build/test tool's zero exit status
                            # is its authoritative verdict. Quiet output is a
                            # normal success mode, not evidence of missing
                            # runtime behavior for an LLM to reinterpret.
                            contract_verdict = None
                            verification_authority = "process_exit"
                            grade = {
                                "passed": True,
                                "reasoning": (
                                    f"All deterministic {deterministic_kind} verification "
                                    "commands completed successfully (exit code 0)."
                                ),
                                "likely_files": [],
                            }
                            logger.info(
                                "Runtime verification: trusting deterministic %s command "
                                "process status instead of behavioral LLM grading.",
                                deterministic_kind,
                            )
                        else:
                            contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                        if deterministic_kind is None and contract_verdict is not None:
                            verification_authority = "contract"
                            logger.info(
                                "Runtime verification: using deterministic verification-contract "
                                "marker instead of LLM grading."
                            )
                            grade = contract_verdict
                        elif deterministic_kind is None:
                            grade = await ctx.run_verifier.grade(
                                goal=ctx.goal,
                                success_criteria=judgment["success_criteria"],
                                output=run_res["output"],
                                returncode=run_res["returncode"],
                                files_written=list(state.all_files_written),
                            )
                    if not grade["passed"]:
                        # A compile error always names its own broken file
                        # (file:[line,col]) - a runtime failure's captured
                        # output (broker banners, SLF4J lines with no .java
                        # suffix) structurally never does. RunVerifierAgent.grade()'s
                        # already-validated likely_files (grade.get("likely_files"),
                        # absent on the two synthetic timed-out/step-failed grades
                        # built above, only present from a real grade() call) is
                        # passed straight into Failure.likely_files as
                        # extra_likely_files - no more stringify-into-the-message-
                        # then-re-derive-via-regex round-trip.
                        message = (
                            f"RUNTIME VERIFICATION FAILURE: {grade['reasoning']}"
                            f"\n\nCaptured output:\n{run_res['output']}"
                        )
                        # Append the grader's reasoning to what gets PERSISTED
                        # (Failure.raw_output -> to_gate_outcome()'s "output"
                        # field), mirroring the PASSED path a few lines below
                        # (which already does this) - found live, 2026-08-11
                        # (kriya-oneshot-protocol-ignite-qpid audit): without
                        # this, to_gate_outcome() prefers raw_output over
                        # message whenever raw_output is non-empty, so the
                        # grader's diagnosis (embedded in message, used only
                        # for the in-memory retry prompt) was silently absent
                        # from every persisted FAILED run_verification
                        # gate_outcome/traces.db record, even though it was
                        # computed and even though the identical PASSED case
                        # persists it - a real debugging/forensics asymmetry,
                        # confirmed directly against a real trace.
                        enriched_output = run_res["output"] + f"\n\n[Grader reasoning]: {grade['reasoning']}"
                        failure = _build_quality_gate_failure(
                            gate_type, message, enriched_output,
                            ctx.worktree_path, state.all_files_written, state.attempt_number,
                            extra_likely_files=grade.get("likely_files") or [],
                        )
                        if self_correction_result is not None:
                            # The loop ran (only ever possible from the plain-
                            # nonzero-exit branch above) but didn't leave the
                            # gate passing - either it never resolved, or it
                            # resolved the infrastructure issue but the
                            # re-run still failed for a genuine application-
                            # logic reason. Persist what it tried either way,
                            # same forensics reasoning as the compile gate's
                            # identical pattern above.
                            failure.self_correction_attempt = {
                                "turns_used": self_correction_result.turns_used,
                                "transcript": self_correction_result.transcript,
                                "final_compile_output": self_correction_result.final_compile_output,
                            }
                        failure_outcome = failure.to_gate_outcome()
                        failure_outcome.update({
                            "graded_by": verification_authority,
                            "commands": resolved_run_commands,
                            "steps": run_res.get("steps", []),
                        })
                        state.gate_outcomes.append(failure_outcome)
                        raise QualityGateFailure(failure)
                    run_verification_outcome = {
                        "attempt": state.attempt_number,
                        "type": "run_verification",
                        "success": True,
                        "output": run_res["output"] + f"\n\n[Grader reasoning]: {grade['reasoning']}",
                        # Reachable via the clean-run branch above, or (since
                        # 2026-08-22) the plain-nonzero-exit branch's own
                        # self-correction re-verification - the timed-out
                        # branch always forces grade["passed"] = False, so it
                        # can never reach here - contract_verdict is
                        # guaranteed in scope either way. Makes deterministic-
                        # contract-vs-LLM-grader compliance queryable directly
                        # from traces.db instead of grepping raw stdout logs
                        # by hand, which is what diagnosing the underlying
                        # reliability gap required this session, repeatedly.
                        "graded_by": verification_authority,
                        "commands": resolved_run_commands,
                        "steps": run_res.get("steps", []),
                        "deterministic_result": (
                            "PASS" if verification_authority == "process_exit" else None
                        ),
                    }
                    if self_correction_result is not None and self_correction_result.resolved:
                        # Same markers the compile gate's own self-correction
                        # success path already records - lets Pillar 3's
                        # lesson-extraction trigger (kriya/workflow/workflow.py)
                        # find this outcome and feed the real transcript
                        # (diagnosis + before/after + verification) into
                        # LiveFailureChannel.extract() as richer evidence than
                        # bare error_context/file_contents.
                        run_verification_outcome["self_corrected"] = True
                        run_verification_outcome["self_correction_turns"] = self_correction_result.turns_used
                        run_verification_outcome["self_correction_transcript"] = self_correction_result.transcript
                    state.gate_outcomes.append(run_verification_outcome)
                    logger.info(f"Quality Gates: Runtime verification PASSED: {grade['reasoning']}")
                    # A passing real-world run is exactly the proof the
                    # skill-verification gap check is looking for - mark every
                    # skill that contributed to this generation as verified so
                    # future runs stop asking about it.
                    for active_skill_name in ctx.active_skills:
                        try:
                            active_skill_obj = ctx.skill_engine.get_skill(active_skill_name)
                            context = _skill_verification_context(active_skill_obj, ctx.goal)
                            ctx.skill_engine.mark_verified(active_skill_name, context=context)
                            # Also flip per-rule provenance for exactly the
                            # rules that were part of this skill when this
                            # run's context was built (the pre-retry-loop
                            # snapshot) - not whatever rules.txt contains now.
                            if active_skill_obj.source_path and active_skill_name in ctx.active_skill_rules_snapshot:
                                from kriya.skills.skill import mark_rules_verified
                                mark_rules_verified(active_skill_obj.source_path, ctx.active_skill_rules_snapshot[active_skill_name])
                        except Exception as ex:
                            logger.debug(f"Failed to mark skill '{active_skill_name}' verified: {ex}")

    # Quality Gates: Migration Completion. Deliberately runs BEFORE Spec
    # Compliance and is never overridable by it - an explicit, objectively-
    # testable migration obligation (see kriya/workflow/migration.py's own
    # docstring for the PRV-05, 2026-08-28 incident) must not be reopened by
    # a semantic verdict that disagrees.
    #
    # PRV-05 run 6 (2026-08-28): this used to call resolve_migration_
    # obligation() fresh, per attempt, against ctx.workspace_path - re-
    # inferring source/target identity from whatever state the repository
    # happened to be in at that moment, which is exactly the timing-
    # sensitive defect this module's own docstring now documents. Reads the
    # run's ONE already-resolved migration_resolution instead (see
    # AttemptContext.migration_resolution's own docstring) - identity is
    # fixed; only find_migration_incomplete's completion CHECK still runs
    # fresh, against this attempt's own current candidate tree, which is
    # exactly what it's for.
    #
    # Only relevant to THIS attempt when its own scope (architect_files)
    # actually touches the obligation's grounded consumer(s) or the
    # manifest (pom.xml) - an unrelated subtask elsewhere in the same plan
    # must not be failed for a migration it was never responsible for.
    migration_obligation = (
        ctx.migration_resolution.obligation
        if ctx.migration_resolution is not None
        and ctx.migration_resolution.status == MigrationResolutionStatus.RESOLVED
        else None
    )
    subtask_owns_migration_scope = migration_obligation is not None and bool(
        set(ctx.architect_files) & (set(migration_obligation.grounded_consumers) | {"pom.xml"})
    )
    if subtask_owns_migration_scope:
        # validation_scope=CURRENT_SUBTASK (PRV-05 run 7, 2026-08-28): only
        # requirements DUE at this subtask's position in the plan can fail
        # this gate - a requirement whose only implicated file(s) belong to
        # a not-yet-reached, dependency-ordered subtask (e.g. this exact
        # PRV-05 plan's s4, which owns removing the old dependency from
        # pom.xml) is PENDING here, not FAILED, even though s1 legitimately
        # touches the grounded consumer. Degrades to the original always-
        # TERMINAL behavior when ctx.structured_plan/current_subtask_id are
        # None (a non-MA6-structured caller) - see MigrationValidationScope's
        # own docstring.
        migration_gap = find_migration_incomplete(
            migration_obligation, ctx.worktree_path,
            current_subtask_id=ctx.current_subtask_id,
            engineering_plan=ctx.structured_plan,
            validation_scope=MigrationValidationScope.CURRENT_SUBTASK,
            obligation_ledger=ctx.obligation_ledger,
            revision=state.attempt_number, source="migration.attempt_gate",
        )
        if migration_gap:
            message = (
                "MIGRATION INCOMPLETE: the goal explicitly requires replacing "
                f"{migration_gap['source_identity']} with {migration_gap['target_identity']}, but "
                f"{', '.join(migration_gap['reason_codes'])}. Grounded consumer(s) that must use "
                f"{migration_gap['target_identity']}: {', '.join(migration_gap['grounded_consumers']) or 'none'}."
            )
            # Union, not "first non-empty wins": found live, PRV-05
            # (2026-08-28 rerun) - a fully-migrated consumer with the
            # dependency still declared leaves BOTH unmigrated_consumers
            # and source_usage_files empty, so the failure had no
            # likely_files at all and couldn't point the retry/
            # attribution pipeline at the one file that actually needs
            # fixing (typically pom.xml, owned by a different, already-
            # completed subtask - see migration.py's own manifest_files
            # docstring). Already DUE-filtered by find_migration_incomplete
            # above, so this union never includes a future-owned file.
            evidence_files = list(dict.fromkeys(
                migration_gap["unmigrated_consumers"]
                + migration_gap["source_usage_files"]
                + migration_gap["manifest_files"]
            ))
            failure = _build_quality_gate_failure(
                "migration_incomplete", message, message,
                ctx.worktree_path, state.all_files_written, state.attempt_number,
                extra_likely_files=evidence_files,
            )
            # PRV-05 run 7: this evidence is deterministic (parsed from
            # pom.xml/scanned imports), not a text-scan guess - it must
            # outrank the model's own self-diagnosis in attribute_failure()
            # (kriya/workflow/attribution.py), and its "high" confidence
            # lets the existing plan-scope-conflict check (retry_strategy.py)
            # correctly hard-stop rather than call the Developer at all if
            # ever an authoritative target genuinely falls outside this
            # subtask's authorized write scope.
            failure.authoritative_files = evidence_files
            failure.diagnostics = {
                **(failure.diagnostics or {}),
                "reason_code": "MIGRATION_INCOMPLETE",
                "reason_codes": migration_gap["reason_codes"],
                "pending_reason_codes": migration_gap.get("pending_reason_codes", []),
            }
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

    # Quality Gates: Goal Spec Compliance. Compiling, passing tests, and (when
    # applicable) runtime verification above all structurally can't catch a goal's
    # LITERALLY named requirement (an exact field/method/class name, an exact type,
    # an exact constant) going unimplemented - the generated code can be perfectly
    # valid and even behave correctly while still being a different shape than what
    # was actually asked for. Runs once, here, only after every other gate has
    # already passed, so an attempt doomed for another reason never pays for it.
    # See SpecComplianceAgent's own docstring (kriya/agents/agent.py) for the live
    # incident (ignite_qpid_protocol milestone 1, 2026-08-21) this closes.
    if ctx.kernel.config.autonomy.spec_compliance_enabled:
        # A bounded consumer is judged against both its own candidate and the
        # already-verified upstream contracts it consumes. Restricting this to
        # files written by the current subtask made compliance incorrectly say
        # an upstream field/type was absent, then reopen a healthy owner.
        spec_check_files = sorted(
            set(state.all_files_written) | set(ctx.established_files)
        )
        spec_file_contents: Dict[str, str] = {}
        for spec_path in spec_check_files:
            spec_full_path = os.path.join(ctx.worktree_path, spec_path)
            if not os.path.exists(spec_full_path):
                spec_full_path = os.path.join(ctx.workspace_path, spec_path)
            if not os.path.exists(spec_full_path):
                continue
            try:
                with open(spec_full_path, "r", encoding="utf-8", errors="replace") as fh:
                    spec_file_contents[spec_path] = fh.read()
            except Exception as e:
                logger.debug(f"Spec compliance check: couldn't read {spec_path}, skipping it: {e}")
        authoritative_context = _spec_compliance_authoritative_context(ctx.obligation_ledger)
        # Correctness Continuity Part A (PRV-06, 2026-08-29): computed BEFORE
        # the call so the fingerprint reflects exactly what's about to be
        # judged, and captured here (not recomputed later) so the settled
        # check and the eventual ledger write always agree on one value.
        goal_spec_obligation_id = (
            _goal_spec_requirement_obligation_id(ctx.current_subtask_id)
            if ctx.current_subtask_id else None
        )
        goal_spec_evidence_fingerprint = _goal_spec_evidence_fingerprint(ctx.goal, spec_file_contents)
        settled_goal_spec = _settled_goal_spec_requirement(
            ctx.obligation_ledger, goal_spec_obligation_id, goal_spec_evidence_fingerprint,
        )
        spec_result = await ctx.spec_compliance.check(
            goal=ctx.goal, files_written=spec_check_files, file_contents=spec_file_contents,
            authoritative_context=authoritative_context,
        )
        if spec_result.get("status") == "indeterminate":
            # SpecComplianceAgent.check() returns this when the model's own
            # verdict is internally contradictory (compliant=false naming no
            # concrete missing requirement) - used to be silently forced to
            # compliant=True (see that method's own docstring for the PRV-05,
            # 2026-08-28 incident this closes: a real migration failure the
            # model's own reasoning had already identified became a
            # fabricated PASS). One bounded re-evaluation, not a retry-budget
            # spend - if it's STILL indeterminate, stop rather than guess
            # either way; trusting a bare compliant=false here would just
            # move the same problem (an unreliable LLM verdict as sole
            # authority) in the opposite direction.
            spec_result = await ctx.spec_compliance.check(
                goal=ctx.goal, files_written=spec_check_files, file_contents=spec_file_contents,
                authoritative_context=authoritative_context,
            )
        if spec_result.get("status") == "indeterminate":
            if _migration_obligations_all_satisfied(ctx.obligation_ledger):
                # MA8 follow-up (found live, PRV-05, 2026-08-28): this branch
                # used to raise unconditionally, with ZERO reference to
                # ctx.obligation_ledger, even though the sibling "not
                # compliant, with actual named requirements" branch just
                # below already arbitrates against it. A candidate whose
                # migration was ALREADY fully and correctly complete
                # (confirmed live: the real worktree files, not just the
                # log - pom.xml, JsonService.java, JacksonConfig.java all
                # correct) got destabilized into an 11-attempt budget
                # exhaustion by this exact gap - the model's own repeated
                # verdict ("the code still uses Jackson... does not show
                # replacement of any prior library") is the same direction-
                # hallucination class this whole gate exists to catch, just
                # reached through the indeterminate shape instead of a
                # named-requirement one. The up-front authoritative_context
                # prompt addition alone did not prevent it - confirming the
                # spec's own "do not trust the prompt alone" warning live.
                # Same suppression treatment as the arbitrated-requirements
                # branch below: normalize to compliant so the rest of this
                # function's existing success path handles it, rather than
                # adding a second, parallel success path.
                logger.warning(
                    "Quality Gates: Goal spec compliance returned an internally "
                    "contradictory INDETERMINATE verdict twice in a row while every "
                    "current migration obligation is deterministically SATISFIED - "
                    "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY, suppressed (treated as "
                    "compliant, not used to trigger retry): %s",
                    spec_result.get("reasoning"),
                )
                spec_result = {
                    "compliant": True, "reasoning": spec_result.get("reasoning", ""),
                    "missing_requirements": [], "likely_files": [],
                }
            else:
                message = (
                    "SPEC COMPLIANCE INDETERMINATE: the compliance check returned an "
                    "internally contradictory verdict (compliant=false naming no concrete "
                    f"missing requirement) twice in a row: {spec_result['reasoning']}"
                )
                failure = Failure(
                    type="spec_compliance_indeterminate", message=message, raw_output=message,
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
        if spec_result.get("status") == "unknown" and ctx.strict_spec_compliance:
            message = (
                "SPEC COMPLIANCE INFRASTRUCTURE FAILURE: authoritative execution cannot "
                f"treat an unavailable compliance judgment as satisfied: {spec_result['reasoning']}"
            )
            failure = Failure(
                type="verification_infrastructure_failure", message=message,
                raw_output=message, attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        if not spec_result["compliant"] and settled_goal_spec is not None:
            # Correctness Continuity Part A (PRV-06, 2026-08-29): the SAME
            # evidence (requirement text + every checked file's content,
            # byte for byte) already satisfied this exact obligation on an
            # earlier attempt for this subtask - e.g. s2's own planned pass,
            # re-judged again during a later owner-recovery attempt with no
            # relevant content change. A later contradictory JUDGMENT-
            # authority verdict against UNCHANGED evidence must never
            # overturn a settled fact (MA8: DETERMINISTIC > GROUNDED >
            # JUDGMENT; here, even within JUDGMENT itself, unchanged
            # evidence cannot be destabilized by a mere re-ask of the same
            # question). Live incident this closes: byte-identical App.java
            # content passed goal_spec_compliance during s2's own pass, then
            # failed the same check type during s2's owner-recovery,
            # inventing a "protocolVersion field" requirement that appeared
            # nowhere in the goal, plan, or either file.
            logger.warning(
                "Quality Gates: Goal spec compliance returned a contradictory verdict against "
                f"UNCHANGED evidence already SATISFIED at attempt {settled_goal_spec.revision} for "
                f"{settled_goal_spec.id!r} - SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY, suppressed "
                f"(not used to trigger retry): {spec_result.get('reasoning')}"
            )
            spec_result = {
                "compliant": True, "reasoning": spec_result.get("reasoning", ""),
                "missing_requirements": [], "likely_files": [],
                "reason_code": "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY",
            }
        arbitrated_contradictions: List[str] = []
        if not spec_result["compliant"]:
            # MA8 (PRV-05 run #8, 2026-08-28) - kriya/workflow/obligations.py.
            # SpecComplianceAgent is pure LLM judgment (authority=JUDGMENT) -
            # a missing_requirement whose text correlates to a migration
            # identity the DETERMINISTIC migration gate already reports fully
            # SATISFIED must never drive a retry; DETERMINISTIC always
            # outranks JUDGMENT for the same real-world fact. Found live: "the
            # code still uses Jackson... does not show evidence of replacing
            # the old dependency" fired AFTER the migration was actually
            # complete, and drove 3 further wasted, destabilizing attempts.
            # Only ever suppresses requirements when EVERY current migration
            # obligation is SATISFIED (never touches one while the
            # deterministic check itself still reports a violation - that's
            # not a contradiction, judgment and determinism simply agree).
            kept_requirements, arbitrated_contradictions = _spec_requirements_contradicting_authority(
                spec_result["missing_requirements"], ctx.obligation_ledger,
            )
            if arbitrated_contradictions:
                logger.warning(
                    "Quality Gates: Goal spec compliance reported requirement(s) that contradict "
                    "an authoritative deterministic SATISFIED obligation - SPEC_COMPLIANCE_"
                    "CONTRADICTS_AUTHORITY, suppressed (not used to trigger retry): %s",
                    arbitrated_contradictions,
                )
            # PRV-11 (2026-08-30): the other half of build_subtask_goal_
            # text()'s own authority-isolation split - a prompt instruction
            # telling SpecComplianceAgent not to treat a Planned-Implementation
            # -only identifier as a requirement is not a deterministic
            # guarantee (same "do not trust the prompt alone" principle the
            # migration arbitration above already applies), and this model
            # was observed reconstructing its own worked counter-example
            # almost verbatim. Deterministically re-checks each surviving
            # requirement's own identifier token(s) against ctx.goal's two
            # labeled sections directly - never a prompt-only fix.
            kept_requirements, planner_only_requirements = _spec_requirements_naming_planner_only_identifiers(
                kept_requirements, ctx.goal,
            )
            if planner_only_requirements:
                logger.warning(
                    "Quality Gates: Goal spec compliance reported requirement(s) naming an "
                    "identifier that appears only in the Planned Implementation Strategy "
                    "section of the goal, never in the Authoritative Goal section - "
                    "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY (planner-only identifier), "
                    "suppressed (not used to trigger retry): %s",
                    planner_only_requirements,
                )
        else:
            kept_requirements = []
            arbitrated_contradictions = []
            planner_only_requirements = []
        if not spec_result["compliant"] and kept_requirements:
            missing_desc = "; ".join(kept_requirements)
            message = (
                "GOAL SPEC COMPLIANCE FAILURE: the goal names concrete requirements the "
                f"generated code doesn't satisfy: {missing_desc}\n\n{spec_result['reasoning']}"
            )
            # Full synchronous tree-walk + per-file read, re-run on every
            # failed spec-compliance retry; offload so it doesn't block the
            # event loop inside this async attempt.
            grounded_architectural_owners = await asyncio.to_thread(
                discover_response_construction_owners,
                ctx.worktree_path, ctx.grounding_goal or ctx.goal, spec_check_files,
            )
            failure = _build_quality_gate_failure(
                "goal_spec_compliance", message, message,
                ctx.worktree_path, state.all_files_written, state.attempt_number,
                extra_likely_files=list(dict.fromkeys(
                    (spec_result.get("likely_files") or [])
                    + grounded_architectural_owners
                )),
            )
            failure.diagnostics = {
                **(failure.diagnostics or {}),
                **({"grounded_architectural_owners": grounded_architectural_owners}
                   if grounded_architectural_owners else {}),
                **({"reason_code": "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY",
                    "arbitrated_contradictions": arbitrated_contradictions}
                   if arbitrated_contradictions else {}),
                **({"reason_code": "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY",
                    "planner_only_requirements": planner_only_requirements}
                   if planner_only_requirements else {}),
            }
            if goal_spec_obligation_id and ctx.obligation_ledger is not None:
                # Correctness Continuity Part A6: this IS new/changed evidence
                # (settled_goal_spec was None, or content genuinely differed) -
                # a real violation is always free to (re)invalidate.
                ctx.obligation_ledger.record(ObligationRecord(
                    id=goal_spec_obligation_id, kind=ObligationKind.GOAL_SPEC_REQUIREMENT,
                    status=ObligationStatus.VIOLATED, authority=ObligationAuthority.JUDGMENT,
                    description="goal_spec_compliance verdict for this subtask's checked files",
                    source="attempt.run_attempt", revision=state.attempt_number,
                    evidence={"fingerprint": goal_spec_evidence_fingerprint,
                              "missing_requirements": kept_requirements},
                    owner_subtask_id=ctx.current_subtask_id, terminal_required=False,
                ))
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        if goal_spec_obligation_id and ctx.obligation_ledger is not None:
            ctx.obligation_ledger.record(ObligationRecord(
                id=goal_spec_obligation_id, kind=ObligationKind.GOAL_SPEC_REQUIREMENT,
                status=ObligationStatus.SATISFIED, authority=ObligationAuthority.JUDGMENT,
                description="goal_spec_compliance verdict for this subtask's checked files",
                source="attempt.run_attempt", revision=state.attempt_number,
                evidence={"fingerprint": goal_spec_evidence_fingerprint},
                owner_subtask_id=ctx.current_subtask_id, terminal_required=False,
            ))
        state.gate_outcomes.append({
            "attempt": state.attempt_number,
            "type": "goal_spec_compliance",
            "success": True,
            "output": spec_result["reasoning"],
            **({"reason_code": "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY",
                "arbitrated_contradictions": arbitrated_contradictions}
               if arbitrated_contradictions else {}),
            **({"reason_code": "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY",
                "planner_only_requirements": planner_only_requirements}
               if planner_only_requirements else {}),
            **({"reason_code": spec_result["reason_code"]} if spec_result.get("reason_code") else {}),
        })
        logger.info(f"Quality Gates: Goal spec compliance PASSED: {spec_result['reasoning']}")

    # The isolated candidate passed its inner checks. Terminal full regression
    # and application still remain, so this must never claim overall success.
    state.candidate_gates_succeeded = True
    if state.api_contract_recovery:
        state.api_contract_recovery.candidate_gates_passed()
    state.record_event(RunEvent(
        kind="candidate_gates.passed",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.AUTHORITATIVE,
        details={"passed": True, "terminal": False},
    ))
    log_gate_banner(
        "CANDIDATE GATES", "PASSED", state.attempt_number,
        scope=ctx.execution_scope,
    )

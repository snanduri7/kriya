"""Failure-handling for the Developer + Quality Gates retry loop - the other
half of the attempt/failure cycle kriya/workflow/attempt.py's run_attempt()
covers. Extracted verbatim from kriya/workflow/workflow.py's
run_generation_workflow() (2026-08-11, Opportunity 2 Slice 3).

Deliberately NOT split into a smaller "pure decision" function plus several
inline leftovers, despite that being the original plan for this slice: the
real code interleaves classification (Failure/attempt_mode/fail_type),
error-triggered live lookup (network + human-approval-gated), LSP grounding
(a persistent client's lifecycle), retry-budget accounting, and the final
terminal/continue decision into one continuous sequence with real data
dependencies flowing through - state.last_attempt_mode set before this even
runs, current_failure_signature feeding both the live-lookup gate AND the
budget's last_failure_signature field. Carving out only the "pure" parts and
leaving the rest inline would have meant re-deriving values already computed
here, exactly the class of bug that made Slice 2 non-trivial - moving it as
one cohesive, thoroughly-tested unit was the safer call once the actual
shape of the code was read in full, not assumed from an earlier line-range
estimate.

PRD-031 (2026-09-27) split it only where no value crosses the cut:
kriya/workflow/recovery_coordinator.py classifies the exception (reads only
the exception, the attempt mode and the scope-denial grounding) and makes
the stop/continue decision (reads only the recorded state). Everything with
the data dependencies above stays together here as _record_attempt_failure.
"""
import hashlib
import logging
import os
from typing import Any, Optional

from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.attribution import (
    DETERMINISTIC_ATTRIBUTION_TIERS,
    AttributionResult,
    _detect_missing_build_manifest,
    attribute_failure,
    read_worktree_file,
)
from kriya.workflow.banners import log_gate_banner
from kriya.workflow.deterministic_failure_diagnostic import (
    DeterministicFailureCorrectability,
    evaluate_candidate_independent_failure,
)
from kriya.workflow.failure import (
    Failure,
    FailureAttributionKind,
    classify_failure_attribution,
)
from kriya.workflow.failure_grounding import (
    _build_error_source_context,
    build_failure_signature,
    classify_environment_failure,
    extract_error_search_terms,
    resolve_repository_locator_files,
)
from kriya.workflow.file_resolution import (
    IncompleteGenerationError,
    classify_api_recovery_file_roles,
    is_runnable_test_file,
)
from kriya.workflow.live_lookup import _augment_error_with_live_lookup
from kriya.workflow.lsp_integration import _build_lsp_diagnostics_context, _get_or_start_jdtls_client
from kriya.workflow.recovery_coordinator import ClassifiedAttemptFailure, RecoveryCoordinator
from kriya.workflow.repair_contract import RepairContractStatus
from kriya.workflow.retry_policy import (
    api_contract_recovery_handed_back,
    charge_failed_attempt,
    force_strategy_transition,
    observe_failure_family,
    reset_scoped_budgets_for_new_family,
)
from kriya.workflow.retry_progress import (
    NO_PROGRESS_TERMINAL_REASON,
    REGRESSION,
    ProgressVector,
    build_progress_vector,
    classify_progress,
)
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.state import APIContractRecovery, GenerationState

logger = logging.getLogger(__name__)


_REPAIR_FEEDBACK_FAILURE_TYPES = {
    "anchored_edit",
    "attribution_rejected",
    "diagnosis_mismatch",
    "misdirected_edit",
    "no_op_edit",
    "operation_contract",
    "structural_corruption",
    "unaddressed_error_location",
}


def _failure_from_validated_scope_denial(
    error: Exception, ctx,
) -> Optional[Failure]:
    """Turn an exact denied existing target into plan evidence."""
    if not isinstance(error, PolicyDeniedError):
        return None
    if error.result.reason_code != "FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE":
        return None
    if not isinstance(error.request.target, str) or not error.request.target:
        return None
    target = os.path.realpath(error.request.target)
    worktree = os.path.realpath(ctx.worktree_path)
    try:
        relative = os.path.normpath(os.path.relpath(target, worktree))
    except ValueError:
        return None
    if relative == ".." or relative.startswith(f"..{os.sep}"):
        return None
    # Automatic authority expansion is only justified for a real existing
    # owner - a hallucinated new path cannot mutate the approved plan. A
    # test file that genuinely exists on disk is NOT excluded here: a
    # dependent, not-yet-run subtask can legitimately own it (a downstream
    # test-update stage the current stage's own regression gate needs
    # before it can pass), and workflow_controller.py's
    # revise_plan_for_grounded_scope_owner() already handles a downstream
    # owner correctly (merges it forward, drops the now-redundant
    # dependency edge). Confirmed live, P2 production-validation run
    # (2026-09-05): a plan split "change EmployeeService.giveRaise" (s1)
    # and "update the existing EmployeeServiceTest that pins its old
    # behavior" (s2, depends_on=[s1]) into two subtasks; s1's own full
    # regression gate could never pass without updating that test, s1 has
    # no authority to write it, and the blanket exclusion below used to
    # send this straight to the unrecoverable-scope-denial circuit breaker
    # (see test_handle_attempt_failure_stops_immediately_on_first_
    # unrecoverable_scope_denial) instead of the plan-surgery path this
    # exact shape was built for - killing the whole run on a legitimate,
    # already-planned cross-subtask dependency.
    if not os.path.isfile(target):
        return None
    message = (
        "PLAN_SCOPE_DEFECT: generated repair targeted an existing "
        f"owner outside validated subtask scope: {relative}"
    )
    return Failure(
        type="plan_scope_conflict",
        message=message,
        raw_output=str(error),
        source="authorized_file_writer",
        authority="deterministic",
        likely_files=[relative],
        diagnostics={"grounded_scope_owner_files": [relative]},
    )


def record_workspace_progress(
    state: GenerationState,
    workspace_hash: str,
    limit: int,
    *,
    failure_signature=None,
    stage: Optional[str] = None,
    files=None,
    action: Optional[str] = None,
    vector: Optional[ProgressVector] = None,
) -> bool:
    """Classify every failed attempt and bound retries without progress.

    PRD-026: with ``vector`` (every production caller passes one), a vector
    this run already produced is REPEATED_VECTOR and counts toward
    ``limit``, whatever changed in between. That closes the A -> B -> A
    cycle the pairwise comparison below cannot see. Without a vector the
    pre-PRD-026 pairwise rules apply unchanged."""
    normalized_files = tuple(sorted(set(files or ())))
    same_workspace = workspace_hash == state.last_failed_workspace_hash
    stage_order = {
        "operation_contract": 0, "anchored_edit": 0,
        "structural_corruption": 1, "compile": 2,
        "test": 3, "run_verification": 4, "run_verification_hung": 4,
        "goal_spec_compliance": 5, "regression_test": 6,
    }
    action_changed = action != state.last_progress_action
    stage_regressed = (
        stage is not None
        and state.last_progress_stage is not None
        and stage in stage_order
        and state.last_progress_stage in stage_order
        and stage_order[stage] < stage_order[state.last_progress_stage]
    )
    repeated_action = (
        failure_signature == state.last_progress_failure_signature
        and stage == state.last_progress_stage
        and normalized_files == state.last_progress_files
    )
    classification, counts = classify_progress(
        already_seen=vector is not None and vector.digest() in state.progress_vector_digests,
        same_workspace=same_workspace, action_changed=action_changed,
        stage_regressed=stage_regressed, repeated_action=repeated_action,
    )
    if counts:
        state.consecutive_no_progress_attempts += 1
    else:
        state.consecutive_no_progress_attempts = 0
    if vector is not None:
        previous = state.last_progress_vector
        state.progress_vector_digests.setdefault(vector.digest(), state.attempt_number)
        state.last_progress_vector = vector
        state.record_event(RunEvent(
            kind="retry.progress_vector",
            attempt=state.attempt_number,
            source="retry_strategy.record_workspace_progress",
            authority=EventAuthority.ADVISORY,
            message=f"Retry progress classified {classification}.",
            details={
                "digest": vector.digest(),
                "classification": classification,
                "changed_dimensions": list(vector.changed_dimensions(previous)),
                "consecutive_no_progress_attempts": state.consecutive_no_progress_attempts,
                "distinct_vectors": len(state.progress_vector_digests),
            },
        ))
    state.last_failed_workspace_hash = workspace_hash
    state.last_progress_failure_signature = failure_signature
    state.last_progress_stage = stage
    state.last_progress_files = normalized_files
    state.last_progress_action = action
    state.last_progress_classification = classification
    if classification == REGRESSION:
        state.consecutive_no_progress_attempts = max(
            state.consecutive_no_progress_attempts, 2,
        )
    state.no_progress_terminated = state.consecutive_no_progress_attempts >= limit
    if state.no_progress_terminated and state.no_progress_reason is None:
        state.no_progress_reason = NO_PROGRESS_TERMINAL_REASON
        state.record_event(RunEvent(
            kind="retry.no_progress_terminal",
            attempt=state.attempt_number,
            source="retry_strategy.record_workspace_progress",
            authority=EventAuthority.ADVISORY,
            message=(
                f"{NO_PROGRESS_TERMINAL_REASON}: {state.consecutive_no_progress_attempts} consecutive "
                f"attempts without material progress (limit {limit})."
            ),
            details={
                "reason_code": NO_PROGRESS_TERMINAL_REASON,
                "classification": classification,
                "limit": limit,
                "last_vector_digest": vector.digest() if vector is not None else None,
            },
        ))
    return not state.no_progress_terminated


def _attempt_progress_vector(
    state: GenerationState, ctx: Any, failure: Any, failure_signature: Any, workspace_hash: str,
    missing_files: Any,
) -> ProgressVector:
    """PRD-026: the canonical progress vector of the attempt that just
    failed, from state Kriya already holds (never raw model text)."""
    profile = state.last_developer_request_profile
    recovery = state.api_contract_recovery
    plan = getattr(ctx, "structured_plan", None)
    diagnostics = getattr(failure, "diagnostics", None) or {}
    return build_progress_vector(
        failure_signature=failure_signature,
        workspace_hash=workspace_hash,
        implicated_files=getattr(failure, "likely_files", None) or (),
        missing_files=missing_files or (),
        evidence_fingerprint=state.budgets.last_retry_evidence_fingerprint,
        context_items=state.known_target_context_items,
        action=state.last_attempt_mode,
        protocol=recovery.phase.value if recovery is not None else None,
        request_profile=(
            f"{profile.model}@{profile.digest}" if profile is not None
            else state.last_model_override or ctx.kernel.config.llm.model
        ),
        plan_revision=plan.content_hash() if plan is not None else None,
        repair_contract=state.repair_contract,
        diagnostics=(diagnostics.get("reason_code"),) if isinstance(diagnostics, dict) else (),
    )


def compute_effective_workspace_hash(workspace_path: str, known_files=None) -> str:
    """Hash the live workspace content that a retry can actually change.

    A git ``HEAD`` tree is intentionally unsuitable here: Developer writes are
    uncommitted until the workflow succeeds, so every failed attempt otherwise
    appears identical.  Keep the retry signal local and stack-neutral by
    hashing regular workspace files while excluding Kriya/Git control data.
    """
    digest = hashlib.sha256()
    if known_files:
        for relative_path in sorted(set(known_files)):
            full_path = os.path.join(workspace_path, relative_path)
            digest.update(relative_path.encode("utf-8", errors="surrogateescape"))
            try:
                with open(full_path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                digest.update(b"<missing>")
        return digest.hexdigest()

    excluded_dirs = {
        ".git", ".kriya", ".pytest_cache", "__pycache__", "node_modules",
        "target", "build", "dist",
    }
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = sorted(name for name in dirs if name not in excluded_dirs)
        for name in sorted(files):
            full_path = os.path.join(root, name)
            relative_path = os.path.relpath(full_path, workspace_path)
            try:
                if not os.path.isfile(full_path) or os.path.islink(full_path):
                    continue
                digest.update(relative_path.encode("utf-8", errors="surrogateescape"))
                with open(full_path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                # A concurrently removed transient file should not turn failure
                # recovery itself into a new workflow failure.
                continue
    return digest.hexdigest()


async def handle_attempt_failure(state: GenerationState, ctx, e: Exception) -> bool:
    """Called from workflow.py's `except Exception as e:` after a failed
    run_attempt() call. Mutates state (error_context, last_implicated_files/
    last_missing_files, last_error_source_context, jdtls_client/
    jdtls_unavailable/lsp_warning, budgets, gate_outcomes, environment_failure,
    final_attempt_contents) and returns whether the retry loop should
    explicitly `break` now (True only for an environment/toolchain failure,
    a plan-scope conflict or no progress - genuine budget exhaustion is left
    to the `while` loop's own condition going False on its next check).

    PRD-031: sequenced by RecoveryCoordinator (kriya/workflow/recovery_coordinator.py):
    classify the exception, record it (_record_attempt_failure below), then
    decide from the recorded state via the retry policy."""
    decision = await RecoveryCoordinator(
        ground_scope_denial=_failure_from_validated_scope_denial, record_failure=_record_attempt_failure,
    ).handle(state, ctx, e)
    return decision.stop_loop


async def _record_attempt_failure(
    state: GenerationState, ctx, e: Exception, classified: ClassifiedAttemptFailure,
) -> None:
    """Record one classified failure into the run's state: the failure
    ledger and events, environment-failure classification, error-triggered
    live lookup, the repeated-failure and no-progress checks, attribution
    and LSP grounding, and the retry-budget charge. The stop/continue
    decision is the coordinator's, from the state recorded here."""
    raw_error_context = classified.raw_error_context
    attempt_mode = classified.attempt_mode
    failure = classified.failure
    is_unrecoverable_scope_denial = classified.unrecoverable_scope_denial
    if is_unrecoverable_scope_denial:
        state.unrecoverable_scope_denial_count += 1
    failure_detail = (
        f"{attempt_mode}, full-set {state.budgets.retry_count}/{ctx.max_retries} + "
        f"targeted {state.budgets.targeted_retry_count}/{ctx.targeted_max_retries}: {e}"
    )
    if failure.type == "regression_test":
        state.terminal_regression_succeeded = False
        log_gate_banner(
            "FULL REGRESSION", "FAILED", state.attempt_number, failure_detail,
            scope=ctx.execution_scope,
        )
    elif not state.candidate_gates_succeeded:
        log_gate_banner(
            "CANDIDATE GATES", "FAILED", state.attempt_number, failure_detail,
            scope=ctx.execution_scope,
        )
    state.overall_attempt_succeeded = False
    state.record_developer_attempt_outcome(getattr(ctx.developer, "llm", None), passed=False)
    log_gate_banner(
        "OVERALL ATTEMPT", "FAILED", state.attempt_number, failure_detail,
        scope=ctx.execution_scope,
    )
    previous_primary_failure = state.last_failure
    previous_error_source_context = dict(state.last_error_source_context)
    failure.attempt = state.attempt_number
    failure.mode = attempt_mode
    state.record_failure(failure, operation=attempt_mode)
    state.record_event(RunEvent(
        kind="attempt.failed",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.AUTHORITATIVE,
        operation=attempt_mode,
        details={
            "passed": False,
            "failure_type": failure.type,
            "candidate_gates_passed": state.candidate_gates_succeeded,
            "terminal_regression_passed": state.terminal_regression_succeeded,
            "applied": False,
        },
    ))
    failure_is_authoritative = getattr(failure, "authority", "authoritative") == "authoritative"
    # Advisory Developer feedback is useful retry/attribution evidence, but
    # may not replace the compiler/test/runtime failure the next repair prompt
    # must solve. FailureLedger enforces the same rule for event history; keep
    # GenerationState's active typed failure aligned with that authority model.
    if failure_is_authoritative or previous_primary_failure is None:
        state.last_failure = failure
    else:
        state.last_failure = previous_primary_failure
    fail_type = failure.type
    recovery_diagnostics = (failure.diagnostics or {}).get("api_contract_recovery")
    if recovery_diagnostics:
        violations = list(recovery_diagnostics.get("violations", []))
        protected = sorted({
            path for item in violations for path in item.get("evidence_files", [])
        })
        if recovery_diagnostics.get("mode") == "API_CONTRACT_RECOVERY":
            state.api_contract_recovery = APIContractRecovery.from_diagnostics(
                recovery_diagnostics
            )
            if state.api_contract_recovery.phase.value == "DETECTED":
                state.api_contract_recovery.begin_restoration()
        else:
            state.api_contract_recovery = APIContractRecovery.detected(
                violations,
                protected,
                classify_api_recovery_file_roles(
                    violations,
                    set(state.all_files_written) | set(ctx.established_files),
                ),
            )
            state.api_contract_recovery.begin_restoration()
        logger.warning(
            "Entering API_CONTRACT_RECOVERY: authoritative signatures=%s protected_evidence=%s",
            [f"{item['owner']}::{item['removed_signature']}" for item in violations],
            protected,
        )
    is_incomplete_generation = isinstance(e, IncompleteGenerationError)

    # Error-triggered live lookup: only for a REPEATED failure (same
    # fail_type + same extracted tool/library terms, or identical raw
    # text if none were extracted) - a first-time failure resolves
    # normally most of the time and doesn't need it. Scoped to
    # compile/run_verification failures, since those are the ones most
    # likely to be a generic tooling/config gap a search can actually
    # resolve (a test-assertion failure is usually application-logic-
    # specific, not something external docs fix). Terms are extracted
    # via a hard, code-enforced regex restricted to Maven/Gradle-style
    # groupId:artifactId coordinates found IN the error text, plus
    # (separately, safety-bounded via the worktree's own declared
    # dependencies below) a wrong-import-path shape - the same
    # query-safety boundary as the existing goal/design-stage live
    # lookup: never the raw error/stack-trace text itself, which can
    # contain project-specific class/variable names. When neither
    # matches (e.g. a plain Java stack trace), the raw text itself
    # is the fallback signature - normalized first to strip Maven's
    # own always-different build-timing lines, or two occurrences
    # of the exact same failure would never compare equal.
    state.environment_failure = (
        failure.message
        if failure.type in {
            "time_budget_exhausted", "verification_infrastructure_failure",
            # PRV-06 completion (2026-08-29): an internal Kriya exception
            # is exactly as unfixable-by-retrying as these two - stop
            # immediately (RetryAction.STOP_ENVIRONMENT) rather than
            # burning further Developer attempts or classifying it via
            # classify_environment_failure()'s own text-pattern heuristics,
            # which were never designed to recognize an arbitrary internal
            # traceback.
            "internal_framework_error",
            # SEC-001 (2026-09-11): same reasoning - see the
            # containment_setup_failure classification in
            # kriya/workflow/recovery_coordinator.py.
            "containment_setup_failed",
            # VAL-001 G1-DEVINV2 (2026-09-20): a full-regression block with
            # zero candidate-attributable evidence (kriya/workflow/
            # workflow.py's own _full_regression_unattributed branch, after
            # isolated-pristine replay of every ambiguous entry) is exactly
            # as unfixable-by-retrying as the three types above - no amount
            # of Developer regeneration can resolve an aggregate-level delta
            # that names no specific test.
            "regression_unattributed",
            # PRD-017: the fallback model cannot serve this attempt (evidence:
            # failed qualification, a required patch it cannot return, no
            # prompt room). Re-sending it cannot change that.
            "fallback_incompatible",
            # MODEL-EVIDENCE-HARDENING-001: a production Developer retry
            # whose retry-temperature identity is not QUALIFIED. Retrying
            # cannot qualify it.
            "retry_identity_not_qualified",
            # PRD-020: an original requirement without accepted evidence under
            # a blocking policy. The Developer cannot supply the verifier's
            # missing verdict or confirm an unverifiable requirement.
            "requirements_unresolved",
            # PRD-029: a ContractRegistry transition that cannot be made
            # exactly (unverified invalidated consumers, a corrupt registry,
            # an incomplete promotion). Regeneration cannot change it.
            "contract_registry",
            # PRD-031A: the static-analysis gate did not permit the commit.
            # A deterministic stop: its findings are never fed back to the
            # Developer as retry evidence in v1 (and scanner text never
            # reaches a prompt).
            "static_analysis_blocked", "static_analysis_unknown", "static_analysis_unavailable",
            # PRD-032: the verified candidate's terminal commit did not
            # commit. Regeneration cannot change the workspace's revision,
            # the commit evidence or a refused commit guard.
            "workspace_commit",
        }
        else classify_environment_failure(
            raw_error_context,
            worktree_path=ctx.worktree_path,
            known_files=set(state.all_files_written) | set(ctx.established_files) | set(ctx.expected_files_upfront),
        )
    )
    if is_unrecoverable_scope_denial and state.unrecoverable_scope_denial_count >= 1:
        # PRV-17 preflight correction (2026-09-03): stop on the FIRST
        # deterministically unrecoverable denial, not the second - once
        # _failure_from_validated_scope_denial() has already established
        # there is no real existing file to hand recovery off to, a retry
        # cannot discover a legal target that doesn't exist. No further
        # Developer/LLM call is made for this subtask after this point.
        #
        # Reuses state.environment_failure/RetryAction.STOP_ENVIRONMENT
        # purely as the STOP MECHANISM - decide_retry_action() itself
        # (kriya/workflow/retry_policy.py) only checks whether this field
        # is truthy, never its content, so that part genuinely is a
        # generic deterministic stop. Verified before reusing it, per
        # this fix's own review requirement, that its two DOWNSTREAM
        # consumers (kriya/cli.py's user-facing message, kriya/workflow/
        # workflow.py's failure_category trace classification) hard-code
        # an environment/toolchain-specific MEANING onto it - both are
        # updated alongside this change so this case is never reported to
        # a user or trace as a JVM/toolchain problem it is not.
        #
        # The one existing "scope/plan" terminal classification
        # (state.plan_scope_conflict) was considered and rejected: it
        # feeds workflow_controller.py's revise_plan_for_grounded_scope_
        # owner(), which unconditionally raises ValueError for any
        # grounded path that isn't a real file already on disk
        # (kriya/workflow/workflow_controller.py ~line 2326) - exactly
        # this case's target. Reusing it here would trade one confirmed
        # incident for a new, worse one (an uncaught crash), and would
        # pull an unowned/hallucinated path into MA8 owner-recovery
        # machinery designed for a real, discoverable owner. Not reused.
        state.environment_failure = (
            "UNAUTHORIZED_GENERATION_TARGET: the Developer proposed a write to repository "
            "metadata or Kriya control state (.git/.kriya, PLAT-039); candidate writes never "
            "reach them, so retrying cannot help."
            if classified.unrecoverable_denial_reason == "TRUSTED_CONTROL_PATH_DENIED" else
            "UNAUTHORIZED_GENERATION_TARGET: the Developer proposed a write outside "
            "this subtask's validated scope, and the target names no existing file "
            "with a real owner to hand recovery off to - retrying cannot discover a "
            "legal target that doesn't exist."
        )

    # Read fresh from the worktree's CURRENT pom.xml each attempt,
    # not cached once before the loop - the project's own
    # groupId:artifactId can legitimately change mid-run (e.g. the
    # Developer renaming the artifact while extending the project),
    # and a stale cached value would fail to exclude Maven's own
    # build banner for whatever the CURRENT attempt actually named
    # the project.
    own_project_coordinate = None
    worktree_dependency_coordinates = None
    try:
        from kriya.tools.validate import get_pom_dependencies, get_pom_own_coordinate
        worktree_pom_path = os.path.join(ctx.worktree_path, "pom.xml")
        own_project_coordinate = get_pom_own_coordinate(worktree_pom_path)
        worktree_dependency_coordinates = get_pom_dependencies(worktree_pom_path) or None
    except Exception as ex:
        logger.debug(f"Failed to resolve project's own pom.xml coordinate/dependencies: {ex}")
    error_terms = extract_error_search_terms(
        raw_error_context,
        exclude_coordinates=[own_project_coordinate] if own_project_coordinate else None,
        dependency_coordinates=worktree_dependency_coordinates,
    )
    previous_failure_signature = state.budgets.last_failure_signature
    current_failure_signature = build_failure_signature(fail_type, raw_error_context)
    # Edit/protocol feedback is about the validator failure the attempted
    # repair was already addressing, not a new defect family entitled to fresh
    # retry budgets. Advisory failures follow the same rule structurally.
    if previous_failure_signature is not None and (
        not failure_is_authoritative or fail_type in _REPAIR_FEEDBACK_FAILURE_TYPES
    ):
        current_failure_signature = previous_failure_signature

    failure_family_changed = observe_failure_family(
        state.budgets, previous_failure_signature, current_failure_signature,
    )
    if failure_family_changed:
        logger.info(
            "Quality Gates surfaced a new failure family - resetting scoped "
            "targeted/fallback budgets while preserving the global attempt bound."
        )
        reset_scoped_budgets_for_new_family(state.budgets)
    state.error_context = raw_error_context
    if (
        current_failure_signature == previous_failure_signature
        # unaddressed_error_location included alongside compile: it's a
        # cheaper, earlier-firing variant of the same signal (the model's
        # own edit didn't address the reported location) - repeating it
        # is just as strong a "this model is stuck" signal as a repeated
        # compile failure, and today's repeat-based gate is the only
        # thing standing between a first occurrence and live lookup, so
        # it should fire on the same schedule, not a slower one.
        and fail_type in ("compile", "run_verification", "run_verification_hung", "unaddressed_error_location")
        and ctx.kernel.config.autonomy.web_lookup_enabled
        and ctx.kernel.config.search.base_url
        and error_terms
        and await ctx.approve_web_lookup(error_terms, ctx.kernel.config.search.base_url, ctx.web_lookup_query_callback)
    ):
        state.error_context = await _augment_error_with_live_lookup(
            raw_error_context, error_terms,
            ctx.kernel.config.search.base_url, ctx.kernel.config.search.top_k
        )
    state.budgets.last_failure_signature = current_failure_signature
    # Measure every failed attempt. Repeating the same failure/action against
    # identical bytes is the strongest no-progress evidence and must not reset
    # merely because the failure family stayed the same.
    current_workspace_hash = compute_effective_workspace_hash(
        ctx.worktree_path,
        set(state.all_files_written) | set(ctx.established_files),
    )
    # Candidate-independent deterministic failure detection (PRV-17,
    # 2026-09-08, P7 efficiency finding) - read-only reuse of state BEFORE
    # record_workspace_progress overwrites state.last_failed_workspace_hash
    # below; this block never writes to GenerationState.budgets or to any
    # field record_workspace_progress itself reads/writes, and never calls
    # it. See kriya/workflow/deterministic_failure_diagnostic.py's own
    # module docstring for the full incident and why this is deliberately a
    # separate mechanism from record_workspace_progress, MA8, and MA9's
    # cross-owner RECOVERY_NO_PROGRESS.
    if (
        ctx.deterministic_failure_diagnostics is not None
        and not state.environment_failure
    ):
        # R1 Deliverable 5 - observational only, does not read the store's
        # own conclusions or influence anything below. records_before/after
        # is a cheap, non-invasive proxy for "did this call actually record
        # a new (subtask, signature) conclusion" (which only happens on the
        # trigger-conditions-met + baseline-replay-attempted path inside
        # evaluate_candidate_independent_failure - see that function's own
        # docstring) without needing that function's own return contract to
        # change or a second, parallel replay-counting mechanism.
        state.candidate_independent_diagnostic_invocations += 1
        _diagnostics_records_before = len(ctx.deterministic_failure_diagnostics._records)
        diagnostic = evaluate_candidate_independent_failure(
            store=ctx.deterministic_failure_diagnostics,
            fail_type=fail_type,
            current_failure_signature=current_failure_signature,
            current_error_text=raw_error_context,
            previous_failure_signature=previous_failure_signature,
            workspace_changed=current_workspace_hash != state.last_failed_workspace_hash,
            has_implicated_files=bool(getattr(failure, "likely_files", None)),
            authoritative_workspace_path=ctx.workspace_path,
            known_files=ctx.established_files,
            autonomy_cfg=ctx.kernel.config.autonomy,
            subtask_id=ctx.current_subtask_id,
        )
        if len(ctx.deterministic_failure_diagnostics._records) > _diagnostics_records_before:
            state.baseline_replay_count += 1
        if diagnostic is not None and diagnostic.correctability == DeterministicFailureCorrectability.NON_CANDIDATE_CORRECTABLE:
            # Reuses the EXISTING state.environment_failure/STOP_ENVIRONMENT
            # mechanism as the stop signal (same reuse pattern already used
            # for UNAUTHORIZED_GENERATION_TARGET/NO_AUTHORIZED_REPAIR_TARGET
            # just above in this module) - no new retry_policy.py branch, no
            # budget change. workflow.py's own failure_category classifier
            # recognizes this exact prefix so it is reported as a validator
            # defect, never mislabeled "[ENVIRONMENT/TOOLCHAIN ISSUE]".
            state.environment_failure = (
                "CANDIDATE_INDEPENDENT_DETERMINISTIC_FAILURE: the same normalized "
                f"{fail_type} failure recurred after a materially different candidate, "
                "named no credible repair target, and was independently reproduced by "
                "replaying the same deterministic check against an isolated copy of the "
                "pre-candidate baseline - this is a validator/build-configuration/"
                "environment defect, not something further Developer regeneration can "
                f"resolve.\n\nBaseline replay output:\n{diagnostic.baseline_output}"
            )
            logger.error(
                "CANDIDATE_INDEPENDENT_DETERMINISTIC_FAILURE: baseline replay reproduced "
                "the identical %s failure signature - stopping further Developer "
                "regeneration for this subtask.",
                fail_type,
            )
    # The runtime contract allows two recovery actions and stops on the third
    # consecutive no-progress result. Older configurations commonly used 2
    # for the former failure-family-churn counter; do not reinterpret that as
    # an immediate stop under the richer per-attempt signal.
    no_progress_limit = max(
        3, ctx.kernel.config.autonomy.max_consecutive_no_progress_attempts,
    )
    if not record_workspace_progress(
        state,
        current_workspace_hash,
        no_progress_limit,
        failure_signature=current_failure_signature,
        stage=fail_type,
        files=getattr(failure, "likely_files", None),
        action=(
            f"{state.last_attempt_mode}:"
            f"{state.last_model_override or ctx.kernel.config.llm.model}"
        ),
        vector=_attempt_progress_vector(
            state, ctx, failure, current_failure_signature, current_workspace_hash,
            e.missing_files if is_incomplete_generation else (),
        ),
    ):
        logger.error(
            "Quality Gates stopped after %s consecutive attempts produced no "
            "effective workspace change (classification=%s).",
            no_progress_limit,
            state.last_progress_classification,
        )
    elif force_strategy_transition(
        state.budgets, consecutive_no_progress=state.consecutive_no_progress_attempts,
        targeted_max_retries=ctx.targeted_max_retries, has_fallback_model=bool(ctx.chain),
    ):
        state.record_event(RunEvent(
            kind="retry.strategy_transition",
            attempt=state.attempt_number,
            source="retry_strategy.handle_attempt_failure",
            authority=EventAuthority.ADVISORY,
            message="No material progress on consecutive attempts - forcing a strategy transition.",
            details={
                "reason": state.last_progress_classification,
                "consecutive_no_progress_attempts": state.consecutive_no_progress_attempts,
                "from_mode": state.last_attempt_mode,
                "targeted_budget_closed": True,
                "fallback_targeted_requested": bool(ctx.chain),
                "last_vector_digest": (
                    state.last_progress_vector.digest() if state.last_progress_vector is not None else None
                ),
            },
        ))

    # Re-evaluate which file(s) THIS failure implicates/is missing -
    # independent of whether this attempt was itself targeted, missing-
    # files, or full-set, so any attempt's failure can still kick off
    # (or redirect) a scoped retry afterward. An IncompleteGenerationError
    # sets last_missing_files and clears last_implicated_files (the
    # missing file, by definition, was never written, so it can never
    # appear in all_files_written for extract_implicated_files to find);
    # any other failure does the reverse - the two trackers are mutually
    # exclusive per attempt, matching that they route to different,
    # differently-built retry prompts.
    if is_incomplete_generation:
        state.last_missing_files = e.missing_files
        state.last_implicated_files = None
    else:
        # attribute_failure() (kriya/workflow/attribution.py) is the single
        # decision point for "which file(s) is this failure about" - replaces
        # the old inline "failure.likely_files or extract_implicated_files(...)"
        # fallback with a tiered ladder that also covers the case neither of
        # those two sources ever handled: a deterministic verification-
        # contract FAIL, which deliberately carries no locator at all (see
        # extract_contract_verdict()'s own docstring) and used to fall
        # straight through to a blind full-set walk. Reuses
        # ctx.developer.llm (the same configured LLMClient generation
        # already uses) for its triage tier, and state.budgets.retry_count/
        # ctx.chain so that tier rides the exact same model-escalation
        # ladder the current attempt is already on.
        # Only trust a self-diagnosis (kriya/workflow/attribution.py's
        # extract_self_diagnosed_files(), captured in attempt.py right after
        # the attempt that produced it) when THIS failure is the CONFIRMED
        # outcome of the very attempt that diagnosis was captured during -
        # both the signature AND the attempt number must match. Signature
        # alone is NOT sufficient (PRV-05 run 7, 2026-08-28 - see attempt.py's
        # state.last_self_diagnosis capture site for the full incident):
        # _REPAIR_FEEDBACK_FAILURE_TYPES collapses an edit-protocol failure's
        # signature onto whatever authoritative failure it's repairing, so a
        # diagnosis captured mid-repair can share its stored signature with a
        # LATER, genuinely fresh occurrence of that same authoritative
        # failure - one the diagnosis was never actually about. Gating on
        # attempt number too restricts reuse to "explains THIS attempt's own
        # outcome", closing that replay without narrowing anything else - a
        # diagnosis that legitimately predicts the NEXT attempt's own result
        # is still captured fresh, every attempt, whenever that attempt's own
        # response carries new FIX ANALYSIS text.
        self_diagnosed_files = None
        if (
            state.last_self_diagnosis
            and state.last_self_diagnosis[0] == current_failure_signature
            and state.last_self_diagnosis[2] == state.attempt_number
        ):
            self_diagnosed_files = state.last_self_diagnosis[1]

        # Unioned with ctx.established_files (kriya/workflow/attempt.py's
        # AttemptContext field - see its own docstring) so the locator/judge
        # tiers can also implicate an EARLIER milestone's file the raw failure
        # text names (e.g. a javac error literally saying "... in Protocol"),
        # not just files this attempt itself has written - this is what lets
        # a genuinely new failure (not yet a "confirmed repeat") redirect
        # immediately, one attempt earlier than self_diagnosed_files alone
        # (which only kicks in on the signature-matched repeat) would allow.
        known_attribution_files = sorted(
            set(state.all_files_written) | set(ctx.established_files)
        )
        repository_regrounded_files: list[str] = []
        # PRV-03 hardened (2026-08-27): a "compile" failure deserves the
        # exact same repository re-grounding as "regression_test" - a
        # candidate that changes an existing type's public contract (e.g.
        # a record constructor's arity) can break a REAL, existing sibling
        # file (e.g. CustomerService.java) this subtask never declared as
        # known scope. Before this widening, that sibling was invisible to
        # known_attribution_files/self_diagnosed_files here AND
        # attempt.py's own compile-failure message asserted it was "likely
        # stale/leftover content from an earlier, unrelated run" - actively
        # wrong for a real, current brownfield file broken by THIS
        # candidate's own change. Genuinely stale content (the scenario
        # this mechanism was originally built for, MA-era ignite_qpid_
        # protocol) still resolves to nothing here (it doesn't exist on
        # disk either), so this widening only ever adds real regrounding
        # signal, never manufactures one.
        if fail_type in ("regression_test", "compile"):
            repository_regrounded_files = resolve_repository_locator_files(
                failure.raw_output or failure.message,
                ctx.worktree_path,
                known_attribution_files,
            )
            known_attribution_files = sorted(
                set(known_attribution_files) | set(repository_regrounded_files)
            )
            if repository_regrounded_files:
                # The prior self-diagnosis was formed without this repository
                # owner in its candidate set. Fresh authoritative terminal
                # evidence must be allowed to invalidate that stale scope.
                self_diagnosed_files = None
        attribution_kind = classify_failure_attribution(fail_type, failure.message)
        failure.attribution_kind = attribution_kind.value
        if attribution_kind is FailureAttributionKind.PLAN_SCOPE_DEFECT:
            grounded_scope_owners = list(dict.fromkeys(
                (failure.diagnostics or {}).get("grounded_scope_owner_files", [])
            ))
            attribution = AttributionResult(
                tier="architectural_owner",
                files=grounded_scope_owners,
                confidence="high" if grounded_scope_owners else "low",
                reasoning=(
                    "AuthorizedFileWriter deterministically denied these existing production "
                    "targets outside the validated subtask scope."
                ),
            )
        elif fail_type == "managed_service_runtime_plan_gap":
            # Runtime-Evidence Plan Repair (PRV-17 Run 13, 2026-09-04): this
            # failure already carries deterministic evidence (a captured
            # ModuleNotFoundError traceback, pattern-matched in attempt.py -
            # see kriya.workflow.attempt._execute_managed_service_
            # verification) that a project-local Python module has no
            # current owner. Routing it through attribute_failure()'s
            # LLM-based self-diagnosis pipeline below would both waste a
            # call and risk downgrading DETERMINISTIC evidence to a JUDGMENT
            # verdict. No physical file path or owner subtask is resolved
            # here - required_files stays empty on purpose. Only
            # kriya/workflow/workflow_controller.py's dedicated RUNTIME_PLAN_
            # GAP branch (the control plane, never this attempt-scoped
            # function) may resolve a candidate artifact path/owner and
            # mutate the plan - see ObligationKind.RUNTIME_PLAN_GAP's own
            # docstring for the invariant this split preserves. tier=
            # "full_set" (not a DETERMINISTIC_ATTRIBUTION_TIERS member)
            # deliberately keeps scope_conflict_is_grounded False below, so
            # the generic PLAN_SCOPE_DEFECT machinery further down never
            # overwrites this dict - the same precedent VERIFICATION_
            # CONTRACT_DEFECT already established just below.
            attribution = AttributionResult(
                tier="full_set", files=[], confidence="high",
                reasoning=(
                    "Managed-service runtime verification captured deterministic evidence "
                    "of a missing project-local artifact; ownership/scope resolution is "
                    "reserved for the control plane's own Runtime-Evidence Plan Repair path."
                ),
            )
            diagnostics = failure.diagnostics or {}
            state.plan_scope_conflict = {
                "classification": "runtime_plan_gap",
                "reason_code": "RUNTIME_PLAN_GAP",
                "failure_type": fail_type,
                "reason": attribution.reasoning,
                "missing_logical_artifact": diagnostics.get("missing_python_module"),
                "artifact_kind": "python_module",
                "managed_service_outcome": diagnostics.get("managed_service_outcome"),
                "required_files": [],
            }
        elif attribution_kind in (
            FailureAttributionKind.VERIFICATION_CONTRACT_DEFECT,
            FailureAttributionKind.INFRASTRUCTURE_DEFECT,
        ):
            attribution = AttributionResult(
                tier="full_set", files=[], confidence="high",
                reasoning=(
                    f"{attribution_kind.value} is owned by the verification/control plane; "
                    "production source-file attribution is intentionally disabled."
                ),
            )
            if attribution_kind is FailureAttributionKind.VERIFICATION_CONTRACT_DEFECT:
                state.plan_scope_conflict = {
                    "classification": attribution_kind.value,
                    "reason_code": "VERIFICATION_CONTRACT_REVISION_REQUIRED",
                    "failure_type": fail_type,
                    "reason": attribution.reasoning,
                    "required_files": [],
                    "allowed_files": sorted(ctx.allowed_write_relpaths),
                }
        elif fail_type in (
            "verification_strategy_incompatible",
            "process_terminating_behavior_tested_in_process",
            "test_verification_infrastructure_failure",
        ):
            # is_runnable_test_file()'s filename regex is narrower than the
            # classify_file_role() directory-based check that can populate
            # likely_files upstream (failure_grounding.py's
            # _crashed_test_artifacts, which matches on an exact simple
            # class-name equality against the crashed test class - already
            # precise evidence). A Failsafe integration test (AppIT.java)
            # or Spock spec (FooSpec.groovy) is real attribution this
            # stricter regex just doesn't recognize by name - never drop
            # real evidence to an empty list solely because of that naming
            # gap; fall back to the unfiltered evidence instead.
            runnable_likely_files = [
                path for path in failure.likely_files
                if is_runnable_test_file(path)
            ]
            attribution_files = runnable_likely_files or failure.likely_files
            attribution = AttributionResult(
                tier="deterministic",
                files=attribution_files,
                confidence="high" if attribution_files else "low",
                reasoning=(
                    "Deterministic verification-safety evidence identified the test "
                    "artifact whose in-process strategy is incompatible with required "
                    "process termination; production behavior is not a repair target."
                ),
            )
        else:
            attribution = await attribute_failure(
                failure,
                known_attribution_files,
                state.budgets.retry_count,
                ctx.chain,
                ctx.developer.llm,
                lambda fp: read_worktree_file(ctx.worktree_path, fp),
                self_diagnosed_files=self_diagnosed_files,
                original_contents=state.all_original_contents,
                skip_fallbacks=tuple(state.incompatible_fallbacks),
            )
        implicated = attribution.files
        if (
            attribution_kind is FailureAttributionKind.TEST_DEFECT
            and any(not is_runnable_test_file(path) for path in implicated)
        ):
            # A test process produced the symptom, but deterministic/grounded
            # localization selected production. The repair owner is therefore
            # source, not the evidence-bearing test suite.
            attribution_kind = FailureAttributionKind.SOURCE_DEFECT
            failure.attribution_kind = attribution_kind.value
        if repository_regrounded_files and set(implicated) & set(repository_regrounded_files):
            attribution.reasoning = (
                f"{attribution.reasoning} Terminal regression re-grounded the precise locator "
                "to a unique existing repository file outside the prior repair set."
            )
        state.last_attribution = attribution
        # failure.likely_files must be overwritten with the FINAL attribution
        # result, not left at whatever _build_quality_gate_failure() computed
        # at construction time (its own internal extract_implicated_files()
        # call, before this module's self_diagnosis/triage tiers ever ran) -
        # found via a live test failure, not assumed: the actual retry
        # targeting already correctly used the local `implicated` var above,
        # but the PERSISTED gate_outcome's likely_files silently stayed
        # stale, same class of bug as the attribution_tier fix below.
        failure.likely_files = implicated
        failure.attribution_tier = attribution.tier
        failure.attribution_confidence = attribution.confidence
        failure.attribution_reasoning = attribution.reasoning
        # PRV-11 (2026-08-31): tier-gated, not confidence-gated. The
        # self_diagnosis tier is hardcoded confidence="high" unconditionally
        # (attribute_failure()'s own docstring: ranked ABOVE locator/judge
        # deliberately, since a repeat-confirmed self-diagnosis is real
        # evidence) - but "real evidence worth trusting for the NEXT
        # retry's own target" is a different bar than "reliable enough to
        # trigger PLAN SURGERY, reopening a completed subtask." Found live:
        # a self-diagnosis-driven "the model's own FIX ANALYSIS named a
        # different file as the real cause" was treated as grounded enough
        # to set plan_scope_conflict and reopen an upstream owner, even
        # though _scope_conflict_evidence_authority() (workflow_
        # controller.py) already correctly classifies that exact tier as
        # JUDGMENT, not DETERMINISTIC - just too late, after the reopening
        # had already happened. DETERMINISTIC_ATTRIBUTION_TIERS (attribution
        # .py) is the SAME set that function already uses, so the decision
        # to reopen and the resulting obligation's own recorded authority
        # can never disagree again. misdirected_edit remains its own
        # unconditional disjunct - a real, deterministic edit-safety fact
        # (the search block for this file was found instead inside a
        # DIFFERENT known file), not an attribution tier at all.
        scope_conflict_is_grounded = (
            attribution.tier in DETERMINISTIC_ATTRIBUTION_TIERS
            or fail_type == "misdirected_edit"
        )
        # Verification-routing fix (PRV-06, 2026-08-29): a DENY_ALL context
        # (a verification-only subtask, files=[]) has an EMPTY allowed
        # scope by construction - `ctx.allowed_write_relpaths` being falsy
        # there means "everything is out of scope," not "no scope
        # restriction applies," the exact ambiguity WriteScopeMode itself
        # was introduced to resolve for the write GATE (kriya/policy/
        # filesystem.py's own docstring) but this scope-conflict check
        # never consulted. Live incident this closes: a genuine runtime-
        # verification failure grounded to App.java (high-confidence
        # attribution) from a DENY_ALL subtask never set
        # state.plan_scope_conflict at all, because `ctx.allowed_write_
        # relpaths` (always []) made the `if` below false - so the SAME
        # cross-owner/effective-owner recovery machinery that already
        # handles a compile/test failure reaching an out-of-scope file
        # (§11 MA8.1) never got a chance to run for a runtime-verification
        # failure discovered from a non-mutating context. An ALLOWLIST
        # subtask's own real (non-empty) allowed scope is unaffected.
        is_deny_all_scope = getattr(ctx, "write_scope_mode", None) == WriteScopeMode.DENY_ALL
        if (ctx.allowed_write_relpaths or is_deny_all_scope) and scope_conflict_is_grounded:
            allowed_scope = set() if is_deny_all_scope else set(ctx.allowed_write_relpaths)
            outside_scope = sorted(set(implicated) - allowed_scope)
            if outside_scope:
                state.plan_scope_conflict = {
                    "classification": FailureAttributionKind.PLAN_SCOPE_DEFECT.value,
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": fail_type,
                    "reason": attribution.reasoning,
                    "required_files": outside_scope,
                    "allowed_files": sorted(allowed_scope),
                    "attribution_tier": attribution.tier,
                    "grounded_owner_files": (
                        outside_scope if attribution.tier == "architectural_owner" else []
                    ),
                    # MA8.1 (PRV-06, 2026-08-29): the raw grounded evidence
                    # (a compiler/test error, not an attribution summary) -
                    # preserved separately from "reason" because a later,
                    # failed grounded-owner plan-revision attempt overwrites
                    # "reason" with its OWN failure message
                    # (workflow_controller.py's `{**scope_conflict, "reason":
                    # revision_failure_reason}`), which would otherwise
                    # silently erase the actual cause an owner-recovery
                    # obligation needs to quote. Bounded length - this
                    # becomes Developer-facing prompt text, not a log dump.
                    "raw_evidence": (failure.raw_output or "")[:2000],
                }
        # For a QualityGateFailure type that appends its own gate_outcome at
        # the RAISE SITE (compile/test/regression_test/run_verification/
        # anchored_edit, all inside attempt.py) - that append already
        # happened, with a to_gate_outcome() snapshot taken BEFORE this
        # attribution ever ran, so likely_files/attribution_tier/confidence/
        # reasoning would silently stay stale/None in the persisted
        # gate_outcome despite being set on the Failure object right above.
        # Confirmed via a live test failure, not assumed: patch the
        # already-appended entry for THIS attempt in place rather than
        # relying on to_gate_outcome() being called again - the
        # de-dup-guarded append further below already handles the other
        # case (general_error, which never appends at a raise site)
        # correctly on its own.
        for outcome in state.gate_outcomes:
            if outcome.get("attempt") == state.attempt_number and outcome.get("type") == fail_type:
                outcome["likely_files"] = failure.likely_files
                outcome["attribution_tier"] = failure.attribution_tier
                outcome["attribution_confidence"] = failure.attribution_confidence
                outcome["attribution_reasoning"] = failure.attribution_reasoning
                outcome["attribution_kind"] = failure.attribution_kind
        # A missing build manifest the Architect never asked for at all
        # (see _detect_missing_build_manifest) takes priority over normal
        # implication scoping - extract_implicated_files() can never name
        # a file that was never written and never mentioned in the error
        # text, so without this check a "package X does not exist" error
        # would keep re-targeting the files that DO exist (which already
        # correctly declined to fix a dependency problem outside their
        # own scope) forever, rather than ever generating the one file
        # that would actually fix it.
        missing_manifest = (
            _detect_missing_build_manifest(ctx.worktree_path, raw_error_context)
            if fail_type in ("compile", "test", "targeted_test", "regression_test") else None
        )
        planned_prerequisite_owner = None
        if missing_manifest and ctx.structured_plan is not None and ctx.current_subtask_id:
            owners = [
                subtask.id
                for subtask in ctx.structured_plan.subtasks
                if any(pf.path == missing_manifest for pf in subtask.planned_files)
            ]
            if len(owners) == 1 and owners[0] != ctx.current_subtask_id:
                planned_prerequisite_owner = owners[0]

        if (
            missing_manifest
            and planned_prerequisite_owner
            and missing_manifest not in set(ctx.allowed_write_relpaths)
        ):
            # PRV-12: the compiler has established a missing dependency and
            # the approved plan already names its unique, different owner.
            # Retrying the consumer cannot create that out-of-scope artifact.
            # Surface directly through the existing PLAN_SCOPE_DEFECT
            # controller transition; MA8 authorization remains unchanged.
            reasoning = (
                f"Deterministic compile evidence requires planned prerequisite artifact "
                f"{missing_manifest!r}, owned by subtask {planned_prerequisite_owner!r}, "
                f"outside consumer {ctx.current_subtask_id!r}'s authorized scope."
            )
            owner_subtask = ctx.structured_plan.subtask_by_id(planned_prerequisite_owner)
            owner_capability = (
                owner_subtask.provides[0]
                if owner_subtask is not None and len(owner_subtask.provides) == 1
                else None
            )
            consumer_artifacts = sorted(
                path for path in failure.likely_files
                if path in set(ctx.allowed_write_relpaths)
            )
            attribution_kind = FailureAttributionKind.PLAN_SCOPE_DEFECT
            failure.attribution_kind = attribution_kind.value
            failure.likely_files = [missing_manifest]
            failure.attribution_tier = "authoritative_deterministic"
            failure.attribution_confidence = "high"
            failure.attribution_reasoning = reasoning
            state.last_attribution = AttributionResult(
                tier="authoritative_deterministic",
                files=[missing_manifest],
                confidence="high",
                reasoning=reasoning,
            )
            state.plan_scope_conflict = {
                "classification": FailureAttributionKind.PLAN_SCOPE_DEFECT.value,
                "reason_code": "PLANNED_PREREQUISITE_OWNER_REQUIRED",
                "failure_type": fail_type,
                "reason": reasoning,
                "required_files": [missing_manifest],
                "allowed_files": sorted(ctx.allowed_write_relpaths),
                "attribution_tier": "authoritative_deterministic",
                "grounded_owner_files": [missing_manifest],
                "required_owner_subtask_id": planned_prerequisite_owner,
                "required_capability": owner_capability,
                "consumer_artifacts": consumer_artifacts,
                "raw_evidence": (failure.raw_output or "")[:2000],
            }
            state.last_missing_files = None
            state.last_implicated_files = None
        elif missing_manifest:
            state.last_missing_files = [missing_manifest]
            state.last_implicated_files = None
        else:
            narrowed_implicated = implicated
            if (
                implicated
                and getattr(ctx, "write_scope_mode", None) == WriteScopeMode.ALLOWLIST
                and ctx.allowed_write_relpaths
            ):
                # Correctness Continuity Part B (PRV-06, 2026-08-29): an
                # implicated file outside this attempt's authorized write
                # scope must never become a generation TARGET, independent
                # of attribution confidence - unlike the scope_conflict_is_
                # grounded block above (which only ESCALATES to a plan-scope
                # conflict on high-confidence/misdirected_edit evidence),
                # this filter applies unconditionally, because offering an
                # unauthorized file as something the Developer should "focus
                # on" is never correct regardless of how confident the
                # attribution was. Live incident this closes: a medium-
                # confidence GOAL_SPEC_COMPLIANCE_FAILURE implicated both
                # App.java (authorized) and InMemoryService.java (owned by a
                # different subtask, NOT authorized for this owner-recovery
                # attempt) - too low-confidence to trip the existing
                # plan_scope_conflict escalation above, so InMemoryService.java
                # silently rode along into the next "Targeted retry" prompt
                # and burned a whole attempt on a MISDIRECTED_EDIT against a
                # file this attempt was never allowed to touch.
                allowed_scope = set(ctx.allowed_write_relpaths)
                narrowed_implicated = [f for f in implicated if f in allowed_scope]
                rejected = sorted(set(implicated) - allowed_scope)
                if rejected:
                    logger.warning(
                        "RECOVERY_GENERATION_TARGET_REJECTED filepaths=%s reason=outside_authorized_scope "
                        "allowed=%s - dropped from the next generation attempt's targets",
                        rejected, sorted(allowed_scope),
                    )
                    state.rejected_generation_targets.extend(rejected)
            elif implicated and getattr(ctx, "write_scope_mode", None) == WriteScopeMode.DENY_ALL:
                # Verification-routing fix (PRV-06, 2026-08-29): a DENY_ALL
                # context's authorized scope is the empty set BY
                # CONSTRUCTION (a verification-only subtask owns nothing) -
                # every implicated file is unconditionally out of scope,
                # the same reasoning as the ALLOWLIST branch above, just
                # with an always-empty allowed_scope rather than a
                # populated one. Without this, a low/medium-confidence
                # runtime-verification attribution (too weak to trip the
                # scope_conflict_is_grounded escalation above) could still
                # offer an unwritable file as the next attempt's "Targeted
                # retry" focus, inside a subtask that can never legally
                # write it.
                logger.warning(
                    "RECOVERY_GENERATION_TARGET_REJECTED filepaths=%s reason=deny_all_scope "
                    "- dropped from the next generation attempt's targets", sorted(implicated),
                )
                state.rejected_generation_targets.extend(sorted(implicated))
                narrowed_implicated = []
            state.last_implicated_files = narrowed_implicated if narrowed_implicated else None
            state.last_missing_files = None
            # Recovery admission (PRV-17, 2026-09-03): a GROUNDED failure
            # (attribution actually implicated real file(s), not a blind
            # guess) whose implication is ENTIRELY outside this subtask's
            # ALLOWLIST scope must not fall through to an ordinary FULL_SET
            # retry - decide_retry_action() (kriya/workflow/retry_policy.py)
            # has no visibility into WHY has_implicated_files became False
            # here, so without this it silently re-derives "attribution
            # found nothing" and asks the Developer to regenerate this
            # subtask's own (already-correct, irrelevant) authorized files
            # anyway - a full generation cycle that cannot possibly address
            # a failure whose real cause lives in a file this subtask can
            # never legally write. Distinct from "attribution found nothing
            # at all" (implicated empty from the start), which still
            # legitimately deserves an ordinary FULL_SET attempt. Reuses
            # state.environment_failure/STOP_ENVIRONMENT purely as the stop
            # MECHANISM, the same reuse already established (and reviewed)
            # for the write-time unrecoverable-scope-denial case above in
            # this same function - never overwrites an already-decided
            # classification.
            #
            # ALLOWLIST-only, deliberately NOT DENY_ALL: a DENY_ALL subtask
            # never calls the Developer for real generation regardless of
            # this retry decision (see run_attempt's own verification-only
            # branch/its dedicated test) - continuing its loop just retries
            # verification itself, never an unwinnable FULL_SET generation
            # cycle, so this admission gate has nothing to prevent there and
            # must not change its existing should_break=False behavior
            # (test_handle_attempt_failure_never_offers_a_deny_all_target_
            # even_at_low_confidence).
            #
            # state.plan_scope_conflict is None: mutually exclusive with the
            # EXISTING architectural-owner escalation above in this same
            # function (a DETERMINISTIC_ATTRIBUTION_TIERS-grounded implication
            # naming a real, existing file already routes there - owner-
            # recovery, not a stop) - never layer this admission gate on top
            # of a conflict that's already correctly routing to a real owner
            # (test_handle_attempt_failure_scope_denial_with_real_existing_
            # owner_is_unaffected).
            if (
                implicated and not narrowed_implicated
                and getattr(ctx, "write_scope_mode", None) == WriteScopeMode.ALLOWLIST
                and state.environment_failure is None
                and state.plan_scope_conflict is None
            ):
                state.environment_failure = (
                    f"NO_AUTHORIZED_REPAIR_TARGET: this failure is grounded to "
                    f"{sorted(set(implicated))!r}, entirely outside this subtask's "
                    f"authorized write scope ({sorted(set(ctx.allowed_write_relpaths))!r}) - "
                    "no generation against this subtask's own authorized files can fix a "
                    "failure whose real cause lives elsewhere; stopping before another "
                    "Developer call rather than paying for a full-set retry that cannot "
                    "possibly address it."
                )

    # MA9 (2026-08-29): the ONE place attribution's own output ordinarily
    # already narrows to "which file(s) does THIS failure implicate" - reused
    # here purely as prompt-emphasis metadata (RepairContract.
    # immediate_correction_targets), never to narrow participating_artifacts
    # itself or drop a participant from the next coordinated generation pass
    # (see repair_contract.py's own RepairContract docstring for the
    # three-way authorized/participating/immediate distinction this
    # maintains). A no-op whenever no RepairContract is active, or this
    # failure's own implication doesn't overlap the active contract's
    # participants at all (keeps the contract's existing targets rather than
    # collapsing to an empty tuple on an unrelated failure).
    if (
        state.repair_contract is not None
        and state.repair_contract.status == RepairContractStatus.ACTIVE
        and state.last_implicated_files
    ):
        narrowed = tuple(
            f for f in state.last_implicated_files
            if f in state.repair_contract.participating_artifacts
        )
        if narrowed:
            state.repair_contract.immediate_correction_targets = narrowed

    # Charged before the scope override below, so the override sees whether
    # this attempt spent the last of recovery's own budget. Nothing after this
    # point sets plan_scope_conflict or changes the attempt's mode or family.
    charge_failed_attempt(
        state.budgets, attempt_mode=state.last_attempt_mode,
        plan_scope_conflict=state.plan_scope_conflict is not None,
        failure_family_changed=failure_family_changed,
    )
    if state.api_contract_recovery and not api_contract_recovery_handed_back(state):
        # Later compiler/test failures remain diagnostic history; they cannot
        # replace the authoritative owner/signature/call-site recovery scope.
        # Once recovery has handed back (WORKFLOW-RECOVERY-HANDBACK-001), the
        # failure's own attribution routes the retry, exactly as in a run
        # that never needed recovery.
        state.last_implicated_files = sorted({
            item["owner"] for item in state.api_contract_recovery["violations"]
        })
        state.last_missing_files = None
        required = ", ".join(
            f"{item['owner']}::{item['removed_signature']}"
            for item in state.api_contract_recovery["violations"]
        )
        state.error_context = (
            "API_CONTRACT_RECOVERY remains authoritative. Restore exact baseline "
            f"signatures before addressing secondary failures: {required}.\n\n"
            + raw_error_context
        )

    # Generic across ANY compile error shape (see
    # extract_error_source_locations) - the exact broken source
    # line(s), read fresh from the worktree, keyed by file so the
    # next retry's per-file prompt shows this only to the file(s)
    # actually implicated, not broadcast to every file in a
    # full-set batch (same scoping fix as prior_error_context
    # below).
    fresh_error_source_context = _build_error_source_context(
        ctx.worktree_path,
        raw_error_context,
        set(state.all_files_written)
        | set(ctx.established_files)
        | set(state.last_implicated_files or []),
    )
    state.last_error_source_context = (
        fresh_error_source_context
        if failure_is_authoritative or previous_primary_failure is None
        else previous_error_source_context
    )

    # LSP grounding (Java only, silently skipped if jdtls isn't
    # installed or this isn't a Maven project) - deterministic,
    # real-classpath ground truth for the same implicated file(s),
    # merged directly into the existing error_source_context dict
    # so it reaches the retry prompt through the same, already-
    # scoped injection point rather than needing new plumbing.
    if not state.jdtls_unavailable and os.path.exists(os.path.join(ctx.worktree_path, "pom.xml")):
        state.jdtls_client = await _get_or_start_jdtls_client(state.jdtls_client, ctx.worktree_path)
        if state.jdtls_client is None:
            state.jdtls_unavailable = True
            if state.lsp_warning is None:
                from kriya.tools.lsp import find_jdtls as _find_jdtls_for_warning
                if _find_jdtls_for_warning():
                    state.lsp_warning = (
                        "jdtls was found on PATH but failed to start - LSP grounding was "
                        "unavailable for the rest of this run (see logs for the startup error)."
                    )
                    logger.warning(f"LSP preflight: {state.lsp_warning}")
        else:
            lsp_context = await _build_lsp_diagnostics_context(
                state.jdtls_client, ctx.worktree_path,
                state.last_implicated_files if state.last_implicated_files else state.all_files_written,
            )
            for lsp_filepath, lsp_text in lsp_context.items():
                state.last_error_source_context[lsp_filepath] = (
                    state.last_error_source_context.get(lsp_filepath, "") + lsp_text
                )

    # De-dup fallback: a QualityGateFailure-sourced failure already appended
    # its own gate_outcome at the raise site (via failure.to_gate_outcome()),
    # so this only ever fires for a source that never reaches a try-block
    # append - chiefly IncompleteGenerationError, plus the general_error
    # defensive path.
    if not any(o.get("attempt") == state.attempt_number and o.get("type") == fail_type for o in state.gate_outcomes):
        state.gate_outcomes.append(failure.to_gate_outcome())

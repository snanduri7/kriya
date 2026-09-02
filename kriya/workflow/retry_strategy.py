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
"""
import hashlib
import logging
import os
from typing import Optional

from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.attribution import AttributionResult, DETERMINISTIC_ATTRIBUTION_TIERS, _detect_missing_build_manifest, attribute_failure, read_worktree_file
from kriya.workflow.banners import log_gate_banner
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
from kriya.workflow.repair_contract import RepairContractStatus
from kriya.workflow.state import APIContractRecovery, GenerationState
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.worktree import remove_git_worktree
from kriya.workflow.retry_policy import RetryAction, decide_for_state

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


def _abandon_active_repair_contract_if_any(state: GenerationState, *, reason: str) -> None:
    """MA9 (2026-08-29 v2 design review): marks an ACTIVE RepairContract
    ABANDONED at the two unambiguous "this subtask's retry loop is stopping
    now, and it isn't because the obligation got SATISFIED" points in
    handle_attempt_failure() below. Deliberately conservative/narrow - there
    may be other paths where a subtask's retry loop ends with an obligation
    still VIOLATED that this doesn't cover (e.g. an exception propagating
    from somewhere this function never sees); an orphaned ACTIVE contract at
    run end in one of those uncovered paths is a disclosed limitation, not a
    correctness bug (the run itself still fails closed correctly regardless
    of this status label - see RepairContractStatus.ABANDONED's own
    docstring). A no-op whenever no contract is active, matching every other
    MA9 hook in this codebase."""
    if state.repair_contract is None or state.repair_contract.status != RepairContractStatus.ACTIVE:
        return
    state.repair_contract.status = RepairContractStatus.ABANDONED
    state.record_event(RunEvent(
        kind="repair_contract_abandoned", attempt=state.attempt_number, source="retry_strategy.handle_attempt_failure",
        authority=EventAuthority.ADVISORY,
        message=f"RepairContract '{state.repair_contract.id}' abandoned - retry loop stopping ({reason}).",
        details={"repair_contract_id": state.repair_contract.id, "reason": reason},
    ))


def _failure_from_validated_scope_denial(
    error: Exception, ctx,
) -> Optional[Failure]:
    """Turn an exact denied existing production target into plan evidence."""
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
    # production owner. A hallucinated new path or test target remains a
    # denied policy error and cannot mutate the approved plan.
    if not os.path.isfile(target) or is_runnable_test_file(relative):
        return None
    message = (
        "PLAN_SCOPE_DEFECT: generated repair targeted an existing production "
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
) -> bool:
    """Classify every failed attempt and bound retries without content progress."""
    normalized_files = tuple(sorted(set(files or ())))
    same_workspace = workspace_hash == state.last_failed_workspace_hash
    stage_order = {
        "operation_contract": 0, "anchored_edit": 0,
        "structural_corruption": 1, "compile": 2,
        "test": 3, "run_verification": 4, "run_verification_hung": 4,
        "goal_spec_compliance": 5, "regression_test": 6,
    }
    action_changed = action != state.last_progress_action
    if not same_workspace:
        classification = "PROGRESS"
    elif action_changed:
        classification = "NO_PROGRESS"
    elif (
        stage is not None
        and state.last_progress_stage is not None
        and stage in stage_order
        and state.last_progress_stage in stage_order
        and stage_order[stage] < stage_order[state.last_progress_stage]
    ):
        classification = "REGRESSION"
    elif (
        failure_signature == state.last_progress_failure_signature
        and stage == state.last_progress_stage
        and normalized_files == state.last_progress_files
    ):
        classification = "REPEATED_ACTION"
    else:
        classification = "NO_PROGRESS"

    if same_workspace and not action_changed:
        state.consecutive_no_progress_attempts += 1
    else:
        state.consecutive_no_progress_attempts = 0
    state.last_failed_workspace_hash = workspace_hash
    state.last_progress_failure_signature = failure_signature
    state.last_progress_stage = stage
    state.last_progress_files = normalized_files
    state.last_progress_action = action
    state.last_progress_classification = classification
    if classification == "REGRESSION":
        state.consecutive_no_progress_attempts = max(
            state.consecutive_no_progress_attempts, 2,
        )
    state.no_progress_terminated = state.consecutive_no_progress_attempts >= limit
    return not state.no_progress_terminated


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
    explicitly `break` now (True only for an environment/toolchain failure -
    genuine budget exhaustion is left to the `while` loop's own condition
    going False on its next check, matching the original inline code's exact
    control flow rather than introducing a new signal for it)."""
    raw_error_context = str(e)
    # "full_set" -> "full-set" to match this log line's original
    # wording exactly; the other three modes were already hyphen-free.
    attempt_mode = "full-set" if state.last_attempt_mode in (None, "full_set") else state.last_attempt_mode
    # Every failure source now raises with a real Failure object attached
    # (QualityGateFailure.failure directly, or IncompleteGenerationError.failure
    # for backward compat - see kriya/workflow/failure.py) instead of a bare
    # message string later re-sniffed for its type by prefix-matching.
    #
    # PRV-06 completion (2026-08-29, "MA8.1 <-> MA9 composition and
    # AttemptContext correctness"): a bare Exception reaching this final
    # fallback was ALWAYS documented as "shouldn't normally happen -
    # defensive only". A NARROW, explicitly-named subset of exception types
    # - ones that can ONLY ever indicate a bug in Kriya's OWN control-plane
    # code, never a deliberate "the model/input was bad" signal - is
    # reclassified as "internal_framework_error" instead of "general_error":
    # never fed back to the Developer as diagnosis/repair evidence, and
    # wired into the SAME environment_failure/STOP_ENVIRONMENT mechanism
    # time_budget_exhausted/verification_infrastructure_failure already use
    # below, so the run fails closed on the very first occurrence instead of
    # burning further Developer retries or letting MA8.1 open a new cross-
    # owner recovery requirement from it. Live-reproduced proof: an
    # UnboundLocalError inside run_attempt's own coordinated-repair branch.
    #
    # ValueError is deliberately EXCLUDED from that set, even though it's
    # just as "bare" here - this codebase already uses it pervasively and
    # intentionally for "the model/input was bad" signaling (e.g.
    # DeveloperAgent's own malformed-JSON-response ValueError in agent.py),
    # and reclassifying that as internal would wrongly hard-stop a perfectly
    # ordinary, retryable model failure. Confirmed live while implementing
    # this fix: an early, broader version of this change (reclassifying
    # EVERY bare exception) did exactly that regression, caught by this
    # module's own existing test_workflow.py regression sweep.
    #
    # Future hardening note (2026-08-29 design review, not a blocker for
    # this pass): this whitelist is a pragmatic fix for TODAY's evidenced
    # failure classes, not permanent architectural truth - classification
    # is properly about an exception's ORIGIN (did Kriya's own control-
    # plane code break) and SEMANTICS, not merely its Python type. A
    # TypeError could someday be a deliberate signal from a validator or
    # plugin boundary, the same way ValueError already is here for
    # DeveloperAgent's own malformed-response signaling. If a future
    # addition needs one of these four types as an intentional, non-buggy
    # signal, prefer a typed internal exception (a dedicated base class
    # control-plane code raises deliberately) or an explicit control-plane
    # boundary over continuing to grow this whitelist - see docs/design.md
    # §11.3's own "Future hardening note" for the fuller rationale.
    attached_failure = getattr(e, "failure", None)
    scope_denial_failure = attached_failure is None and _failure_from_validated_scope_denial(e, ctx)
    is_internal_framework_bug = (
        attached_failure is None
        and not scope_denial_failure
        and isinstance(e, (UnboundLocalError, TypeError, KeyError, AssertionError))
    )
    failure: Failure = (
        attached_failure
        or scope_denial_failure
        or Failure(
            type="internal_framework_error" if is_internal_framework_bug else "general_error",
            message=(
                f"INTERNAL KRIYA ERROR (not a generated-application defect): {raw_error_context}"
                if is_internal_framework_bug else raw_error_context
            ),
            raw_output=raw_error_context, source="orchestrator",
        )
    )
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
        }
        else classify_environment_failure(raw_error_context)
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

    failure_family_changed = (
        previous_failure_signature is not None
        and current_failure_signature != previous_failure_signature
    )
    if failure_family_changed:
        logger.info(
            "Quality Gates surfaced a new failure family - resetting scoped "
            "targeted/fallback budgets while preserving the global attempt bound."
        )
        state.budgets.targeted_retry_count = 0
        state.budgets.fallback_targeted_attempted = False
        state.budgets.fallback_targeted_requested = False
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
    ):
        logger.error(
            "Quality Gates stopped after %s consecutive attempts produced no "
            "effective workspace change (classification=%s).",
            no_progress_limit,
            state.last_progress_classification,
        )
    elif state.consecutive_no_progress_attempts >= 2:
        # Force the existing bounded full-set/escalation route; this changes
        # strategy, not authorization or file scope.
        state.budgets.targeted_retry_count = max(
            state.budgets.targeted_retry_count, ctx.targeted_max_retries,
        )
        if ctx.chain:
            state.budgets.fallback_targeted_requested = True

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
            attribution = AttributionResult(
                tier="deterministic",
                files=[
                    path for path in failure.likely_files
                    if is_runnable_test_file(path)
                ],
                confidence="high",
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

    if state.api_contract_recovery:
        # Later compiler/test failures remain diagnostic history; they cannot
        # replace the authoritative owner/signature/call-site recovery scope.
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

    if state.plan_scope_conflict is not None:
        # This exits to the authoritative controller immediately. It is a
        # plan transition, not an ordinary Developer retry, and must not burn
        # any full-set/targeted recovery budget.
        pass
    elif state.last_attempt_mode == "api_contract_recovery":
        state.budgets.api_contract_recovery_count += 1
    elif state.last_attempt_mode in ("targeted", "missing_files"):
        if not failure_family_changed:
            state.budgets.targeted_retry_count += 1
        # When the narrow repair resolved its original defect and exposed a
        # different validator family, the new family starts at targeted zero.
        # The attempt_number ceiling already counts the model call; do not
        # mischarge it to the unrelated full-set budget below.
    elif state.last_attempt_mode == "fallback_targeted":
        # Deliberately counts against NEITHER budget - it's a genuinely
        # separate, one-shot step (fallback_targeted_attempted, already
        # set True at the branch entry above, is what prevents this from
        # ever firing twice), not a full-set attempt or an extension of
        # the primary-model-only targeted budget.
        pass
    else:
        state.budgets.retry_count += 1

    # De-dup fallback: a QualityGateFailure-sourced failure already appended
    # its own gate_outcome at the raise site (via failure.to_gate_outcome()),
    # so this only ever fires for a source that never reaches a try-block
    # append - chiefly IncompleteGenerationError, plus the general_error
    # defensive path.
    if not any(o.get("attempt") == state.attempt_number and o.get("type") == fail_type for o in state.gate_outcomes):
        state.gate_outcomes.append(failure.to_gate_outcome())

    if state.plan_scope_conflict is not None or state.no_progress_terminated:
        _abandon_active_repair_contract_if_any(state, reason="plan_scope_conflict_or_no_progress")
        if state.plan_scope_conflict is not None:
            logger.error(
                "Quality Gates stopped early - grounded repair requires file(s) outside the "
                "validated write scope; authoritative plan revision is required (%s).",
                state.plan_scope_conflict["required_files"],
            )
        if ctx.worktree_path != ctx.workspace_path:
            for filepath in state.all_files_written:
                worktree_file = os.path.join(ctx.worktree_path, filepath)
                try:
                    with open(worktree_file, "r", encoding="utf-8", errors="replace") as fh:
                        state.final_attempt_contents[filepath] = fh.read()
                except Exception as exc:
                    logger.debug(
                        "Failed to capture final content of %r before scope-conflict cleanup: %s",
                        worktree_file, exc,
                    )
            remove_git_worktree(ctx.workspace_path, ctx.worktree_path)
        return True

    retry_decision = decide_for_state(
        state, max_retries=ctx.max_retries,
        targeted_max_retries=ctx.targeted_max_retries,
        has_fallback_model=bool(ctx.chain),
    )
    budgets_exhausted = not retry_decision.should_continue
    if budgets_exhausted:
        _abandon_active_repair_contract_if_any(state, reason=retry_decision.action.value)
        if retry_decision.action is RetryAction.STOP_ENVIRONMENT:
            logger.error(f"Quality Gates stopped early - {state.environment_failure}")
        else:
            logger.error("Quality Gates exceeded maximum debug retries (full-set and targeted). Continuing to review with errors.")
        if ctx.worktree_path != ctx.workspace_path:
            for filepath in state.all_files_written:
                worktree_file = os.path.join(ctx.worktree_path, filepath)
                try:
                    with open(worktree_file, "r", encoding="utf-8", errors="replace") as fh:
                        state.final_attempt_contents[filepath] = fh.read()
                except Exception as e:
                    logger.debug(f"Failed to capture final content of '{worktree_file}' before worktree cleanup: {e}")
            remove_git_worktree(ctx.workspace_path, ctx.worktree_path)
        # An environment/toolchain failure needs an explicit break -
        # unlike genuine budget exhaustion (which naturally coincides
        # with the `while` loop's own condition going False on its next
        # check), this can fire on the very first attempt, well before
        # retry_count reaches max_retries, and the loop would otherwise
        # continue straight into another pointless Developer retry.
        if retry_decision.action is RetryAction.STOP_ENVIRONMENT:
            return True
    return False

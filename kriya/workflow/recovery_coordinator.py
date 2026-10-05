"""Recovery after a failed Developer + Quality Gates attempt (PRD-031).

:class:`RecoveryCoordinator` sequences one failed attempt's recovery in
three steps:

1. **classify.** :func:`classify_attempt_exception` turns whatever the
   attempt raised into one typed :class:`Failure`, with the facts the
   recording step needs. It reads only the exception, the attempt mode and
   the scope-denial grounding.
2. **record.** The failure is recorded into the run's state: the failure
   ledger, the retry evidence, live lookup, LSP grounding and the budget
   charge. This is the existing machinery in kriya/workflow/retry_strategy.py,
   injected as ``record_failure``.
3. **decide.** :func:`conclude_attempt_failure` reads only the recorded
   state. It stops at a plan-scope conflict or when no progress is being
   made. Otherwise it defers to the pure retry policy
   (kriya/workflow/retry_policy.py) and finalizes a run whose budgets are
   exhausted.

The coordinator decides nothing itself: every stop/continue decision is the
retry policy's, or one of the two state flags the recording step sets. It
never imports attempt.py or retry_strategy.py; retry_strategy imports it.
``retry_strategy.handle_attempt_failure`` stays the compatibility entry
point the retry loop calls.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.policy.errors import PolicyDeniedError
from kriya.tools.containment import ContainmentSetupError
from kriya.workflow.failure import Failure
from kriya.workflow.repair_contract import RepairContractStatus
from kriya.workflow.retry_policy import RetryAction, RetryDecision, decide_for_state
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.state import GenerationState
from kriya.workflow.worktree import remove_git_worktree

logger = logging.getLogger(__name__)

# Exception types only Kriya's own control-plane code raises by accident
# (never a deliberate "the model/input was bad" signal). ValueError is
# deliberately excluded: DeveloperAgent uses it for a malformed response,
# an ordinary retryable model failure. See docs/design.md §11.3.
INTERNAL_FRAMEWORK_ERROR_TYPES = (UnboundLocalError, TypeError, KeyError, AssertionError)

# (exception, attempt context) -> the plan_scope_conflict Failure a validated
# scope denial grounds into, or a falsy value when it cannot be grounded.
ScopeDenialGrounding = Callable[[Exception, Any], Any]


@dataclass(frozen=True)
class ClassifiedAttemptFailure:
    """One failed attempt, classified. ``failure`` is what the recording step
    records; the flags are the classification facts it acts on."""

    failure: Failure
    raw_error_context: str
    # The retry mode as logged: "full-set", "targeted", "missing_files", ...
    attempt_mode: str
    # A PolicyDeniedError (FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE) that could
    # not be grounded into a plan-scope conflict, or a trusted control path
    # (TRUSTED_CONTROL_PATH_DENIED, PLAT-039); its reason code.
    unrecoverable_scope_denial: bool
    internal_framework_bug: bool
    containment_setup_failure: bool
    unrecoverable_denial_reason: Optional[str] = None


# Writer denials no retry can resolve: the stop is on the first one.
UNRECOVERABLE_WRITE_DENIALS = frozenset({"FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE", "TRUSTED_CONTROL_PATH_DENIED"})


@dataclass(frozen=True)
class RecoveryDecision:
    """``stop_loop`` = the retry loop must break now. ``action`` is the
    retry policy's decision, or None when the attempt stopped at a
    plan-scope conflict or for lack of progress (before the policy)."""

    stop_loop: bool
    action: Optional[RetryAction]
    budgets_exhausted: bool
    # LR-R1-M1: the retry policy's full decision, for the evidence record
    # only (None when the attempt stopped before the policy). Not compared:
    # the decision is the three fields above.
    retry_decision: Optional[RetryDecision] = field(default=None, compare=False)


def classify_attempt_exception(
    exc: Exception, ctx: Any, *, last_attempt_mode: Optional[str], ground_scope_denial: ScopeDenialGrounding,
) -> ClassifiedAttemptFailure:
    """Every failure source raises with a real Failure attached
    (QualityGateFailure.failure, IncompleteGenerationError.failure). Only an
    exception without one is classified here: a grounded scope denial, a
    PRD-016 budget refusal, a containment refusal, an internal framework
    error, or else a general error."""
    raw_error_context = str(exc)
    # "full_set" -> "full-set", the log wording; the other modes are hyphen-free.
    attempt_mode = "full-set" if last_attempt_mode in (None, "full_set") else last_attempt_mode
    attached_failure = getattr(exc, "failure", None)
    scope_denial_failure = attached_failure is None and ground_scope_denial(exc, ctx)
    unclassified = attached_failure is None and not scope_denial_failure
    # PRV-17: a denial that could not be grounded into a plan_scope_conflict
    # (no real existing owner to hand recovery to) is unrecoverable.
    unrecoverable_scope_denial = (
        unclassified and isinstance(exc, PolicyDeniedError)
        and exc.result.reason_code in UNRECOVERABLE_WRITE_DENIALS
    )
    internal_framework_bug = unclassified and isinstance(exc, INTERNAL_FRAMEWORK_ERROR_TYPES)
    # SEC-001: containment refused to run a command uncontained; retrying
    # cannot make a backend available.
    containment_setup_failure = unclassified and isinstance(exc, ContainmentSetupError)
    # PRD-016: a request refused before inference because the prompt plus the
    # minimum output cannot fit the served window. Typed, with its reason
    # code; a fallback model with its own window may still succeed.
    from kriya.core.token_budget import OUTPUT_BUDGET_UNSATISFIABLE, ContextBudgetUnsatisfiableError

    budget_failure = (
        Failure(
            type=(
                "output_budget_unsatisfiable"
                if getattr(exc, "reason_code", None) == OUTPUT_BUDGET_UNSATISFIABLE
                else "context_budget_unsatisfiable"
            ),
            message=str(exc), raw_output=str(exc), source="orchestrator",
            diagnostics={"reason_code": exc.reason_code, "budget": exc.decision.to_dict()},
        )
        if unclassified and isinstance(exc, ContextBudgetUnsatisfiableError)
        else None
    )
    # PROVIDER-CONTRACT-001: the provider does not apply what Kriya relies
    # on (an unapplied or unknown setting, a served window below the
    # requested one, a silently truncated prompt). Resending the same
    # request cannot change the provider, so it is a typed stop.
    from kriya.core.provider_contract import ProviderContractError

    contract_failure = (
        Failure(
            type="provider_contract", message=str(exc), raw_output=str(exc), source="orchestrator",
            diagnostics={"reason_code": exc.reason_code, **exc.details},
        )
        if unclassified and isinstance(exc, ProviderContractError)
        else None
    )
    # PROVIDER-CONTRACT-001A: the run's generation deadline stopped a model
    # call. No retry or fallback can make time, so it is the same
    # deterministic stop as the pre-generation budget check.
    from kriya.core.llm import InferenceDeadlineError

    deadline_failure = (
        Failure(
            type="time_budget_exhausted",
            message=f"GENERATION TIME BUDGET EXHAUSTED: {exc}", raw_output=str(exc), source="orchestrator",
            diagnostics={"reason_code": exc.reason_code, **exc.details},
        )
        if unclassified and isinstance(exc, InferenceDeadlineError)
        else None
    )
    failure: Failure = (
        attached_failure
        or scope_denial_failure
        or budget_failure
        or contract_failure
        or deadline_failure
        or Failure(
            type=(
                "containment_setup_failed" if containment_setup_failure
                else "internal_framework_error" if internal_framework_bug
                else "general_error"
            ),
            message=(
                f"CONTAINMENT_SETUP_FAILED: {raw_error_context}" if containment_setup_failure
                else f"INTERNAL KRIYA ERROR (not a generated-application defect): {raw_error_context}"
                if internal_framework_bug else raw_error_context
            ),
            raw_output=raw_error_context, source="orchestrator",
            # PRD-011: a typed containment refusal (e.g.
            # TOOLCHAIN_REQUIREMENT_CONFLICT) keeps its reason code.
            diagnostics=(
                {"reason_code": exc.reason_code}
                if containment_setup_failure and getattr(exc, "reason_code", None) else None
            ),
        )
    )
    return ClassifiedAttemptFailure(
        failure=failure, raw_error_context=raw_error_context, attempt_mode=attempt_mode,
        unrecoverable_scope_denial=bool(unrecoverable_scope_denial),
        internal_framework_bug=bool(internal_framework_bug),
        containment_setup_failure=bool(containment_setup_failure),
        unrecoverable_denial_reason=exc.result.reason_code if unrecoverable_scope_denial else None,
    )


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


def _capture_final_contents_and_remove_sandbox(state: GenerationState, ctx: Any, *, cleanup: str) -> None:
    """The run stops: keep the failing candidate's final contents for the
    result, then remove its sandbox (never the real workspace)."""
    if ctx.worktree_path == ctx.workspace_path:
        return
    for filepath in state.all_files_written:
        worktree_file = os.path.join(ctx.worktree_path, filepath)
        try:
            with open(worktree_file, "r", encoding="utf-8", errors="replace") as fh:
                state.final_attempt_contents[filepath] = fh.read()
        except Exception as exc:
            logger.debug("Failed to capture final content of %r before %s: %s", worktree_file, cleanup, exc)
    remove_git_worktree(ctx.workspace_path, ctx.worktree_path)


def conclude_attempt_failure(state: GenerationState, ctx: Any) -> RecoveryDecision:
    """The recorded failure's stop/continue decision."""
    if state.plan_scope_conflict is not None or state.no_progress_terminated:
        _abandon_active_repair_contract_if_any(state, reason="plan_scope_conflict_or_no_progress")
        if state.plan_scope_conflict is not None:
            logger.error(
                "Quality Gates stopped early - grounded repair requires file(s) outside the "
                "validated write scope; authoritative plan revision is required (%s).",
                state.plan_scope_conflict["required_files"],
            )
        _capture_final_contents_and_remove_sandbox(state, ctx, cleanup="scope-conflict cleanup")
        return RecoveryDecision(stop_loop=True, action=None, budgets_exhausted=False)

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
            logger.error("Quality Gates stopped - %s. Continuing to review with errors.", retry_decision.reason)
        _capture_final_contents_and_remove_sandbox(state, ctx, cleanup="worktree cleanup")
    # An environment/toolchain failure needs an explicit break: unlike genuine
    # budget exhaustion (which coincides with the retry loop's own condition
    # going False on its next check), it can fire on the very first attempt,
    # and the loop would otherwise continue into another pointless retry.
    return RecoveryDecision(
        stop_loop=budgets_exhausted and retry_decision.action is RetryAction.STOP_ENVIRONMENT,
        action=retry_decision.action, budgets_exhausted=budgets_exhausted, retry_decision=retry_decision,
    )


RecordFailure = Callable[[GenerationState, Any, Exception, ClassifiedAttemptFailure], Awaitable[None]]


class RecoveryCoordinator:
    """classify -> record (injected) -> decide, for one failed attempt."""

    def __init__(self, *, ground_scope_denial: ScopeDenialGrounding, record_failure: RecordFailure) -> None:
        self._ground_scope_denial = ground_scope_denial
        self._record_failure = record_failure

    async def handle(self, state: GenerationState, ctx: Any, exc: Exception) -> RecoveryDecision:
        classified = classify_attempt_exception(
            exc, ctx, last_attempt_mode=state.last_attempt_mode, ground_scope_denial=self._ground_scope_denial,
        )
        await self._record_failure(state, ctx, exc, classified)
        decision = conclude_attempt_failure(state, ctx)
        _record_recovery_decision(state, classified, decision)
        return decision


def _record_recovery_decision(state: GenerationState, classified: ClassifiedAttemptFailure,
                              decision: RecoveryDecision) -> None:
    """LR-R1-M1 ``recovery.decision``: the classification, the decision and
    the retry policy's reason, after the decision was made (observational)."""
    if attempt_evidence_scope.capture_mode() is None:
        return
    try:
        retry = decision.retry_decision
        conflict = state.plan_scope_conflict
        payload = {
            "attempt": state.attempt_number, "failure_type": classified.failure.type,
            "attempt_mode": classified.attempt_mode,
            "unrecoverable_scope_denial": classified.unrecoverable_scope_denial,
            "unrecoverable_denial_reason": classified.unrecoverable_denial_reason,
            "internal_framework_bug": classified.internal_framework_bug,
            "containment_setup_failure": classified.containment_setup_failure,
            "stop_loop": decision.stop_loop, "action": decision.action.value if decision.action else None,
            "budgets_exhausted": decision.budgets_exhausted,
            "retry_decision": None if retry is None else {
                "action": retry.action.value, "reason": retry.reason,
                "reserved_fallback": retry.reserved_fallback, "should_continue": retry.should_continue},
            "retry": bool(not decision.stop_loop and retry is not None and retry.should_continue),
            "consecutive_no_progress_attempts": state.consecutive_no_progress_attempts,
            "no_progress_terminated": state.no_progress_terminated,
            "plan_scope_conflict": None if conflict is None else {
                "required_files": list(conflict.get("required_files") or []),
                "reason_code": conflict.get("reason_code")},
            "environment_failure": state.environment_failure is not None,
        }
    except Exception as error:  # observational: never alters the decision
        logger.warning("Attempt evidence: recovery.decision not built (%s: %s)", type(error).__name__, error)
        return
    attempt_evidence_scope.emit("recovery.decision", payload,
                                content={"environment_failure": state.environment_failure})


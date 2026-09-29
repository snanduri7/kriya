"""Pure retry-state reduction with no filesystem, model, or network effects."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

API_CONTRACT_RECOVERY_MAX_ATTEMPTS = 3
# Consecutive attempts without material progress (PRD-026) after which the
# retry loop is forced onto the bounded full-set/fallback route.
STRATEGY_TRANSITION_AFTER_NO_PROGRESS = 2


class RetryAction(str, Enum):
    API_CONTRACT_RECOVERY = "api_contract_recovery"
    TARGETED = "targeted"
    MISSING_FILES = "missing_files"
    FALLBACK_TARGETED = "fallback_targeted"
    FULL_SET = "full_set"
    STOP_ENVIRONMENT = "stop_environment"
    STOP_EXHAUSTED = "stop_exhausted"


@dataclass(frozen=True)
class RetryDecision:
    action: RetryAction
    reason: str

    @property
    def should_continue(self) -> bool:
        return self.action not in (
            RetryAction.STOP_ENVIRONMENT, RetryAction.STOP_EXHAUSTED,
        )


def decide_retry_action(
    *,
    retry_count: int,
    max_retries: int,
    targeted_retry_count: int,
    targeted_max_retries: int,
    has_implicated_files: bool,
    has_missing_files: bool,
    has_fallback_model: bool,
    fallback_targeted_attempted: bool,
    environment_failure: Optional[str],
    fallback_targeted_requested: bool = False,
    attempt_number: Optional[int] = None,
    max_total_attempts: Optional[int] = None,
    has_api_contract_recovery: bool = False,
    api_contract_recovery_count: int = 0,
    api_contract_recovery_max_attempts: int = API_CONTRACT_RECOVERY_MAX_ATTEMPTS,
    api_contract_restored: bool = False,
) -> RetryDecision:
    if environment_failure:
        return RetryDecision(RetryAction.STOP_ENVIRONMENT, environment_failure)
    # Checked BEFORE the global ceiling below, not after (PRV-11, 2026-08-30):
    # max_total_attempts' own formula already adds api_contract_recovery_
    # max_attempts as a dedicated allowance for this family, on the
    # assumption that those attempts are always available once discovered.
    # But attempt_number is a single counter SHARED across every family
    # (full-set/targeted/fallback_targeted/api_contract_recovery) - if the
    # ordinary families' own attempts (each independently bounded by their
    # own budgets below) have already driven attempt_number up to the
    # ceiling by the time API_CONTRACT_RECOVERY is first DISCOVERED (a live
    # incident: discovered on the exact attempt that also equalled the
    # ceiling), the global check used to fire first and consume the entire
    # reserved allowance without api_contract_recovery_count ever moving
    # off zero. Safe to prioritize: once state.api_contract_recovery is
    # set it is sticky (kept until terminal_succeeded() clears it or its
    # own count below reaches api_contract_recovery_max_attempts) - this
    # cannot loop indefinitely, it only guarantees the family's own
    # separately-bounded budget is honored regardless of how much of the
    # shared counter other families spent getting here.
    # Recovery owns restoration only (WORKFLOW-RECOVERY-HANDBACK-001). Once
    # the contract was verified restored, an exhausted recovery budget hands
    # control back to the ordinary families below, under the global ceiling
    # (which already counts recovery's attempts: no budget is added). An
    # unrestored contract fails closed: no ordinary retry, no fallback.
    if has_api_contract_recovery:
        if api_contract_recovery_count < api_contract_recovery_max_attempts:
            return RetryDecision(
                RetryAction.API_CONTRACT_RECOVERY,
                "authoritative baseline API contract must be restored",
            )
        if not api_contract_restored:
            return RetryDecision(
                RetryAction.STOP_EXHAUSTED,
                "API contract recovery attempt budget exhausted before the contract was restored",
            )
    if (
        attempt_number is not None and max_total_attempts is not None
        and attempt_number >= max_total_attempts
    ):
        return RetryDecision(
            RetryAction.STOP_EXHAUSTED,
            "global attempt bound reached across all failure families",
        )
    # fallback_targeted_requested only ever disqualifies TARGETED below (an
    # authoritative locator outranks another attempt by the SAME, already-
    # rejecting model) - it does NOT jump the queue ahead of MISSING_FILES.
    # Both are real, independently-grounded repair opportunities, and
    # "required files are missing" is resolved the same way regardless of
    # whether a fallback-targeted request also happens to be pending; that
    # request still gets its one bounded attempt via the ordinary
    # FALLBACK_TARGETED check below once MISSING_FILES's own budget is
    # exhausted or doesn't apply.
    prefer_fallback_targeted = (
        fallback_targeted_requested and has_implicated_files
        and has_fallback_model and not fallback_targeted_attempted
    )
    if not prefer_fallback_targeted and has_implicated_files and targeted_retry_count < targeted_max_retries:
        return RetryDecision(RetryAction.TARGETED, "grounded implicated files remain")
    if has_missing_files and targeted_retry_count < targeted_max_retries:
        return RetryDecision(RetryAction.MISSING_FILES, "required files are missing")
    if has_implicated_files and has_fallback_model and not fallback_targeted_attempted:
        reason = (
            "authoritative target retained after primary model rejected it"
            if prefer_fallback_targeted
            else "one bounded fallback-model repair remains"
        )
        return RetryDecision(RetryAction.FALLBACK_TARGETED, reason)
    if retry_count < max_retries:
        return RetryDecision(RetryAction.FULL_SET, "full-set retry budget remains")
    return RetryDecision(RetryAction.STOP_EXHAUSTED, "all applicable retry budgets are exhausted")


def _state_inputs(state, *, max_retries: int, targeted_max_retries: int, has_fallback_model: bool) -> dict:
    """The run state's retry inputs, shared by both decision sites below so
    they can never read the state differently."""
    return dict(
        retry_count=state.budgets.retry_count,
        max_retries=max_retries,
        targeted_retry_count=state.budgets.targeted_retry_count,
        targeted_max_retries=targeted_max_retries,
        has_implicated_files=bool(state.last_implicated_files),
        has_missing_files=bool(state.last_missing_files),
        has_fallback_model=has_fallback_model,
        fallback_targeted_attempted=state.budgets.fallback_targeted_attempted,
        fallback_targeted_requested=state.budgets.fallback_targeted_requested,
        has_api_contract_recovery=bool(state.api_contract_recovery),
        api_contract_recovery_count=state.budgets.api_contract_recovery_count,
        api_contract_restored=api_contract_restored(state),
    )


def decide_for_state(state, *, max_retries: int, targeted_max_retries: int, has_fallback_model: bool) -> RetryDecision:
    """The retry loop's continue/stop decision."""
    return decide_retry_action(
        **_state_inputs(
            state, max_retries=max_retries, targeted_max_retries=targeted_max_retries,
            has_fallback_model=has_fallback_model,
        ),
        environment_failure=state.environment_failure,
        attempt_number=state.attempt_number,
        max_total_attempts=(
            max_retries + targeted_max_retries + (1 if has_fallback_model else 0)
            + API_CONTRACT_RECOVERY_MAX_ATTEMPTS
            + state.budgets.best_of_n_candidates_tried
        ),
    )


def decide_attempt_mode(state, *, max_retries: int, targeted_max_retries: int, has_fallback_model: bool) -> RetryDecision:
    """The mode of the attempt the loop already admitted. The stop conditions
    (environment failure, global ceiling) were decided by decide_for_state
    before this attempt began and must not fire again against its
    since-incremented attempt_number."""
    return decide_retry_action(
        **_state_inputs(
            state, max_retries=max_retries, targeted_max_retries=targeted_max_retries,
            has_fallback_model=has_fallback_model,
        ),
        environment_failure=None,
    )


def api_contract_restored(state) -> bool:
    recovery = state.api_contract_recovery
    return recovery is not None and recovery.contract_restored


def api_contract_recovery_handed_back(state) -> bool:
    """Recovery owns restoration only (WORKFLOW-RECOVERY-HANDBACK-001): once
    the contract was verified restored and recovery's own budget is spent,
    the ordinary families own the retry, routed by the failure's own
    attribution."""
    return api_contract_restored(state) and (
        state.budgets.api_contract_recovery_count >= API_CONTRACT_RECOVERY_MAX_ATTEMPTS
    )


# --- budget bookkeeping of one failed attempt ------------------------------
# The budget side of retry_strategy._record_attempt_failure, in the order it
# runs there. Each mutates only the run's RetryBudgets, so the state-machine
# tier (tests/state_machine/) drives these same functions.

def observe_failure_family(budgets, previous_signature, current_signature) -> bool:
    """Record the attempt's failure signature and return whether it opens a
    genuinely new failure family: different from the previous attempt's AND
    never seen before in this candidate. A return to an earlier family (A ->
    B -> A) is not new evidence, so it earns no fresh budget and is charged
    (STATE-FAILURE-FAMILY-CYCLE-001)."""
    new_family = (
        previous_signature is not None
        and current_signature != previous_signature
        and current_signature not in budgets.seen_failure_signatures
    )
    budgets.seen_failure_signatures.add(current_signature)
    return new_family


def reset_scoped_budgets_for_new_family(budgets) -> None:
    """A genuinely different failure family earns fresh scoped (targeted and
    fallback-targeted) budgets; the global attempt ceiling still bounds the
    run, and the full-set budget never resets."""
    budgets.targeted_retry_count = 0
    budgets.fallback_targeted_attempted = False
    budgets.fallback_targeted_requested = False


def force_strategy_transition(
    budgets, *, consecutive_no_progress: int, targeted_max_retries: int, has_fallback_model: bool,
) -> bool:
    """After STRATEGY_TRANSITION_AFTER_NO_PROGRESS consecutive attempts
    without material progress (and before the no-progress ceiling), close
    the primary's targeted budget and request the one fallback-targeted
    repair, so the next attempt changes strategy: the fallback when one is
    configured, else the full-set route. Changes strategy only, never
    authorization or file scope. Returns whether it fired."""
    if consecutive_no_progress < STRATEGY_TRANSITION_AFTER_NO_PROGRESS:
        return False
    budgets.targeted_retry_count = max(budgets.targeted_retry_count, targeted_max_retries)
    if has_fallback_model:
        budgets.fallback_targeted_requested = True
    return True


def charge_failed_attempt(budgets, *, attempt_mode: Optional[str], plan_scope_conflict: bool,
                          failure_family_changed: bool) -> None:
    """Charge one failed attempt to the budget of the family it ran in, and
    to no other. A plan-scope conflict exits to the authoritative controller
    (a plan transition, not a retry) and charges nothing; a targeted or
    missing-files repair that exposed a new failure family starts that
    family at zero; the one-shot fallback-targeted attempt is bounded by its
    own flag; everything else is a full-set attempt."""
    if plan_scope_conflict:
        return
    if attempt_mode == RetryAction.API_CONTRACT_RECOVERY.value:
        budgets.api_contract_recovery_count += 1
    elif attempt_mode in (RetryAction.TARGETED.value, RetryAction.MISSING_FILES.value):
        if not failure_family_changed:
            budgets.targeted_retry_count += 1
    elif attempt_mode == RetryAction.FALLBACK_TARGETED.value:
        pass
    else:
        budgets.retry_count += 1

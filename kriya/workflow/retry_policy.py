"""Pure retry-state reduction with no filesystem, model, or network effects."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


API_CONTRACT_RECOVERY_MAX_ATTEMPTS = 3


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
) -> RetryDecision:
    if environment_failure:
        return RetryDecision(RetryAction.STOP_ENVIRONMENT, environment_failure)
    if (
        attempt_number is not None and max_total_attempts is not None
        and attempt_number >= max_total_attempts
    ):
        return RetryDecision(
            RetryAction.STOP_EXHAUSTED,
            "global attempt bound reached across all failure families",
        )
    if has_api_contract_recovery:
        if api_contract_recovery_count >= api_contract_recovery_max_attempts:
            return RetryDecision(
                RetryAction.STOP_EXHAUSTED,
                "API contract recovery attempt budget exhausted",
            )
        return RetryDecision(
            RetryAction.API_CONTRACT_RECOVERY,
            "authoritative baseline API contract must be restored",
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


def decide_for_state(state, *, max_retries: int, targeted_max_retries: int, has_fallback_model: bool) -> RetryDecision:
    return decide_retry_action(
        retry_count=state.budgets.retry_count,
        max_retries=max_retries,
        targeted_retry_count=state.budgets.targeted_retry_count,
        targeted_max_retries=targeted_max_retries,
        has_implicated_files=bool(state.last_implicated_files),
        has_missing_files=bool(state.last_missing_files),
        has_fallback_model=has_fallback_model,
        fallback_targeted_attempted=state.budgets.fallback_targeted_attempted,
        environment_failure=state.environment_failure,
        fallback_targeted_requested=state.budgets.fallback_targeted_requested,
        attempt_number=state.attempt_number,
        max_total_attempts=(
            max_retries + targeted_max_retries + (1 if has_fallback_model else 0)
            + API_CONTRACT_RECOVERY_MAX_ATTEMPTS
            + state.budgets.best_of_n_candidates_tried
        ),
        has_api_contract_recovery=bool(state.api_contract_recovery),
        api_contract_recovery_count=state.budgets.api_contract_recovery_count,
    )

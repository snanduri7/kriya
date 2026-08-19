"""Pure retry-state reduction with no filesystem, model, or network effects."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RetryAction(str, Enum):
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
) -> RetryDecision:
    if environment_failure:
        return RetryDecision(RetryAction.STOP_ENVIRONMENT, environment_failure)
    if has_implicated_files and targeted_retry_count < targeted_max_retries:
        return RetryDecision(RetryAction.TARGETED, "grounded implicated files remain")
    if has_missing_files and targeted_retry_count < targeted_max_retries:
        return RetryDecision(RetryAction.MISSING_FILES, "required files are missing")
    if has_implicated_files and has_fallback_model and not fallback_targeted_attempted:
        return RetryDecision(RetryAction.FALLBACK_TARGETED, "one bounded fallback-model repair remains")
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
    )

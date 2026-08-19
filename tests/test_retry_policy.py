from kriya.workflow.operations import (
    CodeOperation, all_results_are_no_change, classify_result_operation,
    operation_for_attempt,
)
from kriya.workflow.retry_policy import RetryAction, decide_retry_action


def test_operation_contracts_distinguish_repairs_creation_and_no_change():
    assert operation_for_attempt("missing_files", has_prior_failure=True) is CodeOperation.CREATE_FULL_FILE
    assert operation_for_attempt("targeted", has_prior_failure=True) is CodeOperation.REPAIR_WITH_PATCH
    assert operation_for_attempt("full_set", has_prior_failure=True) is CodeOperation.REPAIR_WITH_FULL_FILE
    assert classify_result_operation({"content": None, "edits": []}) is CodeOperation.NO_CHANGE_ASSESSMENT
    assert all_results_are_no_change([{"content": None, "edits": None}])


def test_retry_reducer_prefers_grounded_cheap_work_before_full_set():
    decision = decide_retry_action(
        retry_count=0, max_retries=4, targeted_retry_count=0,
        targeted_max_retries=3, has_implicated_files=True,
        has_missing_files=False, has_fallback_model=True,
        fallback_targeted_attempted=False, environment_failure=None,
    )
    assert decision.action is RetryAction.TARGETED


def test_retry_reducer_stops_environment_failures_immediately():
    decision = decide_retry_action(
        retry_count=0, max_retries=4, targeted_retry_count=0,
        targeted_max_retries=3, has_implicated_files=True,
        has_missing_files=False, has_fallback_model=True,
        fallback_targeted_attempted=False,
        environment_failure="maven executable is unavailable",
    )
    assert decision.action is RetryAction.STOP_ENVIRONMENT
    assert not decision.should_continue


def test_retry_reducer_uses_one_fallback_targeted_attempt_after_primary_budget():
    decision = decide_retry_action(
        retry_count=4, max_retries=4, targeted_retry_count=3,
        targeted_max_retries=3, has_implicated_files=True,
        has_missing_files=False, has_fallback_model=True,
        fallback_targeted_attempted=False, environment_failure=None,
    )
    assert decision.action is RetryAction.FALLBACK_TARGETED

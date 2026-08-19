from kriya.workflow.operations import (
    CodeOperation, all_results_are_no_change, classify_result_operation,
    operation_for_attempt, operation_for_file, validate_operation_result,
)
from kriya.workflow.retry_policy import RetryAction, decide_retry_action


def test_operation_contracts_distinguish_repairs_creation_and_no_change():
    assert operation_for_attempt("missing_files", has_prior_failure=True) is CodeOperation.CREATE_FULL_FILE
    assert operation_for_attempt("targeted", has_prior_failure=True) is CodeOperation.REPAIR_WITH_PATCH
    assert operation_for_attempt("full_set", has_prior_failure=True) is CodeOperation.REPAIR_WITH_FULL_FILE
    assert classify_result_operation({"content": None, "edits": []}) is CodeOperation.NO_CHANGE_ASSESSMENT
    assert all_results_are_no_change([{"content": None, "edits": None}])


def test_operation_contract_classifies_content_using_target_existence():
    result = {"content": "class App {}", "edits": []}
    assert classify_result_operation(
        result, file_exists=False,
    ) is CodeOperation.CREATE_FULL_FILE
    assert classify_result_operation(
        result, file_exists=True,
    ) is CodeOperation.REPAIR_WITH_FULL_FILE
    assert operation_for_file(
        CodeOperation.CREATE_FULL_FILE, file_exists=True,
    ) is CodeOperation.REPAIR_WITH_FULL_FILE


def test_operation_contract_allows_only_explicit_safe_repair_fallbacks():
    full_result = {"content": "class App {}", "edits": []}
    actual, error = validate_operation_result(
        full_result,
        expected=CodeOperation.REPAIR_WITH_PATCH,
        file_exists=True,
    )
    assert actual is CodeOperation.REPAIR_WITH_FULL_FILE
    assert error is None

    actual, error = validate_operation_result(
        {"content": None, "edits": []},
        expected=CodeOperation.CREATE_FULL_FILE,
        file_exists=False,
    )
    assert actual is CodeOperation.NO_CHANGE_ASSESSMENT
    assert error == (
        "requested create_full_file, but the response has "
        "no_change_assessment shape"
    )


def test_operation_contract_rejects_mixed_write_shapes_and_create_overwrite():
    mixed = {
        "content": "class App {}",
        "edits": [{"search": "A", "replace": "B"}],
    }
    _, error = validate_operation_result(
        mixed,
        expected=CodeOperation.REPAIR_WITH_FULL_FILE,
        file_exists=True,
    )
    assert error == "repair_with_full_file must contain exactly one write shape"

    _, error = validate_operation_result(
        {"content": "class App {}", "edits": []},
        expected=CodeOperation.CREATE_FULL_FILE,
        file_exists=True,
    )
    assert error is not None


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

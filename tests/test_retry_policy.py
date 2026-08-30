from kriya.workflow.operations import (
    CodeOperation, all_results_are_no_change, classify_result_operation,
    operation_for_attempt, operation_for_file, validate_operation_result,
)
from kriya.workflow.retry_policy import RetryAction, decide_retry_action


def test_sticky_api_contract_recovery_outranks_ordinary_failure_routing():
    decision = decide_retry_action(
        retry_count=1, max_retries=4,
        targeted_retry_count=1, targeted_max_retries=3,
        has_implicated_files=True, has_missing_files=True,
        has_fallback_model=True, fallback_targeted_attempted=False,
        environment_failure=None, has_api_contract_recovery=True,
    )
    assert decision.action is RetryAction.API_CONTRACT_RECOVERY


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
    assert operation_for_file(
        CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=False,
    ) is CodeOperation.CREATE_FULL_FILE
    assert operation_for_file(
        CodeOperation.REPAIR_WITH_PATCH, file_exists=False,
    ) is CodeOperation.CREATE_FULL_FILE


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


def test_operation_contract_rejects_malformed_repair_protocol_before_shape_fallback():
    actual, error = validate_operation_result(
        {
            "filepath": "tests/__init__.py",
            "content": None,
            "edits": [],
            "protocol_error": "incomplete repair markers",
        },
        expected=CodeOperation.REPAIR_WITH_PATCH,
        file_exists=True,
    )

    assert actual is CodeOperation.NO_CHANGE_ASSESSMENT
    assert error == "malformed repair response: incomplete repair markers"


def test_retry_reducer_prefers_grounded_cheap_work_before_full_set():
    decision = decide_retry_action(
        retry_count=0, max_retries=4, targeted_retry_count=0,
        targeted_max_retries=3, has_implicated_files=True,
        has_missing_files=False, has_fallback_model=True,
        fallback_targeted_attempted=False, environment_failure=None,
    )
    assert decision.action is RetryAction.TARGETED


def test_retry_reducer_honors_authoritative_fallback_target_request():
    decision = decide_retry_action(
        retry_count=0, max_retries=4, targeted_retry_count=1,
        targeted_max_retries=3, has_implicated_files=True,
        has_missing_files=False, has_fallback_model=True,
        fallback_targeted_attempted=False, environment_failure=None,
        fallback_targeted_requested=True,
    )
    assert decision.action is RetryAction.FALLBACK_TARGETED


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


def test_retry_reducer_enforces_global_attempt_bound_after_per_failure_resets():
    decision = decide_retry_action(
        retry_count=1, max_retries=4, targeted_retry_count=0,
        targeted_max_retries=3, has_implicated_files=True,
        has_missing_files=False, has_fallback_model=True,
        fallback_targeted_attempted=False, environment_failure=None,
        attempt_number=8, max_total_attempts=8,
    )
    assert decision.action is RetryAction.STOP_EXHAUSTED


def test_api_contract_recovery_uses_its_own_budget_not_targeted_budget():
    decision = decide_retry_action(
        retry_count=4, max_retries=4, targeted_retry_count=99,
        targeted_max_retries=3, has_implicated_files=True,
        has_missing_files=False, has_fallback_model=False,
        fallback_targeted_attempted=False, environment_failure=None,
        has_api_contract_recovery=True, api_contract_recovery_count=2,
        api_contract_recovery_max_attempts=3,
    )
    assert decision.action is RetryAction.API_CONTRACT_RECOVERY


def test_api_contract_recovery_stops_at_its_own_bound():
    decision = decide_retry_action(
        retry_count=0, max_retries=4, targeted_retry_count=0,
        targeted_max_retries=3, has_implicated_files=True,
        has_missing_files=False, has_fallback_model=False,
        fallback_targeted_attempted=False, environment_failure=None,
        has_api_contract_recovery=True, api_contract_recovery_count=3,
        api_contract_recovery_max_attempts=3,
    )
    assert decision.action is RetryAction.STOP_EXHAUSTED
    assert "API contract recovery" in decision.reason


def test_api_contract_recovery_discovered_at_the_global_ceiling_still_gets_its_own_budget():
    """PRV-11 (2026-08-30): max_total_attempts' own formula already adds
    api_contract_recovery_max_attempts as a dedicated allowance for this
    family - but attempt_number is a single counter SHARED across every
    family, so a live run that discovered API_CONTRACT_RECOVERY on the
    exact attempt that also equalled the global ceiling used to hit
    STOP_EXHAUSTED before api_contract_recovery_count ever moved off
    zero - the family's whole reserved budget went unused. This is the
    literal shape of that incident: attempt_number == max_total_attempts,
    AND has_api_contract_recovery is newly True with its own count still
    at 0 - the family must still get its own attempt, not be preempted by
    a global ceiling its own formula already accounted for it in."""
    decision = decide_retry_action(
        retry_count=1, max_retries=4, targeted_retry_count=0,
        targeted_max_retries=3, has_implicated_files=True,
        has_missing_files=False, has_fallback_model=True,
        fallback_targeted_attempted=True, environment_failure=None,
        attempt_number=11, max_total_attempts=11,
        has_api_contract_recovery=True, api_contract_recovery_count=0,
        api_contract_recovery_max_attempts=3,
    )
    assert decision.action is RetryAction.API_CONTRACT_RECOVERY


def test_api_contract_recovery_own_bound_still_governs_past_the_global_ceiling():
    """The reorder above must not turn the global ceiling into a no-op for
    THIS family either - once api_contract_recovery_count itself reaches
    its own bound, the result is still STOP_EXHAUSTED (via the family's
    own existing check), not an infinite API_CONTRACT_RECOVERY loop, even
    though attempt_number is now further past max_total_attempts than in
    the test above."""
    decision = decide_retry_action(
        retry_count=1, max_retries=4, targeted_retry_count=0,
        targeted_max_retries=3, has_implicated_files=True,
        has_missing_files=False, has_fallback_model=True,
        fallback_targeted_attempted=True, environment_failure=None,
        attempt_number=14, max_total_attempts=11,
        has_api_contract_recovery=True, api_contract_recovery_count=3,
        api_contract_recovery_max_attempts=3,
    )
    assert decision.action is RetryAction.STOP_EXHAUSTED
    assert "API contract recovery" in decision.reason

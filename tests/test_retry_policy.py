from kriya.workflow.operations import (
    CodeOperation,
    all_results_are_no_change,
    classify_result_operation,
    operation_for_attempt,
    operation_for_file,
    validate_operation_result,
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


# --- WORKFLOW-RECOVERY-HANDBACK-001: recovery owns restoration only -------------------------
# PRD-036 rc5 matrix trial 3, C6: the primary's first candidate removed a
# public signature, recovery restored it and spent its 3 attempts on the
# primary, and the run stopped with full-set retries and the configured
# fallback untried.

def _exhausted_recovery(**overrides):
    values = dict(
        retry_count=1, max_retries=4, targeted_retry_count=0, targeted_max_retries=3,
        has_implicated_files=True, has_missing_files=False, has_fallback_model=True,
        fallback_targeted_attempted=False, environment_failure=None,
        attempt_number=4, max_total_attempts=11,
        has_api_contract_recovery=True, api_contract_recovery_count=3,
        api_contract_recovery_max_attempts=3, api_contract_restored=True,
    )
    values.update(overrides)
    return decide_retry_action(**values)


def test_a_restored_contract_hands_an_exhausted_recovery_back_to_the_ordinary_families():
    assert _exhausted_recovery().action is RetryAction.TARGETED
    assert _exhausted_recovery(targeted_retry_count=3).action is RetryAction.FALLBACK_TARGETED
    assert _exhausted_recovery(
        targeted_retry_count=3, fallback_targeted_attempted=True,
    ).action is RetryAction.FULL_SET
    assert _exhausted_recovery(has_implicated_files=False).action is RetryAction.FULL_SET


def test_an_unrestored_contract_stops_fail_closed_whatever_budget_and_fallback_remain():
    decision = _exhausted_recovery(api_contract_restored=False)
    assert decision.action is RetryAction.STOP_EXHAUSTED
    assert "before the contract was restored" in decision.reason


def test_handback_grants_no_budget_the_ordinary_families_do_not_have():
    spent = _exhausted_recovery(
        retry_count=4, targeted_retry_count=3, fallback_targeted_attempted=True,
    )
    assert spent.action is RetryAction.STOP_EXHAUSTED
    assert spent.reason == "all applicable retry budgets are exhausted"
    ceiling = _exhausted_recovery(attempt_number=11)
    assert ceiling.action is RetryAction.STOP_EXHAUSTED
    assert ceiling.reason == "global attempt bound reached across all failure families"


def test_a_restored_contract_still_spends_the_recovery_budget_first():
    assert _exhausted_recovery(api_contract_recovery_count=2).action is RetryAction.API_CONTRACT_RECOVERY


def test_contract_restored_follows_the_verified_phase():
    from kriya.workflow.state import APIContractRecovery, APIContractRecoveryPhase

    recovery = APIContractRecovery.detected([{"owner": "calc.py"}], [], {})
    assert not recovery.contract_restored
    recovery.begin_restoration()
    assert recovery.phase is APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT
    assert not recovery.contract_restored
    recovery.owner_contract_restored()
    assert recovery.contract_restored
    recovery.candidate_gates_passed()
    assert recovery.contract_restored
    recovery.terminal_succeeded()
    assert recovery.contract_restored


def test_decide_for_state_reads_the_restored_phase_from_the_run_state():
    from kriya.workflow.retry_policy import decide_for_state
    from kriya.workflow.state import APIContractRecovery, GenerationState

    state = GenerationState()
    state.attempt_number = 4
    state.last_implicated_files = ["calc.py"]
    state.budgets.retry_count = 1
    state.budgets.api_contract_recovery_count = 3
    state.api_contract_recovery = APIContractRecovery.detected([{"owner": "calc.py"}], [], {})
    state.api_contract_recovery.begin_restoration()
    args = dict(max_retries=4, targeted_max_retries=3, has_fallback_model=True)

    assert decide_for_state(state, **args).action is RetryAction.STOP_EXHAUSTED
    state.api_contract_recovery.owner_contract_restored()
    assert decide_for_state(state, **args).action is RetryAction.TARGETED


def _recovery_state(*, restored, spent):
    from kriya.workflow.state import APIContractRecovery, GenerationState

    state = GenerationState()
    state.budgets.api_contract_recovery_count = spent
    state.api_contract_recovery = APIContractRecovery.detected([{"owner": "calc.py"}], [], {})
    state.api_contract_recovery.begin_restoration()
    if restored:
        state.api_contract_recovery.owner_contract_restored()
    return state


def test_recovery_hands_back_only_when_restored_and_its_budget_is_spent():
    from kriya.workflow.retry_policy import api_contract_recovery_handed_back
    from kriya.workflow.state import GenerationState

    assert not api_contract_recovery_handed_back(GenerationState())
    assert not api_contract_recovery_handed_back(_recovery_state(restored=False, spent=3))
    assert not api_contract_recovery_handed_back(_recovery_state(restored=True, spent=2))
    assert api_contract_recovery_handed_back(_recovery_state(restored=True, spent=3))


def test_the_attempt_mode_and_the_loop_decision_read_the_same_handback():
    """run_attempt's mode selection and the loop's continue decision share
    one input builder: after a handback both route to the ordinary family,
    and the mode never re-applies the loop's global ceiling."""
    from kriya.workflow.retry_policy import decide_attempt_mode, decide_for_state

    state = _recovery_state(restored=True, spent=3)
    state.last_implicated_files = ["calc.py"]
    state.budgets.retry_count = 1
    args = dict(max_retries=4, targeted_max_retries=3, has_fallback_model=True)
    state.attempt_number = 4
    assert decide_for_state(state, **args).action is RetryAction.TARGETED
    state.attempt_number = 5  # run_attempt incremented it for the admitted attempt
    assert decide_attempt_mode(state, **args).action is RetryAction.TARGETED
    # The last slot below the ceiling (11) is the fallback's reserved allowance
    # (STATE-RESERVED-FALLBACK-001): the loop admits it at 10 and the mode,
    # at the incremented 11, is that same decision, never a ceiling stop.
    state.attempt_number = 10
    loop = decide_for_state(state, **args)
    state.attempt_number = 11
    mode = decide_attempt_mode(state, **args)
    assert (loop.action, loop.reserved_fallback) == (mode.action, mode.reserved_fallback) == (
        RetryAction.FALLBACK_TARGETED, True)
    unrestored = _recovery_state(restored=False, spent=3)
    assert decide_attempt_mode(unrestored, **args).action is RetryAction.STOP_EXHAUSTED

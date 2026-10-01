"""The retry decision, exhaustively: every decision-relevant input combination
of retry_policy.decide_retry_action (41,472 of them, pure, under a second) checked
against the declared transition rules, plus the budget bookkeeping functions.

Transition rules (the reference model; each is one assertion below):

R1  an environment failure stops at once.
R2  active API contract recovery with its own budget left runs recovery,
    whatever else is pending (authority first; outranks the global ceiling).
R3  recovery whose budget is spent on an UNRESTORED contract stops: no
    ordinary retry, no fallback (fail closed).
R4  recovery whose budget is spent on a RESTORED contract hands back: the
    decision equals the one for the same state with no recovery at all
    (subsystem exhaustion never changes the global transition).
R5  otherwise the global attempt ceiling stops.
R6  a stop below the ceiling happens only when no family remains: targeted
    and missing-files spent or ungrounded, the one fallback-targeted repair
    used, unavailable or ungrounded, and the full-set budget spent.
R7  each family runs only within its own budget and grounding.
R8  targeted outranks missing-files, which outranks fallback-targeted,
    which outranks full-set; a pending fallback-targeted request (an
    authoritative locator the primary rejected, or the forced no-progress
    transition) jumps only the primary's targeted repair.
R9  the attempt-mode decision equals the loop decision whenever the loop
    continues (one decision, two call sites).
R10 the fallback's allowance in the ceiling belongs to the fallback
    (STATE-RESERVED-FALLBACK-001): once the remaining capacity is no more
    than its unused allowance, the attempt is the fallback's -
    fallback-targeted when grounded, else the escalated full-set (from
    retry_count 1, which every recorded failure reaches) - and only
    after the recovery rules; with the allowance used, or no fallback, or
    capacity above it, the ordinary rules decide.
"""
import itertools
from types import SimpleNamespace

import pytest

from kriya.workflow.retry_policy import (
    API_CONTRACT_RECOVERY_MAX_ATTEMPTS,
    FALLBACK_ALLOWANCE,
    STRATEGY_TRANSITION_AFTER_NO_PROGRESS,
    RetryAction,
    charge_failed_attempt,
    decide_attempt_mode,
    decide_for_state,
    decide_retry_action,
    force_strategy_transition,
    observe_failure_family,
    reset_scoped_budgets_for_new_family,
)
from kriya.workflow.state import RetryBudgets

pytestmark = pytest.mark.state_machine

M, T = 4, 3  # max_retries with a one-model chain (max(4, 2)), TARGETED_MAX_RETRIES
CEILING = 11
STOPS = (RetryAction.STOP_ENVIRONMENT, RetryAction.STOP_EXHAUSTED)


def _domain():
    """Every combination that can change a decision: each counter at 0, one
    below and at its bound, the ceiling below/at, both recovery outcomes."""
    for (retry, targeted, implicated, missing, has_fallback, fb_attempted, fb_requested, env,
         recovery, recovery_count, restored, attempt_number, fallback_used) in itertools.product(
            (0, M - 1, M), (0, T - 1, T), (False, True), (False, True), (False, True),
            (False, True), (False, True), (False, True), (False, True),
            range(API_CONTRACT_RECOVERY_MAX_ATTEMPTS + 1), (False, True),
            (CEILING - 6, CEILING - FALLBACK_ALLOWANCE - 1, CEILING - FALLBACK_ALLOWANCE, CEILING),
            (0, FALLBACK_ALLOWANCE)):
        if not recovery and (recovery_count or restored):
            continue
        yield dict(
            retry_count=retry, max_retries=M, targeted_retry_count=targeted, targeted_max_retries=T,
            has_implicated_files=implicated, has_missing_files=missing, has_fallback_model=has_fallback,
            fallback_targeted_attempted=fb_attempted, fallback_targeted_requested=fb_requested,
            environment_failure="env" if env else None,
            attempt_number=attempt_number, max_total_attempts=CEILING,
            has_api_contract_recovery=recovery, api_contract_recovery_count=recovery_count,
            api_contract_restored=restored, fallback_attempts_used=fallback_used,
        )


def _no_family_remains(s) -> bool:
    return (
        (not s["has_implicated_files"] or s["targeted_retry_count"] >= T)
        and (not s["has_missing_files"] or s["targeted_retry_count"] >= T)
        and (not s["has_implicated_files"] or not s["has_fallback_model"] or s["fallback_targeted_attempted"])
        and s["retry_count"] >= M
    )


def test_every_decision_follows_the_transition_rules():
    checked = 0
    for s in _domain():
        action = decide_retry_action(**s).action
        checked += 1
        recovery_budget_left = (
            s["has_api_contract_recovery"] and s["api_contract_recovery_count"] < API_CONTRACT_RECOVERY_MAX_ATTEMPTS
        )
        if s["environment_failure"]:
            assert action is RetryAction.STOP_ENVIRONMENT, s  # R1
            continue
        assert action is not RetryAction.STOP_ENVIRONMENT, s
        if recovery_budget_left:
            assert action is RetryAction.API_CONTRACT_RECOVERY, s  # R2
            continue
        assert action is not RetryAction.API_CONTRACT_RECOVERY, s
        if s["has_api_contract_recovery"] and not s["api_contract_restored"]:
            assert action is RetryAction.STOP_EXHAUSTED, s  # R3
            continue
        if s["has_api_contract_recovery"]:
            without = dict(s, has_api_contract_recovery=False, api_contract_recovery_count=0,
                           api_contract_restored=False)
            assert action is decide_retry_action(**without).action, s  # R4
        if s["attempt_number"] >= s["max_total_attempts"]:
            assert action is RetryAction.STOP_EXHAUSTED, s  # R5
            continue
        decision = decide_retry_action(**s)
        reserved = (
            s["has_fallback_model"] and s["fallback_attempts_used"] < FALLBACK_ALLOWANCE
            and s["max_total_attempts"] - s["attempt_number"] <= FALLBACK_ALLOWANCE - s["fallback_attempts_used"]
        )
        if reserved and s["has_implicated_files"] and not s["fallback_targeted_attempted"]:
            assert action is RetryAction.FALLBACK_TARGETED and decision.reserved_fallback, s  # R10
            continue
        if reserved and 1 <= s["retry_count"] < M:
            assert action is RetryAction.FULL_SET and decision.reserved_fallback, s  # R10
            continue
        assert not decision.reserved_fallback, s
        if action is RetryAction.STOP_EXHAUSTED:
            assert _no_family_remains(s), s  # R6
        if action is RetryAction.TARGETED:  # R7, R8
            assert s["has_implicated_files"] and s["targeted_retry_count"] < T, s
            assert not (s["fallback_targeted_requested"] and s["has_fallback_model"]
                        and not s["fallback_targeted_attempted"]), s
        if action is RetryAction.MISSING_FILES:
            assert s["has_missing_files"] and s["targeted_retry_count"] < T, s
            assert not (s["has_implicated_files"] and s["targeted_retry_count"] < T
                        and not s["fallback_targeted_requested"]), s
        if action is RetryAction.FALLBACK_TARGETED:
            assert s["has_implicated_files"] and s["has_fallback_model"] and not s["fallback_targeted_attempted"], s
            assert s["fallback_targeted_requested"] or s["targeted_retry_count"] >= T, s
            assert not (s["has_missing_files"] and s["targeted_retry_count"] < T), s
        if action is RetryAction.FULL_SET:
            assert s["retry_count"] < M, s
            assert not (s["has_implicated_files"] and s["targeted_retry_count"] < T), s
            assert not (s["has_missing_files"] and s["targeted_retry_count"] < T), s
            assert not (s["has_implicated_files"] and s["has_fallback_model"]
                        and not s["fallback_targeted_attempted"]), s
    assert checked == 41_472  # the whole domain was enumerated


def _state(s):
    budgets = RetryBudgets(
        retry_count=s["retry_count"], targeted_retry_count=s["targeted_retry_count"],
        api_contract_recovery_count=s["api_contract_recovery_count"],
        fallback_targeted_attempted=s["fallback_targeted_attempted"],
        fallback_targeted_requested=s["fallback_targeted_requested"],
        fallback_attempts_used=s["fallback_attempts_used"],
    )
    recovery = SimpleNamespace(contract_restored=s["api_contract_restored"]) if s["has_api_contract_recovery"] else None
    return SimpleNamespace(
        budgets=budgets, last_implicated_files=["a.py"] if s["has_implicated_files"] else None,
        last_missing_files=["b.py"] if s["has_missing_files"] else None, api_contract_recovery=recovery,
        environment_failure=s["environment_failure"], attempt_number=s["attempt_number"],
    )


def test_the_attempt_mode_is_the_loop_decision_whenever_the_loop_continues():
    """R9, from run state, over the same domain (the ceiling below: the loop
    admitted the attempt)."""
    for s in _domain():
        if s["attempt_number"] >= s["max_total_attempts"] or s["environment_failure"]:
            continue
        state = _state(s)
        state.attempt_number = s["attempt_number"]
        policy = dict(max_retries=M, targeted_max_retries=T, has_fallback_model=s["has_fallback_model"])
        loop = decide_for_state(state, **policy)
        if loop.should_continue:
            state.attempt_number += 1  # run_attempt increments before deciding its mode
            mode = decide_attempt_mode(state, **policy)
            assert (mode.action, mode.reserved_fallback) == (loop.action, loop.reserved_fallback), s


# --- bookkeeping -----------------------------------------------------------------------

MODES = ("api_contract_recovery", "targeted", "missing_files", "fallback_targeted", "full_set", None)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("plan_scope_conflict", (False, True))
@pytest.mark.parametrize("family_changed", (False, True))
def test_a_failed_attempt_is_charged_to_its_own_family_only(mode, plan_scope_conflict, family_changed):
    budgets = RetryBudgets(retry_count=1, targeted_retry_count=1, api_contract_recovery_count=1)
    charge_failed_attempt(budgets, attempt_mode=mode, plan_scope_conflict=plan_scope_conflict,
                          failure_family_changed=family_changed)
    delta = (budgets.retry_count - 1, budgets.targeted_retry_count - 1, budgets.api_contract_recovery_count - 1)
    expected = {
        "api_contract_recovery": (0, 0, 1),
        "targeted": (0, 0 if family_changed else 1, 0),
        "missing_files": (0, 0 if family_changed else 1, 0),
        "fallback_targeted": (0, 0, 0),
        "full_set": (1, 0, 0),
        None: (1, 0, 0),  # an attempt with no recorded mode is the initial full-set attempt
    }[mode]
    assert delta == ((0, 0, 0) if plan_scope_conflict else expected)
    assert budgets.fallback_targeted_attempted is False and budgets.fallback_targeted_requested is False


def test_a_new_family_resets_only_the_scoped_budgets():
    budgets = RetryBudgets(retry_count=3, targeted_retry_count=3, api_contract_recovery_count=2,
                           fallback_targeted_attempted=True, fallback_targeted_requested=True)
    reset_scoped_budgets_for_new_family(budgets)
    assert (budgets.targeted_retry_count, budgets.fallback_targeted_attempted,
            budgets.fallback_targeted_requested) == (0, False, False)
    assert (budgets.retry_count, budgets.api_contract_recovery_count) == (3, 2)


def test_the_forced_transition_closes_targeted_and_requests_the_fallback_only_at_its_threshold():
    for consecutive in range(STRATEGY_TRANSITION_AFTER_NO_PROGRESS + 2):
        for has_fallback in (False, True):
            budgets = RetryBudgets(targeted_retry_count=1)
            fired = force_strategy_transition(budgets, consecutive_no_progress=consecutive,
                                              targeted_max_retries=T, has_fallback_model=has_fallback)
            due = consecutive >= STRATEGY_TRANSITION_AFTER_NO_PROGRESS
            assert fired is due
            assert budgets.targeted_retry_count == (T if due else 1)
            assert budgets.fallback_targeted_requested is (due and has_fallback)
            assert (budgets.retry_count, budgets.fallback_targeted_attempted) == (0, False)


def test_only_a_never_seen_failure_family_is_new():
    budgets = RetryBudgets()
    a, b, c = ("test", ("a",)), ("test", ("b",)), ("test", ("c",))
    observed = [observe_failure_family(budgets, previous, current)
                for previous, current in ((None, a), (a, a), (a, b), (b, a), (a, b), (b, c), (c, c))]
    # first failure: no previous; a repeat; B new; back to A; back to B; C new; a repeat
    assert observed == [False, False, True, False, False, True, False]
    assert budgets.seen_failure_signatures == {a, b, c}

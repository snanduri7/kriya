"""One iteration of the Developer loop as a typed lifecycle (full modularization W4, E5;
``kriya/workflow/attempt_lifecycle.py``).

- the plan: every loop-head plan IS ``retry_policy.decide_for_state`` (the only continue/stop authority), typed as the
  mode the admitted attempt runs in; the transitions full-set -> targeted -> fallback-targeted -> full-set -> stop on a
  real ``GenerationState``, and verification-only; the runner (Best-of-N on a sandboxed first attempt only);
- the plan against what the attempt itself recorded: on real scripted runs (with and without a fallback chain) the
  plan's mode of every admitted attempt equals the ``attempt.started`` mode ``run_attempt`` decided
  (``decide_attempt_mode``), and a verification-only unit's plans are VERIFICATION_ONLY;
- the iteration: resets before the attempt, the evidence scope closed on every path, each failure route's outcome.

The exhaustive equality over the whole decision domain is R11 in ``tests/state_machine/test_sm_retry_decisions.py``.
In the state-machine tier (tests/conftest.py STATE_MACHINE_TIER_FILES) and the kernel tier.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kriya.workflow import attempt_lifecycle
from kriya.workflow.attempt_lifecycle import (
    AttemptMode,
    AttemptRunner,
    IterationEnd,
    plan_attempt,
    run_iteration,
)
from kriya.workflow.retry_policy import RetryAction, decide_for_state
from kriya.workflow.state import GenerationState

sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

M, T = 4, 3   # max_retries with a one-model chain (max(4, 2)), TARGETED_MAX_RETRIES


def _plan(state, *, fallback=True, verification_only=False, best_of_n=1, sandboxed=True):
    return plan_attempt(state, max_retries=M, targeted_max_retries=T, has_fallback_model=fallback,
                        verification_only=verification_only, best_of_n=best_of_n, sandboxed=sandboxed)


def _decision(state, *, fallback=True):
    return decide_for_state(state, max_retries=M, targeted_max_retries=T, has_fallback_model=fallback)


def _failed(state, *, implicated=None):
    """One failed attempt as the loop leaves the state (the attempt counted, the full-set budget charged)."""
    state.attempt_number += 1
    state.last_implicated_files = implicated


# ----------------------------------------------------------------------------- the plan


def test_the_mode_transitions_each_equal_the_loop_decision():
    """full-set -> targeted (x3) -> fallback-targeted -> full-set -> ... -> stop on one GenerationState, each step's
    plan equal to ``decide_for_state`` on the same state."""
    state = GenerationState()
    seen = []

    def step(**kwargs):
        plan = _plan(state, **kwargs)
        decision = _decision(state, fallback=kwargs.get("fallback", True))
        assert plan.decision == decision and plan.should_continue is decision.should_continue
        seen.append(plan.mode)
        return plan

    assert step().mode is AttemptMode.FULL_SET                     # the first attempt
    _failed(state, implicated=["calc.py"])
    state.budgets.retry_count = 1
    for _ in range(T):
        assert step().mode is AttemptMode.TARGETED                 # a grounded failure: targeted, within its budget
        _failed(state, implicated=["calc.py"])
        state.budgets.targeted_retry_count += 1
    assert step().mode is AttemptMode.FALLBACK_TARGETED            # targeted spent, a fallback model configured
    _failed(state, implicated=["calc.py"])
    state.budgets.fallback_targeted_attempted = True
    assert step().mode is AttemptMode.FULL_SET                     # the one fallback repair used: full set again
    state.budgets.retry_count = M
    assert step().mode is None and not seen[-1]                    # every budget spent: the loop stops
    assert _plan(state).decision.action is RetryAction.STOP_EXHAUSTED


def test_without_a_fallback_model_the_spent_targeted_budget_goes_to_full_set():
    state = GenerationState()
    _failed(state, implicated=["calc.py"])
    state.budgets.retry_count, state.budgets.targeted_retry_count = 1, T
    plan = _plan(state, fallback=False)
    assert plan.mode is AttemptMode.FULL_SET and plan.decision == _decision(state, fallback=False)


def test_an_environment_failure_stops_whatever_the_unit():
    state = GenerationState()
    state.environment_failure = "containment_setup_failed"
    for verification_only in (False, True):
        plan = _plan(state, verification_only=verification_only)
        assert not plan.should_continue and plan.mode is None
        assert plan.decision.action is RetryAction.STOP_ENVIRONMENT


def test_a_verification_only_unit_runs_verification_only_but_stops_on_the_loop_decision():
    state = GenerationState()
    plan = _plan(state, verification_only=True)
    assert plan.mode is AttemptMode.VERIFICATION_ONLY and plan.decision == _decision(state)
    state.budgets.retry_count, state.attempt_number = M, 1   # nothing left for any family
    stopped = _plan(state, verification_only=True)
    assert not stopped.should_continue and stopped.mode is None and stopped.decision == _decision(state)


@pytest.mark.parametrize(("attempt_number", "best_of_n", "sandboxed", "runner"), [
    (0, 3, True, AttemptRunner.BEST_OF_N),
    (0, 1, True, AttemptRunner.SINGLE),        # Best-of-N not configured
    (0, 3, False, AttemptRunner.SINGLE),       # no sandbox: a discarded candidate would stay on the project
    (1, 3, True, AttemptRunner.SINGLE),        # only the run's first attempt
])
def test_the_runner_is_best_of_n_only_on_a_sandboxed_first_attempt(attempt_number, best_of_n, sandboxed, runner):
    state = GenerationState()
    state.attempt_number = attempt_number
    plan = _plan(state, best_of_n=best_of_n, sandboxed=sandboxed)
    assert plan.runner is runner and plan.best_of_n == best_of_n


def test_the_reserved_fallback_is_the_decisions():
    state = GenerationState()
    state.attempt_number = M + T + 1 + 3 - 1   # the ceiling (max+targeted+fallback+recovery) minus the fallback's allowance
    state.budgets.retry_count = 1
    state.last_implicated_files = ["calc.py"]
    plan = _plan(state)
    assert plan.reserved_fallback is True and plan.mode is AttemptMode.FALLBACK_TARGETED
    assert plan.decision == _decision(state)


# ----------------------------------------------------------------------------- the plan against the attempt's own mode


@pytest.fixture
def _git_identity(monkeypatch):
    for name, value in kg.GIT_ENV.items():
        monkeypatch.setenv(name, value)


def _plans_and_attempt_modes(run):
    """Every loop-head plan (spied on development_loop's binding) and every attempt.started mode run_attempt recorded."""
    from kriya.workflow import development_loop

    plans = []

    def spy(*args, **kwargs):
        plans.append(attempt_lifecycle.plan_attempt(*args, **kwargs))
        return plans[-1]

    with patch.object(development_loop, "plan_attempt", side_effect=spy):
        outcome = run()
    legacy = outcome.result.legacy_result if hasattr(outcome.result, "legacy_result") else outcome.result
    return plans, legacy, outcome


@pytest.mark.parametrize(("scenario", "modes"), [
    # MEASURED. With a chain: oscillating primary failures (each new failure family earns a fresh targeted budget),
    # then the fallback rescues (the fallback-targeted transition)
    ("legacy_fallback_success", ["full_set", "targeted", "targeted", "targeted", "targeted", "fallback_targeted"]),
    # no chain: the same failures through targeted, then full set until the budgets end the loop (its last plan stops)
    ("legacy_gates_exhausted", ["full_set", "targeted", "targeted", "targeted", "targeted", "full_set", "full_set",
                                "full_set"]),
])
def test_each_admitted_plan_is_the_mode_the_attempt_recorded(scenario, modes, tmp_path, _git_identity):
    import json
    import sqlite3

    from kriya.core.state_paths import trace_db_path

    plans, legacy, outcome = _plans_and_attempt_modes(lambda: kg.SCENARIOS[scenario](tmp_path))
    with sqlite3.connect(trace_db_path(outcome.cfg)) as db:
        events = json.loads(db.execute("SELECT run_events FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()[0])
    recorded = [event["details"]["mode"] for event in events if event["kind"] == "attempt.started"]
    admitted = [plan.mode.value for plan in plans if plan.should_continue]
    assert recorded == modes and admitted == recorded
    assert not plans[-1].should_continue or legacy.get("quality_gates_passed") is True


def test_a_verification_only_unit_is_planned_verification_only(tmp_path, _git_identity):
    """The enforce run's verification unit (DENY_ALL, a declared test verifier): run_attempt never opens a retry-family
    attempt for it (no attempt.started), and every plan of that unit is VERIFICATION_ONLY."""
    plans, _legacy, _outcome = _plans_and_attempt_modes(
        lambda: kg.SCENARIOS["enforce_success_with_verification_unit"](tmp_path))
    modes = [plan.mode for plan in plans if plan.should_continue]
    assert AttemptMode.VERIFICATION_ONLY in modes and modes[0] is not AttemptMode.VERIFICATION_ONLY


# ----------------------------------------------------------------------------- the iteration


class _Ctx(SimpleNamespace):
    pass


class _Advanced(Exception):
    source, target = "RESTORE", "VERIFY"


class _Recorded(Exception):
    def __init__(self, stop_loop):
        super().__init__("recorded")
        self.stop_loop = stop_loop


def _iterate(*, attempt=None, accept=None, handled=False, runner=AttemptRunner.SINGLE):
    state = GenerationState()
    state.pre_approval_review, state.verified_candidate_binding = "stale review", "stale binding"
    ctx = _Ctx(deferred_executing_gates=object(), post_source_review_guard=object())
    calls = []

    async def run_attempt(state_, ctx_):
        calls.append(("run_attempt", state_.pre_approval_review, ctx_.deferred_executing_gates))
        if attempt is not None:
            raise attempt

    async def best_of_n(state_, ctx_, n):
        calls.append(("best_of_n", n))

    async def handle(state_, ctx_, error):
        calls.append(("handled", type(error).__name__))
        return handled

    async def accepted():
        calls.append(("accept",))
        return accept

    plan = attempt_lifecycle.AttemptPlan(decision=decide_for_state(state, max_retries=M, targeted_max_retries=T,
                                                                   has_fallback_model=False),
                                         mode=AttemptMode.FULL_SET, runner=runner, best_of_n=3)
    with patch.object(attempt_lifecycle.attempt_evidence_scope, "exit_attempt_iteration") as closed:
        try:
            outcome = asyncio.run(run_iteration(
                plan, state, ctx, run_attempt=run_attempt, load_best_of_n=lambda: best_of_n, accept=accepted,
                handle_attempt_failure=handle, phase_advanced=_Advanced, best_of_n_recorded=_Recorded,
                logger=SimpleNamespace(info=lambda *a: calls.append(("logged",)))))
        finally:
            assert closed.call_count == 1   # the iteration's evidence scope is closed on every path
    return outcome, calls, state


def test_the_resets_happen_before_the_attempt():
    outcome, calls, state = _iterate()
    assert calls[0] == ("run_attempt", None, None)
    assert state.verified_candidate_binding is None and outcome.end is IterationEnd.APPLIED


def test_the_first_sandboxed_attempt_runs_best_of_n():
    _outcome, calls, _state = _iterate(runner=AttemptRunner.BEST_OF_N)
    assert calls[0] == ("best_of_n", 3)


def test_an_aborting_acceptance_is_the_runs_result():
    outcome, _calls, _state = _iterate(accept={"status": "human_rejected"})
    assert (outcome.end, outcome.result) == (IterationEnd.RESULT, {"status": "human_rejected"})


def test_a_recovery_phase_advance_is_progress_not_a_failure():
    outcome, calls, state = _iterate(attempt=_Advanced("restored"))
    assert outcome.end is IterationEnd.RETRY and ("handled", "_Advanced") not in calls
    assert state.budgets.api_contract_recovery_count == 1
    [event] = [e for e in state.run_events if e.kind == "api_contract_recovery.phase_advanced"]
    assert event.details == {"source_phase": "RESTORE", "target_phase": "VERIFY", "passed": True}


@pytest.mark.parametrize(("stop_loop", "end"), [(True, IterationEnd.STOP), (False, IterationEnd.RETRY)])
def test_a_failure_best_of_n_recorded_is_never_handled_again(stop_loop, end):
    outcome, calls, _state = _iterate(attempt=_Recorded(stop_loop))
    assert outcome.end is end and not any(call[0] == "handled" for call in calls)


@pytest.mark.parametrize(("handled", "end"), [(True, IterationEnd.STOP), (False, IterationEnd.RETRY)])
def test_any_other_failure_is_handed_to_the_failure_handling(handled, end):
    outcome, calls, _state = _iterate(attempt=ValueError("gate failed"), handled=handled)
    assert outcome.end is end and ("handled", "ValueError") in calls and ("accept",) not in calls


class _Interrupted(BaseException):
    """Not an Exception (as a cancellation or an interrupt): never routed as a failed attempt."""


def test_an_interruption_propagates_after_the_scope_is_closed():
    with pytest.raises(_Interrupted):
        _iterate(attempt=_Interrupted())

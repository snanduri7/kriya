"""One iteration of the Developer + Quality Gates loop as a typed lifecycle (full modularization W4, E5).

The loop (``kriya/workflow/development_loop.py::run_attempt_loop``) asks ``plan_attempt`` at its head for an
``AttemptPlan``: the loop's own retry decision (``retry_policy.decide_for_state``, the ONLY continue/stop authority,
called exactly once per iteration as before), typed as the mode the admitted attempt runs in, the runner (Best-of-N on
a sandboxed first attempt, else one ``run_attempt``) and the reserved-fallback flag. ``run_iteration`` then runs that
plan through the iteration's lifecycle, moved verbatim from the loop body: the per-candidate resets, the LR-R1-M1
attempt-evidence iteration scope, the attempt, the candidate's acceptance, and the routing of a failed attempt (a
verified API-contract recovery transition, a failure Best-of-N already recorded, any other failure to
``retry_strategy.handle_attempt_failure``); it reports how the iteration ended (``IterationEnd``).

The attempt itself still decides its mode inside ``run_attempt`` (``retry_policy.decide_attempt_mode``, attempt.py,
unchanged): the plan's mode equals it whenever the loop continues (``tests/test_attempt_lifecycle.py`` cross-checks
both on real runs; ``tests/state_machine/test_sm_retry_decisions.py`` R9/R11 over the whole decision domain). A
verification-only unit (a DENY_ALL write scope with a directly executable verifier) is not a retry family: its plan's
mode is VERIFICATION_ONLY whatever family the decision names, and only its continue/stop is the decision's.

Collaborators from the kernel's all-import cycle (``run_attempt``, Best-of-N, ``handle_attempt_failure``, the two
exception types) are passed in by the loop from its ``StageDeps``; this module never imports ``kriya.workflow.workflow``,
``attempt``, ``workflow_controller`` or ``kriya.cli``.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Type

from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.workflow.retry_policy import RetryAction, RetryDecision, decide_for_state
from kriya.workflow.run_events import EventAuthority, RunEvent

RunState = Any   # kriya.workflow.state.GenerationState (kriya/workflow/stage_context.py says why it is not imported)


class AttemptMode(str, enum.Enum):
    """The family an admitted attempt runs in (``state.last_attempt_mode``'s vocabulary, plus VERIFICATION_ONLY)."""

    API_CONTRACT_RECOVERY = "api_contract_recovery"
    TARGETED = "targeted"
    MISSING_FILES = "missing_files"
    FALLBACK_TARGETED = "fallback_targeted"
    FULL_SET = "full_set"
    VERIFICATION_ONLY = "verification_only"


class AttemptRunner(str, enum.Enum):
    SINGLE = "single"          # one run_attempt
    BEST_OF_N = "best_of_n"    # best_of_n.run_attempt_with_best_of_n (the run's sandboxed first attempt only)


# Every continuing RetryAction is the family of the same name (run_attempt maps them identically).
_MODE_OF_ACTION = {
    RetryAction.API_CONTRACT_RECOVERY: AttemptMode.API_CONTRACT_RECOVERY,
    RetryAction.TARGETED: AttemptMode.TARGETED,
    RetryAction.MISSING_FILES: AttemptMode.MISSING_FILES,
    RetryAction.FALLBACK_TARGETED: AttemptMode.FALLBACK_TARGETED,
    RetryAction.FULL_SET: AttemptMode.FULL_SET,
}


@dataclass(frozen=True)
class AttemptPlan:
    """The loop head's decision for the next iteration."""

    decision: RetryDecision            # retry_policy.decide_for_state at the loop head (the continue/stop authority)
    mode: Optional[AttemptMode]        # None when the loop stops
    runner: AttemptRunner
    best_of_n: int                     # autonomy.best_of_n_first_attempt (the candidate count of a BEST_OF_N runner)

    @property
    def should_continue(self) -> bool:
        return self.decision.should_continue

    @property
    def reserved_fallback(self) -> bool:
        return self.decision.reserved_fallback


def plan_attempt(state: RunState, *, max_retries: int, targeted_max_retries: int, has_fallback_model: bool,
                 verification_only: bool, best_of_n: int, sandboxed: bool) -> AttemptPlan:
    """The loop's decision (``decide_for_state``, unchanged) and the typed plan of the attempt it admits.

    Best-of-N only ever applies to the very first attempt of a run (state.attempt_number == 0 going in - resumed
    checkpoints also start here, but short-circuit to a trivial success on the first call, so they're unaffected), and
    only when a real isolated worktree sandbox actually exists (``sandboxed``: worktree_path != workspace_path) -
    without one, "discarding a candidate" has nothing to reset and would leave that candidate's files on the real
    project."""
    decision = decide_for_state(
        state, max_retries=max_retries, targeted_max_retries=targeted_max_retries, has_fallback_model=has_fallback_model,
    )
    if not decision.should_continue:
        mode = None
    elif verification_only:
        mode = AttemptMode.VERIFICATION_ONLY
    else:
        mode = _MODE_OF_ACTION[decision.action]
    runner = (AttemptRunner.BEST_OF_N if state.attempt_number == 0 and best_of_n > 1 and sandboxed
              else AttemptRunner.SINGLE)
    return AttemptPlan(decision=decision, mode=mode, runner=runner, best_of_n=best_of_n)


class IterationEnd(str, enum.Enum):
    """How one iteration ended, for the loop."""

    APPLIED = "applied"    # the candidate passed every gate and was applied: the loop ends
    RESULT = "result"      # the run stops with a result of its own (the approval gate's abort)
    STOP = "stop"          # a failed attempt whose handling ends the loop (budgets, environment, no progress)
    RETRY = "retry"        # the loop asks for its next plan


@dataclass(frozen=True)
class IterationOutcome:
    end: IterationEnd
    result: Optional[Dict[str, Any]] = None


def begin_attempt(state: RunState, attempt_ctx: Any) -> None:
    """Reset once per loop iteration, unconditionally - NOT just inside the "4.5" section (the approval gate).
    Independent review (2026-08-15) found the narrower reset placement genuinely insufficient: run_attempt() (called
    just after) raises QualityGateFailure directly from many sites (compile-check, targeted-test, anchored-edit
    failures) that jump straight to the loop's failure handling, skipping "4.5" entirely for that iteration. If THAT
    failure is what ultimately ends the loop (an immediate environment_failure break, or ordinary retry-budget
    exhaustion), a stale review from an EARLIER, now-superseded attempt that DID reach "4.5" would otherwise survive and
    get returned as if it described the final (failing) attempt's content - a concretely reproduced bug, not a
    theoretical one. Resetting here, before run_attempt() is even called, guarantees no exception path can skip it."""
    state.pre_approval_review = None
    state.verified_candidate_binding = None
    attempt_ctx.deferred_executing_gates = None
    attempt_ctx.post_source_review_guard = None


def record_phase_advanced(state: RunState, transition: Any, logger: Any) -> None:
    """A verified state-machine transition is progress, not a failed quality-gate attempt. It still consumes the
    dedicated model attempt budget, but must not enter generic failure routing or append a historical FAILED
    outcome."""
    state.budgets.api_contract_recovery_count += 1
    state.record_event(RunEvent(
        kind="api_contract_recovery.phase_advanced",
        attempt=state.attempt_number,
        source="ownership_gate",
        authority=EventAuthority.AUTHORITATIVE,
        message=str(transition),
        details={
            "source_phase": transition.source,
            "target_phase": transition.target,
            "passed": True,
        },
    ))
    logger.info(
        "API_CONTRACT_RECOVERY TRANSITION PASSED - Attempt %d: %s",
        state.attempt_number, transition,
    )


async def run_iteration(
    plan: AttemptPlan, state: RunState, attempt_ctx: Any, *,
    run_attempt: Callable[[RunState, Any], Awaitable[None]],
    load_best_of_n: Callable[[], Callable[..., Awaitable[None]]],
    accept: Callable[[], Awaitable[Optional[Dict[str, Any]]]],
    handle_attempt_failure: Callable[[RunState, Any, BaseException], Awaitable[bool]],
    phase_advanced: Type[BaseException], best_of_n_recorded: Type[BaseException], logger: Any,
) -> IterationOutcome:
    """One iteration: the resets, the attempt-evidence iteration scope, the planned attempt, the candidate's acceptance
    (``accept``: None = applied, a dict = the run's result), and a failed attempt's routing. Exceptions that are not
    ``Exception`` (cancellation, interruption) propagate after the scope is closed, as before."""
    begin_attempt(state, attempt_ctx)
    # LR-R1-M1: the whole iteration is the attempt's evidence identity
    # (observational; reset in this try's finally).
    attempt_evidence_token = attempt_evidence_scope.enter_attempt_iteration(lambda: state.attempt_number)
    try:
        if plan.runner is AttemptRunner.BEST_OF_N:
            run_attempt_with_best_of_n = load_best_of_n()
            await run_attempt_with_best_of_n(state, attempt_ctx, n=plan.best_of_n)
        else:
            await run_attempt(state, attempt_ctx)
        result = await accept()
        if result is not None:
            return IterationOutcome(IterationEnd.RESULT, result)
        return IterationOutcome(IterationEnd.APPLIED)
    except phase_advanced as transition:
        record_phase_advanced(state, transition, logger)
        return IterationOutcome(IterationEnd.RETRY)
    except best_of_n_recorded as recorded:
        # Already recorded by best-of-N (STATE-BEST-OF-N-HANDOFF-001):
        # act on its decision, never record the failure again.
        if recorded.stop_loop:
            return IterationOutcome(IterationEnd.STOP)
        return IterationOutcome(IterationEnd.RETRY)
    except Exception as e:
        if await handle_attempt_failure(state, attempt_ctx, e):
            return IterationOutcome(IterationEnd.STOP)
        return IterationOutcome(IterationEnd.RETRY)
    finally:
        attempt_evidence_scope.exit_attempt_iteration(
            attempt_evidence_token, succeeded=state.overall_attempt_succeeded)


__all__ = ["AttemptMode", "AttemptPlan", "AttemptRunner", "IterationEnd", "IterationOutcome", "begin_attempt",
           "plan_attempt", "record_phase_advanced", "run_iteration"]

"""Bounded trajectory driver for the retry/recovery/fallback state machine.

Runs the retry loop's decision-relevant skeleton over the REAL decision and
bookkeeping functions, in the order the production code calls them:

- the loop: ``decide_for_state`` before every attempt (workflow.py), then
  ``decide_attempt_mode`` and the mode's model (attempt.py: the primary for
  targeted/missing-files/recovery, ``resolve_fallback_model(1)`` for the
  fallback-targeted repair, ``resolve_fallback_model(retry_count)`` for a
  full-set attempt);
- a verified RESTORE step: ``RecoveryPhaseAdvanced`` charges recovery's own
  budget and records no failure (workflow.py);
- a failed attempt, as ``retry_strategy._record_attempt_failure`` records it:
  recovery entry, failure-family reset, ``record_workspace_progress`` with the
  attempt's progress vector, the forced strategy transition, attribution,
  ``charge_failed_attempt``, recovery's scope override; then
  ``conclude_attempt_failure``'s stop rules.

Only the attempt OUTCOME is scripted (an ``Outcome`` per attempt). What the
driver adds is glue with no decision of its own; tests/state_machine/
test_sm_retry_trajectories.py pins that the production code calls the same
functions.
"""
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Iterable, List, Optional

from kriya.workflow.attribution import resolve_fallback_model
from kriya.workflow.retry_policy import (
    API_CONTRACT_RECOVERY_MAX_ATTEMPTS,
    FALLBACK_ALLOWANCE,
    RetryAction,
    api_contract_recovery_handed_back,
    charge_failed_attempt,
    decide_attempt_mode,
    decide_for_state,
    force_strategy_transition,
    observe_failure_family,
    reset_scoped_budgets_for_new_family,
)
from kriya.workflow.retry_progress import build_progress_vector
from kriya.workflow.retry_strategy import record_workspace_progress
from kriya.workflow.state import GenerationState

PRIMARY = "primary"
TARGETED_MAX_RETRIES = 3  # workflow.py TARGETED_MAX_RETRIES
NO_PROGRESS_LIMIT = 3     # max(3, autonomy.max_consecutive_no_progress_attempts) at the default

# Terminal reasons, typed.
SUCCESS = "SUCCESS"
STOP_ENVIRONMENT = "STOP_ENVIRONMENT"
STOP_EXHAUSTED = "STOP_EXHAUSTED"
NO_PROGRESS = "NO_PROGRESS"
FALLBACK_INCOMPATIBLE = "FALLBACK_INCOMPATIBLE"
SCRIPT_ENDED = "SCRIPT_ENDED"


@dataclass(frozen=True)
class Outcome:
    """One attempt's scripted result. ``kind``: pass | fail | violation
    (fail + a public-API violation entering recovery) | restore_ok |
    restore_fail | environment. ``content`` keys the workspace bytes the
    attempt left; ``signature`` the failure family."""
    kind: str
    content: str = "c0"
    signature: str = "s0"
    implicated: bool = True
    missing: bool = False

    def to_dict(self) -> dict:
        return {"kind": self.kind, "content": self.content, "signature": self.signature,
                "implicated": self.implicated, "missing": self.missing}


@dataclass
class Recovery:
    """The fields of APIContractRecovery the decisions read."""
    contract_restored: bool = False
    phase: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(value="RESTORE_PUBLIC_CONTRACT"))
    violations: tuple = ({"owner": "owner.py", "removed_signature": "add(a, b)"},)

    def __getitem__(self, key):  # retry_strategy reads recovery["violations"]
        return getattr(self, key)


@dataclass(frozen=True)
class Step:
    attempt: int
    mode: str
    model: str
    outcome: str
    consecutive_no_progress: int
    classification: Optional[str]
    budgets: tuple  # (retry, targeted, recovery, fallback_targeted_attempted, fallback_targeted_requested)
    reserved_fallback: bool
    in_recovery: bool
    restored: bool


@dataclass
class Trajectory:
    steps: List[Step]
    terminal: str
    state: GenerationState
    chain: list
    max_retries: int

    @property
    def models(self) -> List[str]:
        return [step.model for step in self.steps]

    def fallback_called(self) -> bool:
        return any(model != PRIMARY for model in self.models)


def chain_of(length: int) -> list:
    return [SimpleNamespace(model=f"fallback-{i}") for i in range(length)]


def global_ceiling(max_retries: int, has_fallback: bool, best_of_n_tried: int = 0) -> int:
    """decide_for_state's max_total_attempts, restated for assertions."""
    return (max_retries + TARGETED_MAX_RETRIES + (FALLBACK_ALLOWANCE if has_fallback else 0)
            + API_CONTRACT_RECOVERY_MAX_ATTEMPTS + best_of_n_tried)


def _budgets(state) -> tuple:
    b = state.budgets
    return (b.retry_count, b.targeted_retry_count, b.api_contract_recovery_count,
            b.fallback_targeted_attempted, b.fallback_targeted_requested)


def _mode_value(action: RetryAction) -> str:
    # attempt.py: STOP_* cannot occur for an admitted attempt; it maps to full_set.
    return action.value if action in (
        RetryAction.API_CONTRACT_RECOVERY, RetryAction.TARGETED,
        RetryAction.MISSING_FILES, RetryAction.FALLBACK_TARGETED,
    ) else RetryAction.FULL_SET.value


def run_trajectory(
    outcomes: Callable[[str, str, Optional[Recovery]], Optional[Outcome]] | Iterable[Outcome],
    *,
    chain_length: int = 1,
    incompatible: Iterable[str] = (),
    max_attempts_guard: int = 64,
) -> Trajectory:
    """Drive one run. ``outcomes`` is a sequence of Outcomes, or a callable
    (mode, model, recovery) -> Outcome for generated trajectories."""
    chain = chain_of(chain_length)
    has_fallback = bool(chain)
    max_retries = max(4, 1 + len(chain)) if chain else 4  # workflow.py
    skip = set(incompatible)
    if not callable(outcomes):
        scripted = iter(outcomes)
        outcomes = lambda mode, model, recovery: next(scripted, None)  # noqa: E731 - adapter
    state = GenerationState()
    steps: List[Step] = []
    terminal = STOP_EXHAUSTED

    def policy_kwargs():
        return dict(max_retries=max_retries, targeted_max_retries=TARGETED_MAX_RETRIES,
                    has_fallback_model=has_fallback)

    while True:
        decision = decide_for_state(state, **policy_kwargs())
        if not decision.should_continue:
            terminal = STOP_ENVIRONMENT if decision.action is RetryAction.STOP_ENVIRONMENT else STOP_EXHAUSTED
            break
        if len(steps) >= max_attempts_guard:
            raise AssertionError("retry loop did not terminate within the guard")
        state.attempt_number += 1
        admitted = decide_attempt_mode(state, **policy_kwargs())
        assert admitted.action is decision.action, "attempt mode diverged from the loop decision"
        mode = _mode_value(admitted.action)
        state.last_attempt_mode = mode
        if mode == RetryAction.FALLBACK_TARGETED.value:
            state.budgets.fallback_targeted_attempted = True
            state.budgets.fallback_targeted_requested = False
            selected = resolve_fallback_model(1, chain, skip)
        elif mode == RetryAction.FULL_SET.value:
            selected = resolve_fallback_model(state.budgets.retry_count, chain, skip)
            if selected is None and state.budgets.retry_count > 0 and chain:
                terminal = FALLBACK_INCOMPATIBLE  # _raise_fallback_incompatible
                break
        else:
            selected = None
        if mode == RetryAction.FALLBACK_TARGETED.value and selected is None:
            terminal = FALLBACK_INCOMPATIBLE
            break
        model = selected.model if selected is not None else PRIMARY
        if model != PRIMARY:
            state.budgets.fallback_attempts_used += 1
        state.last_model_override = None if model == PRIMARY else model
        recovery: Optional[Recovery] = state.api_contract_recovery
        outcome = outcomes(mode, model, recovery)
        if outcome is None:
            terminal = SCRIPT_ENDED
            break

        def step(kind, mode=mode, model=model, admitted=admitted):
            steps.append(Step(
                attempt=state.attempt_number, mode=mode, model=model, outcome=kind,
                reserved_fallback=admitted.reserved_fallback,
                consecutive_no_progress=state.consecutive_no_progress_attempts,
                classification=state.last_progress_classification, budgets=_budgets(state),
                in_recovery=state.api_contract_recovery is not None,
                restored=bool(state.api_contract_recovery and state.api_contract_recovery.contract_restored),
            ))

        if outcome.kind == "pass":
            step("pass")
            terminal = SUCCESS
            break
        if outcome.kind == "restore_ok":
            if not (mode == RetryAction.API_CONTRACT_RECOVERY.value and recovery is not None
                    and recovery.phase.value == "RESTORE_PUBLIC_CONTRACT"):
                raise AssertionError(f"restore_ok scripted outside a RESTORE attempt ({mode})")
            # workflow.py: except RecoveryPhaseAdvanced - progress, not a failure.
            state.budgets.api_contract_recovery_count += 1
            recovery.contract_restored = True
            recovery.phase = SimpleNamespace(value="REPAIR_BEHAVIOR")
            step("restore_ok")
            continue
        _record_failure(state, outcome, mode, model, chain)
        step(outcome.kind)
        # conclude_attempt_failure: a no-progress stop precedes the policy.
        if state.no_progress_terminated:
            terminal = NO_PROGRESS
            break
    return Trajectory(steps=steps, terminal=terminal, state=state, chain=chain, max_retries=max_retries)


def _record_failure(state: GenerationState, outcome: Outcome, mode: str, model: str, chain: list) -> None:
    """retry_strategy._record_attempt_failure's decision-relevant steps, in order."""
    if outcome.kind == "environment":
        state.environment_failure = "scripted environment failure"
    if outcome.kind == "violation" and state.api_contract_recovery is None:
        state.api_contract_recovery = Recovery()
    previous = state.budgets.last_failure_signature
    family_changed = observe_failure_family(state.budgets, previous, outcome.signature)
    if family_changed:
        reset_scoped_budgets_for_new_family(state.budgets)
    state.budgets.last_failure_signature = outcome.signature
    recovery = state.api_contract_recovery
    implicated = ("impl.py",) if outcome.implicated and not outcome.missing else ()
    missing = ("missing.py",) if outcome.missing else ()
    vector = build_progress_vector(
        failure_signature=outcome.signature, workspace_hash=outcome.content,
        implicated_files=implicated, missing_files=missing, action=mode,
        protocol=recovery.phase.value if recovery is not None else None, request_profile=model,
    )
    if record_workspace_progress(
        state, outcome.content, NO_PROGRESS_LIMIT, failure_signature=outcome.signature,
        stage="test", files=implicated, action=f"{mode}:{model}", vector=vector,
    ):
        force_strategy_transition(
            state.budgets, consecutive_no_progress=state.consecutive_no_progress_attempts,
            targeted_max_retries=TARGETED_MAX_RETRIES, has_fallback_model=bool(chain),
        )
    # Attribution: the two trackers are mutually exclusive per attempt.
    state.last_missing_files = list(missing) or None
    state.last_implicated_files = None if missing else (list(implicated) or None)
    charge_failed_attempt(state.budgets, attempt_mode=state.last_attempt_mode,
                          plan_scope_conflict=False, failure_family_changed=family_changed)
    if state.api_contract_recovery and not api_contract_recovery_handed_back(state):
        state.last_implicated_files = sorted({item["owner"] for item in state.api_contract_recovery["violations"]})
        state.last_missing_files = None


def generated_outcomes(rng, *, stuck_primary: float = 0.5, pass_rate: float = 0.05):
    """A seeded outcome source: the primary often repeats its bytes (a stuck
    model), fallbacks vary; recovery and environment events are rare."""
    def next_outcome(mode, model, recovery):
        roll = rng.random()
        if recovery is not None and recovery.phase.value == "RESTORE_PUBLIC_CONTRACT" and mode == "api_contract_recovery":
            return Outcome("restore_ok") if roll < 0.7 else Outcome(
                "restore_fail", content=f"r{rng.randrange(2)}", signature="restore")
        if roll < pass_rate:
            return Outcome("pass")
        if roll < pass_rate + 0.01:
            return Outcome("environment")
        kind = "violation" if recovery is None and roll < pass_rate + 0.08 else "fail"
        repeat = model == "primary" and rng.random() < stuck_primary
        return Outcome(
            kind,
            content="c-stuck" if repeat else f"c{rng.randrange(6)}",
            signature="s-stuck" if repeat else f"s{rng.randrange(3)}",
            implicated=rng.random() < 0.7,
            missing=rng.random() < 0.1,
        )
    return next_outcome

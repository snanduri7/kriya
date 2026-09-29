"""Retry/recovery/fallback trajectories over the real decision and bookkeeping
functions (tests/state_machine/_retry_model.py).

A named corpus pins every live-discovered shape and every known policy edge;
a seeded generator explores bounded event sequences against the trajectory
invariants. A failing generated sequence is written out (its seed and
outcome list) so it can be added to CORPUS as a permanent case.
"""
import ast
import inspect
import json
import os
import random

import pytest
from _retry_model import (
    FALLBACK_INCOMPATIBLE,
    NO_PROGRESS,
    PRIMARY,
    STOP_EXHAUSTED,
    SUCCESS,
    Outcome,
    generated_outcomes,
    global_ceiling,
    run_trajectory,
)

from kriya.workflow import retry_strategy
from kriya.workflow.retry_policy import API_CONTRACT_RECOVERY_MAX_ATTEMPTS

pytestmark = pytest.mark.state_machine

FB = "fallback-0"


def _fail(content, signature="s-a", **kwargs):
    return Outcome("fail", content=content, signature=signature, **kwargs)


def _modes(t):
    return [(step.mode, step.model) for step in t.steps]


# --- corpus: live-discovered shapes and policy edges ------------------------------------

def test_c6_a_restored_contract_hands_back_and_reaches_the_fallback():
    """PRD-036 rc5 C6 (WORKFLOW-RECOVERY-HANDBACK-001): violation -> restore
    -> recovery budget spent on the primary -> handback -> fallback."""
    # The behaviour failures name no source file (a test assertion), as in C6.
    outcomes = [Outcome("violation", content="dropped", signature="api"), Outcome("restore_ok"),
                _fail("r1", "s-behavior", implicated=False), _fail("r2", "s-behavior", implicated=False)]
    t = run_trajectory(lambda mode, model, rec: outcomes.pop(0) if outcomes else Outcome("pass"))
    assert t.terminal == SUCCESS
    assert _modes(t)[:4] == [("full_set", PRIMARY)] + [("api_contract_recovery", PRIMARY)] * 3
    assert t.steps[1].outcome == "restore_ok" and t.steps[3].restored
    assert t.state.budgets.api_contract_recovery_count == API_CONTRACT_RECOVERY_MAX_ATTEMPTS
    assert t.steps[4].mode == "full_set" and t.steps[4].model == FB


def test_an_unrestorable_contract_stops_fail_closed_without_the_fallback():
    t = run_trajectory([Outcome("violation", content="dropped", signature="api")]
                       + [Outcome("restore_fail", content=f"r{i}", signature="restore") for i in range(3)])
    assert t.terminal == STOP_EXHAUSTED
    assert [step.mode for step in t.steps[1:]] == ["api_contract_recovery"] * 3
    assert not t.fallback_called()


def test_c8_c10_a_repeating_failure_spends_the_targeted_budget_then_reaches_the_fallback():
    """PRD-036 rc7 C8 / rc8 C10 (FAILURE-SIGNATURE-*): one failure family,
    new bytes every attempt."""
    t = run_trajectory([_fail(f"c{i}") for i in range(4)] + [Outcome("pass")])
    assert _modes(t) == [("full_set", PRIMARY)] + [("targeted", PRIMARY)] * 3 + [("fallback_targeted", FB)]
    assert t.terminal == SUCCESS


def test_an_oscillating_primary_is_charged_and_reaches_the_fallback():
    """STATE-FAILURE-FAMILY-CYCLE-001: A -> B -> A -> B with new bytes."""
    t = run_trajectory([_fail(f"c{i}", "s-a" if i % 2 == 0 else "s-b") for i in range(5)] + [Outcome("pass")])
    assert [mode for mode, _ in _modes(t)] == ["full_set"] + ["targeted"] * 4 + ["fallback_targeted"]
    assert t.steps[-1].model == FB and t.terminal == SUCCESS


def test_a_stuck_primary_is_moved_to_the_fallback_before_the_no_progress_ceiling():
    """The open PRD-026 question, answered for the current policy: identical
    bytes on the primary force the strategy transition after two counted
    no-progress attempts (the first full-set -> targeted change is itself a
    strategy change, not counted), and the fallback runs before a third
    counted attempt can stop the run."""
    t = run_trajectory([_fail("same")] * 4 + [_fail("fb-1", "s-fb")] + [Outcome("pass")])
    assert [step.classification for step in t.steps[:4]] == [
        "PROGRESS", "NO_PROGRESS", "REPEATED_VECTOR", "REPEATED_VECTOR"]
    assert [step.consecutive_no_progress for step in t.steps[:4]] == [0, 0, 1, 2]
    assert t.steps[4].mode == "fallback_targeted" and t.steps[4].model == FB
    assert t.terminal == SUCCESS


def test_stuck_primary_and_stuck_fallback_end_bounded():
    """Both models repeat themselves: the fallback runs right after the
    transition, and the run still ends within its budgets, on the PRD-026
    bound or the full-set budget, never looping."""
    t = run_trajectory(lambda mode, model, rec: _fail("same") if model == PRIMARY else _fail("fb-same", "s-fb"))
    assert t.models.index(FB) == 4
    assert t.terminal in (STOP_EXHAUSTED, NO_PROGRESS)
    assert t.state.budgets.retry_count <= t.max_retries


def test_an_ungrounded_stuck_primary_reaches_the_fallback_through_the_full_set_route():
    """No implicated file: the fallback-targeted repair cannot run, so the
    transition's route is the full-set attempt, which escalates."""
    t = run_trajectory([_fail("same", implicated=False)] * 3 + [Outcome("pass")])
    assert [mode for mode, _ in _modes(t)] == ["full_set"] * 4
    assert FB in t.models[:4]
    assert t.terminal == SUCCESS


def test_an_incompatible_only_fallback_is_a_typed_terminal_failure():
    """PRD-017, documented policy: when the escalation needs a fallback and
    none configured can serve, the run ends FALLBACK_MODEL_INCOMPATIBLE."""
    t = run_trajectory([_fail(f"c{i}") for i in range(8)], incompatible={FB})
    assert t.terminal == FALLBACK_INCOMPATIBLE
    assert not t.fallback_called()


def test_without_a_fallback_the_run_is_bounded_by_the_full_set_budget():
    t = run_trajectory([_fail(f"c{i}") for i in range(20)], chain_length=0)
    assert t.terminal == STOP_EXHAUSTED
    assert t.state.budgets.retry_count == t.max_retries
    assert not t.fallback_called()


# --- generated trajectories -------------------------------------------------------------

SEEDS = range(1500)


def _check_invariants(t, *, chain_length):
    ceiling = global_ceiling(t.max_retries, chain_length > 0)
    # API contract recovery keeps its own budget even when discovered at the
    # ceiling (PRV-11); every other attempt is within the ceiling.
    assert all(step.mode == "api_contract_recovery" for step in t.steps[ceiling:]), "attempts beyond the ceiling"
    assert len(t.steps) <= ceiling + API_CONTRACT_RECOVERY_MAX_ATTEMPTS
    budgets = t.state.budgets
    assert budgets.retry_count <= t.max_retries
    assert budgets.api_contract_recovery_count <= API_CONTRACT_RECOVERY_MAX_ATTEMPTS
    for step in t.steps:
        # Only the fallback-targeted repair and escalated full-set attempts run on a fallback.
        if step.model != PRIMARY:
            assert step.mode in ("fallback_targeted", "full_set") and chain_length
        if step.mode in ("targeted", "missing_files", "api_contract_recovery"):
            assert step.model == PRIMARY
    for before, after in zip(t.steps, t.steps[1:], strict=False):
        # Fail closed: an unrestored contract is followed only by recovery.
        if before.in_recovery and not before.restored and after.attempt:
            assert after.mode == "api_contract_recovery"
        # Handback: a restored, spent recovery never runs another recovery attempt.
        if before.restored and before.budgets[2] >= API_CONTRACT_RECOVERY_MAX_ATTEMPTS:
            assert after.mode != "api_contract_recovery"
    if t.terminal == NO_PROGRESS and chain_length:
        # The strategy transition always hands a stuck run to the fallback
        # before the no-progress ceiling ends it.
        assert t.fallback_called(), "no-progress stop with the configured fallback unused"


@pytest.mark.parametrize("chain_length", (0, 1, 2))
def test_generated_trajectories_keep_the_invariants(chain_length, tmp_path):
    failures = []
    for seed in SEEDS:
        rng = random.Random(seed * 3 + chain_length)
        recorded = []
        source = generated_outcomes(rng, stuck_primary=rng.choice((0.3, 0.7, 0.95)))

        def logged(mode, model, recovery, _source=source, _recorded=recorded):
            outcome = _source(mode, model, recovery)
            _recorded.append(outcome.to_dict())
            return outcome

        t = run_trajectory(logged, chain_length=chain_length)
        try:
            _check_invariants(t, chain_length=chain_length)
        except AssertionError as error:
            failures.append({"seed": seed, "chain_length": chain_length, "error": str(error), "outcomes": recorded})
    if failures:
        target = os.environ.get("KRIYA_STATE_MACHINE_FAILURES") or str(tmp_path)
        path = os.path.join(target, f"retry-trajectory-failures-chain{chain_length}.json")
        with open(path, "w") as handle:
            json.dump(failures, handle, indent=1)
        pytest.fail(f"{len(failures)} trajectories broke an invariant; sequences in {path}; first: {failures[0]}")


# --- the model drives what production drives -------------------------------------------

def test_the_failure_recording_calls_the_modelled_bookkeeping_in_order():
    """_record_attempt_failure must call the same bookkeeping, in the same
    order, as the trajectory model; a reordering or a new inline budget
    mutation breaks this pin."""
    source = inspect.getsource(retry_strategy._record_attempt_failure)
    order = ["observe_failure_family(", "reset_scoped_budgets_for_new_family(", "record_workspace_progress(",
             "force_strategy_transition(", "charge_failed_attempt(", "api_contract_recovery_handed_back("]
    positions = [source.index(name) for name in order]
    assert positions == sorted(positions)
    tree = ast.parse(inspect.cleandoc("\n" + source) if source.startswith(" ") else source)
    mutated = sorted({
        node.attr for node in ast.walk(tree)
        if isinstance(node, (ast.Attribute,)) and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Attribute) and node.value.attr == "budgets"
    })
    # The only budget fields still written inline are the evidence records
    # (not counters or flags a decision reads).
    assert set(mutated) <= {"last_failure_signature"}, mutated


# --- STATE-RESERVED-FALLBACK-001: the fallback's allowance is the fallback's --------------

def _new_families(count, **kwargs):
    return [_fail(f"c{i}", f"s{i}", **kwargs) for i in range(count)]


def test_the_last_slot_is_the_fallbacks_when_every_primary_attempt_is_new_progress():
    t = run_trajectory(_new_families(10) + [Outcome("pass")])
    assert [step.reserved_fallback for step in t.steps] == [False] * 10 + [True]
    assert _modes(t)[-1] == ("fallback_targeted", FB)
    assert len(t.steps) == global_ceiling(t.max_retries, True)


def test_an_ungrounded_new_family_run_reaches_the_fallback_by_the_full_set_route():
    """New families with no grounded target: targeted cannot run, so the
    route is full-set, which escalates from the first charged full-set
    attempt on - the fallback runs without needing the reservation."""
    t = run_trajectory(_new_families(3, implicated=False) + [Outcome("pass")])
    assert t.models[:2] == [PRIMARY, FB]  # the ordinary escalation, no reservation needed
    assert not any(step.reserved_fallback for step in t.steps)


def test_after_the_fallback_has_run_no_slot_stays_reserved():
    """C8 shape first (the fallback-targeted repair runs and fails with a new
    family), then new families on the primary: the allowance is used, so the
    primary keeps the ordinary ceiling."""
    t = run_trajectory([_fail(f"c{i}") for i in range(4)] + [_fail("fb", "s-fb")] + _new_families(20))
    assert t.steps[4].model == FB and not t.steps[4].reserved_fallback
    assert not any(step.reserved_fallback for step in t.steps)
    assert t.terminal == STOP_EXHAUSTED


def test_mandatory_recovery_outranks_the_reserved_fallback_slot():
    """A violation at the reservation boundary: unsafe authoritative state
    comes first - recovery runs (past the ceiling, PRV-11), never the fallback."""
    outcomes = _new_families(9) + [Outcome("violation", content="dropped", signature="api")]
    outcomes += [Outcome("restore_fail", content="r", signature="restore")] * 3
    t = run_trajectory(outcomes)
    assert [step.mode for step in t.steps[10:]] == ["api_contract_recovery"] * 3
    assert not t.fallback_called()
    assert t.terminal == STOP_EXHAUSTED  # unrestored: fail closed

"""STATE-BEST-OF-N-HANDOFF-001: best-of-N candidate failures and the retry loop.

Each failed attempt is recorded exactly once, a verified recovery transition
is never recorded as a failure, and an independent candidate never starts
while authoritative repair state (API contract recovery) is active: that
state is the main loop's, and a reset candidate would run in its mode.
"""
import pytest
from _scripted_run import FALLBACK, PRIMARY, engine, git_workspace, kinds, read, run, run_events, script

from kriya.workflow import attempt as attempt_module
from kriya.workflow import retry_strategy, worktree
from kriya.workflow import workflow as workflow_module

pytestmark = pytest.mark.state_machine

BASELINE = "def add(a, b):\n    return a + b\n"
GOOD = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
HELPER = "def sub(a, b):\n    return a - b\n"
LEAKED = HELPER + "\nUse `sub` for subtraction.\n"  # prose leak: a candidate-gate failure in a new file
DROPS_ADD = "def sub(a, b):\n    return a - b\n"
GOAL = "Add a function sub(a, b) returning a - b to calc.py."
FILES = {"calc.py": BASELINE, "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"}


def _failing(n):
    return f"# attempt {n}\ndef add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"


def _best_of(n):
    def configure(cfg):
        cfg.autonomy.best_of_n_first_attempt = n
    return configure


def _count_handling(monkeypatch):
    """Every exception handle_attempt_failure records, by identity, through
    both names the retry loop and best-of-N reach it by."""
    handled = []
    real = retry_strategy.handle_attempt_failure

    async def counting(state, ctx, exc):
        handled.append(exc)
        return await real(state, ctx, exc)

    monkeypatch.setattr(retry_strategy, "handle_attempt_failure", counting)
    monkeypatch.setattr(workflow_module, "handle_attempt_failure", counting)
    return handled


def _assert_each_failure_recorded_once(handled, events):
    assert len({id(exc) for exc in handled}) == len(handled), "an attempt failure was handled twice"
    assert len(kinds(events, "attempt.failed")) == len(handled)


def test_a_stopping_candidate_failure_is_recorded_once(tmp_path, monkeypatch):
    """A non-final candidate that ends the loop (an internal framework error,
    STOP_ENVIRONMENT) used to be re-raised into the loop's own handler and
    recorded, charged and progress-classified a second time."""
    workspace = git_workspace(tmp_path, FILES)
    eng, llm, cfg = engine(configure=_best_of(3))
    script(llm, plan="Step 1: add sub", target="calc.py", developer=lambda model, n, text: GOOD)
    handled = _count_handling(monkeypatch)
    real_attempt = attempt_module.run_attempt

    async def first_candidate_crashes(state, ctx):
        if state.attempt_number == 0:
            state.attempt_number += 1
            raise TypeError("internal control-plane bug")
        return await real_attempt(state, ctx)

    monkeypatch.setattr(attempt_module, "run_attempt", first_candidate_crashes)

    result = run(eng, workspace, GOAL)

    assert result["quality_gates_passed"] is False
    assert len(handled) == 1
    _assert_each_failure_recorded_once(handled, run_events(cfg))
    assert read(workspace, "calc.py") == BASELINE


def test_a_failed_worktree_reset_keeps_the_recorded_failure_and_the_state_it_was_recorded_in(tmp_path, monkeypatch):
    """No fresh sandbox for the next candidate: best-of-N ends, the failure
    already recorded is not recorded again, no candidate that never ran is
    counted, and the main loop retries from the state the failure left."""
    workspace = git_workspace(tmp_path, FILES)
    eng, llm, cfg = engine(configure=_best_of(3))
    script(llm, plan="Step 1: create helpers.py with sub(a, b)", target="helpers.py",
           developer=lambda model, n, text: LEAKED if n == 1 else HELPER)
    handled = _count_handling(monkeypatch)
    resets = []

    def no_fresh_sandbox(*args, **kwargs):
        resets.append(args)
        raise OSError("worktree reset refused")

    # best_of_n.py imports it at call time; the workflow's own import is untouched.
    monkeypatch.setattr(worktree, "create_git_worktree", no_fresh_sandbox)

    result = run(eng, workspace, "Create helpers.py with a function sub(a, b) returning a - b.")

    assert resets, "the worktree-reset exit was not reached"
    assert result["quality_gates_passed"] is True, result.get("failure_category")
    assert len(handled) == 1
    events = run_events(cfg)
    _assert_each_failure_recorded_once(handled, events)
    # The next attempt repairs the recorded failure (its evidence kept, the
    # failing file implicated): a targeted retry, not a reset full-set candidate.
    assert [event["details"]["mode"] for event in kinds(events, "attempt.started")] == ["full_set", "targeted"]
    assert read(workspace, "helpers.py") == HELPER


def test_a_contract_violation_hands_recovery_to_the_main_loop(tmp_path, monkeypatch):
    """The C6 shape under best-of-N: the first candidate removes add(a, b).
    Recovery is authoritative from then on, so no independent candidate is
    sampled in its mode, and its verified RESTORE -> REPAIR_BEHAVIOR step is
    a transition, never a recorded failure."""
    workspace = git_workspace(tmp_path, FILES)
    eng, llm, cfg = engine(configure=_best_of(3))
    primary = iter([DROPS_ADD] + [_failing(n) for n in range(40)])
    record = script(llm, plan="Step 1: add sub", target="calc.py",
                    developer=lambda model, n, text: GOOD if model == FALLBACK else next(primary))
    handled = _count_handling(monkeypatch)

    result = run(eng, workspace, GOAL)

    assert result["quality_gates_passed"] is True, result.get("failure_category")
    assert read(workspace, "calc.py") == GOOD
    events = run_events(cfg)
    _assert_each_failure_recorded_once(handled, events)
    assert not [exc for exc in handled if type(exc).__name__ == "RecoveryPhaseAdvanced"]
    advanced = kinds(events, "api_contract_recovery.phase_advanced")
    assert [(e["details"]["source_phase"], e["details"]["target_phase"]) for e in advanced] == [
        ("RESTORE_PUBLIC_CONTRACT", "REPAIR_BEHAVIOR"),
    ]
    assert result["retry_progress"]["no_progress_terminated"] is False
    assert FALLBACK in record.developer_calls
    assert record.developer_calls[0] == PRIMARY


def test_a_recorded_stop_ends_the_loop_even_without_an_environment_failure(tmp_path, monkeypatch):
    """A plan-scope conflict or no-progress stop sets no environment failure,
    so the loop's own condition would not end the run: best-of-N's recorded
    stop decision must."""
    workspace = git_workspace(tmp_path, FILES)
    eng, llm, _cfg = engine(configure=_best_of(3))
    script(llm, plan="Step 1: add sub", target="calc.py", developer=lambda model, n, text: GOOD)
    attempts = []
    real_attempt = attempt_module.run_attempt

    async def first_candidate_fails(state, ctx):
        attempts.append(state.attempt_number)
        if not state.attempt_number:
            state.attempt_number += 1
            raise ValueError("candidate failed")
        return await real_attempt(state, ctx)

    async def records_a_stop(state, ctx, exc):
        state.plan_scope_conflict = {"required_files": ["elsewhere.py"]}
        return True

    monkeypatch.setattr(attempt_module, "run_attempt", first_candidate_fails)
    monkeypatch.setattr(retry_strategy, "handle_attempt_failure", records_a_stop)
    monkeypatch.setattr(workflow_module, "handle_attempt_failure", records_a_stop)

    result = run(eng, workspace, GOAL)

    assert attempts == [0]
    assert result["quality_gates_passed"] is False
    assert read(workspace, "calc.py") == BASELINE

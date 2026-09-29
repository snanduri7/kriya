"""STATE-ATTEMPT-METRICS-001: the reported retry counts are attempts that ran.

generation_metrics.retry.{full_set,targeted}_attempts (the certification
report's "Retries (full/targeted)", `kriya metrics`) used to be the scoped
budget counters. Those reset on every new failure family and are forced to
their bound by the no-progress strategy transition, so the report said 4
targeted retries where 3 ran (post-hardening matrix, C11) and 0 or 1 where
nine ran (a primary exposing a new family every attempt). They now count
the attempts actually started, per mode.
"""
import pytest
from _scripted_run import FALLBACK, engine, git_workspace, kinds, run, run_events, script

pytestmark = pytest.mark.state_machine

BASE = "def add(a, b):\n    return a + b\n"
GOOD = BASE + "\n\ndef mul(a, b):\n    return a * b\n"
TESTS = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
GOAL = "Add a function mul(a, b) returning a * b to calc.py."
ERRORS = ("ValueError", "KeyError", "TypeError", "RuntimeError", "LookupError", "IndexError",
          "ZeroDivisionError", "AttributeError", "OSError", "ArithmeticError", "BufferError", "EOFError")


def _defect(error):
    return f"def add(a, b):\n    raise {error}('defect')\n\n\ndef mul(a, b):\n    return a * b\n"


def _started_modes(cfg):
    return [event["details"]["mode"] for event in kinds(run_events(cfg), "attempt.started")]


def _assert_counts_match_attempts(result, modes):
    retry = result["generation_metrics"]["retry"]
    assert retry["full_set_attempts"] == modes.count("full_set")
    assert retry["targeted_attempts"] == modes.count("targeted") + modes.count("missing_files")
    assert retry["attempts_by_mode"] == {mode: modes.count(mode) for mode in set(modes)}


def test_new_family_resets_do_not_hide_targeted_attempts(tmp_path):
    workspace = git_workspace(tmp_path, {"calc.py": BASE, "test_calc.py": TESTS})
    eng, llm, cfg = engine()
    script(llm, plan="Step 1: add mul", target="calc.py",
           developer=lambda model, n, text: GOOD if model == FALLBACK else _defect(ERRORS[n % len(ERRORS)]))

    result = run(eng, workspace, GOAL)

    modes = _started_modes(cfg)
    assert modes.count("targeted") == 9
    _assert_counts_match_attempts(result, modes)


def test_the_forced_strategy_transition_does_not_inflate_targeted_attempts(tmp_path):
    """Identical bytes on the primary: the transition closes the targeted
    budget during a targeted attempt, which pushed the counter past it."""
    workspace = git_workspace(tmp_path, {"calc.py": BASE, "test_calc.py": TESTS})
    eng, llm, cfg = engine()
    script(llm, plan="Step 1: add mul", target="calc.py",
           developer=lambda model, n, text: GOOD if model == FALLBACK else _defect("ValueError"))

    result = run(eng, workspace, GOAL)

    modes = _started_modes(cfg)
    assert "targeted" in modes and result["quality_gates_passed"] is True
    _assert_counts_match_attempts(result, modes)


def test_the_counts_come_from_attempts_not_from_the_budget_counters():
    from kriya.workflow.state import GenerationState

    state = GenerationState()
    # Budget counters that disagree with what ran, as after resets/transitions.
    state.budgets.retry_count = 0
    state.budgets.targeted_retry_count = 4
    state.attempts_by_mode = {"full_set": 2, "targeted": 3, "missing_files": 1, "fallback_targeted": 1}

    retry = state.generation_metrics()["retry"]

    assert (retry["full_set_attempts"], retry["targeted_attempts"]) == (2, 4)
    assert retry["attempts_by_mode"] == {"full_set": 2, "targeted": 3, "missing_files": 1, "fallback_targeted": 1}

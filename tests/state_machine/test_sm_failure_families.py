"""STATE-FAILURE-FAMILY-CYCLE-001: a failure family the run already saw is
not a new family.

Found by the state-machine tier's trajectory model: a primary that fixes one
defect by re-breaking another (A -> B -> A -> B ...) produced a "new failure
family" on every attempt. Each reset the scoped targeted and fallback budgets
and was never charged, and every attempt changed the bytes (PROGRESS), so the
primary spent the whole global ceiling on targeted repairs. The run ended
exhausted with the configured fallback never tried and three of four
full-set retries unused. Only a family never seen before in the candidate
earns fresh scoped budgets; returning to an earlier one is charged.
"""
import pytest
from _scripted_run import FALLBACK, PRIMARY, engine, git_workspace, kinds, read, run, run_events, script

pytestmark = pytest.mark.state_machine

BASE = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
GOOD = BASE + "\n\ndef mul(a, b):\n    return a * b\n"
TESTS = ("from calc import add, sub\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\n"
         "def test_sub():\n    assert sub(3, 2) == 1\n")
GOAL = "Add a function mul(a, b) returning a * b to calc.py."


def _oscillating(n):
    """Fixing one breaks the other: add and sub take turns raising inside
    calc.py (so attribution targets it), with new bytes every attempt."""
    if n % 2:
        add_body, sub_body = 'raise ValueError("add unavailable")', "return a - b"
    else:
        add_body, sub_body = "return a + b", 'raise KeyError("sub unavailable")'
    return (f"# attempt {n}\ndef add(a, b):\n    {add_body}\n\n\ndef sub(a, b):\n    {sub_body}\n\n\n"
            "def mul(a, b):\n    return a * b\n")


def test_an_oscillating_primary_reaches_the_configured_fallback(tmp_path):
    workspace = git_workspace(tmp_path, {"calc.py": BASE, "test_calc.py": TESTS})
    eng, llm, cfg = engine()
    record = script(llm, plan="Step 1: add mul", target="calc.py",
                    developer=lambda model, n, text: GOOD if model == FALLBACK else _oscillating(n))

    result = run(eng, workspace, GOAL)

    assert result["quality_gates_passed"] is True, result.get("failure_category")
    assert read(workspace, "calc.py") == GOOD
    events = run_events(cfg)
    modes = [event["details"]["mode"] for event in kinds(events, "attempt.started")]
    # The initial attempt fails with family A, the first targeted repair
    # exposes B (new: fresh budget), and each return to A or B is charged:
    # three charged targeted repairs, then the one fallback-targeted repair.
    assert modes == ["full_set", "targeted", "targeted", "targeted", "targeted", "fallback_targeted"]
    assert record.developer_calls == [PRIMARY] * 5 + [FALLBACK]
    # Every attempt changed the bytes: progress, never a no-progress stop.
    assert result["retry_progress"]["no_progress_terminated"] is False
    transitions = [e["details"] for e in kinds(events, "model.transition") if e["details"]["fallback"]]
    assert transitions and transitions[0]["to"]["model"] == FALLBACK


def test_with_no_fallback_the_oscillation_ends_in_a_bounded_full_set_failure(tmp_path):
    """No fallback: after the charged targeted repairs, the full-set budget
    runs (not more resets on the primary), and the run stops bounded."""
    workspace = git_workspace(tmp_path, {"calc.py": BASE, "test_calc.py": TESTS})
    eng, llm, cfg = engine(chain=False)
    script(llm, plan="Step 1: add mul", target="calc.py", developer=lambda model, n, text: _oscillating(n))

    result = run(eng, workspace, GOAL)

    assert result["quality_gates_passed"] is False
    assert read(workspace, "calc.py") == BASE
    modes = [event["details"]["mode"] for event in kinds(run_events(cfg), "attempt.started")]
    assert modes[:5] == ["full_set", "targeted", "targeted", "targeted", "targeted"]
    assert "full_set" in modes[5:]
    assert len(modes) <= 4 + 3 + 3  # the global ceiling without a fallback

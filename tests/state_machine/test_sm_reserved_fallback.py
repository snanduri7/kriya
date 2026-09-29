"""STATE-RESERVED-FALLBACK-001: the fallback's allowance in the global
ceiling belongs to the fallback.

A primary that exposes a genuinely new failure family on every attempt
legitimately refreshes its targeted budget each time (each is progress), but
it used to spend the whole global ceiling (11 attempts) on itself: the
ceiling counted a fallback allowance the fallback never received. Now, once
the remaining capacity reaches the fallback's unused allowance, the attempt
is the fallback's, recorded as a typed retry.reserved_fallback event, and no
attempt is added.
"""
import pytest
from _scripted_run import FALLBACK, PRIMARY, engine, git_workspace, kinds, read, run, run_events, script

from kriya.workflow.retry_policy import RESERVED_FALLBACK_ALLOWANCE

pytestmark = pytest.mark.state_machine

BASE = "def add(a, b):\n    return a + b\n"
GOOD = BASE + "\n\ndef mul(a, b):\n    return a * b\n"
TESTS = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
GOAL = "Add a function mul(a, b) returning a * b to calc.py."
# A different exception each attempt: a genuinely new, never-seen failure
# family every time, raised inside calc.py so attribution grounds it there.
ERRORS = ("ValueError", "KeyError", "TypeError", "RuntimeError", "LookupError", "IndexError",
          "ZeroDivisionError", "AttributeError", "OSError", "ArithmeticError", "BufferError", "EOFError")
CEILING = 4 + 3 + 1 + 3  # full-set + targeted + fallback allowance + recovery allowance


def _new_defect(n):
    return f"def add(a, b):\n    raise {ERRORS[n % len(ERRORS)]}('defect')\n\n\ndef mul(a, b):\n    return a * b\n"


def _run(tmp_path, *, chain=True, fallback_output=GOOD):
    workspace = git_workspace(tmp_path, {"calc.py": BASE, "test_calc.py": TESTS})
    eng, llm, cfg = engine(chain=chain)
    record = script(llm, plan="Step 1: add mul", target="calc.py",
                    developer=lambda model, n, text: fallback_output if model == FALLBACK else _new_defect(n))
    result = run(eng, workspace, GOAL)
    events = run_events(cfg)
    return result, record, events, workspace


def test_a_primary_making_new_progress_every_attempt_cannot_consume_the_fallback_slot(tmp_path):
    result, record, events, workspace = _run(tmp_path)

    assert result["quality_gates_passed"] is True, result.get("failure_category")
    assert read(workspace, "calc.py") == GOOD
    modes = [event["details"]["mode"] for event in kinds(events, "attempt.started")]
    # Every primary attempt was progress (a new family); the last slot is the fallback's.
    assert modes == ["full_set"] + ["targeted"] * (CEILING - 2) + ["fallback_targeted"]
    assert record.developer_calls == [PRIMARY] * (CEILING - 1) + [FALLBACK]
    assert result["retry_progress"]["no_progress_terminated"] is False
    reserved = kinds(events, "retry.reserved_fallback")
    assert [(e["attempt"], e["details"]["reason_code"], e["details"]["mode"]) for e in reserved] == [
        (CEILING, RESERVED_FALLBACK_ALLOWANCE, "fallback_targeted"),
    ]


def test_the_reserved_fallback_attempt_adds_no_attempt_to_the_ceiling(tmp_path):
    """The fallback fails too: the run ends at the unchanged ceiling."""
    result, record, events, workspace = _run(tmp_path, fallback_output=_new_defect(99))

    assert result["quality_gates_passed"] is False
    assert len(kinds(events, "attempt.started")) == CEILING
    assert record.developer_calls[-1] == FALLBACK and record.developer_calls.count(FALLBACK) == 1
    assert read(workspace, "calc.py") == BASE


def test_without_a_fallback_the_primary_keeps_the_ordinary_ceiling(tmp_path):
    result, record, events, _workspace = _run(tmp_path, chain=False)

    assert result["quality_gates_passed"] is False
    assert set(record.developer_calls) == {PRIMARY}
    assert len(kinds(events, "attempt.started")) == CEILING - 1  # no fallback allowance
    assert not kinds(events, "retry.reserved_fallback")


def _mixed_run(tmp_path, primary_outputs):
    workspace = git_workspace(tmp_path, {"calc.py": BASE, "test_calc.py": TESTS})
    eng, llm, cfg = engine()
    primary = iter(primary_outputs)
    record = script(llm, plan="Step 1: add mul", target="calc.py",
                    developer=lambda model, n, text: _new_defect(100) if model == FALLBACK else next(primary))
    result = run(eng, workspace, GOAL)
    return result, record, run_events(cfg)


def test_a_fallback_targeted_repair_that_already_ran_leaves_no_slot_reserved(tmp_path):
    """The same family three times sends the primary's targeted repairs to the
    one fallback-targeted repair early; its new failure then leaves the
    primary to the ordinary ceiling, with no second, reserved fallback turn."""
    same = [f"# attempt {n}\n" + _new_defect(0) for n in range(4)]
    result, record, events = _mixed_run(tmp_path, same + [_new_defect(n) for n in range(1, 40)])

    assert result["quality_gates_passed"] is False
    assert record.developer_calls[4] == FALLBACK and record.developer_calls.count(FALLBACK) == 1
    assert not kinds(events, "retry.reserved_fallback")


def test_an_escalated_full_set_attempt_that_already_ran_leaves_no_slot_reserved(tmp_path):
    """An ungrounded first failure escalates the next full-set attempt to the
    fallback; its new failure then leaves the primary to the ordinary
    ceiling, with no reserved fallback turn."""
    ungrounded = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
    result, record, events = _mixed_run(tmp_path, [ungrounded] + [_new_defect(n) for n in range(1, 40)])

    assert result["quality_gates_passed"] is False
    assert record.developer_calls[1] == FALLBACK and record.developer_calls.count(FALLBACK) == 1
    assert not kinds(events, "retry.reserved_fallback")

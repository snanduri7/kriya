"""Request/context budgets -> retry/fallback (audit area 11).

A PRD-016 refusal on the primary (the prompt plus the minimum output cannot
fit its served window) is typed, grounds no file, and nothing was sent, so
the next attempt takes the escalated full-set route to the fallback, whose
own window may fit. It must never burn the primary's targeted budget or end
the run while the configured fallback is unused.
"""
from types import SimpleNamespace

import pytest
from _scripted_run import FALLBACK, PRIMARY, engine, git_workspace, kinds, read, run, run_events, script

from kriya.core.token_budget import ContextBudgetUnsatisfiableError

pytestmark = pytest.mark.state_machine

BASELINE = "def add(a, b):\n    return a + b\n"
GOOD = BASELINE + "\n\ndef sub(a, b):\n    return a - b\n"
FILES = {"calc.py": BASELINE, "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"}


def _refusal():
    decision = SimpleNamespace(
        prompt_tokens=90000, counting_method="default", min_output_tokens=256, reasoning_allowance=0,
        context_window=32768, window_source="served_num_ctx", context_expanded=False,
        to_dict=lambda: {"prompt_tokens": 90000, "context_window": 32768},
    )
    return ContextBudgetUnsatisfiableError(decision)


def test_a_primary_budget_refusal_reaches_the_fallback_by_the_full_set_route(tmp_path):
    workspace = git_workspace(tmp_path, FILES)
    eng, llm, cfg = engine()

    def developer(model, n, text):
        if model == PRIMARY:
            raise _refusal()
        return GOOD

    record = script(llm, plan="Step 1: add sub", target="calc.py", developer=developer)

    result = run(eng, workspace, "Add a function sub(a, b) returning a - b to calc.py.")

    assert result["quality_gates_passed"] is True, result.get("failure_category")
    assert read(workspace, "calc.py") == GOOD
    events = run_events(cfg)
    assert [e["details"]["failure_type"] for e in kinds(events, "attempt.failed")] == ["context_budget_unsatisfiable"]
    assert [e["details"]["mode"] for e in kinds(events, "attempt.started")] == ["full_set", "full_set"]
    assert record.developer_calls[-1] == FALLBACK and PRIMARY in record.developer_calls

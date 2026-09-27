"""PRD-032: the chaos harness's own contract.

- Every scenario in SCENARIOS is bound to exactly one test, and every @chaos
  test names a registered scenario (a scenario can never silently lose its
  test, and a test can never report under an unknown id).
- The report is a pure, reproducible function of the results: the content
  and its digest never depend on input order, run time or paths, a FAILED
  test is never hidden by a passing one, and an unrun scenario is NOT_RUN.
- An observation is typed and content-free.
- A passing chaos test that records no observation fails.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from _chaos_harness import SCENARIOS, SUPPORTING_SUITES, ChaosCase, close_case
from _chaos_report import (
    FAILED,
    NOT_RUN,
    PASSED,
    PHASE_REPORTS,
    SKIPPED,
    build_report,
    item_verdict,
    render_markdown,
)

TESTS = Path(__file__).resolve().parent
CHAOS_FILES = sorted(TESTS.glob("test_prd032_chaos_*.py")) + [TESTS / "test_live_prd032_chaos.py"]


def _bindings():
    bound = {}
    for path in CHAOS_FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and getattr(decorator.func, "id", None) == "chaos"
                        and decorator.args and isinstance(decorator.args[0], ast.Constant)):
                    bound.setdefault(decorator.args[0].value, []).append(f"{path.name}::{node.name}")
    return bound


def test_every_scenario_is_bound_to_exactly_one_test():
    bound = _bindings()
    assert sorted(set(bound) - set(SCENARIOS)) == [], "a @chaos test names an unregistered scenario"
    assert sorted(set(SCENARIOS) - set(bound)) == [], "a registered scenario has no test"
    assert {sid: tests for sid, tests in bound.items() if len(tests) > 1} == {}


def test_every_family_names_its_supporting_suites_and_they_exist():
    assert {s.family for s in SCENARIOS.values()} <= set(SUPPORTING_SUITES)
    missing = [suite for suites in SUPPORTING_SUITES.values() for suite in suites
               if not (TESTS.parent / suite).is_file()]
    assert missing == []


def _result(sid, verdict, nodeid=None, outcome="X"):
    return {"scenario_id": sid, "nodeid": nodeid or f"t.py::{sid}", "verdict": verdict,
            "observation": {"outcome": outcome, "evidence": {"n": 1}, "identity": {"model": "m"}}}


RUN_A = {"revision": "a", "python": "3.12", "selection": "chaos", "exit_status": 0}
RUN_B = {"revision": "b", "python": "3.14", "selection": "", "exit_status": 1}


def test_the_report_content_is_reproducible_and_independent_of_run_metadata():
    results = [_result("A01", PASSED), _result("B01", PASSED), _result("C01", FAILED)]
    first = build_report(results, SCENARIOS, SUPPORTING_SUITES, RUN_A)
    second = build_report(list(reversed(results)), SCENARIOS, SUPPORTING_SUITES, RUN_B)
    assert first["content"] == second["content"] and first["content_digest"] == second["content_digest"]
    assert first["run"] != second["run"]
    assert json.loads(json.dumps(first)) == first


def test_verdicts_never_hide_a_failure_and_unrun_scenarios_are_not_run():
    report = build_report([_result("A01", PASSED, "a.py::x"), _result("A01", FAILED, "b.py::y"),
                           _result("A02", SKIPPED)], SCENARIOS, SUPPORTING_SUITES, RUN_A)
    rows = {row["scenario_id"]: row for row in report["content"]["scenarios"]}
    assert rows["A01"]["verdict"] == FAILED and rows["A02"]["verdict"] == SKIPPED
    assert rows["A03"]["verdict"] == NOT_RUN and rows["A03"]["observed_outcome"] is None
    summary = report["content"]["summary"]
    assert summary["total"] == len(SCENARIOS) and summary["failed"] == 1 and summary["not_run"] == len(SCENARIOS) - 2
    markdown = render_markdown(report)
    assert report["content_digest"] in markdown and "| A01 |" in markdown


def test_a_result_for_an_unregistered_scenario_is_refused():
    with pytest.raises(ValueError, match="unregistered"):
        build_report([_result("Z99", PASSED)], SCENARIOS, SUPPORTING_SUITES, RUN_A)


def _phase(passed=False, failed=False, skipped=False):
    return SimpleNamespace(passed=passed, failed=failed, skipped=skipped)


def test_item_verdict_reads_every_phase():
    assert item_verdict({"setup": _phase(passed=True), "call": _phase(passed=True),
                         "teardown": _phase(passed=True)}) == PASSED
    assert item_verdict({"setup": _phase(passed=True), "call": _phase(passed=True),
                         "teardown": _phase(failed=True)}) == FAILED
    assert item_verdict({"setup": _phase(skipped=True)}) == SKIPPED
    assert item_verdict({"setup": _phase(passed=True)}) == NOT_RUN


def test_an_observation_must_be_typed_and_content_free(tmp_path):
    case = ChaosCase("A01", tmp_path)
    with pytest.raises(AssertionError, match="untyped outcome"):
        case.observe("the model said: all good!")
    with pytest.raises(AssertionError, match="path"):
        case.observe("OK", where=str(tmp_path / "x"))
    with pytest.raises(TypeError):
        case.observe("OK", blob=object())
    case.observe("OK", codes=["A", "B"], count=2)
    assert case.observation["outcome"] == "OK"


def test_a_passing_chaos_test_without_an_observation_fails(tmp_path):
    node = SimpleNamespace(user_properties=[], stash={PHASE_REPORTS: {"call": _phase(passed=True)}})
    with pytest.raises(pytest.fail.Exception, match="without recording an observation"):
        close_case(SimpleNamespace(node=node), ChaosCase("A01", tmp_path))
    failed = SimpleNamespace(user_properties=[], stash={PHASE_REPORTS: {"call": _phase(failed=True)}})
    close_case(SimpleNamespace(node=failed), ChaosCase("A01", tmp_path))  # the failure itself is the report
    observed = ChaosCase("A01", tmp_path)
    observed.observe("OK")
    close_case(SimpleNamespace(node=node), observed)
    assert node.user_properties == [("chaos_observation", observed.observation)]

"""REG-R1 end-to-end reproducer through the real pipeline (only the model runtime is scripted).

The OBS-4 sequence: a brownfield Python workspace whose suite already has failing tests that print run-varying text,
a required full-regression baseline, and a candidate that changes no test's outcome (it adds ``sub`` to calc.py and
resolves the one test that needed it). Before REG-R1 the whole-output fingerprint of the POST suite differed from the
baseline's and the run stopped REGRESSION_UNATTRIBUTED; after REG-R1 per-test authority with untouched-baseline
stability lets the candidate through and retains the decision. This module imports nothing introduced by REG-R1, so
it runs unchanged against the pre-fix product (the BEFORE evidence).
"""
from _chaos_harness import CALC, CALC_WITH_SUB, benign_roles, chaos_config
from _reg_r1_fixture import SUITE
from _t6_harness import direct_run

# The test of the missing function fails at RUN time (a complete baseline session, as in OBS-4); importing the
# missing name instead would be a collection error - an incomplete baseline, which fails closed by design.
TEST_SUB = "import calc\n\n\ndef test_sub():\n    assert calc.sub(3, 1) == 2\n"
# Like the live workspace, the repository ignores interpreter output: the baseline's own run then leaves the
# pristine revision unchanged (see the second test for a repository that does not).
FILES = {"calc.py": CALC, "test_calc.py": TEST_SUB, "requirements.txt": "", ".gitignore": "__pycache__/\n",
         "tests/__init__.py": "", "tests/test_suite.py": SUITE}


def _developer(role, request):
    return CALC_WITH_SUB if role == "developer" else benign_roles(role, request)


def test_reg_r1_a_candidate_that_changes_no_outcome_survives_run_varying_pre_existing_failures(tmp_path, monkeypatch):
    cfg = chaos_config(brownfield_full_regression_baseline_policy="required")
    observed = direct_run(tmp_path, monkeypatch, _developer, FILES, cfg=cfg)

    decisions = [r["payload"] for r in observed.of("recovery.decision")]
    assert all(d.get("stop_reason_code") != "REGRESSION_UNATTRIBUTED" for d in decisions), decisions
    assert all(r["payload"]["type"] != "regression_unattributed" for r in observed.of("mirror.gate_outcome"))
    assert observed.result["quality_gates_passed"] is True, observed.result.get("failure_category")
    assert "def sub(a, b):" in (observed.workspace / "calc.py").read_text()

    [decision] = [r for r in observed.of("regression.decision") if r["payload"]["scope"] == "full_regression"]
    payload = decision["payload"]
    assert payload["authority"] == "pytest_per_test" and payload["blocking"] is False
    assert payload["diagnostic_level1"] == "CHANGED_FAILURE"            # the whole output did differ
    comparison = __import__("json").loads(observed.run.blob(decision["blobs"]["comparison"]))
    assert comparison["level2"] == {
        "test_calc.py::test_sub": "RESOLVED_FAILURE",
        "tests/test_suite.py::test_message_varies_each_run": "PRE_EXISTING_FAILURE",
        "tests/test_suite.py::test_stable_failure": "PRE_EXISTING_FAILURE",
        "tests/test_suite.py::test_traceback_arguments_vary_each_run": "PRE_EXISTING_FAILURE",
        "tests/test_suite.py::test_z_fails_only_under_the_full_suite": "PRE_EXISTING_FAILURE",
    }
    assert comparison["volatile_fields_ignored"] == {
        "tests/test_suite.py::test_message_varies_each_run": ["message", "body"],
        "tests/test_suite.py::test_traceback_arguments_vary_each_run": ["body"],
        "tests/test_suite.py::test_z_fails_only_under_the_full_suite": ["message", "body"],
    }
    # measured in the same (full-suite) context: the suite-dependent failure passes alone, fails in every suite run
    stability = __import__("json").loads(observed.run.blob(decision["blobs"]["stability"]))
    suite_dependent = [r for r in stability if r["test"] == "tests.test_suite::test_z_fails_only_under_the_full_suite"]
    assert [(r["flaky"], [s["fields"]["message"] for s in r["envelope"]["states"]]) for r in suite_dependent] == [
        (False, ["VOLATILE"])]                                   # one state (outcome and type stable), message volatile
    # the stability replays ran as the baseline's own full-suite gate (their raw evidence retained): baseline, POST
    # and exactly two same-context replays, each a full-suite run reporting every test of the suite
    full_suite_runs = [r for r in observed.of("gate.result")
                       if (r["payload"].get("test_execution") or {}).get("cases") == 8]
    assert len(full_suite_runs) == 4


def test_reg_r1_a_baseline_that_its_own_run_changed_cannot_excuse_a_difference_and_blames_nothing(tmp_path,
                                                                                                  monkeypatch):
    """A repository whose test run leaves non-ignored output (here __pycache__) has no untouched baseline to replay
    once the baseline ran: stability is INDETERMINATE, the run fails closed (unattributed), and no pre-existing
    failure is ever reported as candidate-caused. (Such a candidate worktree also inherits that output through the
    workspace sync - a separate, recorded finding.)"""
    files = {name: text for name, text in FILES.items() if name != ".gitignore"}
    observed = direct_run(tmp_path, monkeypatch, _developer, files,
                          cfg=chaos_config(brownfield_full_regression_baseline_policy="required"))
    [decision] = [r for r in observed.of("regression.decision") if r["payload"]["scope"] == "full_regression"]
    comparison = __import__("json").loads(observed.run.blob(decision["blobs"]["comparison"]))
    stability = __import__("json").loads(observed.run.blob(decision["blobs"]["stability"]))
    assert decision["payload"]["blocking"] is True and decision["payload"]["authority"] == "pytest_per_test"
    assert "CHANGED_FAILURE" not in comparison["level2"].values() and "NEW_FAILURE" not in comparison["level2"].values()
    assert {r["reason"] for r in stability} == {"BASELINE_REVISION_CHANGED"}
    assert {(r["envelope"]["unresolved"], len(r["envelope"]["states"])) for r in stability} == {
        ("BASELINE_REVISION_CHANGED", 0)}
    assert observed.result["quality_gates_passed"] is False

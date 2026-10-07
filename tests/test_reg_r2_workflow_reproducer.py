"""REG-R2 end-to-end reproducer through the real pipeline (only the model runtime is scripted).

The POST-REG-R1 Arm-A sequence (run 20261007T071630-e21ca9b2): a correct candidate, and a pre-existing test whose
outcome flips between executions of the SAME untouched code in the same full-suite context - FAIL on the baseline,
FAIL on the candidate (same exception type, another message), and FAIL / PASS / FAIL across the baseline and its two
same-context replays. REG-R1 called the baseline outcome-unstable, left message/body UNRESOLVED and stopped the run
REGRESSION_UNATTRIBUTED (a confirmed false negative). Generically here: the test passes on exactly the third
execution of the suite (a counter outside the workspace, so the pristine revision never changes), which is the
third full-suite run after the baseline and the candidate - the first stability replay.

This module imports nothing introduced by REG-R2, so it runs unchanged against the pre-fix product (BEFORE evidence).
"""
import json

from _chaos_harness import CALC, CALC_WITH_SUB, benign_roles, chaos_config
from _t6_harness import direct_run

TEST_SUB = "import calc\n\n\ndef test_sub():\n    assert calc.sub(3, 1) == 2\n"
FLAKY = "tests.test_flaky::test_timing_depends_on_the_run"


def flaky_suite(counter):
    """A pre-existing test whose outcome depends on how often the suite has run (never on the code under test)."""
    return f'''import pathlib

COUNTER = pathlib.Path({str(counter)!r})


def test_passes():
    assert True


def test_timing_depends_on_the_run():
    run = int(COUNTER.read_text()) + 1 if COUNTER.exists() else 1
    COUNTER.write_text(str(run))
    assert run == 3, f"took {{run}}.0x the reference time"
'''


def _developer(role, request):
    return CALC_WITH_SUB if role == "developer" else benign_roles(role, request)


def test_reg_r2_a_correct_candidate_survives_a_pre_existing_test_that_flips_on_the_untouched_baseline(tmp_path,
                                                                                                      monkeypatch):
    counter = tmp_path / "flake-counter"
    files = {"calc.py": CALC, "test_calc.py": TEST_SUB, "requirements.txt": "", ".gitignore": "__pycache__/\n",
             "tests/__init__.py": "", "tests/test_flaky.py": flaky_suite(counter)}
    observed = direct_run(tmp_path, monkeypatch, _developer, files,
                          cfg=chaos_config(brownfield_full_regression_baseline_policy="required"))
    assert observed.workspace not in counter.parents, "the counter must never change the pristine revision"

    [decision] = [r for r in observed.of("regression.decision") if r["payload"]["scope"] == "full_regression"]
    comparison = json.loads(observed.run.blob(decision["blobs"]["comparison"]))
    stability = json.loads(observed.run.blob(decision["blobs"]["stability"]))
    # the measured Arm-A shape: baseline FAIL, candidate FAIL (same type, another message), replays PASS then FAIL
    assert int(counter.read_text()) == 4
    [flaky] = [r for r in stability if r["test"] == FLAKY]
    assert len(flaky["observations"]) == 3
    assert observed.result["quality_gates_passed"] is True, observed.result.get("failure_category")
    assert decision["payload"]["blocking"] is False
    assert comparison["level2"]["tests/test_flaky.py::test_timing_depends_on_the_run"] == "FLAKY_PREEXISTING"
    assert comparison["level2"]["test_calc.py::test_sub"] == "RESOLVED_FAILURE"
    assert "def sub(a, b):" in (observed.workspace / "calc.py").read_text()

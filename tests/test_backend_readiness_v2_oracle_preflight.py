"""BACKEND-READINESS-V2 (owner item 5): an oracle's non-zero exit is a candidate verdict only with evidence the candidate was
judged. Fixtures are the real logs of the Stage A/B control runs (environment disturbances and baseline rejections)."""
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent.parent / "handover" / "BACKEND-READINESS-V2" / "gate-certification" / "oracle_preflight.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "oracle_preflight"
spec = importlib.util.spec_from_file_location("oracle_preflight", MODULE)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


@pytest.mark.parametrize("name,exit_code,expected", [
    ("env-T1-pip_index_unreachable", 2, preflight.ENVIRONMENT_UNAVAILABLE),
    ("env-T2-empty_maven_repo", 1, preflight.ENVIRONMENT_UNAVAILABLE),
    ("env-T3-empty_GRADLE_USER_HOME", 1, preflight.ENVIRONMENT_UNAVAILABLE),
    ("reject-T1-base", 1, preflight.REJECT),
    ("reject-T2-base", 1, preflight.REJECT),
    ("reject-T3-base", 1, preflight.REJECT),
    ("reject-S4_B-base", 1, preflight.REJECT),
])
def test_recorded_runs_are_classified_as_environment_or_candidate_verdicts(name, exit_code, expected):
    verdict, _reason = preflight.classify_run(exit_code, (FIXTURES / f"{name}.txt").read_text())
    assert verdict == expected
    assert preflight.counts_as_candidate_verdict(verdict) == (expected == preflight.REJECT)


def test_exit_zero_passes_and_unexplained_failure_is_indeterminate_never_a_rejection():
    assert preflight.classify_run(0, "")[0] == preflight.PASS
    verdict, _ = preflight.classify_run(1, "something exploded\nACCEPT_EXIT=1\n")
    assert verdict == preflight.INDETERMINATE and not preflight.counts_as_candidate_verdict(verdict)


def test_a_candidate_compile_error_is_a_rejection_even_though_no_test_ran():
    log = "[ERROR] COMPILATION ERROR :\n[ERROR] Foo.java:[3,1] error: cannot find symbol\nUPSTREAM TOTALS tests=0 failures=0\n"
    assert preflight.classify_run(1, log)[0] == preflight.REJECT


def test_environment_signature_wins_over_executed_tests_of_an_earlier_phase():
    log = "Tests run: 5, Failures: 0\nUPSTREAM TOTALS tests=12\nCould not transfer artifact x from central\nBUILD FAILURE\n"
    assert preflight.classify_run(1, log)[0] == preflight.ENVIRONMENT_UNAVAILABLE


def test_a_compile_task_that_fails_on_dependency_resolution_is_environment_not_a_candidate_fault():
    log = "Execution failed for task ':compileJava'.\n> Could not resolve all files for configuration ':compileClasspath'.\n"
    assert preflight.classify_run(1, log)[0] == preflight.ENVIRONMENT_UNAVAILABLE


@pytest.mark.parametrize("log", [
    "ERROR collecting tests/test_x.py\nImportError: cannot import name y\n1 error during collection\n",
    "[ERROR] /w/src/main/java/A.java:[3,9] ';' expected\n[ERROR] Failed to execute goal compiler:testCompile\n",
    "src/main/java/A.java:3: error: ';' expected\n",
])
def test_candidate_import_and_javac_errors_are_rejections(log):
    assert preflight.classify_run(1, log)[0] == preflight.REJECT


def test_empty_contract_lines_are_not_faults():
    log = "TAMPERED=[]\nFORBIDDEN_CHANGES=[]\nCOMPAT_EXIT=0\nUPSTREAM TOTALS tests=0\nCannot access central in offline mode and the artifact a:b has not been downloaded\n"
    assert preflight.classify_run(1, log)[0] == preflight.ENVIRONMENT_UNAVAILABLE

"""JVM-TEST-PHASE-001 (WS3-A): the typed phase record of a failed Maven / Gradle test gate.

The record is a PHASE FACT (where the build stopped), built from invocation identity, started / timeout / return-code facts and
the structured report of the invocation. It never reads output text, and it carries no owner, defect or repair field: attribution
and mutation authority are not an adapter's to decide.

Every control drives the real ``PolymorphicValidator.run_tests`` -> adapter -> report-binding path; only the contained command
(``_run_maven_cmd`` / ``_run_gradle_cmd``) is scripted, and a "report" is a real JUnit XML file written where the build writes it.
"""
import os
from unittest.mock import patch

import pytest

from kriya.capabilities import GRADLE, MAVEN
from kriya.config import AppConfig
from kriya.tools import test_execution as te
from kriya.tools.containment import ContainmentSetupError
from kriya.tools.validate import PolymorphicValidator

OK = {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}
FAIL = {"returncode": 1, "stdout": "", "stderr": "", "timeout": False}

SYSTEMS = {
    "maven": {"marker": "pom.xml", "main": ["compile"], "test_source": ["test-compile"],
              "report": os.path.join("target", "surefire-reports", "TEST-a.VTest.xml")},
    "gradle": {"marker": "build.gradle", "main": ["classes"], "test_source": ["testClasses"],
               "report": os.path.join("build", "test-results", "test", "TEST-a.VTest.xml")},
}
_PHASE_OF = {"test": "test", "compile": "main", "classes": "main", "test-compile": "test_source", "testClasses": "test_source"}

FAILED_CASE = '<testcase classname="a.VTest" name="m"><failure message="x">boom</failure></testcase>'
ERRORED_CASE = '<testcase classname="a.VTest" name="m"><error message="x">boom</error></testcase>'
PASSED_CASE = '<testcase classname="a.VTest" name="m"/>'


def _suite(case, *, failures=0, errors=0):
    return f'<testsuite name="a.VTest" tests="1" failures="{failures}" errors="{errors}" skipped="0">{case}</testsuite>'


def _run(tmp_path, system, script, *, report=None, text="", target="src/test/java/a/VTest.java"):
    """Run the real test gate with a scripted contained command. ``script`` maps phase (test / main / test_source) to the command
    result, or to an exception to raise. ``report``: JUnit XML the test invocation leaves behind. ``text`` is added to every
    scripted result's stdout and stderr. Returns (gate result, [argument list of each invocation])."""
    spec = SYSTEMS[system]
    (tmp_path / spec["marker"]).write_text("<project/>" if system == "maven" else "")
    calls = []

    def command(arguments):
        calls.append(list(arguments))
        phase = _PHASE_OF[arguments[0].rsplit(":", 1)[-1]]
        outcome = script[phase]
        if isinstance(outcome, BaseException):
            raise outcome
        if phase == "test" and report is not None:
            path = tmp_path / spec["report"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report)
        return {**outcome, "stdout": outcome.get("stdout", "") + text, "stderr": outcome.get("stderr", "") + text}

    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AppConfig().autonomy)
    with patch.object(PolymorphicValidator, "_run_maven_cmd", new=lambda self, goals, cwd, timeout=300, **_: command(goals)), \
            patch.object(PolymorphicValidator, "_run_gradle_cmd", new=lambda self, cmd, tasks, cwd, **_: command(tasks)):
        result = validator.run_tests(target)
    return result, calls


both = pytest.mark.parametrize("system", sorted(SYSTEMS))


@both
def test_a_passing_gate_runs_nothing_more_and_carries_no_phase_record(tmp_path, system):
    result, calls = _run(tmp_path, system, {"test": OK}, report=_suite(PASSED_CASE))
    assert result["success"] is True and "test_phase" not in result
    assert len(calls) == 1 and calls[0][0] == "test"


@both
@pytest.mark.parametrize("case,counts", [(FAILED_CASE, {"failures": 1}), (ERRORED_CASE, {"errors": 1})])
def test_an_executed_failing_case_is_a_test_execution_failure_without_a_ladder(tmp_path, system, case, counts):
    result, calls = _run(tmp_path, system, {"test": FAIL}, report=_suite(case, **counts))
    phase = result["test_phase"]
    assert result["success"] is False and len(calls) == 1
    assert (phase["phase_class"], phase["basis"], phase["ladder_ran"]) == (te.TEST_EXECUTION_FAILURE, te.BASIS_EXECUTED_FAILED_CASE, False)
    assert [step["phase"] for step in phase["steps"]] == [te.PHASE_TEST]


@both
def test_no_report_and_a_failing_main_compile_is_the_main_compile_phase(tmp_path, system):
    result, calls = _run(tmp_path, system, {"test": {**FAIL, "stdout": "raw test output"}, "main": FAIL})
    phase = result["test_phase"]
    assert (phase["phase_class"], phase["basis"], phase["ladder_ran"]) == (te.MAIN_COMPILE_FAILURE, te.BASIS_STEP_RETURNCODE, True)
    assert calls[1:] == [SYSTEMS[system]["main"]]                       # test-source compilation is never run after a failed main compile
    assert result["success"] is False and result["output"] == "raw test output\n"   # the gate's own failure path is not relabelled


@both
def test_main_compile_passing_and_test_source_compile_failing_is_the_neutral_phase_fact(tmp_path, system):
    result, calls = _run(tmp_path, system, {"test": FAIL, "main": OK, "test_source": FAIL})
    phase = result["test_phase"]
    assert (phase["phase_class"], phase["basis"]) == (te.TEST_SOURCE_COMPILE_FAILURE, te.BASIS_STEP_RETURNCODE)
    assert calls[1:] == [SYSTEMS[system]["main"], SYSTEMS[system]["test_source"]]
    assert [(s["phase"], s["returncode"]) for s in phase["steps"]] == [
        (te.PHASE_TEST, 1), (te.PHASE_MAIN_COMPILE, 0), (te.PHASE_TEST_SOURCE_COMPILE, 1)]


@both
def test_both_compile_phases_passing_without_an_executed_failure_requires_authority(tmp_path, system):
    result, calls = _run(tmp_path, system, {"test": FAIL, "main": OK, "test_source": OK})
    phase = result["test_phase"]
    assert (phase["phase_class"], phase["basis"]) == (te.SEMANTIC_AUTHORITY_REQUIRED, te.BASIS_COMPILE_PHASES_PASSED)
    assert len(calls) == 3


@both
def test_reported_cases_without_an_authoritative_failure_require_authority_without_a_ladder(tmp_path, system):
    """The process failed, the report shows only passing cases: mixed evidence. And a report whose counts contradict its failed
    case is not COMPLETE, so its failed case is not authoritative either."""
    for report in (_suite(PASSED_CASE), _suite(FAILED_CASE, failures=0)):
        result, calls = _run(tmp_path, system, {"test": FAIL}, report=report)
        phase = result["test_phase"]
        assert (phase["phase_class"], phase["basis"]) == (te.SEMANTIC_AUTHORITY_REQUIRED, te.BASIS_REPORT_WITHOUT_AUTHORITATIVE_FAILURE)
        assert len(calls) == 1


PRESERVED = [
    ({"timeout": True}, te.TEST_PHASE_TIMEOUT),
    ({"target_started": False}, te.TEST_PHASE_NOT_STARTED),
    ({"environment_reason_code": "SOME_ENVIRONMENT_CODE"}, te.TEST_PHASE_ENVIRONMENT),
    ({"acquisition_incomplete": True}, te.TEST_PHASE_ACQUISITION_INCOMPLETE),
]


@both
@pytest.mark.parametrize("fact,expected", PRESERVED)
@pytest.mark.parametrize("at", ["test", "main", "test_source"])
def test_a_no_verdict_fact_of_any_step_keeps_its_own_category(tmp_path, system, fact, expected, at):
    """Timeout, never-started, environment and acquisition-incomplete are never read as a compile or test failure, at whichever
    step they occur; a failed-case report next to such a test step does not outrank it."""
    script = {"test": FAIL, "main": OK, "test_source": FAIL}
    script[at] = {**FAIL, **fact}
    result, calls = _run(tmp_path, system, script, report=_suite(FAILED_CASE, failures=1) if at == "test" else None)
    phase = result["test_phase"]
    assert (phase["phase_class"], phase["basis"]) == (expected, te.BASIS_STEP_FACT)
    assert len(calls) == {"test": 1, "main": 2, "test_source": 3}[at]


@both
@pytest.mark.parametrize("at", ["main", "test_source"])
def test_a_raising_diagnostic_step_is_no_proof_and_a_containment_setup_failure_propagates(tmp_path, system, at):
    script = {"test": FAIL, "main": OK, "test_source": FAIL}
    script[at] = RuntimeError("boom")
    phase = _run(tmp_path, system, script)[0]["test_phase"]
    assert (phase["phase_class"], phase["basis"]) == (te.SEMANTIC_AUTHORITY_REQUIRED, te.BASIS_LADDER_EXCEPTION)
    assert phase["steps"][-1]["raised"] is True
    script[at] = ContainmentSetupError("setup")
    with pytest.raises(ContainmentSetupError):
        _run(tmp_path, system, script)


@both
def test_a_malformed_step_result_is_no_proof(tmp_path, system):
    phase = _run(tmp_path, system, {"test": FAIL, "main": {"stdout": "", "stderr": ""}})[0]["test_phase"]
    assert (phase["phase_class"], phase["basis"]) == (te.SEMANTIC_AUTHORITY_REQUIRED, te.BASIS_STEP_RESULT_MALFORMED)


# --- negative controls: text never creates or changes a class; no owner is ever named ---------------------------------------

DIAGNOSTIC_TEXTS = [
    "\n[ERROR] COMPILATION ERROR :\n[INFO] BUILD FAILURE\n",
    "\nerror: cannot find symbol\n  symbol:   method total()\n  location: class a.Ledger\n",
    "\n[ERROR] /w/src/test/java/a/VTest.java:[3,5] cannot find symbol\n",          # a verifier-file-only compiler location
    "\n[ERROR] /w/src/main/java/a/Ledger.java:[7,1] incompatible types\n",         # a production-file compiler location
    "\n> Task :compileTestJava FAILED\nFAILURE: Build failed with an exception.\n",
    "\nTests run: 1, Failures: 1, Errors: 0, Skipped: 0\n",
    "\nCould not resolve dependencies for project g:a:jar:1: Could not find artifact x:y:jar:1\nMAVEN_ACQUISITION_INCOMPLETE: x\n",
]
SCENARIOS = {
    "unknown": ({"test": FAIL, "main": OK, "test_source": OK}, None),
    "executed": ({"test": FAIL}, _suite(FAILED_CASE, failures=1)),
    "mixed": ({"test": FAIL}, _suite(PASSED_CASE)),
    "main": ({"test": FAIL, "main": FAIL}, None),
    "test_source": ({"test": FAIL, "main": OK, "test_source": FAIL}, None),
}


@both
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("text", DIAGNOSTIC_TEXTS)
def test_output_text_cannot_create_or_change_a_phase_class(tmp_path, system, scenario, text):
    """Compiler, build, test-summary and dependency text in every stream of every step: the record is byte-for-byte the record of
    the same typed facts without the text. (Dependency text demotes only upstream, in the acquisition path that sets the typed
    ``acquisition_incomplete`` fact; here, with no typed fact, it changes nothing.)"""
    script, report = SCENARIOS[scenario]
    plain_dir, text_dir = tmp_path / "plain", tmp_path / "text"
    plain_dir.mkdir()
    text_dir.mkdir()
    plain = _run(plain_dir, system, script, report=report)[0]["test_phase"]
    noisy_result, noisy_calls = _run(text_dir, system, script, report=report, text=text)
    assert noisy_result["test_phase"] == plain
    assert len(noisy_calls) == len(plain["steps"])


@both
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_the_record_names_no_owner_defect_or_repair_and_holds_no_text(tmp_path, system, scenario):
    """Closed schema: a compiler location in a verifier file (or a production file) cannot become ownership, because the record
    has no field that could carry one, and no step keeps any output text."""
    script, report = SCENARIOS[scenario]
    phase = _run(tmp_path, system, script, report=report, text=DIAGNOSTIC_TEXTS[2] + DIAGNOSTIC_TEXTS[3])[0]["test_phase"]
    assert set(phase) == {"version", "phase_class", "basis", "ladder_ran", "steps"}
    assert phase["version"] == te.TEST_PHASE_RECORD_VERSION and phase["phase_class"] in te.TEST_PHASE_CLASSES
    for step in phase["steps"]:
        assert set(step) == {"phase", "returncode", "timeout", "target_started", "environment_reason_code", "acquisition_incomplete"}
        assert not any(isinstance(value, str) and ("VTest" in value or "Ledger" in value) for value in step.values())


# --- module selection and the typed acquisition transport ----------------------------------------------------------------------

def test_the_diagnostic_steps_keep_the_module_selection(tmp_path):
    seen = []

    def maven(self, goals, cwd, timeout=300, **_):
        seen.append(list(goals))
        return dict(FAIL if goals[0] != "compile" else OK)

    def gradle(self, cmd, tasks, cwd, **_):
        seen.append(list(tasks))
        return dict(FAIL if not tasks[0].endswith(":classes") else OK)

    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AppConfig().autonomy)
    with patch.object(PolymorphicValidator, "_run_maven_cmd", new=maven), patch.object(PolymorphicValidator, "_run_gradle_cmd", new=gradle):
        assert MAVEN.run_tests(validator, "VTest", module="core")["test_phase"]["phase_class"] == te.TEST_SOURCE_COMPILE_FAILURE
        assert GRADLE.run_tests(validator, "VTest", module="core")["test_phase"]["phase_class"] == te.TEST_SOURCE_COMPILE_FAILURE
    assert seen == [
        ["test", "-pl", "core", "-am", "-Dsurefire.failIfNoSpecifiedTests=false", "-DfailIfNoTests=false", "-Dtest=VTest"],
        ["compile", "-pl", "core", "-am"], ["test-compile", "-pl", "core", "-am"],
        [":core:test", "--tests", "VTest"], [":core:classes"], [":core:testClasses"],
    ]


def test_acquisition_incomplete_is_a_typed_field_and_the_marker_text_stays(tmp_path):
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AppConfig().autonomy)
    maven = validator._acquisition_incomplete(dict(FAIL), ["test"], "why")
    gradle = validator._gradle_acquisition_incomplete(dict(FAIL), ["test"], "why")
    assert maven["acquisition_incomplete"] is True and maven["stderr"].startswith("MAVEN_ACQUISITION_INCOMPLETE:")
    assert gradle["acquisition_incomplete"] is True and gradle["stderr"].startswith("GRADLE_ACQUISITION_INCOMPLETE:")
    assert validator._validation_result(False, "out", maven)["acquisition_incomplete"] is True
    assert "acquisition_incomplete" not in validator._validation_result(False, "out", dict(FAIL))
    # the marker TEXT alone (no typed field) is not the fact: only the acquisition path's own decision sets it
    assert "acquisition_incomplete" not in validator._validation_result(False, "out", {**FAIL, "stderr": maven["stderr"]})

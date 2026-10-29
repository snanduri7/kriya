"""A12 (Wave 2 Step 4): JVM acceptance reads the candidate compile gate's result truthfully.

Only a compile gate that RAN and REJECTED the code may call the candidate's own main code broken
(``ACCEPTANCE_CANDIDATE_COMPILE_FAILED``). A typed no-verdict (``environment_reason_code``) or a result that is no answer at all is
``ACCEPTANCE_ENVIRONMENT_UNAVAILABLE``: nothing about the candidate was observed, nothing runs, and the requirement stays unjudged.
The stand-in validator executes nothing, so this file needs neither Docker nor Maven (the real-Maven twin is test_b2c_jvm_acceptance.py).
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from _b2c_fixtures import ACCEPTANCE, EXACT_GOAL, TARGET, base_revision, candidate, maven_workspace
from test_b2a_acceptance_oracle import _ledger

from kriya.config.config import AutonomyConfig
from kriya.tools.dependency_execution import (
    COMPILE_NO_VERDICT_CODES,
    GRADLE_COMPILE_INDETERMINATE,
    JAVAC_INVOCATION_FAILED,
    MAVEN_COMPILE_INDETERMINATE,
    MAVEN_INVOCATION_FAILED,
    compile_environment_result,
    compile_no_verdict_result,
)
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import acceptance_jvm as jvm
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow.requirements import RequirementOutcome, derive_requirements, requirement_outcomes

GENUINE_LOCATED = json.loads((Path(__file__).resolve().parent / "fixtures" / "maven_compile" / "recorded_maven_compile.json").read_text(encoding="utf-8"))["contained_compile_error_located"]

REJECTED = {"success": False, "output": "COMPILATION ERROR: ';' expected"}
PASSED = {"success": True, "output": "BUILD SUCCESS"}


class _Validator:
    """Executes nothing: ``compiled`` is what the compile gate returns, ``tested`` what the injected class's test run returns."""
    stack = "java"

    def __init__(self, compiled, tested=None):
        self.compiled, self.tested, self.calls = compiled, tested if tested is not None else {"success": True, "output": ""}, []

    def run_compile_check(self, files):
        self.calls.append("compile")
        return self.compiled

    def run_tests(self, **_kwargs):
        self.calls.append("tests")
        return self.tested


@pytest.fixture
def ws(tmp_path):
    return maven_workspace(tmp_path / "ws")


@pytest.fixture
def artifact(tmp_path):
    path = tmp_path / "KriyaAcceptanceTest.java"
    path.write_text(ACCEPTANCE)
    return ao.load_acceptance(str(path), derive_requirements(EXACT_GOAL), str(tmp_path / "artifacts"))


def _run(artifact, ws, tmp_path, validator):
    cand = candidate(ws, tmp_path / "c", None)
    return jvm.run_java_acceptance(artifact, str(cand), candidate_paths=[TARGET], base_revision=base_revision(ws),
                                   validator_factory=lambda _root: validator)


NO_VERDICTS = [
    pytest.param({"success": False, "environment_reason_code": "MAVEN_INVOCATION_FAILED", "output": "docker unreachable"},
                 "MAVEN_INVOCATION_FAILED", id="typed-no-verdict"),
    pytest.param({"success": True, "environment_reason_code": "JAVAC_UNAVAILABLE", "output": "BUILD SUCCESS"},
                 "JAVAC_UNAVAILABLE", id="a-coded-result-is-never-trusted-even-with-success-true"),
    pytest.param(None, "NoneType", id="none-is-only-not-applicable"),
    pytest.param("BUILD SUCCESS", "str", id="not-a-dict"),
]


@pytest.mark.parametrize("compiled, detail", NO_VERDICTS)
def test_a_compile_gate_that_returned_no_verdict_is_not_a_candidate_failure(tmp_path, ws, artifact, compiled, detail):
    validator = _Validator(compiled)
    run = _run(artifact, ws, tmp_path, validator)
    assert run.refusal is not None and run.refusal.reason_code == ao.ACCEPTANCE_ENVIRONMENT_UNAVAILABLE
    assert detail in run.refusal.message
    assert "ordinary candidate failure" not in run.refusal.message and "does not compile" not in run.refusal.message
    assert validator.calls == ["compile"]                      # nothing was injected or run on an unjudged candidate
    assert not run.result and run.report is None                # the refusal is the whole outcome: no test run recorded
    assert run.trust_surface_digest and run.candidate_digest    # the evidence identity is still recorded (result contract, refusal path)
    judgments = ao.judge_acceptance(artifact, run)
    assert {j.code for j in judgments.values()} == {ao.ACCEPTANCE_ENVIRONMENT_UNAVAILABLE}
    assert all(j.evidence["trust_surface_digest"] == run.trust_surface_digest for j in judgments.values())


@pytest.mark.parametrize("compiled, detail", NO_VERDICTS)
def test_a_no_verdict_leaves_the_requirement_unjudged_not_convicted_not_closed(tmp_path, ws, artifact, compiled, detail):
    reqs, ledger = _ledger(EXACT_GOAL)
    [attempt] = ao.close_requirements_with_acceptance(
        ledger, reqs, artifact, test_files=[], execute=lambda a: _run(a, ws, tmp_path, _Validator(compiled)), source="test", revision=1)
    assert attempt["reason_code"] == ao.ACCEPTANCE_ENVIRONMENT_UNAVAILABLE
    assert attempt["closed"] is False and attempt["behavior"] == "indeterminate"
    assert requirement_outcomes(ledger, reqs)["REQ-1"] not in (RequirementOutcome.CLOSED_BY_EVIDENCE, RequirementOutcome.VIOLATED)
    assert detail in attempt["reason"]


def test_a_compile_gate_that_ran_and_rejected_the_code_is_still_a_candidate_failure(tmp_path, ws, artifact):
    validator = _Validator(REJECTED)
    run = _run(artifact, ws, tmp_path, validator)
    assert run.refusal.reason_code == jvm.ACCEPTANCE_CANDIDATE_COMPILE_FAILED
    assert "ordinary candidate failure" in run.refusal.message
    assert validator.calls == ["compile"] and not run.result
    assert {j.code for j in ao.judge_acceptance(artifact, run).values()} == {jvm.ACCEPTANCE_CANDIDATE_COMPILE_FAILED}


def test_a_compile_gate_that_passed_runs_the_injected_class(tmp_path, ws, artifact):
    validator = _Validator(PASSED)
    run = _run(artifact, ws, tmp_path, validator)
    assert run.refusal is None and validator.calls == ["compile", "tests"]


def test_a_no_verdict_from_the_test_run_after_a_passing_compile_is_indeterminate_not_a_candidate_failure(tmp_path, ws, artifact):
    """Pins the neighbouring path as it is: the test gate's own no-verdict has no structured report, so it is unjudged evidence."""
    validator = _Validator(PASSED, {"success": False, "environment_reason_code": "MAVEN_INVOCATION_FAILED", "output": "docker unreachable"})
    run = _run(artifact, ws, tmp_path, validator)
    assert run.refusal is None and validator.calls == ["compile", "tests"]
    codes = {j.code for j in ao.judge_acceptance(artifact, run).values()}
    assert codes == {ao.ACCEPTANCE_EVIDENCE_INDETERMINATE}


# ---------------------------------------------------------------- every typed compile no-verdict the compile paths can return (Maven, Gradle, javac)

TYPED_CODES = sorted(COMPILE_NO_VERDICT_CODES | {MAVEN_INVOCATION_FAILED, MAVEN_COMPILE_INDETERMINATE, GRADLE_COMPILE_INDETERMINATE, JAVAC_INVOCATION_FAILED})


@pytest.mark.parametrize("code", TYPED_CODES)
def test_every_typed_compile_no_verdict_stays_a_no_verdict_through_jvm_acceptance(tmp_path, ws, artifact, code):
    typed = compile_no_verdict_result({"success": False, "output": "x", "environment_reason_code": code}, {"returncode": 1}) if code in COMPILE_NO_VERDICT_CODES \
        else compile_environment_result(code, "x")
    validator = _Validator(typed)
    run = _run(artifact, ws, tmp_path, validator)
    assert run.refusal.reason_code == ao.ACCEPTANCE_ENVIRONMENT_UNAVAILABLE and code in run.refusal.message
    assert validator.calls == ["compile"] and not run.result                       # nothing was injected or run on an unjudged candidate
    assert {j.code for j in ao.judge_acceptance(artifact, run).values()} == {ao.ACCEPTANCE_ENVIRONMENT_UNAVAILABLE}


def test_a_no_verdict_is_not_a_requirement_violation_so_it_never_reaches_a_repair_path(tmp_path, ws, artifact):
    reqs, ledger = _ledger(EXACT_GOAL)
    [attempt] = ao.close_requirements_with_acceptance(
        ledger, reqs, artifact, test_files=[], source="test", revision=1,
        execute=lambda a: _run(a, ws, tmp_path, _Validator(compile_environment_result(MAVEN_INVOCATION_FAILED, "mvn not found"))))
    assert attempt["reason_code"] == ao.ACCEPTANCE_ENVIRONMENT_UNAVAILABLE and attempt["behavior"] == "indeterminate" and attempt["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-1"] not in (RequirementOutcome.VIOLATED, RequirementOutcome.CLOSED_BY_EVIDENCE)


# ------------------------------------------- the production-shaped path: the real validator and the real Maven adapter, only the mvn process seam scripted

def _production_run(artifact, ws, tmp_path, mvn_answer):
    cand = candidate(ws, tmp_path / "c", None)
    with patch.object(PolymorphicValidator, "_run_maven_cmd", lambda self, goals, cwd, *a, **k: dict(mvn_answer)):
        return jvm.run_java_acceptance(
            artifact, str(cand), candidate_paths=[TARGET], base_revision=base_revision(ws),
            validator_factory=lambda root: PolymorphicValidator(root, original_workspace_path=str(ws), autonomy_cfg=AutonomyConfig()))


def test_a_timed_out_maven_compile_is_unavailable_not_a_candidate_failure(tmp_path, ws, artifact):
    run = _production_run(artifact, ws, tmp_path, {"returncode": -1, "stdout": "[INFO] Compiling", "stderr": "", "timeout": True})
    assert run.refusal.reason_code == ao.ACCEPTANCE_ENVIRONMENT_UNAVAILABLE and "MAVEN_COMPILE_TIMED_OUT" in run.refusal.message


def test_a_maven_resolution_failure_is_unavailable_not_a_candidate_failure(tmp_path, ws, artifact):
    offline = ("[ERROR] Failed to execute goal on project a: Could not resolve dependencies for project g:a:jar:1\n"
               "[ERROR] \tCannot access central (https://repo.maven.apache.org/maven2) in offline mode and the artifact org.zzbad:nothing:jar:9 has not been downloaded\n")
    run = _production_run(artifact, ws, tmp_path, {"returncode": 1, "stdout": offline, "stderr": "", "timeout": False})
    assert run.refusal.reason_code == ao.ACCEPTANCE_ENVIRONMENT_UNAVAILABLE and "MAVEN_COMPILE_RESOLUTION_UNVERIFIED" in run.refusal.message


def test_the_real_recorded_located_compiler_error_is_still_a_candidate_failure(tmp_path, ws, artifact):
    """The genuine source-failure control, through the same real adapter: Maven's located javac diagnostic (REAL recorded output) convicts the candidate's own code."""
    run = _production_run(artifact, ws, tmp_path, {"returncode": GENUINE_LOCATED["returncode"], "stdout": GENUINE_LOCATED["output"], "stderr": "", "timeout": False})
    assert run.refusal.reason_code == jvm.ACCEPTANCE_CANDIDATE_COMPILE_FAILED

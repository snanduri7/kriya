"""FS-1C2 B2-c: operator JVM acceptance evidence (Maven + JUnit 5/Surefire) - kriya/workflow/acceptance_jvm.py.

Every acceptance run here is real Maven/Surefire on a small project (tests/_b2c_fixtures.py) whose candidates are git
worktrees of the base workspace, exactly the shape a run's candidate has.
"""
import hashlib
import json
import os
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _b2c_fixtures import (
    ACCEPTANCE,
    CASES,
    EXACT_GOAL,
    EXISTING_TEST,
    GENERAL_GOAL,
    INJECTION,
    POM,
    TARGET,
    base_revision,
    calc_with_clamp,
    candidate,
    maven_workspace,
)
from click.testing import CliRunner
from test_b2a_acceptance_oracle import _ledger

from kriya.config.config import AppConfig, AutonomyConfig
from kriya.tools import test_execution
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import acceptance_jvm as jvm
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow.named_test_oracle import CLOSURE_METHOD, ORACLE_PASSED, OracleJudgment
from kriya.workflow.requirements import (
    BEHAVIOR,
    BEHAVIOR_EXAMPLES,
    BEHAVIOR_GENERAL,
    RequirementOutcome,
    blocking_requirements,
    close_unverified_requirements_with_named_tests,
    derive_requirements,
    record_requirement_claim,
    requirement_evidence,
    requirement_outcomes,
)

pytestmark = pytest.mark.skipif(shutil.which("mvn") is None, reason="real Maven is not installed")
PRODUCTION = {"unknown_policy": "block", "unverified_policy": "block"}


def _artifact(tmp_path, source=ACCEPTANCE, goal=EXACT_GOAL, name="KriyaAcceptanceTest.java"):
    path = tmp_path / name
    path.write_text(source)
    return ao.load_acceptance(str(path), derive_requirements(goal), str(tmp_path / "artifacts"))


def _validator(workspace):
    return lambda root: PolymorphicValidator(root, original_workspace_path=str(workspace), autonomy_cfg=AutonomyConfig())


def _run(artifact, workspace, cand, paths=(TARGET,), base=None):
    return jvm.run_java_acceptance(artifact, str(cand), candidate_paths=list(paths),
                                   base_revision=base if base is not None else base_revision(workspace),
                                   validator_factory=_validator(workspace))


def _judge(artifact, workspace, cand, **kwargs):
    return ao.judge_acceptance(artifact, _run(artifact, workspace, cand, **kwargs))["REQ-1"]


def _close(ledger, reqs, artifact, workspace, cand, test_files=(), paths=(TARGET,)):
    return ao.close_requirements_with_acceptance(
        ledger, reqs, artifact, test_files=list(test_files), execute=lambda a: _run(a, workspace, cand, paths),
        source="test", revision=1)


@pytest.fixture
def ws(tmp_path):
    return maven_workspace(tmp_path / "ws")


# ---------------------------------------------------------------- 1, 2, 3, 5, 6: the artifact and its binding

def test_1_5_6_a_java_artifact_is_validated_digested_stored_outside_the_workspace_and_bound(tmp_path, ws):
    artifact = _artifact(tmp_path)
    assert (artifact.language, artifact.java) == ("java", {
        "package": "demo", "class_name": "KriyaAcceptanceTest", "injection_path": INJECTION})
    assert sorted(case.identity for case in artifact.cases) == CASES
    assert artifact.digest == hashlib.sha256(ACCEPTANCE.encode()).hexdigest()
    assert artifact.requirement_set_digest == derive_requirements(EXACT_GOAL).digest
    assert artifact.stored_path.endswith(f"{artifact.digest}.java")
    assert os.path.relpath(artifact.stored_path, ws).startswith("..")  # 3: outside candidate authority


@pytest.mark.parametrize("source, code", [
    (ACCEPTANCE.replace("REQ-1", "REQ-9", 1), ao.ACCEPTANCE_UNKNOWN_REQUIREMENT),                       # 2
    (ACCEPTANCE.replace("    // kriya_requirement: REQ-1\n", "", 1), ao.ACCEPTANCE_ARTIFACT_INVALID),  # unmarked @Test
    (ACCEPTANCE.replace("    @Test\n    void clampsBelow", "    @Test\n    @Disabled\n    void clampsBelow"),
     ao.ACCEPTANCE_ARTIFACT_INVALID),
    (ACCEPTANCE.replace("@Test", "@ParameterizedTest", 1), ao.ACCEPTANCE_ARTIFACT_INVALID),
    (ACCEPTANCE.replace("package demo;\n", ""), ao.ACCEPTANCE_ARTIFACT_INVALID),
    (ACCEPTANCE + "\nclass Second {}\n", ao.ACCEPTANCE_ARTIFACT_INVALID),
    (ACCEPTANCE.replace("    // kriya_requirement: REQ-1\n    @Test\n    void clampsAbove",
                        "    // kriya_requirement: REQ-1\n    void clampsAbove"), ao.ACCEPTANCE_ARTIFACT_INVALID),
    (ACCEPTANCE.replace("REQ-1", "first requirement", 1), ao.ACCEPTANCE_ARTIFACT_INVALID),
])
def test_2_an_unknown_requirement_or_a_case_that_could_skip_or_multiply_is_refused_before_generation(
        tmp_path, source, code):
    with pytest.raises(ao.AcceptanceError) as refused:
        _artifact(tmp_path, source)
    assert refused.value.reason_code == code


def test_2_a_scope_only_requirement_cannot_be_a_jvm_behaviour_target(tmp_path):
    goal = EXACT_GOAL + "- Do not modify any other file.\n"
    with pytest.raises(ao.AcceptanceError) as refused:
        _artifact(tmp_path, ACCEPTANCE.replace("REQ-1", "REQ-2"), goal)
    assert refused.value.reason_code == ao.ACCEPTANCE_REQUIREMENT_NOT_BEHAVIORAL


def test_1_the_cli_binds_a_java_artifact_before_the_workflow_starts(tmp_path, monkeypatch):
    from _strict_doubles import strict_kernel

    monkeypatch.chdir(tmp_path)
    (tmp_path / "KriyaAcceptanceTest.java").write_text(ACCEPTANCE)
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    dispatch = AsyncMock(return_value={"status": "success", "run_id": "r", "quality_gates_passed": True})
    with patch("kriya.cli.load_config", return_value=cfg), patch("kriya.cli.LLMClient", autospec=True), \
         patch("kriya.cli.Kernel", return_value=strict_kernel(cfg)), patch("kriya.cli.WorkflowEngine") as engine, \
         patch("kriya.cli._learned_reference_context", new=AsyncMock(return_value="")), \
         patch("kriya.cli._dispatch_generation", new=dispatch):
        result = CliRunner().invoke(__import__("kriya.cli", fromlist=["main"]).main,
                                    ["generate", EXACT_GOAL, "-y", "--acceptance", str(tmp_path / "KriyaAcceptanceTest.java")])
    assert result.exit_code == 0, result.output
    assert engine.return_value.acceptance.language == "java"


# ---------------------------------------------------------------- J1-J5: claim semantics (B2-COV unchanged)

def test_j1_8_a_correct_candidate_closes_an_exact_claim(tmp_path, ws):
    cand = candidate(ws, tmp_path / "c", "correct")
    reqs, ledger = _ledger(EXACT_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path), ws, cand)
    assert (attempt["reason_code"], attempt["strength"], attempt["closed"]) == (ao.ACCEPTANCE_PASSED, "EXACT", True)
    assert attempt["case_results"] == {case: ["passed"] for case in CASES}
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    evidence = requirement_evidence(ledger, reqs)["REQ-1"]["claims"][BEHAVIOR]
    assert evidence["runner"] == "maven" and evidence["trust_surface_digest"] and evidence["injection_path"] == INJECTION
    assert evidence["acceptance_digest"] and evidence["candidate_digest"] and evidence["report_files"]
    assert not (cand / INJECTION).exists()  # never injected into the candidate's tree


@pytest.mark.parametrize("variant", ["wrong", "throws"])
def test_j2_j3_9_10_a_wrong_result_or_a_candidate_exception_is_violated(tmp_path, ws, variant):
    cand = candidate(ws, tmp_path / "c", variant)
    reqs, ledger = _ledger(EXACT_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path), ws, cand)
    status = "failed" if variant == "wrong" else "error"
    assert attempt["case_results"] == {case: [status] for case in CASES}
    assert attempt["reason_code"] == ao.ACCEPTANCE_VIOLATED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED


def test_j4_11_finite_passing_cases_leave_a_general_claim_unverified(tmp_path, ws):
    cand = candidate(ws, tmp_path / "c", "correct")
    reqs, ledger = _ledger(GENERAL_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, goal=GENERAL_GOAL), ws, cand)
    assert (attempt["reason_code"], attempt["strength"]) == (ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN, BEHAVIOR_GENERAL)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    claims = requirement_evidence(ledger, reqs)["REQ-1"]["claims"]
    assert claims[BEHAVIOR] is None and claims[BEHAVIOR_EXAMPLES]["reason_code"] == ao.ACCEPTANCE_PASSED


def test_j5_12_one_counterexample_violates_a_general_claim(tmp_path, ws):
    off_by_one = calc_with_clamp("correct").replace("return Math.max(min, Math.min(max, value));",
                                                     "return value > max ? max - 1 : Math.max(min, value);")
    cand = candidate(ws, tmp_path / "c", None, {TARGET: off_by_one})
    reqs, ledger = _ledger(GENERAL_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, goal=GENERAL_GOAL), ws, cand)
    assert attempt["case_results"]["demo.KriyaAcceptanceTest.clampsAboveTheMaximum"] == ["failed"]
    assert attempt["case_results"]["demo.KriyaAcceptanceTest.clampsBelowTheMinimum"] == ["passed"]
    assert attempt["reason_code"] == ao.ACCEPTANCE_VIOLATED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED


# ---------------------------------------------------------------- J6, 13, harness/infrastructure: never VIOLATED

def test_j6_13_an_expected_identity_that_did_not_execute_is_not_a_pass(tmp_path, ws):
    cand = candidate(ws, tmp_path / "c", "correct")
    artifact = _artifact(tmp_path)
    run = _run(artifact, ws, cand)
    run.report.cases = [c for c in run.report.cases if c.name != "clampsAboveTheMaximum"]
    run.case_details.pop("demo.KriyaAcceptanceTest.clampsAboveTheMaximum")
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == ao.ACCEPTANCE_IDENTITY_NOT_EXECUTED


def test_a_harness_that_does_not_compile_is_not_a_violation(tmp_path, ws):
    cand = candidate(ws, tmp_path / "c", None)  # clamp missing: the operator class cannot compile
    judgment = _judge(_artifact(tmp_path), ws, cand)
    assert judgment.code == jvm.ACCEPTANCE_HARNESS_COMPILE_FAILED


def test_a_build_rejected_before_the_tests_ran_is_indeterminate_and_names_the_goal(tmp_path, ws):
    """Measured on commons-cli/commons-lang: Apache RAT (validate phase) rejects an operator class
    without the repository's license header; no test runs, so there is no evidence either way."""
    cand = candidate(ws, tmp_path / "c", "wrong")
    artifact = _artifact(tmp_path)
    rejected = {"success": False, "output": "[ERROR] Failed to execute goal org.apache.rat:apache-rat-plugin:0.18:check "
                "(rat-check) on project demo: Counter(s) UNAPPROVED exceeded", "returncode": 1}
    with patch("kriya.capabilities.maven.MavenBuildAdapter.run_tests", return_value=rejected):
        judgment = ao.judge_acceptance(artifact, _run(artifact, ws, cand))["REQ-1"]
    assert judgment.code == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE
    assert "org.apache.rat:apache-rat-plugin:0.18:check" in judgment.reason


def test_an_assertion_in_a_class_that_never_references_candidate_code_is_not_a_violation(tmp_path, ws):
    source = ACCEPTANCE.replace("assertEquals(1, Calc.clamp(0, 1, 5))", "assertEquals(1, 2)").replace(
        "assertEquals(5, Calc.clamp(9, 1, 5))", "assertEquals(5, 6)")
    cand = candidate(ws, tmp_path / "c", "correct")
    assert _judge(_artifact(tmp_path, source), ws, cand).code == ao.ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION


def test_a_candidate_whose_own_code_does_not_compile_is_an_ordinary_failure_not_evidence(tmp_path, ws):
    cand = candidate(ws, tmp_path / "c", None, {TARGET: calc_with_clamp("correct").replace("return Math", "retur Math")})
    judgment = _judge(_artifact(tmp_path), ws, cand)
    assert judgment.code == jvm.ACCEPTANCE_CANDIDATE_COMPILE_FAILED


def test_an_exception_only_in_the_acceptance_code_is_not_a_violation(tmp_path, ws):
    source = ACCEPTANCE.replace("        assertEquals(1, Calc.clamp(0, 1, 5));",
                                "        throw new UnsupportedOperationException(\"harness\");")
    cand = candidate(ws, tmp_path / "c", "wrong")
    run = _run(_artifact(tmp_path, source), ws, cand)
    judgment = ao.judge_acceptance(_artifact(tmp_path, source), run)["REQ-1"]
    # the other case still observes the wrong result: that alone decides
    assert judgment.code == ao.ACCEPTANCE_VIOLATED
    assert judgment.evidence["case_results"]["demo.KriyaAcceptanceTest.clampsBelowTheMinimum"] == ["error"]
    only_harness = ACCEPTANCE.split("    // kriya_requirement: REQ-1\n    @Test\n    void clampsAbove")[0].replace(
        "        assertEquals(1, Calc.clamp(0, 1, 5));", "        throw new UnsupportedOperationException(\"harness\");"
    ) + "}\n"
    assert ao.judge_acceptance(_artifact(tmp_path, only_harness, name="H.java"),
                               _run(_artifact(tmp_path, only_harness, name="H.java"), ws, cand))["REQ-1"].code == (
        ao.ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION)


def _complete_run(tmp_path, ws):
    cand = candidate(ws, tmp_path / "c", "correct")
    artifact = _artifact(tmp_path)
    run = _run(artifact, ws, cand)
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == ao.ACCEPTANCE_PASSED
    return artifact, run, cand


@pytest.mark.parametrize("damage", [
    lambda run: setattr(run, "report", None),
    lambda run: setattr(run.report, "completeness", test_execution.INDETERMINATE),
    lambda run: setattr(run.report, "runner", "gradle"),
    lambda run: setattr(run, "case_details", None),
    lambda run: run.case_details.update({CASES[0]: [{"status": "failed", "type": "", "trace": ""}]}),
])
def test_15_malformed_missing_or_inconsistent_evidence_cannot_close(tmp_path, ws, damage):
    artifact, run, _ = _complete_run(tmp_path, ws)
    damage(run)
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE


def test_j9_14_a_stale_report_never_authorizes_closure(tmp_path, ws):
    """A passing Surefire report of an earlier invocation sits in the
    candidate's target/; the runner process 'succeeds' without writing one."""
    cand = candidate(ws, tmp_path / "c", "correct")
    reports = cand / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-demo.KriyaAcceptanceTest.xml").write_text(
        '<testsuite>' + "".join(f'<testcase classname="demo.KriyaAcceptanceTest" name="{c.rsplit(".", 1)[1]}"/>'
                                for c in CASES) + '</testsuite>')
    artifact = _artifact(tmp_path)
    real = PolymorphicValidator.run_compile_check
    with patch.object(PolymorphicValidator, "run_compile_check", new=real), \
         patch("kriya.capabilities.maven.MavenBuildAdapter.run_tests",
               return_value={"success": True, "output": "BUILD SUCCESS"}):
        run = _run(artifact, ws, cand)
    judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
    assert judgment.code == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE and "TEST_PROCESS_NOT_RUN" in judgment.reason


def test_a_report_file_changed_after_the_run_is_not_read(tmp_path, ws):
    cand = candidate(ws, tmp_path / "c", "correct")
    artifact = _artifact(tmp_path)
    original = jvm._case_details

    def tampered(workspace, report):
        path = os.path.join(workspace, report.report_files[0]["path"])
        with open(path, "a") as handle:
            handle.write("<!-- rewritten -->")
        return original(workspace, report)

    with patch.object(jvm, "_case_details", new=tampered):
        run = _run(artifact, ws, cand)
    assert run.case_details is None
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE


# ---------------------------------------------------------------- J7, J8, 16, 17: trust surface

@pytest.mark.parametrize("extra", [
    {"pom.xml": POM.replace("<version>3.2.5</version>", "<version>3.2.5</version><configuration>"
                            "<testFailureIgnore>true</testFailureIgnore></configuration>")},          # J7
    {"src/test/java/demo/CalcTest.java": EXISTING_TEST.replace("assertEquals(4", "assertEquals(4 + 0")},  # J8
    {"src/test/java/demo/Helper.java": "package demo;\nclass Helper {}\n"},                          # J8: new test-side file
    {"src/test/resources/junit-platform.properties": "junit.jupiter.execution.parallel.enabled=true\n"},
    {"src/main/resources/META-INF/services/org.junit.platform.launcher.TestExecutionListener": "demo.L\n"},
    {".mvn/maven.config": "-Dmaven.test.failure.ignore=true\n"},
    {"target/classes/demo/Calc.class": "not a class"},                                                # output root write
])
def test_j7_j8_16_17_a_changed_build_or_test_surface_prevents_any_acceptance_evidence(tmp_path, ws, extra):
    cand = candidate(ws, tmp_path / "c", "correct", extra)
    reqs, ledger = _ledger(EXACT_GOAL)
    with patch.object(PolymorphicValidator, "run_tests") as tests:
        [attempt] = _close(ledger, reqs, _artifact(tmp_path), ws, cand, paths=[TARGET, *extra])
    assert tests.call_count == 0
    assert attempt["reason_code"] == jvm.ACCEPTANCE_TRUST_SURFACE_CHANGED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_no_base_revision_is_no_trust_anchor(tmp_path, ws):
    cand = candidate(ws, tmp_path / "c", "correct")
    run = jvm.run_java_acceptance(_artifact(tmp_path), str(cand), candidate_paths=[TARGET], base_revision=None,
                                  validator_factory=_validator(ws))
    assert run.refusal.reason_code == jvm.ACCEPTANCE_TRUST_SURFACE_UNAVAILABLE


def test_a_project_without_a_root_build_script_is_refused(tmp_path):
    """Gradle projects are supported since BACKEND-READINESS-004 (tests/test_backend_readiness_004_gradle_acceptance.py);
    a tree with neither a pom.xml nor a build.gradle[.kts] at the root has no JVM runner."""
    from _b2c_fixtures import _git

    bare = tmp_path / "g"
    (bare / "src/main/java/demo").mkdir(parents=True)
    (bare / "app" ).mkdir()
    (bare / "app" / "build.gradle").write_text("plugins { id 'java' }\n")  # nested only: not a root build
    (bare / TARGET).write_text(calc_with_clamp("correct"))
    _git(bare, "init", "-q")
    _git(bare, "add", "-A")
    _git(bare, "commit", "-q", "-m", "base")
    run = jvm.run_java_acceptance(_artifact(tmp_path), str(bare), candidate_paths=[TARGET],
                                  base_revision=base_revision(bare), validator_factory=_validator(bare))
    assert run.refusal.reason_code == "ACCEPTANCE_RUNNER_UNSUPPORTED" and run.runner is None
    assert jvm.detect_jvm_runner(str(bare)) is None


# ---------------------------------------------------------------- J10, 18: the injection path

@pytest.mark.parametrize("where", ["candidate", "base"])
def test_j10_18_an_existing_file_at_the_injection_path_is_refused_never_overwritten(tmp_path, ws, where):
    mine = "package demo;\nclass KriyaAcceptanceTest { /* the candidate's own */ }\n"
    if where == "base":
        (ws / INJECTION).parent.mkdir(parents=True, exist_ok=True)
        (ws / INJECTION).write_text(mine)
        from _b2c_fixtures import _git

        _git(ws, "add", "-A")
        _git(ws, "commit", "-q", "-m", "collision")
        cand = candidate(ws, tmp_path / "c", "correct")
    else:
        cand = candidate(ws, tmp_path / "c", "correct", {INJECTION: mine})
    with patch.object(PolymorphicValidator, "run_compile_check") as compiled, \
         patch.object(PolymorphicValidator, "run_tests") as tested:
        run = _run(_artifact(tmp_path), ws, cand, paths=[TARGET] + ([INJECTION] if where == "candidate" else []))
    assert compiled.call_count == 0 and tested.call_count == 0  # refused before any repository code runs
    assert run.refusal.reason_code in (jvm.ACCEPTANCE_PATH_COLLISION, jvm.ACCEPTANCE_TRUST_SURFACE_CHANGED)
    assert run.refusal.reason_code == (jvm.ACCEPTANCE_TRUST_SURFACE_CHANGED if where == "candidate"
                                       else jvm.ACCEPTANCE_PATH_COLLISION)
    assert (cand / INJECTION).read_text() == mine


def test_the_injected_class_changed_during_the_run_is_indeterminate(tmp_path, ws):
    cand = candidate(ws, tmp_path / "c", "correct")
    artifact = _artifact(tmp_path)
    original = PolymorphicValidator.run_tests

    def rewriting(self, target_test=None):
        result = original(self, target_test)
        with open(os.path.join(self.workspace_path, INJECTION), "a") as handle:
            handle.write("// rewritten\n")
        return result

    with patch.object(PolymorphicValidator, "run_tests", new=rewriting):
        run = _run(artifact, ws, cand)
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == "ACCEPTANCE_ARTIFACT_CHANGED"


# ---------------------------------------------------------------- 4, 19, 20, 21, 22: nothing else closes behaviour

def test_4_evidence_for_another_candidate_never_closes_this_one(tmp_path, ws):
    from kriya.workflow.requirements import record_requirement_verdicts

    cand = candidate(ws, tmp_path / "c", "correct")
    reqs, ledger = _ledger(EXACT_GOAL, candidate="cand-A")
    _close(ledger, reqs, _artifact(tmp_path), ws, cand)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.SATISFIED, "m")}, revision=2,
                                evidence_fingerprint="cand-B", source="test")
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_19_a_model_satisfied_verdict_never_replaces_missing_jvm_evidence(tmp_path, ws):
    cand = candidate(ws, tmp_path / "c", None)  # harness cannot compile: no evidence
    reqs, ledger = _ledger(EXACT_GOAL, verdict=RequirementOutcome.SATISFIED)
    _close(ledger, reqs, _artifact(tmp_path), ws, cand)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, **PRODUCTION)] == ["REQ-1"]


def test_20_candidate_written_junit_tests_never_close_behaviour(tmp_path, ws):
    proof = EXISTING_TEST.replace("class CalcTest", "class ClampTest").replace(
        "assertEquals(4, Calc.twice(2))", "assertEquals(1, Calc.clamp(0, 1, 5))")
    cand = candidate(ws, tmp_path / "c", "correct", {"src/test/java/demo/ClampTest.java": proof})
    reqs, ledger = _ledger(EXACT_GOAL)
    assert _close(ledger, reqs, None, ws, cand) == []  # no operator file: no oracle at all
    [attempt] = _close(ledger, reqs, _artifact(tmp_path), ws, cand,
                       paths=[TARGET, "src/test/java/demo/ClampTest.java"])
    assert attempt["reason_code"] == jvm.ACCEPTANCE_TRUST_SURFACE_CHANGED  # a candidate test is never trusted
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    with pytest.raises(ValueError):
        record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="named_test_oracle",
                                 detail={}, source="test", revision=1)


def test_21_c0_regression_evidence_never_closes_behaviour(tmp_path):
    goal = EXACT_GOAL.replace(".\n", ", keeping CalcTest passing.\n")
    reqs, ledger = _ledger(goal)
    [named] = close_unverified_requirements_with_named_tests(
        ledger, reqs, test_files=["src/test/java/demo/CalcTest.java"], modified=[TARGET],
        judge=lambda named: OracleJudgment(ORACLE_PASSED, "", {"method": CLOSURE_METHOD, "tests": list(named)}),
        source="test", revision=1)
    assert named["regression_preserved"] is True and named["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_22_a_changed_or_withdrawn_artifact_supersedes_earlier_jvm_evidence(tmp_path, ws):
    from kriya.workflow.resume_fingerprints import generation_resume_fingerprints

    cand = candidate(ws, tmp_path / "c", "correct")
    reqs, ledger = _ledger(EXACT_GOAL)
    _close(ledger, reqs, _artifact(tmp_path), ws, cand)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    stricter = ACCEPTANCE.replace("assertEquals(5, Calc.clamp(9, 1, 5))", "assertEquals(6, Calc.clamp(9, 1, 5))")
    [changed] = _close(ledger, reqs, _artifact(tmp_path, stricter, name="v2.java"), ws, cand)
    assert changed["reason_code"] == ao.ACCEPTANCE_VIOLATED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED
    [withdrawn] = _close(ledger, reqs, None, ws, cand)
    assert withdrawn["reason_code"] == ao.ACCEPTANCE_SUPERSEDED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    a = generation_resume_fingerprints(cfg, str(ws), goal=EXACT_GOAL, acceptance_digest=_artifact(tmp_path).digest)
    b = generation_resume_fingerprints(cfg, str(ws), goal=EXACT_GOAL, acceptance_digest="0" * 64)
    assert a["verification_policy"] != b["verification_policy"]


# ---------------------------------------------------------------- 23, 24, 25: the run paths

@pytest.mark.parametrize("variant, outcome", [("correct", "closed_by_evidence"), ("wrong", "violated")])
@pytest.mark.asyncio
async def test_23_direct_path(tmp_path, variant, outcome):
    """The real direct workflow (scripted Developer writes Calc.java); every
    gate but the closure stubbed; the closure runs real Maven."""
    from test_prd020_requirement_lineage import _verdicts_json

    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    # PRD-020's _engine, with the plan naming this project's file.
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = True
    cfg.autonomy.requirement_unknown_policy = "block"
    cfg.autonomy.requirement_unverified_policy = "block"
    cfg.llm_chain = []
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        system = system_prompt or ""
        if "Goal Spec Compliance Checker" in system:
            return _verdicts_json(user_prompt)
        if "Kriya Planner Agent" in system:
            return f"Step 1: create {TARGET} with clamp (REQ-1)"
        if "Kriya Architect Agent" in system:
            return f"Design: {TARGET} exposes clamp (REQ-1)"
        return "Review: Approved"

    llm.complete = complete
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    workspace = maven_workspace(tmp_path / "ws", with_calc=False)
    engine.developer.run_generation = AsyncMock(return_value=[{"filepath": TARGET, "content": calc_with_clamp(variant)}])
    engine.acceptance = _artifact(tmp_path)
    real_tests, real_compile = PolymorphicValidator.run_tests, PolymorphicValidator.run_compile_check

    def acceptance_only(real, stub):
        # The generation gates are stubbed; Kriya's acceptance staging runs real Maven.
        def run(self, *args, **kwargs):
            if f"{os.sep}{jvm._STAGING_DIR}{os.sep}" in self.workspace_path:  # pylint: disable=protected-access
                return real(self, *args, **kwargs)
            return stub
        return run

    with patch.object(PolymorphicValidator, "run_tests",
                      new=acceptance_only(real_tests, {"success": True, "output": ""})), \
         patch.object(PolymorphicValidator, "run_compile_check",
                      new=acceptance_only(real_compile, {"success": True, "output": ""})):
        res = await engine.run_generation_workflow(goal=EXACT_GOAL, workspace_path=str(workspace))
    from test_prd020_requirement_lineage import _events

    closures = [c for e in _events(cfg, "requirement.closure") for c in e["closures"] if c.get("kind") == ao.ACCEPTANCE_METHOD]
    assert closures and closures[-1]["reason_code"] == (ao.ACCEPTANCE_PASSED if outcome == "closed_by_evidence"
                                                        else ao.ACCEPTANCE_VIOLATED), json.dumps(closures, default=str)[:2000]
    assert res["requirements"]["outcomes"]["REQ-1"] == outcome, json.dumps(
        {k: (v if not isinstance(v, (dict, list)) or len(str(v)) < 600 else str(v)[:600]) for k, v in res.items() if any(w in k for w in ("fail", "error", "gate", "reason", "message", "diagnos"))}, default=str)[:3000]
    assert res["quality_gates_passed"] is (outcome == "closed_by_evidence")
    assert (workspace / TARGET).exists() is (outcome == "closed_by_evidence")  # never applied without success
    assert not (workspace / INJECTION).exists()


@pytest.mark.parametrize("variant, outcome", [("correct", "closed_by_evidence"), ("throws", "violated")])
@pytest.mark.asyncio
async def test_24_enforce_terminal_path(tmp_path, monkeypatch, variant, outcome):
    from test_prd020_requirement_lineage import _verdicts_json

    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.plan_schema import (
        ChangeKind,
        EngineeringPlan,
        ExecutionMethod,
        FileAction,
        PlannedFile,
        Subtask,
    )
    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    workspace = maven_workspace(tmp_path / "ws")
    monkeypatch.setattr(wc, "create_git_worktree", lambda path: path)
    plan = EngineeringPlan(plan_id="b2c", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="clamp", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path=TARGET, action=FileAction.MODIFY)], requirement_ids=["REQ-1"])])
    cfg = AppConfig()
    cfg.autonomy.spec_compliance_enabled = True
    cfg.autonomy.requirement_unknown_policy = "block"
    cfg.autonomy.requirement_unverified_policy = "block"
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT))
    we.kernel = SimpleNamespace(config=cfg)
    we.acceptance = _artifact(tmp_path)

    async def check(**kwargs):
        prompt = "\n".join(f"{r.id}: {r.text}" for r in kwargs["requirements"].requirements)
        return json.loads(_verdicts_json(prompt))

    we.spec_compliance.check = check

    async def fake_run(**kwargs):
        (workspace / TARGET).write_text(calc_with_clamp(variant))
        return {"status": "success", "quality_gates_passed": True, "files": [TARGET]}

    we.run_generation_workflow = fake_run
    we.planner.run = AsyncMock(return_value="plan")
    with patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        result = await wc.WorkflowController(we).execute(EXACT_GOAL, str(workspace), migration_mode="enforce")
    assert result.legacy_result["requirements"]["outcomes"] == {"REQ-1": outcome}
    gap = result.legacy_result.get("global_requirement_gap")
    assert (not gap) is (outcome == "closed_by_evidence"), gap


def test_25_milestone_cli_binds_a_java_artifact_to_the_plans_original_goal(tmp_path, monkeypatch):
    """`generate --from-milestones --acceptance`: ids come from the plan's
    original goal (the integration unit verifies them - the same closure as
    the direct path)."""
    from _strict_doubles import strict_kernel

    monkeypatch.chdir(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"group_id": "g", "original_goal": EXACT_GOAL, "milestones": []}))
    (tmp_path / "KriyaAcceptanceTest.java").write_text(ACCEPTANCE)
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    dispatch = AsyncMock(return_value={"status": "success"})
    with patch("kriya.cli.load_config", return_value=cfg), patch("kriya.cli.LLMClient", autospec=True), \
         patch("kriya.cli.Kernel", return_value=strict_kernel(cfg)), patch("kriya.cli.WorkflowEngine") as engine, \
         patch("kriya.cli._learned_reference_context", new=AsyncMock(return_value="")), \
         patch("kriya.cli._dispatch_milestones", new=dispatch), \
         patch("kriya.workflow.milestones.load_or_resume_milestone_run_state", return_value=MagicMock(original_goal=EXACT_GOAL)):
        result = CliRunner().invoke(__import__("kriya.cli", fromlist=["main"]).main, [
            "generate", "--from-milestones", str(plan), "-y", "--acceptance", str(tmp_path / "KriyaAcceptanceTest.java")])
    assert result.exit_code == 0, result.output
    bound = engine.return_value.acceptance
    assert bound.language == "java" and bound.requirement_set_digest == derive_requirements(EXACT_GOAL).digest


# ---------------------------------------------------------------- 26: Python B2-a unchanged

def test_26_python_artifacts_keep_their_runner_and_contract(tmp_path):
    from _b2a_fixtures import CALC_ACCEPTANCE, CALC_GOAL

    path = tmp_path / "acceptance.py"
    path.write_text(CALC_ACCEPTANCE)
    artifact = ao.load_acceptance(str(path), derive_requirements(CALC_GOAL), str(tmp_path / "st"))
    assert (artifact.language, artifact.java, artifact.stored_path.endswith(".py")) == ("python", None, True)
    with pytest.raises(ao.AcceptanceError) as refused:
        (tmp_path / "acceptance.txt").write_text(CALC_ACCEPTANCE)
        ao.load_acceptance(str(tmp_path / "acceptance.txt"), derive_requirements(CALC_GOAL), str(tmp_path / "st"))
    assert refused.value.reason_code == ao.ACCEPTANCE_ARTIFACT_INVALID

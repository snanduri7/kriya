"""GRADLE-JVM-ACCEPTANCE-001 (BACKEND-READINESS-004): B2-c JVM acceptance through the Gradle test gate.

The runner is detected once per run from the root build script (pom.xml -> maven, build.gradle[.kts] -> gradle) and
sealed on every judgment (runner, runner_contract_digest); the operator class runs through the ordinary Gradle test
gate (``test --tests <Class>``) with the FS-1A fresh report binding on build/test-results; the same trust-surface,
collision, integrity and judgment rules as Maven. A report from the other runner, an incomplete report or a harness
compilation failure never closes. The approval (B3) binds the detected runner's contract digest.

The Gradle build is scripted (no Gradle distribution in the suite): the validator double runs the REAL FS-1A report
binding over JUnit XML it writes exactly where Gradle writes it, so what is exercised is Kriya's reading of a Gradle
report, not Gradle itself.
"""
import json
import os
from pathlib import Path

from _b2c_fixtures import ACCEPTANCE, BASE_CALC, CASES, EXACT_GOAL, GENERAL_GOAL, INJECTION, TARGET, _git, base_revision

from kriya.tools import test_execution
from kriya.workflow import acceptance_approval as b3
from kriya.workflow import acceptance_jvm as jvm
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow.requirements import derive_requirements

GRADLE_SCRIPT = "plugins { id 'java' }\nrepositories { mavenCentral() }\n"


def _workspace(root: Path, script_name="build.gradle"):
    (root / "src/main/java/demo").mkdir(parents=True)
    (root / script_name).write_text(GRADLE_SCRIPT)
    (root / "settings.gradle").write_text("rootProject.name = 'demo'\n")
    (root / TARGET).write_text(BASE_CALC)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _artifact(tmp_path, goal=EXACT_GOAL):
    path = tmp_path / "KriyaAcceptanceTest.java"
    path.write_text(ACCEPTANCE)
    return ao.load_acceptance(str(path), derive_requirements(goal), str(tmp_path / "artifacts"))


def _xml(cases):
    """A Gradle JUnit XML report: cases -> (status, failure type) per method."""
    body = ""
    for name, (status, kind) in cases.items():
        if status == "passed":
            body += f'  <testcase name="{name}" classname="demo.KriyaAcceptanceTest" time="0.01"/>\n'
        elif status == "failed":
            body += (f'  <testcase name="{name}" classname="demo.KriyaAcceptanceTest" time="0.01">\n'
                     f'    <failure message="expected: &lt;1&gt; but was: &lt;0&gt;" type="{kind}">{kind}: expected 1\n'
                     f'\tat demo.KriyaAcceptanceTest.{name}(KriyaAcceptanceTest.java:9)\n\tat demo.Calc.clamp(Calc.java:5)\n</failure>\n  </testcase>\n')
    n = len(cases)
    failures = sum(1 for s, _ in cases.values() if s == "failed")
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="demo.KriyaAcceptanceTest" tests="{n}" skipped="0" '
            f'failures="{failures}" errors="0" timestamp="2026-10-08T10:00:00" time="0.05">\n{body}</testsuite>\n')


class _GradleDouble:
    """The validator the acceptance run builds for its scratch export: a Gradle project whose test gate produces
    the JUnit XML scripted for this run through the real FS-1A binding."""

    stack = "java"

    def __init__(self, root, cases, *, output="BUILD SUCCESSFUL", report=True, runner="gradle"):
        self.workspace_path = root
        self.cases, self.output, self.report, self.runner = cases, output, report, runner
        self.calls = []

    def run_compile_check(self, files):
        self.calls.append(("compile", list(files)))
        return {"success": True, "output": "ok"}

    def run_tests(self, target_test=None):
        self.calls.append(("test", target_test))
        binding = test_execution.prepare(self.workspace_path, self.runner)
        if self.report:
            report_dir = os.path.join(self.workspace_path, "build", "test-results", "test")
            os.makedirs(report_dir, exist_ok=True)
            with open(os.path.join(report_dir, "TEST-demo.KriyaAcceptanceTest.xml"), "w", encoding="utf-8") as handle:
                handle.write(_xml(self.cases))
        result = {"success": "FAILED" not in self.output, "output": self.output, "returncode": 0}
        binding.observe(result)
        result["test_execution"] = test_execution.collect(binding).to_dict()
        return result


def _run(tmp_path, ws, double_factory, goal=EXACT_GOAL):
    tmp_path.mkdir(parents=True, exist_ok=True)
    artifact = _artifact(tmp_path, goal)
    made = []

    def factory(root):
        double = double_factory(root)
        made.append(double)
        return double
    run = jvm.run_java_acceptance(artifact, str(ws), candidate_paths=[TARGET], base_revision=base_revision(ws),
                                  validator_factory=factory)
    return artifact, run, made


def test_01_a_gradle_project_runs_the_operator_class_through_the_gradle_gate_and_closes_an_exact_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    ws = _workspace(tmp_path / "ws")
    passing = {"clampsBelowTheMinimum": ("passed", None), "clampsAboveTheMaximum": ("passed", None)}
    artifact, run, [double] = _run(tmp_path, ws, lambda root: _GradleDouble(root, passing))
    assert run.refusal is None and run.runner == "gradle" and run.report is not None and run.report.complete
    assert run.report.runner == "gradle" and double.calls[1] == ("test", INJECTION)
    assert not os.path.lexists(ws / INJECTION)  # never injected into the real workspace
    judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
    assert judgment.passed and judgment.evidence["runner"] == "gradle"
    assert judgment.evidence["runner_contract_digest"] == jvm.runner_source_digest("gradle") != jvm.RUNNER_SOURCE_DIGEST
    assert sorted(judgment.evidence["cases"]) == CASES


def test_02_a_contradiction_is_violated_and_an_incomplete_or_foreign_report_is_indeterminate(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    ws = _workspace(tmp_path / "ws", "build.gradle.kts")  # GRADLE-KOTLIN-DSL-001: a Kotlin-DSL root is Gradle too
    failing = {"clampsBelowTheMinimum": ("failed", "org.opentest4j.AssertionFailedError"), "clampsAboveTheMaximum": ("passed", None)}
    artifact, run, _ = _run(tmp_path, ws, lambda root: _GradleDouble(root, failing))
    judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
    assert judgment.violated and "clampsBelowTheMinimum" in judgment.reason
    # no report at all (the build never ran the tests): INDETERMINATE naming the failed task, never a verdict
    artifact, run, _ = _run(tmp_path / "b", _workspace(tmp_path / "b" / "ws"),
                            lambda root: _GradleDouble(root, {}, output="> Task :checkstyleMain FAILED\nBUILD FAILED", report=False))
    judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
    assert judgment.code == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE and ":checkstyleMain" in judgment.reason
    # the operator class did not compile against the candidate: harness, not a violation
    artifact, run, _ = _run(tmp_path / "c", _workspace(tmp_path / "c" / "ws"),
                            lambda root: _GradleDouble(root, {}, output="KriyaAcceptanceTest.java:8: error: cannot find symbol\n> Task :compileTestJava FAILED", report=False))
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == jvm.ACCEPTANCE_HARNESS_COMPILE_FAILED
    # a report produced under the other runner's binding is never read as this runner's evidence
    passing = {"clampsBelowTheMinimum": ("passed", None), "clampsAboveTheMaximum": ("passed", None)}
    artifact, run, _ = _run(tmp_path / "d", _workspace(tmp_path / "d" / "ws"), lambda root: _GradleDouble(root, passing, runner="maven"))
    judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
    assert judgment.code == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE and not judgment.passed


def test_03_the_trust_surface_covers_the_gradle_build_files_and_the_approval_binds_the_gradle_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    ws = _workspace(tmp_path / "ws")
    (ws / "build.gradle").write_text(GRADLE_SCRIPT + "test { ignoreFailures = true }\n")  # candidate edits the build
    passing = {"clampsBelowTheMinimum": ("passed", None), "clampsAboveTheMaximum": ("passed", None)}
    artifact, run, made = _run(tmp_path, ws, lambda root: _GradleDouble(root, passing))
    assert run.refusal is not None and run.refusal.reason_code == jvm.ACCEPTANCE_TRUST_SURFACE_CHANGED and made == []
    # B3: the approval written for this Gradle workspace binds the Gradle runner contract, and a run under it accepts
    (ws / "build.gradle").write_text(GRADLE_SCRIPT)
    (tmp_path / "g").mkdir()
    artifact = _artifact(tmp_path / "g", GENERAL_GOAL)
    reqs = derive_requirements(GENERAL_GOAL)
    template = b3.approval_template(reqs, artifact, ["REQ-1"], base_revision(ws), workspace=str(ws))
    assert template["approvals"][0]["runner_contract_sha256"] == jvm.runner_source_digest("gradle")
    template["approvals"][0]["accept_suite_as_sufficient"] = True
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(template))
    approval = b3.load_approval(str(path), reqs, artifact, state_root=str(tmp_path / "s"), workspace=str(ws),
                                base_revision=base_revision(ws))
    assert approval.entries["REQ-1"].runner_contract_sha256 == jvm.runner_source_digest("gradle")
    judged = jvm.judge_java_acceptance(artifact, jvm.run_java_acceptance(
        artifact, str(ws), candidate_paths=[TARGET], base_revision=base_revision(ws),
        validator_factory=lambda root: _GradleDouble(root, passing)), {})["REQ-1"]
    assert b3.approval_problem(approval, reqs.get("REQ-1"), reqs, artifact, base_revision(ws),
                               runner_digest=judged.evidence["runner_contract_digest"]) is None
    # the same approval against a Maven run's contract: mismatch (the runner is part of the bound contract)
    assert "runner contract differs" in b3.approval_problem(approval, reqs.get("REQ-1"), reqs, artifact, base_revision(ws),
                                                             runner_digest=jvm.RUNNER_SOURCE_DIGEST)

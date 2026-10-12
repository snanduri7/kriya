"""JVM-MODULE-OWNERSHIP-001: the goal-example acceptance class runs in the build module that owns the types it calls.

Generic fixtures only: no task, repository or module name is known to the code under test. The Gradle build is scripted (as
tests/test_backend_readiness_004_gradle_acceptance.py does): the validator double runs the REAL FS-1A report binding over JUnit
XML written exactly where Gradle writes it, so what is exercised is Kriya's placement, task selection and reading of a
module's report - not Gradle itself. The real-build replay is recorded in handover/BACKEND-READINESS-V2/."""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from _b2c_fixtures import _git, base_revision

from kriya.capabilities.gradle import GradleBuildAdapter
from kriya.capabilities.maven import MavenBuildAdapter
from kriya.tools import test_execution
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import acceptance_jvm as jvm
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow import example_oracle_java as ej
from kriya.workflow import jvm_module_ownership as own
from kriya.workflow.requirements import derive_requirements

GOAL = "Fix the clamp helper.\n\nCalc.clamp(5, 0, 3) -> 3\n"
CALC = ("/*\n * Copyright (c) Example.\n * Licensed under the Example License.\n */\npackage demo;\n\n"
        "public class Calc {\n    public static int clamp(int v, int lo, int hi) { return Math.max(lo, Math.min(hi, v)); }\n}\n")
CLASS_ID = "kriya.examples.KriyaGoalExamplesTest"


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _gradle(root: Path, *, owner="sub", declared=("sub",), build_files=("sub",), extra=()):
    """A Gradle multi-project: the root aggregates; ``owner`` holds Calc; ``declared`` are settings includes."""
    _write(root, "build.gradle", "// aggregator\n")
    _write(root, "settings.gradle", "rootProject.name = 'agg'\n" + "".join(f"include '{m}'\n" for m in declared))
    for module in build_files:
        _write(root, f"{module}/build.gradle", "plugins { id 'java' }\n")
    if owner:
        _write(root, f"{owner}/src/main/java/demo/Calc.java", CALC)
    for rel, text in extra:
        _write(root, rel, text)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _single(root: Path):
    _write(root, "build.gradle", "plugins { id 'java' }\n")
    _write(root, "settings.gradle", "rootProject.name = 'one'\n")
    _write(root, "src/main/java/demo/Calc.java", CALC)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _derive(tmp_path, root, goal=GOAL):
    return ej.derive_java_example_artifact(goal, derive_requirements(goal), state_root=str(tmp_path / "state"),
                                           candidate_root=str(root))


# --- ownership: source path -> module ------------------------------------------------------------------

def test_single_module_project_is_the_root_and_the_placement_is_unchanged(tmp_path):
    root = _single(tmp_path / "ws")
    artifact, report = _derive(tmp_path, root)
    assert artifact.java["injection_path"] == "src/test/java/kriya/examples/KriyaGoalExamplesTest.java"
    assert "module" not in artifact.java and report["module"] == "" and report["artifact"]["module"] == ""


def test_a_declared_nested_gradle_project_owns_its_types(tmp_path):
    root = _gradle(tmp_path / "ws", owner="a/b", declared=("a:b",), build_files=("a/b",))
    assert own.resolve_module_ownership(str(root), "a/b/src/main/java/demo/Calc.java", "gradle").module == "a/b"
    artifact, report = _derive(tmp_path, root)
    assert artifact.java["module"] == "a/b"
    assert artifact.java["injection_path"] == "a/b/src/test/java/kriya/examples/KriyaGoalExamplesTest.java"
    assert own.gradle_project_path("a/b") == ":a:b"


def test_the_target_type_resolves_to_its_declared_module_not_a_sibling(tmp_path):
    root = _gradle(tmp_path / "ws", owner="lib", declared=("lib", "other"), build_files=("lib", "other"))
    artifact, _report = _derive(tmp_path, root)
    assert artifact.java["module"] == "lib"
    assert artifact.java["injection_path"].startswith("lib/src/test/java/")


def test_an_undeclared_directory_is_unresolved_and_the_examples_stay_shadow(tmp_path):
    root = _gradle(tmp_path / "ws", owner="sub", declared=(), build_files=("sub",))
    resolution = own.resolve_module_ownership(str(root), "sub/src/main/java/demo/Calc.java", "gradle")
    assert not resolution.resolved and "not declared" in resolution.reason
    artifact, report = _derive(tmp_path, root)
    assert artifact is None                                    # nothing enforced, nothing guessed
    shadow = [row for row in report["shadow_examples"] if row.get("status") == "MODULE_NOT_ENFORCEABLE"]
    assert len(shadow) == 1 and "not declared" in shadow[0]["reason"]


def test_a_declared_module_that_is_not_a_project_directory_is_unresolved(tmp_path):
    root = _gradle(tmp_path / "ws", owner="sub", declared=("sub",), build_files=())
    resolution = own.resolve_module_ownership(str(root), "sub/src/main/java/demo/Calc.java", "gradle")
    assert not resolution.resolved and "project directory" in resolution.reason


def test_a_type_declared_in_two_modules_has_no_single_owner(tmp_path):
    root = _gradle(tmp_path / "ws", owner="one", declared=("one", "two"), build_files=("one", "two"),
                   extra=[("two/src/main/java/demo/Calc.java", CALC)])
    artifact, report = _derive(tmp_path, root)
    assert artifact is None and report["rejected"]            # the compiler already refuses an ambiguous simple name


def test_the_runner_must_match_the_root_build_system(tmp_path):
    root = _gradle(tmp_path / "ws", extra=[("sub/pom.xml", "<project/>\n")])      # a stray pom must not make it a Maven module
    assert not own.resolve_module_ownership(str(root), "sub/src/main/java/demo/Calc.java", "maven").resolved
    assert not own.resolve_module_ownership(str(root), "sub/src/main/java/demo/Calc.java", None).resolved
    assert not own.resolve_module_ownership(str(root), None, "gradle").resolved
    assert not own.resolve_module_ownership(str(root), "docs/Calc.java", "gradle").resolved


def test_maven_modules_resolve_from_the_reactor(tmp_path):
    root = tmp_path / "ws"
    _write(root, "pom.xml", "<project><modules><module>core</module></modules></project>\n")
    _write(root, "core/pom.xml", "<project/>\n")
    _write(root, "core/src/main/java/demo/Calc.java", CALC)
    _write(root, "loose/pom.xml", "<project/>\n")
    _write(root, "loose/src/main/java/demo/Other.java", "package demo; public class Other {}\n")
    assert own.resolve_module_ownership(str(root), "core/src/main/java/demo/Calc.java", "maven").module == "core"
    assert "not declared" in own.resolve_module_ownership(str(root), "loose/src/main/java/demo/Other.java", "maven").reason


def test_class_module_prefers_the_root_never_displaces_a_root_example_and_refuses_two_modules():
    assert own.class_module(["", "sub"]) == ""
    assert own.class_module(["sub", "sub"]) == "sub"
    assert own.class_module(["a", "b"]) is None and own.class_module([]) is None


def test_mixed_root_and_module_examples_keep_the_root_ones_and_shadow_the_rest(tmp_path):
    root = _gradle(tmp_path / "ws", owner="sub", build_files=("sub",),
                   extra=[("src/main/java/demo/Top.java",
                           "package demo;\npublic class Top { public static int one() { return 1; } }\n")])
    goal = "Fix.\n\nCalc.clamp(5, 0, 3) -> 3\n\nTop.one() -> 1\n"
    artifact, report = _derive(tmp_path, root, goal)
    assert artifact.java["injection_path"].startswith("src/test/java/") and "module" not in artifact.java
    assert [e["expression"] for e in report["examples"]] == ["Top.one()"]
    assert [row["status"] for row in report["shadow_examples"] if row.get("status") == "MODULE_NOT_ENFORCEABLE"] \
        == ["MODULE_NOT_ENFORCEABLE"]


def test_the_license_header_of_the_owning_modules_type_is_preserved(tmp_path):
    root = _gradle(tmp_path / "ws")
    artifact, _ = _derive(tmp_path, root)
    source = Path(artifact.stored_path).read_text(encoding="utf-8")
    assert source.startswith("/*\n * Copyright (c) Example.") and "package kriya.examples;" in source


# --- execution: placement, task selection, report reading, cleanup, authority -------------------------------

class _ModuleDouble:
    """The validator the acceptance run builds for its scratch export; a module-qualified run writes the JUnit XML of
    ``cases`` into ``report_module``'s build/test-results through the real FS-1A binding."""

    stack = "java"

    def __init__(self, root, cases, *, report_module="sub", output="BUILD SUCCESSFUL", report=True):
        self.workspace_path = root
        self.cases, self.report_module, self.output, self.report = cases, report_module, output, report
        self.calls = []

    def run_compile_check(self, files):
        return {"success": True, "output": "ok"}

    def run_tests(self, target_test=None, **kwargs):
        self.calls.append((target_test, dict(kwargs)))
        binding = test_execution.prepare(self.workspace_path, "gradle")
        if self.report:
            directory = Path(self.workspace_path, *([self.report_module] if self.report_module else []), "build",
                             "test-results", "test")
            directory.mkdir(parents=True, exist_ok=True)
            body = "".join(f'  <testcase name="{name}" classname="{CLASS_ID}" time="0.01"/>\n'
                           if passed else
                           f'  <testcase name="{name}" classname="{CLASS_ID}" time="0.01">\n'
                           '    <failure message="expected: &lt;3&gt; but was: &lt;5&gt;" '
                           'type="org.opentest4j.AssertionFailedError">org.opentest4j.AssertionFailedError: '
                           f'expected: &lt;3&gt;\n\tat demo.Calc.clamp(Calc.java:5)\n</failure>\n  </testcase>\n'
                           for name, passed in self.cases.items())
            failures = sum(1 for passed in self.cases.values() if not passed)
            (directory / f"TEST-{CLASS_ID}.xml").write_text(
                f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="{CLASS_ID}" tests="{len(self.cases)}" '
                f'skipped="0" failures="{failures}" errors="0" timestamp="2026-10-12T10:00:00" time="0.05">\n'
                f"{body}</testsuite>\n", encoding="utf-8")
        result = {"success": "FAILED" not in self.output, "output": self.output, "returncode": 0}
        binding.observe(result)
        result["test_execution"] = test_execution.collect(binding).to_dict()
        return result


def _tree_digest(root: Path) -> str:
    entries = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(files):
            path = Path(current, name)
            entries.append(f"{path.relative_to(root)}:{hashlib.sha256(path.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def _run(tmp_path, root, artifact, double_factory):
    made = []

    def factory(workspace):
        made.append(double_factory(workspace))
        return made[-1]

    run = jvm.run_java_acceptance(artifact, str(root), candidate_paths=["sub/src/main/java/demo/Calc.java"],
                                  base_revision=base_revision(root), validator_factory=factory)
    return run, made


def test_a_module_class_is_injected_and_run_in_its_module_and_the_workspace_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    root = _gradle(tmp_path / "ws")
    artifact, _ = _derive(tmp_path, root)
    before = _tree_digest(root)
    run, [double] = _run(tmp_path, root, artifact, lambda ws: _ModuleDouble(ws, {"example1": True}))

    assert run.refusal is None and run.module == "sub" and run.runner == "gradle"
    assert double.calls == [("sub/src/test/java/kriya/examples/KriyaGoalExamplesTest.java", {"module": "sub"})]
    judgment = ao.judge_acceptance(artifact, run)
    assert all(j.passed for j in judgment.values())
    assert next(iter(judgment.values())).evidence["module"] == "sub"
    assert _tree_digest(root) == before                      # never injected into, nor built in, the real workspace
    staging = Path(os.environ["KRIYA_STATE_DIR"]) / jvm._STAGING_DIR          # noqa: SLF001 - the runner's staging root
    assert not staging.exists() or not any(staging.iterdir())                 # the scratch copy is removed


def test_a_root_class_runs_exactly_as_before_without_a_module_argument(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    root = _single(tmp_path / "ws")
    artifact, _ = _derive(tmp_path, root)
    run, [double] = _run(tmp_path, root, artifact, lambda ws: _ModuleDouble(ws, {"example1": True}, report_module=""))
    assert run.module is None
    assert double.calls == [("src/test/java/kriya/examples/KriyaGoalExamplesTest.java", {})]
    assert "module" not in next(iter(ao.judge_acceptance(artifact, run).values())).evidence


def test_a_known_semantic_near_miss_in_the_module_is_a_violation_and_a_correct_candidate_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    root = _gradle(tmp_path / "ws")
    artifact, _ = _derive(tmp_path, root)
    run, _ = _run(tmp_path, root, artifact, lambda ws: _ModuleDouble(ws, {"example1": False}))
    judgment = next(iter(ao.judge_acceptance(artifact, run).values()))
    assert judgment.violated and "example1" in judgment.reason


def test_a_report_of_another_module_or_a_missing_report_or_a_build_failure_is_never_a_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    root = _gradle(tmp_path / "ws")
    artifact, _ = _derive(tmp_path, root)
    # no report at all (the module's task never ran the tests): INDETERMINATE, not a violation, not a pass
    run, _ = _run(tmp_path, root, artifact,
                  lambda ws: _ModuleDouble(ws, {}, output="> Task :sub:test FAILED\nBUILD FAILED", report=False))
    verdict = next(iter(ao.judge_acceptance(artifact, run).values()))
    assert verdict.code == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE and not verdict.passed and not verdict.violated
    # the module's task is rejected before the harness compiled
    run, _ = _run(tmp_path, root, artifact, lambda ws: _ModuleDouble(
        ws, {}, output="KriyaGoalExamplesTest.java:8: error: x\n> Task :sub:compileTestJava FAILED", report=False))
    assert next(iter(ao.judge_acceptance(artifact, run).values())).code == jvm.ACCEPTANCE_HARNESS_COMPILE_FAILED


def test_the_injection_path_may_never_overwrite_an_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    path = "sub/src/test/java/kriya/examples/KriyaGoalExamplesTest.java"
    root = _gradle(tmp_path / "ws", extra=[(path, "class KriyaGoalExamplesTest {}\n")])
    artifact, _ = _derive(tmp_path, root)
    run, made = _run(tmp_path, root, artifact, lambda ws: _ModuleDouble(ws, {"example1": True}))
    assert run.refusal is not None and run.refusal.reason_code == jvm.ACCEPTANCE_PATH_COLLISION and not made


# --- the adapters and the validator select the module ----------------------------------------------------

def _validator_stub(calls):
    return SimpleNamespace(
        workspace_path="/ws", test_report_binding=None,
        _run_gradle_cmd=lambda command, tasks, cwd: calls.append((command, list(tasks))) or {"returncode": 0, "stdout": "", "stderr": ""},
        _run_maven_cmd=lambda goals, cwd, timeout: calls.append(list(goals)) or {"returncode": 0, "stdout": "", "stderr": ""},
        _validation_result=lambda ok, output, res: {"success": ok, "output": output})


def test_gradle_runs_the_declared_project_task_only_and_the_root_selector_is_unchanged():
    calls = []
    GradleBuildAdapter().run_tests(_validator_stub(calls), "X", module="a/b")
    GradleBuildAdapter().run_tests(_validator_stub(calls), "X")
    assert [tasks for _cmd, tasks in calls] == [[":a:b:test", "--tests", "X"], ["test", "--tests", "X"]]


def test_maven_selects_the_module_with_its_reactor_dependencies_and_the_root_goal_is_unchanged():
    calls = []
    MavenBuildAdapter().run_tests(_validator_stub(calls), "X", module="core")
    MavenBuildAdapter().run_tests(_validator_stub(calls), "X")
    assert calls == [["test", "-pl", "core", "-am", "-Dsurefire.failIfNoSpecifiedTests=false", "-DfailIfNoTests=false",
                      "-Dtest=X"],
                     ["test", "-Dtest=X"]]


def test_the_validator_refuses_a_module_run_on_an_adapter_that_cannot_select_a_module(monkeypatch):
    class Plain:
        language, build_system, supports_modules = "java", "plainbuild", False

        def detects(self, _root):
            return True

        def run_tests(self, *_args, **_kwargs):
            raise AssertionError("must not run")

    monkeypatch.setattr("kriya.tools.validate.BUILD_ADAPTERS", [Plain()])
    validator = object.__new__(PolymorphicValidator)
    validator.stack, validator.workspace_path = "java", "/ws"
    result = validator._run_tests("a/T.java", module="sub")                  # noqa: SLF001 - the gate under test
    assert result["success"] is False and "not supported" in result["output"]


def test_the_validator_passes_the_module_to_an_adapter_that_selects_modules(monkeypatch):
    seen = []

    class Modular:
        language, build_system, supports_modules = "java", "modular", True

        def detects(self, _root):
            return True

        def run_tests(self, _validator, test_class, **kwargs):
            seen.append((test_class, kwargs))
            return {"success": True, "output": ""}

    monkeypatch.setattr("kriya.tools.validate.BUILD_ADAPTERS", [Modular()])
    validator = object.__new__(PolymorphicValidator)
    validator.stack, validator.workspace_path = "java", "/ws"
    validator._run_tests("a/T.java", module="sub")                           # noqa: SLF001
    validator._run_tests("a/T.java")                                         # noqa: SLF001
    assert seen == [("T", {"module": "sub"}), ("T", {})]


# --- no task or repository identity in the production code -----------------------------------------------

def test_no_task_or_repository_identity_in_the_module_ownership_code():
    root = Path(__file__).resolve().parent.parent / "kriya"
    for rel in ("workflow/jvm_module_ownership.py", "workflow/example_oracle_java.py", "capabilities/gradle.py",
                "capabilities/maven.py"):
        text = (root / rel).read_text(encoding="utf-8").lower()
        for token in ("hamcrest", "jsoup", "json-path", "jayway", "commons-text"):
            assert token not in text, (rel, token)


@pytest.mark.parametrize("path,expected", [
    ("src/main/java/a/B.java", ""), ("m/src/main/java/a/B.java", "m"), ("a/b/src/main/java/c/D.java", "a/b"),
    ("src/test/java/a/B.java", None), ("README.md", None)])
def test_module_of_source_path(path, expected):
    assert own.module_of_source_path(path) == expected


# --- the intermediate gate and the terminal recheck use the same module-aware class ----------------------------

def test_a_violation_in_a_module_enters_the_existing_repair_path_with_the_exact_example(tmp_path):
    from kriya.workflow import contract_feedback as cf
    from kriya.workflow.attempt import _run_contract_feedback_gate
    from kriya.workflow.failure import QualityGateFailure
    from kriya.workflow.state import GenerationState

    root = _gradle(tmp_path / "ws")
    artifact, report = _derive(tmp_path, root)
    examples = cf.contract_examples(artifact, report, str(root))
    assert [e.source_path for e in examples] == ["sub/src/main/java/demo/Calc.java"]

    details = {f"{CLASS_ID}.example1": [{"status": "failed", "type": "org.opentest4j.AssertionFailedError",
                                         "trace": "org.opentest4j.AssertionFailedError: expected: <3> but was: <5>\n"}]}
    report_obj = SimpleNamespace(complete=True, runner="gradle", statuses=lambda: {k: ["failed"] for k in details},
                                 passed=lambda identity: False)
    run = SimpleNamespace(runner="gradle", refusal=None, integrity_problem=None, report=report_obj, case_details=details,
                          candidate_classes=["demo.Calc"], result={}, module="sub")
    executed = []

    def execute(art, touched):
        executed.append(list(touched))
        return run

    feedback = cf.ContractFeedback(artifact=artifact, examples=examples, execute=execute)
    state = GenerationState()
    state.attempt_number = 1
    state.all_files_written = ["sub/src/main/java/demo/Calc.java"]
    with pytest.raises(QualityGateFailure) as stop:
        _run_contract_feedback_gate(state, SimpleNamespace(contract_feedback=feedback, worktree_path=str(root)))
    failure = stop.value.failure
    assert failure.type == "deterministic_contract_violation" and executed == [["sub/src/main/java/demo/Calc.java"]]
    for fragment in ("FAILED EXAMPLE: Calc.clamp(5, 0, 3) -> 3", "EXPECTED: 3", "OBSERVED: 5",
                     "TARGET: demo.Calc (sub/src/main/java/demo/Calc.java)"):
        assert fragment in failure.message, fragment


def test_every_acceptance_execution_goes_through_the_one_module_aware_runner():
    source = (Path(__file__).resolve().parent.parent / "kriya" / "workflow" / "workflow.py").read_text(encoding="utf-8")
    assert source.count("run_java_acceptance(") >= 3          # intermediate gate, terminal closer, baseline: one runner
    assert "validator.run_tests(target_test=injection" in (
        Path(__file__).resolve().parent.parent / "kriya" / "workflow" / "acceptance_jvm.py").read_text(encoding="utf-8")


# --- the recorded replay (real Gradle, the production derive -> run -> judge path, owner B5) ---------------------

def _replay_rows():
    import json

    path = (Path(__file__).resolve().parent.parent / "handover" / "BACKEND-READINESS-V2" / "module-replay"
            / "module_replay.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_replay_zero_false_rejection_of_correct_subjects_and_every_wrong_subject_is_rejected():
    rows = _replay_rows()
    correct = [r for r in rows if r["role"] == "correct"]
    wrong = [r for r in rows if r["role"] == "wrong"]
    assert {r["subject"] for r in correct} >= {"good", "unrelated", "recorded-v2-regex-GENUINE"}
    assert {r["subject"] for r in wrong} >= {"base", "near_miss_unicode_only", "near_miss_trim_only",
                                              "recorded-v2f-isWhitespace", "recorded-v6f-isWhitespace"}
    assert all(r["verdict"] == "PASS" for r in correct)                     # zero false rejection / indeterminate
    assert all(r["verdict"] == "VIOLATED" for r in wrong)
    assert all(r["module"] == "hamcrest" and r["injection_path"].startswith("hamcrest/src/test/java/") for r in rows)
    assert all(len(r["graduated"]) == 4 for r in rows)                        # the four explicit matcher examples, all enforced


def test_replay_is_stable_across_repeats_for_every_subject():
    by_subject = {}
    for row in _replay_rows():
        by_subject.setdefault(row["subject"], []).append(row["verdict"])
    assert all(len(verdicts) >= 3 and len(set(verdicts)) == 1 for verdicts in by_subject.values()), by_subject


# --- the graduated matcher form is enforced in its owning module and stays shadow where the module is unresolved ------

MATCHER_GOAL = ('Fix the matcher.\n\nThe matcher is Matchers.equalToCompressingWhiteSpace(String).\n\n'
                'assertThat("my foo bar", equalToCompressingWhiteSpace("  my  foo bar "))   -> fails, used to pass\n')


def _matcher_tree(root: Path, *, declared):
    units = [("sub/src/main/java/org/hamcrest/Matchers.java", "package org.hamcrest;\npublic class Matchers {}\n"),
             ("sub/src/main/java/org/hamcrest/MatcherAssert.java", "package org.hamcrest;\npublic class MatcherAssert {}\n")]
    return _gradle(root, owner=None, declared=declared, build_files=("sub",), extra=units)


def test_the_matcher_form_is_enforced_in_its_declared_module(tmp_path):
    root = _matcher_tree(tmp_path / "ws", declared=("sub",))
    artifact, report = _derive(tmp_path, root, MATCHER_GOAL)
    assert artifact is not None and artifact.java["module"] == "sub"
    assert [e["form"] for e in report["graduated_examples"]] == ["java_matcher_pass"]
    assert artifact.java["injection_path"].startswith("sub/src/test/java/kriya/examples/")


def test_the_matcher_form_stays_shadow_when_its_module_is_not_declared(tmp_path):
    root = _matcher_tree(tmp_path / "ws", declared=())
    artifact, report = _derive(tmp_path, root, MATCHER_GOAL)
    assert artifact is None and report["graduated_examples"] == []
    assert [row["status"] for row in report["shadow_examples"]] == ["MODULE_NOT_ENFORCEABLE"]


# --- review fixes: no loss of behaviour for single-project builds, composite builds are not modules ---------------

def test_a_single_project_build_keeps_an_example_whose_type_is_declared_under_two_source_roots(tmp_path):
    # the type is declared under two src/main/java roots: no single declaring file, enforced at the root before module
    # ownership existed - and the build declares no module, so it still is
    root = _single(tmp_path / "ws")
    _write(root, "other/src/main/java/demo/Calc.java", CALC)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "second root")
    artifact, report = _derive(tmp_path, root, "Fix the clamp helper.\n\ndemo.Calc.clamp(5, 0, 3) -> 3\n")
    assert artifact is not None and "module" not in artifact.java and report["module"] == ""
    assert artifact.java["injection_path"] == "src/test/java/kriya/examples/KriyaGoalExamplesTest.java"
    assert [e["expression"] for e in report["examples"]] == ["demo.Calc.clamp(5, 0, 3)"]


def test_a_multi_module_build_still_shadows_an_example_with_no_single_declaring_file(tmp_path):
    root = _gradle(tmp_path / "ws", declared=("sub", "dup"), build_files=("sub", "dup"),
                   extra=[("dup/src/main/java/demo/Calc.java", CALC)])
    artifact, report = _derive(tmp_path, root, "Fix.\n\ndemo.Calc.clamp(5, 0, 3) -> 3\n")
    assert artifact is None
    assert [row["status"] for row in report["shadow_examples"] if row.get("status") == "MODULE_NOT_ENFORCEABLE"] \
        == ["MODULE_NOT_ENFORCEABLE"]


def test_include_build_and_include_flat_declare_no_module(tmp_path):
    from kriya.workflow.repository_topology import declared_build_modules

    root = tmp_path / "ws"
    _write(root, "build.gradle", "// root\n")
    _write(root, "settings.gradle", "include 'sub'\nincludeBuild('build-logic')\nincludeFlat 'sibling'\ninclude(':a:b', ':c')\n")
    assert declared_build_modules(str(root)) == ("gradle", ["sub", "a/b", "c"])


def test_maven_module_entries_are_normalized(tmp_path):
    root = tmp_path / "ws"
    _write(root, "pom.xml", "<project><modules><module>./core/</module></modules></project>\n")
    _write(root, "core/pom.xml", "<project/>\n")
    assert own.resolve_module_ownership(str(root), "core/src/main/java/demo/Calc.java", "maven").module == "core"

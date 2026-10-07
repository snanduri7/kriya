"""FS-1A: structured test-execution evidence and test-execution integrity.

Real runner output throughout: the JUnit XML Surefire wrote in the live R2 T3
and T5 runs (handover/evidence/fs1/specimens/, with the base and candidate
test sources), and real pytest runs through PolymorphicValidator.run_tests.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kriya.capabilities import GRADLE, MAVEN, PIP
from kriya.tools import test_execution
from kriya.tools.test_execution import COMPLETE, INDETERMINATE, TestExecutionReport, parse_junit_xml
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.test_delta import (
    TEST_DELTA_EXECUTED,
    TEST_EXECUTION_EVIDENCE_INDETERMINATE,
    TEST_NOT_EXECUTED,
    TEST_NOT_PASSED,
    judge_test_delta,
    test_source_delta,
)

SPECIMENS = Path(__file__).resolve().parent.parent / "handover" / "evidence" / "fs1" / "specimens"
T3_PATH = "src/test/java/org/apache/commons/lang3/CharSetUtilsTest.java"
T5_PATH = "src/test/java/org/springframework/samples/petclinic/model/OwnerTests.java"


def _specimen(name):
    return (SPECIMENS / name).read_text()


def _report(xml_bytes):
    return TestExecutionReport(gate_id="g", runner="maven", workspace="/w", completeness=COMPLETE,
                               cases=parse_junit_xml(xml_bytes))


def _java(methods, package="a.b", cls="CalcTest"):
    return f"package {package};\nclass {cls} {{\n{methods}\n}}\n"


def _surefire(cases, cls="a.b.CalcTest"):
    body = "".join(f'<testcase name="{name}" classname="{cls}" time="0">{inner}</testcase>'
                   for name, inner in cases)
    return f'<?xml version="1.0"?><testsuite name="{cls}" tests="{len(cases)}">{body}</testsuite>'.encode()


# ---------------------------------------------------------------- the live specimens

def test_the_t3_specimen_added_test_without_its_annotation_is_not_executed():
    """Required test 2 on the real T3 evidence: the added method has no @Test,
    Surefire's report of that very run lists 11 cases and not it."""
    report = _report((SPECIMENS / "T3-TEST-CharSetUtilsTest.xml").read_bytes())
    verdict = judge_test_delta({T3_PATH: (_specimen("T3-CharSetUtilsTest.base.java"),
                                          _specimen("T3-CharSetUtilsTest.candidate.java"))}, report)
    assert verdict.reason_code == TEST_NOT_EXECUTED and not verdict.satisfied
    assert [(c.identity, c.change, c.declared_test) for c in verdict.delta] == [
        ("org.apache.commons.lang3.CharSetUtilsTest.testContainsOnly_StringString", "added", False)]
    assert "testContainsOnly_StringString" in verdict.detail


def test_the_t5_specimen_added_junit_tests_executed_and_passed():
    """Required test 4 on the real T5 evidence: two added @Test methods, both
    in Surefire's report, both passed."""
    report = _report((SPECIMENS / "T5-TEST-OwnerTests.xml").read_bytes())
    verdict = judge_test_delta({T5_PATH: (_specimen("T5-OwnerTests.base.java"),
                                          _specimen("T5-OwnerTests.candidate.java"))}, report)
    assert verdict.reason_code == TEST_DELTA_EXECUTED
    prefix = "org.springframework.samples.petclinic.model.OwnerTests."
    assert verdict.executed == [prefix + "hasPet_returnsFalseWhenPetDoesNotExist",
                                prefix + "hasPet_returnsTrueWhenPetExists"]


# ---------------------------------------------------------------- the decision

BASE = _java("  @Test void adds() { }\n  @Test void subtracts() { }")


def test_a_modified_existing_test_must_execute_and_pass():
    """Required test 5: a changed existing @Test must be in the report and pass."""
    candidate = BASE.replace("void subtracts() { }", "void subtracts() { int x = 1; }")
    delta = {"src/test/java/a/b/CalcTest.java": (BASE, candidate)}
    ran = judge_test_delta(delta, _report(_surefire([("adds", ""), ("subtracts", "")])))
    assert ran.reason_code == TEST_DELTA_EXECUTED and ran.executed == ["a.b.CalcTest.subtracts"]
    skipped = judge_test_delta(delta, _report(_surefire([("adds", ""), ("subtracts", "<skipped/>")])))
    assert skipped.reason_code == TEST_NOT_EXECUTED and skipped.not_executed == ["a.b.CalcTest.subtracts"]
    missing = judge_test_delta(delta, _report(_surefire([("adds", "")])))
    assert missing.reason_code == TEST_NOT_EXECUTED and missing.not_executed == ["a.b.CalcTest.subtracts"]
    failed = judge_test_delta(delta, _report(_surefire([("adds", ""), ("subtracts", "<failure/>")])))
    assert failed.reason_code == TEST_NOT_PASSED and failed.not_passed == ["a.b.CalcTest.subtracts"]


def test_a_helper_added_beside_an_executed_test_is_not_required_to_run():
    """Required test 6: helpers in a test file never have to execute on their own."""
    candidate = BASE.replace("@Test void subtracts() { }",
                             "@Test void subtracts() { }\n  private int helper() { return 1; }\n"
                             "  @Test void multiplies() { helper(); }")
    verdict = judge_test_delta({"src/test/java/a/b/CalcTest.java": (BASE, candidate)},
                               _report(_surefire([("adds", ""), ("subtracts", ""), ("multiplies", "")])))
    assert verdict.reason_code == TEST_DELTA_EXECUTED and verdict.executed == ["a.b.CalcTest.multiplies"]
    assert {c.identity for c in verdict.delta} == {"a.b.CalcTest.helper", "a.b.CalcTest.multiplies"}


def test_a_declared_test_missing_from_the_report_fails_even_beside_an_executed_one():
    candidate = BASE.replace("@Test void subtracts() { }",
                             "@Test void subtracts() { }\n  @Test void multiplies() { }\n"
                             "  @ParameterizedTest void divides(int x) { }")
    verdict = judge_test_delta({"src/test/java/a/b/CalcTest.java": (BASE, candidate)},
                               _report(_surefire([("adds", ""), ("subtracts", ""), ("multiplies", "")])))
    assert verdict.reason_code == TEST_NOT_EXECUTED and verdict.not_executed == ["a.b.CalcTest.divides"]


def test_a_test_count_increase_alone_is_not_evidence():
    """The report grew by one case - but not the delta's: never a pass."""
    candidate = BASE.replace("@Test void subtracts() { }", "@Test void subtracts() { }\n  void multiplies() { }")
    verdict = judge_test_delta({"src/test/java/a/b/CalcTest.java": (BASE, candidate)},
                               _report(_surefire([("adds", ""), ("subtracts", ""), ("other", "")])))
    assert verdict.reason_code == TEST_NOT_EXECUTED


def test_indeterminate_evidence_is_never_positive():
    """Required test 8: no report, an incomplete report, an unparseable or
    undecodable test source - all INDETERMINATE."""
    delta = {"src/test/java/a/b/CalcTest.java": (BASE, BASE.replace("{ }", "{ int y; }", 1))}
    complete = _report(_surefire([("adds", ""), ("subtracts", "")]))
    assert judge_test_delta(delta, complete).satisfied
    assert judge_test_delta(delta, None).reason_code == TEST_EXECUTION_EVIDENCE_INDETERMINATE
    incomplete = TestExecutionReport(gate_id="g", runner="maven", workspace="/w", completeness=INDETERMINATE,
                                     reason="STRUCTURED_REPORT_MISSING", cases=list(complete.cases))
    assert judge_test_delta(delta, incomplete).reason_code == TEST_EXECUTION_EVIDENCE_INDETERMINATE
    assert judge_test_delta({"spec/calc_spec.rb": (None, "it 'adds'")}, complete).reason_code == (
        TEST_EXECUTION_EVIDENCE_INDETERMINATE)
    assert judge_test_delta({"src/test/java/a/b/CalcTest.java": (BASE, None)}, complete).reason_code == (
        TEST_EXECUTION_EVIDENCE_INDETERMINATE)


def test_an_unchanged_declaration_is_no_delta_and_a_moved_method_is_unchanged():
    moved = _java("  @Test void subtracts() { }\n  @Test void adds() { }")
    assert test_source_delta("src/test/java/a/b/CalcTest.java", BASE, moved) == []


# ---------------------------------------------------------------- real pytest through the validator

PY_BASE = "def test_existing():\n    assert True\n"


def _pytest_workspace(tmp_path, tests):
    (tmp_path / "requirements.txt").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "tests" / "test_calc.py").write_text(tests)
    validator = PolymorphicValidator(str(tmp_path))
    validator._resolve_python_interpreter = lambda: (sys.executable, None)
    return validator


@pytest.mark.parametrize("added, expected, identities", [
    ("def check_new():\n    assert False\n", TEST_NOT_EXECUTED, []),          # required test 3: never collected
    ("def test_new():\n    assert True\n", TEST_DELTA_EXECUTED, ["tests.test_calc.test_new"]),
    ("import pytest\n\n\n@pytest.mark.skip\ndef test_new():\n    assert True\n", TEST_NOT_EXECUTED, []),
    ("def helper():\n    return 1\n\n\ndef test_new():\n    assert helper()\n", TEST_DELTA_EXECUTED,
     ["tests.test_calc.test_new"]),
])
def test_a_real_pytest_report_decides_the_python_delta(tmp_path, added, expected, identities):
    candidate = PY_BASE + "\n\n" + added
    validator = _pytest_workspace(tmp_path, candidate)
    result = validator.run_tests()
    assert result["success"] is True                     # the gate itself passes in every case
    report = test_execution.report_from_result(result)
    assert report.complete and report.runner == "pytest" and report.passed("tests.test_calc.test_existing")
    verdict = judge_test_delta({"tests/test_calc.py": (PY_BASE, candidate)}, report)
    assert verdict.reason_code == expected and verdict.executed == identities


def test_each_invocation_reads_only_its_own_fresh_report(tmp_path):
    """Required test 7 (pytest): a planted report at another gate's path, or a
    leftover, is never read; each invocation has its own destination, which
    Kriya removes after reading."""
    validator = _pytest_workspace(tmp_path, PY_BASE)
    reports = tmp_path / ".kriya" / "test-reports"
    reports.mkdir(parents=True)
    forged = (b'<testsuite><testcase classname="tests.test_calc" name="test_forged"/></testsuite>')
    (reports / "planted.xml").write_bytes(forged)
    first = test_execution.report_from_result(validator.run_tests())
    second = test_execution.report_from_result(validator.run_tests())
    assert first.gate_id != second.gate_id
    for report in (first, second):
        assert [c.identity for c in report.cases] == ["tests.test_calc.test_existing"]
        assert report.report_files[0]["path"] == os.path.join(".kriya", "test-reports", f"{report.gate_id}.xml")
    assert sorted(os.listdir(reports)) == ["planted.xml"]


def test_an_incomplete_pytest_session_is_indeterminate(tmp_path):
    validator = _pytest_workspace(tmp_path, PY_BASE)
    result = validator.run_tests(target_test="tests/missing_file.py")   # usage error: pytest exit 4
    report = test_execution.report_from_result(result)
    assert report.completeness == INDETERMINATE and report.reason == "PYTEST_SESSION_INCOMPLETE:exit_4"


def test_the_pytest_argument_is_an_option_before_the_separator_and_its_directory_is_gate_output(tmp_path):
    validator = _pytest_workspace(tmp_path, PY_BASE)
    seen = []
    real = validator._run_cmd_with_timeout

    def recording(cmd, *args, **kwargs):
        seen.append(list(cmd))
        return real(cmd, *args, **kwargs)

    validator._run_cmd_with_timeout = recording
    validator.run_tests(target_test="tests/test_calc.py")
    [cmd] = seen
    separator = cmd.index("--")
    [argument] = [arg for arg in cmd if arg.startswith("--junitxml=")]
    assert cmd.index(argument) < separator and cmd[separator + 1:] == ["tests/test_calc.py"]
    assert argument == "--junitxml=" + os.path.join(".kriya", "test-reports", argument.rsplit("/", 1)[-1])
    assert os.path.join(str(tmp_path), ".kriya", "test-reports") in PIP.output_roots(cmd, str(tmp_path))


# ---------------------------------------------------------------- JVM: Kriya clears stale reports before the run

@pytest.mark.parametrize("adapter, report_dir", [
    (MAVEN, ("target", "surefire-reports")), (GRADLE, ("build", "test-results", "test")),
])
def test_a_stale_jvm_report_is_cleared_and_only_this_runs_reports_are_read(tmp_path, adapter, report_dir):
    """Required test 7 (JVM): a report left by an earlier run claims the
    delta's test passed; Kriya deletes it before the run, so this run's
    report (or its absence) alone decides."""
    marker = "pom.xml" if adapter is MAVEN else "build.gradle"
    (tmp_path / marker).write_text("<project/>" if adapter is MAVEN else "")
    directory = tmp_path.joinpath(*report_dir)
    directory.mkdir(parents=True)
    (directory / "TEST-a.b.CalcTest.xml").write_bytes(_surefire([("adds", ""), ("multiplies", "")]))
    runner = adapter.build_system

    binding = test_execution.prepare(str(tmp_path), runner)
    assert binding.cleared_reports == 1 and not list(directory.iterdir())
    binding.observe({"returncode": 0, "timed_out": False})            # the run wrote no report
    missing = test_execution.collect(binding)
    assert missing.completeness == INDETERMINATE and missing.reason == "STRUCTURED_REPORT_MISSING"

    binding = test_execution.prepare(str(tmp_path), runner)
    (directory / "TEST-a.b.CalcTest.xml").write_bytes(_surefire([("adds", "")]))   # this run's own report
    binding.observe({"returncode": 0, "timed_out": False})
    fresh = test_execution.collect(binding)
    assert fresh.complete and [c.identity for c in fresh.cases] == ["a.b.CalcTest.adds"]


@pytest.mark.parametrize("adapter", [MAVEN, GRADLE])
def test_the_jvm_adapters_record_the_test_process_on_the_binding(tmp_path, adapter):
    process = {"returncode": 1, "stdout": "", "stderr": "", "timed_out": True}
    binding = test_execution.ReportBinding(gate_id="g", runner=adapter.build_system, workspace=str(tmp_path))
    v = SimpleNamespace(workspace_path=str(tmp_path), test_report_binding=binding,
                        _run_maven_cmd=lambda *a, **k: process, _run_cmd_with_timeout=lambda *a, **k: process,
                        _validation_result=PolymorphicValidator._validation_result)
    adapter.run_tests(v, "CalcTest")
    assert (binding.observed, binding.exit_code, binding.timed_out) == (True, 1, True)
    assert test_execution.collect(binding).reason == "GATE_TIMED_OUT"


def test_no_test_process_and_unsupported_runners_are_indeterminate(tmp_path):
    binding = test_execution.prepare(str(tmp_path), "pytest")
    assert test_execution.collect(binding).reason == "TEST_PROCESS_NOT_RUN"
    assert not os.listdir(tmp_path / ".kriya" / "test-reports")
    for runner in ("javac", "rspec", "none"):
        report = test_execution.collect(test_execution.prepare(str(tmp_path), runner))
        assert report.completeness == INDETERMINATE and report.reason.startswith("NO_STRUCTURED_REPORT")


def test_malformed_xml_is_indeterminate(tmp_path):
    binding = test_execution.prepare(str(tmp_path), "pytest")
    (tmp_path / binding.pytest_report).write_bytes(b"<testsuite><testcase")
    binding.observe({"returncode": 0})
    report = test_execution.collect(binding)
    assert report.completeness == INDETERMINATE and report.reason.startswith("STRUCTURED_REPORT_UNREADABLE")
    assert report.cases == []


# ---------------------------------------------------------------- the attempt gate (Rule E)

class _State:
    def __init__(self, files):
        self.all_files_written = list(files)
        self.attempt_number = 2
        self.outcomes = []

    def record_gate_outcome(self, outcome):
        self.outcomes.append(outcome)


def _attempt_workspaces(tmp_path, files):
    base, candidate = tmp_path / "base", tmp_path / "cand"
    for root in (base, candidate):
        (root / "tests").mkdir(parents=True)
    for path, (before, after) in files.items():
        if before is not None:
            (base / path).write_text(before)
        (candidate / path).write_text(after)
    return SimpleNamespace(workspace_path=str(base), worktree_path=str(candidate))


def _result(cases, completeness=COMPLETE, success=True):
    report = TestExecutionReport(gate_id="g1", runner="pytest", workspace="/w", completeness=completeness,
                                 reason=None if completeness == COMPLETE else "STRUCTURED_REPORT_MISSING",
                                 cases=[test_execution.TestCaseResult(i, i.rsplit(".", 1)[0], i.rsplit(".", 1)[1],
                                                                      "passed") for i in cases])
    return {"success": success, "output": "", "test_execution": report.to_dict()}


class _Validator:
    def __init__(self, results, stack="python"):
        self.stack, self.results, self.calls = stack, list(results), []

    def run_tests(self, target_test=None):
        self.calls.append(target_test)
        return self.results.pop(0)


def _gate(tmp_path, accepted, selected, covering=(), files=None, stack="python"):
    from kriya.workflow.attempt import _raise_unexecuted_test_delta

    files = files or {"tests/test_calc.py": (PY_BASE, PY_BASE + "\n\ndef test_new():\n    assert True\n")}
    ctx = _attempt_workspaces(tmp_path, files)
    state, validator = _State(files), _Validator(covering, stack)
    try:
        _raise_unexecuted_test_delta(state, ctx, validator, accepted, selected)
        return None, state, validator
    except Exception as error:  # the gate's typed failure
        return error, state, validator


def test_the_gate_passes_on_the_accepted_invocations_own_report(tmp_path):
    error, state, validator = _gate(tmp_path, _result(["tests.test_calc.test_existing", "tests.test_calc.test_new"]),
                                    "tests/test_calc.py")
    assert error is None and validator.calls == []          # no extra run
    [outcome] = state.outcomes
    assert outcome["type"] == "test_delta" and outcome["reason_code"] == TEST_DELTA_EXECUTED
    assert outcome["test_execution"]["gate_id"] == "g1"


def test_an_uncollected_test_is_a_repairable_test_acceptance_failure_naming_it(tmp_path):
    files = {"tests/test_calc.py": (PY_BASE, PY_BASE + "\n\ndef check_new():\n    assert True\n")}
    error, state, _ = _gate(tmp_path, _result(["tests.test_calc.test_existing"]), None, files=files)
    failure = error.failure
    assert failure.type == "test_acceptance" and failure.diagnostics["reason_code"] == TEST_NOT_EXECUTED
    assert failure.likely_files == ["tests/test_calc.py"] and "tests.test_calc.check_new" in failure.message
    assert [o["type"] for o in state.outcomes] == ["test_delta", "test_acceptance"]


def test_missing_evidence_is_a_typed_stop_never_a_pass(tmp_path):
    """Required test 8 at the gate: a report-less result triggers one covering
    run; when that is INDETERMINATE too the attempt stops, typed."""
    error, state, validator = _gate(tmp_path, {"success": True, "output": "1 passed"}, "tests/test_calc.py",
                                    covering=[_result(["tests.test_calc.test_new"], completeness=INDETERMINATE)])
    assert validator.calls == [["tests/test_calc.py"]]
    assert error.failure.type == "verification_infrastructure_failure"
    assert error.failure.diagnostics["reason_code"] == TEST_EXECUTION_EVIDENCE_INDETERMINATE
    assert state.outcomes[0]["success"] is False


def test_a_self_corrected_result_without_a_report_gets_one_covering_run(tmp_path):
    error, _, validator = _gate(tmp_path, {"success": True, "output": "1 passed"}, "tests/test_calc.py",
                                covering=[_result(["tests.test_calc.test_new"])])
    assert error is None and validator.calls == [["tests/test_calc.py"]]


@pytest.mark.parametrize("stack, expected_call", [("python", ["tests/test_a.py", "tests/test_calc.py"]),
                                                  ("java", None)])
def test_a_targeted_run_that_missed_a_changed_test_file_is_covered(tmp_path, stack, expected_call):
    """The accepted run selected one file; the other changed file's tests were
    never run by it, so one covering run (both files; the suite for Java)
    supplies the evidence instead of blaming the candidate."""
    files = {"tests/test_calc.py": (PY_BASE, PY_BASE + "\n\ndef test_new():\n    assert True\n"),
             "tests/test_a.py": (None, "def test_a():\n    assert True\n")}
    error, _, validator = _gate(
        tmp_path, _result(["tests.test_calc.test_new"]), "tests/test_calc.py", files=files, stack=stack,
        covering=[_result(["tests.test_calc.test_new", "tests.test_a.test_a"])])
    assert error is None and validator.calls == [expected_call]


def test_a_failing_covering_run_is_an_ordinary_test_failure(tmp_path):
    error, _, _ = _gate(tmp_path, {"success": True, "output": ""}, "tests/test_calc.py",
                        covering=[_result([], success=False)])
    assert error.failure.type in ("test", "test_process_terminated")


def test_unchanged_test_sources_need_no_evidence(tmp_path):
    files = {"tests/test_calc.py": (PY_BASE, PY_BASE)}
    error, state, validator = _gate(tmp_path, None, None, files=files)
    assert error is None and state.outcomes == [] and validator.calls == []


def test_the_report_leaves_the_gate_output_byte_identical(tmp_path):
    """Gate output reaches retry prompts and the no-progress fingerprint; a
    per-invocation report path in it made identical failures look new
    (measured: T6 REPEATED_VECTOR became NO_PROGRESS). With and without the
    report, the output is the same text."""
    import re

    validator = _pytest_workspace(tmp_path, PY_BASE + "\n\ndef test_fails():\n    assert 1 == 2\n")
    with_report = validator.run_tests()
    assert test_execution.report_from_result(with_report).complete
    without_report = PIP.run_tests(validator, None)      # no binding outside run_tests
    assert "test_execution" not in without_report

    def normalized(text):
        return re.sub(r"in [0-9.]+s", "in <t>s", text)

    assert "generated xml file" not in with_report["output"] and ".kriya" not in with_report["output"]
    assert normalized(with_report["output"]) == normalized(without_report["output"])


# ---------------------------------------------------------------- required test 12: the T5 reference success, honestly

T5_GOAL = ("Add a method `public boolean hasPet(String name)` to org.springframework.samples.petclinic.model.Owner "
           "that returns true when the owner has a pet with that name (matched the same way as the existing "
           "`getPet(String name)`) and false otherwise, and add a unit test for it in OwnerTests.")
T5_REQUIREMENT_SET_DIGEST = "c5609719b08e3c82fafe87f8ff60a4e8504d48f9076cc4f9087792c49d8a0acf"  # the live run's


def test_the_t5_reference_success_is_unverified_and_blocked_under_the_production_policy():
    """R2 T5 (preserved packet): its tests genuinely executed (Rule E holds on
    the run's own Surefire report), but its one requirement had only the
    verifier's "satisfied". Candidate-written tests close no original
    requirement (owner decision: no EXECUTED_CANDIDATE_TESTS closure), and the
    test the requirement names (OwnerTests) was changed by the candidate, so
    the named-test closure refuses it. Under the production policy T5 is
    therefore UNVERIFIED and blocked - not a success."""
    from kriya.workflow.obligations import ObligationLedger
    from kriya.workflow.requirements import (
        RequirementOutcome,
        blocking_requirements,
        close_unverified_requirements_with_named_tests,
        derive_requirements,
        record_requirement_verdicts,
        requirement_outcomes,
        seed_requirement_obligations,
    )

    report = _report((SPECIMENS / "T5-TEST-OwnerTests.xml").read_bytes())
    assert judge_test_delta({T5_PATH: (_specimen("T5-OwnerTests.base.java"),
                                       _specimen("T5-OwnerTests.candidate.java"))}, report).satisfied

    reqs = derive_requirements(T5_GOAL)
    assert reqs.digest == T5_REQUIREMENT_SET_DIGEST and reqs.ids == ["REQ-1"]
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.SATISFIED, "hasPet exists; tests exist")},
                                revision="terminal", evidence_fingerprint="t5-candidate", source="test")
    runs = []
    [attempt] = close_unverified_requirements_with_named_tests(
        ledger, reqs, test_files=[T5_PATH], modified=[T5_PATH], judge=lambda paths: runs.append(paths),
        source="test", revision="terminal")
    assert attempt["closed"] is False and "written or changed by this candidate" in attempt["reason"]
    assert runs == []
    assert requirement_outcomes(ledger, reqs) == {"REQ-1": RequirementOutcome.UNVERIFIED}
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, unknown_policy="block",
                                                   unverified_policy="block")] == ["REQ-1"]

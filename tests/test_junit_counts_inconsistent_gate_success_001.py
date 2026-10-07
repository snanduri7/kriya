"""JUNIT-COUNTS-INCONSISTENT-GATE-SUCCESS-001: a test gate never reports PASS on contradictory structured evidence.

Measured mechanism (blind cohort T4, python-slugify, pytest 9.1.1): a suite using ``unittest.subTest`` completed
(133 test ids) while its JUnit ``tests`` attribute declared 267 - pytest counts every call-phase report, including
each passed subtest, but writes one ``<testcase>`` per test id. Kriya's parser demanded an exact match, flagged the
document JUNIT_COUNTS_INCONSISTENT, the gate still reported success (exit code only) and the per-test regression
authority refused attribution on both sides.

Invariant: a passing test process whose structured evidence contradicts itself, cannot be read, is missing when the
runner always writes it, or names a case that did not pass, is a typed non-PASS (TEST_EVIDENCE_INCONSISTENT);
pytest's uncounted passed-subtest reports are reconciled from the document's own counts, never from console text.
"""
import sys
import xml.etree.ElementTree as ET

import pytest
from _reg_r1_fixture import validator_for, write_project

from kriya.tools import test_execution
from kriya.tools.test_execution import (
    COMPLETE,
    INDETERMINATE,
    TEST_EVIDENCE_INCONSISTENT,
    evidence_inconsistency,
    junit_report_integrity,
    parse_pytest_case_evidence,
)

SUBTEST_SUITE = '''import unittest


class SubTests(unittest.TestCase):
    def test_plain(self):
        self.assertTrue(True)

    def test_with_subtests(self):
        for i in range(3):
            with self.subTest(i=i):
                self.assertEqual(i, i)

    def test_skipped_subtest(self):
        with self.subTest(part="a"):
            self.skipTest("not here")
'''

FAILING_SUBTEST_SUITE = '''import unittest


class SubTests(unittest.TestCase):
    def test_with_a_failing_subtest(self):
        for i in range(3):
            with self.subTest(i=i):
                self.assertNotEqual(i, 1)
'''


def suite(cases, *, tests=None, failures=None, errors=None, skipped=None):
    """A JUnit document: ``cases`` = (name, child-tag-or-None) pairs; counts default to the exact element counts."""
    body = "".join(f'<testcase classname="c" name="{name}">{f"<{tag}/>" if tag else ""}</testcase>' for name, tag in cases)
    counts = {"tests": len(cases), "failures": sum(1 for _, t in cases if t == "failure"),
              "errors": sum(1 for _, t in cases if t == "error"), "skipped": sum(1 for _, t in cases if t == "skipped")}
    for key, value in (("tests", tests), ("failures", failures), ("errors", errors), ("skipped", skipped)):
        if value is not None:
            counts[key] = value
    attrs = " ".join(f'{k}="{v}"' for k, v in counts.items())
    return f"<testsuite {attrs}>{body}</testsuite>".encode()


def gate_with_report(root, document, *, returncode=0, stdout="2 passed"):
    """The production gate with the runner process stubbed: it exits ``returncode``, prints ``stdout`` and leaves
    ``document`` (bytes, or None for no report) at Kriya's bound report destination."""
    validator = validator_for(root)

    def fake_run(target=None):
        binding = validator.test_report_binding
        if document is not None:
            (root / binding.pytest_report).write_bytes(document)
        binding.observe({"returncode": returncode, "stdout": stdout, "stderr": ""})
        return {"success": returncode == 0, "output": stdout}

    validator._run_tests = fake_run  # pylint: disable=protected-access
    return validator.run_tests()


# ---------------------------------------------------------------- the measured mechanism (real pytest, real subtests)
def test_01_passed_subtests_are_a_counted_surplus_and_the_gate_is_complete(tmp_path):
    root = write_project(tmp_path / "ws", SUBTEST_SUITE)
    result = validator_for(root).run_tests()
    integrity = result["pytest_evidence"]["evidence"]["integrity"]
    assert result["success"] is True and "reason_code" not in result
    assert result["test_execution"]["completeness"] == COMPLETE and result["test_execution"]["cases"] == 3
    assert result["pytest_evidence"]["complete"] is True
    # pytest 9 counts 7 call-phase reports (3 parent calls + 3 passed subtests + 1 skipped subtest) for 3 elements:
    # the skipped subtest materializes as its parent's <skipped> child, the passed ones leave no element
    assert integrity["ok"] is True and integrity["parsed"] == {"tests": 3, "failures": 0, "errors": 0, "skipped": 1}
    assert integrity["declared"] == {"tests": 7, "failures": 0, "errors": 0, "skipped": 1}
    assert integrity["surplus_reports"] == 4
    outcomes = {key.split("::")[-1]: case["outcome"] for key, case in result["pytest_evidence"]["evidence"]["cases"].items()}
    assert outcomes == {"test_plain": "passed", "test_with_subtests": "passed", "test_skipped_subtest": "skipped"}


def test_02_a_failed_subtest_fails_its_parent_and_the_failure_counts_match(tmp_path):
    root = write_project(tmp_path / "ws", FAILING_SUBTEST_SUITE)
    result = validator_for(root).run_tests()
    integrity = result["pytest_evidence"]["evidence"]["integrity"]
    assert result["success"] is False and result["pytest_evidence"]["complete"] is True
    assert integrity["ok"] is True and integrity["parsed"]["failures"] == integrity["declared"]["failures"] == 1
    (case,) = result["pytest_evidence"]["evidence"]["cases"].values()
    assert case["outcome"] == "failed" and "AssertionError" in (case["failure_type"] or "")


def test_03_the_cohort_numbers_reconcile_as_a_surplus_only():
    # T4 as measured: declared 267/20/0/8 against 133 elements with 20 failures and 8 skips; the first cohort: 136 vs 133
    evidence = parse_pytest_case_evidence(suite([(f"t{i}", None) for i in range(133)], tests=267))
    assert evidence["integrity"]["ok"] and evidence["integrity"]["surplus_reports"] == 134
    evidence = parse_pytest_case_evidence(suite([(f"t{i}", None) for i in range(133)], tests=136))
    assert evidence["integrity"]["ok"] and evidence["integrity"]["surplus_reports"] == 3


# ---------------------------------------------------------------- consistency rules on documents
@pytest.mark.parametrize("document", [
    suite([]),                                                   # consistent zero
    suite([("a", None), ("b", "failure"), ("c", "skipped")]),    # consistent nonzero
    suite([("p[1]", None), ("p[2]", None), ("p[3]", "failure")]),  # parameterized: one element each
    b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="x" name="a"/></testsuite>'
    b'<testsuite tests="2" failures="1" errors="0" skipped="0"><testcase classname="y" name="b"><failure/></testcase>'
    b'<testcase classname="y" name="c"/></testsuite></testsuites>',  # multiple suites: counts sum
])
def test_04_consistent_documents(document):
    evidence = parse_pytest_case_evidence(document)
    assert evidence["integrity"]["ok"] and evidence["integrity"]["surplus_reports"] == 0
    assert junit_report_integrity(document)["ok"]


@pytest.mark.parametrize("document, reason", [
    (suite([("a", None), ("b", None)], tests=1), "JUNIT_COUNTS_INCONSISTENT"),      # deficit
    (suite([("a", None)], failures=1), "JUNIT_COUNTS_INCONSISTENT"),                # failure count without a failure
    (suite([("a", "failure")], failures=0), "JUNIT_COUNTS_INCONSISTENT"),           # failure without a count
    (suite([("a", "error")], errors=0), "JUNIT_COUNTS_INCONSISTENT"),
    (suite([("a", "skipped")], skipped=0, tests=1), "JUNIT_COUNTS_INCONSISTENT"),
    (suite([("a", None), ("a", None)]), "JUNIT_DUPLICATE_CASE"),
    (b'<testsuite><testcase classname="c" name="a"/></testsuite>', "JUNIT_COUNTS_UNDECLARED"),
])
def test_05_inconsistent_documents(document, reason):
    assert parse_pytest_case_evidence(document)["integrity"]["reason"] == reason


def test_06_jvm_documents_are_exact_a_surplus_is_inconsistent():
    # Surefire/Gradle write one element per executed test (measured on 220 real reports): a surplus is a contradiction
    assert junit_report_integrity(suite([("a", None)], tests=2))["reason"] == "JUNIT_COUNTS_INCONSISTENT"
    assert junit_report_integrity(suite([("a", "failure")], failures=0))["reason"] == "JUNIT_COUNTS_INCONSISTENT"
    assert junit_report_integrity(suite([("a", "failure")]))["ok"]


# ---------------------------------------------------------------- the gate verdict
def test_07_a_passing_process_with_a_deficit_report_is_not_pass(tmp_path):
    root = write_project(tmp_path / "ws")
    result = gate_with_report(root, suite([("a", None), ("b", None)], tests=1), stdout="2 passed in 0.01s")
    assert result["success"] is False and result["reason_code"] == TEST_EVIDENCE_INCONSISTENT
    assert result["output"].startswith(f"{TEST_EVIDENCE_INCONSISTENT}: ") and "JUNIT_COUNTS_INCONSISTENT" in result["output"]
    assert result["test_execution"]["completeness"] == INDETERMINATE
    assert result["test_execution"]["reason"] == "JUNIT_COUNTS_INCONSISTENT"
    assert result["pytest_evidence"]["complete"] is False


def test_08_a_passing_process_with_a_truncated_report_is_not_pass_but_a_missing_one_stays_indeterminate(tmp_path):
    root = write_project(tmp_path / "ws")
    truncated = gate_with_report(root, b'<testsuite tests="1"><testcase classname="c" name="a"', stdout="1 passed")
    assert truncated["success"] is False and truncated["reason_code"] == TEST_EVIDENCE_INCONSISTENT
    assert truncated["test_execution"]["reason"].startswith("STRUCTURED_REPORT_UNREADABLE")
    # No report at all is no contradiction: INDETERMINATE evidence, the whole-output authority (REG-R1, unchanged)
    missing = gate_with_report(root, None, stdout="1 passed")
    assert missing["success"] is True and "reason_code" not in missing
    assert missing["test_execution"]["reason"] == "STRUCTURED_REPORT_MISSING"


def test_09_console_text_never_decides(tmp_path):
    root = write_project(tmp_path / "ws")
    # structured pass, prose disagreement: the verdict follows the exit code and the document
    result = gate_with_report(root, suite([("a", None)]), stdout="1 failed, something odd")
    assert result["success"] is True and "reason_code" not in result
    # success text with a contradictory structured record: a failed case in a complete report is never PASS
    result = gate_with_report(root, suite([("a", "failure")]), stdout="1 passed in 0.01s")
    assert result["success"] is False and result["reason_code"] == TEST_EVIDENCE_INCONSISTENT
    assert "1 case(s) not passed in the structured report: c.a" in result["output"]


def test_10_a_failing_process_keeps_its_own_verdict_and_reason(tmp_path):
    root = write_project(tmp_path / "ws")
    result = gate_with_report(root, suite([("a", None)], tests=2), returncode=1, stdout="1 failed")
    assert result["success"] is False and "reason_code" not in result and result["output"] == "1 failed"


def test_11_evidence_inconsistency_is_none_for_indeterminate_but_uncontradicted_reports():
    for runner, reason in (("none", "NO_STRUCTURED_REPORT_FOR_RUNNER:none"), ("pytest", "REPORT_DESTINATION_UNAVAILABLE:OSError"),
                           ("maven", "STRUCTURED_REPORT_MISSING"), ("pytest", "STRUCTURED_REPORT_MISSING"),
                           ("pytest", "TEST_PROCESS_NOT_RUN"), ("pytest", "GATE_TIMED_OUT")):
        report = test_execution.TestExecutionReport(gate_id="g", runner=runner, workspace="w", reason=reason)
        assert evidence_inconsistency(report) is None, reason
    for reason in ("JUNIT_COUNTS_INCONSISTENT", "JUNIT_DUPLICATE_CASE", "JUNIT_COUNTS_UNDECLARED",
                   "STRUCTURED_REPORT_UNREADABLE:ParseError"):
        report = test_execution.TestExecutionReport(gate_id="g", runner="pytest", workspace="w", reason=reason)
        assert evidence_inconsistency(report) == reason


def test_12_jvm_collection_folds_integrity_into_completeness(tmp_path):
    reports = tmp_path / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (tmp_path / "pom.xml").write_text("<project/>")

    def run(second):  # bind first (stale reports are cleared), then the "build" writes its reports
        binding = test_execution.prepare(str(tmp_path), "maven")
        (reports / "TEST-a.xml").write_bytes(suite([("a", None)]))
        (reports / "TEST-b.xml").write_bytes(second)
        binding.observe({"returncode": 0, "stdout": "BUILD SUCCESS", "stderr": ""})
        return test_execution.collect(binding)

    report = run(suite([("b", None)], tests=3))
    assert report.completeness == INDETERMINATE and report.reason == "JUNIT_COUNTS_INCONSISTENT"
    assert [case.name for case in report.cases] == ["a", "b"]  # the cases are still recorded as evidence
    assert evidence_inconsistency(report) == "JUNIT_COUNTS_INCONSISTENT"
    assert run(suite([("b", None)])).completeness == COMPLETE


def test_13_the_real_subtest_document_parses_as_pytest_writes_it(tmp_path):
    """The document itself, not Kriya's summary of it: one element per test id, counts above the elements."""
    root = write_project(tmp_path / "ws", SUBTEST_SUITE)
    result = validator_for(root).run_tests()
    document = ET.fromstring(result["pytest_evidence"]["raw"]["junit"])
    suites = [e for e in document.iter() if e.tag == "testsuite"]
    assert int(suites[0].get("tests")) == 7 and len([e for e in document.iter() if e.tag == "testcase"]) == 3
    assert sys.version_info >= (3, 10)

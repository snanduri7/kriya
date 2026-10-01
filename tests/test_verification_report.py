"""MA6.11: VerificationReport integration (kriya/workflow/verification_report.py) -
first real pytest coverage for this module."""

from kriya.workflow.plan_schema import AcceptanceCriterion, VerificationMethodType
from kriya.workflow.verification_report import build_verification_report
from kriya.workflow.workflow_types import VerificationVerdict


def _tool_criterion(cid, tool_name="compile_check"):
    return AcceptanceCriterion(id=cid, description="compiles", method=VerificationMethodType.TOOL, tool_name=tool_name)


def _judgment_criterion(cid):
    return AcceptanceCriterion(id=cid, description="looks right")


def test_empty_criteria_and_no_gate_result_is_needs_review():
    report = build_verification_report([])
    assert report.verdict == VerificationVerdict.NEEDS_REVIEW
    assert report.checks == ()


def test_judgment_criterion_with_no_grader_is_unresolved_never_self_graded():
    report = build_verification_report([_judgment_criterion("ac1")])
    assert len(report.checks) == 1
    assert report.checks[0].passed is None
    assert report.verdict == VerificationVerdict.NEEDS_REVIEW


def test_judgment_criterion_resolved_by_independent_pass():
    report = build_verification_report([_judgment_criterion("ac1")], judgment_results={"ac1": True})
    assert report.checks[0].passed is True
    assert report.verdict == VerificationVerdict.PASS


def test_judgment_criterion_resolved_as_failed():
    report = build_verification_report([_judgment_criterion("ac1")], judgment_results={"ac1": False})
    assert report.checks[0].passed is False
    assert report.verdict == VerificationVerdict.FAIL
    assert len(report.failures) == 1


def test_tool_criterion_unresolved_when_tool_has_not_run():
    report = build_verification_report([_tool_criterion("ac1")])
    assert report.checks[0].passed is None
    assert report.verdict == VerificationVerdict.NEEDS_REVIEW


def test_tool_criterion_resolved_pass():
    report = build_verification_report([_tool_criterion("ac1", tool_name="compile_check")], tool_results={"compile_check": True})
    assert report.checks[0].passed is True
    assert report.verdict == VerificationVerdict.PASS


def test_tool_criterion_resolved_fail():
    report = build_verification_report([_tool_criterion("ac1", tool_name="compile_check")], tool_results={"compile_check": False})
    assert report.checks[0].passed is False
    assert report.verdict == VerificationVerdict.FAIL


def test_existing_gate_failure_forces_fail_even_if_all_criteria_pass():
    report = build_verification_report(
        [_judgment_criterion("ac1")], judgment_results={"ac1": True},
        existing_gate_success=False, existing_gate_failures=["compile error in b.py"],
    )
    assert report.verdict == VerificationVerdict.FAIL
    assert "compile error in b.py" in report.failures


def test_existing_gate_success_true_with_all_criteria_passed_is_pass():
    report = build_verification_report(
        [_judgment_criterion("ac1")], judgment_results={"ac1": True}, existing_gate_success=True,
    )
    assert report.verdict == VerificationVerdict.PASS


def test_mixed_resolved_and_unresolved_criteria_is_needs_review_not_pass():
    report = build_verification_report(
        [_judgment_criterion("ac1"), _judgment_criterion("ac2")],
        judgment_results={"ac1": True},
    )
    assert report.verdict == VerificationVerdict.NEEDS_REVIEW


def test_failed_criterion_takes_priority_over_unresolved_criterion():
    report = build_verification_report(
        [_judgment_criterion("ac1"), _judgment_criterion("ac2")],
        judgment_results={"ac1": False},
    )
    assert report.verdict == VerificationVerdict.FAIL

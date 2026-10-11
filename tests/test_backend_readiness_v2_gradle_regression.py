"""BACKEND-READINESS-V2, GRADLE-REGRESSION-DELTA-PER-TEST-001: structured per-test regression evidence for Gradle.

Mechanism measured on the frozen JavaHamcrest run (handover/BACKEND-READINESS-V2/01, case T3): the candidate wrote a
Java regex with a property that does not exist, three of the edited class's own tests failed with
``PatternSyntaxException: Unknown character property name {Whitespace}``, and the full-regression delta - which read per-test
outcomes only from Maven Surefire console blocks or pytest output - had nothing to attribute, so the run stopped
REGRESSION_UNATTRIBUTED with no Developer retry and no failing test names. Gradle's own JUnit XML (already read by the
test-execution evidence) names every failing test and its exception. The fixtures reproduce that shape synthetically.
"""
import xml.sax.saxutils as saxutils

from kriya.tools import test_execution
from kriya.workflow.validation_baseline import (
    DeltaClassification,
    ValidationBaseline,
    ValidationInvocation,
    build_validation_outcome,
    classify_baseline_delta,
    regression_unattributed_diagnosis,
    render_blocking_regression_evidence,
)

CLASS = "org.demo.text.WhitespaceMatcherTest"
GRADLE_CONSOLE_FAIL = (
    "> Task :demo:test\n\n"
    f"{CLASS.rsplit('.', 1)[1]} > testTrims() FAILED\n"
    "    java.util.regex.PatternSyntaxException at WhitespaceMatcherTest.java:34\n\n"
    "7 tests completed, 3 failed\n\nFAILURE: Build failed with an exception.\n")
GRADLE_CONSOLE_PASS = "> Task :demo:test\n\nBUILD SUCCESSFUL in 2s\n"
REGEX_MESSAGE = "java.util.regex.PatternSyntaxException: Unknown character property name {Whitespace} near index 13\n\\p{Whitespace}+\n             ^"


def _case(name, failure=None, error=None, skipped=False, classname=CLASS):
    body = f'<testcase name="{name}" classname="{classname}" time="0.0">'
    if failure is not None:
        kind, message = failure
        body += f'<failure message={saxutils.quoteattr(message)} type="{kind}">stack trace text</failure>'
    if error is not None:
        kind, message = error
        body += f'<error message={saxutils.quoteattr(message)} type="{kind}">stack trace text</error>'
    if skipped:
        body += "<skipped/>"
    return body + "</testcase>"


def _suite(cases, classname=CLASS):
    failures = sum("<failure" in c for c in cases)
    errors = sum("<error" in c for c in cases)
    skipped = sum("<skipped" in c for c in cases)
    return (f'<?xml version="1.0" encoding="UTF-8"?><testsuite name="{classname}" tests="{len(cases)}" skipped="{skipped}" '
            f'failures="{failures}" errors="{errors}">' + "".join(cases) + "</testsuite>")


def _gradle_run(tmp_path, label, cases, console, success, report_exit=1, write_report=True):
    """The gate result of one Gradle test invocation: the build output plus the TestExecutionReport collected from the
    JUnit XML that invocation produced (the same prepare -> run -> observe -> collect sequence the validator performs)."""
    ws = tmp_path / label
    (ws / "demo").mkdir(parents=True)
    (ws / "demo" / "build.gradle").write_text("// build\n")
    binding = test_execution.prepare(str(ws), "gradle")
    if write_report:
        results = ws / "demo" / "build" / "test-results" / "test"
        results.mkdir(parents=True)
        (results / f"TEST-{CLASS}.xml").write_text(_suite(cases))
    binding.observe({"returncode": report_exit if not success else 0})
    return {"success": success, "output": console, "test_execution": test_execution.collect(binding).to_dict()}


def _baseline(raw):
    return ValidationBaseline(
        workspace_revision="rev", run_id="run", invocation=ValidationInvocation("gradle test", "full"), captured_at=0.0,
        outcome=build_validation_outcome(raw))


PASSING = [_case("testTrims()"), _case("testIgnoresNull()"), _case("testMismatch()"), _case("testCollapses()")]
BROKEN = [_case("testTrims()", failure=("java.util.regex.PatternSyntaxException", REGEX_MESSAGE)),
          _case("testIgnoresNull()"),
          _case("testMismatch()", failure=("java.util.regex.PatternSyntaxException", REGEX_MESSAGE)),
          _case("testCollapses()", error=("java.lang.IllegalStateException", "boom"))]


def test_t3_a_candidate_that_breaks_three_gradle_tests_is_attributed_per_test_with_the_exception_text(tmp_path):
    pre = _gradle_run(tmp_path, "pre", PASSING, GRADLE_CONSOLE_PASS, success=True)
    post = _gradle_run(tmp_path, "post", BROKEN, GRADLE_CONSOLE_FAIL, success=False)
    baseline = _baseline(pre)
    outcome = build_validation_outcome(post)
    assert outcome.test_outcomes is not None and {o.test_id for o in outcome.test_outcomes} == {
        f"{CLASS}.testTrims", f"{CLASS}.testMismatch", f"{CLASS}.testCollapses"}
    assert outcome.aggregate_counts == {"passed": 1, "failed": 2, "error": 1, "skipped": 0}
    from kriya.workflow.validation_baseline import TestStatus

    assert {o.test_id: o.status for o in outcome.test_outcomes} == {
        f"{CLASS}.testTrims": TestStatus.FAIL, f"{CLASS}.testMismatch": TestStatus.FAIL, f"{CLASS}.testCollapses": TestStatus.ERROR}
    delta = classify_baseline_delta(baseline, outcome)
    assert delta.level2_available is True and delta.blocking is True
    assert {t: c for t, c in delta.level2.items()} == {
        f"{CLASS}.testTrims": DeltaClassification.NEW_FAILURE, f"{CLASS}.testMismatch": DeltaClassification.NEW_FAILURE,
        f"{CLASS}.testCollapses": DeltaClassification.NEW_FAILURE}
    confirmed = sorted(t for t, c in delta.level2.items() if c is DeltaClassification.NEW_FAILURE)
    details = test_execution.failure_details(post)
    text = render_blocking_regression_evidence(baseline=baseline, confirmed_test_ids=confirmed,
                                               raw_output=post["output"], failure_details=details)
    assert "Unknown character property name {Whitespace}" in text and "IllegalStateException" in text
    assert "failure detail unavailable" not in text
    assert text.count("=== ") == 3  # one section per confirmed regression, nothing else


def test_t3_gradle_negative_controls_stay_fail_closed(tmp_path):
    pre = _gradle_run(tmp_path, "pre", PASSING, GRADLE_CONSOLE_PASS, success=True)
    baseline = _baseline(pre)
    # no structured report (a custom test command): per-test evidence is unavailable and the gate stays closed
    missing = _gradle_run(tmp_path, "nr", BROKEN, GRADLE_CONSOLE_FAIL, success=False, write_report=False)
    outcome = build_validation_outcome(missing)
    assert outcome.test_outcomes is None
    delta = classify_baseline_delta(baseline, outcome)
    assert delta.level2_available is False and delta.blocking is True
    # a report that contradicts its own counts is not a complete record -> never positive evidence
    ws_bad = tmp_path / "bad"
    (ws_bad / "demo" / "build" / "test-results" / "test").mkdir(parents=True)
    (ws_bad / "demo" / "build.gradle").write_text("// b\n")
    binding = test_execution.prepare(str(ws_bad), "gradle")
    (ws_bad / "demo" / "build" / "test-results" / "test" / "TEST-x.xml").write_text(
        _suite(BROKEN).replace('failures="2"', 'failures="0"'))
    binding.observe({"returncode": 1})
    inconsistent = {"success": False, "output": GRADLE_CONSOLE_FAIL, "test_execution": test_execution.collect(binding).to_dict()}
    assert build_validation_outcome(inconsistent).test_outcomes is None
    # a failing PRE test that fails the same way in POST is pre-existing; the same test failing differently is a changed failure
    pre_failing = _gradle_run(tmp_path, "pf", [_case("testTrims()", failure=("java.lang.AssertionError", "expected a but was b")),
                                              _case("testIgnoresNull()")], GRADLE_CONSOLE_FAIL, success=False)
    same = _gradle_run(tmp_path, "same", [_case("testTrims()", failure=("java.lang.AssertionError", "expected a but was b")),
                                          _case("testIgnoresNull()")], GRADLE_CONSOLE_FAIL, success=False)
    other = _gradle_run(tmp_path, "oth", [_case("testTrims()", failure=("java.lang.AssertionError", "expected a but was c")),
                                          _case("testIgnoresNull()")], GRADLE_CONSOLE_FAIL, success=False)
    failing_baseline = _baseline(pre_failing)
    same_delta = classify_baseline_delta(failing_baseline, build_validation_outcome(same))
    assert same_delta.level2 == {f"{CLASS}.testTrims": DeltaClassification.PRE_EXISTING_FAILURE} and same_delta.blocking is False
    other_delta = classify_baseline_delta(failing_baseline, build_validation_outcome(other))
    assert other_delta.level2 == {f"{CLASS}.testTrims": DeltaClassification.CHANGED_FAILURE} and other_delta.blocking is True
    # the structured set, not the console, is the Level-1 identity: unrelated console noise cannot make an unchanged
    # failing set look changed (the Surefire summary fingerprint exists for the same reason)
    noisy = _gradle_run(tmp_path, "noise", [_case("testTrims()", failure=("java.lang.AssertionError", "expected a but was b")),
                                            _case("testIgnoresNull()")],
                        "Download https://repo.example/a-9.jar\nDaemon started pid 98765\n" + GRADLE_CONSOLE_FAIL, success=False)
    assert build_validation_outcome(noisy).failure_fingerprint == build_validation_outcome(same).failure_fingerprint
    assert classify_baseline_delta(failing_baseline, build_validation_outcome(noisy)).blocking is False
    # a failed gate whose complete report names no failing test (the build failed for another reason) is not per-test evidence
    nothing_failing = _gradle_run(tmp_path, "nf", PASSING, GRADLE_CONSOLE_FAIL, success=False)
    assert build_validation_outcome(nothing_failing).test_outcomes is None
    # only the JVM runners use the XML path: a pytest report keeps its own authority (REG-R1)
    broken = _gradle_run(tmp_path, "brk", BROKEN, GRADLE_CONSOLE_FAIL, success=False)
    assert build_validation_outcome(broken).test_outcomes is not None  # the same failing report IS evidence for a JVM runner ...
    foreign = {**broken, "output": "no recognizable console", "test_execution": {**broken["test_execution"], "runner": "pytest"}}
    assert build_validation_outcome(foreign).test_outcomes is None  # ... and is not for any other runner
    # a case status this reader does not know is not evidence, even beside well-formed cases
    weird = {**broken, "test_execution": {**broken["test_execution"], "test_cases": [
        {**broken["test_execution"]["test_cases"][0], "status": "mystery"}, *broken["test_execution"]["test_cases"][1:]]}}
    assert build_validation_outcome(weird).test_outcomes is None
    # a test that stops being reported is a silent disappearance, not a pass
    shrunk = _gradle_run(tmp_path, "sh", PASSING[:2], GRADLE_CONSOLE_PASS, success=True)
    assert classify_baseline_delta(baseline, build_validation_outcome(shrunk)).aggregate_drop_detected is True


def test_t3_the_unattributed_message_never_claims_replays_that_did_not_run(tmp_path):
    """With no per-test evidence for the run the message says exactly that; the replay wording stays only where per-test
    entries were actually replayed (REGRESSION-UNATTRIBUTED-PROSE-001)."""
    pre = _gradle_run(tmp_path, "pre", PASSING, GRADLE_CONSOLE_PASS, success=True)
    post = _gradle_run(tmp_path, "nr", BROKEN, GRADLE_CONSOLE_FAIL, success=False, write_report=False)
    delta = classify_baseline_delta(_baseline(pre), build_validation_outcome(post))
    state, message = regression_unattributed_diagnosis(delta)
    assert message.startswith("REGRESSION_UNATTRIBUTED:") and delta.level2_available is False
    assert "independently replayed" not in message and "no per-test evidence" in message
    assert state == "aggregate_delta"

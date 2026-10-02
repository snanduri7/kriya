"""Live matrix, spring-petclinic (run 5705698c): the candidate passed its gates,
then the brownfield full-regression comparison blocked it - REGRESSION_
UNATTRIBUTED, level1=CHANGED_FAILURE - for a failure that exists before any
change (PostgresIntegrationTests: JNA cannot map its native library in the
container). MEASURED on the unchanged tree: two runs give different
whole-output fingerprints (Spring Boot log timestamps, the container's random
hostname, log interleaving), so a Maven test run with any pre-existing
failure could never compare as pre-existing, and Maven had no per-test
parser. The Surefire "Results:" blocks are now the identity of a Maven test
run (level 1) and its per-test outcomes (level 2).
"""
from kriya.workflow.validation_baseline import (
    DeltaClassification,
    ValidationBaseline,
    ValidationInvocation,
    build_validation_outcome,
    classify_baseline_delta,
    compute_failure_fingerprint,
    parse_surefire_structured_outcomes,
)


def _run(host, clock, tmp, failures=(), errors=(("PostgresIntegrationTests.available:68", "UnsatisfiedLink"),),
         run=80, skipped=2):
    lines = [
        f"[INFO] Unable to get build host, skipping property build.host. Error message: {host}: {host}: "
        "Temporary failure in name resolution",
        f"{clock} [main] INFO org.springframework.boot.test.context.SpringBootTestContextBootstrapper -- Found "
        "@SpringBootConfiguration for test class OwnerControllerTests",
        "[INFO] Tests run: 18, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 1.092 s -- in OwnerControllerTests",
        "[INFO] ", "[INFO] Results:", "[INFO] ",
    ]
    if failures:
        lines.append("[ERROR] Failures: ")
        lines += [f"[ERROR]   {test} {reason}" for test, reason in failures]
    if errors:
        lines.append("[ERROR] Errors: ")
        lines += [f"[ERROR]   {test} » {reason} /kriya/tmp/.cache/JNA/temp/jna{tmp}.tmp: failed to map segment"
                  for test, reason in errors]
    lines += ["[INFO] ", f"[ERROR] Tests run: {run}, Failures: {len(failures)}, Errors: {len(errors)}, "
              f"Skipped: {skipped}", "[INFO] BUILD FAILURE", f"[INFO] Finished at: 2026-10-02T06:3{clock[-1]}:52Z"]
    return "\n".join(lines) + "\n"


def _baseline(output):
    return ValidationBaseline(
        workspace_revision="r", run_id="pre", captured_at=0.0, status="captured",
        invocation=ValidationInvocation(command_identity="mvn test", selection_identity="full"),
        outcome=build_validation_outcome({"success": False, "output": output}))


PRE = _run("2c7333fc0ee6", "06:32:29.328", "16066322533976538267")
POST_SAME = _run("242d708ea191", "06:33:18.194", "18145953065786441971")


def test_two_runs_of_the_same_failure_compare_as_pre_existing():
    delta = classify_baseline_delta(_baseline(PRE), build_validation_outcome({"success": False, "output": POST_SAME}))
    assert delta.level1.classification is DeltaClassification.PRE_EXISTING_FAILURE
    assert delta.level2_available and not delta.blocking, delta.blocking_reasons


def test_a_new_failing_test_still_blocks():
    post = _run("242d708ea191", "06:33:18.194", "18145953065786441971",
                failures=(("OwnerControllerTests.testProcessFindFormSuccess:118", "expected: <5> but was: <2>"),))
    delta = classify_baseline_delta(_baseline(PRE), build_validation_outcome({"success": False, "output": post}))
    assert delta.blocking and delta.level1.classification is DeltaClassification.CHANGED_FAILURE
    # The new failure is never excused as pre-existing (passing tests are not listed by Surefire, so it is
    # not comparable - attribution replays it in isolation), the old one is.
    assert delta.level2["OwnerControllerTests.testProcessFindFormSuccess"] is not \
        DeltaClassification.PRE_EXISTING_FAILURE
    assert delta.level2["PostgresIntegrationTests.available"] is DeltaClassification.PRE_EXISTING_FAILURE


def test_the_parser_reads_ids_statuses_and_totals_of_every_module():
    module_b = _run("x", "06:40:00.000", "1", errors=(), failures=(("VetTests.serialization:40", "boom"),),
                    run=5, skipped=0)
    outcomes, counts = parse_surefire_structured_outcomes(PRE + module_b)
    assert {(o.test_id, o.status.value) for o in outcomes} == {
        ("PostgresIntegrationTests.available", "error"), ("VetTests.serialization", "fail")}
    assert counts == {"passed": 77 + 4, "failed": 1, "error": 1, "skipped": 2}


def test_output_without_surefire_results_keeps_the_whole_text_fingerprint():
    compile_failure = "[ERROR] COMPILATION ERROR :\n[ERROR] /w/src/A.java:[3,1] class, interface expected\n"
    outcome = build_validation_outcome({"success": False, "output": compile_failure})
    assert outcome.failure_fingerprint == compute_failure_fingerprint(compile_failure)
    assert outcome.test_outcomes is None

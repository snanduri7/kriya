"""REG-R1 - pytest per-test regression authority with same-context untouched-baseline field stability.

Live defect (OBS-4, run 20261006T231821-fb31caad): two runs of the same untouched code differed in their whole
pytest output (a pre-existing failure's measured ratio, a container hostname in another's traceback); the
whole-output fingerprint called that CHANGED_FAILURE and stopped a candidate that changed no test's outcome. A second
mechanism (6d242e7 deterministic replay): a pre-existing failure that fails in every full-suite run but passes when
run alone cannot be measured by replaying it alone.

Invariant: a candidate may be blamed only for a difference that is stable on the untouched baseline, measured in the
SAME verification context that produced the disputed evidence. Authority order for complete pytest reports: test id,
outcome, exception type, message, failure body. Whole output is diagnostic when per-test evidence is complete;
missing/incomplete evidence and context mismatch fail closed. (REG-R2 replaced the rule that baseline outcome/type
instability alone fails closed: tests/test_reg_r2_flaky_baseline_envelope.py.)

``test_01``..``test_18`` are the first required matrix; ``test_c01``..``test_c12`` the context-stability controls;
the real-pytest tests use tests/_reg_r1_fixture.py.
"""
import json

import pytest
from _reg_r1_fixture import (
    STABLE,
    SUITE,
    SUITE_DEPENDENT,
    VARYING_BODY,
    VARYING_MESSAGE,
    validator_for,
    write_project,
)

from kriya.tools import test_execution
from kriya.tools.test_execution import PYTEST_CASE_EVIDENCE_VERSION, parse_pytest_case_evidence
from kriya.workflow.pytest_stability import (
    BASELINE_REPLAYS,
    classify_with_baseline_stability,
    record_regression_decision,
    verification_context,
    verification_context_id,
)
from kriya.workflow.validation_baseline import (
    FIELD_STABLE,
    FIELD_VOLATILE,
    OLD_CHECKPOINT_INSUFFICIENT,
    PYTEST_PER_TEST_AUTHORITY,
    WHOLE_OUTPUT_AUTHORITY,
    DeltaClassification,
    PytestSuiteEvidence,
    ValidationBaseline,
    ValidationInvocation,
    ValidationOutcome,
    baseline_behavior_envelope,
    build_validation_outcome,
    capture_brownfield_baselines,
    classify_baseline_delta,
    compute_failure_fingerprint,
    junit_key_for_node_id,
    pytest_suite_evidence,
)

D = DeltaClassification
KEY = "tests.t::test_x"
NODE = "tests/t.py::test_x"
OTHER = "tests.t::test_y"
OTHER_NODE = "tests/t.py::test_y"
SESSION = "platform t -- Python 3.12, pytest-9.1.1, pluggy-1.6.0\nrootdir: /w\nplugins: p-1.0\n"
PASSED = ("passed", None, None, None)


def F(message="m1", body="b1", failure_type="AssertionError", outcome="failed"):  # noqa: N802 - a case literal
    return (outcome, failure_type, message, body)


# ---------------------------------------------------------------- raw gate results (the run_tests shape)

def raw_result(cases, *, complete=True, integrity_ok=True, session=SESSION, extra="", success=False):
    """What run_tests returns for pytest: ``cases`` maps key -> (outcome, type, message, body)."""
    junit = {key: {"outcome": o, "failure_type": t, "message": m, "body": b} for key, (o, t, m, b) in cases.items()}
    lines = [f"FAILED {key.split('::')[0].replace('.', '/')}.py::{key.split('::')[1]} - x"
             for key, (o, *_rest) in cases.items() if o in ("failed", "error")]
    return {"success": success,
            "output": "=== test session starts ===\n" + session + "\n".join(lines) + f"\n{extra}\n=== 1 failed in 0.1s ===",
            "pytest_evidence": {"version": PYTEST_CASE_EVIDENCE_VERSION, "complete": complete,
                                "reason": None if complete else "PYTEST_REPORT_INCOMPLETE",
                                "report_files": [{"path": "r.xml", "sha256": "d"}],
                                "evidence": {"cases": junit, "integrity": {"ok": integrity_ok,
                                                                           "reason": None if integrity_ok else "X"}},
                                "raw": {"stdout": "", "stderr": "", "junit": ""}}}


def make_baseline(cases, *, revision="rev", environment="env", command="polymorphic_validator.run_tests",
                  selection=None, run_id="run", extra=""):
    return ValidationBaseline(
        workspace_revision=revision, run_id=run_id, captured_at=0.0,
        outcome=build_validation_outcome(raw_result(cases, extra=extra)),
        invocation=ValidationInvocation(
            command_identity=command, environment_fingerprint=environment, target_test=selection,
            selection_identity="full_suite" if selection is None else f"target_test:{json.dumps(list(selection))}"))


def replays_returning(*results):
    calls = []

    def replay(selection):
        calls.append(selection)
        return results[min(len(calls) - 1, len(results) - 1)]
    replay.calls = calls
    return replay


def decide(pre, post, *, replay=None, cache=None, post_success=False, revision="rev", base=None, post_extra="",
           working_directory="/w"):
    base = base or make_baseline(pre)
    post_outcome = build_validation_outcome(raw_result(post, success=post_success, extra=post_extra))
    return classify_with_baseline_stability(
        baseline=base, post=post_outcome, post_environment=None, cache={} if cache is None else cache,
        replay=replay or replays_returning(), current_revision=lambda: revision, working_directory=working_directory)


def fields(measurement, key):
    """The message/body stability of a disputed test whose baseline showed one failing state."""
    [state] = measurement.envelopes[key].states
    return dict(state.fields)


def record_fields(record):
    """The same from a retained stability record: (outcome-stable, type-stable, message, body)."""
    [state] = record["envelope"]["states"]
    return state["fields"]


def outcome_of(evidence, *, success=False, output="out"):
    return ValidationOutcome(execution_status="completed", success=success,
                             failure_fingerprint=None if success else compute_failure_fingerprint(output),
                             pytest_evidence=evidence)


# ---------------------------------------------------------------- the first required matrix

def test_01_identical_complete_result_is_no_regression():
    delta, measurement = decide({KEY: F(), OTHER: PASSED}, {KEY: F(), OTHER: PASSED})
    assert delta.authority == PYTEST_PER_TEST_AUTHORITY and measurement is None
    assert delta.blocking is False and delta.level2 == {NODE: D.PRE_EXISTING_FAILURE}


def test_02_same_failing_test_same_type_message_body_is_pre_existing():
    delta, _ = decide({KEY: F()}, {KEY: F()}, post_extra="other output")
    assert delta.level2 == {NODE: D.PRE_EXISTING_FAILURE} and not delta.blocking and delta.stability_required == {}


def test_03_message_varying_on_the_untouched_baseline_is_ignored_for_that_test():
    replay = replays_returning(raw_result({KEY: F("m-A")}), raw_result({KEY: F("m-B")}))
    delta, measurement = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replay)
    assert delta.level2 == {NODE: D.PRE_EXISTING_FAILURE} and not delta.blocking
    assert delta.volatile_fields_ignored == {NODE: ("message",)}
    assert fields(measurement, KEY) == {"message": FIELD_VOLATILE, "body": FIELD_STABLE}


def test_04_message_stable_on_the_untouched_baseline_and_changed_by_the_candidate_blocks():
    stable = raw_result({KEY: F("m1")})
    delta, measurement = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replays_returning(stable, stable))
    assert fields(measurement, KEY) == {"message": FIELD_STABLE, "body": FIELD_STABLE}
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking
    assert delta.blocking_reasons == (f"level2:{NODE}:CHANGED_FAILURE",)


def test_05_body_varying_on_the_untouched_baseline_is_ignored_for_that_test():
    replay = replays_returning(raw_result({KEY: F(body="run A")}), raw_result({KEY: F(body="run B")}))
    delta, measurement = decide({KEY: F(body="b1")}, {KEY: F(body="b2")}, replay=replay)
    assert fields(measurement, KEY) == {"message": FIELD_STABLE, "body": FIELD_VOLATILE}
    assert delta.level2 == {NODE: D.PRE_EXISTING_FAILURE} and delta.volatile_fields_ignored == {NODE: ("body",)}


def test_06_exception_type_change_blocks():
    """REG-R2: decided against the whole baseline envelope (not the original observation alone); a type the stable
    baseline never showed is outside it."""
    replay = replays_returning(raw_result({KEY: F()}))
    delta, measurement = decide({KEY: F()}, {KEY: F(failure_type="KeyError")}, replay=replay)
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking
    assert replay.calls == [None, None] and not measurement.envelopes[KEY].flaky


def test_06b_failed_to_error_outcome_change_blocks():
    delta, _ = decide({KEY: F()}, {KEY: F(outcome="error")}, replay=replays_returning(raw_result({KEY: F()})))
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking


def test_07_pass_to_fail_blocks():
    delta, _ = decide({KEY: PASSED}, {KEY: F()}, replay=replays_returning(raw_result({KEY: PASSED})))
    assert delta.level2 == {NODE: D.NEW_FAILURE} and delta.blocking


def test_08_fail_to_pass_is_an_improvement_not_a_regression():
    delta, _ = decide({KEY: F()}, {KEY: PASSED}, post_success=True)
    assert delta.level2 == {NODE: D.RESOLVED_FAILURE} and not delta.blocking


def test_09_new_failing_test_blocks_and_a_new_passing_test_does_not():
    delta, _ = decide({OTHER: PASSED}, {OTHER: PASSED, KEY: F(), "tests.t::test_new": PASSED})
    assert delta.level2 == {NODE: D.NEW_FAILURE} and delta.blocking


@pytest.mark.parametrize("before", [PASSED, F(), ("skipped", None, None, None)])
def test_10_expected_test_missing_fails_closed(before):
    delta, _ = decide({KEY: before, OTHER: PASSED}, {OTHER: PASSED}, post_success=True)
    shown = NODE if before[0] == "failed" else KEY
    assert delta.level2[shown] == D.NEWLY_SKIPPED_OR_NOT_EXECUTED and delta.blocking


def test_10b_pass_to_skip_and_fail_to_skip_block():
    for before in (PASSED, F()):
        delta, _ = decide({KEY: before}, {KEY: ("skipped", None, None, None)}, post_success=True,
                          replay=replays_returning(raw_result({KEY: before})))
        assert list(delta.level2.values()) == [D.NEWLY_SKIPPED_OR_NOT_EXECUTED] and delta.blocking


@pytest.mark.parametrize("side", ["PRE", "POST"])
def test_11_incomplete_report_fails_closed_even_when_the_whole_output_matches(side):
    """An aggregate match is never proof: identical whole outputs (level 1 PRE_EXISTING) still block."""
    complete = pytest_suite_evidence(raw_result({KEY: F()}))
    incomplete = pytest_suite_evidence(raw_result({KEY: F()}, complete=False))
    pre, post = (incomplete, complete) if side == "PRE" else (complete, incomplete)
    base = ValidationBaseline(workspace_revision="rev", run_id="r", captured_at=0.0, outcome=outcome_of(pre),
                              invocation=ValidationInvocation("c", "full_suite"))
    delta = classify_baseline_delta(base, outcome_of(post))
    assert delta.level1.classification == D.PRE_EXISTING_FAILURE
    assert delta.blocking and delta.authority == WHOLE_OUTPUT_AUTHORITY
    assert delta.blocking_reasons[-1] == f"pytest_evidence_incomplete:{side}:PYTEST_REPORT_INCOMPLETE"


def test_11b_a_fully_passing_post_suite_without_complete_evidence_is_not_blocked():
    base = make_baseline({KEY: F()})
    post = build_validation_outcome(raw_result({}, complete=False, success=True))
    delta = classify_baseline_delta(base, post)
    assert not delta.blocking and delta.pytest_evidence_status.startswith("POST:PYTEST_REPORT_INCOMPLETE")


def test_12_real_collection_failure_fails_closed(tmp_path):
    root = write_project(tmp_path / "ws")
    pre = validator_for(root).run_tests()
    (root / "tests" / "test_broken.py").write_text("def test_(:\n")
    post = validator_for(root).run_tests()
    assert post["pytest_evidence"]["complete"] is False                       # pytest exit 2
    base = ValidationBaseline(workspace_revision="rev", run_id="r", captured_at=0.0,
                              outcome=build_validation_outcome(pre), invocation=ValidationInvocation("c", "full_suite"))
    delta = classify_baseline_delta(base, build_validation_outcome(post))
    assert delta.blocking
    assert delta.blocking_reasons[-1] == "pytest_evidence_incomplete:POST:PYTEST_SESSION_INCOMPLETE:exit_2"


def test_13_malformed_or_inconsistent_junit_fails_closed(tmp_path):
    with pytest.raises(Exception):
        parse_pytest_case_evidence(b"<testsuite><testcase")
    inconsistent = parse_pytest_case_evidence(
        b'<testsuite tests="2" failures="0" errors="0" skipped="0"><testcase classname="a" name="t"/></testsuite>')
    assert (inconsistent["integrity"]["ok"], inconsistent["integrity"]["reason"]) == (False, "JUNIT_COUNTS_INCONSISTENT")
    duplicated = parse_pytest_case_evidence(
        b'<testsuite tests="2" failures="0" errors="0" skipped="0"><testcase classname="a" name="t"/>'
        b'<testcase classname="a" name="t"/></testsuite>')
    assert duplicated["integrity"]["reason"] == "JUNIT_DUPLICATE_CASE"
    undeclared = parse_pytest_case_evidence(b'<testsuite><testcase classname="a" name="t"/></testsuite>')
    assert undeclared["integrity"]["reason"] == "JUNIT_COUNTS_UNDECLARED"
    # through the production collector: a malformed report is INDETERMINATE evidence, and the comparator fails closed
    binding = test_execution.prepare(str(tmp_path), "pytest")
    (tmp_path / binding.pytest_report).write_bytes(b"<testsuite><testcase")
    binding.observe({"returncode": 1, "stdout": "o", "stderr": ""})
    report = test_execution.collect(binding)
    malformed = {"success": False, "output": "FAILED tests/t.py::test_x - e", "pytest_evidence": report.pytest_evidence()}
    evidence = pytest_suite_evidence(malformed)
    assert evidence.complete is False and evidence.reason.startswith("STRUCTURED_REPORT_UNREADABLE")
    for bad in (raw_result({KEY: F()}, integrity_ok=False), malformed):
        delta = classify_baseline_delta(make_baseline({KEY: F()}), build_validation_outcome(bad))
        assert delta.blocking and delta.authority == WHOLE_OUTPUT_AUTHORITY


def test_14_aggregate_output_differs_but_complete_stable_per_test_evidence_matches_is_diagnostic_only():
    delta, _ = decide({KEY: F()}, {KEY: F()}, post_extra="run 2 host=b ratio 3.1x at 0xdeadbeef")
    assert delta.level1.classification == D.CHANGED_FAILURE              # still computed, as a diagnostic
    assert not delta.blocking and delta.blocking_reasons == ()


def test_15_aggregate_output_matches_but_per_test_evidence_regresses_blocks():
    base = make_baseline({KEY: PASSED, OTHER: F()}, extra="same")
    post = build_validation_outcome(raw_result({KEY: F(), OTHER: F()}, extra="same"))
    delta = classify_baseline_delta(base, post)
    assert delta.blocking and delta.level2[NODE] == D.NEW_FAILURE


def test_16_volatility_is_never_learned_from_the_candidate():
    """The candidate's own text differs run to run; the untouched baseline's does not: the change is blamed. The
    replay is only ever the baseline's own selection, and POST evidence is never one of its inputs."""
    stable = raw_result({KEY: F("m1")})
    replay = replays_returning(stable)
    first, _ = decide({KEY: F("m1")}, {KEY: F("candidate-run-1")}, replay=replay)
    second, _ = decide({KEY: F("m1")}, {KEY: F("candidate-run-2")}, replay=replay)
    assert first.level2 == second.level2 == {NODE: D.CHANGED_FAILURE}
    assert replay.calls == [None] * 4


def test_17_cached_stability_is_bound_to_base_command_environment_and_baseline():
    stable = raw_result({KEY: F("m1")})
    cache = {}
    replay = replays_returning(stable)
    reference = make_baseline({KEY: F("m1")})
    decide(None, {KEY: F("m2")}, base=reference, replay=replay, cache=cache)
    assert len(replay.calls) == 2 and len(cache) == 1
    decide(None, {KEY: F("m2")}, base=reference, replay=replay, cache=cache)   # same context: cached, no replay
    assert len(replay.calls) == 2
    for changed, revision in ((make_baseline({KEY: F("m1")}, revision="rev2"), "rev2"),
                              (make_baseline({KEY: F("m1")}, environment="env2"), "rev"),
                              (make_baseline({KEY: F("m1")}, command="other"), "rev"),
                              (make_baseline({KEY: F("m1")}, run_id="another-baseline"), "rev")):
        before = len(replay.calls)
        delta, measurement = decide(None, {KEY: F("m2")}, base=changed, replay=replay, cache=cache, revision=revision)
        assert len(replay.calls) == before + 2, "a different context is re-established, never reused"
        assert measurement.records[0]["source"] == "replay" and delta.level2 == {NODE: D.CHANGED_FAILURE}


def test_18_old_checkpoint_without_pytest_evidence_is_re_established_never_silently_passed():
    new = make_baseline({KEY: F()}).to_dict()
    old = json.loads(json.dumps(new))
    del old["outcome"]["pytest_evidence"]                                  # the pre-REG-R1 format
    captured = []

    def run_validator(target):
        captured.append(target)
        return raw_result({KEY: F()})
    result = capture_brownfield_baselines(
        run_id="r", target_test=None, full_regression_policy="required", run_validator=run_validator,
        compute_revision=lambda: "rev", resume_baseline_full_regression={**old, "workspace_revision": "rev"},
        require_pytest_evidence=True)
    assert result.full_regression_source == "captured" and captured == [None]
    assert result.reuse_rejections == (f"{OLD_CHECKPOINT_INSUFFICIENT}:resume",)
    assert result.full_regression.outcome.pytest_evidence.complete
    reused = capture_brownfield_baselines(       # another runner (Maven): exactly today's reuse
        run_id="r", target_test=None, full_regression_policy="required", run_validator=run_validator,
        compute_revision=lambda: "rev", resume_baseline_full_regression={**old, "workspace_revision": "rev"})
    assert reused.full_regression_source == "resume" and reused.reuse_rejections == ()
    legacy_pre = ValidationBaseline.from_dict({**old, "workspace_revision": "rev"})
    assert legacy_pre.outcome.pytest_evidence is None
    delta = classify_baseline_delta(legacy_pre, build_validation_outcome(raw_result({KEY: F()})))
    assert delta.blocking and delta.blocking_reasons[-1] == "pytest_evidence_incomplete:PRE:PYTEST_EVIDENCE_MISSING"


# ---------------------------------------------------------------- the context-stability controls

def test_c01_c02_real_full_suite_failure_that_passes_alone_is_measured_in_the_full_suite_context(tmp_path):
    """c01/c02: the failure's message varies only under the full suite; alone it PASSES. The same-context replays run
    the full suite (never the test alone), so its message is measured volatile and the outcome stable."""
    root = write_project(tmp_path / "ws")
    alone = validator_for(root).run_tests(target_test=[SUITE_DEPENDENT])
    assert alone["success"] is True                           # isolated, it passes: no basis for full-suite evidence
    base = ValidationBaseline(workspace_revision="rev", run_id="r", captured_at=0.0,
                              outcome=build_validation_outcome(validator_for(root).run_tests()),
                              invocation=ValidationInvocation("polymorphic_validator.run_tests", "full_suite"))
    post = build_validation_outcome(validator_for(root).run_tests())
    calls = []

    def replay(selection):                                    # the baseline's own gate call, in place
        calls.append(selection)
        return validator_for(root).run_tests(target_test=selection)
    delta, measurement = classify_with_baseline_stability(baseline=base, post=post, post_environment=None, cache={},
                                                          replay=replay, current_revision=lambda: "rev",
                                                          working_directory=str(root))
    assert calls == [None, None], "two full-suite replays, never the disputed tests alone"
    assert delta.authority == PYTEST_PER_TEST_AUTHORITY and delta.blocking is False
    assert delta.level2[SUITE_DEPENDENT] == D.PRE_EXISTING_FAILURE
    record = {r["test"]: r for r in measurement.records}[junit_key_for_node_id(SUITE_DEPENDENT)]
    assert not record["flaky"] and record_fields(record)["message"] == FIELD_VOLATILE   # one state: outcome+type stable


def test_c03_fail_fail_fail_same_type_varying_message_is_volatile_message_stable_outcome():
    replay = replays_returning(raw_result({KEY: F("m-A")}), raw_result({KEY: F("m-B")}))
    _, measurement = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replay)
    record = measurement.records[0]
    assert not record["flaky"] and record_fields(record) == {"message": FIELD_VOLATILE, "body": FIELD_STABLE}
    assert len(record["observations"]) == 1 + BASELINE_REPLAYS


def test_c04_c05_superseded_by_reg_r2():
    """c04/c05 (baseline outcome or type instability alone fails closed) were the REG-R1 rule that produced the
    confirmed POST-REG-R1 Arm-A false negative; REG-R2 replaced them with the baseline behavior envelope. The
    controls that keep the safe half (a state outside the envelope blocks) are in
    tests/test_reg_r2_flaky_baseline_envelope.py; this keeps the two original baselines as REG-R2 inputs."""
    replay = replays_returning(raw_result({KEY: PASSED}), raw_result({KEY: F("m3")}))      # FAIL / PASS / FAIL
    delta, measurement = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replay)
    assert measurement.records[0]["flaky"] and delta.level2 == {NODE: D.FLAKY_PREEXISTING} and not delta.blocking
    replay = replays_returning(raw_result({KEY: F(failure_type="RuntimeError")}), raw_result({KEY: F()}))
    delta, _ = decide({KEY: F()}, {KEY: F(failure_type="KeyError")}, replay=replay)    # a type it never showed
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking


def test_c06_stable_baseline_message_changed_only_by_the_candidate_blocks():
    stable = raw_result({KEY: F("m1")})
    delta, _ = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replays_returning(stable))
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking


def test_c07_volatile_baseline_message_changed_by_the_candidate_with_stable_id_outcome_type_is_no_regression():
    replay = replays_returning(raw_result({KEY: F("m-A")}), raw_result({KEY: F("m-B")}))
    delta, _ = decide({KEY: F("m1")}, {KEY: F("candidate text")}, replay=replay)
    assert delta.level2 == {NODE: D.PRE_EXISTING_FAILURE} and not delta.blocking
    # ... but the stronger stable fields still bind: the same volatile message with another type is blamed
    delta, _ = decide({KEY: F("m1")}, {KEY: F("candidate text", failure_type="KeyError")}, replay=replay)
    assert delta.level2 == {NODE: D.CHANGED_FAILURE}


@pytest.mark.parametrize("changed", ["working_directory", "session", "selection"])
def test_c08_c10_context_mismatch_never_reuses_cached_stability(changed):
    """c08 (context id: working directory, pytest session facts) and c10 (test command / selection)."""
    stable = raw_result({KEY: F("m1")})
    cache = {}
    replay = replays_returning(stable)
    reference = make_baseline({KEY: F("m1")})
    decide(None, {KEY: F("m2")}, base=reference, replay=replay, cache=cache)
    assert len(replay.calls) == 2
    if changed == "working_directory":
        base, kwargs = reference, {"working_directory": "/elsewhere"}
    elif changed == "session":
        base = ValidationBaseline(
            workspace_revision="rev", run_id="run", captured_at=0.0, invocation=reference.invocation,
            outcome=build_validation_outcome(raw_result({KEY: F("m1")}, session=SESSION.replace("p-1.0", "p-2.0"))))
        replay = replays_returning(raw_result({KEY: F("m1")}, session=SESSION.replace("p-1.0", "p-2.0")))
        kwargs = {}
    else:
        base, kwargs = make_baseline({KEY: F("m1")}, selection=(NODE,)), {}
    ids = {verification_context_id(verification_context(reference, working_directory="/w")),
           verification_context_id(verification_context(base, working_directory=kwargs.get("working_directory", "/w")))}
    assert len(ids) == 2
    before = len(replay.calls)
    delta, measurement = decide(None, {KEY: F("m2")}, base=base, replay=replay, cache=cache, **kwargs)
    assert len(replay.calls) == before + 2 and measurement.records[0]["source"] == "replay"
    assert delta.level2 == {NODE: D.CHANGED_FAILURE}
    if changed == "selection":
        assert replay.calls[-2:] == [(NODE,), (NODE,)], "a targeted baseline is replayed with its own selection"


def test_c08b_a_replay_whose_session_differs_from_the_baseline_is_unresolved():
    replay = replays_returning(raw_result({KEY: F("m1")}, session=SESSION.replace("p-1.0", "other-9")))
    cache = {}
    delta, measurement = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replay, cache=cache)
    assert measurement.records[0]["reason"] == "REPLAY_CONTEXT_MISMATCH" and cache == {}
    assert delta.level2 == {NODE: D.STABILITY_UNRESOLVED} and delta.blocking


def test_c09_base_revision_mismatch_never_reuses_cached_stability():
    stable = raw_result({KEY: F("m1")})
    cache, replay = {}, replays_returning(stable)
    decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replay, cache=cache)
    decide(None, {KEY: F("m2")}, base=make_baseline({KEY: F("m1")}, revision="rev2"), replay=replay, cache=cache,
           revision="rev2")
    assert len(replay.calls) == 4 and len(cache) == 2


def test_c11_one_full_suite_replay_pair_serves_every_disputed_test_of_the_context():
    pre = {KEY: F("m1"), OTHER: F("o1")}
    replay = replays_returning(raw_result({KEY: F("m-A"), OTHER: F("o1")}), raw_result({KEY: F("m-B"), OTHER: F("o1")}))
    cache = {}
    delta, measurement = decide(pre, {KEY: F("m2"), OTHER: F("o2")}, replay=replay, cache=cache)
    assert replay.calls == [None, None]
    assert delta.level2 == {NODE: D.PRE_EXISTING_FAILURE, OTHER_NODE: D.CHANGED_FAILURE}
    assert {r["test"] for r in measurement.records} == {KEY, OTHER}
    # a later attempt disputing only one of them reuses the same replays
    delta, measurement = decide(pre, {KEY: F("m9"), OTHER: F("o1")}, replay=replay, cache=cache)
    assert replay.calls == [None, None] and measurement.records[0]["source"] == "cache"
    assert delta.level2[NODE] == D.PRE_EXISTING_FAILURE
    [entry] = cache.values()
    assert set(entry["tests"]) == {KEY, OTHER} and len(entry["replays"]) == BASELINE_REPLAYS


def test_c12_candidate_execution_never_contributes_to_volatility():
    """POST evidence that itself varies wildly changes nothing: the classification only reads baseline replays."""
    stable = raw_result({KEY: F("m1")})
    for candidate in ("candidate A", "candidate B", "candidate C"):
        delta, measurement = decide({KEY: F("m1")}, {KEY: F(candidate)}, replay=replays_returning(stable))
        assert fields(measurement, KEY)["message"] == FIELD_STABLE and delta.level2 == {NODE: D.CHANGED_FAILURE}


# ---------------------------------------------------------------- unresolved edges: never blame

@pytest.mark.parametrize("make_replay, revision, expected_reason", [
    (lambda: replays_returning(raw_result({})), "rev", "TEST_ABSENT_FROM_REPLAY"),
    (lambda: replays_returning({**raw_result({KEY: F()}), "pytest_evidence": None}), "rev",
     "REPLAY_EVIDENCE_INCOMPLETE:MISSING"),
    (lambda: replays_returning(raw_result({KEY: F()}, complete=False)), "rev",
     "REPLAY_EVIDENCE_INCOMPLETE:PYTEST_REPORT_INCOMPLETE"),
    (lambda: replays_returning(raw_result({KEY: F()})), "drifted", "BASELINE_REVISION_CHANGED"),
])
def test_an_unobservable_baseline_field_keeps_blocking_without_blaming_the_candidate(make_replay, revision,
                                                                                     expected_reason):
    cache = {}
    delta, measurement = decide({KEY: F()}, {KEY: F("m2")}, replay=make_replay(), revision=revision, cache=cache)
    assert delta.level2 == {NODE: D.STABILITY_UNRESOLVED} and delta.blocking
    assert measurement.records[0]["reason"] == expected_reason
    assert measurement.envelopes[KEY].unresolved == expected_reason and measurement.envelopes[KEY].states == ()
    if expected_reason != "TEST_ABSENT_FROM_REPLAY":
        assert cache == {}, "a failed measurement is never cached"


def test_a_raising_replay_is_unresolved():
    def broken(_selection):
        raise OSError("disk")
    delta, measurement = decide({KEY: F()}, {KEY: F("m2")}, replay=broken)
    assert delta.level2 == {NODE: D.STABILITY_UNRESOLVED} and measurement.records[0]["reason"] == "REPLAY_FAILED:OSError"


def test_a_baseline_that_had_drifted_when_the_replays_started_is_never_replayed():
    """Both revision checks matter: drift present before the replays (even if gone afterwards) means the replays
    would not run on the untouched baseline - no replay runs, nothing is measured or cached."""
    revisions = iter(["drifted", "rev", "rev"])
    replay = replays_returning(raw_result({KEY: F()}))
    cache = {}
    delta, measurement = classify_with_baseline_stability(
        baseline=make_baseline({KEY: F()}), post=build_validation_outcome(raw_result({KEY: F("m2")})),
        post_environment=None, cache=cache, replay=replay, current_revision=lambda: next(revisions),
        working_directory="/w")
    assert replay.calls == [] and cache == {}
    assert measurement.records[0]["reason"] == "BASELINE_REVISION_CHANGED"
    assert delta.level2 == {NODE: D.STABILITY_UNRESOLVED} and delta.blocking


def test_a_stable_changed_field_is_blamed_even_when_another_differing_field_is_volatile():
    replay = replays_returning(raw_result({KEY: F("m1", "body A")}), raw_result({KEY: F("m1", "body B")}))
    delta, measurement = decide({KEY: F("m1", "b1")}, {KEY: F("changed", "changed too")}, replay=replay)
    assert fields(measurement, KEY) == {"message": FIELD_STABLE, "body": FIELD_VOLATILE}
    assert delta.level2 == {NODE: D.CHANGED_FAILURE}


# ---------------------------------------------------------------- evidence extraction and checkpoint

def test_exception_type_comes_from_pytests_crash_location_line_then_the_message():
    from kriya.tools.test_execution import _failure_type  # pylint: disable=protected-access
    body = "def t():\n>  assert 1 == 2\nE  assert 1 == 2\n\ntests/t.py:3: AssertionError\n"
    assert _failure_type("assert 1 == 2", body) == "AssertionError"
    assert _failure_type("json.decoder.JSONDecodeError: Expecting value", "no crash line") == "json.decoder.JSONDecodeError"
    assert _failure_type("assert 1 == 2", "no crash line") is None
    parsed = parse_pytest_case_evidence(
        b'<testsuite tests="1" failures="1" errors="0" skipped="0"><testcase classname="tests.t" name="test_x">'
        b'<failure message="AssertionError: boom">body\ntests/t.py:3: AssertionError</failure></testcase></testsuite>')
    assert parsed["cases"][KEY] == {"outcome": "failed", "failure_type": "AssertionError",
                                    "message": "AssertionError: boom", "body": "body\ntests/t.py:3: AssertionError"}


def test_signature_digests_keep_material_text_and_use_only_the_existing_volatile_rules():
    a = pytest_suite_evidence(raw_result({KEY: F("expected 3 got 4", "at /tmp/abc/x.py 0x7ffee1")})).by_key()[KEY]
    b = pytest_suite_evidence(raw_result({KEY: F("expected 3 got 5", "at /tmp/zzz/x.py 0x1234ab")})).by_key()[KEY]
    assert a.message_digest != b.message_digest, "a numeric change in a message is material"
    assert a.body_digest == b.body_digest, "temp paths and addresses are the existing volatile tokens"


def test_session_facts_are_the_keyed_header_lines_only():
    output = SESSION + "Using --randomly-seed=12345\nasyncio: mode=strict\ncollected 3 items\n"
    evidence = pytest_suite_evidence({**raw_result({KEY: F()}), "output": output})
    assert evidence.session == (("platform", "t -- Python 3.12, pytest-9.1.1, pluggy-1.6.0"), ("plugins", "p-1.0"),
                                ("rootdir", "/w"))


def test_junit_key_matches_pytests_own_address_mangling():
    assert junit_key_for_node_id("tests/test_x.py::test_a") == "tests.test_x::test_a"
    assert junit_key_for_node_id("tests/sub/test_x.py::TestC::test_a[1-a/b]") == "tests.sub.test_x.TestC::test_a[1-a/b]"
    assert junit_key_for_node_id("tests/test_x.py") == "::tests.test_x"


def test_checkpoint_round_trip_and_an_unsupported_evidence_version_is_never_reinterpreted():
    base = make_baseline({KEY: F(), OTHER: PASSED, "tests.t::test_s": ("skipped", None, None, None)})
    data = base.to_dict()
    assert ValidationBaseline.from_dict(json.loads(json.dumps(data))) == base
    data["outcome"]["pytest_evidence"]["version"] = 99
    loaded = ValidationBaseline.from_dict(data).outcome.pytest_evidence
    assert loaded.complete is False and loaded.reason == "PYTEST_EVIDENCE_VERSION_UNSUPPORTED"


def test_the_cached_measurement_round_trips_through_the_checkpoint_json():
    cache = {}
    replay = replays_returning(raw_result({KEY: F("m-A")}), raw_result({KEY: F("m-B")}))
    decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replay, cache=cache)
    restored = json.loads(json.dumps(cache))
    delta, measurement = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replay, cache=restored)
    assert len(replay.calls) == 2 and measurement.records[0]["source"] == "cache"
    assert delta.level2 == {NODE: D.PRE_EXISTING_FAILURE}
    [entry] = restored.values()
    assert record_fields(entry["tests"][KEY])["message"] == FIELD_VOLATILE
    assert len(entry["tests"][KEY]["observations"]) == 3 and entry["tests"][KEY]["envelope_digest"]


# ---------------------------------------------------------------- non-pytest runners: no behaviour change

def test_non_pytest_outcomes_are_decided_exactly_as_before():
    from kriya.workflow.validation_baseline import (  # pylint: disable=protected-access
        _whole_output_delta,
        classify_level1_delta,
    )
    for pre_out, post_out, pre_ok, post_ok in (("a", "b", False, False), ("a", "a", False, False),
                                               ("a", "", False, True), ("", "b", True, False)):
        pre = ValidationOutcome(execution_status="completed", success=pre_ok,
                                failure_fingerprint=None if pre_ok else compute_failure_fingerprint(pre_out))
        post = ValidationOutcome(execution_status="completed", success=post_ok,
                                 failure_fingerprint=None if post_ok else compute_failure_fingerprint(post_out))
        base = ValidationBaseline(workspace_revision="rev", run_id="r", captured_at=0.0, outcome=pre,
                                  invocation=ValidationInvocation("c", "full_suite"))
        envelope = baseline_behavior_envelope([pytest_suite_evidence(raw_result({KEY: F()})).by_key()[KEY]] * 3)
        delta = classify_baseline_delta(base, post, stability={KEY: envelope})
        assert delta == _whole_output_delta(pre, post, classify_level1_delta(pre, post))
        assert delta.authority == WHOLE_OUTPUT_AUTHORITY and delta.pytest_evidence_status is None


def test_jvm_reports_carry_no_pytest_evidence(tmp_path):
    report = test_execution.TestExecutionReport(gate_id="g", runner="maven", workspace=str(tmp_path))
    assert report.pytest_evidence() is None
    assert pytest_suite_evidence({"success": False, "output": "x"}) is None


# ---------------------------------------------------------------- real pytest: the reproducer and controls

def _real(root):
    calls = []

    def replay(selection):
        calls.append(selection)
        return validator_for(root).run_tests(target_test=selection)
    replay.calls = calls
    base = ValidationBaseline(workspace_revision="rev", run_id="r", captured_at=0.0,
                              outcome=build_validation_outcome(validator_for(root).run_tests()),
                              invocation=ValidationInvocation("polymorphic_validator.run_tests", "full_suite"))
    return base, replay


def _decide_real(base, post_root, replay, root):
    post = build_validation_outcome(validator_for(post_root).run_tests())
    return classify_with_baseline_stability(baseline=base, post=post, post_environment=None, cache={}, replay=replay,
                                            current_revision=lambda: "rev", working_directory=str(root))


def test_reg_r1_reproducer_untouched_baseline_against_itself_is_no_regression(tmp_path):
    """OBS-4, generically: two runs of the SAME untouched code. Before REG-R1: level1 CHANGED_FAILURE, blocking.
    After: per-test authority; the run-varying fields are measured volatile in the same (full-suite) context."""
    root = write_project(tmp_path / "ws")
    base, replay = _real(root)
    delta, measurement = _decide_real(base, root, replay, root)
    assert delta.level1.classification == D.CHANGED_FAILURE               # the whole output differs (diagnostic)
    assert delta.authority == PYTEST_PER_TEST_AUTHORITY and delta.blocking is False
    assert delta.level2 == {STABLE: D.PRE_EXISTING_FAILURE, VARYING_MESSAGE: D.PRE_EXISTING_FAILURE,
                            VARYING_BODY: D.PRE_EXISTING_FAILURE, SUITE_DEPENDENT: D.PRE_EXISTING_FAILURE}
    assert delta.volatile_fields_ignored == {VARYING_MESSAGE: ("message", "body"), VARYING_BODY: ("body",),
                                             SUITE_DEPENDENT: ("message", "body")}
    assert replay.calls == [None, None]
    assert {r["test"]: tuple(record_fields(r).values()) for r in measurement.records} == {
        junit_key_for_node_id(VARYING_MESSAGE): (FIELD_VOLATILE, FIELD_VOLATILE),
        junit_key_for_node_id(VARYING_BODY): (FIELD_STABLE, FIELD_VOLATILE),
        junit_key_for_node_id(SUITE_DEPENDENT): (FIELD_VOLATILE, FIELD_VOLATILE)}


def test_stable_changed_failure_control_is_blamed(tmp_path):
    """A candidate that changes the message of a failure the baseline reproduces exactly is CHANGED_FAILURE."""
    root = write_project(tmp_path / "ws")
    base, replay = _real(root)
    candidate = write_project(tmp_path / "cand", SUITE.replace('"arithmetic is stable"', '"arithmetic changed"'))
    delta, _ = _decide_real(base, candidate, replay, root)
    assert delta.level2[STABLE] == D.CHANGED_FAILURE and delta.blocking
    assert delta.level2[VARYING_MESSAGE] == delta.level2[SUITE_DEPENDENT] == D.PRE_EXISTING_FAILURE


def test_new_failure_control_is_blamed(tmp_path):
    root = write_project(tmp_path / "ws")
    base, replay = _real(root)
    candidate = write_project(tmp_path / "cand", SUITE.replace("    assert True\n", "    assert False\n", 1))
    delta, _ = _decide_real(base, candidate, replay, root)
    assert delta.level2["tests/test_suite.py::test_passes"] == D.NEW_FAILURE and delta.blocking


# ---------------------------------------------------------------- evidence retention (observational)

def test_gate_result_retains_raw_stdout_stderr_and_junit_and_the_decision_record_its_artifacts(tmp_path, monkeypatch):
    from kriya.core.attempt_evidence import scope

    emitted = []
    monkeypatch.setattr(scope, "capture_mode", lambda: "full")
    monkeypatch.setattr(scope, "emit", lambda kind, payload, content=None, **_k: emitted.append((kind, payload, content)))
    root = write_project(tmp_path / "ws")
    result = validator_for(root).run_tests()           # the gate itself records its gate.result
    [(_kind, payload, content)] = [e for e in emitted if e[0] == "gate.result"]
    raw = result["pytest_evidence"]["raw"]
    assert content["pytest_stdout"] == raw["stdout"] and content["pytest_junit"] == raw["junit"]
    assert content["pytest_stderr"] == raw["stderr"] == ""
    assert result["output"] == raw["stdout"] + "\n" + raw["stderr"]
    assert payload["pytest_evidence"] == {"complete": True, "integrity_ok": True, "integrity_reason": None}

    base = make_baseline({KEY: F("m1")})
    post = build_validation_outcome(raw_result({KEY: F("m2")}))
    delta, measurement = classify_with_baseline_stability(
        baseline=base, post=post, post_environment=None, cache={}, replay=replays_returning(raw_result({KEY: F("m1")})),
        current_revision=lambda: "rev", working_directory="/w")
    record_regression_decision("full_regression", base, post, delta, measurement)
    [(_kind, payload, content)] = [e for e in emitted if e[0] == "regression.decision"]
    assert payload["blocking"] is True and payload["authority"] == PYTEST_PER_TEST_AUTHORITY
    assert payload["stability_context_id"] == measurement.context_id
    assert json.loads(content["comparison"])["level2"] == {NODE: "CHANGED_FAILURE"}
    stability = json.loads(content["stability"])[0]
    assert not stability["flaky"] and record_fields(stability)["message"] == FIELD_STABLE
    assert json.loads(content["baseline_cases"])["failing"][0]["key"] == KEY
    assert json.loads(content["post_cases"])["session"] == [list(f) for f in post.pytest_evidence.session]


def test_recording_never_changes_or_breaks_a_decision(monkeypatch):
    from kriya.core.attempt_evidence import scope

    def explode(*_a, **_k):
        raise RuntimeError("store down")
    monkeypatch.setattr(scope, "capture_mode", lambda: "full")
    monkeypatch.setattr(scope, "emit", explode)
    delta, _ = decide({KEY: F()}, {KEY: PASSED}, post_success=True)
    snapshot = repr(delta)
    base = make_baseline({KEY: F()})
    record_regression_decision("full_regression", base, base.outcome, delta, None)
    assert repr(delta) == snapshot


def test_a_pytest_failure_without_complete_evidence_on_either_side_never_passes_on_an_aggregate_match():
    pre = PytestSuiteEvidence(version=1, complete=False, reason="X", collected=0, report_digest=None,
                              runner_version=None, cases=())
    base = ValidationBaseline(workspace_revision="rev", run_id="r", captured_at=0.0,
                              outcome=outcome_of(pre, output="same"), invocation=ValidationInvocation("c", "full_suite"))
    delta = classify_baseline_delta(base, outcome_of(pre, output="same"))
    assert delta.level1.classification == D.PRE_EXISTING_FAILURE and delta.blocking


def test_a_baseline_that_changes_during_the_replays_is_never_measured_or_cached():
    """The replays only count when the pristine revision is unchanged after them too (a replay that left content
    behind, or a concurrent change, means they did not observe the untouched baseline)."""
    revisions = iter(["rev", "drifted"])
    replay = replays_returning(raw_result({KEY: F("m1")}))
    cache = {}
    delta, measurement = classify_with_baseline_stability(
        baseline=make_baseline({KEY: F("m1")}), post=build_validation_outcome(raw_result({KEY: F("m2")})),
        post_environment=None, cache=cache, replay=replay, current_revision=lambda: next(revisions),
        working_directory="/w")
    assert len(replay.calls) == BASELINE_REPLAYS and cache == {}
    assert measurement.records[0]["reason"] == "BASELINE_REVISION_CHANGED"
    assert delta.level2 == {NODE: D.STABILITY_UNRESOLVED} and delta.blocking

"""REG-R2 - flaky baseline attribution authority: the baseline behavior envelope.

Confirmed false negative (POST-REG-R1 Arm A, run 20261007T071630-e21ca9b2): a correct candidate (external 5/5) was
rejected because a pre-existing timing test was FAIL / PASS / FAIL across the untouched baseline and its two
same-context replays; REG-R1 treated that baseline outcome instability as reason to reject every candidate.

Rule: a test's execution state is (outcome, exception type). S = the states the UNTOUCHED baseline showed in one
verification context - the original observation and its same-context replays, none privileged. A candidate state
outside S blocks. Inside S nothing is attributed to the candidate, except a message/body that is STABLE among the
baseline's observations of THAT same state and that the candidate changed. A state seen once has message/body
STABILITY_NOT_ESTABLISHED, which never becomes evidence against the candidate. Whether the candidate changed a flaky
test's failure RATE is not assessed. Never enveloped: missing tests, new failing tests, incomplete/invalid evidence.

``test_01``..``test_15`` are the owner's required controls; the real-pipeline reproducer is
tests/test_reg_r2_workflow_reproducer.py.
"""
import json
from dataclasses import replace

from test_baseline_surefire import POST_SAME as SUREFIRE_POST
from test_baseline_surefire import PRE as SUREFIRE_PRE
from test_baseline_surefire import _baseline as surefire_baseline
from test_baseline_surefire import _run as surefire_run
from test_reg_r1_pytest_regression_authority import (
    KEY,
    NODE,
    OTHER,
    OTHER_NODE,
    PASSED,
    F,
    decide,
    make_baseline,
    raw_result,
    replays_returning,
)

from kriya.workflow.pytest_stability import (
    FLAKE_RATE_REGRESSION,
    decision_summary,
    verification_context,
    verification_context_id,
)
from kriya.workflow.validation_baseline import (
    FIELD_NOT_ESTABLISHED,
    FIELD_STABLE,
    FIELD_VOLATILE,
    PYTEST_PER_TEST_AUTHORITY,
    DeltaClassification,
    baseline_behavior_envelope,
    build_validation_outcome,
    classify_baseline_delta,
    pytest_suite_evidence,
)

D = DeltaClassification
SKIPPED = ("skipped", None, None, None)


def runs(*cases):
    """Two same-context replays, each the given case of KEY."""
    return replays_returning(*(raw_result({KEY: case}) for case in cases))


def envelope_of(*cases):
    return baseline_behavior_envelope([pytest_suite_evidence(raw_result({KEY: c})).by_key()[KEY] for c in cases])


def state_fields(measurement, outcome="failed", failure_type="AssertionError"):
    [state] = [s for s in measurement.envelopes[KEY].states if (s.outcome, s.failure_type) == (outcome, failure_type)]
    return dict(state.fields)


# ---------------------------------------------------------------- the owner's required controls

def test_01_fail_pass_fail_baseline_candidate_fail_same_type_is_flaky_preexisting():
    """The Arm-A shape exactly: baseline FAIL, replays PASS then FAIL, candidate FAIL / AssertionError."""
    replay = runs(PASSED, F("took 3.4x"))
    delta, measurement = decide({KEY: F("took 2.1x")}, {KEY: F("took 2.9x")}, replay=replay)
    assert delta.level2 == {NODE: D.FLAKY_PREEXISTING} and not delta.blocking and delta.blocking_reasons == ()
    assert replay.calls == [None, None] and measurement.envelopes[KEY].flaky
    assert {(s.outcome, s.failure_type, s.observations) for s in measurement.envelopes[KEY].states} == {
        ("failed", "AssertionError", 2), ("passed", None, 1)}
    assert decision_summary(delta, measurement)["flaky_preexisting"] == {NODE: FLAKE_RATE_REGRESSION}
    assert FLAKE_RATE_REGRESSION == "NOT_ASSESSED"


def test_02_fail_pass_fail_baseline_candidate_pass_is_baseline_supported_and_non_blocking():
    # PASS is never blocking, so it never triggers replays on its own ...
    replay = runs(PASSED, F("m3"))
    delta, measurement = decide({KEY: F("m1")}, {KEY: PASSED}, post_success=True, replay=replay)
    assert delta.level2 == {NODE: D.RESOLVED_FAILURE} and not delta.blocking
    assert replay.calls == [] and measurement is None
    # ... and with the context's envelope known, the PASS the baseline itself showed is FLAKY_PREEXISTING
    post = build_validation_outcome(raw_result({KEY: PASSED}, success=True))
    known = classify_baseline_delta(make_baseline({KEY: F("m1")}), post,
                                    stability={KEY: envelope_of(F("m1"), PASSED, F("m3"))})
    assert known.level2 == {NODE: D.FLAKY_PREEXISTING} and not known.blocking
    resolved = classify_baseline_delta(make_baseline({KEY: F("m1")}), post,
                                       stability={KEY: envelope_of(F("m1"), F("m1"), F("m1"))})
    assert resolved.level2 == {NODE: D.RESOLVED_FAILURE}, "a baseline that never passed: a real resolution"


def test_03_fail_pass_fail_assertion_baseline_candidate_error_runtime_error_blocks():
    delta, measurement = decide({KEY: F()}, {KEY: F(failure_type="RuntimeError", outcome="error")},
                                replay=runs(PASSED, F()))
    assert measurement.envelopes[KEY].flaky
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking
    assert delta.blocking_reasons == (f"level2:{NODE}:CHANGED_FAILURE",)


def test_04_pass_pass_pass_baseline_candidate_fail_blocks():
    replay = runs(PASSED, PASSED)
    delta, measurement = decide({KEY: PASSED}, {KEY: F()}, replay=replay)
    assert replay.calls == [None, None], "the original PASS is not privileged: the envelope is measured"
    assert [(s.outcome, s.observations) for s in measurement.envelopes[KEY].states] == [("passed", 3)]
    assert delta.level2 == {NODE: D.NEW_FAILURE} and delta.blocking


def test_05_always_fail_assertion_baseline_candidate_runtime_error_blocks():
    delta, measurement = decide({KEY: F()}, {KEY: F(failure_type="RuntimeError")}, replay=runs(F(), F()))
    assert not measurement.envelopes[KEY].flaky
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking


def test_06_volatile_conditional_message_is_not_blocking():
    """FAIL A / PASS / FAIL B: for state FAIL / AssertionError the message varies, so message C is not blamed."""
    delta, measurement = decide({KEY: F("message A", "body A")}, {KEY: F("message C", "body C")},
                                replay=runs(PASSED, F("message B", "body B")))
    assert state_fields(measurement) == {"message": FIELD_VOLATILE, "body": FIELD_VOLATILE}
    assert delta.level2 == {NODE: D.FLAKY_PREEXISTING} and not delta.blocking
    assert delta.volatile_fields_ignored == {NODE: ("message", "body")}


def test_07_stable_conditional_message_changed_by_the_candidate_blocks():
    # every run fails identically: the message is stable, the candidate's other message is blamed
    delta, measurement = decide({KEY: F("expected 4 but got 5")}, {KEY: F("index out of range")},
                                replay=runs(F("expected 4 but got 5"), F("expected 4 but got 5")))
    assert state_fields(measurement)["message"] == FIELD_STABLE
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking
    # the same holds for a flaky test: two same-state observations with the same message are stable evidence
    delta, measurement = decide({KEY: F("expected 4 but got 5")}, {KEY: F("index out of range")},
                                replay=runs(PASSED, F("expected 4 but got 5")))
    assert measurement.envelopes[KEY].flaky and state_fields(measurement)["message"] == FIELD_STABLE
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking
    # and a candidate showing exactly the stable failure is pre-existing (flaky)
    delta, _ = decide({KEY: PASSED}, {KEY: F("expected 4 but got 5")},
                      replay=runs(F("expected 4 but got 5"), F("expected 4 but got 5")))
    assert delta.level2 == {NODE: D.FLAKY_PREEXISTING} and not delta.blocking


def test_08_a_state_seen_once_has_message_body_stability_not_established_never_fabricated_stable():
    delta, measurement = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=runs(PASSED, PASSED))
    assert state_fields(measurement) == {"message": FIELD_NOT_ESTABLISHED, "body": FIELD_NOT_ESTABLISHED}
    assert delta.level2 == {NODE: D.FLAKY_PREEXISTING} and not delta.blocking
    assert delta.unestablished_fields_ignored == {NODE: ("message",)} and delta.volatile_fields_ignored == {}
    # the once-seen state itself still is part of the envelope: another type is outside it
    delta, _ = decide({KEY: F("m1")}, {KEY: F("m1", failure_type="KeyError")}, replay=runs(PASSED, PASSED))
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking


def test_09_the_candidate_never_enlarges_the_baseline_envelope():
    cache = {}
    replay = runs(PASSED, PASSED)
    for candidate in (F("a"), F("b"), F("a")):
        delta, measurement = decide({KEY: PASSED}, {KEY: candidate}, replay=replay, cache=cache)
        assert delta.level2 == {NODE: D.NEW_FAILURE} and delta.blocking
        assert [s.outcome for s in measurement.envelopes[KEY].states] == ["passed"]
    [entry] = cache.values()
    assert [s["outcome"] for s in entry["tests"][KEY]["envelope"]["states"]] == ["passed"]
    assert len(replay.calls) == 2, "measured once per context; the candidates are never an input"


def test_10_the_candidate_never_teaches_volatility():
    stable = F("expected 4 but got 5")
    for candidate in ("candidate run 1", "candidate run 2", "candidate run 3"):
        delta, measurement = decide({KEY: stable}, {KEY: F(candidate)}, replay=runs(stable, stable))
        assert state_fields(measurement)["message"] == FIELD_STABLE and delta.level2 == {NODE: D.CHANGED_FAILURE}


def test_11_context_mismatch_invalidates_the_envelope():
    cache = {}
    flaky = runs(PASSED, F("m3"))
    delta, _ = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=flaky, cache=cache)
    assert delta.level2 == {NODE: D.FLAKY_PREEXISTING}
    stable = runs(F("m1"), F("m1"))      # the same baseline in another working directory reproduces exactly
    delta, measurement = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=stable, cache=cache,
                                working_directory="/elsewhere")
    assert stable.calls == [None, None] and measurement.records[0]["source"] == "replay"
    assert delta.level2 == {NODE: D.CHANGED_FAILURE}, "the flaky envelope of another context never applies"
    base = make_baseline({KEY: F("m1")})
    assert (verification_context_id(verification_context(base, working_directory="/w"))
            != verification_context_id(verification_context(base, working_directory="/elsewhere")))
    assert len(cache) == 2


def test_12_base_revision_mismatch_invalidates_the_envelope():
    cache = {}
    decide({KEY: F("m1")}, {KEY: F("m2")}, replay=runs(PASSED, F("m3")), cache=cache)
    stable = runs(F("m1"), F("m1"))
    delta, measurement = decide(None, {KEY: F("m2")}, base=make_baseline({KEY: F("m1")}, revision="rev2"),
                                replay=stable, cache=cache, revision="rev2")
    assert stable.calls == [None, None] and measurement.records[0]["source"] == "replay"
    assert delta.level2 == {NODE: D.CHANGED_FAILURE}


def test_13_missing_incomplete_or_invalid_evidence_fails_closed_whatever_the_envelope():
    flaky = envelope_of(F("m1"), PASSED, F("m3"))
    base = make_baseline({KEY: F("m1"), OTHER: PASSED})
    # missing expected test
    missing = classify_baseline_delta(base, build_validation_outcome(raw_result({OTHER: PASSED}, success=True)),
                                      stability={KEY: flaky})
    assert missing.level2[NODE] == D.NEWLY_SKIPPED_OR_NOT_EXECUTED and missing.blocking
    # incomplete POST report / invalid JUnit integrity: no per-test authority at all
    for bad in (raw_result({KEY: F("m1"), OTHER: PASSED}, complete=False),
                raw_result({KEY: F("m1"), OTHER: PASSED}, integrity_ok=False)):
        delta = classify_baseline_delta(base, build_validation_outcome(bad), stability={KEY: flaky})
        assert delta.blocking and delta.authority != PYTEST_PER_TEST_AUTHORITY
    # a new failing test is never enveloped (the baseline never ran it)
    new = classify_baseline_delta(
        base, build_validation_outcome(raw_result({KEY: F("m1"), OTHER: PASSED, "tests.t::test_new": F()})),
        stability={KEY: flaky, "tests.t::test_new": flaky})
    assert new.level2["tests/t.py::test_new"] == D.NEW_FAILURE and new.blocking
    # an incomplete replay or a test absent from a replay: no envelope - the provisional verdict stands
    for replay in (replays_returning(raw_result({KEY: F(), OTHER: PASSED}, complete=False)),
                   replays_returning(raw_result({OTHER: PASSED}))):
        delta, measurement = decide({KEY: F("m1"), OTHER: PASSED}, {KEY: F("m2"), OTHER: PASSED}, replay=replay)
        assert delta.level2[NODE] == D.STABILITY_UNRESOLVED and delta.blocking
        assert measurement.envelopes[KEY].unresolved
    delta, _ = decide({KEY: PASSED}, {KEY: F()}, replay=replays_returning(raw_result({OTHER: PASSED})))
    assert delta.level2 == {NODE: D.NEW_FAILURE} and delta.blocking


def test_14_whole_output_stays_diagnostic():
    delta, _ = decide({KEY: F("m1"), OTHER: PASSED}, {KEY: F("m2"), OTHER: PASSED},
                      replay=runs(PASSED, F("m3")), post_extra="another run's whole output")
    assert delta.level1.classification == D.CHANGED_FAILURE
    assert delta.authority == PYTEST_PER_TEST_AUTHORITY and not delta.blocking
    assert all(not reason.startswith("level1") for reason in delta.blocking_reasons)


def test_15_maven_surefire_is_decided_exactly_as_before():
    flaky = {KEY: envelope_of(F("m1"), PASSED, F("m3"))}
    for post_output in (SUREFIRE_POST, surefire_run("h", "06:33:18.194", "1", errors=(("Other.t:1", "Boom"),))):
        post = build_validation_outcome({"success": False, "output": post_output})
        assert (classify_baseline_delta(surefire_baseline(SUREFIRE_PRE), post, stability=flaky)
                == classify_baseline_delta(surefire_baseline(SUREFIRE_PRE), post))


# ---------------------------------------------------------------- the original observation is not privileged

def test_a_baseline_whose_original_observation_passed_still_excuses_a_failure_it_showed_in_its_replays():
    delta, measurement = decide({KEY: PASSED}, {KEY: F("took 2.9x")}, replay=runs(F("took 3.4x"), F("took 2.2x")))
    assert state_fields(measurement)["message"] == FIELD_VOLATILE
    assert delta.level2 == {NODE: D.FLAKY_PREEXISTING} and not delta.blocking


def test_a_candidate_showing_exactly_a_once_seen_state_is_within_the_envelope():
    delta, _ = decide({KEY: F("m1")}, {KEY: F("m1", outcome="error", failure_type="RuntimeError")},
                      replay=runs(F("m1"), F("m9", outcome="error", failure_type="RuntimeError")))
    assert delta.level2 == {NODE: D.FLAKY_PREEXISTING} and not delta.blocking
    assert delta.unestablished_fields_ignored == {NODE: ("message",)}


def test_skips_are_execution_states_too():
    delta, _ = decide({KEY: PASSED}, {KEY: SKIPPED}, post_success=True, replay=runs(SKIPPED, PASSED))
    assert delta.level2 == {KEY: D.FLAKY_PREEXISTING} and not delta.blocking
    delta, _ = decide({KEY: PASSED}, {KEY: SKIPPED}, post_success=True, replay=runs(PASSED, PASSED))
    assert delta.level2 == {KEY: D.NEWLY_SKIPPED_OR_NOT_EXECUTED} and delta.blocking


def test_one_envelope_measurement_serves_every_disputed_test_of_the_context():
    pre = {KEY: F("m1"), OTHER: PASSED}
    replay = replays_returning(raw_result({KEY: PASSED, OTHER: PASSED}), raw_result({KEY: F("m3"), OTHER: PASSED}))
    delta, measurement = decide(pre, {KEY: F("m2"), OTHER: F()}, replay=replay)
    assert replay.calls == [None, None] and {r["test"] for r in measurement.records} == {KEY, OTHER}
    assert delta.level2 == {NODE: D.FLAKY_PREEXISTING, OTHER_NODE: D.NEW_FAILURE} and delta.blocking
    assert delta.blocking_reasons == (f"level2:{OTHER_NODE}:NEW_FAILURE",)


# ---------------------------------------------------------------- checkpoint / resume

def test_the_envelope_checkpoint_reproduces_the_same_decision_on_resume():
    cache = {}
    replay = runs(PASSED, F("m3"))
    first, first_measurement = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replay, cache=cache)
    restored = json.loads(json.dumps(cache))
    second, second_measurement = decide({KEY: F("m1")}, {KEY: F("m2")}, replay=replay, cache=restored)
    assert len(replay.calls) == 2 and second_measurement.records[0]["source"] == "cache"
    assert second == first and second_measurement.envelopes == first_measurement.envelopes
    [entry] = restored.values()
    record = entry["tests"][KEY]
    assert record["flaky"] is True and record["envelope_digest"] and len(record["observations"]) == 3
    assert entry["context"]["policy"] == 3
    # an entry measured under another policy (REG-R1's v2) is another context: never reused
    base = make_baseline({KEY: F("m1")})
    assert verification_context(base, working_directory="/w")["policy"] == 3


def test_envelope_construction_is_order_independent_and_pure():
    a = envelope_of(F("m1"), PASSED, F("m3"))
    b = envelope_of(PASSED, F("m3"), F("m1"))
    assert a == b and a.to_dict() == b.to_dict()
    case = pytest_suite_evidence(raw_result({KEY: F()})).by_key()[KEY]
    assert baseline_behavior_envelope([case, None, case]).unresolved == "TEST_ABSENT_FROM_REPLAY"
    assert baseline_behavior_envelope([case, replace(case, message_digest="x"), case]).states[0].field_status(
        "message") == FIELD_VOLATILE

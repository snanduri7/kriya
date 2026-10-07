"""REG-R1 - pytest per-test regression authority with untouched-baseline field stability.

Live defect (OBS-4, run 20261006T231821-fb31caad): two runs of the same untouched code differed in their whole
pytest output (a pre-existing failure's measured timing ratio, a container hostname in another's traceback); the
whole-output fingerprint called that CHANGED_FAILURE and stopped a candidate that changed no test's outcome.

Invariant: a candidate may be blamed only for a difference that is stable on the untouched baseline. Authority
order for complete pytest reports: test id, outcome, exception type, message, failure body - message and body
only where the untouched baseline reproduces them. Whole output is diagnostic when per-test evidence is complete;
missing/incomplete per-test evidence fails closed.

The numbered tests are the owner's required matrix (1-18); the real-pytest tests use tests/_reg_r1_fixture.py.
"""
import json
import pathlib
import shutil

import pytest
from _reg_r1_fixture import STABLE, SUITE, VARYING_BODY, VARYING_MESSAGE, validator_for, write_project

from kriya.tools import test_execution
from kriya.tools.test_execution import PYTEST_CASE_EVIDENCE_VERSION, parse_pytest_case_evidence
from kriya.workflow import pytest_stability
from kriya.workflow.pytest_stability import (
    BASELINE_REPLAYS,
    classify_with_baseline_stability,
    record_regression_decision,
    stability_binding,
)
from kriya.workflow.validation_baseline import (
    FIELD_INDETERMINATE,
    FIELD_STABLE,
    FIELD_VOLATILE,
    OLD_CHECKPOINT_INSUFFICIENT,
    PYTEST_PER_TEST_AUTHORITY,
    WHOLE_OUTPUT_AUTHORITY,
    DeltaClassification,
    PytestCaseSignature,
    PytestSuiteEvidence,
    ValidationBaseline,
    ValidationInvocation,
    ValidationOutcome,
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


# ---------------------------------------------------------------- synthetic evidence

def sig(key=KEY, outcome="failed", failure_type="AssertionError", message="m1", body="b1", node_id=NODE):
    failing = outcome in ("failed", "error")
    return PytestCaseSignature(key=key, outcome=outcome, failure_type=failure_type if failing else None,
                               message_digest=message if failing else None, body_digest=body if failing else None,
                               node_id=node_id if failing else None)


def suite(*cases, complete=True, reason=None):
    return PytestSuiteEvidence(version=1, complete=complete, reason=reason, collected=len(cases), report_digest="r",
                               runner_version="9", cases=tuple(sorted(cases, key=lambda c: c.key)))


def outcome(evidence, *, success=False, output="out"):
    return ValidationOutcome(execution_status="completed", success=success,
                             failure_fingerprint=None if success else compute_failure_fingerprint(output),
                             pytest_evidence=evidence)


def baseline(pre, *, revision="rev", environment="env", command="polymorphic_validator.run_tests"):
    return ValidationBaseline(workspace_revision=revision, run_id="run", captured_at=0.0, outcome=pre,
                              invocation=ValidationInvocation(command_identity=command, selection_identity="full_suite",
                                                              environment_fingerprint=environment))


def raw_result(cases, *, complete=True, integrity_ok=True, failing_lines=None):
    """The test-gate result shape run_tests returns for pytest: ``cases`` maps key -> (outcome, type, message, body)."""
    junit = {key: {"outcome": o, "failure_type": t, "message": m, "body": b} for key, (o, t, m, b) in cases.items()}
    lines = failing_lines if failing_lines is not None else [
        f"FAILED {key.split('::')[0].replace('.', '/')}.py::{key.split('::')[1]} - x"
        for key, (o, *_rest) in cases.items() if o in ("failed", "error")]
    return {"success": False, "output": "=== test session starts ===\n" + "\n".join(lines) + "\n=== 1 failed in 0.1s ===",
            "pytest_evidence": {"version": PYTEST_CASE_EVIDENCE_VERSION, "complete": complete, "reason": None,
                                "report_files": [{"path": "r.xml", "sha256": "d"}],
                                "evidence": {"cases": junit, "integrity": {"ok": integrity_ok,
                                                                           "reason": None if integrity_ok else "X"}},
                                "raw": {"stdout": "", "stderr": "", "junit": ""}}}


def replays_returning(*results):
    calls = []

    def replay(node_ids):
        calls.append(list(node_ids))
        return results[min(len(calls) - 1, len(results) - 1)]
    replay.calls = calls
    return replay


def decide(pre_cases, post_cases, *, replay=None, cache=None, post_success=False, revision="rev",
           pre_output="out", post_output="out2"):
    base = baseline(outcome(suite(*pre_cases), output=pre_output))
    post = outcome(suite(*post_cases), success=post_success, output=post_output)
    return classify_with_baseline_stability(
        baseline=base, post=post, post_environment=None, cache={} if cache is None else cache,
        replay=replay or replays_returning(), current_revision=lambda: revision)


def raw_for(case_tuple, key=KEY):
    return raw_result({key: case_tuple})


# ---------------------------------------------------------------- the required matrix

def test_01_identical_complete_result_is_no_regression():
    delta, measurement = decide([sig(), sig("tests.t::test_ok", "passed")], [sig(), sig("tests.t::test_ok", "passed")])
    assert delta.authority == PYTEST_PER_TEST_AUTHORITY and measurement is None
    assert delta.blocking is False and delta.level2 == {NODE: D.PRE_EXISTING_FAILURE}


def test_02_same_failing_test_same_type_message_body_is_pre_existing():
    delta, _ = decide([sig()], [sig()], pre_output="a", post_output="b")
    assert delta.level2 == {NODE: D.PRE_EXISTING_FAILURE} and not delta.blocking and delta.stability_required == {}


def test_03_message_differs_and_untouched_baseline_message_varies_is_ignored_for_that_test():
    replay = replays_returning(raw_for(("failed", "AssertionError", "m-A", "b1")),
                               raw_for(("failed", "AssertionError", "m-B", "b1")))
    delta, measurement = decide([sig(message="m1")], [sig(message="m2")], replay=replay)
    assert delta.level2 == {NODE: D.PRE_EXISTING_FAILURE} and not delta.blocking
    assert delta.volatile_fields_ignored == {NODE: ("message",)}
    assert len(replay.calls) == BASELINE_REPLAYS == 2 and replay.calls[0] == [NODE]
    assert measurement.statuses[KEY]["message"] == FIELD_VOLATILE


def test_04_message_differs_and_untouched_baseline_message_is_stable_blocks_as_changed_failure():
    stable = raw_for(("failed", "AssertionError", "m1", "b1"))
    # replay results are digested from their text, so PRE is the digest of exactly that text
    pre_digest = pytest_suite_evidence(stable).by_key()[KEY]
    delta, measurement = decide([pre_digest], [sig(message="m2", body=pre_digest.body_digest)],
                                replay=replays_returning(stable, stable))
    assert measurement.statuses[KEY] == {"message": FIELD_STABLE, "body": FIELD_STABLE}
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking
    assert delta.blocking_reasons == (f"level2:{NODE}:CHANGED_FAILURE",)


def test_05_body_differs_and_untouched_baseline_body_varies_is_ignored_for_that_test():
    first = raw_for(("failed", "AssertionError", "m1", "body run A"))
    second = raw_for(("failed", "AssertionError", "m1", "body run B"))
    pre = pytest_suite_evidence(first).by_key()[KEY]
    post = PytestCaseSignature(key=KEY, outcome="failed", failure_type="AssertionError",
                               message_digest=pre.message_digest, body_digest="other", node_id=NODE)
    delta, measurement = decide([pre], [post], replay=replays_returning(second, first))
    assert measurement.statuses[KEY] == {"message": FIELD_STABLE, "body": FIELD_VOLATILE}
    assert delta.level2 == {NODE: D.PRE_EXISTING_FAILURE} and delta.volatile_fields_ignored == {NODE: ("body",)}


def test_06_exception_type_change_blocks_without_any_replay():
    replay = replays_returning()
    delta, measurement = decide([sig()], [sig(failure_type="KeyError")], replay=replay)
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking and measurement is None
    assert replay.calls == []


def test_06b_failed_to_error_outcome_change_blocks():
    delta, _ = decide([sig()], [sig(outcome="error")])
    assert delta.level2 == {NODE: D.CHANGED_FAILURE} and delta.blocking


def test_07_pass_to_fail_blocks():
    delta, _ = decide([sig(outcome="passed")], [sig()])
    assert delta.level2 == {NODE: D.NEW_FAILURE} and delta.blocking


def test_08_fail_to_pass_is_an_improvement_not_a_regression():
    delta, _ = decide([sig()], [sig(outcome="passed")], post_success=True)
    assert delta.level2 == {NODE: D.RESOLVED_FAILURE} and not delta.blocking


def test_09_new_failing_test_blocks_and_a_new_passing_test_does_not():
    delta, _ = decide([sig("tests.t::test_ok", "passed")],
                      [sig("tests.t::test_ok", "passed"), sig(), sig("tests.t::test_new", "passed")])
    assert delta.level2 == {NODE: D.NEW_FAILURE} and delta.blocking


@pytest.mark.parametrize("before", ["passed", "failed", "skipped"])
def test_10_expected_test_missing_fails_closed(before):
    delta, _ = decide([sig(outcome=before), sig("tests.t::test_ok", "passed")], [sig("tests.t::test_ok", "passed")],
                      post_success=True)
    assert delta.level2[NODE if before == "failed" else KEY] == D.NEWLY_SKIPPED_OR_NOT_EXECUTED
    assert delta.blocking


def test_10b_pass_to_skip_and_fail_to_skip_block():
    for before in ("passed", "failed"):
        delta, _ = decide([sig(outcome=before)], [sig(outcome="skipped")], post_success=True)
        assert list(delta.level2.values()) == [D.NEWLY_SKIPPED_OR_NOT_EXECUTED] and delta.blocking


@pytest.mark.parametrize("side", ["PRE", "POST"])
def test_11_incomplete_report_fails_closed_even_when_the_whole_output_matches(side):
    """An aggregate match is never proof: identical whole outputs (level 1 PRE_EXISTING) still block."""
    incomplete = suite(sig(), complete=False, reason="PYTEST_SESSION_INCOMPLETE:exit_2")
    pre = incomplete if side == "PRE" else suite(sig())
    post = incomplete if side == "POST" else suite(sig())
    delta = classify_baseline_delta(baseline(outcome(pre, output="same")), outcome(post, output="same"))
    assert delta.level1.classification == D.PRE_EXISTING_FAILURE
    assert delta.blocking and delta.authority == WHOLE_OUTPUT_AUTHORITY
    assert delta.blocking_reasons[-1].startswith(f"pytest_evidence_incomplete:{side}:PYTEST_SESSION_INCOMPLETE")


def test_11b_a_fully_passing_post_suite_without_complete_evidence_is_not_blocked():
    delta = classify_baseline_delta(baseline(outcome(suite(sig()))),
                                    outcome(suite(complete=False, reason="X"), success=True))
    assert not delta.blocking and delta.pytest_evidence_status.startswith("POST:X")


def test_12_real_collection_failure_fails_closed(tmp_path):
    root = write_project(tmp_path / "ws")
    pre = validator_for(root).run_tests()
    (root / "tests" / "test_broken.py").write_text("def test_(:\n")
    post = validator_for(root).run_tests()
    post_outcome = build_validation_outcome(post)
    assert post["pytest_evidence"]["complete"] is False                       # pytest exit 2
    delta = classify_baseline_delta(baseline(build_validation_outcome(pre)), post_outcome)
    assert delta.blocking and "pytest_evidence_incomplete:POST:PYTEST_SESSION_INCOMPLETE:exit_2" in delta.blocking_reasons[-1]


def test_13_malformed_or_inconsistent_junit_fails_closed(tmp_path):
    with pytest.raises(Exception):
        parse_pytest_case_evidence(b"<testsuite><testcase")
    inconsistent = parse_pytest_case_evidence(
        b'<testsuite tests="2" failures="0" errors="0" skipped="0"><testcase classname="a" name="t"/></testsuite>')
    assert inconsistent["integrity"] == {**inconsistent["integrity"], "ok": False, "reason": "JUNIT_COUNTS_INCONSISTENT"}
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
    for bad in (raw_for(("failed", "E", "m", "b")) | {"pytest_evidence": {**raw_for(("failed", "E", "m", "b"))[
            "pytest_evidence"], "evidence": {"cases": {}, "integrity": {"ok": False, "reason": "JUNIT_DUPLICATE_CASE"}}}},
                malformed):
        delta = classify_baseline_delta(baseline(outcome(suite(sig()))), build_validation_outcome(bad))
        assert delta.blocking and delta.authority == WHOLE_OUTPUT_AUTHORITY


def test_14_aggregate_output_differs_but_complete_stable_per_test_evidence_matches_is_diagnostic_only():
    delta, _ = decide([sig()], [sig()], pre_output="run 1 at 0xdeadbeef host=a", post_output="run 2 host=b ratio 3.1x")
    assert delta.level1.classification == D.CHANGED_FAILURE              # still computed, as a diagnostic
    assert not delta.blocking and delta.blocking_reasons == ()
    assert not any(reason.startswith("level1") for reason in delta.blocking_reasons)


def test_15_aggregate_output_matches_but_per_test_evidence_regresses_blocks():
    other = sig("tests.t::test_y", node_id="tests/t.py::test_y")
    delta, _ = decide([sig(outcome="passed"), other], [sig(), other],
                      pre_output="same", post_output="same")
    assert delta.level1.classification == D.PRE_EXISTING_FAILURE and delta.blocking
    assert delta.level2[NODE] == D.NEW_FAILURE


def test_16_volatility_is_never_learned_from_the_candidate():
    """The candidate's own observations vary run to run; the untouched baseline's do not: the change is blamed.
    The replay is only ever asked for the baseline's tests, and its inputs never include POST evidence."""
    stable = raw_for(("failed", "AssertionError", "m1", "b1"))
    pre = pytest_suite_evidence(stable).by_key()[KEY]
    seen = []

    def replay(node_ids):
        seen.append(list(node_ids))
        return stable
    first, _ = decide([pre], [sig(message="candidate-run-1", body=pre.body_digest)], replay=replay)
    second, _ = decide([pre], [sig(message="candidate-run-2", body=pre.body_digest)], replay=replay)
    assert first.level2 == second.level2 == {NODE: D.CHANGED_FAILURE}
    assert seen == [[NODE]] * 4


def test_17_cached_stability_is_bound_to_base_command_environment_test_and_result():
    stable = raw_for(("failed", "AssertionError", "m1", "b1"))
    pre = pytest_suite_evidence(stable).by_key()[KEY]
    post = outcome(suite(sig(message="m2", body=pre.body_digest)))
    cache = {}
    replay = replays_returning(stable, stable)

    def run(base, revision="rev"):
        return classify_with_baseline_stability(baseline=base, post=post, post_environment=None, cache=cache,
                                                replay=replay, current_revision=lambda: revision)
    reference = baseline(outcome(suite(pre)))
    run(reference)
    assert len(replay.calls) == 2 and len(cache) == 1
    run(reference)                                                      # same binding: cached, no replay
    assert len(replay.calls) == 2
    for changed, revision in ((baseline(outcome(suite(pre)), revision="rev2"), "rev2"),
                              (baseline(outcome(suite(pre)), environment="env2"), "rev"),
                              (baseline(outcome(suite(pre)), command="other"), "rev")):
        before = len(replay.calls)
        delta, measurement = run(changed, revision)
        assert len(replay.calls) == before + 2, "a different binding is re-established, never reused"
        assert measurement.records[0]["source"] == "replay" and delta.level2 == {NODE: D.CHANGED_FAILURE}
    other_result = PytestCaseSignature(key=KEY, outcome="failed", failure_type="AssertionError",
                                       message_digest="x", body_digest=pre.body_digest, node_id=NODE)
    assert stability_binding(reference, other_result) != stability_binding(reference, pre)
    assert stability_binding(baseline(outcome(suite(pre))), pre) == stability_binding(reference, pre)


def test_18_old_checkpoint_without_pytest_evidence_is_re_established_never_silently_passed():
    new = baseline(outcome(suite(sig()))).to_dict()
    old = json.loads(json.dumps(new))
    del old["outcome"]["pytest_evidence"]                                  # the pre-REG-R1 format
    captured = []

    def run_validator(target):
        captured.append(target)
        return raw_for(("failed", "AssertionError", "m1", "b1"))
    result = capture_brownfield_baselines(
        run_id="r", target_test=None, full_regression_policy="required", run_validator=run_validator,
        compute_revision=lambda: "rev", resume_baseline_full_regression={**old, "workspace_revision": "rev"},
        require_pytest_evidence=True)
    assert result.full_regression_source == "captured" and captured == [None]
    assert result.reuse_rejections == (f"{OLD_CHECKPOINT_INSUFFICIENT}:resume",)
    assert result.full_regression.outcome.pytest_evidence.complete
    # another runner (Maven): exactly today's reuse
    reused = capture_brownfield_baselines(
        run_id="r", target_test=None, full_regression_policy="required", run_validator=run_validator,
        compute_revision=lambda: "rev", resume_baseline_full_regression={**old, "workspace_revision": "rev"})
    assert reused.full_regression_source == "resume" and reused.reuse_rejections == ()
    # and a PRE without evidence never makes a failing pytest POST pass
    legacy_pre = ValidationBaseline.from_dict({**old, "workspace_revision": "rev"})
    assert legacy_pre.outcome.pytest_evidence is None
    delta = classify_baseline_delta(legacy_pre, outcome(suite(sig()), output="out"))
    assert delta.blocking and "pytest_evidence_incomplete:PRE:PYTEST_EVIDENCE_MISSING" in delta.blocking_reasons[-1]


# ---------------------------------------------------------------- stability edges: indeterminate never blames

@pytest.mark.parametrize("make_replay, revision, expected_reason", [
    (lambda: replays_returning(raw_for(("passed", None, None, None))), "rev", "BASELINE_REPLAY_OUTCOME_DIFFERS"),
    (lambda: replays_returning(raw_for(("failed", "KeyError", "m1", "b1"))), "rev", "BASELINE_REPLAY_OUTCOME_DIFFERS"),
    (lambda: replays_returning(raw_result({}, failing_lines=[])), "rev", "BASELINE_REPLAY_OUTCOME_DIFFERS"),
    (lambda: replays_returning(raw_for(("failed", "AssertionError", "m1", "b1")) | {"pytest_evidence": None}),
     "rev", "REPLAY_EVIDENCE_INCOMPLETE:MISSING"),
    (lambda: replays_returning(raw_result({KEY: ("failed", "AssertionError", "m", "b")}, complete=False)),
     "rev", "REPLAY_EVIDENCE_INCOMPLETE:PYTEST_REPORT_INCOMPLETE"),
    (lambda: replays_returning(raw_for(("failed", "AssertionError", "m1", "b1"))), "drifted", "BASELINE_REVISION_CHANGED"),
])
def test_an_unobservable_baseline_field_keeps_blocking_without_blaming_the_candidate(make_replay, revision,
                                                                                     expected_reason):
    cache = {}
    delta, measurement = decide([sig()], [sig(message="m2")], replay=make_replay(), revision=revision, cache=cache)
    assert delta.level2 == {NODE: D.STABILITY_UNRESOLVED} and delta.blocking
    assert measurement.records[0]["reason"] == expected_reason
    assert measurement.statuses[KEY] == {"message": FIELD_INDETERMINATE, "body": FIELD_INDETERMINATE}
    assert cache == {}, "an indeterminate measurement is never cached"


def test_a_raising_replay_is_indeterminate_and_a_test_without_a_node_id_is_never_replayed():
    def broken(_node_ids):
        raise OSError("disk")
    delta, measurement = decide([sig()], [sig(message="m2")], replay=broken)
    assert delta.level2 == {NODE: D.STABILITY_UNRESOLVED} and measurement.records[0]["reason"] == "REPLAY_FAILED:OSError"
    replay = replays_returning()
    delta, measurement = decide([sig(node_id=None)], [sig(message="m2", node_id=None)], replay=replay)
    assert replay.calls == [] and measurement.records[0]["reason"] == "NO_NODE_ID"
    assert delta.level2 == {KEY: D.STABILITY_UNRESOLVED}


def test_a_stable_changed_field_is_blamed_even_when_another_differing_field_is_volatile():
    first = raw_for(("failed", "AssertionError", "m1", "body A"))
    second = raw_for(("failed", "AssertionError", "m1", "body B"))
    pre = pytest_suite_evidence(first).by_key()[KEY]
    post = sig(message="changed by candidate", body="changed too")
    delta, measurement = decide([pre], [post], replay=replays_returning(second, first))
    assert measurement.statuses[KEY] == {"message": FIELD_STABLE, "body": FIELD_VOLATILE}
    assert delta.level2 == {NODE: D.CHANGED_FAILURE}


# ---------------------------------------------------------------- evidence extraction

def test_exception_type_comes_from_pytests_crash_location_line_then_the_message():
    from kriya.tools.test_execution import _failure_type  # pylint: disable=protected-access
    assert _failure_type("assert 1 == 2", "def t():\n>  assert 1 == 2\nE  assert 1 == 2\n\ntests/t.py:3: AssertionError\n") \
        == "AssertionError"
    assert _failure_type("json.decoder.JSONDecodeError: Expecting value", "no crash line") == "json.decoder.JSONDecodeError"
    assert _failure_type("assert 1 == 2", "no crash line") is None
    # the message and body are never omitted from a failing case
    parsed = parse_pytest_case_evidence(
        b'<testsuite tests="1" failures="1" errors="0" skipped="0"><testcase classname="tests.t" name="test_x">'
        b'<failure message="AssertionError: boom">body\ntests/t.py:3: AssertionError</failure></testcase></testsuite>')
    assert parsed["cases"][KEY] == {"outcome": "failed", "failure_type": "AssertionError",
                                    "message": "AssertionError: boom", "body": "body\ntests/t.py:3: AssertionError"}


def test_signature_digests_keep_material_text_and_use_only_the_existing_volatile_rules():
    first = pytest_suite_evidence(raw_for(("failed", "AssertionError", "expected 3 got 4", "at /tmp/abc/x.py 0x7ffee1")))
    second = pytest_suite_evidence(raw_for(("failed", "AssertionError", "expected 3 got 5", "at /tmp/zzz/x.py 0x1234ab")))
    a, b = first.by_key()[KEY], second.by_key()[KEY]
    assert a.message_digest != b.message_digest, "a numeric change in a message is material"
    assert a.body_digest == b.body_digest, "temp paths and addresses are the existing volatile tokens"


def test_junit_key_matches_pytests_own_address_mangling():
    assert junit_key_for_node_id("tests/test_x.py::test_a") == "tests.test_x::test_a"
    assert junit_key_for_node_id("tests/sub/test_x.py::TestC::test_a[1-a/b]") == "tests.sub.test_x.TestC::test_a[1-a/b]"
    assert junit_key_for_node_id("tests/test_x.py") == "::tests.test_x"


def test_checkpoint_round_trip_and_an_unsupported_evidence_version_is_never_reinterpreted():
    base = baseline(outcome(suite(sig(), sig("tests.t::test_ok", "passed"), sig("tests.t::test_s", "skipped"))))
    data = base.to_dict()
    assert ValidationBaseline.from_dict(json.loads(json.dumps(data))) == base
    data["outcome"]["pytest_evidence"]["version"] = 99
    loaded = ValidationBaseline.from_dict(data).outcome.pytest_evidence
    assert loaded.complete is False and loaded.reason == "PYTEST_EVIDENCE_VERSION_UNSUPPORTED"


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
        delta = classify_baseline_delta(baseline(pre), post, stability={KEY: {"message": FIELD_VOLATILE}})
        assert delta == _whole_output_delta(pre, post, classify_level1_delta(pre, post))
        assert delta.authority == WHOLE_OUTPUT_AUTHORITY and delta.pytest_evidence_status is None


def test_jvm_reports_carry_no_pytest_evidence(tmp_path):
    report = test_execution.TestExecutionReport(gate_id="g", runner="maven", workspace=str(tmp_path))
    assert report.pytest_evidence() is None
    assert pytest_suite_evidence({"success": False, "output": "x"}) is None


# ---------------------------------------------------------------- real pytest: the reproducer and controls

def _real_replay(root, scratch):
    count = []

    def replay(node_ids):
        copy = scratch / f"replay{len(count)}"
        count.append(list(node_ids))
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".kriya", "__pycache__", ".pytest_cache"))
        return validator_for(copy).run_tests(target_test=list(node_ids))
    replay.calls = count
    return replay


def _real_baseline(root):
    return ValidationBaseline(workspace_revision="rev", run_id="r", captured_at=0.0,
                              outcome=build_validation_outcome(validator_for(root).run_tests()),
                              invocation=ValidationInvocation("polymorphic_validator.run_tests", "full_suite"))


def test_reg_r1_reproducer_untouched_baseline_against_itself_is_no_regression(tmp_path):
    """OBS-4, generically: two runs of the SAME untouched code. Before REG-R1: level1 CHANGED_FAILURE, blocking.
    After: per-test authority; the run-varying fields are measured volatile on the untouched baseline only."""
    root = write_project(tmp_path / "ws")
    base = _real_baseline(root)
    post = build_validation_outcome(validator_for(root).run_tests())
    replay = _real_replay(root, tmp_path)
    delta, measurement = classify_with_baseline_stability(baseline=base, post=post, post_environment=None, cache={},
                                                          replay=replay, current_revision=lambda: "rev")
    assert delta.level1.classification == D.CHANGED_FAILURE               # the whole output differs (diagnostic)
    assert delta.authority == PYTEST_PER_TEST_AUTHORITY and delta.blocking is False
    assert delta.level2 == {STABLE: D.PRE_EXISTING_FAILURE, VARYING_MESSAGE: D.PRE_EXISTING_FAILURE,
                            VARYING_BODY: D.PRE_EXISTING_FAILURE}
    assert delta.volatile_fields_ignored == {VARYING_MESSAGE: ("message", "body"), VARYING_BODY: ("body",)}
    assert replay.calls == [sorted([VARYING_MESSAGE, VARYING_BODY])] * 2
    assert {r["test"]: r["fields"] for r in measurement.records} == {
        junit_key_for_node_id(VARYING_MESSAGE): {"message": FIELD_VOLATILE, "body": FIELD_VOLATILE},
        junit_key_for_node_id(VARYING_BODY): {"message": FIELD_STABLE, "body": FIELD_VOLATILE}}


def test_stable_changed_failure_control_is_blamed(tmp_path):
    """A candidate that changes the message of a failure the baseline reproduces exactly is CHANGED_FAILURE."""
    root = write_project(tmp_path / "ws")
    base = _real_baseline(root)
    candidate = write_project(tmp_path / "cand", SUITE.replace('"arithmetic is stable"', '"arithmetic changed"'))
    post = build_validation_outcome(validator_for(candidate).run_tests())
    delta, _ = classify_with_baseline_stability(baseline=base, post=post, post_environment=None, cache={},
                                                replay=_real_replay(root, tmp_path), current_revision=lambda: "rev")
    assert delta.level2[STABLE] == D.CHANGED_FAILURE and delta.blocking
    assert delta.level2[VARYING_MESSAGE] == delta.level2[VARYING_BODY] == D.PRE_EXISTING_FAILURE


def test_new_failure_control_is_blamed(tmp_path):
    root = write_project(tmp_path / "ws")
    base = _real_baseline(root)
    candidate = write_project(tmp_path / "cand", SUITE.replace("    assert True\n", "    assert False\n", 1))
    post = build_validation_outcome(validator_for(candidate).run_tests())
    delta, _ = classify_with_baseline_stability(baseline=base, post=post, post_environment=None, cache={},
                                                replay=_real_replay(root, tmp_path), current_revision=lambda: "rev")
    assert delta.level2["tests/test_suite.py::test_passes"] == D.NEW_FAILURE and delta.blocking


def test_the_production_replay_runs_only_the_named_tests_on_a_fresh_copy_never_the_workspace(tmp_path):
    root = write_project(tmp_path / "ws")
    (root / ".kriya").mkdir()
    (root / ".kriya" / "marker").write_text("control plane")
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    original = pytest_stability.PolymorphicValidator if hasattr(pytest_stability, "PolymorphicValidator") else None
    assert original is None  # imported lazily inside the replay
    from kriya.tools.validate import PolymorphicValidator
    seen = []
    real_run_tests = PolymorphicValidator.run_tests

    def spy(self, target_test=None):
        seen.append((self.workspace_path, target_test, sorted(p.name for p in pathlib.Path(self.workspace_path).iterdir())))
        self._resolve_python_interpreter = lambda: (__import__("sys").executable, None)
        return real_run_tests(self, target_test=target_test)
    PolymorphicValidator.run_tests = spy
    try:
        result = pytest_stability.replay_on_untouched_baseline([STABLE], workspace_path=str(root), autonomy_cfg=None)
    finally:
        PolymorphicValidator.run_tests = real_run_tests
    [(workspace, targets, names)] = seen
    assert workspace != str(root) and targets == [STABLE] and ".kriya" not in names
    assert not pathlib.Path(workspace).exists(), "the copy is always removed"
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before
    evidence = pytest_suite_evidence(result)
    assert evidence.complete and [c.key for c in evidence.cases] == [junit_key_for_node_id(STABLE)]


# ---------------------------------------------------------------- evidence retention (observational)

def test_gate_result_retains_raw_stdout_stderr_and_junit_and_the_decision_record_its_artifacts(tmp_path, monkeypatch):
    from kriya.core.attempt_evidence import scope

    emitted = []
    monkeypatch.setattr(scope, "capture_mode", lambda: "full")
    monkeypatch.setattr(scope, "emit", lambda kind, payload, content=None, **_k: emitted.append((kind, payload, content)))
    root = write_project(tmp_path / "ws")
    result = validator_for(root).run_tests()           # the gate itself records its gate.result
    [(kind, payload, content)] = [e for e in emitted if e[0] == "gate.result"]
    raw = result["pytest_evidence"]["raw"]
    assert content["pytest_stdout"] == raw["stdout"] and content["pytest_junit"] == raw["junit"]
    assert content["pytest_stderr"] == raw["stderr"] == ""
    assert result["output"] == raw["stdout"] + "\n" + raw["stderr"]
    assert payload["pytest_evidence"] == {"complete": True, "integrity_ok": True, "integrity_reason": None}

    stable = raw_for(("failed", "AssertionError", "m1", "b1"))
    pre = pytest_suite_evidence(stable).by_key()[KEY]
    base = baseline(outcome(suite(pre)))
    post = outcome(suite(sig(message="m2", body=pre.body_digest)))
    delta, measurement = classify_with_baseline_stability(baseline=base, post=post, post_environment=None, cache={},
                                                          replay=replays_returning(stable), current_revision=lambda: "rev")
    record_regression_decision("full_regression", base, post, delta, measurement)
    [(kind, payload, content)] = [e for e in emitted if e[0] == "regression.decision"]
    assert payload["blocking"] is True and payload["authority"] == PYTEST_PER_TEST_AUTHORITY
    assert json.loads(content["comparison"])["level2"] == {NODE: "CHANGED_FAILURE"}
    assert json.loads(content["stability"])[0]["fields"] == {"message": FIELD_STABLE, "body": FIELD_STABLE}
    assert json.loads(content["baseline_cases"])["failing"][0]["key"] == KEY
    assert json.loads(content["post_cases"])["failing"][0]["message_digest"] == "m2"


def test_recording_never_changes_or_breaks_a_decision(monkeypatch):
    from kriya.core.attempt_evidence import scope

    def explode(*_a, **_k):
        raise RuntimeError("store down")
    monkeypatch.setattr(scope, "capture_mode", lambda: "full")
    monkeypatch.setattr(scope, "emit", explode)
    delta, _ = decide([sig()], [sig(outcome="passed")], post_success=True)
    snapshot = repr(delta)
    record_regression_decision("full_regression", baseline(outcome(suite(sig()))), outcome(suite(sig())), delta, None)
    assert repr(delta) == snapshot


def test_a_baseline_that_had_drifted_when_the_replay_copies_were_taken_is_never_replayed():
    """Both revision checks matter: drift present before the replays (even if gone afterwards) means the copies
    were not the untouched baseline - no replay runs, nothing is measured or cached."""
    revisions = iter(["drifted", "rev", "rev"])
    replay = replays_returning(raw_for(("failed", "AssertionError", "m1", "b1")))
    cache = {}
    delta, measurement = classify_with_baseline_stability(
        baseline=baseline(outcome(suite(sig()))), post=outcome(suite(sig(message="m2"))), post_environment=None,
        cache=cache, replay=replay, current_revision=lambda: next(revisions))
    assert replay.calls == [] and cache == {}
    assert measurement.records[0]["reason"] == "BASELINE_REVISION_CHANGED"
    assert delta.level2 == {NODE: D.STABILITY_UNRESOLVED} and delta.blocking

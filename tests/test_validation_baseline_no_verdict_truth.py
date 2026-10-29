"""VALIDATION-BASELINE-OUTCOME-NO-VERDICT-READ-AS-COMPLETED-FAILURE-001 (Wave-2 Step 10B).

Invariant: a validator NO-VERDICT (a typed ``environment_reason_code`` result: the tool could not start, or
returned nothing trustworthy) never becomes a candidate failure, a PASS or a trustworthy baseline merely because it
crosses the outcome / capture / persistence / delta / attribution boundary. A genuine failure stays a failure and a
success stays a success.

Live shape (MEASURED at a47f7b9, WAVE2_STEP10B_VALIDATION_BASELINE_NO_VERDICT_2026-10-29/PREFIX_repro_output):
``build_validation_outcome`` read the coded result as ``execution_status="completed"`` with a failure fingerprint,
``capture_brownfield_baselines`` kept it as a ``captured`` baseline (no hard stop under policy ``required``), the
baseline was reused on resume, a genuine POST failure then read as CHANGED_FAILURE (candidate blamed) and an identical
infrastructure error as PRE_EXISTING_FAILURE (not blocking).
"""
import json
import subprocess

import pytest
from _strict_doubles import strict_config

from kriya.tools.test_execution import PYTEST_CASE_EVIDENCE_VERSION
from kriya.workflow.baseline_policy import full_suite_evidence_for_reuse
from kriya.workflow.pytest_stability import classify_with_baseline_stability
from kriya.workflow.suite_attribution import attribute_suite_result
from kriya.workflow.validation_baseline import (
    DeltaClassification,
    ValidationBaseline,
    ValidationInvocation,
    build_validation_outcome,
    capture_brownfield_baselines,
    capture_validation_baseline,
    classify_baseline_delta,
    is_baseline_reusable,
)

D = DeltaClassification
CODE = "MAVEN_INVOCATION_FAILED"
NO_VERDICT_OUTPUT = "Failed to invoke mvn: [Errno 2] No such file or directory: 'mvn'"
NO_VERDICT = {"success": False, "environment_reason_code": CODE, "output": NO_VERDICT_OUTPUT}
FAILURE = {"success": False, "output": "[ERROR] Tests run: 3, Failures: 1\nFAILED: com.x.FooTest.bar expected 1 but was 2"}
# A genuine failure whose text happens to equal the infrastructure message: only the typed code tells them apart.
LOOKALIKE_FAILURE = {"success": False, "output": NO_VERDICT_OUTPUT}
SUCCESS = {"success": True, "output": "[INFO] Tests run: 3, Failures: 0"}
INVOCATION = ValidationInvocation(command_identity="polymorphic_validator.run_tests", selection_identity="full_suite",
                                  environment_fingerprint="env", target_test=None)


def _baseline(raw, revision="rev1"):
    return capture_validation_baseline(workspace_revision=revision, run_id="run", invocation=INVOCATION, raw_result=raw)


def _capture(raw, **kw):
    return capture_brownfield_baselines(
        run_id="run", target_test=kw.pop("target_test", None), full_regression_policy=kw.pop("policy", "required"),
        run_validator=lambda _target: raw, compute_revision=lambda: "rev1", **kw)


# ----------------------------------------------------------------------------- the outcome seam

@pytest.mark.parametrize("raw, status, success, has_fingerprint", [
    (NO_VERDICT, "environment_failure", False, True),
    (FAILURE, "completed", False, True),
    (SUCCESS, "completed", True, False),
])
def test_the_outcome_keeps_a_no_verdict_a_genuine_failure_and_a_success_apart(raw, status, success, has_fingerprint):
    outcome = build_validation_outcome(dict(raw), evidence_ref="ref-1")
    assert (outcome.execution_status, outcome.success) == (status, success)
    assert (outcome.failure_fingerprint is not None) is has_fingerprint
    assert outcome.evidence_ref == "ref-1"


def test_the_typed_code_is_read_before_success_and_a_code_on_a_passing_flag_is_still_no_verdict():
    outcome = build_validation_outcome({"success": True, "environment_reason_code": CODE, "output": ""})
    assert (outcome.execution_status, outcome.success) == ("environment_failure", False)


# ----------------------------------------------------------------------------- capture

def test_a_no_verdict_is_not_a_baseline_a_failure_and_a_success_are():
    no_verdict, failure, success = (_baseline(raw) for raw in (NO_VERDICT, FAILURE, SUCCESS))
    assert (no_verdict.status, no_verdict.outcome) == ("baseline_indeterminate", None)
    assert CODE in no_verdict.indeterminate_reason
    assert (failure.status, failure.outcome.execution_status, failure.outcome.success) == ("captured", "completed", False)
    assert (success.status, success.outcome.execution_status, success.outcome.success) == ("captured", "completed", True)


def test_an_explicit_indeterminate_reason_is_kept_for_a_no_verdict():
    baseline = capture_validation_baseline(workspace_revision="rev1", run_id="run", invocation=INVOCATION,
                                           raw_result=NO_VERDICT, indeterminate_reason="caller said so")
    assert (baseline.status, baseline.indeterminate_reason) == ("baseline_indeterminate", "caller said so")


def test_required_policy_hard_stops_on_a_no_verdict_baseline_and_only_on_that():
    stopped = _capture(NO_VERDICT)
    assert stopped.full_regression.status == "baseline_indeterminate"
    assert CODE in stopped.hard_stop_reason
    for raw in (FAILURE, SUCCESS):
        captured = _capture(raw)
        assert captured.hard_stop_reason is None and captured.full_regression.status == "captured"
        assert captured.full_regression.outcome.success is raw["success"]


def test_a_targeted_no_verdict_is_indeterminate_not_a_captured_failure():
    captured = _capture(NO_VERDICT, target_test=("tests/test_a.py",), policy="disabled")
    assert captured.targeted.status == "baseline_indeterminate" and captured.targeted.outcome is None


# ----------------------------------------------------------------------------- persistence / reuse

@pytest.mark.parametrize("raw, success", [(FAILURE, False), (SUCCESS, True)])
def test_a_trustworthy_baseline_survives_persistence_with_its_status(raw, success):
    restored = ValidationBaseline.from_dict(json.loads(json.dumps(_baseline(raw).to_dict())))
    assert (restored.status, restored.outcome.execution_status, restored.outcome.success) == ("captured", "completed", success)
    assert is_baseline_reusable(restored, current_workspace_revision="rev1", invocation=INVOCATION)


def test_a_persisted_no_verdict_baseline_is_never_reused_and_the_validator_runs_again():
    persisted = json.loads(json.dumps(_baseline(NO_VERDICT).to_dict()))
    restored = ValidationBaseline.from_dict(persisted)
    assert not is_baseline_reusable(restored, current_workspace_revision="rev1", invocation=INVOCATION)
    calls = []

    def validator(target):
        calls.append(target)
        return SUCCESS

    resumed = capture_brownfield_baselines(
        run_id="run", target_test=None, full_regression_policy="required", run_validator=validator,
        compute_revision=lambda: "rev1", resume_baseline_full_regression=persisted)
    assert calls == [None]
    assert (resumed.full_regression_source, resumed.full_regression.status) == ("captured", "captured")
    assert resumed.full_regression.outcome.success is True


# ----------------------------------------------------------------------------- baseline delta / policy consumers

@pytest.mark.parametrize("pre_raw", [SUCCESS, FAILURE, LOOKALIKE_FAILURE])
def test_a_post_no_verdict_blocks_as_infrastructure_whatever_the_baseline_was(pre_raw):
    delta = classify_baseline_delta(_baseline(pre_raw), build_validation_outcome(dict(NO_VERDICT)))
    assert delta.level1.classification is D.INFRASTRUCTURE_ENVIRONMENT_FAILURE
    assert delta.blocking and "level1:INFRASTRUCTURE_ENVIRONMENT_FAILURE" in delta.blocking_reasons


@pytest.mark.parametrize("pre_raw, post_raw, classification, blocking", [
    (SUCCESS, SUCCESS, D.UNCHANGED_PASS, False),
    (SUCCESS, FAILURE, D.NEW_FAILURE, True),
    (FAILURE, SUCCESS, D.RESOLVED_FAILURE, False),
    (FAILURE, FAILURE, D.PRE_EXISTING_FAILURE, False),
    (FAILURE, LOOKALIKE_FAILURE, D.CHANGED_FAILURE, True),
])
def test_genuine_failure_and_success_keep_their_delta_classification(pre_raw, post_raw, classification, blocking):
    delta = classify_baseline_delta(_baseline(pre_raw), build_validation_outcome(dict(post_raw)))
    assert (delta.level1.classification, delta.blocking) == (classification, blocking)


def _pytest_raw(success, outcome):
    """A complete pytest report for one test (the run_tests shape REG-R1 reads)."""
    case = {"outcome": outcome, "failure_type": None if outcome == "passed" else "AssertionError",
            "message": None if outcome == "passed" else "m", "body": None if outcome == "passed" else "b"}
    session = "platform t -- Python 3.12, pytest-9.1.1, pluggy-1.6.0\nrootdir: /w\nplugins: p-1.0\n"
    return {"success": success,
            "output": "=== test session starts ===\n" + session
                      + ("FAILED tests/t.py::test_x - x\n" if outcome != "passed" else "") + "=== 1 done in 0.1s ===",
            "pytest_evidence": {"version": PYTEST_CASE_EVIDENCE_VERSION, "complete": True, "reason": None,
                                "report_files": [{"path": "r.xml", "sha256": "d"}],
                                "evidence": {"cases": {"tests.t::test_x": case}, "integrity": {"ok": True, "reason": None}},
                                "raw": {"stdout": "", "stderr": "", "junit": ""}}}


@pytest.mark.parametrize("pre_outcome", ["passed", "failed"])
def test_reg_r1_a_pytest_baseline_against_a_post_no_verdict_blocks_without_measuring_stability(pre_outcome):
    pre = _baseline(_pytest_raw(pre_outcome == "passed", pre_outcome))
    assert pre.outcome.pytest_evidence is not None
    replays = []
    delta, measurement = classify_with_baseline_stability(
        baseline=pre, post=build_validation_outcome(dict(NO_VERDICT)), post_environment=None, cache={},
        replay=lambda target: replays.append(target) or {}, current_revision=lambda: "rev1", working_directory="/w")
    assert delta.blocking and delta.level1.classification is D.INFRASTRUCTURE_ENVIRONMENT_FAILURE
    assert measurement is None and replays == []


def test_reg_r1_a_genuine_pytest_pass_against_a_pytest_baseline_still_does_not_block():
    delta, _ = classify_with_baseline_stability(
        baseline=_baseline(_pytest_raw(True, "passed")), post=build_validation_outcome(_pytest_raw(True, "passed")),
        post_environment=None, cache={}, replay=lambda target: {}, current_revision=lambda: "rev1",
        working_directory="/w")
    assert not delta.blocking and delta.level1.classification is D.UNCHANGED_PASS


def _attribute(baseline, result, tmp_path):
    return attribute_suite_result(
        scope="full_regression", baseline=baseline, result=dict(result), post_environment=None, stability_cache={},
        replay=lambda target: pytest.fail("a non-pytest suite result must not need a stability replay"),
        workspace_path=str(tmp_path))


@pytest.mark.parametrize("pre_raw", [SUCCESS, FAILURE])
def test_suite_attribution_blocks_a_no_verdict_as_infrastructure_never_as_pre_existing(tmp_path, pre_raw):
    attribution = _attribute(_baseline(pre_raw), NO_VERDICT, tmp_path)
    assert attribution.blocking and not attribution.pre_existing_only
    assert attribution.delta.level1.classification is D.INFRASTRUCTURE_ENVIRONMENT_FAILURE
    assert attribution.suite_success is False


def test_suite_attribution_keeps_genuine_outcomes(tmp_path):
    assert not _attribute(_baseline(FAILURE), FAILURE, tmp_path).blocking          # pre-existing only
    assert _attribute(_baseline(FAILURE), FAILURE, tmp_path).pre_existing_only
    assert _attribute(_baseline(SUCCESS), FAILURE, tmp_path).blocking              # a new failure
    passed = _attribute(_baseline(SUCCESS), SUCCESS, tmp_path)
    assert not passed.blocking and passed.suite_success is True


def _git_workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("a\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"]):
        subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)
    return str(workspace)


def test_a_terminal_suite_result_is_kept_as_next_run_evidence_only_when_it_is_a_verdict(tmp_path):
    workspace, autonomy = _git_workspace(tmp_path), strict_config().autonomy

    def kept(raw):
        return full_suite_evidence_for_reuse(workspace, autonomy, goal=None, run_id="run", raw_result=raw)

    assert kept(NO_VERDICT) is None
    assert kept(LOOKALIKE_FAILURE)["outcome"]["success"] is False
    assert kept(SUCCESS)["outcome"]["success"] is True

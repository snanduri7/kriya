"""OI-83 (MVN-VALIDATE-FAIL-OPEN-NEW) - final disposition: FIXED + VERIFIED, with the propagation traced end to end.

The historical claim: `PolymorphicValidator.run_pom_validate` ended in ``except Exception: return {"success": True, ... "skipped, not confirmed valid"}``, so any unexpected
failure (a policy denial from the command audit, a coding error) was a PASS of the pom_validate gate.

Traced and pinned at every level (tests/test_pom_validate_fail_closed.py pins the validator and the gate):
  * validator: an invocation failure (OSError) is a failed validation (typed, F7), a timeout is a failed validation (typed, F7), a containment setup failure and ANY other exception propagate;
  * gate (`_run_pom_validation_gate`): an exception from the validator records no gate outcome at all, in particular no passing one;
  * RecoveryCoordinator / retry (`classify_attempt_exception`): the propagated exception is classified - a policy-denial style RuntimeError is an ordinary retryable `general_error`, a Python
    coding error (NameError / AttributeError / TypeError) is the INTERNAL KRIYA ERROR stop;
  * the run's result (below, the real direct workflow): never a success, nothing applied to the workspace, no passing gate recorded.
"""
import pytest
from test_greenfield_direct_plan_runtime import FILES, _succeeded, run_direct

from kriya.workflow.recovery_coordinator import classify_attempt_exception


def _classify(error):
    return classify_attempt_exception(error, None, last_attempt_mode=None, ground_scope_denial=lambda _exc, _ctx: None)


def test_a_policy_denial_style_exception_is_an_ordinary_retryable_failure_never_a_pass():
    classified = _classify(RuntimeError("policy denied the command"))
    assert classified.failure.type == "general_error" and not classified.internal_framework_bug


@pytest.mark.parametrize("error", [NameError("coding error"), AttributeError("coding error"), TypeError("coding error")], ids=["name", "attribute", "type"])
def test_a_python_coding_error_in_the_validation_is_the_internal_stop_never_a_pass(error):
    classified = _classify(error)
    assert classified.internal_framework_bug and classified.failure.type == "internal_framework_error"


def _raises(error):
    def hook(_workspace):
        raise error
    return hook


@pytest.mark.parametrize("error,first_type", [(RuntimeError("policy denied the command"), "general_error"), (NameError("coding error"), "internal_framework_error")],
                         ids=["policy-denial", "coding-error"])
def test_an_unexpected_exception_in_the_pom_validation_never_becomes_a_passing_run(tmp_path, error, first_type):
    """The real direct workflow with a pom.xml among the generated files: the validator raises on its first call. (The scripted model transport of this harness cannot answer a
    SECOND attempt's prompts - a test-harness artifact, not a product behaviour - so only the first classification and the invariants are asserted.)"""
    run = run_direct(tmp_path, tamper_during_pom=_raises(error))
    result = run.result
    assert not _succeeded(result), result
    assert result["quality_gates_passed"] is False and result["candidate_gates_passed"] is False
    assert [path for path in FILES if (run.workspace / path).exists()] == []                  # nothing was applied to the workspace
    outcomes = [outcome["type"] for outcome in run.gate_outcomes]
    assert outcomes and outcomes[0] == first_type, outcomes                                    # the propagated exception is the recorded failure
    assert not any(outcome.get("success") is True or outcome.get("passed") is True for outcome in run.gate_outcomes)

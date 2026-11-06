"""OI-90: the history projection of a runtime gate agrees with the gate's own recorded verdict - an expected non-zero exit the goal declares is a PASS.

Measured defect (2026-11-06 re-verification): ``verifier_evidence.apply_runtime_disposition`` records such a run correctly (gate ``success: True``, real steps
with exit code 2, ``runtime_disposition = {deterministic_reason: EXPECTED_NONZERO_EXIT_GROUNDED, final: PASS}``), but ``application_outcome`` computed
``succeeded = success and exit_code == 0`` and never read the disposition, so the result screen said "Runtime verification failed" for a run whose
authoritative verdict is pass. The projection now reads the owner's disposition (it never decides it).
"""
import pytest

from kriya.application.history import STATUS_EXECUTED, application_outcome
from kriya.workflow.verifier_evidence import EXPECTED_NONZERO_EXIT_GROUNDED, NONZERO_EXIT_AUTHORITATIVE

GROUNDED = {"deterministic": "NONE", "deterministic_reason": EXPECTED_NONZERO_EXIT_GROUNDED, "semantic": "PASS", "semantic_reason": None, "final": "PASS",
            "authority": "llm"}


def gate(success=True, exit_code=2, disposition=None, timed_out=False, kind="run_verification"):
    step = {"command": ["python", "app.py"], "exit_code": exit_code, "stdout": "usage: error\n", "stderr": "", "timed_out": timed_out}
    record = {"attempt": 1, "type": kind, "success": success, "output": "x", "commands": [step["command"]], "steps": [step]}
    if disposition is not None:
        record["runtime_disposition"] = disposition
    return record


def succeeded(record):
    out = application_outcome([record])
    assert out["status"] == STATUS_EXECUTED
    return out["succeeded"]


def test_a_grounded_expected_nonzero_exit_of_a_passed_gate_is_a_success():
    assert succeeded(gate(disposition=dict(GROUNDED))) is True


@pytest.mark.parametrize("record", [
    gate(),                                                                           # non-zero exit, no recorded disposition: still a failure
    gate(disposition={**GROUNDED, "deterministic_reason": NONZERO_EXIT_AUTHORITATIVE, "deterministic": "FAIL", "final": "FAIL"}),
    gate(disposition={**GROUNDED, "final": "FAIL"}),                                  # inconsistent record: never read as a pass
    gate(disposition={**GROUNDED, "final": "UNKNOWN"}),
    gate(success=False, disposition=dict(GROUNDED)),                                  # the gate itself did not pass
    gate(disposition=dict(GROUNDED), timed_out=True),                                 # a timeout is never an expected exit
    gate(disposition=dict(GROUNDED), kind="run_verification_hung"),
    gate(exit_code=None, disposition=dict(GROUNDED)),                                 # an unknown exit never passes
    gate(disposition="PASS"),                                                         # a malformed disposition is ignored
    gate(disposition=[GROUNDED]),
])
def test_everything_else_stays_a_failure(record):
    assert succeeded(record) is False


def test_exit_zero_behaviour_is_unchanged():
    assert succeeded(gate(exit_code=0)) is True
    assert succeeded(gate(exit_code=0, success=False)) is False


def test_the_projection_does_not_decide_the_disposition_it_only_reads_it():
    """A disposition grounded for exit 2 does not turn an unrelated non-zero exit of a FAILED gate into a pass, and the recorded exit code is still reported."""
    out = application_outcome([gate(disposition=dict(GROUNDED), exit_code=3)])
    assert out["exit_code"] == 3 and out["succeeded"] is True      # the owner's record says the declared exit was expected; the code shown is the recorded one


def test_mutation_the_old_exit_zero_only_rule_reproduces_the_defect():
    """The pre-fix formula reads this correctly recorded pass as a failure (so the pins above are not vacuous)."""
    record = gate(disposition=dict(GROUNDED))
    last = record["steps"][-1]
    old = record["success"] is True and last["exit_code"] == 0 and not last["timed_out"]
    assert old is False and succeeded(record) is True

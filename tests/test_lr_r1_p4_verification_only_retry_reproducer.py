"""LR-R1-P4 deterministic reproducer: a verification-only unit retried with no
possible change (CAGC-v2 80-run census: terminal in 6 runs - spring-boot-
pagesize A r1/r4/r5, B r6/r7; python-symbol-zipb B r7).

Investigation: handover/LR_R1_P4_VERIFICATION_ONLY_RETRY_INVESTIGATION.md.
First written to pin the defective behaviour (investigation branch, 5576bc3);
flipped to the fixed behaviour before the P4 fix was implemented. Through the
real WorkflowController enforce loop and the real workflow; only the model
transport and the two toolchain gates are stubbed (the test-gate stub fails
only while s2 runs):

- s1 (editable producer) passes its own gates;
- s2 (execution_role=verification, no planned files: DENY_ALL) runs its test
  verifier, which fails deterministically;
- s2 never calls the Developer, never re-runs s1, receives no new evidence;
- defective behaviour: the same verification was retried - progress PROGRESS,
  then REPEATED_VECTOR x3 with no changed dimension, then
  RETRY_NO_PROGRESS_EXHAUSTED (four verifications);
- fixed behaviour (LR-R1-P4): nothing can change the next verification's
  inputs (no mutation authority, inputs digest unchanged, no recovery route),
  so no equivalent retry is scheduled: one verification, the same truthful
  terminal (s2 failed, failure_category no_progress), typed reason
  VERIFICATION_RETRY_NO_CHANGE_POSSIBLE.
- BACKEND-FINAL-CLOSURE-005 (ENFORCE-VERIFICATION-UNIT-STOP-BEFORE-TERMINAL-
  GATE-001): the enforce controller now reopens the completed upstream owner
  (s1) ONCE with s2's own gate output; here the scripted Developer regenerates
  the same bytes, so nothing is folded, s2 is not verified a second time and
  the terminal above stands unchanged (tests/test_backend_final_closure_005_
  verification_owner_reopen.py covers the owner that does fix it).
"""
from _lr_r1_p4_run import BASE_SOURCE, SERVICE, _enforce


def test_a_failing_verification_only_unit_with_no_change_mechanism_is_not_retried(tmp_path):
    import os

    from kriya.core.attempt_evidence import reader
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR

    workspace, events, result, model_calls, test_gate_runs = _enforce(tmp_path)

    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "s2": "failed"}   # failure stays failure
    assert (workspace / SERVICE).read_text() == BASE_SOURCE
    assert result.legacy_result["status"] == "failed"

    # s2: ONE verification, no Developer, no model call at all; the owner s1 was reopened once with s2's gate
    # output (its own gates ran: the third test-gate run is s1's terminal regression) and regenerated identical
    # bytes, so s2 was not verified again.
    assert test_gate_runs == ["s1", "s2", "s1"]
    assert [c for c in model_calls if c[0] == "s2"] == []
    assert sum(1 for _unit, first in model_calls if "Developer Agent" in first) == 2   # s1 planned + s1 reopened

    # s2's verification failed once; the reopened s1 regenerated identical bytes, which is a typed refusal at the
    # unit (ENFORCE-IDENTICAL-WRITE-COMPLETION-001: an identical rewrite is not a repair) - one attempt each.
    assert [(e.attempt, e.details.get("failure_type")) for e in events if e.kind == "attempt.failed"] == [
        (1, "test"), (1, "verified_no_change_refused")]
    [admission] = [e.details for e in events if e.kind == "retry.verification_admission"]
    assert admission["verification_only"] is True and admission["mutation_possible"] is False
    assert admission["workspace_changed"] is False and admission["recovery_route"] is None
    assert admission["inputs_digest_at_verification"] == admission["inputs_digest_now"]
    assert admission["retryable"] is False and admission["reason_code"] == "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"
    terminals = [e.details for e in events if e.kind == "retry.no_progress_terminal"]
    assert [t["reason_code"] for t in terminals] == ["VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"] * 2  # s2, then the reopened s1
    assert terminals[1]["declared_by"] == "verified_no_change_refused"

    # M1: the deciding recovery record and the run's terminal say why (Q6 / Q9).
    state_dir = os.environ[ENV_STATE_DIR]
    runs = [explain_run(state_dir, run_id) for run_id in reader.list_runs(state_dir)]
    [explained] = [run for run in runs if any(a["unit_id"] == "s2" for a in run["attempts"])]  # s2's own unit run
    [s2] = [a for a in explained["attempts"] if a["unit_id"] == "s2"]
    [decision] = s2["answers"]["Q6"]["items"]
    assert decision["retry"] is False and decision["stop_loop"] is True
    assert decision["no_progress_reason"] == "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"
    # BACKEND-FINAL-CLOSURE-005: the controller's own recorded decision ends the run - the reopened owner produced
    # no accepted change, so s2's typed failure stands (never the owner's successful unit that closed last).
    terminal_cause = explained["Q9"]["terminal_cause"]
    assert terminal_cause["source"] == "controller_terminal_event"
    assert terminal_cause["kind"] == "verification.owner_recovery_rejected"
    assert terminal_cause["reason_codes"] == ["VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"]
    assert "failure stands" in terminal_cause["error"]
    [s2_unit] = [u for u in explained["Q9"]["units"] if u["unit_id"] == "s2"]
    assert s2_unit["quality_gates_passed"] is False and s2_unit["failure_category"] == "no_progress"
    assert explained["Q9"]["items"][0]["terminal_status"] == "FAILURE"
    assert explained["Q9"]["last_recovery_decision"]["no_progress_reason"] == "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"

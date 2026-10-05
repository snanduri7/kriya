"""LR-R1-M1 T6 defect fixes A-F: one test group per defect, each written to
fail before its fix (design §12 T6; owner authorization 2026-10-05)."""
from _chaos_harness import CALC, TEST_SUB, benign_roles, chaos_config
from _t6_harness import direct_run

from kriya.workflow.retry_progress import NO_PROGRESS_TERMINAL_REASON

WRONG_SUB = CALC + "\n\ndef sub(a, b):\n    return a + b\n"
FILES = {"calc.py": CALC, "test_calc.py": TEST_SUB}


def _always(answer):
    return lambda role, request: answer if role == "developer" else benign_roles(role, request)


# -- A: no-progress terminal evidence ----------------------------------------------------------

def test_a_no_progress_terminal_records_its_classification_and_reason(tmp_path, monkeypatch):
    observed = direct_run(tmp_path, monkeypatch, _always(WRONG_SUB), FILES)
    progress = observed.result["retry_progress"]
    assert progress["no_progress_terminated"] is True
    decisions = observed.of("recovery.decision")
    last = decisions[-1]["payload"]
    # The runtime's own already-computed facts, not a recomputation.
    assert last["progress_classification"] == progress["classification"]
    assert last["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON == progress["terminal_reason"]
    assert last["no_progress_terminated"] is True and last["retry"] is False
    # Every earlier decision carries its own classification (the progression).
    assert all("progress_classification" in d["payload"] for d in decisions)
    assert all(d["payload"]["no_progress_reason"] is None for d in decisions[:-1])
    # explain: Q6 of the last attempt says why it stopped; Q9 names the terminal cause.
    final = observed.attempt(observed.attempts()[-1]["attempt"])
    [why] = final["Q6"]["items"]
    assert why["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON
    assert why["progress_classification"] == progress["classification"]
    q9 = observed.explained["Q9"]
    assert q9["last_recovery_decision"]["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON
    assert q9["last_recovery_decision"]["progress_classification"] == progress["classification"]


def test_a_recording_the_terminal_changes_no_retry_behaviour(tmp_path, monkeypatch):
    """The same scenario with the recorder off: identical attempt count, model
    calls and progress outcome."""
    (tmp_path / "on").mkdir()
    with_recorder = direct_run(tmp_path / "on", monkeypatch, _always(WRONG_SUB), FILES)
    cfg = chaos_config()
    cfg.evidence.attempt_recorder.capture = "off"
    (tmp_path / "off").mkdir()
    from _chaos_harness import ChaosRuntime, RuntimeRegistration, chaos_engine, git_workspace, run_direct

    from kriya.core.state_paths import ENV_STATE_DIR

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "off" / "state"))
    runtime = ChaosRuntime(_always(WRONG_SUB))
    with RuntimeRegistration(runtime):
        without = run_direct(chaos_engine(cfg), "add sub to calc.py", git_workspace(tmp_path / "off", FILES))
    # last_vector_digest covers failure text that names the (different)
    # absolute workspace path; byte identity at one path is the I-2 suite's.
    def comparable(progress):
        return {k: v for k, v in progress.items() if k != "last_vector_digest"}
    assert comparable(without["retry_progress"]) == comparable(with_recorder.result["retry_progress"])
    for key in ("failure_category", "quality_gates_passed", "candidate_gates_passed"):
        assert without.get(key) == with_recorder.result.get(key), key
    # The same model calls in the same order: the same attempts and retries.
    assert runtime.roles == with_recorder.runtime.roles


# -- B: refused proposal / NO_CHANGE / genuinely absent ---------------------------------------

def test_b_a_proposal_refused_before_staging_is_a_refused_candidate_without_invented_bytes(tmp_path, monkeypatch):
    """A10: on the repair attempts the Developer answers a whole file where the
    operation contract requires a patch; the answer for calc.py is refused
    (it does not parse as the required protocol) and no candidate is staged."""
    observed = direct_run(tmp_path, monkeypatch, _always(WRONG_SUB), FILES)
    refused_attempts = [a for a in observed.attempts()
                        if any(d["type"] == "operation_contract" for d in a["answers"]["Q5"].get("diagnoses") or [])]
    assert refused_attempts, [a["answers"]["Q5"] for a in observed.attempts()]
    for attempt in refused_attempts:
        q4 = attempt["answers"]["Q4"]
        assert q4["status"] == "RECORDED", q4
        [change] = q4["items"]
        assert change["decision"] == "REFUSED" and change["candidate_staged"] is False
        # The refusal's own typed reason code, the one its diagnosis carries.
        [diagnosis] = [d for d in attempt["answers"]["Q5"]["diagnoses"] if d["type"] == "operation_contract"]
        assert change["path"] == "calc.py" and change["reason_code"] == diagnosis["reason_code"]
        assert change["diff"] == "NOT_APPLICABLE" and change["after_digest"] == "NOT_APPLICABLE"
        assert "content" not in change                       # no bytes invented
        # The answer did not parse as the patch the contract required.
        assert change["proposal_kind"] == "invalid" and change["parse_reason_code"] == "MODEL_EDIT_PROTOCOL_INVALID"
        # Bound to the parse that produced the proposal.
        parse = next(r for r in observed.records if r["seq"] == change["parse_seq"])
        assert parse["kind"] == "developer.parse" and parse["payload"]["path"] == "calc.py"
        assert parse["attempt_number"] == attempt["attempt"]


def test_b_a_verified_no_change_is_not_missing_evidence(tmp_path, monkeypatch):
    from _t6_harness import shop_enforce

    observed = shop_enforce(tmp_path, monkeypatch)
    statuses = {r.subtask_id: r for r in observed.result.subtask_results}
    assert "VERIFIED_NO_CHANGE" in statuses["s2"].reason_codes
    q4 = observed.attempt(1, unit="s2")["Q4"]
    assert q4 == {"status": "NOT_APPLICABLE", "reason": "model_proposed_no_change"}


def test_b_genuinely_absent_candidate_evidence_stays_not_recorded(tmp_path, monkeypatch):
    """A Developer request whose answer was never parsed and no candidate
    decision: the only state that is reported as missing evidence."""
    from kriya.core.attempt_evidence import scope
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR
    from tests._strict_doubles import strict_config

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))

    class _Context:
        run_id = "run-absent"
    cfg = strict_config()
    with scope.run_scope(_Context()):
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "u1", "direct"):
            with scope.attempt_scope(lambda: 1):
                scope.attempt_opened({"mode": "full_set"})
                with scope.call_scope("developer"):
                    scope.emit("model.request", {"dispatched": True})
        scope.close_run(_Context(), lambda: None)
    q4 = explain_run(str(tmp_path / "state"), "run-absent")["attempts"][0]["answers"]["Q4"]
    assert q4["status"] == "NOT_RECORDED" and "no candidate" in q4["reason"]


def test_b_an_unverified_no_change_is_still_no_proposal_never_a_refused_candidate(tmp_path, monkeypatch):
    """A judgment criterion cannot verify NO_CHANGE: the attempt fails, but
    the model still proposed no mutation - no REFUSED candidate is invented."""
    import test_enforce_verified_no_change as shape
    from _t6_harness import shop_enforce

    observed = shop_enforce(tmp_path, monkeypatch, plans=[lambda: shape._plan(shape.JUDGMENT_CRITERION)])
    s2 = [a for a in observed.attempts() if a["unit_id"] == "s2"]
    assert s2 and any(a["answers"]["Q6"]["status"] == "RECORDED" for a in s2), "the no-change attempt must fail"
    for attempt in s2:
        assert attempt["answers"]["Q4"] == {"status": "NOT_APPLICABLE", "reason": "model_proposed_no_change"}
    assert not [r for r in observed.of("candidate.change", unit_id="s2")]


def test_b_a_parsed_proposal_without_a_candidate_decision_is_missing_evidence(tmp_path, monkeypatch):
    """Negative control: a parsed file proposal, then no staging or refusal
    record (an interrupted attempt) is NOT_RECORDED, never NO_CHANGE."""
    from kriya.core.attempt_evidence import scope
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR
    from tests._strict_doubles import strict_config

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))

    class _Context:
        run_id = "run-interrupted"
    cfg = strict_config()
    with scope.run_scope(_Context()):
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "u1", "direct"):
            with scope.attempt_scope(lambda: 1):
                scope.attempt_opened({"mode": "full_set"})
                scope.emit("developer.parse", {"path": "calc.py", "kind": "file"})
        scope.close_run(_Context(), lambda: None)
    q4 = explain_run(str(tmp_path / "state"), "run-interrupted")["attempts"][0]["answers"]["Q4"]
    assert q4["status"] == "NOT_RECORDED"

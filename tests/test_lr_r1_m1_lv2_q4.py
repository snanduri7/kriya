"""LR-R1-M1 LV-2 (live validation 2026-10-05, run 20261005T133256-87d73817):
an attempt that made no Developer call cannot have a candidate, so its Q4
is NOT_APPLICABLE(no_model_call) - a known fact, never missing evidence.
NOT_RECORDED stays reserved for evidence that should exist and does not.
Explain-only: the records below are what the recorder writes today."""
from kriya.core.attempt_evidence import scope
from kriya.core.attempt_evidence.explain import explain_run
from kriya.core.state_paths import ENV_STATE_DIR
from tests._strict_doubles import strict_config

NO_MODEL_CALL = {"status": "NOT_APPLICABLE", "reason": "no_model_call: no Developer request in this attempt"}
MISSING = {"status": "NOT_RECORDED", "reason": "no candidate was staged or refused in this attempt"}


class _Context:
    run_id = "run-lv2"


def _q4(tmp_path, monkeypatch, attempt_body):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    cfg = strict_config()
    with scope.run_scope(_Context()):
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "s1", "direct"):
            with scope.attempt_scope(lambda: 1):
                attempt_body()
        scope.close_run(_Context(), lambda: None)
    explained = explain_run(str(state), _Context.run_id)
    assert explained["verification"] == "VERIFIED"
    [attempt] = explained["attempts"]
    return attempt["answers"]


def _developer_call(*, dispatched=True, response=True, refusal_type=None):
    with scope.call_scope("developer"):
        with scope.wire_scope():
            payload = {"dispatched": dispatched, "model": "m"}
            if refusal_type:
                payload.update(refusal_type=refusal_type)
            scope.emit("model.request", payload)
            if response:
                scope.emit("model.response", {"dispatched": True, "finish_reason": "stop"})


def test_the_live_shape_no_developer_call_is_not_applicable(tmp_path, monkeypatch):
    """Run 2 attempts 2-4: refused before any model call (ANCHOR_CONTEXT_NOT_ESCALATED,
    FALLBACK_MODEL_INCOMPATIBLE at the call phase): a diagnosis and a recovery decision, nothing else."""
    def body():
        scope.attempt_opened({"mode": "targeted"})
        scope.emit("diagnosis", {"type": "no_progress_retry", "reason_code": "ANCHOR_CONTEXT_NOT_ESCALATED"})
        scope.emit("recovery.decision", {"action": "targeted", "retry": True})
    answers = _q4(tmp_path, monkeypatch, body)
    assert answers["Q4"] == NO_MODEL_CALL
    assert answers["Q1"]["status"] == "NOT_APPLICABLE" and answers["Q3"]["status"] == "NOT_APPLICABLE"


def test_a_verification_only_attempt_has_no_candidate(tmp_path, monkeypatch):
    def body():
        scope.attempt_opened({"mode": "verification_only"})
        scope.emit("gate.result", {"stage": "validator", "gate": "tests", "success": True})
    assert _q4(tmp_path, monkeypatch, body)["Q4"] == NO_MODEL_CALL


def test_a_developer_call_with_no_candidate_evidence_is_still_not_recorded(tmp_path, monkeypatch):
    def body():
        scope.attempt_opened({"mode": "full_set"})
        _developer_call()
    assert _q4(tmp_path, monkeypatch, body)["Q4"] == MISSING


def test_a_gate_that_ran_on_a_candidate_without_its_record_is_not_recorded(tmp_path, monkeypatch):
    """No Developer call, but a validator gate ran in an ordinary attempt (a
    deterministic candidate, e.g. a restoration): the candidate existed, its
    record is missing."""
    def body():
        scope.attempt_opened({"mode": "api_contract_recovery"})
        scope.emit("gate.result", {"stage": "validator", "gate": "compile", "success": True})
    assert _q4(tmp_path, monkeypatch, body)["Q4"] == MISSING


def test_a_pre_dispatch_refusal_keeps_the_typed_no_model_answer(tmp_path, monkeypatch):
    def body():
        scope.attempt_opened({"mode": "full_set"})
        _developer_call(dispatched=False, response=False, refusal_type="OutputBudgetUnsatisfiableError")
    assert _q4(tmp_path, monkeypatch, body)["Q4"] == {
        "status": "NOT_APPLICABLE", "reason": "no_model_answer: OutputBudgetUnsatisfiableError"}


def test_a_staged_or_refused_candidate_is_recorded(tmp_path, monkeypatch):
    for decision in ("STAGED", "REFUSED"):
        sub = tmp_path / decision
        sub.mkdir()

        def body(decision=decision):
            scope.attempt_opened({"mode": "full_set"})
            _developer_call()
            scope.emit("candidate.change", {"decision": decision, "path": "a.py"})
        q4 = _q4(sub, monkeypatch, body)["Q4"]
        assert q4["status"] == "RECORDED" and q4["items"][0]["decision"] == decision


def test_a_non_developer_call_does_not_count_as_a_developer_call(tmp_path, monkeypatch):
    """A triage or verifier call inside the attempt is not a Developer call:
    still no candidate could exist."""
    def body():
        scope.attempt_opened({"mode": "targeted"})
        with scope.call_scope("triage"):
            with scope.wire_scope():
                scope.emit("model.request", {"dispatched": True, "model": "m"})
                scope.emit("model.response", {"dispatched": True, "finish_reason": "stop"})
    assert _q4(tmp_path, monkeypatch, body)["Q4"] == NO_MODEL_CALL

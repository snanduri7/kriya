"""LR-R1-M1.9: doctor row ``evidence.attempt_recorder`` (design §9.4, §13).

Never required (D5: the recorder never blocks a run). PASS when the store
root is writable and the newest store verifies; WARN with capture off, a
corrupt newest store, or a traced run whose recorder was unavailable.
"""
import os

from test_production_doctor import _blocking, _checks, _production_cfg, _run

from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.core.trace import TraceLogger
from kriya.production_doctor import CheckStatus


def _state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    return state


def _sealed_store(state, run_id):
    writer = AttemptEvidenceWriter(str(state), run_id, capture="full", manifest={})
    writer.record("run.opened", {})
    writer.seal("test")
    return writer.directory


def test_a_healthy_store_passes_and_is_never_required(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    _sealed_store(state, "r-1")
    report = _run(tmp_path)
    check = _checks(report)["evidence.attempt_recorder"]
    assert check.status is CheckStatus.PASS and check.required is False
    assert check.evidence["newest_run"] == {"run_id": "r-1", "verification": "VERIFIED"}
    assert check.evidence["capture"] == "full" and check.evidence["store"]["writable"] is True
    assert "evidence.attempt_recorder" not in _blocking(report)


def test_capture_off_warns(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    cfg = _production_cfg(tmp_path)
    cfg.evidence.attempt_recorder.capture = "off"
    check = _checks(_run(tmp_path, cfg=cfg))["evidence.attempt_recorder"]
    assert check.status is CheckStatus.WARN and "capture is off" in check.remediation


def test_a_corrupt_newest_store_warns_and_never_blocks(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    directory = _sealed_store(state, "r-1")
    records = os.path.join(directory, "records.jsonl")
    with open(records, "ab") as handle:
        handle.write(b'{"partial')
    report = _run(tmp_path)
    check = _checks(report)["evidence.attempt_recorder"]
    assert check.status is CheckStatus.WARN and check.evidence["newest_run"]["verification"] == "TRUNCATED_TAIL"
    assert "evidence.attempt_recorder" not in _blocking(report)


def test_a_traced_run_whose_recorder_was_unavailable_is_named(tmp_path, monkeypatch):
    from kriya.core.state_paths import trace_db_path
    from kriya.workflow.run_events import EventAuthority, RunEvent

    _state(tmp_path, monkeypatch)
    cfg = _production_cfg(tmp_path)
    event = RunEvent(kind="evidence.attempt_store", attempt=0, source="test", authority=EventAuthority.ADVISORY,
                     message="unavailable", details={"status": "RECORDER_UNAVAILABLE", "reason": "disk full"})
    TraceLogger(trace_db_path(cfg)).log_run("run-unavailable", "goal", 1.0, 1, "failed", [],
                                            run_events=[event.to_dict()])
    check = _checks(_run(tmp_path, cfg=cfg))["evidence.attempt_recorder"]
    assert check.status is CheckStatus.WARN
    assert check.evidence["last_unavailable_run"]["run_id"] == "run-unavailable"

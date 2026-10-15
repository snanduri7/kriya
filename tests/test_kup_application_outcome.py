"""ApplicationOutcome (PRODUCT-UX-COMPLETION): a read-only projection of the recorded runtime-gate execution. Never invents a command, never claims success the record does not carry."""
from __future__ import annotations

import pytest

from kriya.core.trace import TraceLogger
from kriya.kup.application_outcome import (
    OUTPUT_MAX_CHARS,
    STATUS_EXECUTED,
    STATUS_NOT_APPLICABLE,
    STATUS_UNKNOWN,
    application_outcome,
)
from kriya.kup.inspect import history_detail
from kriya.kup.store import Snapshot


def gate(success=True, exit_code=0, stdout="Hello from Kriya\n", stderr="", timed_out=False, kind="run_verification", command=None, attempt=1):
    step = {"command": command or ["java", "-jar", "target/hello-kriya.jar"], "exit_code": exit_code, "stdout": stdout, "stderr": stderr, "timed_out": timed_out}
    return {"attempt": attempt, "type": kind, "success": success, "output": "ignored", "commands": [step["command"]], "steps": [step]}


def test_exit_zero_with_recorded_success_is_executed_and_succeeded_with_the_recorded_output():
    out = application_outcome([{"attempt": 1, "type": "compile", "success": True}, gate()])
    assert out["status"] == STATUS_EXECUTED and out["succeeded"] is True and out["exit_code"] == 0 and out["timed_out"] is False
    assert out["command"] == "java -jar target/hello-kriya.jar" and out["stdout"] == {"text": "Hello from Kriya\n", "truncated": False}
    assert out["attempt"] == 1


@pytest.mark.parametrize("record", [
    gate(success=True, exit_code=1),                         # the gate says success but the process exited non-zero
    gate(success=False, exit_code=0),                        # exit 0 but the gate did not record success
    gate(success=True, exit_code=0, timed_out=True),         # killed after the timeout
    gate(success=True, exit_code=0, kind="run_verification_hung"),
    gate(success=True, exit_code=None),                      # no exit code recorded
])
def test_a_run_that_did_not_cleanly_succeed_is_executed_but_never_succeeded(record):
    out = application_outcome([record])
    assert out["status"] == STATUS_EXECUTED and out["succeeded"] is False


def test_the_newest_recorded_execution_wins():
    out = application_outcome([gate(success=False, exit_code=1, stdout="boom", attempt=1), gate(success=True, exit_code=0, stdout="ok", attempt=2)])
    assert out["attempt"] == 2 and out["stdout"]["text"] == "ok" and out["succeeded"] is True


def test_no_runtime_gate_means_not_applicable_and_no_command_is_offered():
    for gates in ([], [{"type": "compile", "success": True}], [{"type": "test", "success": True, "steps": [{"command": ["mvn", "test"], "exit_code": 0}]}],
                  [{"type": "run_verification", "success": True, "steps": []}]):
        out = application_outcome(gates)
        assert out == {"status": STATUS_NOT_APPLICABLE, "reason": "no_runtime_execution_recorded"}  # a TEST gate's command is not "the application"


@pytest.mark.parametrize("value", [None, "not json", {"a": 1}, 7])
def test_unreadable_gate_outcomes_are_unknown(value):
    assert application_outcome(value) == {"status": STATUS_UNKNOWN, "reason": "no_gate_outcomes_recorded"}


def test_output_is_bounded_and_secret_scrubbed_and_the_command_too():
    big = "x" * (OUTPUT_MAX_CHARS + 500)
    out = application_outcome([gate(stdout=big, stderr="api_key=sk-abcdefghijklmnopqrstuvwxyz0123456789\n", command=["run.sh", "--token=hunter2supersecret", "AKIAABCDEFGHIJKLMNOP"])])
    assert len(out["stdout"]["text"]) == OUTPUT_MAX_CHARS and out["stdout"]["truncated"] is True
    blob = repr(out)
    for secret in ("sk-abcdefghijklmnopqrstuvwxyz0123456789", "AKIAABCDEFGHIJKLMNOP"):
        assert secret not in blob
    assert "[REDACTED" in blob or "REDACTED" in blob


def test_history_detail_carries_the_projection_as_a_derived_section(tmp_path):
    db = str(tmp_path / "t.db")
    TraceLogger(db).log_run(run_id="r1", goal="Create hello-kriya.", duration_sec=1.0, attempts=1, status="success", files_modified=["pom.xml"], gate_outcomes=[gate()])
    detail = history_detail(Snapshot(snapshot_id="s", directory=str(tmp_path), db_path=db, manifest={}), "r1")
    section = detail["application_outcome"]
    assert section["availability"] == "recorded" and section["provenance"] == "derived:runs.gate_outcomes"
    assert section["data"]["status"] == STATUS_EXECUTED and section["data"]["stdout"]["text"] == "Hello from Kriya\n"
    snap = Snapshot(snapshot_id="s", directory=str(tmp_path), db_path=db, manifest={})
    TraceLogger(db).log_run(run_id="r2", goal="g", duration_sec=1.0, attempts=1, status="success", files_modified=[])  # recorded, but no gate ran
    assert history_detail(snap, "r2")["application_outcome"]["data"]["status"] == STATUS_NOT_APPLICABLE
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE runs SET gate_outcomes = NULL WHERE run_id = 'r2'")  # the column itself is not recorded
    assert history_detail(snap, "r2")["application_outcome"]["data"]["status"] == STATUS_UNKNOWN


@pytest.mark.parametrize("code", ["MANAGED_SERVICE_APPROVAL_UNSUPPORTED", "REQUIRED_RUNTIME_VERIFICATION_DECLINED", "REQUIRED_RUNTIME_VERIFICATION_DISABLED"])
def test_a_runtime_stop_with_a_known_code_is_reported_as_blocked_and_nothing_was_executed(code):
    out = application_outcome([{"attempt": 1, "type": "compile", "success": True},
                               {"attempt": 1, "type": "verification_infrastructure_failure", "success": False, "output": f"{code}: this goal requires something. No service was started."}])
    assert out == {"status": STATUS_NOT_APPLICABLE, "reason": "no_runtime_execution_recorded", "blocked_reason_code": code}


@pytest.mark.parametrize("output", [
    "an unrelated infrastructure failure", "the message mentions MANAGED_SERVICE_APPROVAL_UNSUPPORTED: but not at the start", "MANAGED_SERVICE_APPROVAL_UNSUPPORTED no colon",
    "managed_service_approval_unsupported: lowercase", None, 7, "MANAGED_SERVICE_APPROVAL_UNSUPPORTEDX: near miss",
])
def test_only_the_exact_code_prefix_counts_as_a_blocked_reason(output):
    out = application_outcome([{"attempt": 1, "type": "verification_infrastructure_failure", "success": False, "output": output}])
    assert out == {"status": STATUS_NOT_APPLICABLE, "reason": "no_runtime_execution_recorded"}


def test_a_blocked_reason_is_ignored_when_the_application_actually_ran():
    out = application_outcome([{"attempt": 1, "type": "verification_infrastructure_failure", "success": False, "output": "REQUIRED_RUNTIME_VERIFICATION_DECLINED: x"}, gate()])
    assert out["status"] == STATUS_EXECUTED and "blocked_reason_code" not in out

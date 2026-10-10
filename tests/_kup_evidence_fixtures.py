"""Fixture helpers for the KUP evidence tests: attempt-evidence stores written by the REAL recorder
(``AttemptEvidenceWriter``) in the test's own isolated state directory, plus trace snapshots that carry the
``evidence.attempt_store`` run event. Fixtures only; never a live run."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from kriya.config import AppConfig
from kriya.core.attempt_evidence import model
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter, run_directory
from kriya.core.state_paths import resolve_state_directory, trace_db_path
from kriya.core.trace import TraceLogger
from kriya.kup.cli_ops import run_kup_operation

IDENTITY = {"unit_id": "u1", "unit_kind": "task", "invocation_seq": 1, "phase": "development", "attempt_number": 1, "role": "developer"}


def state_dir() -> str:
    return resolve_state_directory(AppConfig())[0]


def write_store(run_id: str, *, capture: str = model.CAPTURE_FULL, seal: bool = True, records: int = 3, gaps: int = 0) -> str:
    """A store as the recorder writes it: ``records`` model.request records each carrying one blob (full capture)."""
    writer = AttemptEvidenceWriter(state_dir(), run_id, capture=capture, manifest={"workspace_name": "fixture", "goal_digest": "sha256:" + "0" * 64})
    for index in range(records):
        writer.append("model.request", {"n": index}, identity=IDENTITY, content={"messages": f"prompt number {index}"})
    for _ in range(gaps):
        writer.gap("model.response", "fixture gap", identity=IDENTITY)
    if seal:
        assert writer.seal("run_closed")
    return run_directory(state_dir(), run_id)


def rewrite_record_line(directory: str, line_index: int, mutate) -> None:
    """Edit one stored record line (tamper): ``mutate(record_dict) -> bytes`` returns the new line bytes."""
    path = os.path.join(directory, "records.jsonl")
    with open(path, "rb") as handle:
        lines = handle.read().split(b"\n")
    lines[line_index] = mutate(json.loads(lines[line_index]))
    with open(path, "wb") as handle:
        handle.write(b"\n".join(lines))


def seed_trace_run(run_id: str, store_event_details: Optional[Dict[str, Any]], *, with_event: bool = True) -> None:
    """One trace row whose run_events optionally carries the ``evidence.attempt_store`` event (workflow.py ~2752)."""
    events: List[Dict[str, Any]] = [{"kind": "egress.authority", "attempt": 0, "source": "workflow", "authority": "auxiliary", "message": "x",
                                     "failure_type": None, "operation": None, "details": {}, "created_at": 1.0}]
    if with_event:
        events.append({"kind": "evidence.attempt_store", "attempt": 0, "source": "workflow.run_generation_workflow", "authority": "auxiliary",
                       "message": "attempt evidence store", "failure_type": None, "operation": None, "details": store_event_details or {}, "created_at": 2.0})
    TraceLogger(trace_db_path(AppConfig())).log_run(run_id=run_id, goal="g", duration_sec=1.0, attempts=1, status="SUCCESS",
                                                    files_modified=[], run_events=events)


def acquire_snapshot_id(cfg=None) -> str:
    env = run_kup_operation(cfg or AppConfig(), {"operation": "snapshot.acquire"})
    assert env["error"] is None, env
    return env["data"]["snapshot_id"]


def rich_store(run_id: str, *, seal: bool = True, keep_open: bool = False, phase_events: bool = False):
    """A store with several units, attempts, roles and kinds (15 records, or 17 with explicit phase events). Returns (directory, writer):
    the writer is sealed unless ``keep_open`` (an active-looking store the test can keep appending to)."""
    writer = AttemptEvidenceWriter(state_dir(), run_id, capture=model.CAPTURE_FULL, manifest={"workspace_name": "fixture"})
    attempt = {"unit_id": "u1", "unit_kind": "task", "invocation_seq": 1, "phase": "attempt"}
    other = {"unit_id": "u2", "unit_kind": "task", "invocation_seq": 1, "phase": "attempt"}
    writer.append("run.opened", {"goal_digest": "sha256:" + "1" * 64})
    writer.append("model.request", {"n": 1}, identity={"phase": "planning", "role": "planner", "call_seq": 1}, content={"messages": "plan please"})
    writer.append("model.response", {"n": 1}, identity={"phase": "planning", "role": "planner", "call_seq": 1}, content={"wire_body": "plan"})
    writer.append("unit.opened", {"unit_id": "u1"}, identity={"unit_id": "u1", "unit_kind": "task", "invocation_seq": 1})
    if phase_events:
        writer.append("phase.opened", {"phase_id": "development"}, identity={"unit_id": "u1"})
    writer.append("attempt.opened", {"attempt": 1}, identity={**attempt, "attempt_number": 1})
    writer.append("model.request", {"n": 2}, identity={**attempt, "attempt_number": 1, "role": "developer", "call_seq": 2}, content={"messages": "write code"})
    writer.append("model.response", {"n": 2}, identity={**attempt, "attempt_number": 1, "role": "developer", "call_seq": 2}, content={"wire_body": "code"})
    writer.append("gate.result", {"gate": "compile", "success": False}, identity={**attempt, "attempt_number": 1}, content={"output": "boom"})
    if phase_events:
        writer.append("phase.closed", {"phase_id": "development"}, identity={"unit_id": "u1"})
    writer.append("attempt.opened", {"attempt": 2}, identity={**attempt, "attempt_number": 2})
    writer.append("gate.result", {"gate": "compile", "success": True}, identity={**attempt, "attempt_number": 2})
    writer.append("unit.closed", {"outcome": "SUCCESS"}, identity={"unit_id": "u1", "unit_kind": "task", "invocation_seq": 1})
    writer.append("unit.opened", {"unit_id": "u2"}, identity={"unit_id": "u2", "unit_kind": "task", "invocation_seq": 1})
    writer.append("model.request", {"n": 3}, identity={**other, "attempt_number": 1, "role": "reviewer", "call_seq": 3}, content={"messages": "review"})
    writer.append("model.request", {"n": 4}, identity={"phase": "custom_role", "role": "custom_role", "call_seq": 4})
    writer.append("run.closed", {"outcome": "SUCCESS"})
    if seal and not keep_open:
        assert writer.seal("run_closed")
    return run_directory(state_dir(), run_id), writer

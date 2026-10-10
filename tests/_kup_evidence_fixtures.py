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

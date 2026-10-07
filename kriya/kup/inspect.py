"""Write-free inspection of a published snapshot (D-4 as written) and the KUP envelopes.

Every query here opens ONLY ``<snapshot>/traces.snapshot.db`` through ``store.connect_read_only`` (``mode=ro``,
``temp_store=MEMORY``, no other pragma). Stored values are returned verbatim: timestamps exactly as stored (no
timezone), ``files_modified`` as the raw comma-joined string, JSON TEXT columns as text. ``prompt_rendered`` is
returned only by ``history.prompt`` (explicit request), never by list or detail.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import re
import sqlite3
from typing import Any, Dict, Optional, Tuple

from kriya.build_info import version_report
from kriya.kup import policy
from kriya.kup.store import Snapshot, SnapshotError, connect_read_only, snapshot_summary, stat_metadata

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
CURSOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+/=_-]*$")

LIST_COLUMNS = ("run_id", "timestamp", "goal", "duration_sec", "attempts", "status", "failure_category", "files_modified",
                "milestone_group_id", "milestone_index", "milestone_total")
DETAIL_TEXT_COLUMNS = ("retrieved_chunks", "active_skills", "gate_outcomes", "model_hops", "run_events", "evidence_records",
                       "generation_metrics", "failure_report")
JSON_TEXT_COLUMNS = ("retrieved_chunks", "gate_outcomes", "model_hops", "run_events", "evidence_records", "generation_metrics", "failure_report")


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


# ------------------------------------------------------------------ envelopes

def envelope(operation: str, request_id: str, *, data: Any = None, error: Optional[Dict[str, Any]] = None,
             source: Optional[Dict[str, Any]] = None, consistency: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "schema_version": policy.KUP_SCHEMA_VERSION,
        "operation": operation,
        "request_id": request_id,
        "observed_at": utc_now(),
        "source": None if error else source,
        "consistency": None if error else consistency,
        "data": None if error else data,
        "error": error,
    }


def error_envelope(operation: str, request_id: str, code: str, message: str, database_state: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message, "database_state": database_state}
    err.update(extra)
    return envelope(operation, request_id, error=err)


def from_snapshot_error(operation: str, request_id: str, error: SnapshotError) -> Dict[str, Any]:
    return error_envelope(operation, request_id, error.code, error.message, error.database_state, **error.extra)


def source_block(state_dir: str, trace_db: str, snapshot: Optional[Snapshot] = None) -> Dict[str, Any]:
    block: Dict[str, Any] = {"state_directory": state_dir, "trace_database": trace_db}
    if snapshot is not None:
        block["snapshot_directory"] = snapshot.directory
        block["snapshot_id"] = snapshot.snapshot_id
    return block


def snapshot_consistency(snapshot: Snapshot, source_path: str) -> Dict[str, Any]:
    """gate C-3: one committed image via SQLite backup; acquisition start/completion recorded; source metadata
    compared by stat only - a difference supports 'source metadata change detected', equality establishes nothing."""
    summary = snapshot_summary(snapshot, stat_metadata(source_path))
    return {
        "kind": policy.CONSISTENCY_SNAPSHOT_COPY,
        "live_stream": False,
        "snapshot_id": snapshot.snapshot_id,
        "acquisition_started_at": summary["acquisition_started_at"],
        "acquisition_completed_at": summary["acquisition_completed_at"],
        "source_metadata_at_acquisition": summary["source_metadata_at_acquisition"],
        "source_metadata_now": summary["source_metadata_now"],
        "source_metadata_changed": summary["source_metadata_changed"],
    }


def live_consistency() -> Dict[str, Any]:
    return {"kind": policy.CONSISTENCY_LIVE_OBSERVATION, "live_stream": False}


def not_applicable_consistency() -> Dict[str, Any]:
    return {"kind": policy.CONSISTENCY_NOT_APPLICABLE, "live_stream": False}


def encode_response(env: Dict[str, Any]) -> str:
    """Serialize; an envelope above the response limit becomes a typed RESPONSE_TOO_LARGE error, never a partial body."""
    text = json.dumps(env, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(text.encode("utf-8")) > policy.MAX_RESPONSE_BYTES:
        replacement = error_envelope(env.get("operation", "unknown"), env.get("request_id", ""), policy.RESPONSE_TOO_LARGE,
                                     f"the response would exceed {policy.MAX_RESPONSE_BYTES} bytes", bytes=len(text.encode("utf-8")))
        return json.dumps(replacement, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return text


# ------------------------------------------------------------------ capabilities

def capabilities() -> Dict[str, Any]:
    report = version_report()
    return {
        "kup_versions": [policy.KUP_SCHEMA_VERSION],
        "operations": list(policy.OPERATIONS),
        "identity": {"kriya_version": report.get("version"), "commit": report.get("commit"), "build_provenance": report.get("build_provenance"),
                     "implementation": policy.PROTOCOL_IMPLEMENTATION, "sqlite_version": sqlite3.sqlite_version},
        "limits": dict(policy.LIMITS),
        "features": {"snapshot": True, "prompt": True, "comparisons": False, "attribution": False, "diagnostics": False, "output": False,
                     "consistency_kinds": list(policy.CONSISTENCY_KINDS), "error_codes": list(policy.ERROR_CODES), "database_states": list(policy.DATABASE_STATES)},
    }


# ------------------------------------------------------------------ cursors (opaque, bound to a snapshot)

def encode_cursor(snapshot_id: str, timestamp: Optional[str], run_id: str) -> str:
    raw = json.dumps([snapshot_id, timestamp, run_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, snapshot_id: str) -> Tuple[Optional[str], str]:
    if not isinstance(cursor, str) or not cursor or len(cursor) > policy.CURSOR_MAX or not CURSOR_RE.match(cursor):
        raise SnapshotError(policy.INVALID_REQUEST, "cursor is not an opaque token issued by this protocol")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        parts = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        bound, timestamp, run_id = parts
    except (ValueError, TypeError) as error:
        raise SnapshotError(policy.INVALID_REQUEST, "cursor does not decode") from error
    if bound != snapshot_id:
        raise SnapshotError(policy.INVALID_REQUEST, "cursor was issued for another snapshot; a snapshot switch invalidates cursors", cursor_snapshot_id=bound)
    if not isinstance(run_id, str) or (timestamp is not None and not isinstance(timestamp, str)):
        raise SnapshotError(policy.INVALID_REQUEST, "cursor payload has an unexpected shape")
    return timestamp, run_id


def validate_run_id(run_id: Any) -> str:
    if not isinstance(run_id, str) or not run_id or len(run_id) > policy.RUN_ID_MAX or not RUN_ID_RE.match(run_id):
        raise SnapshotError(policy.INVALID_REQUEST, "run_id must match [A-Za-z0-9][A-Za-z0-9._:-]* and be at most 256 characters")
    return run_id


# ------------------------------------------------------------------ queries

def _classify_read_error(error: sqlite3.Error, snapshot: Snapshot) -> SnapshotError:
    name = getattr(error, "sqlite_errorname", "") or type(error).__name__
    if name in ("SQLITE_NOTADB", "SQLITE_CORRUPT"):
        return SnapshotError(policy.SNAPSHOT_CORRUPT, f"{name}: {error}", snapshot_id=snapshot.snapshot_id)
    if name in ("SQLITE_CANTOPEN",):
        return SnapshotError(policy.SNAPSHOT_UNAVAILABLE, f"snapshot file cannot be opened ({error})", snapshot_id=snapshot.snapshot_id)
    return SnapshotError(policy.READ_ONLY_UNAVAILABLE, f"{name}: {error}", policy.DB_STATE_UNREADABLE, snapshot_id=snapshot.snapshot_id)


def _row_to_summary(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in LIST_COLUMNS}


def history_list(snapshot: Snapshot, limit: Optional[int], cursor: Optional[str]) -> Dict[str, Any]:
    n = policy.LIST_DEFAULT if limit is None else int(limit)
    if n < 1 or n > policy.LIST_MAX:
        raise SnapshotError(policy.INVALID_REQUEST, f"limit must be between 1 and {policy.LIST_MAX}")
    after: Optional[Tuple[Optional[str], str]] = decode_cursor(cursor, snapshot.snapshot_id) if cursor is not None else None
    try:
        conn = connect_read_only(snapshot.db_path)
    except sqlite3.Error as error:
        raise _classify_read_error(error, snapshot) from error
    try:
        conn.row_factory = sqlite3.Row
        cols = ", ".join(LIST_COLUMNS)
        if after is None:
            rows = conn.execute(f"SELECT {cols} FROM runs ORDER BY timestamp DESC, run_id DESC LIMIT ?", (n + 1,)).fetchall()
        else:
            ts, rid = after
            if ts is None:
                rows = conn.execute(f"SELECT {cols} FROM runs WHERE timestamp IS NULL AND run_id < ? ORDER BY timestamp DESC, run_id DESC LIMIT ?", (rid, n + 1)).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {cols} FROM runs WHERE (timestamp < ?) OR (timestamp = ? AND run_id < ?) OR (timestamp IS NULL) "
                    "ORDER BY timestamp DESC, run_id DESC LIMIT ?", (ts, ts, rid, n + 1)).fetchall()
    except sqlite3.Error as error:
        raise _classify_read_error(error, snapshot) from error
    finally:
        conn.close()
    page = [_row_to_summary(r) for r in rows[:n]]
    next_cursor = encode_cursor(snapshot.snapshot_id, page[-1]["timestamp"], page[-1]["run_id"]) if len(rows) > n and page else None
    return {"runs": page, "next_cursor": next_cursor}


def _section(data: Any, provenance: str, available: bool = True, reason: Optional[str] = None) -> Dict[str, Any]:
    if available:
        return {"availability": "recorded", "provenance": provenance, "reason": None, "data": data}
    return {"availability": "not_recorded", "provenance": provenance, "reason": reason, "data": None}


def _parsed_json_text(text: Optional[str]) -> Tuple[Any, bool]:
    if text is None:
        return None, False
    try:
        return json.loads(text), True
    except (TypeError, ValueError):
        return text, True


def history_detail(snapshot: Snapshot, run_id: str) -> Dict[str, Any]:
    validate_run_id(run_id)
    try:
        conn = connect_read_only(snapshot.db_path)
        conn.row_factory = sqlite3.Row
        cols = ", ".join(LIST_COLUMNS + DETAIL_TEXT_COLUMNS)
        row = conn.execute(f"SELECT {cols} FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        conn.close()
    except sqlite3.Error as error:
        raise _classify_read_error(error, snapshot) from error
    if row is None:
        raise SnapshotError(policy.INVALID_REQUEST, f"no run {run_id} in snapshot {snapshot.snapshot_id}", run_id=run_id)
    summary = _row_to_summary(row)
    fields = {}
    for col in DETAIL_TEXT_COLUMNS:
        raw = row[col]
        fields[col] = {"availability": "recorded" if raw is not None else "not_recorded", "provenance": f"runs.{col}",
                       "reason": None if raw is not None else "column is NULL in the stored row", "data": raw}
    detail: Dict[str, Any] = {"run": summary, "fields": fields}
    for col in JSON_TEXT_COLUMNS:
        parsed, present = _parsed_json_text(row[col])
        detail[col] = _section(parsed, f"runs.{col}", present, None if present else "column is NULL in the stored row")
    # Not persisted by this Kriya version (P-30, D-5): honest, never reconstructed.
    for name, reason in (("context", "the trace row persists context only inside run_events (context.* events); no context record is stored"),
                         ("attribution", "the baseline persists failure categories, not causal attribution"),
                         ("diagnostics", "no diagnostics record is persisted"),
                         ("comparisons", "the trace row stores modified paths, not before/after text"),
                         ("output", "Developer output is not persisted")):
        detail[name] = _section(None, "not_persisted", False, reason)
    return detail


def history_prompt(snapshot: Snapshot, run_id: str) -> Dict[str, Any]:
    validate_run_id(run_id)
    try:
        conn = connect_read_only(snapshot.db_path)
        row = conn.execute("SELECT prompt_rendered FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        conn.close()
    except sqlite3.Error as error:
        raise _classify_read_error(error, snapshot) from error
    if row is None:
        raise SnapshotError(policy.INVALID_REQUEST, f"no run {run_id} in snapshot {snapshot.snapshot_id}", run_id=run_id)
    return {"prompt_rendered": row[0], "role": "planner", "scope": "plan_prompt (one stored prompt per run; not every Developer request)"}

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
from kriya.core.attempt_evidence import model as evidence_model
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

def capabilities(cfg=None) -> Dict[str, Any]:
    report = version_report()
    return {
        "kup_versions": [policy.KUP_SCHEMA_VERSION],
        "operations": list(policy.OPERATIONS),
        "identity": {"kriya_version": report.get("version"), "commit": report.get("commit"), "build_provenance": report.get("build_provenance"),
                     "implementation": policy.PROTOCOL_IMPLEMENTATION, "sqlite_version": sqlite3.sqlite_version},
        "limits": dict(policy.LIMITS),
        "features": {"snapshot": True, "prompt": True, "comparisons": False, "attribution": False, "diagnostics": False, "output": False,
                     "consistency_kinds": list(policy.CONSISTENCY_KINDS), "error_codes": list(policy.ERROR_CODES), "database_states": list(policy.DATABASE_STATES),
                     **_cockpit_features(cfg)},
    }


def _cockpit_features(cfg=None) -> Dict[str, Any]:
    """Capability set 1.1 flags: true only for what this build implements (a flag is never advertised before its operation exists)."""
    # Kriya's own SECURITY_AUTHORITY decision, never a host's: no configuration, or anything but an explicit `on`, is off.
    content_authorized = getattr(getattr(getattr(cfg, "evidence", None), "attempt_recorder", None), "content_visibility", "off") == "on"
    return {
        "capability_set": policy.CAPABILITY_SET,
        "evidence": True, "evidence_content": content_authorized, "evidence_content_classes": sorted(set(policy.BLOB_CLASSES.values())) if content_authorized else [],
        "qualification": False, "authority": False,
        "knowledge": False, "tools": False, "experiments": False, "live": False, "actions": False,
        "workspace_create": True, "skills_view": True, "tools_view": True, "model_probe": True, "skills_catalog": True, "tools_catalog": True,
        "evidence_capture_modes": list(evidence_model.CAPTURE_MODES),
        "evidence_verification_states": list(policy.EVIDENCE_VERIFICATION_STATES),
        "evidence_kinds": sorted(evidence_model.KINDS),
        "evidence_provenances": sorted(evidence_model.PROVENANCES),
        "diagnosis_evidence_classes": list(evidence_model.EVIDENCE_CLASSES),
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


def _has_enforce_column(conn: sqlite3.Connection) -> bool:
    return any(info[1] == "enforce_run_id" for info in conn.execute("PRAGMA table_info(runs)"))


def _display_goal(conn: sqlite3.Connection, row: sqlite3.Row, has_enforce: bool, cache: Dict[str, Optional[str]]) -> Tuple[Optional[str], str]:
    """The ORIGINAL user request for a row, from structured fields only (never by reading prompt text):

    * a row that names an enforce row through ``enforce_run_id`` (a unit of a run) takes that row's ``goal``, which the workflow stores as the original request;
    * a row with no such link (the terminal ``<run>.enforce`` row, a plain run) takes its own ``goal``, unless that goal is a compiled unit goal (it starts with
      the compiled-goal header) - then there is no authoritative request to show and the answer is "unavailable", never a guess."""
    enforce_id = row["enforce_run_id"] if has_enforce else None
    if enforce_id:
        if enforce_id not in cache:
            found = conn.execute("SELECT goal FROM runs WHERE run_id = ?", (enforce_id,)).fetchone()
            cache[enforce_id] = found[0] if found and isinstance(found[0], str) and found[0].strip() else None
        goal = cache[enforce_id]
        if goal is not None and not goal.lstrip().startswith(policy.COMPILED_GOAL_HEADER):
            return goal, policy.DISPLAY_GOAL_ENFORCE_ROW
        return None, policy.DISPLAY_GOAL_UNAVAILABLE
    own = row["goal"]
    if isinstance(own, str) and own.strip() and not own.lstrip().startswith(policy.COMPILED_GOAL_HEADER):
        return own, policy.DISPLAY_GOAL_OWN
    return None, policy.DISPLAY_GOAL_UNAVAILABLE


def _row_to_summary(row: sqlite3.Row, display: Optional[Tuple[Optional[str], str]] = None) -> Dict[str, Any]:
    summary = {k: row[k] for k in LIST_COLUMNS}
    if display is not None:
        summary["display_goal"], summary["display_goal_source"] = display
    return summary


def _select_columns(has_enforce: bool, extra: Tuple[str, ...] = ()) -> str:
    return ", ".join(LIST_COLUMNS + extra + (("enforce_run_id",) if has_enforce else ()))


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
        has_enforce = _has_enforce_column(conn)
        cols = _select_columns(has_enforce)
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
        goals: Dict[str, Optional[str]] = {}
        page = [_row_to_summary(r, _display_goal(conn, r, has_enforce, goals)) for r in rows[:n]]
    except sqlite3.Error as error:
        raise _classify_read_error(error, snapshot) from error
    finally:
        conn.close()
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
        has_enforce = _has_enforce_column(conn)
        cols = _select_columns(has_enforce, DETAIL_TEXT_COLUMNS)
        row = conn.execute(f"SELECT {cols} FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        display = _display_goal(conn, row, has_enforce, {}) if row is not None else None
        conn.close()
    except sqlite3.Error as error:
        raise _classify_read_error(error, snapshot) from error
    if row is None:
        raise SnapshotError(policy.INVALID_REQUEST, f"no run {run_id} in snapshot {snapshot.snapshot_id}", run_id=run_id)
    summary = _row_to_summary(row, display)
    fields = {}
    for col in DETAIL_TEXT_COLUMNS:
        raw = row[col]
        fields[col] = {"availability": "recorded" if raw is not None else "not_recorded", "provenance": f"runs.{col}",
                       "reason": None if raw is not None else "column is NULL in the stored row", "data": raw}
    detail: Dict[str, Any] = {"run": summary, "fields": fields}
    for col in JSON_TEXT_COLUMNS:
        parsed, present = _parsed_json_text(row[col])
        detail[col] = _section(parsed, f"runs.{col}", present, None if present else "column is NULL in the stored row")
    # PRODUCT-UX-COMPLETION: a read-only projection of the recorded runtime-gate execution (never a guess; see kriya/kup/application_outcome.py).
    from kriya.kup.application_outcome import application_outcome

    gates = detail["gate_outcomes"]
    detail["application_outcome"] = _section(application_outcome(gates["data"] if gates["availability"] == "recorded" else None), "derived:runs.gate_outcomes")
    # Not persisted by this Kriya version (P-30, D-5): honest, never reconstructed.
    for name, reason in (("context", "the trace row persists context only inside run_events (context.* events); no context record is stored"),
                         ("attribution", "the baseline persists failure categories, not causal attribution"),
                         ("diagnostics", "no diagnostics record is persisted"),
                         ("comparisons", "the trace row stores modified paths, not before/after text"),
                         ("output", "Developer output is not persisted")):
        detail[name] = _section(None, "not_persisted", False, reason)
    return detail


def run_events_of(snapshot: Snapshot, run_id: str) -> Tuple[bool, Optional[Any]]:
    """(row_exists, parsed ``runs.run_events`` or None when the column is NULL or not JSON), verbatim and read-only. Nothing else
    of the row is read: callers that need the events alone (evidence.manifest's absence derivation) never touch the prompt."""
    validate_run_id(run_id)
    try:
        conn = connect_read_only(snapshot.db_path)
        try:
            row = conn.execute("SELECT run_events FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as error:
        raise _classify_read_error(error, snapshot) from error
    if row is None:
        return False, None
    parsed, present = _parsed_json_text(row[0])
    return True, parsed if present and not isinstance(parsed, str) else None


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

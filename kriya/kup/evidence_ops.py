"""KUP evidence operations (Run Cockpit v1.1 slice 1A-1): ``evidence.manifest`` and ``evidence.verify``.

The only reader of the attempt-evidence store for the UI (04 section 1 rule 1): everything goes through
``kriya.core.attempt_evidence.reader`` (invariant I-1); no store file is opened or named here. Strict read rules (04 section 5): files are opened read-only, nothing is created,
renamed, repaired or locked, a run id is validated before any lookup, dot-prefixed (retention staging) names are
unreachable, and a store that cannot be read is a typed error, never a partial body. The absence reason is DERIVED
from the S1 ``evidence.attempt_store`` run event and says so.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from kriya.core.attempt_evidence import model as evidence_model
from kriya.core.attempt_evidence import reader
from kriya.core.state_paths import resolve_state_directory
from kriya.kup import policy
from kriya.kup.inspect import (
    CURSOR_RE,
    envelope,
    live_consistency,
    run_events_of,
    source_block,
    utc_now,
    validate_run_id,
)
from kriya.kup.store import SnapshotError, require_snapshot, snapshot_directory, trace_store_path

ABSENCE_DERIVED_FROM = "history.detail run_events[evidence.attempt_store]"


def _state_dir(cfg) -> str:
    return resolve_state_directory(cfg)[0]


def _source(cfg, snapshot=None) -> Dict[str, Any]:
    state_dir = _state_dir(cfg)
    block = source_block(state_dir, trace_store_path(cfg), snapshot)
    block["evidence_directory"] = reader.store_directory(state_dir)
    return block


def _store_run(cfg, run_id: Any) -> reader.EvidenceRun:
    """The reader handle for a validated run id. ``validate_run_id`` (first character alphanumeric, no separators) is the whole
    path-safety rule: a leading ``.`` (retention staging) or any traversal is refused before a path is built."""
    validate_run_id(run_id)
    return reader.open_run(_state_dir(cfg), run_id)


def _guard(path_kind: str, call):
    """Run one reader call, mapping its failures to the typed vocabulary (no broad catch: anything else is a coding error)."""
    try:
        return call()
    except reader.UnsupportedEvidenceSchema as error:
        raise SnapshotError(policy.EVIDENCE_SCHEMA_UNSUPPORTED, str(error), schema_seen=error.schema_seen) from error
    except (OSError, ValueError, RecursionError) as error:
        raise SnapshotError(policy.EVIDENCE_STORE_UNREADABLE, f"{type(error).__name__}: {error}", path_kind=path_kind) from error


def _require_identity(run_id: Any, header: Any, records: Any = ()) -> None:
    """Evidence binds to its exact run identity: a store is served as run X only if its manifest and every record it yields name run X. A store directory copied or
    moved under another run's name is refused (typed), never served as that run."""
    stored = header.get("run_id") if isinstance(header, dict) else None
    if stored != run_id or any(isinstance(record, dict) and record.get("run_id") != run_id for record in records):
        raise SnapshotError(policy.EVIDENCE_STORE_UNREADABLE, f"the store found at run {run_id} is not that run's own (its manifest or records name another run); nothing is served from it",
                            path_kind="store", reason="run_identity_mismatch")


# ------------------------------------------------------------------ evidence.manifest

def manifest(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    """Store metadata for an evidence run id, or - with ``from_trace`` - for the store a TRACE row recorded. The two ids differ in every
    real store (MEASURED): a trace row has its own id and its ``evidence.attempt_store`` event names the evidence run id, so the link
    is that recorded event and is resolved here, on the reading side, never guessed by the host."""
    snapshot = require_snapshot(snapshot_directory(cfg), str(request["snapshot_id"])) if request.get("snapshot_id") else None
    link: Optional[Dict[str, Any]] = None
    if request.get("from_trace"):
        trace_id = request.get("run_id")
        validate_run_id(trace_id)
        if snapshot is None:
            raise SnapshotError(policy.INVALID_REQUEST, "from_trace needs the snapshot_id the trace row is read from")
        state, details = _recorded_store_event(snapshot, trace_id)
        run_id = _recorded_evidence_id(details) if state == "found" else None
        if state == "found":
            link = {"trace_run_id": trace_id, "evidence_run_id": run_id, "resolved_from": ABSENCE_DERIVED_FROM, "recorded_event": details}
        absence_inputs = (trace_id, state, details)
    else:
        run_id = request.get("run_id")
        validate_run_id(run_id)
        absence_inputs = None
    run = _store_run(cfg, run_id) if run_id is not None else None
    if run is None or not run.exists:
        if absence_inputs is None:
            absence_inputs = (run_id, *_recorded_store_event(snapshot, run_id)) if snapshot is not None else (run_id, "no_snapshot", None)
        data = {"run_id": run_id, "store": "absent", "manifest": None, "seal": None, "line_count": None, "bytes_on_disk": None,
                "blob_file_count": None, "absence": _absence(snapshot, run_id, *absence_inputs[1:], asked=absence_inputs[0]), "link": link}
    else:
        header = _guard("manifest", run.manifest)
        _require_identity(run_id, header)
        seal = _guard("seal", run.seal)
        line_count, bytes_on_disk, blob_files = _guard("records", run.measure)
        data = {"run_id": run_id, "store": "present", "manifest": header, "seal": seal, "line_count": line_count,
                "bytes_on_disk": bytes_on_disk, "blob_file_count": blob_files, "absence": None, "link": link}
    return envelope("evidence.manifest", request_id, data=data, source=_source(cfg, snapshot), consistency=live_consistency())


def store_summary(cfg, run_id: str) -> Dict[str, Any]:
    """{state: absent|open|sealed|unreadable, line_count, sealed} of a run's store, for ``launch.status``. Cheap (a line count, no verification):
    the cockpit reads the records themselves through evidence.records, which verifies."""
    run = _store_run(cfg, run_id)
    if not run.exists:
        return {"state": "absent", "line_count": None, "sealed": False}
    try:
        if run.manifest().get("run_id") != run_id:  # a store that is not this run's own is never summarized as it
            return {"state": "unreadable", "line_count": None, "sealed": False}
        sealed = run.seal() is not None
        lines = run.measure()[0]
    except (OSError, ValueError, reader.UnsupportedEvidenceSchema):
        return {"state": "unreadable", "line_count": None, "sealed": False}
    return {"state": "sealed" if sealed else "open", "line_count": lines, "sealed": sealed}


def recorded_workspace_identity(cfg, evidence_id: Optional[str]) -> Tuple[str, Optional[str]]:
    """(state, identity) of the workspace an evidence manifest records: state is ``recorded`` (identity is the manifest's own
    ``workspace_identity``) or ``unattributed`` - no id, no store, an unsupported schema, an unreadable manifest or one that names no workspace.
    Read-only; the only workspace-scope reader of a store, so the reader stays confined to this module (design 9.1)."""
    if evidence_id is None:
        return "unattributed", None
    run = reader.open_run(_state_dir(cfg), evidence_id)
    try:
        if not run.exists:
            return "unattributed", None
        header = run.manifest()
        if header.get("run_id") != evidence_id:  # a store copied under another run's name attributes nothing to anyone
            return "unattributed", None
        recorded = header.get("workspace_identity")
    except (OSError, ValueError, reader.UnsupportedEvidenceSchema):
        return "unattributed", None
    return ("recorded", recorded) if isinstance(recorded, str) and recorded else ("unattributed", None)


def _recorded_evidence_id(details: Any) -> Optional[str]:
    """The evidence run id a recorded event names, only when it is a safe run id."""
    value = details.get("run_id") if isinstance(details, dict) else None
    try:
        return validate_run_id(value) if isinstance(value, str) else None
    except SnapshotError:
        return None


def _recorded_store_event(snapshot, run_id: Any) -> Tuple[str, Any]:
    """(state, details) of the LAST ``evidence.attempt_store`` event of a trace row: state is no_row, no_events, no_event or found."""
    exists, events = run_events_of(snapshot, run_id)
    if not exists:
        return "no_row", None
    if events is None:
        return "no_events", None
    details, found = None, False
    for event in events if isinstance(events, list) else []:
        if isinstance(event, dict) and event.get("kind") == "evidence.attempt_store":
            found, details = True, event.get("details")
    return ("found", details) if found else ("no_event", None)


def _absence(snapshot, evidence_id: Any, state: str, details: Any, asked: Any) -> Dict[str, Any]:
    """DERIVED (03 matrix row 27): the reason a store is absent, from the recorded ``evidence.attempt_store`` event of the trace row.
    Only as good as that event. ``asked`` is the id the row was read under; an event that names a DIFFERENT evidence id says nothing about
    the id asked about (never a derived "pruned" for another store), and the answer says how to resolve it."""
    base: Dict[str, Any] = {"derived_from": ABSENCE_DERIVED_FROM, "snapshot_id": snapshot.snapshot_id if snapshot else None, "recorded_event": None}
    if snapshot is None or state == "no_snapshot":
        return {**base, "reason": "unknown", "detail": "no snapshot_id was supplied, so the run's recorded evidence.attempt_store event cannot be read"}
    if state == "no_row":
        return {**base, "reason": "unknown", "detail": f"run {asked} is not in snapshot {snapshot.snapshot_id}"}
    if state == "no_events":
        return {**base, "reason": "unknown", "detail": "the run's run_events column is NULL or is not JSON"}
    if state == "no_event":
        return {**base, "reason": "pre_recorder", "detail": "the run recorded no evidence.attempt_store event (written before the attempt-evidence recorder)"}
    base["recorded_event"] = details
    recorded_id = _recorded_evidence_id(details)
    if recorded_id is None and isinstance(details, dict) and isinstance(details.get("run_id"), str):
        return {**base, "reason": "unknown", "detail": "the recorded event names an evidence run id that is not a usable run id; no store is read for it"}
    if recorded_id is not None and evidence_id is not None and recorded_id != evidence_id:
        return {**base, "reason": "unknown", "detail": f"the trace row {asked} recorded a store under another evidence run id ({recorded_id}); ask with from_trace to resolve it"}
    status = details.get("status") if isinstance(details, dict) else None
    if status == "DISABLED":
        return {**base, "reason": "capture_off", "detail": "the recorded event reports status DISABLED (capture off or no evidence configuration)"}
    if status == "RECORDER_UNAVAILABLE":
        return {**base, "reason": "recorder_unavailable", "detail": f"the recorder was unavailable: {details.get('reason')}"}
    if status in ("OPEN", "CLOSED") and isinstance(details, dict) and details.get("path"):
        return {**base, "reason": "pruned", "detail": f"the run recorded a store at {details['path']} (status {status}) and the store is not on disk now"}
    return {**base, "reason": "unknown", "detail": f"the recorded event reports status {status!r}, which does not establish why the store is absent"}


# ------------------------------------------------------------------ evidence.verify

def verify(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    run_id = request.get("run_id")
    run = _store_run(cfg, run_id)
    chain: List[Dict[str, Any]] = []
    result = _guard("store", lambda: run.verify(chain))
    if result.status != reader.NOT_FOUND:
        _require_identity(run_id, _guard("manifest", run.manifest), chain)
    data = {"run_id": result.run_id, "status": result.status, "record_count": result.record_count, "sealed": result.sealed,
            "broken_seq": result.broken_seq, "detail": result.detail, "seal": result.seal, "blob_problems": list(result.blob_problems),
            "head_digest": result.head_digest, "verified_at": utc_now()}
    if result.status == reader.VERIFIED:
        consistency: Dict[str, Any] = {"kind": policy.CONSISTENCY_SEALED_STORE, "live_stream": False, "head_digest": result.head_digest}
    else:
        consistency = live_consistency()
    return envelope("evidence.verify", request_id, data=data, source=_source(cfg), consistency=consistency)



# ------------------------------------------------------------------ evidence.records

_FILTER_STRINGS = ("unit", "phase", "role")


def _int_param(request: Dict[str, Any], name: str, minimum: int, maximum: Optional[int] = None) -> Optional[int]:
    value = request.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or (maximum is not None and value > maximum):
        bound = f"between {minimum} and {maximum}" if maximum is not None else f">= {minimum}"
        raise SnapshotError(policy.INVALID_REQUEST, f"{name} must be an integer {bound}")
    return value


def _records_filters(request: Dict[str, Any]) -> Dict[str, Any]:
    """The validated, applied filters (non-null only). Everything is checked before any I/O."""
    filters: Dict[str, Any] = {}
    for name in _FILTER_STRINGS:
        value = request.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > policy.EVIDENCE_FILTER_VALUE_MAX:
            raise SnapshotError(policy.INVALID_REQUEST, f"{name} must be a non-empty string of at most {policy.EVIDENCE_FILTER_VALUE_MAX} characters")
        filters[name] = value
    attempt = _int_param(request, "attempt", 0)
    if attempt is not None:
        filters["attempt"] = attempt
    from_seq = _int_param(request, "from_seq", 1)
    if from_seq is not None:
        filters["from_seq"] = from_seq
    kinds = request.get("kind")
    if kinds is not None:
        if not isinstance(kinds, list) or not kinds or any(not isinstance(k, str) or k not in evidence_model.KINDS for k in kinds):
            raise SnapshotError(policy.INVALID_REQUEST, "kind must be a non-empty list of record kinds the evidence schema defines")
        filters["kind"] = sorted(set(kinds))
    return filters


def _fingerprint(filters: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(filters, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _encode_cursor(run_id: str, head: str, seq: int, fingerprint: str) -> str:
    raw = json.dumps([run_id, head, seq, fingerprint], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: Any, run_id: str, fingerprint: str) -> Tuple[str, int]:
    """(head_digest, last_seq) of a cursor issued by this operation for this run and these filters."""
    if not isinstance(cursor, str) or not cursor or len(cursor) > policy.CURSOR_MAX or not CURSOR_RE.match(cursor):
        raise SnapshotError(policy.INVALID_REQUEST, "cursor is not an opaque token issued by this protocol")
    try:
        parts = json.loads(base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode("ascii")).decode("utf-8"))
        bound_run, head, seq, bound_filters = parts
    except (ValueError, TypeError) as error:
        raise SnapshotError(policy.INVALID_REQUEST, "cursor does not decode") from error
    if not (isinstance(head, str) and isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0):
        raise SnapshotError(policy.INVALID_REQUEST, "cursor payload has an unexpected shape")
    if bound_run != run_id or bound_filters != fingerprint:
        raise SnapshotError(policy.INVALID_REQUEST, "cursor was issued for another run or other filters; changing either invalidates it")
    return head, seq


def _matches(record: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    return ((("unit" not in filters or record.get("unit_id") == filters["unit"])
             and ("phase" not in filters or record.get("phase") == filters["phase"])
             and ("role" not in filters or record.get("role") == filters["role"])
             and ("attempt" not in filters or record.get("attempt_number") == filters["attempt"]))
            and ("kind" not in filters or record.get("kind") in filters["kind"]))


def records(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    run_id = request.get("run_id")
    validate_run_id(run_id)
    limit = _int_param(request, "limit", 1, policy.EVIDENCE_PAGE_MAX) or policy.EVIDENCE_PAGE_DEFAULT
    filters = _records_filters(request)
    fingerprint = _fingerprint(filters)
    cursor = request.get("cursor")
    cursor_head, after_seq = _decode_cursor(cursor, run_id, fingerprint) if cursor is not None else (None, 0)
    run, chain, result, live = _verified_chain(cfg, run_id)
    head = result.head_digest
    if cursor_head is not None and cursor_head != head:
        raise SnapshotError(policy.EVIDENCE_HEAD_CHANGED, "the store's chain head moved since the cursor was issued; start again",
                            head_then=cursor_head, head_now=head)
    page, truncated, more = _page(chain, filters, limit, max(filters.get("from_seq", 1), after_seq + 1))
    next_cursor = _encode_cursor(run_id, head, page[-1]["seq"], fingerprint) if page and more else None
    data = {"run_id": run_id, "head_digest": head, "records": page, "next_cursor": next_cursor, "filters_applied": filters,
            "truncated": truncated, "truncated_tail": result.status == reader.TRUNCATED_TAIL}
    consistency = ({"kind": policy.CONSISTENCY_SEALED_STORE, "live_stream": False, "head_digest": head}
                   if result.status == reader.VERIFIED else live_consistency())
    return envelope("evidence.records", request_id, data=data, source=_source(cfg), consistency=consistency)


def _verified_chain(cfg, run_id: Any):
    """(run, records, verification, live): one pass verifying chain, blobs and seal and collecting the records. A store that is not
    VERIFIED (or an unsealed live store) serves nothing: EVIDENCE_VERIFY_FAILED / EVIDENCE_STORE_NOT_FOUND."""
    run = _store_run(cfg, run_id)
    if not run.exists:
        raise SnapshotError(policy.EVIDENCE_STORE_NOT_FOUND, f"no attempt-evidence store for run {run_id}")
    chain: List[Dict[str, Any]] = []
    result = _guard("store", lambda: run.verify(chain))
    if result.status != reader.NOT_FOUND:
        _require_identity(run_id, _guard("manifest", run.manifest), chain)
    live = result.status == reader.UNSEALED or (result.status == reader.TRUNCATED_TAIL and not result.sealed)
    if result.status == reader.NOT_FOUND:
        raise SnapshotError(policy.EVIDENCE_STORE_NOT_FOUND, f"no attempt-evidence store for run {run_id}")
    if result.status != reader.VERIFIED and not live:
        raise SnapshotError(policy.EVIDENCE_VERIFY_FAILED, f"the store did not verify ({result.status}); nothing is served from it",
                            status=result.status, broken_seq=result.broken_seq, detail=result.detail, blob_problems=list(result.blob_problems))
    return run, chain, result, live


def _page(chain: List[Dict[str, Any]], filters: Dict[str, Any], limit: int, first_seq: int) -> Tuple[List[Dict[str, Any]], bool, bool]:
    """(records served, stopped-by-byte-budget, more matching records follow) from the verified chain. ``chain[i]["seq"] == i + 1``
    holds for a verified chain, so the start is a slice, not a scan. The first record is always served, whatever its size."""
    ranges = reader.explicit_phase_ranges(chain)
    page: List[Dict[str, Any]] = []
    used = 0
    for record in chain[first_seq - 1:]:
        if not _matches(record, filters):
            continue
        if len(page) == limit:
            return page, False, True
        served = {**record, "phase_provenance": reader.phase_provenance(record, ranges)}
        cost = len(json.dumps(served, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        if page and used + cost > policy.EVIDENCE_PAGE_MAX_BYTES:
            return page, True, True
        page.append(served)
        used += cost
    return page, False, False


# ------------------------------------------------------------------ evidence.change_content

_PART_RE = re.compile(r"^[a-z_]{1,32}$")


def change_content(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    """ONE part of ONE recorded candidate change (class candidate_content), when Kriya authorizes content."""
    return _serve_part(cfg, request, request_id, operation="evidence.change_content", record_kind="candidate.change", served_class=policy.CONTENT_CLASS_CANDIDATE)


def call_content(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    """ONE part (messages, tools, wire body) of ONE recorded model request (class model_input), when Kriya authorizes content."""
    return _serve_part(cfg, request, request_id, operation="evidence.call_content", record_kind="model.request", served_class=policy.CONTENT_CLASS_MODEL_INPUT)


def _serve_part(cfg, request: Dict[str, Any], request_id: str, *, operation: str, record_kind: str, served_class: str) -> Dict[str, Any]:
    """Order is fail-closed: (1) parameters, (2) the content class of the requested key (a key of another class, or an unlisted key, is refused here)
    and Kriya's visibility setting - before any store access, so a refusal reveals nothing about a store, (3) store verification, (4) the record and
    what it kept, (5) the recorded size against the cap, (6) the bytes, re-hashed on read."""
    run_id = request.get("run_id")
    validate_run_id(run_id)
    seq, part = request.get("seq"), request.get("part")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise SnapshotError(policy.INVALID_REQUEST, "seq must be an integer >= 1")
    if not isinstance(part, str) or not _PART_RE.match(part):
        raise SnapshotError(policy.INVALID_REQUEST, "part must be a lowercase blob key name")
    content_class = policy.BLOB_CLASSES.get(part, policy.CONTENT_CLASS_UNCLASSIFIED)
    if content_class == policy.CONTENT_CLASS_UNCLASSIFIED:
        raise SnapshotError(policy.EVIDENCE_CONTENT_POLICY, f"{part!r} is not a classified content key; unclassified content is never served",
                            content_class=content_class, reason="unclassified_key")
    if content_class != served_class:
        raise SnapshotError(policy.EVIDENCE_CONTENT_POLICY, f"{part!r} is {content_class} content, which {operation} does not serve",
                            content_class=content_class, reason="class_not_served_here")
    visibility = getattr(getattr(getattr(cfg, "evidence", None), "attempt_recorder", None), "content_visibility", "off")
    if visibility != "on":
        raise SnapshotError(policy.EVIDENCE_CONTENT_POLICY, "Kriya's content visibility policy (evidence.attempt_recorder.content_visibility) is off",
                            content_class=content_class, reason="content_visibility_off")
    run, chain, result, _live = _verified_chain(cfg, run_id)
    record = chain[seq - 1] if seq <= len(chain) else None
    if record is None or record.get("kind") != record_kind:
        raise SnapshotError(policy.INVALID_REQUEST, f"record {seq} of run {run_id} is not a {record_kind} record")
    ref = (record.get("blobs") or {}).get(part)
    digest_entry = (record.get("content_digests") or {}).get(part)
    if ref is None:
        reason = "digest_only" if digest_entry is not None else "part_not_recorded"
        raise SnapshotError(policy.EVIDENCE_CONTENT_UNAVAILABLE, f"record {seq} kept no bytes for {part!r} ({reason})", reason=reason)
    recorded_bytes = digest_entry.get("bytes") if isinstance(digest_entry, dict) else None
    if isinstance(recorded_bytes, int) and recorded_bytes > policy.EVIDENCE_BLOB_MAX_BYTES:
        raise SnapshotError(policy.EVIDENCE_BLOB_TOO_LARGE, f"{part!r} is {recorded_bytes} bytes; the response cap is {policy.EVIDENCE_BLOB_MAX_BYTES}", bytes_total=recorded_bytes)
    try:
        data = run.blob(ref)
    except reader.BlobCorrupt as error:
        raise SnapshotError(policy.EVIDENCE_VERIFY_FAILED, str(error), status="BLOB_CORRUPT", ref=ref) from error
    except OSError as error:
        raise SnapshotError(policy.EVIDENCE_STORE_UNREADABLE, f"{type(error).__name__}: {error}", path_kind="blob") from error
    if len(data) > policy.EVIDENCE_BLOB_MAX_BYTES:
        raise SnapshotError(policy.EVIDENCE_BLOB_TOO_LARGE, f"{part!r} is {len(data)} bytes; the response cap is {policy.EVIDENCE_BLOB_MAX_BYTES}", bytes_total=len(data))
    try:
        content, encoding = data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        content, encoding = base64.b64encode(data).decode("ascii"), "base64"
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    out = {"run_id": run_id, "seq": seq, "part": part, "content_class": content_class, "attempt": record.get("attempt_number"),
           "ref": ref, "bytes_total": len(data), "encoding": encoding, "content": content, "digest_ok": True, "head_digest": result.head_digest}
    if record_kind == "candidate.change":
        out["path"] = payload.get("path")
    else:
        out["role"], out["call_seq"] = record.get("role"), record.get("call_seq")
    consistency = ({"kind": policy.CONSISTENCY_SEALED_STORE, "live_stream": False, "head_digest": result.head_digest}
                   if result.status == reader.VERIFIED else live_consistency())
    return envelope(operation, request_id, data=out, source=_source(cfg), consistency=consistency)

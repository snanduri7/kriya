"""KUP evidence operations (Run Cockpit v1.1 slice 1A-1): ``evidence.manifest``, ``evidence.verify``, ``evidence.records``,
``evidence.change_content`` and ``evidence.call_content``.

Transport only (full modularization B2): argument validation, the content-class table and Kriya's visibility check (both run before
any store access, so a refusal reveals nothing about a store), the opaque records cursor, and the envelope. The use cases - reading,
verifying and paging the attempt-evidence store and deriving why a store is absent - are ``kriya.application.EvidenceService``
(kriya/application/evidence.py), the only reader of the store for the UI (04 section 1 rule 1). Its typed refusals are mapped 1:1
onto the KUP error vocabulary by ``run_kup_operation``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any, Dict, Optional, Tuple

from kriya.application import RECORD_KINDS, EvidenceService
from kriya.context import RequestContext
from kriya.kup import policy
from kriya.kup.inspect import (
    CURSOR_RE,
    SnapshotTrace,
    envelope,
    live_consistency,
    source_block,
    utc_now,
    validate_run_id,
)
from kriya.kup.store import SnapshotError, require_snapshot, snapshot_directory, trace_store_path


def _ctx(request_id: str) -> RequestContext:
    return RequestContext.local(None, request_id=request_id)


def _source(cfg, request_id: str, snapshot=None) -> Dict[str, Any]:
    location = EvidenceService().location(_ctx(request_id), cfg)
    block = source_block(location.state_directory, trace_store_path(cfg), snapshot)
    block["evidence_directory"] = location.evidence_directory
    return block


def _consistency(answer_verified: bool, head_digest: Optional[str]) -> Dict[str, Any]:
    if answer_verified:
        return {"kind": policy.CONSISTENCY_SEALED_STORE, "live_stream": False, "head_digest": head_digest}
    return live_consistency()


def store_summary(cfg, run_id: str) -> Dict[str, Any]:
    """{state: absent|open|sealed|unreadable, line_count, sealed} of a run's store, for ``launch.status`` (EvidenceService.store_summary)."""
    return EvidenceService().store_summary(_ctx("launch.status"), cfg, run_id)


# ------------------------------------------------------------------ evidence.manifest

def manifest(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    """Store metadata for an evidence run id, or - with ``from_trace`` - for the store a TRACE row recorded (EvidenceService.manifest)."""
    snapshot = require_snapshot(snapshot_directory(cfg), str(request["snapshot_id"])) if request.get("snapshot_id") else None
    from_trace = bool(request.get("from_trace"))
    asked = request.get("run_id")
    validate_run_id(asked)
    if from_trace and snapshot is None:
        raise SnapshotError(policy.INVALID_REQUEST, "from_trace needs the snapshot_id the trace row is read from")
    data = EvidenceService().manifest(_ctx(request_id), cfg, asked, from_trace=from_trace,
                                      trace=SnapshotTrace(snapshot) if snapshot is not None else None)
    return envelope("evidence.manifest", request_id, data=data, source=_source(cfg, request_id, snapshot), consistency=live_consistency())


# ------------------------------------------------------------------ evidence.verify

def verify(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    answer = EvidenceService().verify(_ctx(request_id), cfg, request.get("run_id"))
    data = {**answer.data, "verified_at": utc_now()}
    return envelope("evidence.verify", request_id, data=data, source=_source(cfg, request_id), consistency=_consistency(answer.verified, data["head_digest"]))


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
        if not isinstance(kinds, list) or not kinds or any(not isinstance(k, str) or k not in RECORD_KINDS for k in kinds):
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


def records(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    run_id = request.get("run_id")
    validate_run_id(run_id)
    limit = _int_param(request, "limit", 1, policy.EVIDENCE_PAGE_MAX) or policy.EVIDENCE_PAGE_DEFAULT
    filters = _records_filters(request)
    fingerprint = _fingerprint(filters)
    cursor = request.get("cursor")
    cursor_head, after_seq = _decode_cursor(cursor, run_id, fingerprint) if cursor is not None else (None, 0)
    page = EvidenceService().records(_ctx(request_id), cfg, run_id, filters=filters, limit=limit, cursor_head=cursor_head, after_seq=after_seq,
                                     max_page_bytes=policy.EVIDENCE_PAGE_MAX_BYTES)
    next_cursor = _encode_cursor(run_id, page.head_digest, page.records[-1]["seq"], fingerprint) if page.records and page.more else None
    data = {"run_id": run_id, "head_digest": page.head_digest, "records": page.records, "next_cursor": next_cursor, "filters_applied": filters,
            "truncated": page.truncated, "truncated_tail": page.truncated_tail}
    return envelope("evidence.records", request_id, data=data, source=_source(cfg, request_id), consistency=_consistency(page.verified, page.head_digest))


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
    and Kriya's visibility setting - before any store access, so a refusal reveals nothing about a store, then EvidenceService.content:
    (3) store verification, (4) the record and what it kept, (5) the recorded size against the cap, (6) the bytes, re-hashed on read."""
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
    answer = EvidenceService().content(_ctx(request_id), cfg, run_id, seq=seq, part=part, record_kind=record_kind, content_class=content_class,
                                       max_bytes=policy.EVIDENCE_BLOB_MAX_BYTES)
    return envelope(operation, request_id, data=answer.data, source=_source(cfg, request_id), consistency=_consistency(answer.verified, answer.data["head_digest"]))

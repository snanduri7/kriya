"""EvidenceService (slice B2): the read use cases over the attempt-evidence store, extracted unchanged from the KUP operation bodies
(``kriya/kup/evidence_ops.py``, Run Cockpit v1.1 slice 1A-1).

Everything goes through ``kriya.core.attempt_evidence.reader`` (invariant I-1); no store file is opened or named here. Strict read
rules (04 section 5): files are opened read-only, nothing is created, renamed, repaired or locked, a run id is validated before any
lookup (``kriya.foundation.ids``: dot-prefixed retention-staging names are unreachable), and a store that cannot be read is a typed
refusal, never a partial body. The absence reason is DERIVED from the S1 ``evidence.attempt_store`` run event and says so.

The KUP adapter keeps the transport: argument validation, the content-class table and the visibility check that run before any store
access (``evidence.change_content``/``call_content``), the opaque cursor token, and the envelope (source, consistency).
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from kriya.application.services import (
    EVIDENCE_BLOB_TOO_LARGE,
    EVIDENCE_CONTENT_UNAVAILABLE,
    EVIDENCE_HEAD_CHANGED,
    EVIDENCE_SCHEMA_UNSUPPORTED,
    EVIDENCE_STORE_NOT_FOUND,
    EVIDENCE_STORE_UNREADABLE,
    EVIDENCE_VERIFY_FAILED,
    INVALID_REQUEST,
    ApplicationRefusal,
    TraceHistory,
)
from kriya.context.model import RequestContext
from kriya.core.attempt_evidence import model as evidence_model
from kriya.core.attempt_evidence import reader
from kriya.core.state_paths import resolve_state_directory
from kriya.foundation.ids import RUN_ID_RULE, is_valid_run_id
from kriya.foundation.redaction import redact_value

if TYPE_CHECKING:  # annotation only: the snapshot reader is imported when a manifest names a snapshot
    from kriya.control.history import Snapshot

ABSENCE_DERIVED_FROM = "history.detail run_events[evidence.attempt_store]"
RECORD_KINDS = evidence_model.KINDS  # the record kinds a records filter may name (the evidence schema's own table)


@dataclass(frozen=True)
class EvidenceLocation:
    state_directory: str
    evidence_directory: str


@dataclass(frozen=True)
class EvidenceAnswer:
    """``data`` is the operation's answer; ``verified`` is True only when it was read from a VERIFIED store (the adapter then reports the
    sealed-store consistency bound to ``data['head_digest']``)."""

    data: Dict[str, Any]
    verified: bool = False


@dataclass(frozen=True)
class RecordsPage:
    run_id: str
    head_digest: Optional[str]
    records: List[Dict[str, Any]]
    truncated: bool
    more: bool
    truncated_tail: bool
    verified: bool


def _validated(run_id: Any) -> str:
    if not is_valid_run_id(run_id):
        raise ApplicationRefusal(INVALID_REQUEST, RUN_ID_RULE)
    return run_id


def _state_dir(cfg) -> str:
    return resolve_state_directory(cfg)[0]


def _store_run(cfg, run_id: Any) -> reader.EvidenceRun:
    """The reader handle for a validated run id: the run-id rule is the whole path-safety rule (a leading ``.`` or any traversal is
    refused before a path is built)."""
    _validated(run_id)
    return reader.open_run(_state_dir(cfg), run_id)


def _guard(path_kind: str, call):
    """Run one reader call, mapping its failures to the typed vocabulary (no broad catch: anything else is a coding error)."""
    try:
        return call()
    except reader.UnsupportedEvidenceSchema as error:
        raise ApplicationRefusal(EVIDENCE_SCHEMA_UNSUPPORTED, str(error), schema_seen=error.schema_seen) from error
    except (OSError, ValueError, RecursionError) as error:
        raise ApplicationRefusal(EVIDENCE_STORE_UNREADABLE, f"{type(error).__name__}: {error}", path_kind=path_kind) from error


def _require_identity(run_id: Any, header: Any, records: Any = ()) -> None:
    """Evidence binds to its exact run identity: a store is served as run X only if its manifest and every record it yields name run X. A store directory copied or
    moved under another run's name is refused (typed), never served as that run."""
    stored = header.get("run_id") if isinstance(header, dict) else None
    if stored != run_id or any(isinstance(record, dict) and record.get("run_id") != run_id for record in records):
        raise ApplicationRefusal(EVIDENCE_STORE_UNREADABLE, f"the store found at run {run_id} is not that run's own (its manifest or records name another run); nothing is served from it",
                                 path_kind="store", reason="run_identity_mismatch")


def recorded_evidence_id(details: Any) -> Optional[str]:
    """The evidence run id a recorded event names, only when it is a safe run id."""
    value = details.get("run_id") if isinstance(details, dict) else None
    return value if isinstance(value, str) and is_valid_run_id(value) else None


def recorded_store_event(trace: TraceHistory, run_id: Any) -> Tuple[str, Any]:
    """(state, details) of the LAST ``evidence.attempt_store`` event of a trace row: state is no_row, no_events, no_event or found."""
    exists, events = trace.run_events(run_id)
    if not exists:
        return "no_row", None
    if events is None:
        return "no_events", None
    details, found = None, False
    for event in events if isinstance(events, list) else []:
        if isinstance(event, dict) and event.get("kind") == "evidence.attempt_store":
            found, details = True, event.get("details")
    return ("found", details) if found else ("no_event", None)


def _absence(trace: Optional[TraceHistory], evidence_id: Any, state: str, details: Any, asked: Any) -> Dict[str, Any]:
    """DERIVED (03 matrix row 27): the reason a store is absent, from the recorded ``evidence.attempt_store`` event of the trace row.
    Only as good as that event. ``asked`` is the id the row was read under; an event that names a DIFFERENT evidence id says nothing about
    the id asked about (never a derived "pruned" for another store), and the answer says how to resolve it."""
    base: Dict[str, Any] = {"derived_from": ABSENCE_DERIVED_FROM, "snapshot_id": trace.snapshot_id if trace else None, "recorded_event": None}
    if trace is None or state == "no_snapshot":
        return {**base, "reason": "unknown", "detail": "no snapshot_id was supplied, so the run's recorded evidence.attempt_store event cannot be read"}
    if state == "no_row":
        return {**base, "reason": "unknown", "detail": f"run {asked} is not in snapshot {trace.snapshot_id}"}
    if state == "no_events":
        return {**base, "reason": "unknown", "detail": "the run's run_events column is NULL or is not JSON"}
    if state == "no_event":
        return {**base, "reason": "pre_recorder", "detail": "the run recorded no evidence.attempt_store event (written before the attempt-evidence recorder)"}
    base["recorded_event"] = details
    recorded_id = recorded_evidence_id(details)
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


def _verified_chain(cfg, run_id: Any):
    """(run, records, verification, live): one pass verifying chain, blobs and seal and collecting the records. A store that is not
    VERIFIED (or an unsealed live store) serves nothing: EVIDENCE_VERIFY_FAILED / EVIDENCE_STORE_NOT_FOUND."""
    run = _store_run(cfg, run_id)
    if not run.exists:
        raise ApplicationRefusal(EVIDENCE_STORE_NOT_FOUND, f"no attempt-evidence store for run {run_id}")
    chain: List[Dict[str, Any]] = []
    result = _guard("store", lambda: run.verify(chain))
    if result.status != reader.NOT_FOUND:
        _require_identity(run_id, _guard("manifest", run.manifest), chain)
    live = result.status == reader.UNSEALED or (result.status == reader.TRUNCATED_TAIL and not result.sealed)
    if result.status == reader.NOT_FOUND:
        raise ApplicationRefusal(EVIDENCE_STORE_NOT_FOUND, f"no attempt-evidence store for run {run_id}")
    if result.status != reader.VERIFIED and not live:
        raise ApplicationRefusal(EVIDENCE_VERIFY_FAILED, f"the store did not verify ({result.status}); nothing is served from it",
                                 status=result.status, broken_seq=result.broken_seq, detail=result.detail, blob_problems=list(result.blob_problems))
    return run, chain, result, live


def _matches(record: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    return ((("unit" not in filters or record.get("unit_id") == filters["unit"])
             and ("phase" not in filters or record.get("phase") == filters["phase"])
             and ("role" not in filters or record.get("role") == filters["role"])
             and ("attempt" not in filters or record.get("attempt_number") == filters["attempt"]))
            and ("kind" not in filters or record.get("kind") in filters["kind"]))


def _page(chain: List[Dict[str, Any]], filters: Dict[str, Any], limit: int, first_seq: int, max_bytes: int) -> Tuple[List[Dict[str, Any]], bool, bool]:
    """(records served, stopped-by-byte-budget, more matching records follow) from the verified chain. ``chain[i]["seq"] == i + 1``
    holds for a verified chain, so the start is a slice, not a scan. The first record is always served, whatever its size.
    A served record is a display copy through the one redaction owner (S2c survey, the OI-131 rule: an envelope payload is
    content-free by design, but a free-text field such as ``tool.execution`` ``error`` can quote a credential), redacted
    BEFORE its size is counted; the stored chain and its digests keep their bytes, content stays behind content_visibility."""
    ranges = reader.explicit_phase_ranges(chain)
    page: List[Dict[str, Any]] = []
    used = 0
    for record in chain[first_seq - 1:]:
        if not _matches(record, filters):
            continue
        if len(page) == limit:
            return page, False, True
        served = redact_value({**record, "phase_provenance": reader.phase_provenance(record, ranges)})
        cost = len(json.dumps(served, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        if page and used + cost > max_bytes:
            return page, True, True
        page.append(served)
        used += cost
    return page, False, False


class EvidenceService:
    """The read side of the attempt-evidence store. Each method takes the request context and the resolved configuration first."""

    def location(self, ctx: RequestContext, cfg) -> EvidenceLocation:
        state_dir = _state_dir(cfg)
        return EvidenceLocation(state_directory=state_dir, evidence_directory=reader.store_directory(state_dir))

    def manifest(self, ctx: RequestContext, cfg, asked: str, *, from_trace: bool, snapshot: Optional["Snapshot"]) -> Dict[str, Any]:
        """Store metadata for an evidence run id, or - with ``from_trace`` - for the store a TRACE row recorded. The two ids differ in every
        real store (MEASURED): a trace row has its own id and its ``evidence.attempt_store`` event names the evidence run id, so the link
        is that recorded event and is resolved here, on the reading side, never guessed by the host. ``asked`` is already a valid run id
        (and, with ``from_trace``, ``snapshot`` is the published snapshot the trace row is read from)."""
        if snapshot is None:
            return self._manifest(cfg, asked, from_trace=from_trace, trace=None)
        from kriya.application.store_refusal import store_refusals
        from kriya.control.history import SnapshotTrace

        with store_refusals():
            return self._manifest(cfg, asked, from_trace=from_trace, trace=SnapshotTrace(snapshot))

    def _manifest(self, cfg, asked: str, *, from_trace: bool, trace: Optional[TraceHistory]) -> Dict[str, Any]:
        link: Optional[Dict[str, Any]] = None
        if from_trace:
            state, details = recorded_store_event(trace, asked)
            run_id = recorded_evidence_id(details) if state == "found" else None
            if state == "found":
                link = {"trace_run_id": asked, "evidence_run_id": run_id, "resolved_from": ABSENCE_DERIVED_FROM, "recorded_event": details}
            absence_inputs = (asked, state, details)
        else:
            run_id = asked
            absence_inputs = None
        run = _store_run(cfg, run_id) if run_id is not None else None
        if run is None or not run.exists:
            if absence_inputs is None:
                absence_inputs = (run_id, *recorded_store_event(trace, run_id)) if trace is not None else (run_id, "no_snapshot", None)
            return {"run_id": run_id, "store": "absent", "manifest": None, "seal": None, "line_count": None, "bytes_on_disk": None,
                    "blob_file_count": None, "absence": _absence(trace, run_id, *absence_inputs[1:], asked=absence_inputs[0]), "link": link}
        header = _guard("manifest", run.manifest)
        _require_identity(run_id, header)
        seal = _guard("seal", run.seal)
        line_count, bytes_on_disk, blob_files = _guard("records", run.measure)
        return {"run_id": run_id, "store": "present", "manifest": header, "seal": seal, "line_count": line_count,
                "bytes_on_disk": bytes_on_disk, "blob_file_count": blob_files, "absence": None, "link": link}

    def verify(self, ctx: RequestContext, cfg, run_id: Any) -> EvidenceAnswer:
        """The chain, blob and seal verification of one store. ``data`` lacks only ``verified_at``, which the adapter stamps when it answers."""
        run = _store_run(cfg, run_id)
        chain: List[Dict[str, Any]] = []
        result = _guard("store", lambda: run.verify(chain))
        if result.status != reader.NOT_FOUND:
            _require_identity(run_id, _guard("manifest", run.manifest), chain)
        data = {"run_id": result.run_id, "status": result.status, "record_count": result.record_count, "sealed": result.sealed,
                "broken_seq": result.broken_seq, "detail": result.detail, "seal": result.seal, "blob_problems": list(result.blob_problems),
                "head_digest": result.head_digest}
        return EvidenceAnswer(data=data, verified=result.status == reader.VERIFIED)

    def records(self, ctx: RequestContext, cfg, run_id: str, *, filters: Dict[str, Any], limit: int, cursor_head: Optional[str], after_seq: int,
                max_page_bytes: int) -> RecordsPage:
        """One page of a VERIFIED (or live, unsealed) store's records matching ``filters``, after ``after_seq``. A cursor issued against another
        chain head is refused: the store changed since."""
        _run, chain, result, _live = _verified_chain(cfg, run_id)
        head = result.head_digest
        if cursor_head is not None and cursor_head != head:
            raise ApplicationRefusal(EVIDENCE_HEAD_CHANGED, "the store's chain head moved since the cursor was issued; start again",
                                     head_then=cursor_head, head_now=head)
        page, truncated, more = _page(chain, filters, limit, max(filters.get("from_seq", 1), after_seq + 1), max_page_bytes)
        return RecordsPage(run_id=run_id, head_digest=head, records=page, truncated=truncated, more=more,
                           truncated_tail=result.status == reader.TRUNCATED_TAIL, verified=result.status == reader.VERIFIED)

    def content(self, ctx: RequestContext, cfg, run_id: str, *, seq: int, part: str, record_kind: str, content_class: str,
                max_bytes: int) -> EvidenceAnswer:
        """ONE part of ONE recorded ``record_kind`` record, AFTER the adapter checked parameters, the part's content class and Kriya's
        visibility setting (nothing here runs before those). Order is fail-closed: store verification, the record and what it kept,
        the recorded size against the cap, then the bytes, re-hashed on read."""
        run, chain, result, _live = _verified_chain(cfg, run_id)
        record = chain[seq - 1] if seq <= len(chain) else None
        if record is None or record.get("kind") != record_kind:
            raise ApplicationRefusal(INVALID_REQUEST, f"record {seq} of run {run_id} is not a {record_kind} record")
        ref = (record.get("blobs") or {}).get(part)
        digest_entry = (record.get("content_digests") or {}).get(part)
        if ref is None:
            reason = "digest_only" if digest_entry is not None else "part_not_recorded"
            raise ApplicationRefusal(EVIDENCE_CONTENT_UNAVAILABLE, f"record {seq} kept no bytes for {part!r} ({reason})", reason=reason)
        recorded_bytes = digest_entry.get("bytes") if isinstance(digest_entry, dict) else None
        if isinstance(recorded_bytes, int) and recorded_bytes > max_bytes:
            raise ApplicationRefusal(EVIDENCE_BLOB_TOO_LARGE, f"{part!r} is {recorded_bytes} bytes; the response cap is {max_bytes}", bytes_total=recorded_bytes)
        try:
            data = run.blob(ref)
        except reader.BlobCorrupt as error:
            raise ApplicationRefusal(EVIDENCE_VERIFY_FAILED, str(error), status="BLOB_CORRUPT", ref=ref) from error
        except OSError as error:
            raise ApplicationRefusal(EVIDENCE_STORE_UNREADABLE, f"{type(error).__name__}: {error}", path_kind="blob") from error
        if len(data) > max_bytes:
            raise ApplicationRefusal(EVIDENCE_BLOB_TOO_LARGE, f"{part!r} is {len(data)} bytes; the response cap is {max_bytes}", bytes_total=len(data))
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
        return EvidenceAnswer(data=out, verified=result.status == reader.VERIFIED)

    def store_summary(self, ctx: RequestContext, cfg, run_id: str) -> Dict[str, Any]:
        return store_summary(cfg, run_id)


__all__ = ["ABSENCE_DERIVED_FROM", "EvidenceAnswer", "EvidenceLocation", "EvidenceService", "RECORD_KINDS", "RecordsPage",
           "recorded_evidence_id", "recorded_store_event", "recorded_workspace_identity", "store_summary"]

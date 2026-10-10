"""KUP evidence operations (Run Cockpit v1.1 slice 1A-1): ``evidence.manifest`` and ``evidence.verify``.

The only reader of the attempt-evidence store for the UI (04 section 1 rule 1): everything goes through
``kriya.core.attempt_evidence.reader`` (invariant I-1); no store file is opened or named here. Strict read rules (04 section 5): files are opened read-only, nothing is created,
renamed, repaired or locked, a run id is validated before any lookup, dot-prefixed (retention staging) names are
unreachable, and a store that cannot be read is a typed error, never a partial body. The absence reason is DERIVED
from the S1 ``evidence.attempt_store`` run event and says so.
"""
from __future__ import annotations

from typing import Any, Dict

from kriya.core.attempt_evidence import reader
from kriya.core.state_paths import resolve_state_directory
from kriya.kup import policy
from kriya.kup.inspect import (
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
    except (OSError, ValueError) as error:
        raise SnapshotError(policy.EVIDENCE_STORE_UNREADABLE, f"{type(error).__name__}: {error}", path_kind=path_kind) from error


# ------------------------------------------------------------------ evidence.manifest

def manifest(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    run_id = request.get("run_id")
    run = _store_run(cfg, run_id)
    snapshot = require_snapshot(snapshot_directory(cfg), str(request["snapshot_id"])) if request.get("snapshot_id") else None
    if not run.exists:
        data = {"run_id": run_id, "store": "absent", "manifest": None, "seal": None, "line_count": None, "bytes_on_disk": None,
                "blob_file_count": None, "absence": _derive_absence(run_id, snapshot)}
    else:
        header = _guard("manifest", run.manifest)
        seal = _guard("seal", run.seal)
        line_count, bytes_on_disk, blob_files = _guard("records", run.measure)
        data = {"run_id": run_id, "store": "present", "manifest": header, "seal": seal, "line_count": line_count,
                "bytes_on_disk": bytes_on_disk, "blob_file_count": blob_files, "absence": None}
    return envelope("evidence.manifest", request_id, data=data, source=_source(cfg, snapshot), consistency=live_consistency())


def _derive_absence(run_id: Any, snapshot) -> Dict[str, Any]:
    """DERIVED (03 matrix row 27): the reason a store is absent, from the run's recorded ``evidence.attempt_store`` event. Only as good
    as that event; pre-recorder means the event itself is missing, and a missing trace row proves nothing about the store."""
    base: Dict[str, Any] = {"derived_from": ABSENCE_DERIVED_FROM, "snapshot_id": snapshot.snapshot_id if snapshot else None, "recorded_event": None}
    if snapshot is None:
        return {**base, "reason": "unknown", "detail": "no snapshot_id was supplied, so the run's recorded evidence.attempt_store event cannot be read"}
    exists, events = run_events_of(snapshot, run_id)
    if not exists:
        return {**base, "reason": "unknown", "detail": f"run {run_id} is not in snapshot {snapshot.snapshot_id}"}
    if events is None:
        return {**base, "reason": "unknown", "detail": "the run's run_events column is NULL or is not JSON"}
    details = None
    found = False
    for event in events if isinstance(events, list) else []:
        if isinstance(event, dict) and event.get("kind") == "evidence.attempt_store":
            found, details = True, event.get("details")
    if not found:
        return {**base, "reason": "pre_recorder", "detail": "the run recorded no evidence.attempt_store event (written before the attempt-evidence recorder)"}
    base["recorded_event"] = details
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
    result = _guard("store", run.verify)
    data = {"run_id": result.run_id, "status": result.status, "record_count": result.record_count, "sealed": result.sealed,
            "broken_seq": result.broken_seq, "detail": result.detail, "seal": result.seal, "blob_problems": list(result.blob_problems),
            "head_digest": result.head_digest, "verified_at": utc_now()}
    if result.status == reader.VERIFIED:
        consistency: Dict[str, Any] = {"kind": policy.CONSISTENCY_SEALED_STORE, "live_stream": False, "head_digest": result.head_digest}
    else:
        consistency = live_consistency()
    return envelope("evidence.verify", request_id, data=data, source=_source(cfg), consistency=consistency)


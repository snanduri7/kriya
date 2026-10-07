"""The KUP operations as one in-process entry point: ``run_kup_operation(cfg, request) -> envelope``.

``kriya traces --json ...`` and ``kriya runs status --json --kup-version 1`` build a request dict from their flags
and print ``encode_response(run_kup_operation(...))``. Nothing here bootstraps logging, migrates a schema,
creates a directory (except acquisition inside the snapshot directory), copies legacy data, or constructs a
kernel, plugin or model; ``sys.dont_write_bytecode`` is set by the CLI before this module is imported.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from kriya.kup import policy
from kriya.kup.inspect import (
    capabilities,
    envelope,
    error_envelope,
    from_snapshot_error,
    history_detail,
    history_list,
    history_prompt,
    live_consistency,
    not_applicable_consistency,
    snapshot_consistency,
    source_block,
    utc_now,
)
from kriya.kup.store import (
    SnapshotError,
    list_snapshots,
    require_snapshot,
    snapshot_directory,
    snapshot_summary,
    stat_metadata,
    trace_store_path,
    verify_digest,
)


def run_kup_operation(cfg, request: Dict[str, Any], request_id: str = "cli") -> Dict[str, Any]:
    operation = str(request.get("operation", ""))
    try:
        if operation == "capabilities":
            return envelope(operation, request_id, data=capabilities(), source=_source(cfg), consistency=not_applicable_consistency())
        if operation == "snapshot.acquire":
            return _acquire(cfg, request, request_id)
        if operation == "snapshot.list":
            return _list_snapshots(cfg, request, request_id)
        if operation == "snapshot.prune":
            return _prune(cfg, request, request_id)
        if operation == "snapshot.verify":
            return _verify(cfg, request, request_id)
        if operation in ("history.list", "history.detail", "history.prompt"):
            return _history(cfg, operation, request, request_id)
        if operation == "workspace.status":
            return _workspace_status(request, request_id)
        return error_envelope(operation or "unknown", request_id, policy.INVALID_REQUEST, f"unknown operation {operation!r}")
    except SnapshotError as error:
        return from_snapshot_error(operation, request_id, error)


def _source(cfg, snapshot=None) -> Dict[str, Any]:
    state_dir = os.path.dirname(trace_store_path(cfg))
    return source_block(state_dir, trace_store_path(cfg), snapshot)


def _history(cfg, operation: str, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    snapshot_id = request.get("snapshot_id")
    if not snapshot_id:
        raise SnapshotError(policy.INVALID_REQUEST, "snapshot_id is required: history is read from a published snapshot only (acquire one, or choose one from snapshot.list)")
    snap = require_snapshot(snapshot_directory(cfg), str(snapshot_id))
    if operation == "history.list":
        data = history_list(snap, request.get("limit"), request.get("cursor"))
    elif operation == "history.detail":
        data = history_detail(snap, str(request.get("run_id")))
    else:
        data = history_prompt(snap, str(request.get("run_id")))
    return envelope(operation, request_id, data=data, source=_source(cfg, snap), consistency=snapshot_consistency(snap, trace_store_path(cfg)))


def _acquire(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    from kriya.kup.acquire import acquire_snapshot  # the only write-capable module; imported only here

    workspace: Optional[str] = request.get("workspace")
    run_active_check = None
    if workspace:
        def run_active_check() -> Optional[str]:
            from kriya.control.recovery import assess_recovery  # read-only: never creates, locks or writes
            assessment = assess_recovery(workspace)
            return assessment.run_active if getattr(assessment, "run_active", None) else None
    result = acquire_snapshot(trace_store_path(cfg), snapshot_directory(cfg), run_active_check=run_active_check)
    snap = result["snapshot"]
    data = {**snapshot_summary(snap, stat_metadata(trace_store_path(cfg))), "orphans_removed": result["orphans_removed"], "pruned": result["pruned"],
            "backup_steps": result["manifest"]["backup_steps"], "duration_ms": result["manifest"]["duration_ms"]}
    return envelope("snapshot.acquire", request_id, data=data, source=_source(cfg, snap), consistency=snapshot_consistency(snap, trace_store_path(cfg)))


def _list_snapshots(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    snapdir = snapshot_directory(cfg)
    now = stat_metadata(trace_store_path(cfg))
    items = []
    for snap in list_snapshots(snapdir):
        entry = snapshot_summary(snap, now)
        if request.get("verify"):
            entry["digest_verified"] = verify_digest(snap) is None
        items.append(entry)
    return envelope("snapshot.list", request_id, data={"snapshot_directory": snapdir, "snapshots": items, "retain": policy.RETAIN_SNAPSHOTS},
                    source=_source(cfg), consistency=live_consistency())


def _verify(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    """Digest verification of EXACTLY the requested snapshot (the host calls this when it pins a snapshot for a
    browsing session). Guarantee, stated precisely: digest verified at pin; metadata (size, mtime) checked per
    query. A later modification that preserves size and mtime is caught only by the next explicit verification."""
    snapshot_id = request.get("snapshot_id")
    if not snapshot_id:
        raise SnapshotError(policy.INVALID_REQUEST, "snapshot_id is required for snapshot.verify")
    snap = require_snapshot(snapshot_directory(cfg), str(snapshot_id))  # typed MISSING / UNAVAILABLE / CORRUPT; never another id
    started = time.monotonic()
    reason = verify_digest(snap)
    if reason:
        raise SnapshotError(policy.SNAPSHOT_CORRUPT, reason, snapshot_id=snap.snapshot_id)
    file_meta = snap.manifest.get("snapshot_file", {})
    data = {"snapshot_id": snap.snapshot_id, "digest_verified": True, "sha256": file_meta.get("sha256"), "size": file_meta.get("size"),
            "verified_at": utc_now(), "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
            "guarantee": "digest verified at this request; size and mtime are checked on every later query"}
    return envelope("snapshot.verify", request_id, data=data, source=_source(cfg, snap), consistency=live_consistency())


def _prune(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    from kriya.kup.acquire import prune_snapshots

    keep = request.get("keep")
    keep = policy.RETAIN_SNAPSHOTS if keep is None else int(keep)
    if keep < 0:
        raise SnapshotError(policy.INVALID_REQUEST, "keep must be >= 0")
    result = prune_snapshots(snapshot_directory(cfg), keep=keep)
    return envelope("snapshot.prune", request_id, data=result, source=_source(cfg), consistency=live_consistency())


def _workspace_status(request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    from kriya.control.recovery import STATUS_CLEAN, STATUS_RUN_ACTIVE, assess_recovery

    workspace = request.get("workspace")
    if not isinstance(workspace, str) or not os.path.isabs(workspace):
        raise SnapshotError(policy.INVALID_REQUEST, "workspace must be an absolute path")
    assessment = assess_recovery(workspace)
    status = assessment.status
    exit_code = 0 if status == STATUS_CLEAN else (3 if status == STATUS_RUN_ACTIVE else 1)  # = kriya runs status's own exit codes
    data = {"workspace": assessment.workspace_path, "run_active": status == STATUS_RUN_ACTIVE, "status": status, "exit_code": exit_code,
            "assessment": assessment.to_dict()}
    # No configuration is loaded on this path (kriya runs skips load_config by design), so source is null (P-24).
    return envelope("workspace.status", request_id, data=data, source=None, consistency=live_consistency())

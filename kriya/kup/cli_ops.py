"""The KUP operations as one in-process entry point: ``run_kup_operation(cfg, request) -> envelope``.

``kriya traces --json ...`` and ``kriya runs status --json --kup-version 1`` build a request dict from their flags
and print ``encode_response(run_kup_operation(...))``. Nothing here bootstraps logging, migrates a schema,
creates a directory (except acquisition inside the snapshot directory), copies legacy data, or constructs a
kernel, plugin or model; ``sys.dont_write_bytecode`` is set by the CLI before this module is imported.

Transport only (full modularization B2-B4): request dispatch, argument validation, the envelope, and the 1:1 mapping of an application
service's typed refusal (``ApplicationRefusal``) onto the KUP error vocabulary. Since B4 every use case is reached through the
``KriyaClient`` (``LocalKriyaClient``, kriya/client/): ``client.<service>.<op>(client.context(...), ...)``; no KUP module imports
``kriya.application``. The read use cases are ``kriya.application`` services
(history, project status, capabilities); since D2 snapshot acquisition, listing, verification and pruning are ``HistoryService`` use cases
over STATE's snapshot store (``kriya/control/history``), and this module keeps only their argument validation and envelopes. The write/run use cases (B3: configuration approval, project create/initialize, change and runtime-command
reviews, preflight and launch status) are services too; this module hands each operation handler its service.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from kriya.client import ApplicationRefusal  # the adapter sees the application only through the client boundary (B4)
from kriya.core.state_paths import StateDirectoryError
from kriya.kup import evidence_ops, policy
from kriya.kup.inspect import (
    capabilities,
    envelope,
    error_envelope,
    from_snapshot_error,
    live_consistency,
    not_applicable_consistency,
    snapshot_consistency,
    source_block,
    utc_now,
)
from kriya.kup.store import SnapshotError


def run_kup_operation(cfg, request: Dict[str, Any], request_id: str = "cli", client=None) -> Dict[str, Any]:
    """One KUP operation: the request is validated and translated into ONE call of the ``KriyaClient`` (``client.<service>.<op>(ctx, ...)``)
    and its answer, or its typed refusal, into the envelope. ``client`` defaults to a ``LocalKriyaClient`` over a fresh composition, built
    for this request (``kup-stdio`` therefore gets one per request, exactly like the configuration)."""
    from kriya.client import local_client

    client = local_client() if client is None else client
    operation = str(request.get("operation", ""))
    try:
        if operation == "capabilities":
            health = client.health.capabilities(client.context(workspace_root=None, request_id=request_id), cfg)
            return envelope(operation, request_id, data=capabilities(health), source=_source(cfg, client, request_id), consistency=not_applicable_consistency())
        if operation == "snapshot.acquire":
            return _acquire(cfg, request, request_id, client)
        if operation == "snapshot.list":
            return _list_snapshots(cfg, request, request_id, client)
        if operation == "snapshot.prune":
            return _prune(cfg, request, request_id, client)
        if operation == "snapshot.verify":
            return _verify(cfg, request, request_id, client)
        if operation in ("history.list", "history.detail", "history.prompt"):
            return _history(cfg, operation, request, request_id, client)
        if operation == "workspace.status":
            return _workspace_status(request, request_id, client)
        if operation == "workspace.describe":
            return _workspace_describe(cfg, request, request_id, client)
        if operation in ("execution.preflight", "launch.status"):
            return _execution(cfg, operation, request, request_id, client)
        if operation in ("configuration.approval_status", "configuration.approve"):
            from kriya.kup import authority_ops

            return (authority_ops.status if operation == "configuration.approval_status" else authority_ops.approve)(cfg, request, request_id, client)
        if operation == "project.initialize":
            from kriya.kup import project_init_ops

            return project_init_ops.initialize(cfg, request, request_id, client)
        if operation == "change_approval.review":
            from kriya.kup import review_ops

            return review_ops.review(cfg, request, request_id, client)
        if operation == "runtime_command.review":
            from kriya.kup import runtime_command_ops

            return runtime_command_ops.review(cfg, request, request_id, client)
        if operation == "workspace.create":
            from kriya.kup import project_ops

            return project_ops.create(cfg, request, request_id, client)
        if operation in ("execution.context", "execution.model_probe"):
            from kriya.kup import context_ops

            return (context_ops.context if operation == "execution.context" else context_ops.model_probe)(cfg, request, request_id, client)
        if operation in ("skills.list", "skills.describe", "tools.list", "tools.describe", "tools.approval_state"):
            from kriya.kup import catalog_ops

            return catalog_ops.HANDLERS[operation](cfg, request, request_id, client)
        if operation in ("evidence.manifest", "evidence.verify", "evidence.records", "evidence.change_content", "evidence.call_content"):
            return _evidence(cfg, operation, request, request_id, client)
        return error_envelope(operation or "unknown", request_id, policy.INVALID_REQUEST, f"unknown operation {operation!r}")
    except SnapshotError as error:
        return from_snapshot_error(operation, request_id, error)
    except ApplicationRefusal as refusal:  # a service's typed refusal: the same code, message, database state and extra fields on the wire
        extra = dict(refusal.extra)
        return error_envelope(operation, request_id, refusal.reason_code, refusal.message, extra.pop("database_state", None), **extra)


def _evidence(cfg, operation: str, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    handler = {"evidence.manifest": evidence_ops.manifest, "evidence.verify": evidence_ops.verify, "evidence.records": evidence_ops.records, "evidence.change_content": evidence_ops.change_content, "evidence.call_content": evidence_ops.call_content}[operation]
    try:
        return handler(cfg, request, request_id, client)
    except StateDirectoryError as error:  # the configured state directory is unusable: a configuration failure, typed
        return error_envelope(operation, request_id, policy.CONFIG_LOAD_FAILED, str(error))


def _execution(cfg, operation: str, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    from kriya.kup import execution

    try:
        return (execution.preflight if operation == "execution.preflight" else execution.launch_status)(cfg, request, request_id, client)
    except StateDirectoryError as error:
        return error_envelope(operation, request_id, policy.CONFIG_LOAD_FAILED, str(error))


def _workspace_describe(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    from kriya.kup import workspace_ops

    try:
        return workspace_ops.describe(cfg, request, request_id, client)
    except StateDirectoryError as error:  # the configured state directory is unusable: a configuration failure, typed
        return error_envelope("workspace.describe", request_id, policy.CONFIG_LOAD_FAILED, str(error))


def _source(cfg, client, request_id: str, snapshot=None) -> Dict[str, Any]:
    location = client.history.location(client.context(workspace_root=None, request_id=request_id), cfg)
    return source_block(location.state_directory, location.trace_database, snapshot)


def _consistency(cfg, client, request_id: str, snapshot) -> Dict[str, Any]:
    summary = client.history.summary(client.context(workspace_root=None, request_id=request_id), cfg, snapshot)
    return snapshot_consistency(snapshot.snapshot_id, summary)


def _history(cfg, operation: str, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    snapshot_id = request.get("snapshot_id")
    if not snapshot_id:
        raise SnapshotError(policy.INVALID_REQUEST, "snapshot_id is required: history is read from a published snapshot only (acquire one, or choose one from snapshot.list)")
    ctx, service = client.context(workspace_root=None, request_id=request_id), client.history
    snap = service.snapshot(ctx, cfg, str(snapshot_id))
    if operation == "history.list":
        if request.get("workspace"):  # GUI M5: only the runs whose evidence manifest records this workspace
            workspace, limit = str(request["workspace"]), request.get("limit")
            if not os.path.isabs(workspace):
                raise SnapshotError(policy.INVALID_REQUEST, "workspace must be an absolute path")
            n = policy.LIST_DEFAULT if limit is None else int(limit)
            if n < 1 or n > policy.LIST_MAX:
                raise SnapshotError(policy.INVALID_REQUEST, f"limit must be between 1 and {policy.LIST_MAX}")
            data = service.list_in_workspace(ctx, cfg, snap, limit=n, cursor=request.get("cursor"), workspace=workspace)
        else:
            data = service.list(ctx, cfg, snap, limit=request.get("limit"), cursor=request.get("cursor"))
    elif operation == "history.detail":
        data = service.detail(ctx, cfg, snap, str(request.get("run_id")))
    else:
        data = service.prompt(ctx, cfg, snap, str(request.get("run_id")))
    return envelope(operation, request_id, data=data, source=_source(cfg, client, request_id, snap), consistency=_consistency(cfg, client, request_id, snap))


def _acquire(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    workspace: Optional[str] = request.get("workspace")
    answer = client.history.acquire(client.context(workspace_root=None, request_id=request_id), cfg, workspace=workspace or None)
    snap = answer.snapshot
    return envelope("snapshot.acquire", request_id, data=answer.data, source=_source(cfg, client, request_id, snap),
                    consistency=_consistency(cfg, client, request_id, snap))


def _list_snapshots(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    data = client.history.snapshots(client.context(workspace_root=None, request_id=request_id), cfg, verify=bool(request.get("verify")))
    return envelope("snapshot.list", request_id, data=data, source=_source(cfg, client, request_id), consistency=live_consistency())


def _verify(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    """Digest verification of EXACTLY the requested snapshot (the host calls this when it pins a snapshot for a
    browsing session). Guarantee, stated precisely: digest verified at pin; metadata (size, mtime) checked per
    query. A later modification that preserves size and mtime is caught only by the next explicit verification."""
    snapshot_id = request.get("snapshot_id")
    if not snapshot_id:
        raise SnapshotError(policy.INVALID_REQUEST, "snapshot_id is required for snapshot.verify")
    answer = client.history.verify_snapshot(client.context(workspace_root=None, request_id=request_id), cfg, str(snapshot_id))
    data = {**answer.data, "verified_at": utc_now()}
    return envelope("snapshot.verify", request_id, data=data, source=_source(cfg, client, request_id, answer.snapshot), consistency=live_consistency())


def _prune(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    keep = request.get("keep")
    keep = policy.RETAIN_SNAPSHOTS if keep is None else int(keep)
    if keep < 0:
        raise SnapshotError(policy.INVALID_REQUEST, "keep must be >= 0")
    result = client.history.prune(client.context(workspace_root=None, request_id=request_id), cfg, keep=keep)
    return envelope("snapshot.prune", request_id, data=result, source=_source(cfg, client, request_id), consistency=live_consistency())


def _workspace_status(request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    workspace = request.get("workspace")
    if not isinstance(workspace, str) or not os.path.isabs(workspace):
        raise SnapshotError(policy.INVALID_REQUEST, "workspace must be an absolute path")
    data = client.projects.status(client.context(workspace_root=None, request_id=request_id), workspace)
    # No configuration is loaded on this path (kriya runs skips load_config by design), so source is null (P-24).
    return envelope("workspace.status", request_id, data=data, source=None, consistency=live_consistency())

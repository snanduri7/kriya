"""KUP ``runtime_command.review``: the EXACT commands a pending (or decided) runtime-command review asks the person to allow Kriya to run (GUI-RUNTIME-COMMAND-APPROVAL-001).

Invariant this exists for: Kriya never asks a human to allow commands to run without making those exact commands reviewable, and the host and renderer never state a command Kriya
did not record. It serves ONLY what the launch process stored for that one review (``kriya/control/launch_approval.py`` ``build_pending_runtime``), after re-proving it:

1. the review exists in the launch record (pending, or decided/expired history), for this workspace identity, and is a RUNTIME-COMMAND review (a change review is not served here);
2. its recorded command fingerprint is the digest the decision must carry (``candidate_digest``) AND re-computes from the recorded working directory, commands in order, input channel and
   stdin digest - so a record that was altered, or whose displayed commands are not the ones bound to the digest, is never shown.

Any failure is a typed refusal and nothing is served. Read-only: it approves nothing; the decision is ``kriya launch-decision --kind runtime_command``, which still refuses a review id /
fingerprint that is no longer the pending one (STALE_REVIEW). No stdin or environment VALUE exists in the record, so none can be served.
"""
from __future__ import annotations

from typing import Any, Dict

from kriya.control.launch import valid_launch_id
from kriya.control.launch_approval import KIND_RUNTIME, REVIEW_ID_RE, WORKING_DIRECTORY_KIND
from kriya.control.persistence import UnreadableRunRecordError, load_launch_record
from kriya.control.workspace_identity import workspace_identity
from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.review_ops import _find_review
from kriya.kup.store import SnapshotError
from kriya.kup.workspace_ops import validated_workspace
from kriya.workflow.runtime_command_approval import fingerprint_of


def review(cfg: Any, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    launch_id, review_id = request.get("launch_id"), request.get("review_id")
    if not valid_launch_id(launch_id):
        raise SnapshotError(policy.INVALID_REQUEST, "launch_id must have the shape YYYYMMDDTHHMMSS-xxxxxxxx")
    if not isinstance(review_id, str) or not REVIEW_ID_RE.match(review_id):
        raise SnapshotError(policy.INVALID_REQUEST, "review_id must be a review identity Kriya gave (ra-<24 hex>)")
    _requested, workspace = validated_workspace(request)
    try:
        record = load_launch_record(workspace, str(launch_id))
    except UnreadableRunRecordError as error:
        raise SnapshotError(policy.EVIDENCE_STORE_UNREADABLE, f"the launch record cannot be trusted: {error.reason}", path_kind="launch") from error
    if record is None:
        raise SnapshotError(policy.LAUNCH_NOT_FOUND, f"no launch {launch_id} is recorded for this folder", reason="not_recorded")
    recorded = record.get("workspace")
    if (recorded.get("identity") if isinstance(recorded, dict) else None) != workspace_identity(workspace):
        raise SnapshotError(policy.REVIEW_NOT_FOUND, "that launch belongs to a different folder", reason="wrong_workspace")
    entry = _find_review(record, review_id)
    if entry is None:
        raise SnapshotError(policy.REVIEW_NOT_FOUND, f"launch {launch_id} has no review {review_id}", reason="not_recorded")
    if entry.get("kind") != KIND_RUNTIME:
        raise SnapshotError(policy.REVIEW_NOT_FOUND, f"review {review_id} is not a runtime-command review", reason="wrong_kind")

    def unverified(reason: str) -> SnapshotError:
        return SnapshotError(policy.REVIEW_COMMAND_UNVERIFIED, "the recorded commands do not match the fingerprint the review is bound to; they are not shown", reason=reason)

    commands, cwd, channel, stdin_sha = entry.get("commands"), entry.get("working_directory"), entry.get("input_channel"), entry.get("stdin_sha256")
    if not isinstance(commands, list) or not commands or not all(isinstance(c, list) and c and all(isinstance(a, str) and a for a in c) for c in commands):
        raise unverified("commands_unreadable")
    if not isinstance(cwd, str) or not cwd or not isinstance(channel, str) or (stdin_sha is not None and not isinstance(stdin_sha, str)):
        raise unverified("identity_unreadable")
    if entry.get("working_directory_kind") != WORKING_DIRECTORY_KIND:
        raise unverified("working_directory_kind")
    digest = entry.get("candidate_digest")
    if digest != entry.get("command_fingerprint") or digest != fingerprint_of(cwd, [tuple(c) for c in commands], channel, stdin_sha):
        raise unverified("fingerprint_mismatch")
    data = {
        "launch_id": launch_id, "review_id": review_id, "run_id": launch_id, "status": str(entry.get("status")), "verified": True,
        "command_fingerprint": digest, "unit_id": entry.get("unit_id") if isinstance(entry.get("unit_id"), str) else None,
        "criterion": str(entry.get("criterion") or ""), "commands": commands, "working_directory": cwd, "working_directory_kind": WORKING_DIRECTORY_KIND,
        "input_channel": channel, "stdin_present": stdin_sha is not None,
        "environment_names": [n for n in (entry.get("environment_names") or []) if isinstance(n, str)],
        "requested_at": str(entry.get("requested_at") or ""), "expires_at": str(entry.get("expires_at") or ""),
    }
    return envelope("runtime_command.review", request_id, data=data, source=None, consistency=live_consistency())

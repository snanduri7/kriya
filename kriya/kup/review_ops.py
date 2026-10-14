"""KUP ``change_approval.review``: the EXACT proposed change a review asks the person to approve (PRODUCT-UX-COMPLETION).

Invariant this exists for: Kriya never asks a human to approve a source mutation without making that exact mutation reviewable.

Why it is its own approval-scoped read and not ``evidence.change_content``: general evidence content stays fail-closed behind
``evidence.attempt_recorder.content_visibility`` (default off); this operation does not touch that policy and serves nothing from the evidence store. It serves ONLY
the diff text the launch process stored for that one review (``kriya/control/launch.py`` ``_store_review_diff``) - the very text the gate handed to the approval
callback - after re-proving that it is what the review is bound to:

1. the review exists in the launch record (pending, or decided/expired history), for this workspace identity;
2. the stored document is complete and ownership-stamped and names the same launch, review, workspace identity and ``candidate_digest``;
3. every file's text re-hashes to the ``diff_sha256`` the review recorded, in the same order, and the recomputed ``candidate_digest`` equals the recorded one.

Any failure is a typed refusal and nothing is served (a diff that cannot be proved to be the reviewed one is never shown as if it were). Read-only: it approves nothing;
the decision is ``kriya launch-decision``, which still refuses a review id / digest that is no longer the pending one (``STALE_REVIEW``).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from kriya.control.launch import valid_launch_id
from kriya.control.launch_approval import _sha, candidate_digest, split_reason
from kriya.control.persistence import UnreadableRunRecordError, load_launch_record, load_launch_review_diff
from kriya.control.workspace_identity import workspace_identity
from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.store import SnapshotError
from kriya.kup.workspace_ops import validated_workspace

REVIEW_ID_RE = re.compile(r"^ra-[0-9a-f]{24}$")
# The most diff LINES one response carries. Past it the file is marked truncated and the host must NOT offer approval: a diff shown in part is not "the exact change".
MAX_LINES_TOTAL = 20000
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(text: str, budget: int) -> Tuple[Dict[str, Any], int]:
    """(file view, lines used). Hunks and typed lines from a unified diff: context / add / remove, with old and new line numbers. ``additions``/``deletions`` count what was
    parsed (the lines themselves), never what a header claims. ``truncated`` is true when ``budget`` ran out; the counts then still cover the whole file."""
    hunks: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    additions = deletions = 0
    used = 0
    truncated = False
    old_no = new_no = 0
    for raw in text.splitlines():
        match = _HUNK_RE.match(raw)
        if match:
            old_start, old_lines = int(match.group(1)), int(match.group(2)) if match.group(2) is not None else 1
            new_start, new_lines = int(match.group(3)), int(match.group(4)) if match.group(4) is not None else 1
            old_no, new_no = old_start, new_start
            current = {"header": raw, "old_start": old_start, "old_lines": old_lines, "new_start": new_start, "new_lines": new_lines, "lines": []}
            hunks.append(current)
            continue
        if current is None:
            continue  # the ---/+++ file header lines (and anything before the first hunk)
        if raw.startswith("\\"):
            kind, line = "note", raw[1:].strip()
        elif raw.startswith("+"):
            kind, line = "add", raw[1:]
            additions += 1
        elif raw.startswith("-"):
            kind, line = "remove", raw[1:]
            deletions += 1
        else:
            kind, line = "context", raw[1:] if raw.startswith(" ") else raw
        entry = {"kind": kind, "text": line, "old_line": None, "new_line": None}
        if kind in ("context", "remove"):
            entry["old_line"] = old_no
            old_no += 1
        if kind in ("context", "add"):
            entry["new_line"] = new_no
            new_no += 1
        if used >= budget:
            truncated = True
            continue
        current["lines"].append(entry)
        used += 1
    change_type = "modified"
    if hunks and all(h["old_lines"] == 0 for h in hunks) and deletions == 0 and hunks[0]["old_start"] == 0:
        change_type = "added"
    elif hunks and all(h["new_lines"] == 0 for h in hunks) and additions == 0 and hunks[0]["new_start"] == 0:
        change_type = "deleted"
    return {"change_type": change_type, "additions": additions, "deletions": deletions, "hunks": hunks, "truncated": truncated}, used


def _find_review(record: Dict[str, Any], review_id: str) -> Optional[Dict[str, Any]]:
    pending = record.get("pending_approval")
    if isinstance(pending, dict) and pending.get("review_id") == review_id:
        return pending
    for entry in record.get("approval_history") or []:
        if isinstance(entry, dict) and entry.get("review_id") == review_id:
            return entry
    return None


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
    identity = workspace_identity(workspace)
    recorded_identity = (record.get("workspace") or {}).get("identity") if isinstance(record.get("workspace"), dict) else None
    if recorded_identity != identity:
        raise SnapshotError(policy.REVIEW_NOT_FOUND, "that launch belongs to a different folder", reason="wrong_workspace")
    entry = _find_review(record, review_id)
    if entry is None:
        raise SnapshotError(policy.REVIEW_NOT_FOUND, f"launch {launch_id} has no review {review_id}", reason="not_recorded")
    document = load_launch_review_diff(workspace, str(launch_id), review_id)
    if document is None:
        raise SnapshotError(policy.REVIEW_DIFF_UNAVAILABLE, "the proposed change was not stored for inspection, or its record cannot be trusted", reason="not_stored")

    def unverified(reason: str) -> SnapshotError:
        return SnapshotError(policy.REVIEW_DIFF_UNVERIFIED, "the stored proposed change does not match the review it belongs to; it is not shown", reason=reason)

    digest = entry.get("candidate_digest")
    if (document.get("launch_id"), document.get("review_id"), document.get("workspace_identity"), document.get("candidate_digest")) != (launch_id, review_id, identity, digest):
        raise unverified("identity_mismatch")
    recorded_files, stored_files = entry.get("files"), document.get("files")
    if not isinstance(recorded_files, list) or not isinstance(stored_files, list) or len(recorded_files) != len(stored_files) or not stored_files:
        raise unverified("file_set_mismatch")
    gate_files: List[Dict[str, str]] = []
    for recorded, stored in zip(recorded_files, stored_files, strict=True):
        if not isinstance(recorded, dict) or not isinstance(stored, dict) or recorded.get("filepath") != stored.get("filepath") or not isinstance(stored.get("diff"), str):
            raise unverified("file_set_mismatch")
        if _sha(stored["diff"]) != recorded.get("diff_sha256"):
            raise unverified("file_digest_mismatch")
        gate_files.append({"filepath": str(stored["filepath"]), "content": stored["diff"]})
    if candidate_digest(gate_files) != digest:
        raise unverified("candidate_digest_mismatch")
    files: List[Dict[str, Any]] = []
    budget = MAX_LINES_TOTAL
    for item in gate_files:
        view, used = parse_unified_diff(item["content"], budget)
        budget -= used
        files.append({"path": item["filepath"], "diff_sha256": _sha(item["content"]), **view})
    reason = str(entry.get("reason") or "")
    policy_note, review_summary = split_reason(reason)
    data = {
        "launch_id": launch_id, "review_id": review_id, "status": str(entry.get("status")), "candidate_digest": digest, "verified": True,
        "reason": reason, "policy_note": policy_note, "review_summary": review_summary, "reason_truncated": bool(entry.get("reason_truncated")), "files": files,
        "truncated": any(f["truncated"] for f in files),
    }
    return envelope("change_approval.review", request_id, data=data, source=None, consistency=live_consistency())

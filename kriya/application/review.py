"""ReviewService (slice B3): the EXACT thing a review asks the person to decide, re-proved against the review's digests before it is served.
Extracted unchanged from ``kriya/kup/review_ops.py`` (``change_approval.review``, PRODUCT-UX-COMPLETION) and
``kriya/kup/runtime_command_ops.py`` (``runtime_command.review``, GUI-RUNTIME-COMMAND-APPROVAL-001).

Invariant this exists for: Kriya never asks a human to approve a source mutation, or to allow commands to run, without making that exact
mutation / those exact commands reviewable; the host and renderer never state a change or a command Kriya did not record.

``change_review`` is its own approval-scoped read, not ``evidence.change_content``: general evidence content stays fail-closed behind
``evidence.attempt_recorder.content_visibility`` (default off); this use case does not touch that policy and serves nothing from the
evidence store. It serves ONLY the diff text the launch process stored for that one review (``kriya/control/launch.py``
``_store_review_diff``) - the very text the gate handed to the approval callback - after re-proving that it is what the review is bound
to: (1) the review exists in the launch record (pending, or decided/expired history), for this workspace identity; (2) the stored
document is complete and ownership-stamped and names the same launch, review, workspace identity and ``candidate_digest``; (3) every
file's text re-hashes to the ``diff_sha256`` the review recorded, in the same order, and the recomputed ``candidate_digest`` equals the
recorded one.

``runtime_command_review`` serves ONLY what the launch process stored for that one review (``kriya/control/launch_approval.py``
``build_pending_runtime``), after re-proving: (1) the review exists, for this workspace identity, and is a RUNTIME-COMMAND review (a change
review is not served here); (2) its recorded command fingerprint is the digest the decision must carry (``candidate_digest``) AND
re-computes from the recorded working directory, commands in order, input channel and stdin digest. No stdin or environment VALUE exists
in the record, so none can be served. S2d (OI-153 (c)): commands that carry a secret the redaction owner recognises are not served (a review
recorded raw by an earlier Kriya; S2d records none) - raw they disclose it, redacted they are not what the fingerprint binds.

Any failure is a typed refusal and nothing is served. Read-only: it approves nothing; the decision is ``kriya launch-decision``
(``ApprovalService.decide``), which still refuses a review id / digest that is no longer the pending one (``STALE_REVIEW``). The adapter
validates the launch and review identities and admits the folder before either runs.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from kriya.application.services import (
    EVIDENCE_STORE_UNREADABLE,
    LAUNCH_NOT_FOUND,
    REVIEW_COMMAND_UNVERIFIED,
    REVIEW_DIFF_UNAVAILABLE,
    REVIEW_DIFF_UNVERIFIED,
    REVIEW_NOT_FOUND,
    ApplicationRefusal,
)
from kriya.context.model import RequestContext
from kriya.context.workspace_identity import workspace_identity
from kriya.control.persistence import UnreadableRunRecordError, load_launch_record, load_launch_review_diff
from kriya.foundation.redaction import redact_text
from kriya.policy.approval import (
    KIND_RUNTIME,
    WORKING_DIRECTORY_KIND,
    _sha,
    candidate_digest,
    commands_carry_secret,
    fingerprint_of,
    split_reason,
)

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
    # split on "\n" ONLY: str.splitlines() also breaks on \x0b, \x0c, \x1c-\x1e, \x85 and U+2028/9, which would render the tail of a changed line as a separate context line
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for raw in lines:
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


def has_unrendered_body(text: str) -> bool:
    """Called for a file whose diff produced no hunk: True when the text still has content beyond its ---/+++ file header, i.e. something that cannot be displayed."""
    return any(line.strip() and not line.startswith(("---", "+++")) for line in text.split("\n"))


def _find_review(record: Dict[str, Any], review_id: str) -> Optional[Dict[str, Any]]:
    pending = record.get("pending_approval")
    if isinstance(pending, dict) and pending.get("review_id") == review_id:
        return pending
    for entry in record.get("approval_history") or []:
        if isinstance(entry, dict) and entry.get("review_id") == review_id:
            return entry
    return None


class ReviewService:
    def change_review(self, ctx: RequestContext, workspace: str, launch_id: str, review_id: str) -> Dict[str, Any]:
        """The proved diff of review ``review_id`` of launch ``launch_id`` in the admitted folder ``workspace`` (canonical)."""
        try:
            record = load_launch_record(workspace, str(launch_id))
        except UnreadableRunRecordError as error:
            raise ApplicationRefusal(EVIDENCE_STORE_UNREADABLE, f"the launch record cannot be trusted: {error.reason}", path_kind="launch") from error
        if record is None:
            raise ApplicationRefusal(LAUNCH_NOT_FOUND, f"no launch {launch_id} is recorded for this folder", reason="not_recorded")
        identity = workspace_identity(workspace)
        recorded_identity = (record.get("workspace") or {}).get("identity") if isinstance(record.get("workspace"), dict) else None
        if recorded_identity != identity:
            raise ApplicationRefusal(REVIEW_NOT_FOUND, "that launch belongs to a different folder", reason="wrong_workspace")
        entry = _find_review(record, review_id)
        if entry is None:
            raise ApplicationRefusal(REVIEW_NOT_FOUND, f"launch {launch_id} has no review {review_id}", reason="not_recorded")
        document = load_launch_review_diff(workspace, str(launch_id), review_id)
        if document is None:
            raise ApplicationRefusal(REVIEW_DIFF_UNAVAILABLE, "the proposed change was not stored for inspection, or its record cannot be trusted", reason="not_stored")

        def unverified(reason: str) -> ApplicationRefusal:
            return ApplicationRefusal(REVIEW_DIFF_UNVERIFIED, "the stored proposed change does not match the review it belongs to; it is not shown", reason=reason)

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
            if not view["hunks"] and has_unrendered_body(item["content"]):
                raise ApplicationRefusal(REVIEW_DIFF_UNAVAILABLE, "a file of the proposed change cannot be displayed as a diff, so the change is not offered for approval", reason="unrenderable")
            budget -= used
            files.append({"path": item["filepath"], "diff_sha256": _sha(item["content"]), **view})
        # OI-146 (owner decision 2026-10-20, strict scope): the DIFF above is the raw surface - reviewed bytes are approved
        # bytes, never redacted. The reason (policy note + the automated review, model text) is not in the candidate digest
        # and is shown through the one redaction owner like every other display text.
        reason = redact_text(str(entry.get("reason") or ""))
        policy_note, review_summary = split_reason(reason)
        data = {
            "launch_id": launch_id, "review_id": review_id, "status": str(entry.get("status")), "candidate_digest": digest, "verified": True,
            "reason": reason, "policy_note": policy_note, "review_summary": review_summary, "reason_truncated": bool(entry.get("reason_truncated")), "files": files,
            "truncated": any(f["truncated"] for f in files),
        }
        return data

    def runtime_command_review(self, ctx: RequestContext, workspace: str, launch_id: str, review_id: str) -> Dict[str, Any]:
        """The proved commands of runtime-command review ``review_id`` of launch ``launch_id`` in the admitted folder ``workspace``."""
        try:
            record = load_launch_record(workspace, str(launch_id))
        except UnreadableRunRecordError as error:
            raise ApplicationRefusal(EVIDENCE_STORE_UNREADABLE, f"the launch record cannot be trusted: {error.reason}", path_kind="launch") from error
        if record is None:
            raise ApplicationRefusal(LAUNCH_NOT_FOUND, f"no launch {launch_id} is recorded for this folder", reason="not_recorded")
        recorded = record.get("workspace")
        if (recorded.get("identity") if isinstance(recorded, dict) else None) != workspace_identity(workspace):
            raise ApplicationRefusal(REVIEW_NOT_FOUND, "that launch belongs to a different folder", reason="wrong_workspace")
        entry = _find_review(record, review_id)
        if entry is None:
            raise ApplicationRefusal(REVIEW_NOT_FOUND, f"launch {launch_id} has no review {review_id}", reason="not_recorded")
        if entry.get("kind") != KIND_RUNTIME:
            raise ApplicationRefusal(REVIEW_NOT_FOUND, f"review {review_id} is not a runtime-command review", reason="wrong_kind")

        def unverified(reason: str) -> ApplicationRefusal:
            return ApplicationRefusal(REVIEW_COMMAND_UNVERIFIED, "the recorded commands do not match the fingerprint the review is bound to; they are not shown", reason=reason)

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
        if commands_carry_secret(commands):
            # S2d (OI-153 (c)): a review recorded raw by a Kriya before S2d (S2d never records one) - its commands are not
            # shown: raw they disclose the secret, redacted they are not what the fingerprint binds.
            raise ApplicationRefusal(REVIEW_COMMAND_UNVERIFIED, "the recorded commands carry a secret on their command line; they are not shown and cannot be "
                                     "approved here", reason="commands_carry_secret")
        data = {
            "launch_id": launch_id, "review_id": review_id, "run_id": launch_id, "status": str(entry.get("status")), "verified": True,
            "command_fingerprint": digest, "unit_id": entry.get("unit_id") if isinstance(entry.get("unit_id"), str) else None,
            "criterion": str(entry.get("criterion") or ""), "commands": commands, "working_directory": cwd, "working_directory_kind": WORKING_DIRECTORY_KIND,
            "input_channel": channel, "stdin_present": stdin_sha is not None,
            "environment_names": [n for n in (entry.get("environment_names") or []) if isinstance(n, str)],
            "requested_at": str(entry.get("requested_at") or ""), "expires_at": str(entry.get("expires_at") or ""),
        }
        return data


__all__ = ["MAX_LINES_TOTAL", "ReviewService", "has_unrendered_body", "parse_unified_diff"]

"""Human review of a change inside a desktop launch (GUI final).

Kriya's change-approval gate is an in-process callback (``approval_callback``): returning True continues the workflow into its regression gates and
the apply step, returning False ends it as ``human_rejected``. That callback stays the ONE continuation point. A desktop launch has no terminal, so
instead of prompting, the callback of ``kriya generate --launch-id`` (kriya/cli.py ``on_approval``):

1. records a PENDING review in the launch record - durable, bound to the launch id, the workspace identity, the exact candidate (a digest over the
   unified diffs the gate was given) and the reviewer text it was given - and waits;
2. is answered by ``kriya launch-decision`` (the typed action below): a separate, validated, one-shot process that writes ONE decision file per
   review id, only if that review is still the pending one;
3. records the decision in the launch record BEFORE returning, so the workflow can only continue after the approval is durable.

Fail closed everywhere: a stale review id or digest, another workspace, an ended or dead launch, a duplicate, an expired review, a host that vanished
- all decline. Nothing here approves anything by itself, passes ``-y`` or changes the autonomy mode; the human decision is the only input.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from kriya.control.persistence import (
    UnreadableRunRecordError,
    load_launch_record,
    load_launch_review_decision,
    save_launch_review_decision,
)
from kriya.control.run_ownership import probe_run_lock
from kriya.control.workspace_identity import canonical_workspace, workspace_identity
from kriya.workflow.edit_safety import FileRevisionConflict

logger = logging.getLogger(__name__)

REVIEW_ID_RE = re.compile(r"^ra-[0-9a-f]{24}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DECISIONS = ("approve", "reject")
STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED = "PENDING", "APPROVED", "REJECTED", "EXPIRED"
APPROVAL_WAIT_SECONDS = 3600.0   # a review nobody answers is declined, never left holding the workspace forever
POLL_SECONDS = 0.25
REASON_MAX_CHARS = 60000
HISTORY_MAX = 20

REFUSAL_CODES = ("INVALID_REQUEST", "LAUNCH_NOT_FOUND", "LAUNCH_RECORD_UNREADABLE", "WRONG_WORKSPACE", "LAUNCH_ENDED", "LAUNCH_NOT_ACTIVE", "NOT_PENDING",
                 "STALE_REVIEW", "ALREADY_DECIDED", "REVIEW_EXPIRED", "DECISION_NOT_RECORDED")


class ApprovalRefused(Exception):
    """A decision that was not accepted. ``code`` is one of REFUSAL_CODES."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def candidate_digest(files: List[Dict[str, str]]) -> str:
    """The identity of the candidate as the gate presented it: the per-file digests of the unified diffs, in order."""
    parts = [[str(f.get("filepath", "")), _sha(str(f.get("content", "")))] for f in files]
    return _sha(json.dumps(parts, separators=(",", ":"), sort_keys=True))


def review_identity(launch_id: str, workspace_identity_value: str, sequence: int, digest: str, reason: str) -> str:
    material = "\0".join((launch_id, workspace_identity_value, str(sequence), digest, _sha(reason)))
    return "ra-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _diff_counts(content: str) -> Dict[str, int]:
    added = removed = 0
    for line in content.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"lines_added": added, "lines_removed": removed}


def build_pending(launch_id: str, workspace_identity_value: str, sequence: int, files: List[Dict[str, str]], reason: str) -> Dict[str, Any]:
    digest = candidate_digest(files)
    requested = _now()
    reason_text = reason[:REASON_MAX_CHARS]
    return {
        "review_id": review_identity(launch_id, workspace_identity_value, sequence, digest, reason), "sequence": sequence, "kind": "change_approval",
        "status": STATUS_PENDING, "requested_at": _stamp(requested), "expires_at": _stamp(requested + timedelta(seconds=APPROVAL_WAIT_SECONDS)),
        "candidate_digest": digest,
        "files": [{"filepath": str(f.get("filepath", "")), "diff_sha256": _sha(str(f.get("content", ""))), **_diff_counts(str(f.get("content", "")))} for f in files],
        "reason": reason_text, "reason_truncated": len(reason) > REASON_MAX_CHARS, "reason_sha256": _sha(reason),
        "decision": None,
    }


def valid_decision_payload(payload: Optional[Dict[str, Any]], launch_id: str, review_id: str, digest: str, workspace_identity_value: str) -> bool:
    """The waiting process re-validates what the typed action wrote; a file that does not bind every identity is not a decision."""
    return (isinstance(payload, dict) and payload.get("schema") == 1 and payload.get("launch_id") == launch_id and payload.get("review_id") == review_id
            and payload.get("candidate_digest") == digest and payload.get("workspace_identity") == workspace_identity_value and payload.get("decision") in DECISIONS)


async def await_decision(session: Any, review_id: str, digest: str) -> bool:
    """Block (cooperatively) until the review is decided, expired or abandoned; returns True ONLY for a durable approval."""
    try:
        return await _wait(session, review_id, digest)
    except BaseException:  # the terminal's own cancellation (SIGINT -> KeyboardInterrupt) or a cancelled task: the review ends undecided, and the record says so
        session.resolve_review(review_id, STATUS_EXPIRED, {"decision": None, "decided_at": _stamp(_now()), "source": "interrupted"})
        raise


async def _wait(session: Any, review_id: str, digest: str) -> bool:
    # The expiry is the WALL-CLOCK instant Kriya recorded (the same one `decide` enforces), not a monotonic timer that stops while the machine sleeps; and the host is the
    # parent this PROCESS started under (sampled when the launch opened), so a host that died before the wait began is noticed too.
    pending = session.record.get("pending_approval") or {}
    expires = datetime.fromisoformat(str(pending.get("expires_at")))
    parent = session.parent_pid
    identity = session.record["workspace"]["identity"]
    while True:
        payload = load_launch_review_decision(session.workspace, session.launch_id, review_id)
        if valid_decision_payload(payload, session.launch_id, review_id, digest, identity):
            if _now() > expires:  # consume-time expiry: a decision file that arrives after the review's end is not honoured, whoever wrote it
                session.resolve_review(review_id, STATUS_EXPIRED, {"decision": None, "decided_at": _stamp(_now()), "source": "timeout"})
                return False
            approved = payload["decision"] == "approve"
            # DURABLE BEFORE RESUME: the record says APPROVED before the workflow is allowed to continue; if that cannot be written, nothing is applied
            if session.resolve_review(review_id, STATUS_APPROVED if approved else STATUS_REJECTED, {"decision": payload["decision"], "decided_at": payload.get("decided_at"), "source": "launch-decision"}):
                return approved
            return False
        if os.getppid() != parent:
            session.resolve_review(review_id, STATUS_EXPIRED, {"decision": None, "decided_at": _stamp(_now()), "source": "host-gone"})
            return False
        if _now() > expires:
            session.resolve_review(review_id, STATUS_EXPIRED, {"decision": None, "decided_at": _stamp(_now()), "source": "timeout"})
            return False
        await asyncio.sleep(POLL_SECONDS)


def decide(workspace_path: str, launch_id: str, review_id: str, candidate_digest_value: str, decision: str) -> Dict[str, Any]:
    """The typed approval action (``kriya launch-decision``): accept ONE decision for the review that is pending right now, or refuse with a closed code."""
    from kriya.control.launch import (  # launch imports this module for its session methods
        PHASE_ENDED,
        PHASE_RUNNING,
        valid_launch_id,
    )

    if not valid_launch_id(launch_id):
        raise ApprovalRefused("INVALID_REQUEST", "launch id must have the shape YYYYMMDDTHHMMSS-xxxxxxxx")
    if not isinstance(review_id, str) or not REVIEW_ID_RE.match(review_id):
        raise ApprovalRefused("INVALID_REQUEST", "review id is malformed")
    if not isinstance(candidate_digest_value, str) or not DIGEST_RE.match(candidate_digest_value):
        raise ApprovalRefused("INVALID_REQUEST", "candidate digest is malformed")
    if decision not in DECISIONS:
        raise ApprovalRefused("INVALID_REQUEST", "decision must be approve or reject")
    workspace = canonical_workspace(workspace_path)
    try:
        record = load_launch_record(workspace, launch_id)
    except UnreadableRunRecordError as error:
        raise ApprovalRefused("LAUNCH_RECORD_UNREADABLE", f"the launch record cannot be trusted: {error.reason}") from error
    if record is None:
        raise ApprovalRefused("LAUNCH_NOT_FOUND", f"no launch {launch_id} is recorded for this folder")
    identity = workspace_identity(workspace)
    recorded = record.get("workspace") if isinstance(record.get("workspace"), dict) else {}
    if recorded.get("identity") != identity:
        raise ApprovalRefused("WRONG_WORKSPACE", "this launch belongs to a different workspace")
    if record.get("phase") == PHASE_ENDED:
        raise ApprovalRefused("LAUNCH_ENDED", "the run has already ended; there is nothing left to approve")
    pending = record.get("pending_approval")
    if record.get("phase") != PHASE_RUNNING or not isinstance(pending, dict):
        raise ApprovalRefused("NOT_PENDING", "no change is waiting for review in this launch")
    if pending.get("review_id") != review_id or pending.get("candidate_digest") != candidate_digest_value:
        raise ApprovalRefused("STALE_REVIEW", "the change under review is no longer the one you were shown; review it again")
    status = pending.get("status")
    if status != STATUS_PENDING:
        raise ApprovalRefused("REVIEW_EXPIRED" if status == STATUS_EXPIRED else "ALREADY_DECIDED", f"this review is already {str(status).lower()}")
    if _now() > datetime.fromisoformat(str(pending.get("expires_at"))):
        raise ApprovalRefused("REVIEW_EXPIRED", "this review expired; nothing was applied")
    holder = probe_run_lock(workspace)
    if holder is None or f"run_id={launch_id}," not in holder:
        raise ApprovalRefused("LAUNCH_NOT_ACTIVE", "the process that was waiting for this decision is no longer running")
    nonce = secrets.token_hex(8)
    payload = {"schema": 1, "launch_id": launch_id, "review_id": review_id, "candidate_digest": candidate_digest_value, "workspace_identity": identity,
               "decision": decision, "decided_at": _stamp(_now()), "nonce": nonce}
    try:
        save_launch_review_decision(workspace, launch_id, review_id, payload)
    except (FileExistsError, FileRevisionConflict) as error:
        raise ApprovalRefused("ALREADY_DECIDED", "this review already has a decision") from error
    stored = load_launch_review_decision(workspace, launch_id, review_id)
    if not isinstance(stored, dict) or stored.get("nonce") != nonce:
        raise ApprovalRefused("ALREADY_DECIDED", "another decision for this review was recorded first")
    return {"accepted": True, "code": "ACCEPTED", "message": f"{decision} recorded for review {review_id}; the waiting run records it and continues",
            "launch_id": launch_id, "review_id": review_id, "decision": decision, "decided_at": payload["decided_at"]}

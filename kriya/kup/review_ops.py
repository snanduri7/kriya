"""KUP ``change_approval.review``: the EXACT proposed change a review asks the person to approve (PRODUCT-UX-COMPLETION).

Transport only (full modularization B3): the launch and review identities are validated and the folder admitted here, and the answer is the
envelope. The use case (what is served and the three proofs below) is ``kriya.application.ReviewService.change_review`` (``client.reviews``
through the ``KriyaClient``; kriya/application/review.py, which also owns the diff rendering and ``MAX_LINES_TOTAL``).

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
from typing import Any, Dict, Tuple

from kriya.control.launch import valid_launch_id
from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.store import SnapshotError
from kriya.kup.workspace_ops import validated_workspace

REVIEW_ID_RE = re.compile(r"^ra-[0-9a-f]{24}$")


def validated_review(request: Dict[str, Any]) -> Tuple[str, str, str]:
    """(launch_id, review_id, canonical workspace) of a review request, or a typed INVALID_REQUEST / WORKSPACE_UNAVAILABLE."""
    launch_id, review_id = request.get("launch_id"), request.get("review_id")
    if not valid_launch_id(launch_id):
        raise SnapshotError(policy.INVALID_REQUEST, "launch_id must have the shape YYYYMMDDTHHMMSS-xxxxxxxx")
    if not isinstance(review_id, str) or not REVIEW_ID_RE.match(review_id):
        raise SnapshotError(policy.INVALID_REQUEST, "review_id must be a review identity Kriya gave (ra-<24 hex>)")
    _requested, workspace = validated_workspace(request)
    return launch_id, review_id, workspace


def review(cfg: Any, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:  # noqa: ARG001 - the operation table passes the configuration to every handler
    launch_id, review_id, workspace = validated_review(request)
    data = client.reviews.change_review(client.context(workspace_root=workspace, request_id=request_id), workspace, launch_id, review_id)
    return envelope("change_approval.review", request_id, data=data, source=None, consistency=live_consistency())

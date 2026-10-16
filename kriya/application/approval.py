"""ApprovalService (slice B3): record ONE human decision for the review pending in a folder's launch (``kriya launch-decision``, the typed
approval action of the desktop host), extracted unchanged from ``kriya/cli.py::launch_decision``.

The decision itself is AUTHORITY's: ``kriya.control.launch_approval.decide`` (reached through ``AuthorityPort.decide_review``, the public
AUTHORITY facade, since slice C3) validates the request, re-reads the launch record, refuses a
review id or candidate digest that is not the one pending right now (``STALE_REVIEW``), a review of the other kind, an expired or
already-decided review, a launch whose process is gone, and writes the decision exclusively (exactly one accepted). The service calls
that function with the reviewer's own identities and adds no check, default or fallback of its own: it never substitutes, widens or
retries a decision. A refusal is the domain's typed ``ApprovalRefused`` (code, message), unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from kriya.context.model import RequestContext
from kriya.policy.approval import ApprovalRefused
from kriya.policy.authority import AuthorityPort


@dataclass(frozen=True)
class ReviewDecisionRequest:
    """One human decision, bound to the exact review and change (or command) identity the person was shown."""

    launch_id: str
    review_id: str
    candidate_digest: str
    decision: str  # "approve" | "reject"
    kind: str      # "change_approval" | "runtime_command"


class ApprovalService:
    def decide(self, ctx: RequestContext, request: ReviewDecisionRequest) -> Dict[str, Any]:
        """Record ``request`` for the launch in ``ctx``'s workspace; raises ``ApprovalRefused`` (closed codes) when it is not accepted."""
        if ctx.workspace is None:
            raise ValueError("a launch decision is about a workspace: the request context names none")
        return AuthorityPort().decide_review(ctx.workspace.root, request.launch_id, request.review_id, request.candidate_digest, request.decision, request.kind)


__all__ = ["ApprovalRefused", "ApprovalService", "ReviewDecisionRequest"]

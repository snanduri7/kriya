"""KUP ``runtime_command.review``: the EXACT commands a pending (or decided) runtime-command review asks the person to allow Kriya to run (GUI-RUNTIME-COMMAND-APPROVAL-001).

Invariant this exists for: Kriya never asks a human to allow commands to run without making those exact commands reviewable, and the host and renderer never state a command Kriya
did not record.

Transport only (full modularization B3): the launch and review identities are validated and the folder admitted here (the change review's own
validation, ``review_ops.validated_review``), and the answer is the envelope. The use case is
``kriya.application.ReviewService.runtime_command_review`` (``client.reviews`` through the ``KriyaClient``; kriya/application/review.py). It serves ONLY what the launch process stored for that one review (``kriya/control/launch_approval.py`` ``build_pending_runtime``), after re-proving it:

1. the review exists in the launch record (pending, or decided/expired history), for this workspace identity, and is a RUNTIME-COMMAND review (a change review is not served here);
2. its recorded command fingerprint is the digest the decision must carry (``candidate_digest``) AND re-computes from the recorded working directory, commands in order, input channel and
   stdin digest - so a record that was altered, or whose displayed commands are not the ones bound to the digest, is never shown.

Any failure is a typed refusal and nothing is served. Read-only: it approves nothing; the decision is ``kriya launch-decision --kind runtime_command``, which still refuses a review id /
fingerprint that is no longer the pending one (STALE_REVIEW). No stdin or environment VALUE exists in the record, so none can be served.
"""
from __future__ import annotations

from typing import Any, Dict

from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.review_ops import validated_review


def review(cfg: Any, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:  # noqa: ARG001 - the operation table passes the configuration to every handler
    launch_id, review_id, workspace = validated_review(request)
    data = client.reviews.runtime_command_review(client.context(workspace_root=workspace, request_id=request_id), workspace, launch_id, review_id)
    return envelope("runtime_command.review", request_id, data=data, source=None, consistency=live_consistency())

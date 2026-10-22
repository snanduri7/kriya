"""How the controlled Kriya fixture reads a change review's outcome - the product's three-way contract, never a bool.

TEST INFRASTRUCTURE (see controlled_kriya.py). The real change gate (kriya/workflow/stage_apply.py) distinguishes a person's decline (the run ends ``human_rejected``) from a review
nobody decided - expired, abandoned, or a launch that could not hold it (the run ends ``approval_required``: nobody said no). The fixture used to collapse the awaited
``ApprovalOutcome`` to ``.approved`` and call everything that was not an approval a human rejection, scripting exactly the conflation the product no longer makes (1-D).
Tests: tests/test_ui_controlled_review.py.
"""
from typing import Any, Callable, Optional

from kriya.policy.approval import APPROVAL_LAUNCH_CANNOT_ASK, ApprovalOutcome


def held_outcome(awaiting: Optional[Any], run: Callable[[Any], Any]) -> ApprovalOutcome:
    """The outcome of the review the launch held. ``awaiting`` is what ``kriya.control.launch.request_change_approval`` returned (the awaitable of the human's outcome); None means no
    review could be held, which the product's callback (kriya/cli.py::_approval_decision) answers UNAVAILABLE - not a decline. ``run`` drives the awaitable (asyncio.run)."""
    if awaiting is None:
        return ApprovalOutcome.unavailable_because(APPROVAL_LAUNCH_CANNOT_ASK)
    outcome = run(awaiting)
    if not isinstance(outcome, ApprovalOutcome):
        raise TypeError(f"the review resolved to {type(outcome).__name__}, not an ApprovalOutcome")
    return outcome


def stop_status(outcome: ApprovalOutcome) -> Optional[str]:
    """The run status a review that did not approve ends with (the same mapping as stage_apply): DECLINED -> human_rejected, UNAVAILABLE -> approval_required; None when approved."""
    if outcome.approved:
        return None
    return "human_rejected" if outcome.declined else "approval_required"


def event_outcome(outcome: ApprovalOutcome) -> str:
    """The ``approval.decision`` event's ``outcome`` vocabulary (recorded traces keep it): a decline is spelled "rejected"."""
    return "approved" if outcome.approved else "rejected" if outcome.declined else "unavailable"

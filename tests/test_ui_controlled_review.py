"""1-D (Wave-1 final fix-forward): the desktop test fixture scripts a change review's outcome the way the product does.

ui/standalone/test/fixtures/controlled_kriya.py replaces only the model-driven workflow body; the review it waits on is the REAL launch review. It used to read the awaited
``ApprovalOutcome`` as ``.approved`` and end every non-approval as ``human_rejected``, so an expired or unrecordable review (UNAVAILABLE: nobody said no) was scripted as a human
rejection - the conflation the product no longer makes (kriya/workflow/stage_apply.py: DECLINED -> human_rejected, UNAVAILABLE -> approval_required).
The mapping lives in fixtures/controlled_review.py so these tests can drive it without a launch; the real approve and reject paths still run end to end through the fixture in
ui/standalone/test/execution_real.test.ts.
"""
import asyncio
import importlib.util
import os
import re

import pytest

from kriya.policy.approval import APPROVAL_REVIEW_EXPIRED, ApprovalOutcome

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "standalone", "test", "fixtures")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(FIXTURES, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review = _load("controlled_review")


def _awaiting(outcome):
    async def resolved():
        return outcome
    return resolved()


APPROVED, DECLINED = ApprovalOutcome.approve(), ApprovalOutcome.decline()
EXPIRED = ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED)


@pytest.mark.parametrize("outcome,stop,event", [(APPROVED, None, "approved"), (DECLINED, "human_rejected", "rejected"), (EXPIRED, "approval_required", "unavailable")],
                         ids=["approved", "human-reject", "expired"])
def test_each_review_outcome_has_its_own_run_status_and_event_outcome(outcome, stop, event):
    held = review.held_outcome(_awaiting(outcome), asyncio.run)
    assert held is outcome
    assert review.stop_status(held) == stop and review.event_outcome(held) == event


def test_a_launch_that_could_not_hold_the_review_is_unavailable_not_a_human_rejection():
    """``request_change_approval`` returns None when no review could be recorded; the product's callback answers UNAVAILABLE for that (kriya/cli.py::_approval_decision)."""
    held = review.held_outcome(None, asyncio.run)
    assert held.unavailable and not held.declined and review.stop_status(held) == "approval_required"


def test_an_awaited_value_that_is_not_an_approval_outcome_is_refused_loudly():
    for value in (True, False, None):
        with pytest.raises(TypeError):
            review.held_outcome(_awaiting(value), asyncio.run)


def test_the_fixture_reads_every_change_review_through_the_helper_and_never_as_a_bool():
    """Structural tripwire: no ``asyncio.run(awaiting).approved`` shortcut (which maps a missing review to a decline) and no ``"human_rejected" if rejected`` conflation survive."""
    source = open(os.path.join(FIXTURES, "controlled_kriya.py"), encoding="utf-8").read()
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    assert len(re.findall(r"held_outcome\(awaiting, asyncio\.run\)", code)) == 2, "both change-review sites (greenfield_flow, review) use the helper"
    assert not re.search(r"asyncio\.run\(awaiting\)\.approved", code)
    assert not re.search(r'"human_rejected"\s+if\s+rejected', code)
    assert "stop_status(review)" in code and "event_outcome(review)" in code

"""Independent review (2026-10-15) B4: a PASSED ``original_requirements`` gate does not by itself mean the requested behavior was verified - under the default record policies an UNVERIFIED /
UNKNOWN / PENDING requirement does not block. The terminal gate now records ``requirements_verified`` beside it, true only when every proposition-bearing requirement was positively verified."""
from unittest.mock import patch

import pytest

from kriya.workflow import terminal_gate_service as tgs
from kriya.workflow.requirements import RequirementOutcome as R
from kriya.workflow.terminal_gate_service import TerminalGateReport, requirements_fully_verified

OK = [R.SATISFIED, R.CLOSED_BY_EVIDENCE, R.HUMAN_ACCEPTED]


def test_every_requirement_positively_verified_is_verified():
    assert requirements_fully_verified(OK, None) is True
    assert requirements_fully_verified([R.SATISFIED, R.NOT_A_CLAIM], None) is True       # a non-claim neither counts nor blocks


@pytest.mark.parametrize("bad", [R.UNVERIFIED, R.UNKNOWN, R.PENDING, R.VIOLATED, R.DISPOSITIONED])
def test_one_requirement_that_is_not_verified_means_not_verified(bad):
    assert requirements_fully_verified([R.SATISFIED, bad], None) is False


def test_no_requirements_a_gap_or_only_non_claims_is_not_verified():
    assert requirements_fully_verified([], None) is False
    assert requirements_fully_verified(OK, "a requirement is violated") is False
    assert requirements_fully_verified([R.NOT_A_CLAIM], None) is False


@pytest.mark.parametrize("verified", [True, False])
def test_the_terminal_record_carries_the_flag_next_to_the_gate_result(verified):
    emitted = []
    with patch.object(tgs.attempt_evidence_scope, "capture_mode", return_value="full"), \
            patch.object(tgs.attempt_evidence_scope, "emit", side_effect=lambda kind, payload, content=None: emitted.append((kind, payload))):
        tgs._record_terminal_report(TerminalGateReport(ran=True, requirements_verified=verified))
    by_gate = {p["gate"]: p for _k, p in emitted}
    assert by_gate["original_requirements"]["requirements_verified"] is verified and by_gate["original_requirements"]["success"] is True
    assert all("requirements_verified" not in p for g, p in by_gate.items() if g != "original_requirements")

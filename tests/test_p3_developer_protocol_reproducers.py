"""P3 reproducers, pinned to today's behaviour (handover/P3_DEVELOPER_PROTOCOL_DECOMPOSITION.md).

Each test passes now and describes the defect on the live bytes of the LR-R1
R2 runs (tests/_p3_reproducers.py). The required behaviour is asserted by
handover/evidence/p3/test_p3_required_behaviour.py (run explicitly; it fails
until the unit is fixed, then moves here replacing the pin - the P1/P4/P5/FS-1
convention; no xfail).
"""
from _p3_reproducers import (
    ARRAYFILL,
    LIVE,
    MEMBER_UNITS,
    anchor_outside_retry,
    candidate_added_lines,
    prose_end_to_end,
    prose_on_unchanged_line,
    stitched_anchor_retry,
)

from kriya.workflow.file_resolution import find_explanatory_prose_contamination


def test_p3a_an_anchor_copied_from_the_whole_file_the_request_carried_is_refused(tmp_path, monkeypatch):
    """R2-T4 s1 a4: the retry request carries the whole current ArrayFill.java
    (planned-source section) and the anchor verbatim; it is still refused."""
    reason, sent = anchor_outside_retry(tmp_path, monkeypatch)
    assert ARRAYFILL in sent and LIVE["r2_t4_a4_outside_search"] in sent   # the model was shown it, exactly
    assert LIVE["r2_t4_a4_outside_search"] in ARRAYFILL                    # and it is real current source
    assert reason == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"


def test_p3a_negative_control_an_anchor_never_sent_is_refused(tmp_path, monkeypatch):
    reason, sent = anchor_outside_retry(tmp_path, monkeypatch, planned_source=False)
    assert LIVE["r2_t4_a4_outside_search"] not in sent
    assert reason == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"


def test_p3b_a_stitched_anchor_leaves_the_capability_unchanged_and_the_retry_is_refused_unsent(tmp_path):
    """R2-T4 s1 a1 -> a2: the SEARCH joins the two shown member units across
    the Javadoc between them; the retry is refused before any model call."""
    reason, outcome, first, second = stitched_anchor_retry(tmp_path)
    assert reason == "ANCHOR_NOT_IN_FILE"
    assert [(s.start_line, s.end_line) for s in first.spans] == list(MEMBER_UNITS)
    assert outcome == "ANCHOR_CONTEXT_NOT_ESCALATED" and second is None
    gap = ARRAYFILL.splitlines()[MEMBER_UNITS[0][1]:MEMBER_UNITS[1][0] - 1]   # lines 196-204
    assert any("/**" in line for line in gap)                                 # the Javadoc never shown


def test_p3c_the_prose_check_flags_a_line_of_the_unchanged_base_file():
    """R2-T2 s1 a2: the flagged line is in the base file; nothing the
    candidate added matches a prose pattern."""
    flagged = prose_on_unchanged_line()
    assert flagged and "This method is deprecated" in flagged
    added = "\n".join(candidate_added_lines())
    assert "This method is deprecated" not in added
    assert find_explanatory_prose_contamination("cssselect2/tree.py", added) is None


def test_p3c_every_attempt_with_the_live_response_is_rejected_end_to_end(tmp_path, monkeypatch):
    passed, failures, final = prose_end_to_end(tmp_path, monkeypatch)
    assert passed is False and "def depth" not in final
    assert failures.count("prose_contamination") >= 3

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


def test_p3a_an_anchor_in_exact_current_source_the_request_carried_is_authorized(tmp_path, monkeypatch):
    """R2-T4 s1 a4 (fixed by P3-A): the retry request carried the whole
    current ArrayFill.java verbatim (planned-source section, kept by the
    fit) and the model anchored on it - authorized."""
    reason, sent = anchor_outside_retry(tmp_path, monkeypatch)
    assert ARRAYFILL in sent and LIVE["r2_t4_a4_outside_search"] in sent
    assert reason is None


def test_p3a_negative_control_an_anchor_never_sent_is_refused(tmp_path, monkeypatch):
    reason, sent = anchor_outside_retry(tmp_path, monkeypatch, planned_source=False)
    assert LIVE["r2_t4_a4_outside_search"] not in sent
    assert reason == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"


def test_p3b_the_retry_shows_the_lines_between_the_stitched_parts(tmp_path):
    """R2-T4 s1 a1 -> a2 (fixed by P3-B): the SEARCH joined two shown member
    units across the Javadoc between them. The anchor is still refused, but
    the retry's capability now shows exactly the left-out lines - real new
    context, so the retry is sent rather than refused unsent."""
    reason, outcome, first, second = stitched_anchor_retry(tmp_path)
    assert reason == "ANCHOR_NOT_IN_FILE"
    assert outcome is None and second.digest != first.digest
    gap = range(MEMBER_UNITS[0][1] + 1, MEMBER_UNITS[1][0])   # 196..204
    assert all(any(s.start_line <= line <= s.end_line for s in second.spans) for line in gap)
    assert all(span.text in ARRAYFILL for span in second.spans)


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

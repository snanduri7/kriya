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


def test_p3c_a_line_of_the_unchanged_base_is_not_candidate_contamination():
    """R2-T2 s1 (fixed by P3-C): the flagged line is in the unchanged base."""
    assert prose_on_unchanged_line() is None


def test_p3c_the_live_response_is_accepted_end_to_end(tmp_path, monkeypatch):
    passed, failures, final = prose_end_to_end(tmp_path, monkeypatch)
    assert "prose_contamination" not in failures
    assert passed is True and "def depth" in final


def test_p3c_negative_controls_still_reject_prose_the_candidate_wrote(tmp_path, monkeypatch):
    added_prose = LIVE["r2_t2_a2_developer_response"].replace(
        "        return len(self.ancestors)", "        The fix is to count the ancestors.\n        return len(self.ancestors)")
    passed, failures, _ = prose_end_to_end(tmp_path, monkeypatch, response=added_prose)
    assert passed is False and "prose_contamination" in failures
    # A new file is all candidate text: a prose line in it is still contamination.
    assert find_explanatory_prose_contamination("pkg/new.py", "The fix is to count.\n") is not None

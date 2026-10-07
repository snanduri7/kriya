"""P3-B PRE-FIX PIN (moved from tests/test_p3_developer_protocol_reproducers.py with the fix;
evidence, passes only on the pre-fix revision: handover/evidence/p3/prefix_9b8b328.txt).
Run: PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/p3/test_p3b_prefix_pin.py"""
from _p3_reproducers import ARRAYFILL, MEMBER_UNITS, stitched_anchor_retry


def test_p3b_a_stitched_anchor_leaves_the_capability_unchanged_and_the_retry_is_refused_unsent(tmp_path):
    """R2-T4 s1 a1 -> a2: the SEARCH joins the two shown member units across
    the Javadoc between them; the retry is refused before any model call."""
    reason, outcome, first, second = stitched_anchor_retry(tmp_path)
    assert reason == "ANCHOR_NOT_IN_FILE"
    assert [(s.start_line, s.end_line) for s in first.spans] == list(MEMBER_UNITS)
    assert outcome == "ANCHOR_CONTEXT_NOT_ESCALATED" and second is None
    gap = ARRAYFILL.splitlines()[MEMBER_UNITS[0][1]:MEMBER_UNITS[1][0] - 1]   # lines 196-204
    assert any("/**" in line for line in gap)                                 # the Javadoc never shown

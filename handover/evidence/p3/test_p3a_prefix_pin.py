"""P3-A PRE-FIX PIN (moved from tests/test_p3_developer_protocol_reproducers.py with the fix;
evidence, passes only on the pre-fix revision: handover/evidence/p3/prefix_9b8b328.txt).
Run: PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/p3/test_p3a_prefix_pin.py"""
from _p3_reproducers import ARRAYFILL, LIVE, anchor_outside_retry


def test_p3a_an_anchor_copied_from_the_whole_file_the_request_carried_is_refused(tmp_path, monkeypatch):
    """R2-T4 s1 a4: the retry request carries the whole current ArrayFill.java
    (planned-source section) and the anchor verbatim; it is still refused."""
    reason, sent = anchor_outside_retry(tmp_path, monkeypatch)
    assert ARRAYFILL in sent and LIVE["r2_t4_a4_outside_search"] in sent   # the model was shown it, exactly
    assert LIVE["r2_t4_a4_outside_search"] in ARRAYFILL                    # and it is real current source
    assert reason == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"

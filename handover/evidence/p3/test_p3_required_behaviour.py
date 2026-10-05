"""P3 required behaviour (evidence; run explicitly, fails until each unit is fixed:
PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/p3/test_p3_required_behaviour.py).
Each test moves into tests/test_p3_developer_protocol_reproducers.py with its fix (no xfail)."""
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


# ---- P3-A: anchor authority is decided on the request actually sent
def test_p3a_an_anchor_in_exact_current_source_the_request_carried_is_authorized(tmp_path, monkeypatch):
    reason, sent = anchor_outside_retry(tmp_path, monkeypatch)
    assert LIVE["r2_t4_a4_outside_search"] in sent
    assert reason is None


def test_p3a_an_anchor_never_sent_stays_refused(tmp_path, monkeypatch):
    reason, _ = anchor_outside_retry(tmp_path, monkeypatch, planned_source=False)
    assert reason == "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"


# ---- P3-B: a stitched anchor escalates with the lines between its parts
def test_p3b_the_retry_shows_the_lines_between_the_stitched_parts(tmp_path):
    reason, outcome, first, second = stitched_anchor_retry(tmp_path)
    assert reason == "ANCHOR_NOT_IN_FILE"                 # the anchor itself is still refused
    assert outcome is None and second.digest != first.digest
    gap = range(MEMBER_UNITS[0][1] + 1, MEMBER_UNITS[1][0])   # 196..204
    assert all(any(s.start_line <= line <= s.end_line for s in second.spans) for line in gap)
    assert all(span.text in ARRAYFILL for span in second.spans)  # exact current bytes only


# ---- P3-C: only what the candidate wrote can be prose contamination
def test_p3c_a_line_of_the_unchanged_base_is_not_candidate_contamination():
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


def test_the_suite_isolation_applies_here():
    import os

    from kriya.core.state_paths import ENV_STATE_DIR

    home = os.path.expanduser("~/.kriya")
    for variable in (ENV_STATE_DIR, "KRIYA_LOG_DIR", "KRIYA_QUALIFICATION_HOME"):
        assert os.environ.get(variable) and not os.environ[variable].startswith(home), variable
    assert os.environ.get("KRIYA_MODEL_RUNTIME_PROBE") == "0"

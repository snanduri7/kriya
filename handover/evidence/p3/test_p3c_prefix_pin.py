"""P3-C PRE-FIX PINS (moved from tests/test_p3_developer_protocol_reproducers.py with the fix;
evidence, pass only on the pre-fix revision: handover/evidence/p3/prefix_9b8b328.txt).
Run: PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/p3/test_p3c_prefix_pin.py"""
from _p3_reproducers import candidate_added_lines, prose_end_to_end, prose_on_unchanged_line

from kriya.workflow.file_resolution import find_explanatory_prose_contamination


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

"""P3-C: the explanatory-prose check judges only lines the candidate added
(count-based against the file before the write). The live R2-T2 case end to
end is in tests/test_p3_developer_protocol_reproducers.py."""
from kriya.workflow.file_resolution import find_explanatory_prose_contamination

PROSE = "    This method is deprecated and the fix is to use ancestors."
BASE = f"def a():\n    return 1\n{PROSE}\n"


def _flagged(content, baseline):
    return find_explanatory_prose_contamination("pkg/tree.py", content, baseline)


def test_an_unchanged_baseline_prose_line_is_not_the_candidates():
    assert _flagged(BASE + "def b():\n    return 2\n", BASE) is None


def test_prose_the_candidate_adds_is_still_rejected():
    assert _flagged(BASE + "The fix is to count the ancestors.\n", BASE) is not None


def test_a_baseline_prose_line_the_candidate_duplicates_is_judged():
    """Counted, not merely present: the second copy is the candidate's."""
    assert _flagged(BASE + PROSE + "\n", BASE) is not None


def test_a_new_file_has_no_baseline_every_line_is_judged():
    assert _flagged(BASE, None) is not None
    assert _flagged(BASE, "") is not None


def test_a_moved_baseline_line_is_still_unchanged_text():
    moved = f"{PROSE}\ndef a():\n    return 1\n"
    assert _flagged(moved, BASE) is None

"""P3-B: a SEARCH block stitched from real, unique, in-order runs of the file
is located with the lines it left out between them - and only such a block.
The live R2-T4 sequence end to end is in tests/test_p3_developer_protocol_reproducers.py."""
from kriya.workflow.edit_capability import build_edit_capability, locate_search_text

LINES = [f"value_{i} = compute_{i}(alpha, beta)" for i in range(1, 31)]


def _block(*ranges):
    return "\n".join(LINES[start - 1] for first, last in ranges for start in range(first, last + 1))


def test_two_stitched_runs_locate_both_and_every_line_between():
    assert locate_search_text(LINES, _block((3, 5), (11, 13))) == list(range(3, 14))


def test_three_stitched_runs_locate_from_the_first_to_the_last():
    assert locate_search_text(LINES, _block((2, 3), (7, 8), (20, 21))) == list(range(2, 22))


def test_a_contiguous_block_is_still_located_whole_and_nothing_more():
    assert locate_search_text(LINES, _block((4, 9))) == list(range(4, 10))


def test_parts_out_of_order_are_not_a_stitch_and_keep_the_per_line_rule():
    assert locate_search_text(LINES, _block((11, 13), (3, 5))) == [11, 12, 13, 3, 4, 5]


def test_an_altered_block_keeps_the_per_line_rule():
    """Measured identical before P3-B: the verbatim lines, and the altered
    one only when it shares enough identifiers with a unique line."""
    altered = _block((3, 4)) + "\nvalue_5 = recompute_5(alpha, gamma)"
    assert locate_search_text(LINES, altered) == [3, 4]


def test_a_part_that_occurs_more_than_once_is_not_a_stitch():
    lines = ["a = f(x, y)", "b = g(x, y)", "c = h(x, y)", "a = f(x, y)", "b = g(x, y)"]
    assert locate_search_text(lines, "a = f(x, y)\nc = h(x, y)") == [1, 4, 3]


def test_a_gap_that_cannot_fit_leaves_no_feasible_operation_a_typed_refusal_not_a_fake_escalation():
    """Gap loci the window budget cannot cover stay uncovered: the capability
    offers no anchored edit (CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE before
    inference), never an unchanged retry."""
    content = "\n".join(LINES) + "\n"
    loci = locate_search_text(LINES, _block((1, 2), (29, 30)))
    assert loci == list(range(1, 31))
    tight = build_edit_capability("a.py", content, full_file=False, loci=loci, budget_chars=200)
    assert tight.uncovered_loci and not tight.feasible
    roomy = build_edit_capability("a.py", content, full_file=False, loci=loci, budget_chars=100_000)
    assert roomy.feasible and not roomy.uncovered_loci

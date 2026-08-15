from kriya.workflow.review_context import build_review_batches


def test_build_review_batches_small_content_single_batch_no_truncation():
    batches, truncated = build_review_batches([("a.py", "x = 1\n"), ("b.py", "y = 2\n")], budget=5000)

    assert len(batches) == 1
    assert "a.py" in batches[0] and "b.py" in batches[0]
    assert truncated == []


def test_build_review_batches_oversized_single_file_is_truncated_and_reported():
    big_content = "\n".join(f"x_{i} = {i}  # padding line number {i}" for i in range(200))

    batches, truncated = build_review_batches([("big.py", big_content)], budget=375)

    assert truncated == ["big.py"]
    assert len(batches) == 1
    assert "TRUNCATED" in batches[0]
    assert "x_199" not in batches[0]  # the tail never made it in


def test_build_review_batches_splits_across_multiple_batches_when_over_budget():
    padding = "\n".join(f"x_{i} = {i}  # padding" for i in range(30))
    files = [("a.py", padding), ("b.py", padding.replace("x_", "y_"))]

    batches, truncated = build_review_batches(files, budget=232)

    assert len(batches) == 2
    assert truncated == []
    assert "a.py" in batches[0] and "b.py" not in batches[0]
    assert "b.py" in batches[1] and "a.py" not in batches[1]


def test_build_review_batches_empty_input_returns_no_batches():
    batches, truncated = build_review_batches([], budget=5000)

    assert batches == []
    assert truncated == []

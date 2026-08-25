from kriya.workflow.review_context import build_review_batches, build_reviewer_verified_evidence


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


def test_build_reviewer_verified_evidence_includes_a_passing_run_verification():
    """Regression test for a real live bug, 2026-08-25 (ignite_qpid_protocol,
    a real Ignite+Qpid Java app): with zero visibility into what already
    passed, ReviewerAgent confidently fabricated specific runtime exceptions
    (an IgniteException, a NoClassDefFoundError) for code that had, moments
    earlier in the same run, actually compiled and RUN successfully -
    directly contradicted by real evidence Kriya already had on hand but
    never showed the Reviewer."""
    gate_outcomes = [
        {"type": "compile", "success": True, "output": "BUILD SUCCESS"},
        {
            "type": "run_verification", "success": True,
            "output": "[VERIFICATION] PASS\n\n[Grader reasoning]: matched expected output",
        },
    ]
    evidence = build_reviewer_verified_evidence(gate_outcomes)

    assert "ACTUALLY RAN" in evidence
    assert "[VERIFICATION] PASS" in evidence
    assert "do not contradict it" in evidence
    # Compile isn't surfaced separately - "Files generated" reaching Review
    # already implies it, and it adds no comparably falsifiable evidence.
    assert "BUILD SUCCESS" not in evidence


def test_build_reviewer_verified_evidence_includes_a_passing_spec_compliance_check():
    gate_outcomes = [
        {"type": "goal_spec_compliance", "success": True, "output": "All named fields present."},
    ]
    evidence = build_reviewer_verified_evidence(gate_outcomes)

    assert "Goal spec compliance check PASSED" in evidence
    assert "All named fields present." in evidence


def test_build_reviewer_verified_evidence_ignores_a_failed_gate_outcome():
    """Only a real PASS is evidence worth surfacing - a failed attempt from
    an earlier retry says nothing trustworthy about the final, successful
    one being reviewed."""
    gate_outcomes = [
        {"type": "run_verification", "success": False, "output": "boom"},
    ]
    evidence = build_reviewer_verified_evidence(gate_outcomes)

    assert evidence == ""


def test_build_reviewer_verified_evidence_returns_empty_string_when_nothing_to_report():
    """A goal with no runtime-observable behavior (run_verification never
    ran at all) must never fabricate a claim of its own."""
    gate_outcomes = [{"type": "compile", "success": True, "output": "ok"}]
    evidence = build_reviewer_verified_evidence(gate_outcomes)

    assert evidence == ""

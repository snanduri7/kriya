"""BACKEND-READINESS-004 registry sweep proofs for rows closed by later architecture.

PYTHON-LIVE-RELIABILITY-001 (CAGC-0 Python sample, 2026-10-03): 6 of 16 runs ended regression_unattributed on the
shape 'level-1 CHANGED_FAILURE with one of two failing target tests resolved and the other unchanged'. Under the
whole-output authority of that date a changed invocation fingerprint blocked alone. REG-R1 (merged 2026-10-07) made
level 1 diagnostic only for a pytest comparison with complete per-test evidence on both sides: the measured shape no
longer blocks - the candidate has progressed, nothing regressed, and the still-failing target test is an ordinary
candidate failure for the next attempt. This test is the deterministic reproducer of that exact shape.
"""
from test_reg_r1_pytest_regression_authority import KEY, OTHER, PASSED, D, F, decide


def test_one_target_resolved_and_the_other_still_failing_is_progress_not_an_unattributed_regression():
    delta, _ = decide({KEY: F("boom", "b"), OTHER: F("same", "s")}, {KEY: PASSED, OTHER: F("same", "s")})
    assert delta.level1.classification == D.CHANGED_FAILURE  # the whole output differs (diagnostic only)
    assert delta.authority == "pytest_per_test" and not delta.blocking and delta.blocking_reasons == ()
    assert delta.level2 == {"tests/t.py::test_x": D.RESOLVED_FAILURE, "tests/t.py::test_y": D.PRE_EXISTING_FAILURE}
    # the same shape with incomplete per-test evidence falls back to the whole-output authority and blocks (fail closed)
    from test_reg_r1_pytest_regression_authority import (
        build_validation_outcome,
        classify_baseline_delta,
        make_baseline,
        raw_result,
    )
    incomplete = classify_baseline_delta(make_baseline({KEY: F(), OTHER: F()}),
                                         build_validation_outcome(raw_result({KEY: PASSED, OTHER: F()}, complete=False)))
    assert incomplete.blocking and incomplete.authority == "whole_output"

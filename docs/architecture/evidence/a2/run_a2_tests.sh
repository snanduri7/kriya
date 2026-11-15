#!/bin/sh
# Slice A2: seven committed verifier-semantic tests, run unchanged on the canonical baseline (no code change).
# Usage (from the closure worktree root): sh docs/architecture/evidence/a2/run_a2_tests.sh <venv python> <state dir>
F=tests/test_verifier_semantic_loop_001.py
PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 KRIYA_STATE_DIR="$2" KRIYA_LOG_DIR="$2" KRIYA_MODEL_RUNTIME_PROBE=0 \
"$1" -m pytest -q -p no:cacheprovider \
  "$F::test_unattended_a_failing_run_authored_verifier_stops_for_review_before_any_retry" \
  "$F::test_unattended_a_passing_run_authored_verifier_is_not_applied" \
  "$F::test_a_direct_run_never_regenerates_expectation_invalid_is_needs_review_and_changes_nothing" \
  "$F::test_a_direct_run_with_expectation_valid_has_no_validated_plan_owner_and_is_needs_review" \
  "$F::test_a_failure_with_no_run_authored_executed_verifier_keeps_the_historical_retry" \
  "$F::test_only_the_enforce_loop_gives_a_unit_a_structured_plan" \
  "$F::test_a_ruled_failure_is_never_handed_to_failure_attribution" 2>&1 | tail -n 4

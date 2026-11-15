#!/bin/sh
# Slice A3 evidence run: the probe, then the committed deterministic tests A3 cites. Model-free, isolated state.
# Usage: sh docs/architecture/evidence/a3/run_a3.sh <python> <closure worktree> <scratch dir>
set -u
PY="$1"; ROOT="$2"; SCRATCH="$3"
mkdir -p "$SCRATCH/state" "$SCRATCH/logs"
export PYTHONPATH="$ROOT" PYTHONDONTWRITEBYTECODE=1 KRIYA_STATE_DIR="$SCRATCH/state" KRIYA_LOG_DIR="$SCRATCH/logs" KRIYA_MODEL_RUNTIME_PROBE=0
OUT="$ROOT/docs/architecture/evidence/a3"
"$PY" "$OUT/a3_probe.py" > "$OUT/a3_probe_output.txt" 2>&1
echo "probe exit: $?"
"$PY" -m pytest -q -p no:cacheprovider \
  "$ROOT/tests/test_project_continuity_two_run.py::test_F_the_requirement_closure_judges_the_candidate_against_the_project_and_does_not_record_success" \
  "$ROOT/tests/test_project_continuity_two_run.py::test_F_with_the_unverified_policy_blocking_the_substitute_is_refused_and_nothing_is_applied" \
  > "$OUT/a3_tests_output.txt" 2>&1
echo "tests exit: $?"
tail -n 3 "$OUT/a3_tests_output.txt"

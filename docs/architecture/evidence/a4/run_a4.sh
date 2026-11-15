#!/bin/sh
# Slice A4 evidence run: the probe, then the committed deterministic tests A4 cites. Model-free, isolated state, no network.
# Usage: sh docs/architecture/evidence/a4/run_a4.sh <python> <closure worktree> <scratch dir>
set -u
PY="$1"; ROOT="$2"; SCRATCH="$3"
mkdir -p "$SCRATCH/state" "$SCRATCH/logs"
export PYTHONPATH="$ROOT" PYTHONDONTWRITEBYTECODE=1 KRIYA_STATE_DIR="$SCRATCH/state" KRIYA_LOG_DIR="$SCRATCH/logs" KRIYA_MODEL_RUNTIME_PROBE=0
OUT="$ROOT/docs/architecture/evidence/a4"
"$PY" "$OUT/a4_probe.py" > "$OUT/a4_probe_output.txt" 2>&1
echo "probe exit: $?"
"$PY" -m pytest -q -p no:cacheprovider \
  "$ROOT/tests/test_greenfield_runtime_verification_dropped.py" \
  "$ROOT/tests/test_plan_external_package_capability_001.py" \
  > "$OUT/a4_tests_output.txt" 2>&1
echo "tests exit: $?"
tail -n 3 "$OUT/a4_tests_output.txt"

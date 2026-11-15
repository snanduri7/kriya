#!/bin/sh
# Slice A6 evidence run: the probe, then the citation / negative-control script. Model-free, isolated state, no network.
# (The probe sets its own scratch HOME, authority home and MCP-approval home before it imports Kriya.)
# Usage: sh docs/architecture/evidence/a6/run_a6.sh <python> <closure worktree> <scratch dir>
set -u
PY="$1"; ROOT="$2"; SCRATCH="$3"
mkdir -p "$SCRATCH/state" "$SCRATCH/logs"
export PYTHONPATH="$ROOT:$ROOT/tests" PYTHONDONTWRITEBYTECODE=1 KRIYA_STATE_DIR="$SCRATCH/state" KRIYA_LOG_DIR="$SCRATCH/logs" KRIYA_MODEL_RUNTIME_PROBE=0
OUT="$ROOT/docs/architecture/evidence/a6"
"$PY" "$OUT/a6_probe.py" > "$OUT/a6_probe_output.txt" 2>&1
echo "probe exit: $?"
python3 "$OUT/check_citations_a6.py" "$ROOT" > "$OUT/check_citations_a6_output.txt" 2>&1
echo "citations exit: $?"
tail -n 3 "$OUT/check_citations_a6_output.txt"

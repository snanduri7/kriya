#!/usr/bin/env bash
# PRD-036 canary: one real production-profile run of the frozen release
# candidate (the demo-03 brownfield fix: a Spring Boot service, 53 existing
# tests), with the exact operator production config, through the enforce
# controller, plus leak checks before and after. Never reuses an old result.
# It runs twice on the same candidate and config: run 1 from a fresh workspace,
# run 2 from run 1's state with only the tracked files restored. Run 2 proves
# the reusable worktrees are reused, not multiplied (OUT_DIR/run-1, run-2; the
# combined verdict is OUT_DIR/canary.json).
#
#   scripts/prd036_canary.sh OUT_DIR
#
# Environment: KRIYA_RELEASE_CANDIDATE (frozen identity), KRIYA_RELEASE_CONFIG,
# KRIYA_RELEASE_TRUST_FILE, KRIYA_RELEASE_WORKSPACE (the approved canary
# workspace; reset here to the baseline), KRIYA_CANARY_BASELINE (the baseline
# git repository), KRIYA_CANARY_GOAL (goal file), KRIYA_CANARY_EXPECT (the one
# path the goal authorizes changing).
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PY="$ROOT/.venv/bin/python"
KRIYA="$ROOT/.venv/bin/kriya"
OUT="${1:?usage: prd036_canary.sh OUT_DIR}"
mkdir -p "$OUT" && OUT="$(cd "$OUT" && pwd)"
for v in KRIYA_RELEASE_CANDIDATE KRIYA_RELEASE_CONFIG KRIYA_RELEASE_TRUST_FILE KRIYA_RELEASE_WORKSPACE \
         KRIYA_CANARY_BASELINE KRIYA_CANARY_GOAL KRIYA_CANARY_EXPECT; do
  [ -n "${!v:-}" ] || { echo "[canary] $v is not set" >&2; exit 2; }
done
WS="$KRIYA_RELEASE_WORKSPACE"
BASE_SHA="$(git -C "$KRIYA_CANARY_BASELINE" rev-parse HEAD)" || exit 2

# A fresh workspace at the baseline: clone once, then reset (never rm -rf).
# The reset removes every worktree an earlier run left (git clean cannot) and
# refuses to go on unless only the main worktree is left.
if [ ! -d "$WS/.git" ]; then
  git clone -q "$KRIYA_CANARY_BASELINE" "$WS" || exit 2
fi
[ "$(git -C "$WS" config --get remote.origin.url)" = "$KRIYA_CANARY_BASELINE" ] \
  || { echo "[canary] $WS is not a clone of $KRIYA_CANARY_BASELINE" >&2; exit 2; }
"$PY" scripts/prd036_canary.py reset "$WS" "$BASE_SHA" | tee "$OUT/reset.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "[canary] could not make the workspace fresh" >&2; exit 2; }

"$PY" scripts/release_candidate.py check --recorded "$KRIYA_RELEASE_CANDIDATE" > "$OUT/release-candidate-check.json" \
  || { cat "$OUT/release-candidate-check.json"; echo "[canary] the frozen release candidate is not CURRENT" >&2; exit 2; }
cp "$KRIYA_RELEASE_CANDIDATE" "$OUT/release-candidate.json"
kriya_cli() { ( cd "$WS" && "$KRIYA" --config "$KRIYA_RELEASE_CONFIG" --trust-file "$KRIYA_RELEASE_TRUST_FILE" "$@" ); }

run_pass() {  # run_pass N START...: one full canary run into $OUT/run-N (START: --fresh | --continues-from DIR)
  local N="$1" RUN="$OUT/run-$1"
  shift
  local START=("$@")
  mkdir -p "$RUN"
  cp "$OUT/release-candidate-check.json" "$OUT/release-candidate.json" "$RUN/"
  kriya_cli doctor --production --json > "$RUN/doctor-before.json" 2> "$RUN/doctor-before.stderr.txt"
  "$PY" scripts/prd036_canary.py snapshot "$WS" "$RUN/before.json" || return 2
  echo "[canary] $(date -u +%Y-%m-%dT%H:%M:%SZ) run $N start (baseline $BASE_SHA)" | tee "$RUN/timeline.txt"
  kriya_cli generate -f "$KRIYA_CANARY_GOAL" -y --json > "$RUN/stdout.json" 2> "$RUN/stderr.log"
  echo $? > "$RUN/exit_code"
  echo "[canary] $(date -u +%Y-%m-%dT%H:%M:%SZ) run $N end (exit $(cat "$RUN/exit_code"))" | tee -a "$RUN/timeline.txt"
  "$PY" scripts/prd036_canary.py snapshot "$WS" "$RUN/after.json" || return 2
  kriya_cli doctor --production --json > "$RUN/doctor-after.json" 2> "$RUN/doctor-after.stderr.txt"
  # Independent re-verification, outside Kriya's own gates.
  ( cd "$WS" && ./mvnw -o -q clean compile > "$RUN/post-run-compile.log" 2>&1; echo $? > "$RUN/post-run-compile.exit" )
  ( cd "$WS" && ./mvnw -o clean test > "$RUN/post-run-test.log" 2>&1; echo $? > "$RUN/post-run-test.exit" )
  git -C "$WS" diff HEAD > "$RUN/workspace-diff.txt"
  mkdir -p "$RUN/run-records" && cp "$WS"/.kriya/control/runs/*.json "$RUN/run-records/" 2>/dev/null
  "$PY" -c 'import json, sys; from kriya.core.release_identity import release_identity; json.dump(release_identity("oci"), open(sys.argv[1], "w"), indent=2, sort_keys=True)' "$RUN/release-identity.json"
  "$PY" scripts/prd036_canary.py verdict "$RUN" "$WS" "${START[@]}" --expect-changed "$KRIYA_CANARY_EXPECT"
}

run_pass 1 --fresh
# Run 2 starts from run 1's state (worktrees, caches, run records kept); only
# the tracked files go back to the baseline so the same goal applies again.
git -C "$WS" reset -q --hard "$BASE_SHA" || exit 2
run_pass 2 --continues-from "$OUT/run-1"
"$PY" scripts/prd036_canary.py combine "$OUT" "$OUT/run-1" "$OUT/run-2"

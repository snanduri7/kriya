#!/usr/bin/env bash
# PRD-036 canary: one real production-profile run of the frozen release
# candidate (the demo-03 brownfield fix: a Spring Boot service, 53 existing
# tests), with the exact operator production config, through the enforce
# controller, plus leak checks before and after. Never reuses an old result.
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
if [ ! -d "$WS/.git" ]; then
  git clone -q "$KRIYA_CANARY_BASELINE" "$WS" || exit 2
fi
[ "$(git -C "$WS" config --get remote.origin.url)" = "$KRIYA_CANARY_BASELINE" ] \
  || { echo "[canary] $WS is not a clone of $KRIYA_CANARY_BASELINE" >&2; exit 2; }
git -C "$WS" reset -q --hard "$BASE_SHA" && git -C "$WS" clean -qfdx || exit 2

"$PY" scripts/release_candidate.py check --recorded "$KRIYA_RELEASE_CANDIDATE" > "$OUT/release-candidate-check.json" \
  || { cat "$OUT/release-candidate-check.json"; echo "[canary] the frozen release candidate is not CURRENT" >&2; exit 2; }
cp "$KRIYA_RELEASE_CANDIDATE" "$OUT/release-candidate.json"
kriya_cli() { ( cd "$WS" && "$KRIYA" --config "$KRIYA_RELEASE_CONFIG" --trust-file "$KRIYA_RELEASE_TRUST_FILE" "$@" ); }

kriya_cli doctor --production --json > "$OUT/doctor-before.json" 2> "$OUT/doctor-before.stderr.txt"
"$PY" scripts/prd036_canary.py snapshot "$WS" "$OUT/before.json" || exit 2
echo "[canary] $(date -u +%Y-%m-%dT%H:%M:%SZ) run start (baseline $BASE_SHA)" | tee "$OUT/timeline.txt"
kriya_cli generate -f "$KRIYA_CANARY_GOAL" -y --json > "$OUT/stdout.json" 2> "$OUT/stderr.log"
echo $? > "$OUT/exit_code"
echo "[canary] $(date -u +%Y-%m-%dT%H:%M:%SZ) run end (exit $(cat "$OUT/exit_code"))" | tee -a "$OUT/timeline.txt"
"$PY" scripts/prd036_canary.py snapshot "$WS" "$OUT/after.json" || exit 2
kriya_cli doctor --production --json > "$OUT/doctor-after.json" 2> "$OUT/doctor-after.stderr.txt"

# Independent re-verification, outside Kriya's own gates.
( cd "$WS" && ./mvnw -o -q clean compile > "$OUT/post-run-compile.log" 2>&1; echo $? > "$OUT/post-run-compile.exit" )
( cd "$WS" && ./mvnw -o clean test > "$OUT/post-run-test.log" 2>&1; echo $? > "$OUT/post-run-test.exit" )
git -C "$WS" diff HEAD > "$OUT/workspace-diff.txt"
mkdir -p "$OUT/run-records" && cp "$WS"/.kriya/control/runs/*.json "$OUT/run-records/" 2>/dev/null
"$PY" -c 'import json, sys; from kriya.core.release_identity import release_identity; json.dump(release_identity("oci"), open(sys.argv[1], "w"), indent=2, sort_keys=True)' "$OUT/release-identity.json"

"$PY" scripts/prd036_canary.py verdict "$OUT" "$WS" --expect-changed "$KRIYA_CANARY_EXPECT"

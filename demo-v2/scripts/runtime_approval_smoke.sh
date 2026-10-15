#!/usr/bin/env bash
# Runtime-approval smoke (Demo V2): drives the evidence-packaged app through Demo 00's journey with the product's CONTROLLED workflow body (NO model) over DISPOSABLE scratch projects and a scratch HOME:
#   main    New Project -> Setup -> configuration approval -> goal -> source review (ONE click) -> runtime-command review -> Continue to Run Approval (scripted NATIVE confirmation) -> Activity -> result -> Verification -> Project details
#   reject  the runtime review is rejected;  cancel  the native confirmation is cancelled (scripted) and the request stays pending;  managed  a managed-service goal stops before anything starts;  failure  a general failure
# The native confirmation is an OS sheet: it cannot appear in an in-page screenshot; its scripted answer and effects are captured. CONTROLLED: files and program output are scripted test data.
# Usage: runtime_approval_smoke.sh [--out DIR] [--app PATH/Kriya.app] [--only main|reject|cancel|managed|failure]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
OUT="$REPO_ROOT/handover/DEMO-V2/evidence/runtime-approval"; APP="$REPO_ROOT/ui/standalone/out-evidence/Kriya.app"; ONLY=""
while [ $# -gt 0 ]; do case "$1" in --out) OUT="${2:-}"; shift 2 ;; --app) APP="${2:-}"; shift 2 ;; --only) ONLY="${2:-}"; shift 2 ;; *) die "unknown argument: $1" ;; esac; done
[ -x "$APP/Contents/MacOS/Kriya" ] || die "$APP is not an evidence-packaged app (cd ui/standalone && npm run package:mac:evidence)"
PY="$REPO_ROOT/.venv/bin/python"; CONTROLLED="$REPO_ROOT/ui/standalone/test/fixtures/controlled_kriya.py"
[ -x "$PY" ] && [ -f "$CONTROLLED" ] || die "the repository venv or the controlled workflow body is missing"
pgrep -f "$APP/Contents/MacOS/Kriya" >/dev/null 2>&1 && die "an app from $APP is already running: quit it first"
mkdir -p "$OUT"
journey() {
  local kind="$1" S confirm
  S="$(mktemp -d "${TMPDIR:-/tmp}/kriya-rt-$kind.XXXXXX")"
  mkdir -p "$S/state" "$S/home" "$S/userdata" "$S/captures" "$S/projects"
  node "$DEMO_V2_DIR/scripts/gui/demo00_steps.mjs" "$kind" "$S/steps.json" >/dev/null
  # the scripted body travels as a FILE (it holds apostrophes: quoting it into the wrapper would break the shell)
  printf '#!/bin/sh\nKRIYA_CONTROLLED_SCRIPT="$(cat "%s")"\nexport KRIYA_CONTROLLED_SCRIPT\nexec "%s" "%s" "$@"\n' "$S/steps.script.json" "$PY" "$CONTROLLED" > "$S/kriya-controlled"; chmod +x "$S/kriya-controlled"
  printf '{"kriyaExecutable": "%s", "editor": "vscode"}\n' "$S/kriya-controlled" > "$S/userdata/kriya-ui-settings.json"
  case "$kind" in main) confirm='["approve","approve"]' ;; cancel) confirm='["approve","cancel"]' ;; *) confirm='["approve"]' ;; esac
  set +e
  HOME="$S/home" KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_STATE_DIR="$S/state" KRIYA_UI_CAPTURE="$S/captures" KRIYA_UI_CAPTURE_STEPS="$S/steps.json" KRIYA_UI_CAPTURE_CHOOSE="[\"$(cd "$S/projects" && pwd -P)\"]" KRIYA_UI_CAPTURE_CONFIRM="$confirm" \
    "$APP/Contents/MacOS/Kriya" --user-data-dir="$S/userdata" >"$S/app.stdout.log" 2>"$S/app.stderr.log"
  local status=$?
  set -e
  [ -f "$S/captures/results.json" ] || { tail -20 "$S/app.stderr.log"; die "$kind: the capture driver wrote no results.json (app exit $status)"; }
  cp "$S"/captures/*.png "$OUT"/ 2>/dev/null || true
  cp "$S/captures/results.json" "$OUT/results-$kind.json"
  echo "$kind: app exit $status" | tee -a "$OUT/independent-checks.txt"
  rm -rf "$S"
}
: > "$OUT/independent-checks.txt"
for k in main reject cancel managed failure; do [ -z "$ONLY" ] || [ "$ONLY" = "$k" ] || continue; journey "$k"; done
echo "$(ls "$OUT"/*.png | wc -l | tr -d ' ') screenshots, results-*.json and independent-checks.txt in $OUT"

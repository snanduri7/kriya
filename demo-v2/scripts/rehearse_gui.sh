#!/usr/bin/env bash
# Scenario 01 GUI rehearsal: drives the evidence-packaged desktop app through the whole flow with the scripted folder chooser and a REAL local model.
# This is a LIVE run: it calls the configured model and takes minutes. It writes only inside the work area (screenshots, results.json, a copy of the app's log).
# Preconditions (checked): verify_demo.sh --phase pre passes, i.e. the workspace is prepared AND an operator approved its configuration.
# Usage: rehearse_gui.sh --scenario 01 [--app PATH/Kriya.app]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SCENARIO=""; APP="$REPO_ROOT/ui/standalone/out-evidence/Kriya.app"
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCENARIO="${2:-}"; shift 2 ;;
    --app) APP="${2:-}"; shift 2 ;;
    -h|--help) sed -n 2,6p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ "$SCENARIO" = "01" ] || die "only scenario 01 has a GUI rehearsal driver (02 is PROVISIONAL, 03 is DESIGN)"
assert_work_outside_repo
SC_DIR="$(scenario_dir "$SCENARIO")"
WORK="$(scenario_work "$SCENARIO")"
WS="$WORK/workspace"
[ -x "$APP/Contents/MacOS/Kriya" ] || die "$APP is not an evidence-packaged app (cd ui/standalone && npm run package:mac:evidence)"
command -v node >/dev/null || die "node is required to generate the capture steps"

"$DEMO_V2_DIR/scripts/verify_demo.sh" --scenario "$SCENARIO" --phase pre || die "pre-flight verification failed: not starting a live run"

GUI="$WORK/gui"; mkdir -p "$GUI/userdata" "$GUI/captures"
node "$SC_DIR/gui/make_capture.mjs" "$SC_DIR/goal.md" "$GUI/capture.json"
printf '{"kriyaExecutable": "%s", "editor": "vscode"}\n' "$REPO_ROOT/.venv/bin/kriya" > "$GUI/userdata/kriya-ui-settings.json"

echo "=== LIVE GUI rehearsal: scenario $SCENARIO (real model; this takes minutes) ==="
echo "app:        $APP"
echo "workspace:  $WS"
echo "state:      $WORK/state"
echo "screenshots and results.json: $GUI/captures"
START="$(date +%s)"
set +e
HOME="$HOME" KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_STATE_DIR="$WORK/state" KRIYA_UI_CAPTURE="$GUI/captures" KRIYA_UI_CAPTURE_STEPS="$GUI/capture.json" \
  KRIYA_UI_CAPTURE_CHOOSE="[\"$WS\"]" "$APP/Contents/MacOS/Kriya" --user-data-dir="$GUI/userdata" >"$GUI/app.stdout.log" 2>"$GUI/app.stderr.log"
STATUS=$?
set -e
echo "app exited with status $STATUS after $(( $(date +%s) - START )) s"
[ -f "$GUI/captures/results.json" ] || die "the capture driver wrote no results.json (see $GUI/app.stderr.log)"
echo "wait_review_or_finish: $(python3 -c "import json;print(json.load(open('$GUI/captures/results.json')).get('wait_review_or_finish'))")"
echo "wait_terminal:         $(python3 -c "import json;print(json.load(open('$GUI/captures/results.json')).get('wait_terminal'))")"
echo "next: verify_demo.sh --scenario $SCENARIO --phase post"

#!/usr/bin/env bash
# Scenario 02 step 7: capture the GUI Skills page (list and per-skill provenance) for the skills of one scenario02_run.sh run. No model, no network, read-only.
# Usage: capture_skills_page.sh --run-id ID [--app PATH/Kriya.app]
# Opens the run's workspace in the evidence-packaged desktop app (scripted folder chooser), opens the Skills destination and writes screenshots + results.json into the run's evidence folder.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
RUN_ID=""; APP="$REPO_ROOT/ui/standalone/out-evidence/Kriya.app"
while [ $# -gt 0 ]; do
  case "$1" in
    --run-id) RUN_ID="${2:-}"; shift 2 ;;
    --app) APP="${2:-}"; shift 2 ;;
    -h|--help) sed -n 2,5p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$RUN_ID" ] || die "--run-id is required"
assert_work_outside_repo
RUN="$DEMO_V2_WORK/scenario-02/$RUN_ID"
[ -d "$RUN/workspace" ] && [ -d "$RUN/skills" ] || die "$RUN is not a scenario02_run.sh run directory"
[ -x "$APP/Contents/MacOS/Kriya" ] || die "$APP is not an evidence-packaged app (cd ui/standalone && npm run package:mac:evidence)"
GUI="$RUN/evidence/gui"; [ ! -e "$GUI" ] || die "$GUI already exists; evidence is never overwritten"
mkdir -p "$GUI/userdata" "$GUI/captures"
# The workspace's own kriya.yaml needs the operator's approval (SEC-009) or the page honestly lists nothing; approve for this capture only and revoke afterwards. Recorded.
KRIYA_BIN="${KRIYA_BIN:-$REPO_ROOT/.venv/bin/kriya}"
trap '( cd "$RUN/workspace" && "$KRIYA_BIN" authority revoke >/dev/null 2>&1 ) || true' EXIT
( cd "$RUN/workspace" && "$KRIYA_BIN" authority approve --confirm ) > "$GUI/authority-approve.txt" 2>&1 || die "authority approve failed, see $GUI/authority-approve.txt"
printf '{"kriyaExecutable": "%s", "editor": "vscode"}\n' "$REPO_ROOT/.venv/bin/kriya" > "$GUI/userdata/kriya-ui-settings.json"
set +e
HOME="$HOME" KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_STATE_DIR="$RUN/state" KRIYA_UI_CAPTURE="$GUI/captures" KRIYA_UI_CAPTURE_STEPS="$DEMO_V2_DIR/scripts/gui/skills_capture.json" \
  KRIYA_UI_CAPTURE_CHOOSE="[\"$RUN/workspace\"]" "$APP/Contents/MacOS/Kriya" --user-data-dir="$GUI/userdata" >"$GUI/app.stdout.log" 2>"$GUI/app.stderr.log"
STATUS=$?
set -e
echo "app exited with status $STATUS"
[ -f "$GUI/captures/results.json" ] || die "the capture driver wrote no results.json (see $GUI/app.stderr.log)"
python3 -c "import json;d=json.load(open('$GUI/captures/results.json'));print('open:',d.get('open'));print(d.get('skills_list'))"

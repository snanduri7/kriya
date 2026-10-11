#!/bin/sh
# GUI final: the DETERMINISTIC dry run of the review smoke (controlled workflow body, no model). Usage: run-review-dryrun.sh <output dir>
# The PACKAGED Kriya.app, launched with NO environment variable that selects a backend, NO screenshots at any step (the capture driver is used only to script the clicks
# and read the in-page sampler). The workflow body is the controlled one (a real Kriya launch, records and polling; no model is called). Scratch state and project.
set -eu
OUT="$1"
UI="$(cd "$(dirname "$0")/../../../../ui/standalone" && pwd)"
ROOT="$(cd "$UI/../.." && pwd)"
APP="$UI/out-evidence/Kriya.app"  # the EVIDENCE package (npm run package:mac:evidence): the shipped product has no capture driver
BASE="$(mktemp -d "${TMPDIR:-/tmp}/kriya-review-dry.XXXXXX")"
mkdir -p "$BASE/state" "$BASE/config" "$BASE/data" "$BASE/ws/hello-project" "$OUT"
printf 'a' > "$BASE/ws/hello-project/a.txt"
( cd "$BASE/ws/hello-project" && git init -q -b main . && git add . && git -c user.name=t -c user.email=t@t commit -qm initial ) 2>&1 || true
EXE="$BASE/kriya-controlled"
printf '#!/bin/sh\nexport KRIYA_CONTROLLED_SCRIPT='"'"'{"outcome":"review","step_ms":300}'"'"'\nexec %s/.venv/bin/python %s/ui/standalone/test/fixtures/controlled_kriya.py "$@"\n' "$ROOT" "$ROOT" > "$EXE"
chmod +x "$EXE"
cat > "$BASE/data/kriya-ui-settings.json" <<JSON
{"kriyaExecutable":"$EXE","workspacePath":null,"configDirectory":"$BASE/config","recentWorkspaces":[]}
JSON
echo "$BASE" > "$OUT/scratch-path.txt"
cd "$UI"
HOME="$HOME" KRIYA_STATE_DIR="$BASE/state" KRIYA_UI_CAPTURE="$OUT" KRIYA_UI_CAPTURE_STEPS="$UI/capture-review.json" \
KRIYA_UI_CAPTURE_CHOOSE="[\"$BASE/ws/hello-project\"]" \
  "$APP/Contents/MacOS/Kriya" --user-data-dir="$BASE/data" 2> "$OUT/stderr.log" | tail -3
echo "scratch: $BASE"

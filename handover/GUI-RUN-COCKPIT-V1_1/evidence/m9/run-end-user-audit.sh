#!/bin/sh
# End-user experience (Phases 22/23): the NEW surfaces on the EVIDENCE package of the real app, over the REAL Kriya. Usage: run-end-user-audit.sh <output dir> <state-and-workspace dir from make_big_state.py (contains state/ and ws/)>
# The scripted chooser answers, in order: the PARENT folder for New Project (twice: once to open the dialog and Escape it, once to create), then the existing workspace for Open Folder.
set -eu
OUT="$1"; BIG="$2"
UI="$(cd "$(dirname "$0")/../../../../ui/standalone" && pwd)"
ROOT="$(cd "$UI/../.." && pwd)"
APP="$UI/out-evidence/Kriya.app"
BASE="$(mktemp -d "${TMPDIR:-/tmp}/kriya-enduser.XXXXXX")"
mkdir -p "$BASE/config" "$BASE/data" "$BASE/parent" "$OUT"
cat > "$BASE/data/kriya-ui-settings.json" <<JSON
{"kriyaExecutable":"$ROOT/.venv/bin/kriya","workspacePath":null,"configDirectory":"$BASE/config","recentWorkspaces":[]}
JSON
cd "$UI"
HOME="$HOME" KRIYA_STATE_DIR="$BIG/state" KRIYA_UI_CAPTURE="$OUT" KRIYA_UI_CAPTURE_STEPS="$UI/capture-end-user.json" KRIYA_UI_CAPTURE_CHOOSE="[\"$BASE/parent\",\"$BASE/parent\",\"$BIG/ws\"]" \
  "$APP/Contents/MacOS/Kriya" --user-data-dir="$BASE/data" 2> "$OUT/stderr.log" | tail -2
echo "created: $(ls "$BASE/parent")" | tee "$OUT/created.txt"
ls -la "$BASE/parent"/* | head -5 | tee -a "$OUT/created.txt"

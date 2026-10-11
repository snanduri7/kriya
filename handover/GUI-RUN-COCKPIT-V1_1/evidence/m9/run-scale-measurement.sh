#!/bin/sh
# GUI v1 hardening Phase E: the SCALE measurement on the EVIDENCE package of the real app (no screenshots). Usage: run-scale-measurement.sh <output dir> <big state dir from make_big_state.py>
set -eu
OUT="$1"; BIG="$2"
UI="$(cd "$(dirname "$0")/../../../../ui/standalone" && pwd)"
ROOT="$(cd "$UI/../.." && pwd)"
APP="$UI/out-evidence/Kriya.app"
BASE="$(mktemp -d "${TMPDIR:-/tmp}/kriya-scale.XXXXXX")"
mkdir -p "$BASE/config" "$BASE/data" "$OUT"
cat > "$BASE/data/kriya-ui-settings.json" <<JSON
{"kriyaExecutable":"$ROOT/.venv/bin/kriya","workspacePath":null,"configDirectory":"$BASE/config","recentWorkspaces":[]}
JSON
cd "$UI"
HOME="$HOME" KRIYA_STATE_DIR="$BIG/state" KRIYA_UI_CAPTURE="$OUT" KRIYA_UI_CAPTURE_STEPS="$UI/capture-scale.json" KRIYA_UI_CAPTURE_CHOOSE="[\"$BIG/ws\"]" \
  "$APP/Contents/MacOS/Kriya" --user-data-dir="$BASE/data" 2> "$OUT/stderr.log" | tail -2

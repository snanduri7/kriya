#!/bin/sh
# M5 real-data capture (development evidence). Usage: run-capture.sh <spawn|service> <output dir>
# Safe copy only: HOME, KRIYA_STATE_DIR and the approved content-ON configuration are ~/kriya-gui-m2/*; no live model; the folders opened are throw-away
# directories in the scratchpad (and the two paths the real runs recorded as their workspaces).
set -eu
TRANSPORT="$1"; OUT="$2"
SP=/private/tmp/claude-501/-Users-sriramnanduri-WorkingDirectory-AI-ClaudeCode-Kriya-By-ClaudeCode/f45a78c2-ffe6-4bce-a516-01055f8abb29/scratchpad
OLD=/private/tmp/claude-501/-Users-sriramnanduri-WorkingDirectory-AI-ClaudeCode-Kriya-By-ClaudeCode/b9c46006-cadc-4425-8a7d-b8d6ae5cfcb7/scratchpad
SAFE="$HOME/kriya-gui-m2"
UI="$(cd "$(dirname "$0")/../../../../ui/standalone" && pwd)"
DATA="$SAFE/userdata-m5-$TRANSPORT"
rm -rf "$DATA"; mkdir -p "$DATA" "$OUT"
cat > "$DATA/kriya-ui-settings.json" <<JSON
{"kriyaExecutable":"$UI/../../.venv/bin/kriya","workspacePath":null,"configDirectory":"$SAFE/config",
 "recentWorkspaces":["$OLD/pin.V0ZC/ws","$OLD/p3dpin/ws","$SP/m5-gone"]}
JSON
cd "$UI"
HOME="$SAFE/home" KRIYA_STATE_DIR="$SAFE/state" KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_UI_TRANSPORT="$TRANSPORT" KRIYA_UI_TRACE=1 \
KRIYA_UI_CAPTURE="$OUT" KRIYA_UI_CAPTURE_STEPS="$UI/capture-m5.json" \
KRIYA_UI_CAPTURE_CHOOSE="[\"$SP/m5-empty\",\"$SP/m5-dirty\",\"$SP/m5-missing\",\"$SP/m5-locked\",null]" \
  npx electron . --user-data-dir="$DATA" 2> "$OUT/trace.log" | tail -3

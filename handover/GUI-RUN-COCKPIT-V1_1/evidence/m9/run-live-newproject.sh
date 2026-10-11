#!/bin/sh
# End-user experience Phase 28: the ONE live smoke of the new user path. Launched with NO KRIYA_UI_ALLOW_REAL_KRIYA. The PACKAGED evidence app, the real read process, the real `kriya generate`
# workflow and the already-configured local model (packaged defaults; no profile or Ollama change). HOME is the operator's real HOME but KRIYA_STATE_DIR is a scratch directory, so nothing is
# added to the owner's run history. The project is created THROUGH the app (New Project) inside a disposable parent folder outside every repository. Usage: run-live-newproject.sh <absolute output dir>
set -eu
OUT="$1"
UI="$(cd "$(dirname "$0")/../../../../ui/standalone" && pwd)"
ROOT="$(cd "$UI/../.." && pwd)"
APP="$UI/out-evidence/Kriya.app"
BASE="$(mktemp -d "${TMPDIR:-/tmp}/kriya-live-newproject.XXXXXX")"
mkdir -p "$BASE/state" "$BASE/config" "$BASE/data" "$BASE/parent" "$OUT"
cat > "$BASE/data/kriya-ui-settings.json" <<JSON
{"kriyaExecutable":"$ROOT/.venv/bin/kriya","workspacePath":null,"configDirectory":"$BASE/config","recentWorkspaces":[]}
JSON
echo "$BASE" > "$OUT/scratch-path.txt"
cd "$UI"
HOME="$HOME" KRIYA_STATE_DIR="$BASE/state" KRIYA_UI_TRACE=1 \
KRIYA_UI_CAPTURE="$OUT" KRIYA_UI_CAPTURE_STEPS="$UI/capture-live-newproject.json" \
KRIYA_UI_CAPTURE_CHOOSE="[\"$BASE/parent\"]" \
  "$APP/Contents/MacOS/Kriya" --user-data-dir="$BASE/data" 2> "$OUT/trace.log" | tail -3
echo "scratch: $BASE"; ls -la "$BASE/parent/hello-greenfield" | tee "$OUT/created-folder-listing.txt"

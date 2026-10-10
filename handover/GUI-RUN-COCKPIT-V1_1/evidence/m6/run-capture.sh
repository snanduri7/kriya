#!/bin/sh
# M6 Start Run capture (development evidence). Usage: run-capture.sh <output dir> [step_ms]
# A throw-away HOME / state / configuration directory (nothing of the owner's), the REAL cockpit build, the REAL Kriya read process, and the
# CONTROLLED workflow body (ui/standalone/test/fixtures/controlled_kriya.py): the real run coordinator, launch record, evidence recorder and trace
# logger; only the model-calling pipeline is a script. No model is called.
set -eu
OUT="$1"; STEP="${2:-1400}"
UI="$(cd "$(dirname "$0")/../../../../ui/standalone" && pwd)"
ROOT="$(cd "$UI/../.." && pwd)"
BASE="$(mktemp -d "${TMPDIR:-/tmp}/kriya-m6-capture.XXXXXX")"
mkdir -p "$BASE/home" "$BASE/state" "$BASE/config" "$BASE/data" "$BASE/ws/greenfield-empty" "$BASE/ws/existing-project" "$OUT"
cat > "$BASE/kriya-controlled" <<SH
#!/bin/sh
export KRIYA_CONTROLLED_SCRIPT='{"outcome":"success","step_ms":$STEP}'
exec "$ROOT/.venv/bin/python" "$UI/test/fixtures/controlled_kriya.py" "\$@"
SH
chmod +x "$BASE/kriya-controlled"
( cd "$BASE/ws/existing-project" && git init -q -b main . && printf 'def importer(rows):\n    return rows\n' > importer.py && git add importer.py \
  && git -c user.name=t -c user.email=t@t commit -qm "initial importer" )
cat > "$BASE/data/kriya-ui-settings.json" <<JSON
{"kriyaExecutable":"$BASE/kriya-controlled","workspacePath":null,"configDirectory":"$BASE/config","recentWorkspaces":[]}
JSON
cd "$UI"
HOME="$BASE/home" KRIYA_STATE_DIR="$BASE/state" KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_UI_TRACE=1 \
KRIYA_UI_CAPTURE="$OUT" KRIYA_UI_CAPTURE_STEPS="$UI/capture-m6.json" \
KRIYA_UI_CAPTURE_CHOOSE="[\"$BASE/ws/greenfield-empty\",\"$BASE/ws/existing-project\"]" \
  npx electron . --user-data-dir="$BASE/data" 2> "$OUT/trace.log" | tail -3
echo "scratch: $BASE"

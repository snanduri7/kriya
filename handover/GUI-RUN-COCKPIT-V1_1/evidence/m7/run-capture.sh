#!/bin/sh
# M7 product walk-through capture (development evidence). Usage: run-capture.sh <output dir> [step_ms]
# Throw-away HOME/state/config, the REAL cockpit build, the REAL Kriya read process, the CONTROLLED workflow body (deterministic, no model).
# The scripted chooser replaces ONLY the native dialog call (native dialog: see NATIVE_DIALOG_CLICK in 08_M7_M8_CLOSURE.md).
set -eu
OUT="$1"; STEP="${2:-1400}"
UI="$(cd "$(dirname "$0")/../../../../ui/standalone" && pwd)"
ROOT="$(cd "$UI/../.." && pwd)"
BASE="$(mktemp -d "${TMPDIR:-/tmp}/kriya-m7-capture.XXXXXX")"
mkdir -p "$BASE/home" "$BASE/state" "$BASE/config" "$BASE/data" "$BASE/ws/hello-project" "$OUT"
cat > "$BASE/kriya-controlled" <<SH
#!/bin/sh
export KRIYA_CONTROLLED_SCRIPT='{"outcome":"success","step_ms":$STEP}'
exec "$ROOT/.venv/bin/python" "$UI/test/fixtures/controlled_kriya.py" "\$@"
SH
chmod +x "$BASE/kriya-controlled"
( cd "$BASE/ws/hello-project" && git init -q -b main . \
  && printf 'def greet(name):\n    return "Hi " + name\n' > hello.py \
  && printf 'from hello import greet\n\n\ndef test_greet():\n    assert greet("Ada").endswith("Ada")\n' > test_hello.py \
  && git add . && git -c user.name=t -c user.email=t@t commit -qm "initial hello project" )
cat > "$BASE/data/kriya-ui-settings.json" <<JSON
{"kriyaExecutable":"$BASE/kriya-controlled","workspacePath":null,"configDirectory":"$BASE/config","recentWorkspaces":[]}
JSON
cd "$UI"
HOME="$BASE/home" KRIYA_STATE_DIR="$BASE/state" KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_UI_TRACE=1 \
KRIYA_UI_CAPTURE="$OUT" KRIYA_UI_CAPTURE_STEPS="$UI/capture-m7.json" \
KRIYA_UI_CAPTURE_CHOOSE="[\"$BASE/ws/hello-project\"]" \
  npx electron . --user-data-dir="$BASE/data" 2> "$OUT/trace.log" | tail -3
echo "scratch: $BASE"

#!/bin/sh
# Launched with NO KRIYA_UI_ALLOW_REAL_KRIYA (GUI-APP-REAL-DEFAULT-001: the packaged app is real by default). M8.6 live GUI smoke. Usage: run-live-smoke.sh <output dir>
# The PACKAGED Kriya.app, the real read process, the real `kriya generate` workflow and the already-configured local model (packaged defaults; no profile
# or Ollama change). HOME is the operator's real HOME (qualification/authority stores are read from it) but KRIYA_STATE_DIR is a scratch directory, so
# nothing is added to the owner's own run history. The project is a disposable folder outside every repository.
set -eu
OUT="$1"
UI="$(cd "$(dirname "$0")/../../../../ui/standalone" && pwd)"
ROOT="$(cd "$UI/../.." && pwd)"
APP="$UI/out-evidence/Kriya.app"  # the EVIDENCE package (npm run package:mac:evidence): the shipped product has no capture driver
BASE="$(mktemp -d "${TMPDIR:-/tmp}/kriya-live-smoke.XXXXXX")"
mkdir -p "$BASE/state" "$BASE/config" "$BASE/data" "$BASE/ws/hello-project" "$OUT"
( cd "$BASE/ws/hello-project" && git init -q -b main . \
  && printf 'def greet(name):\n    return "Hi " + name\n' > hello.py \
  && printf 'from hello import greet\n\n\ndef test_greet_mentions_the_name():\n    assert "Ada" in greet("Ada")\n\n\ndef test_greet_returns_a_string():\n    assert isinstance(greet("Bob"), str)\n' > test_hello.py \
  && git add . && git -c user.name=t -c user.email=t@t commit -qm "initial hello project" ) 2>&1 || echo "(git commit failed in this environment: $?)"
cat > "$BASE/data/kriya-ui-settings.json" <<JSON
{"kriyaExecutable":"$ROOT/.venv/bin/kriya","workspacePath":null,"configDirectory":"$BASE/config","recentWorkspaces":[]}
JSON
echo "$BASE" > "$OUT/scratch-path.txt"
cd "$UI"
HOME="$HOME" KRIYA_STATE_DIR="$BASE/state" KRIYA_UI_TRACE=1 \
KRIYA_UI_CAPTURE="$OUT" KRIYA_UI_CAPTURE_STEPS="$UI/capture-live.json" \
KRIYA_UI_CAPTURE_CHOOSE="[\"$BASE/ws/hello-project\"]" \
  "$APP/Contents/MacOS/Kriya" --user-data-dir="$BASE/data" 2> "$OUT/trace.log" | tail -3
echo "scratch: $BASE"

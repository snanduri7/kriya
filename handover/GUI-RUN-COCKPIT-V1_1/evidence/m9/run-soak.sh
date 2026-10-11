#!/bin/sh
# GUI v1 hardening Phase L: the deterministic SOAK on the EVIDENCE package. Usage: run-soak.sh <output dir> <state dir made by make_big_state.py> [minutes]
# Controlled workflow body (no model); workspace switching, history, tabs, diffs, executions with Approve/Reject cycles. An external monitor samples process count, RSS and file
# descriptors every 15 s. No screenshot is taken at any point.
set -eu
OUT="$1"; SRC="$2"; MIN="${3:-30}"
UI="$(cd "$(dirname "$0")/../../../../ui/standalone" && pwd)"
ROOT="$(cd "$UI/../.." && pwd)"
APP="$UI/out-evidence/Kriya.app"
BASE="$(mktemp -d "${TMPDIR:-/tmp}/kriya-soak.XXXXXX")"
mkdir -p "$BASE/config" "$BASE/data" "$BASE/wsB" "$OUT"
WSA="$SRC/ws"   # the scratch folder the generated history belongs to (its identity matches the copied state); a `.kriya` directory appears in it as controlled runs execute
cp -R "$SRC/state" "$BASE/state"   # a COPY: the generated state is never modified
for w in "$WSA" "$BASE/wsB"; do ( cd "$w" && git init -q -b main . && printf 'a' > a.txt && git add . && git -c user.name=t -c user.email=t@t commit -qm initial ); done
# the copied state's runs belong to the identity of the ORIGINAL workspace folder; re-create that identity relation by regenerating history for wsA/wsB is unnecessary: both folders simply have their own (initially empty) history, which grows with every controlled run
EXE="$BASE/kriya-controlled"
printf '#!/bin/sh\nexport KRIYA_CONTROLLED_SCRIPT='"'"'{"outcome":"review","step_ms":150}'"'"'\nexec %s/.venv/bin/python %s/ui/standalone/test/fixtures/controlled_kriya.py "$@"\n' "$ROOT" "$ROOT" > "$EXE"
chmod +x "$EXE"
cat > "$BASE/data/kriya-ui-settings.json" <<JSON
{"kriyaExecutable":"$EXE","workspacePath":null,"configDirectory":"$BASE/config","recentWorkspaces":[]}
JSON
CHOOSE="["; i=0; while [ $i -lt 600 ]; do if [ $((i % 2)) -eq 0 ]; then CHOOSE="$CHOOSE\"$WSA\""; else CHOOSE="$CHOOSE\"$BASE/wsB\""; fi; i=$((i+1)); [ $i -lt 600 ] && CHOOSE="$CHOOSE,"; done; CHOOSE="$CHOOSE]"
echo "$BASE" > "$OUT/scratch-path.txt"
( while :; do
    PIDS=$(pgrep -f "out-evidence/Kriya.app|kup-stdio|controlled_kriya|generate --json --launch-id" | tr '\n' ',' | sed 's/,$//')
    if [ -n "$PIDS" ]; then
      N=$(echo "$PIDS" | tr ',' '\n' | wc -l | tr -d ' ')
      RSS=$(ps -o rss= -p "$PIDS" 2>/dev/null | awk '{s+=$1} END {print int(s/1024)}')
      FDS=$(lsof -p "$PIDS" 2>/dev/null | wc -l | tr -d ' ')
      PY=$(pgrep -f "kup-stdio" | wc -l | tr -d ' ')
      EX=$(pgrep -f "generate --json --launch-id" | wc -l | tr -d ' ')
      echo "$(date +%s),$N,$RSS,$FDS,$PY,$EX" >> "$OUT/process_samples.csv"
      ps -o pid=,rss=,command= -p "$PIDS" 2>/dev/null | awk -v t="$(date +%s)" '{role="other"; if ($0 ~ /Renderer/) role="renderer"; else if ($0 ~ /Helper.*type=gpu/ || $0 ~ /gpu-process/) role="gpu"; else if ($0 ~ /Helper/) role="helper"; else if ($0 ~ /Contents\/MacOS\/Kriya/) role="main"; else if ($0 ~ /kup-stdio/) role="kup-stdio"; else if ($0 ~ /controlled_kriya|generate/) role="run"; printf "%s,%s,%d\n", t, role, $2/1024}' >> "$OUT/process_roles.csv"
    fi
    sleep 15
  done ) &
MON=$!
cd "$UI"
SOAK_MINUTES="$MIN" caffeinate -dimsu env HOME="$HOME" KRIYA_STATE_DIR="$BASE/state" KRIYA_UI_CAPTURE="$OUT" KRIYA_UI_CAPTURE_STEPS="$UI/capture-soak.json" KRIYA_UI_CAPTURE_CHOOSE="$CHOOSE" \
  "$APP/Contents/MacOS/Kriya" --user-data-dir="$BASE/data" 2> "$OUT/stderr.log" | tail -2 || true
kill $MON 2>/dev/null || true
sleep 3
echo "orphans after quit: kup-stdio=$(pgrep -f kup-stdio | wc -l | tr -d ' ') launch=$(pgrep -f 'generate --json --launch-id' | wc -l | tr -d ' ') app=$(pgrep -f 'out-evidence/Kriya.app' | wc -l | tr -d ' ')" | tee "$OUT/orphans.txt"

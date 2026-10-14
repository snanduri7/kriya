#!/usr/bin/env bash
# Configuration-approval smoke: drives the evidence-packaged app over a DISPOSABLE scratch project whose kriya.yaml needs operator approval, with the product's controlled workflow body (no model).
# Not a Demo V2 scenario: it touches nothing in the demo work area, the repository or ~/.kriya (scratch HOME, so the approval lands in the scratch HOME's ~/.kriya/authority).
# After the app exits it checks, independently of the app, that the real `kriya authority inspect` calls the approval CURRENT and that the stored artifact holds no secret.
# Usage: config_approval_smoke.sh [--out DIR] [--app PATH/Kriya.app]   (default out: handover/DEMO-V2/evidence/gui-config-approval)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
OUT="$REPO_ROOT/handover/DEMO-V2/evidence/gui-config-approval"; APP="$REPO_ROOT/ui/standalone/out-evidence/Kriya.app"
while [ $# -gt 0 ]; do case "$1" in --out) OUT="${2:-}"; shift 2 ;; --app) APP="${2:-}"; shift 2 ;; *) die "unknown argument: $1" ;; esac; done
[ -x "$APP/Contents/MacOS/Kriya" ] || die "$APP is not an evidence-packaged app (cd ui/standalone && npm run package:mac:evidence)"
PY="$REPO_ROOT/.venv/bin/python"; KRIYA="$REPO_ROOT/.venv/bin/kriya"; CONTROLLED="$REPO_ROOT/ui/standalone/test/fixtures/controlled_kriya.py"
[ -x "$PY" ] && [ -x "$KRIYA" ] && [ -f "$CONTROLLED" ] || die "the repository venv or the controlled workflow body is missing"
S="$(mktemp -d "${TMPDIR:-/tmp}/kriya-cfg-smoke.XXXXXX")"
trap 'rm -rf "$S"' EXIT
mkdir -p "$S/ws" "$S/state" "$S/home" "$S/userdata" "$S/captures" "$OUT"
( cd "$S/ws" && git init -q -b main && printf 'def hello():\n    return "hi"\n' > app.py && printf '# scratch project for the configuration-approval smoke\n' > README.md \
  && printf 'llm:\n  base_url: "http://operator:hunter2-pass@127.0.0.1:11434/v1"\nmcp:\n  hostile-mcp:\n    command: /usr/local/bin/hostile-mcp\n    env:\n      API_TOKEN: "tok-9f8e7d6c5b4a-do-not-leak"\n' > kriya.yaml \
  && git add -A && git -c user.email=ux@kriya.local -c user.name=ux commit -q -m init && git update-index --assume-unchanged kriya.yaml )
printf '#!/bin/sh\nexport KRIYA_CONTROLLED_SCRIPT='"'"'{"outcome":"success","step_ms":250}'"'"'\nexec "%s" "%s" "$@"\n' "$PY" "$CONTROLLED" > "$S/kriya-controlled"
chmod +x "$S/kriya-controlled"
printf '{"kriyaExecutable": "%s", "editor": "vscode"}\n' "$S/kriya-controlled" > "$S/userdata/kriya-ui-settings.json"
node "$DEMO_V2_DIR/scripts/gui/config_approval_smoke.mjs" "$S/steps.json"
WS="$(cd "$S/ws" && pwd -P)"
[ -z "$(ls "$S/home/.kriya/authority" 2>/dev/null)" ] || die "the scratch HOME already holds an approval before the app ran"
set +e
HOME="$S/home" KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_STATE_DIR="$S/state" KRIYA_UI_CAPTURE="$S/captures" KRIYA_UI_CAPTURE_STEPS="$S/steps.json" KRIYA_UI_CAPTURE_CHOOSE="[\"$WS\"]" KRIYA_UI_CAPTURE_CONFIRM='["cancel","approve"]' \
  "$APP/Contents/MacOS/Kriya" --user-data-dir="$S/userdata" >"$S/app.stdout.log" 2>"$S/app.stderr.log"
STATUS=$?
set -e
[ -f "$S/captures/results.json" ] || { tail -20 "$S/app.stderr.log"; die "the capture driver wrote no results.json (app exit $STATUS)"; }
# independent witnesses, after the app has exited
INSPECT="$( cd "$WS" && HOME="$S/home" "$KRIYA" authority inspect 2>&1 )"
ARTIFACT="$(ls "$S/home/.kriya/authority"/*.json 2>/dev/null | head -1 || true)"
{
  echo "kriya authority inspect (real CLI, run in the project after the app exited), verdict line:"; echo "$INSPECT" | grep -E 'is CURRENT|does NOT cover|No local approval' || echo "(no verdict line: $INSPECT)"
  echo; echo "approval artifact present in the scratch HOME's store: $([ -n "$ARTIFACT" ] && echo yes || echo NO)"
  if [ -n "$ARTIFACT" ]; then
    echo "artifact holds a secret or credential: $(grep -cE 'tok-9f8e7d6c5b4a|hunter2|/usr/local/bin/hostile-mcp' "$ARTIFACT" | sed 's/^0$/no/;s/^[1-9].*$/YES/')"
    echo "artifact fields: $(python3 -c "import json,sys;print([r['field_path'] for r in json.load(open(sys.argv[1]))['security_subset']['records']])" "$ARTIFACT")"
  fi
  echo "files written into the project by the app (besides the run's own .kriya): $(cd "$WS" && git status --short | grep -v '\.kriya' | wc -l | tr -d ' ')"
} > "$S/captures/independent-checks.txt"
cp "$S"/captures/*.png "$S/captures/results.json" "$S/captures/independent-checks.txt" "$OUT"/
echo "app exit $STATUS; $(ls "$OUT"/*.png | wc -l | tr -d ' ') screenshots, results.json and independent-checks.txt in $OUT"

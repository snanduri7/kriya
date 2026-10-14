#!/usr/bin/env bash
# UX-correction smoke: drives the evidence-packaged app over a DISPOSABLE scratch project with the product's controlled workflow body (no model, ~1 minute). Not a Demo V2 scenario:
# it touches nothing in the demo work area, the repository or ~/.kriya (it runs with a scratch HOME and state directory).
# Usage: ux_smoke.sh [--out DIR] [--app PATH/Kriya.app]   (default out: handover/DEMO-V2/evidence/ux-correction)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
OUT="$REPO_ROOT/handover/DEMO-V2/evidence/ux-correction"; APP="$REPO_ROOT/ui/standalone/out-evidence/Kriya.app"
while [ $# -gt 0 ]; do case "$1" in --out) OUT="${2:-}"; shift 2 ;; --app) APP="${2:-}"; shift 2 ;; *) die "unknown argument: $1" ;; esac; done
[ -x "$APP/Contents/MacOS/Kriya" ] || die "$APP is not an evidence-packaged app (cd ui/standalone && npm run package:mac:evidence)"
PY="$REPO_ROOT/.venv/bin/python"; CONTROLLED="$REPO_ROOT/ui/standalone/test/fixtures/controlled_kriya.py"
[ -x "$PY" ] && [ -f "$CONTROLLED" ] || die "the repository venv or the controlled workflow body is missing"
S="$(mktemp -d "${TMPDIR:-/tmp}/kriya-ux-smoke.XXXXXX")"
trap 'rm -rf "$S"' EXIT
mkdir -p "$S/ws" "$S/state" "$S/home" "$S/userdata" "$S/captures" "$OUT"
( cd "$S/ws" && git init -q -b main && printf 'def hello():\n    return "hi"\n' > app.py && printf '# scratch project for the UX smoke\n' > README.md && git add -A && git -c user.email=ux@kriya.local -c user.name=ux commit -q -m init )
printf '#!/bin/sh\nexport KRIYA_CONTROLLED_SCRIPT='"'"'{"outcome":"review","step_ms":1100}'"'"'\nexec "%s" "%s" "$@"\n' "$PY" "$CONTROLLED" > "$S/kriya-controlled"
chmod +x "$S/kriya-controlled"
printf '{"kriyaExecutable": "%s", "editor": "vscode"}\n' "$S/kriya-controlled" > "$S/userdata/kriya-ui-settings.json"
node "$DEMO_V2_DIR/scripts/gui/ux_smoke.mjs" "$S/steps.json"
WS="$(cd "$S/ws" && pwd -P)"
set +e
HOME="$S/home" KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_STATE_DIR="$S/state" KRIYA_UI_CAPTURE="$S/captures" KRIYA_UI_CAPTURE_STEPS="$S/steps.json" KRIYA_UI_CAPTURE_CHOOSE="[\"$WS\"]" \
  "$APP/Contents/MacOS/Kriya" --user-data-dir="$S/userdata" >"$S/app.stdout.log" 2>"$S/app.stderr.log"
STATUS=$?
set -e
[ -f "$S/captures/results.json" ] || { cat "$S/app.stderr.log" | tail -20; die "the capture driver wrote no results.json (app exit $STATUS)"; }
cp "$S"/captures/*.png "$S/captures/results.json" "$OUT"/
echo "app exit $STATUS; $(ls "$OUT"/*.png | wc -l | tr -d ' ') screenshots and results.json in $OUT"

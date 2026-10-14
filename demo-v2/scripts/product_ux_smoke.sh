#!/usr/bin/env bash
# Product UX smoke (PRODUCT-UX-COMPLETION): drives the evidence-packaged app through the greenfield and the brownfield journeys with the product's CONTROLLED workflow body (NO model),
# over DISPOSABLE scratch projects and a scratch HOME. Not a Demo V2 scenario: it touches nothing in the demo work area, the repository or ~/.kriya.
#   greenfield: New Project -> Project Setup -> configuration approval (scripted NATIVE confirmation) -> Ready -> goal -> run -> change review with the exact diff -> approve -> result with
#               the application's recorded output -> Runs -> Verification -> why-trust -> Forensic Evidence; Skills while the run is active.
#   brownfield: Open Folder -> goal -> change review (a modification with context) -> approve -> result; then a failing run.
# CONTROLLED: the "model" output (files, program output) is scripted test data. Usage: product_ux_smoke.sh [--out DIR] [--app PATH/Kriya.app]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
OUT="$REPO_ROOT/handover/DEMO-V2/evidence/product-ux-completion"; APP="$REPO_ROOT/ui/standalone/out-evidence/Kriya.app"
while [ $# -gt 0 ]; do case "$1" in --out) OUT="${2:-}"; shift 2 ;; --app) APP="${2:-}"; shift 2 ;; *) die "unknown argument: $1" ;; esac; done
[ -x "$APP/Contents/MacOS/Kriya" ] || die "$APP is not an evidence-packaged app (cd ui/standalone && npm run package:mac:evidence)"
PY="$REPO_ROOT/.venv/bin/python"; KRIYA="$REPO_ROOT/.venv/bin/kriya"; CONTROLLED="$REPO_ROOT/ui/standalone/test/fixtures/controlled_kriya.py"
[ -x "$PY" ] && [ -x "$KRIYA" ] && [ -f "$CONTROLLED" ] || die "the repository venv or the controlled workflow body is missing"
pgrep -f "$APP/Contents/MacOS/Kriya" >/dev/null 2>&1 && die "an app from $APP is already running: quit it first (this smoke replaces nothing, but two instances would share a window focus)"
mkdir -p "$OUT"
journey() {
  local kind="$1" S
  S="$(mktemp -d "${TMPDIR:-/tmp}/kriya-ux-$kind.XXXXXX")"
  mkdir -p "$S/state" "$S/home" "$S/userdata" "$S/captures" "$S/projects"
  node "$DEMO_V2_DIR/scripts/gui/product_ux_steps.mjs" "$kind" "$S/steps.json" >/dev/null
  local script; script="$(cat "$S/steps.script.json")"
  printf '#!/bin/sh\nexport KRIYA_CONTROLLED_SCRIPT='"'"'%s'"'"'\nexec "%s" "%s" "$@"\n' "$script" "$PY" "$CONTROLLED" > "$S/kriya-controlled"; chmod +x "$S/kriya-controlled"
  printf '{"kriyaExecutable": "%s", "editor": "vscode"}\n' "$S/kriya-controlled" > "$S/userdata/kriya-ui-settings.json"
  local choose confirm WS=""
  if [ "$kind" = greenfield ]; then
    choose="[\"$(cd "$S/projects" && pwd -P)\"]"; confirm='["approve"]'
  else
    WS="$S/ws"; mkdir -p "$WS/src/main/java/com/myapp/service/driver"
    ( cd "$WS" && git init -q -b main && printf '# driver service\n' > README.md \
      && printf 'package com.myapp.service.driver;\n\npublic class DefaultDriverService {\n    private DriverRepository driverRepository;\n\n    public Driver findById(Long id) { return driverRepository.findById(id); }\n\n    public void delete(Long driverId) {\n        Driver driver = findById(driverId);\n        driver.setDeleted(true);\n    }\n\n    @Override\n    public String toString() { return "DefaultDriverService"; }\n}\n' > src/main/java/com/myapp/service/driver/DefaultDriverService.java \
      && git add -A && git -c user.email=ux@kriya.local -c user.name=ux commit -q -m init )
    choose="[\"$(cd "$WS" && pwd -P)\"]"; confirm='[]'
  fi
  set +e
  HOME="$S/home" KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_STATE_DIR="$S/state" KRIYA_UI_CAPTURE="$S/captures" KRIYA_UI_CAPTURE_STEPS="$S/steps.json" KRIYA_UI_CAPTURE_CHOOSE="$choose" KRIYA_UI_CAPTURE_CONFIRM="$confirm" \
    "$APP/Contents/MacOS/Kriya" --user-data-dir="$S/userdata" >"$S/app.stdout.log" 2>"$S/app.stderr.log"
  local status=$?
  set -e
  [ -f "$S/captures/results.json" ] || { tail -20 "$S/app.stderr.log"; die "$kind: the capture driver wrote no results.json (app exit $status)"; }
  cp "$S"/captures/*.png "$OUT"/ 2>/dev/null || true
  cp "$S/captures/results.json" "$OUT/results-$kind.json"
  {
    echo "== $kind journey (app exit $status) =="
    if [ "$kind" = greenfield ]; then
      local P="$S/projects/hello-kriya"
      echo "project folder created by New Project: $([ -d "$P" ] && echo yes || echo NO)"
      echo "kriya.yaml created by Project Setup (not by the renderer): $([ -s "$P/kriya.yaml" ] && echo yes || echo NO)"
      echo "kriya.yaml first lines: $(head -3 "$P/kriya.yaml" 2>/dev/null | tr '\n' '|')"
      echo "kriya authority inspect (real CLI, after the app exited): $( cd "$P" && HOME="$S/home" "$KRIYA" authority inspect 2>&1 | grep -E 'is CURRENT|does NOT cover|No local approval' || echo '(no verdict line)')"
      echo "files written into the project besides kriya.yaml and Kriya's .kriya: $(cd "$P" && ls -A | grep -v -E '^(kriya.yaml|\.kriya)$' | wc -l | tr -d ' ')"
    else
      echo "files changed in the brownfield project by the app: $(cd "$WS" && git status --short | grep -v '\.kriya' | wc -l | tr -d ' ') (the controlled body applies nothing; the review shows a scripted diff)"
    fi
  } >> "$OUT/independent-checks.txt"
  rm -rf "$S"
  echo "$kind: app exit $status"
}
: > "$OUT/independent-checks.txt"
journey greenfield
journey brownfield
echo "$(ls "$OUT"/*.png | wc -l | tr -d ' ') screenshots, results-*.json and independent-checks.txt in $OUT"

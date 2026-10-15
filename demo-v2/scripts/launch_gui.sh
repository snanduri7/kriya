#!/usr/bin/env bash
# Launch the Kriya desktop app (the PRODUCT package, not the evidence build) for one scenario, with that scenario's own state directory and a demo-only user-data directory.
# Usage: launch_gui.sh --scenario 00|01|02|03 [--app PATH/Kriya.app] [--state ABSOLUTE_DIR]
# The app reads run history from ONE state directory: by default this scenario's own (empty until a run is made with it). To look at runs recorded elsewhere (for example a project run with the
# default state, ~/.kriya/state), pass that directory with --state; the history of a folder is only visible to a window that reads the state directory the runs were recorded in.
# The app runs in the background; quit it with Cmd+Q. Then in the app: Open Folder and choose the workspace this script prints (its path is also copied to the clipboard:
# in the folder dialog press Cmd+Shift+G and paste). Nothing here starts a model or changes the workspace.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SCENARIO=""; APP="$REPO_ROOT/ui/standalone/out/Kriya.app"; STATE_OVERRIDE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCENARIO="${2:-}"; shift 2 ;;
    --app) APP="${2:-}"; shift 2 ;;
    --state) [ $# -ge 2 ] || die "--state needs a directory"; STATE_OVERRIDE="$2"; shift 2 ;;
    -h|--help) sed -n 2,5p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
case "$SCENARIO" in 00|01|02|03) ;; *) die "--scenario must be 00, 01, 02 or 03" ;; esac
assert_work_outside_repo
WORK="$(scenario_work "$SCENARIO")"; WS="$WORK/workspace"
KRIYA_BIN="${KRIYA_BIN:-$REPO_ROOT/.venv/bin/kriya}"
{ [ -d "$WS/.git" ] || { [ "$SCENARIO" = "00" ] && [ -d "$WS" ]; }; } || die "$WS does not exist: run demo-v2/scripts/prepare_demo.sh --scenario $SCENARIO first"
[ -x "$KRIYA_BIN" ] || die "$KRIYA_BIN not found (python3 -m venv .venv && .venv/bin/pip install -e .)"
[ -x "$APP/Contents/MacOS/Kriya" ] || die "$APP is not built: cd ui/standalone && npm run package:mac"
# A product package older than the newest UI change would silently lack pages (the Skills and Tools pages in particular): refuse it.
UI_COMMIT_TIME="$(git -C "$REPO_ROOT" log -1 --format=%ct -- ui)"
APP_TIME="$(stat -f %m "$APP")"
[ "$APP_TIME" -ge "$UI_COMMIT_TIME" ] || die "$APP is older than the newest UI change in this checkout: rebuild it (cd ui/standalone && npm run package:mac)"
pgrep -f "$APP/Contents/MacOS/Kriya" >/dev/null 2>&1 && die "a Kriya desktop app from this build is already running: quit it (Cmd+Q) and launch again, so it starts with this scenario's state"

STATE="${STATE_OVERRIDE:-$WORK/state}"
case "$STATE" in /*) ;; *) die "--state must be an absolute directory" ;; esac
if [ -n "$STATE_OVERRIDE" ]; then
  [ -d "$STATE" ] || die "--state $STATE does not exist (a typo would silently give an empty history)"
  [ -f "$STATE/traces.db" ] || echo "WARNING: $STATE has no traces.db: this window will show no run history." >&2
fi
GUI="$WORK/gui"; mkdir -p "$GUI/userdata" "$STATE"
printf '{"kriyaExecutable": "%s", "editor": "vscode"}\n' "$KRIYA_BIN" > "$GUI/userdata/kriya-ui-settings.json"
( HOME="$HOME" KRIYA_STATE_DIR="$STATE" nohup "$APP/Contents/MacOS/Kriya" --user-data-dir="$GUI/userdata" >"$GUI/app.stdout.log" 2>"$GUI/app.stderr.log" & )
printf '%s' "$WS" | pbcopy 2>/dev/null || true
echo "Kriya started for scenario $SCENARIO (state: $STATE)."
echo "Open Folder -> $WS   (path copied to the clipboard; Cmd+Shift+G in the dialog)"

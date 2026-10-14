#!/usr/bin/env bash
# Materialise a scenario's fixture into the work area and render its configuration. Starts no model and makes no network call (scenario 03 starts its LOCAL scripted stub).
# Usage: prepare_demo.sh --scenario 01|02|03|all [--check-build] [--no-services]
#   --check-build   01: also run the fixture's own offline Maven compile + tests (deterministic, no model) and require the baseline count
#                   02: also run the fixture's own pytest and require the measured baseline (7 failed, 1 passed)
#                   03: also run the fixture's own pytest and require the measured baseline (2 failed, 1 passed)
#   --no-services   03: do not start the scripted stub (start it yourself with scripts/stub_ctl.sh start when you begin scenario 03)
#   all             01, 02 and 03 prepared one after the other with --no-services: the owner start state, nothing running
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SCENARIO=""; CHECK_BUILD=0; NO_SERVICES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCENARIO="${2:-}"; shift 2 ;;
    --check-build) CHECK_BUILD=1; shift ;;
    --no-services) NO_SERVICES=1; shift ;;
    all) SCENARIO="all"; shift ;;
    -h|--help) sed -n 2,10p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$SCENARIO" ] || die "--scenario is required"
assert_work_outside_repo
ensure_work_area
if [ "$SCENARIO" = "all" ]; then
  stop_demo_listeners; stop_searxng   # nothing from an earlier session may be running while the owner start state is built
  EXTRA=(--no-services); [ "$CHECK_BUILD" = 1 ] && EXTRA+=(--check-build)
  for s in 01 02 03; do "${BASH_SOURCE[0]}" --scenario "$s" "${EXTRA[@]}"; done   # each one resets its scenario first (workspace, state, approval)
  echo "=== prepare complete (all scenarios): workspaces and configurations are in place, NOTHING is running, no operator approval is granted ==="
  exit 0
fi
SC_DIR="$(scenario_dir "$SCENARIO")"
case "$SCENARIO" in 01|02|03) ;; *) die "unknown scenario $SCENARIO" ;; esac

"$DEMO_V2_DIR/scripts/reset_demo.sh" --scenario "$SCENARIO"

WORK="$(scenario_work "$SCENARIO")"
WS="$WORK/workspace"
KRIYA_PY="${KRIYA_PY:-$REPO_ROOT/.venv/bin/python}"
mkdir -p "$WORK/config"
STUB_PORT=18767
[ "$SCENARIO" = "03" ] && { load_env_file "$DEMO_V2_DIR/fixtures/controlled-repair/fixture.env"; }
if [ "$SCENARIO" = "02" ]; then
  # The owner/offline configuration: real local model straight from the model server (no recording proxy), public lookup OFF, no search endpoint, the preserved snapshot as the skills directory.
  load_env_file "$DEMO_V2_DIR/fixtures/knowledge-skill/fixture.env"
  TEMPLATE="$SC_DIR/config/offline.template.yaml"
else
  TEMPLATE="$SC_DIR/config/kriya.template.yaml"
fi
sed -e "s#@WORK@#$WORK#g" -e "s#@STUB_PORT@#$STUB_PORT#g" "$TEMPLATE" > "$WORK/config/kriya.yaml"
echo "rendered config: $WORK/config/kriya.yaml (sha256 $(sha256_of "$WORK/config/kriya.yaml"))"

if [ "$CHECK_BUILD" = 1 ] && [ "$SCENARIO" = "01" ]; then
  load_env_file "$DEMO_V2_DIR/fixtures/brownfield-spring/fixture.env"
  echo "--- offline baseline build (no model): compile + tests"
  ( cd "$WS" && ./mvnw -o -q clean compile >"$WORK/evidence/prepare-compile.log" 2>&1 ) || die "baseline compile failed, see $WORK/evidence/prepare-compile.log"
  ( cd "$WS" && ./mvnw -o clean test >"$WORK/evidence/prepare-test.log" 2>&1 ) || die "baseline tests failed, see $WORK/evidence/prepare-test.log"
  SUMMARY="$(grep -E '^\[(INFO|WARNING|ERROR)\] Tests run: [0-9]+, Failures' "$WORK/evidence/prepare-test.log" | tail -1)"
  echo "$SUMMARY"
  case "$SUMMARY" in
    *"Tests run: $FIXTURE_BASELINE_TESTS, Failures: 0, Errors: 0"*) echo "baseline tests: $FIXTURE_BASELINE_TESTS passed" ;;
    *) die "baseline test summary does not match $FIXTURE_BASELINE_TESTS passing tests" ;;
  esac
  git -C "$WS" clean -fdxq   # build output is not part of the pristine starting point (-x also removes ignored files: the config is placed AFTER this block)
  [ -z "$(git -C "$WS" status --short)" ] || die "workspace is not clean after the baseline build"
fi
if [ "$CHECK_BUILD" = 1 ] && { [ "$SCENARIO" = "03" ] || [ "$SCENARIO" = "02" ]; }; then
  echo "--- fixture baseline (no model): pytest"
  SUMMARY="$( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -1 || true )"
  echo "$SUMMARY"
  case "$SUMMARY" in *"$FIXTURE_BASELINE_SUMMARY"*) echo "baseline: $FIXTURE_BASELINE_SUMMARY (as designed)" ;; *) die "fixture baseline is '$SUMMARY', expected '$FIXTURE_BASELINE_SUMMARY'" ;; esac
  git -C "$WS" clean -fdxq
  [ -z "$(git -C "$WS" status --short)" ] || die "workspace is not clean after the baseline test run"
fi

# A GUI-launched run's working directory is the workspace, and Kriya discovers kriya.yaml there. It is excluded from git status (reset_demo.sh) and its
# security-authority fields stay UNAPPROVED until an operator approves them: see SCENARIO.md, RESET step 2.
cp "$WORK/config/kriya.yaml" "$WS/kriya.yaml"
echo "placed config:   $WS/kriya.yaml (needs operator approval: cd the workspace && kriya authority approve)"
if [ "$SCENARIO" = "03" ] && [ "$NO_SERVICES" = 0 ]; then "$DEMO_V2_DIR/scripts/stub_ctl.sh" start; fi
echo "=== prepare complete (scenario $SCENARIO) ==="

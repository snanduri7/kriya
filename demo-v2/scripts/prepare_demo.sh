#!/usr/bin/env bash
# Materialise a scenario's fixture into the work area and render its configuration. Starts no model, makes no network call.
# Usage: prepare_demo.sh --scenario 01 [--check-build]
#   --check-build   also run the fixture's own offline Maven compile + tests (deterministic, no model) and require the baseline count
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SCENARIO=""; CHECK_BUILD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCENARIO="${2:-}"; shift 2 ;;
    --check-build) CHECK_BUILD=1; shift ;;
    -h|--help) sed -n 2,5p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$SCENARIO" ] || die "--scenario is required"
assert_work_outside_repo
SC_DIR="$(scenario_dir "$SCENARIO")"
[ "$SCENARIO" = "01" ] || die "only scenario 01 can be prepared at this stage (02 is PROVISIONAL, 03 is DESIGN); see their SCENARIO.md"

"$DEMO_V2_DIR/scripts/reset_demo.sh" --scenario "$SCENARIO"

WORK="$(scenario_work "$SCENARIO")"
mkdir -p "$WORK/config"
sed "s#@WORK@#$WORK#g" "$SC_DIR/config/kriya.template.yaml" > "$WORK/config/kriya.yaml"
echo "rendered config: $WORK/config/kriya.yaml (sha256 $(sha256_of "$WORK/config/kriya.yaml"))"
if [ "$CHECK_BUILD" = 1 ]; then
  load_env_file "$DEMO_V2_DIR/fixtures/brownfield-spring/fixture.env"
  WS="$WORK/workspace"
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
# A GUI-launched run's working directory is the workspace, and Kriya discovers kriya.yaml there. It is excluded from git status (reset_demo.sh) and its
# security-authority fields stay UNAPPROVED until an operator approves them: see SCENARIO.md, RESET step 2.
cp "$WORK/config/kriya.yaml" "$WORK/workspace/kriya.yaml"
echo "placed config:   $WORK/workspace/kriya.yaml (needs operator approval: cd the workspace && kriya authority approve)"
echo "=== prepare complete (scenario $SCENARIO) ==="

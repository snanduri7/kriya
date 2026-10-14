#!/usr/bin/env bash
# Return ONE scenario to its pristine starting point inside the work area. Never writes to a fixture source or to the repository.
# Usage: reset_demo.sh --scenario 01|02|03
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SCENARIO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCENARIO="${2:-}"; shift 2 ;;
    -h|--help) sed -n 2,4p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$SCENARIO" ] || die "--scenario is required"
assert_work_outside_repo
SC_DIR="$(scenario_dir "$SCENARIO")"

case "$SCENARIO" in
  01) ;;
  02) die "scenario 02 is PROVISIONAL (no technology selected, see scenarios/02-knowledge-skill/SCENARIO.md); there is no reset to run" ;;
  03) die "scenario 03 is at DESIGN stage (see scenarios/03-controlled-repair/SCENARIO.md); there is no reset to run" ;;
  *) die "unknown scenario $SCENARIO" ;;
esac

load_env_file "$DEMO_V2_DIR/fixtures/brownfield-spring/fixture.env"
SRC="${DEMO_V2_BASELINE_SRC:-$FIXTURE_SOURCE_DEFAULT}"
WORK="$(scenario_work "$SCENARIO")"
WS="$WORK/workspace"

echo "=== demo-v2 reset: scenario $SCENARIO ==="
[ -d "$SRC/.git" ] || die "fixture source $SRC is not a git repository (set DEMO_V2_BASELINE_SRC)"
git -C "$SRC" cat-file -e "$FIXTURE_COMMIT^{commit}" 2>/dev/null || die "fixture source does not contain commit $FIXTURE_COMMIT"

rm -rf "$WS"
mkdir -p "$WORK"
git clone -q --no-hardlinks "$SRC" "$WS"
git -C "$WS" checkout -q "$FIXTURE_COMMIT"
# A clone's HEAD is detached at the pinned commit; give Kriya a normal branch at the same commit.
git -C "$WS" checkout -q -B demo-baseline "$FIXTURE_COMMIT"

WS_SHA="$(git -C "$WS" rev-parse HEAD)"
[ "$WS_SHA" = "$FIXTURE_COMMIT" ] || die "workspace HEAD is $WS_SHA, expected $FIXTURE_COMMIT"
[ -z "$(git -C "$WS" status --short)" ] || die "workspace is not clean after clone"
TARGET_HASH="$(sha256_of "$WS/$FIXTURE_TARGET_FILE")"
[ "$TARGET_HASH" = "$FIXTURE_TARGET_SHA256" ] || die "target file hash $TARGET_HASH != expected $FIXTURE_TARGET_SHA256"

# Kriya's own state and memory for this scenario (never inside the target repository).
rm -rf "$WORK/state" "$WORK/memory" "$WORK/evidence" "$WS/.kriya"
mkdir -p "$WORK/state" "$WORK/memory" "$WORK/evidence" "$WORK/skills-empty"

echo "workspace:   $WS"
echo "commit:      $WS_SHA"
echo "target sha:  $TARGET_HASH"
echo "git status:  clean"
echo "=== reset complete ==="

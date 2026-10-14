#!/usr/bin/env bash
# Return ONE scenario to its pristine starting point inside the work area. Never writes to a fixture source or to the repository.
# Usage: reset_demo.sh --scenario 01|03      (02 is STOPPED: nothing to reset)
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
  01|03) ;;
  02) die "scenario 02 is STOPPED and returned to the owner (see scenarios/02-knowledge-skill/SCENARIO.md and handover/DEMO-V2/FINDINGS.md); there is no reset to run" ;;
  *) die "unknown scenario $SCENARIO" ;;
esac

WORK="$(scenario_work "$SCENARIO")"
WS="$WORK/workspace"
echo "=== demo-v2 reset: scenario $SCENARIO ==="
[ "$SCENARIO" = "03" ] && "$DEMO_V2_DIR/scripts/stub_ctl.sh" stop
rm -rf "$WS"
mkdir -p "$WORK"

if [ "$SCENARIO" = "01" ]; then
  load_env_file "$DEMO_V2_DIR/fixtures/brownfield-spring/fixture.env"
  SRC="${DEMO_V2_BASELINE_SRC:-$FIXTURE_SOURCE_DEFAULT}"
  [ -d "$SRC/.git" ] || die "fixture source $SRC is not a git repository (set DEMO_V2_BASELINE_SRC)"
  git -C "$SRC" cat-file -e "$FIXTURE_COMMIT^{commit}" 2>/dev/null || die "fixture source does not contain commit $FIXTURE_COMMIT"
  git clone -q --no-hardlinks "$SRC" "$WS"
  git -C "$WS" checkout -q "$FIXTURE_COMMIT"
  # A clone's HEAD is detached at the pinned commit; give Kriya a normal branch at the same commit.
  git -C "$WS" checkout -q -B demo-baseline "$FIXTURE_COMMIT"
  WS_SHA="$(git -C "$WS" rev-parse HEAD)"
  [ "$WS_SHA" = "$FIXTURE_COMMIT" ] || die "workspace HEAD is $WS_SHA, expected $FIXTURE_COMMIT"
else
  load_env_file "$DEMO_V2_DIR/fixtures/controlled-repair/fixture.env"
  cp -R "$DEMO_V2_DIR/$FIXTURE_DIR" "$WS"
  git -C "$WS" init -q -b main
  git -C "$WS" add -A
  git -C "$WS" -c user.email=demo@kriya.local -c user.name="Kriya demo" commit -q -m "fixture: controlled-repair-pricing"
  WS_SHA="$(git -C "$WS" rev-parse HEAD)"
fi
[ -z "$(git -C "$WS" status --short)" ] || die "workspace is not clean after creation"
TARGET_HASH="$(sha256_of "$WS/$FIXTURE_TARGET_FILE")"
[ "$TARGET_HASH" = "$FIXTURE_TARGET_SHA256" ] || die "target file hash $TARGET_HASH != expected $FIXTURE_TARGET_SHA256"

# The scenario's kriya.yaml and Kriya's .kriya/ are not part of the project's source: keep them out of `git status` (so "exactly one file changed" stays meaningful).
printf 'kriya.yaml\n.kriya/\n' >> "$WS/.git/info/exclude"

# A previous operator approval (kriya authority approve) is bound to this workspace's identity and lives in the operator's HOME, outside the workspace. Revoke it so a
# reset really starts from "not approved". Best effort: reports, never hides, what happened.
KRIYA_BIN="${KRIYA_BIN:-$REPO_ROOT/.venv/bin/kriya}"
if [ -x "$KRIYA_BIN" ]; then
  ( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_BIN" authority revoke 2>&1 | sed 's/^/authority:   /' ) || echo "authority:   revoke reported a problem (see above)"
else
  echo "authority:   $KRIYA_BIN not found; any earlier approval was NOT revoked"
fi

# Kriya's own state and memory for this scenario (never inside the target repository).
rm -rf "$WORK/state" "$WORK/memory" "$WORK/evidence" "$WS/.kriya"
mkdir -p "$WORK/state" "$WORK/memory" "$WORK/evidence" "$WORK/skills-empty"

echo "workspace:   $WS"
echo "commit:      $WS_SHA"
echo "target sha:  $TARGET_HASH"
echo "git status:  clean"
echo "=== reset complete ==="

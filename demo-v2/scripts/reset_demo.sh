#!/usr/bin/env bash
# Return ONE scenario (or all of them) to its pristine starting point inside the work area. Never writes to a fixture source or to the repository. Idempotent: running it twice leaves the same state.
# Usage: reset_demo.sh --scenario 00|01|02|03|04|all     (or: reset_demo.sh all)
# 00 : the GREENFIELD start: a brand-new EMPTY project folder, exactly what New Project creates - no git repository, no file, no kriya.yaml (Project Setup creates it in the app),
#      no Kriya state, no approval.
# 04 : the GREENFIELD start with its knowledge: a brand-new EMPTY project folder (no git repository, no source; prepare_demo.sh then places only kriya.yaml) and a fresh COPY of the verified
#      ignite-java17 skill as the skills directory (its digest is checked). The reference candidate in fixtures/spring-ignite-protocol/reference is NEVER copied into the workspace.
#   all : resets 00, 01, 02, 03 and 04 through their own implementations below, then stops every demo service (scripted stub, recording proxy, the optional search container)
#         and revokes the operator approvals. Nothing is left running; nothing is started.
# 02 : a fresh workspace from the fixture and a fresh copy of the PRESERVED GOVERNED ACQUISITION SNAPSHOT as the skills directory (its digest is checked). The per-run directories of
#      scripts/scenario02_run.sh (c1-*, c2-*, final-*) are scratch evidence copies and are left alone: they do not affect the next run.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SCENARIO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCENARIO="${2:-}"; shift 2 ;;
    all) SCENARIO="all"; shift ;;
    -h|--help) sed -n 2,8p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$SCENARIO" ] || die "--scenario is required"
assert_work_outside_repo
ensure_work_area

if [ "$SCENARIO" = "all" ]; then
  # Fail before changing anything if scenario 01's fixture source is missing (it is the only fixture not vendored in the repository).
  load_env_file "$DEMO_V2_DIR/fixtures/brownfield-spring/fixture.env"
  [ -d "${DEMO_V2_BASELINE_SRC:-$FIXTURE_SOURCE_DEFAULT}/.git" ] || die "scenario 01's fixture source ${DEMO_V2_BASELINE_SRC:-$FIXTURE_SOURCE_DEFAULT} is not a git repository (set DEMO_V2_BASELINE_SRC)"
  for s in 00 01 02 03 04; do "${BASH_SOURCE[0]}" --scenario "$s"; done
  echo "=== services ==="
  stop_demo_listeners
  stop_searxng
  echo "=== reset complete (all scenarios); no demo service is running ==="
  exit 0
fi

SC_DIR="$(scenario_dir "$SCENARIO")"
case "$SCENARIO" in 00|01|02|03|04) ;; *) die "unknown scenario $SCENARIO" ;; esac

WORK="$(scenario_work "$SCENARIO")"
WS="$WORK/workspace"
echo "=== demo-v2 reset: scenario $SCENARIO ==="
[ "$SCENARIO" = "03" ] && "$DEMO_V2_DIR/scripts/stub_ctl.sh" stop
rm -rf "$WS"
mkdir -p "$WORK"

if [ "$SCENARIO" = "00" ] || [ "$SCENARIO" = "04" ]; then
  mkdir -p "$WS"   # a brand-new EMPTY folder: nothing in it, not even a git repository (a plain empty folder is a supported greenfield workspace)
elif [ "$SCENARIO" = "01" ]; then
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
  if [ "$SCENARIO" = "03" ]; then load_env_file "$DEMO_V2_DIR/fixtures/controlled-repair/fixture.env"; else load_env_file "$DEMO_V2_DIR/fixtures/knowledge-skill/fixture.env"; fi
  cp -R "$DEMO_V2_DIR/$FIXTURE_DIR" "$WS"
  git -C "$WS" init -q -b main
  git -C "$WS" add -A
  git -C "$WS" -c user.email=demo@kriya.local -c user.name="Kriya demo" commit -q -m "fixture: $FIXTURE_ID"
  WS_SHA="$(git -C "$WS" rev-parse HEAD)"
fi
if [ "$SCENARIO" != "00" ] && [ "$SCENARIO" != "04" ]; then
  [ -z "$(git -C "$WS" status --short)" ] || die "workspace is not clean after creation"
  TARGET_HASH="$(sha256_of "$WS/$FIXTURE_TARGET_FILE")"
  [ "$TARGET_HASH" = "$FIXTURE_TARGET_SHA256" ] || die "target file hash $TARGET_HASH != expected $FIXTURE_TARGET_SHA256"

  # The scenario's kriya.yaml and Kriya's .kriya/ are not part of the project's source: keep them out of `git status` (so "exactly one file changed" stays meaningful).
  printf 'kriya.yaml\n.kriya/\n' >> "$WS/.git/info/exclude"
fi

# A previous operator approval (kriya authority approve) is bound to this workspace's identity and lives in the operator's HOME, outside the workspace. Revoke it so a
# reset really starts from "not approved". Best effort: reports, never hides, what happened.
KRIYA_BIN="${KRIYA_BIN:-$REPO_ROOT/.venv/bin/kriya}"
if [ -x "$KRIYA_BIN" ]; then
  ( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_BIN" authority revoke 2>&1 | sed 's/^/authority:   /' ) || echo "authority:   revoke reported a problem (see above)"
else
  echo "authority:   $KRIYA_BIN not found; any earlier approval was NOT revoked"
fi

# Kriya's own state and memory for this scenario (never inside the target repository).
rm -rf "$WORK/state" "$WORK/memory" "$WORK/evidence" "$WORK/gui" "$WS/.kriya"
mkdir -p "$WORK/state" "$WORK/memory" "$WORK/evidence" "$WORK/skills-empty"

if [ "$SCENARIO" = "02" ]; then
  # The skills directory is a fresh COPY of the preserved snapshot, never the snapshot itself: the one thing a run writes there (the skill-gap acknowledgement flag in each skill.yaml)
  # must not reach the preserved evidence. The digest proves the copy is the snapshot the measured runs used.
  SNAP="$REPO_ROOT/$SNAPSHOT_DIR"
  [ -d "$SNAP" ] || die "the preserved snapshot $SNAPSHOT_DIR is missing from this checkout"
  [ "$(dir_digest "$SNAP")" = "$SNAPSHOT_DIGEST" ] || die "the preserved snapshot in the repository does not have its recorded digest ($SNAPSHOT_DIGEST): it was edited or the checkout is damaged"
  rm -rf "$WORK/skills"
  cp -R "$SNAP" "$WORK/skills"
  [ "$(dir_digest "$WORK/skills")" = "$SNAPSHOT_DIGEST" ] || die "the skills copy does not match the snapshot digest"
  printf 'PRESERVED GOVERNED ACQUISITION SNAPSHOT\nThe skills in ./skills were acquired on 2026-10-14 by a governed live lookup (every prompt approved and recorded) in an EARLIER run of this scenario.\nThey were NOT acquired during your run. Digest: %s\n' "$SNAPSHOT_DIGEST" > "$WORK/SNAPSHOT-LABEL.txt"
  stop_searxng
  echo "skills:      $WORK/skills (copy of the preserved snapshot, digest $SNAPSHOT_DIGEST)"
fi

if [ "$SCENARIO" = "04" ]; then
  # The skills directory is a fresh COPY of the repository's verified skill, never the repository copy itself: the one thing a run writes there (the skill-gap acknowledgement flag in skill.yaml)
  # must not reach the repository. The digest proves the copy is the skill the model-free qualification measured.
  load_env_file "$DEMO_V2_DIR/fixtures/spring-ignite-protocol/fixture.env"
  SKILL_SRC="$REPO_ROOT/$SKILL_SOURCE_DIR"
  [ -d "$SKILL_SRC" ] || die "the skill $SKILL_SOURCE_DIR is missing from this checkout"
  [ "$(dir_digest "$SKILL_SRC")" = "$SKILL_DIGEST" ] || die "the skill in the repository does not have its recorded digest ($SKILL_DIGEST): it was edited (re-qualify, then update fixture.env) or the checkout is damaged"
  rm -rf "$WORK/skills"
  mkdir -p "$WORK/skills"
  cp -R "$SKILL_SRC" "$WORK/skills/$SKILL_NAME"
  [ "$(dir_digest "$WORK/skills/$SKILL_NAME")" = "$SKILL_DIGEST" ] || die "the skills copy does not match the skill digest"
  echo "skills:      $WORK/skills/$SKILL_NAME (copy of $SKILL_SOURCE_DIR, digest $SKILL_DIGEST)"
fi

echo "workspace:   $WS"
if [ "$SCENARIO" = "00" ]; then
  echo "contents:    empty (a new project folder: no git repository, no kriya.yaml)"
elif [ "$SCENARIO" = "04" ]; then
  echo "contents:    empty (a new project folder: no git repository, no source; prepare_demo.sh places only kriya.yaml)"
else
  echo "commit:      $WS_SHA"
  echo "target sha:  $TARGET_HASH"
  echo "git status:  clean"
fi
echo "=== reset complete ==="

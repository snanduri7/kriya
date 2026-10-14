#!/usr/bin/env bash
# Verify DETERMINISTIC facts about a scenario: prerequisites before a run (pre) and outcomes after one (post).
# It never starts a model, never retries, and never turns model variance into an assertion: a post check reports what
# the run produced; it does not make a run succeed.
# Usage: verify_demo.sh --scenario 01|03 --phase pre|post
# Exit 0 only if every check passed. Each check prints PASS/FAIL; "SKIP" is printed (and counted) when a check does not apply.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SCENARIO=""; PHASE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCENARIO="${2:-}"; shift 2 ;;
    --phase) PHASE="${2:-}"; shift 2 ;;
    -h|--help) sed -n 2,7p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$SCENARIO" ] && [ -n "$PHASE" ] || die "--scenario and --phase are required"
case "$PHASE" in pre|post) ;; *) die "--phase must be pre or post" ;; esac
assert_work_outside_repo
SC_DIR="$(scenario_dir "$SCENARIO")"

FAILS=0; SKIPS=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; FAILS=$((FAILS+1)); }
skip() { echo "SKIP  $*"; SKIPS=$((SKIPS+1)); }
check() { local label="$1"; shift; if "$@" >/dev/null 2>&1; then pass "$label"; else fail "$label"; fi; }
finish() { echo "=== result: $FAILS failed, $SKIPS skipped ==="; [ "$FAILS" -eq 0 ]; exit $?; }

KRIYA_PY="${KRIYA_PY:-$REPO_ROOT/.venv/bin/python}"
KRIYA_BIN="${KRIYA_BIN:-$REPO_ROOT/.venv/bin/kriya}"

echo "=== demo-v2 verify: scenario $SCENARIO, phase $PHASE ==="

# ---- product baseline compatibility (both phases) ----
if git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$BASELINE_TAG" >/dev/null; then
  check "product contains baseline tag $BASELINE_TAG" git -C "$REPO_ROOT" merge-base --is-ancestor "$BASELINE_TAG" HEAD
else
  fail "baseline tag $BASELINE_TAG not present in this checkout (git fetch --tags)"
fi
check "product tracked tree is clean (evidence binds to an exact revision)" test -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)"

case "$SCENARIO" in
  01|03) ;;
  02) skip "scenario 02 is not specified far enough to verify (see its SCENARIO.md STATUS)"; finish ;;
  *) die "unknown scenario $SCENARIO" ;;
esac

if [ "$SCENARIO" = "01" ]; then load_env_file "$DEMO_V2_DIR/fixtures/brownfield-spring/fixture.env"; else load_env_file "$DEMO_V2_DIR/fixtures/controlled-repair/fixture.env"; fi
WORK="$(scenario_work "$SCENARIO")"
WS="$WORK/workspace"

# The operator approval is a deliberate human step; the preflight is Kriya's own answer to "would a run here be allowed?". CONFIG_AUTHORITY_REFUSED = unapproved.
preflight_check() {
  [ -x "$KRIYA_BIN" ] && [ -d "$WS" ] || return 0
  local preflight blockers
  preflight="$(PYTHONDONTWRITEBYTECODE=1 "$KRIYA_BIN" traces --json --execution-preflight "$WS" 2>/dev/null || true)"
  blockers="$(printf '%s' "$preflight" | "$KRIYA_PY" -I -c 'import sys,json; e=json.load(sys.stdin); d=e.get("data"); print("NO_PREFLIGHT_DATA" if not d else ",".join(b["code"] for b in d.get("blockers",[])))' 2>/dev/null || echo "PREFLIGHT_UNREADABLE")"
  # STATE_DIRECTORY_NOT_OBSERVABLE is what the one-shot CLI always reports (only the desktop host knows its state directory); it says nothing about the run.
  blockers="$(printf '%s' "$blockers" | tr ',' '\n' | grep -v '^STATE_DIRECTORY_NOT_OBSERVABLE$' | paste -sd, - || true)"
  case ",$blockers," in
    *,CONFIG_AUTHORITY_REFUSED,*) fail "the scenario configuration is NOT approved (cd the workspace && kriya authority approve); Kriya refuses it as it stands" ;;
    ,,) pass "Kriya's preflight reports no blockers (the configuration is approved and loads)" ;;
    *) fail "preflight reports blockers: $blockers" ;;
  esac
}

if [ "$PHASE" = pre ]; then
  check "kriya executable and python are present" test -x "$KRIYA_PY" -a -x "$KRIYA_BIN"
  check "goal file present and non-empty" test -s "$SC_DIR/goal.md"
  check "rendered configuration present" test -s "$WORK/config/kriya.yaml"
  if [ -s "$WORK/config/kriya.yaml" ] && [ -x "$KRIYA_PY" ]; then
    if "$KRIYA_PY" -I - "$WORK/config/kriya.yaml" <<'PY' >/dev/null 2>&1
import sys, yaml
from kriya.config.config import AppConfig
AppConfig.model_validate(yaml.safe_load(open(sys.argv[1])))
PY
    then pass "rendered configuration validates against this product's AppConfig"
    else fail "rendered configuration does NOT validate against this product's AppConfig"; fi
  fi
  check "workspace exists and is a git repository" test -d "$WS/.git"
  check "scenario kriya.yaml is in the workspace and excluded from git status" bash -c "test -s '$WS/kriya.yaml' && git -C '$WS' check-ignore -q kriya.yaml"
  preflight_check
  if [ -d "$WS/.git" ]; then
    [ -z "$(git -C "$WS" status --short)" ] && pass "workspace is clean (nothing changed yet)" || fail "workspace is not clean (run reset_demo.sh)"
    [ "$(sha256_of "$WS/$FIXTURE_TARGET_FILE")" = "$FIXTURE_TARGET_SHA256" ] && pass "target file has the pristine hash (the bug is present)" || fail "target file hash differs from the pristine hash"
    if [ "$SCENARIO" = "01" ]; then
      [ "$(git -C "$WS" rev-parse HEAD)" = "$FIXTURE_COMMIT" ] && pass "workspace HEAD is the fixture commit" || fail "workspace HEAD is not $FIXTURE_COMMIT"
      BODY="$(method_body "$WS/$FIXTURE_TARGET_FILE" "$FIXTURE_METHOD_START_REGEX")"
      if [ -z "$BODY" ]; then fail "could not locate the delete(Long) method"
      elif printf '%s\n' "$BODY" | grep -q -E "$FIXTURE_FIX_REGEX"; then fail "delete(Long) already persists (the bug is not present)"
      else pass "delete(Long) does not persist before the run (the bug is present)"; fi
    else
      SUMMARY="$( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -1 || true )"
      git -C "$WS" clean -fdxq -e kriya.yaml >/dev/null 2>&1 || true
      case "$SUMMARY" in *"$FIXTURE_BASELINE_SUMMARY"*) pass "fixture baseline is '$FIXTURE_BASELINE_SUMMARY' (the bug is present)" ;; *) fail "fixture baseline is '$SUMMARY', expected '$FIXTURE_BASELINE_SUMMARY'" ;; esac
      check "the scripted stub is running on 127.0.0.1:$STUB_PORT (CONTROLLED / SCRIPTED)" "$DEMO_V2_DIR/scripts/stub_ctl.sh" status
      [ "$(wc -l < "$WORK/evidence/stub-exchanges.jsonl" 2>/dev/null | tr -d ' ')" = "0" ] && pass "the stub is at script position 0 (no exchange yet)" || fail "the stub already served exchanges: restart it (stub_ctl.sh start) before a run"
    fi
  fi
  finish
fi

# ---- post: what the run left behind. These check outcomes; they do not create them. ----
[ -d "$WS/.git" ] || { fail "workspace missing"; finish; }
CHANGED="$(git -C "$WS" status --short | sed 's/^...//' | grep -v '^\.kriya/' || true)"
[ "$(printf '%s\n' "$CHANGED" | grep -c .)" = 1 ] && pass "exactly one file changed" || fail "expected exactly one changed file, got: $(printf '%s' "$CHANGED" | tr '\n' ' ')"
[ "$CHANGED" = "$FIXTURE_TARGET_FILE" ] && pass "the changed file is the declared target" || fail "the changed file is not $FIXTURE_TARGET_FILE"
if [ "$SCENARIO" = "01" ]; then
  POST_BODY="$(method_body "$WS/$FIXTURE_TARGET_FILE" "$FIXTURE_METHOD_START_REGEX")"
  if [ -z "$POST_BODY" ]; then fail "could not locate the delete(Long) method"
  elif printf '%s\n' "$POST_BODY" | grep -q -E "$FIXTURE_FIX_REGEX"; then pass "delete(Long) now persists the change"
  else fail "delete(Long) still does not persist the change"; fi
  ADDED="$(git -C "$WS" diff -U0 -- "$FIXTURE_TARGET_FILE" | grep -c '^+[^+]' || true)"
  REMOVED="$(git -C "$WS" diff -U0 -- "$FIXTURE_TARGET_FILE" | grep -c '^-[^-]' || true)"
  [ "$ADDED" -ge 1 ] && [ "$REMOVED" -le 1 ] && pass "diff is narrow ($ADDED added, $REMOVED removed)" || fail "diff is not narrow ($ADDED added, $REMOVED removed)"
  # Independent re-check, outside Kriya's own gates.
  if ( cd "$WS" && ./mvnw -o clean test >"$WORK/evidence/post-test.log" 2>&1 ); then
    SUMMARY="$(grep -E '^\[(INFO|WARNING|ERROR)\] Tests run: [0-9]+, Failures' "$WORK/evidence/post-test.log" | tail -1)"
    case "$SUMMARY" in
      *"Failures: 0, Errors: 0"*) pass "independent offline test run passes ($SUMMARY)" ;;
      *) fail "independent test run summary unexpected: $SUMMARY" ;;
    esac
  else
    fail "independent offline test run failed (see $WORK/evidence/post-test.log)"
  fi
else
  SUMMARY="$( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -1 || true )"
  case "$SUMMARY" in *"$FIXTURE_FINAL_SUMMARY"*) pass "independent pytest run: $SUMMARY" ;; *) fail "independent pytest run is '$SUMMARY', expected '$FIXTURE_FINAL_SUMMARY'" ;; esac
  git -C "$WS" clean -fdxq -e kriya.yaml >/dev/null 2>&1 || true
  # The repair path, proven from the stub's own exchange log: a SECOND Developer request exists only because Kriya rejected the first candidate. Nothing else could have asked for it.
  STUB_REPORT="$("$KRIYA_PY" -I - "$WORK/evidence/stub-exchanges.jsonl" "$DEMO_V2_DIR/scenarios/03-controlled-repair/script/controlled_script.json" <<'PY' 2>&1 || true
import json, sys
log = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
script = json.load(open(sys.argv[2]))
chats = [r for r in log if r.get("kind") == "chat"]
unscripted = [r for r in chats if r.get("unscripted")]
dev = [r.get("index") for r in chats if r.get("role") == "developer"]
print(f"exchanges={len(chats)} scripted_steps={len(script['steps'])} unscripted={len(unscripted)} developer_calls={dev}")
print("RESULT=OK" if len(chats) == len(script["steps"]) and not unscripted and dev == [0, 1] else "RESULT=BAD")
PY
)"
  case "$STUB_REPORT" in
    *"RESULT=OK"*) pass "stub log: the whole scripted sequence was served and the Developer was asked twice ($STUB_REPORT)" ;;
    *) fail "stub log does not show the full scripted sequence with a repair attempt: $STUB_REPORT" ;;
  esac
  grep -q "wrong candidate" "$DEMO_V2_DIR/scenarios/03-controlled-repair/script/controlled_script.json" && pass "the script labels its first Developer answer as the scripted wrong candidate" || fail "the script does not label the wrong candidate"
fi
check "Kriya recorded state for the run" test -n "$(ls -A "$WORK/state" 2>/dev/null)"
finish

#!/usr/bin/env bash
# Verify DETERMINISTIC facts about a scenario: the hand-off state (idle), prerequisites before a run (pre) and outcomes after one (post).
# It never starts a model, never retries, and never turns model variance into an assertion: a post check reports what
# the run produced; it does not make a run succeed.
# Usage: verify_demo.sh --scenario 01|02|03|all --phase idle|pre|post
#   idle : the OWNER START STATE after `prepare_demo.sh all`: workspaces pristine, NO operator approval, nothing running (no stub, no proxy, no search container), the 02 snapshot intact.
#   pre  : ready to run NOW (operator approval granted; scenario 03: the stub running at script position 0).
#   post : what a finished run left behind.
#   all  : every scenario for the phase, plus the global service checks; exit 0 only if every one passed.
# Exit 0 only if every check passed. Each check prints PASS/FAIL; "SKIP" is printed (and counted) when a check does not apply.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SCENARIO=""; PHASE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCENARIO="${2:-}"; shift 2 ;;
    --phase) PHASE="${2:-}"; shift 2 ;;
    -h|--help) sed -n 2,13p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$SCENARIO" ] && [ -n "$PHASE" ] || die "--scenario and --phase are required"
case "$PHASE" in idle|pre|post) ;; *) die "--phase must be idle, pre or post" ;; esac
assert_work_outside_repo

if [ "$SCENARIO" = "all" ]; then
  RC=0
  for s in 01 02 03; do "${BASH_SOURCE[0]}" --scenario "$s" --phase "$PHASE" || RC=1; done
  echo "=== demo-v2 verify: global services, phase $PHASE ==="
  GFAILS=0
  gcheck() { local label="$1"; shift; if "$@" >/dev/null 2>&1; then echo "PASS  $label"; else echo "FAIL  $label"; GFAILS=$((GFAILS+1)); fi; }
  no_listener() { [ -z "$(lsof -ti ":$1" 2>/dev/null || true)" ]; }
  no_process() { ! pgrep -f "$1" >/dev/null 2>&1; }
  gcheck "product tracked tree is clean" test -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)"
  gcheck "no process on the demo stub/proxy port 18767" no_listener 18767
  gcheck "no process on the demo proxy port 18766" no_listener 18766
  gcheck "no demo model stub/proxy process" no_process "stub_model_server.py"
  gcheck "no prompt-driver process" no_process "answer_prompts.py"
  gcheck "the demo search container $SEARXNG_CONTAINER is not running" bash -c "! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx '$SEARXNG_CONTAINER'"
  echo "=== result (global): $GFAILS failed ==="
  [ "$RC" -eq 0 ] && [ "$GFAILS" -eq 0 ]; exit $?
fi
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

case "$SCENARIO" in 01|02|03) ;; *) die "unknown scenario $SCENARIO" ;; esac

case "$SCENARIO" in 01) load_env_file "$DEMO_V2_DIR/fixtures/brownfield-spring/fixture.env" ;; 02) load_env_file "$DEMO_V2_DIR/fixtures/knowledge-skill/fixture.env" ;; 03) load_env_file "$DEMO_V2_DIR/fixtures/controlled-repair/fixture.env" ;; esac
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
    *,CONFIG_AUTHORITY_REFUSED,*) fail "the scenario configuration is NOT approved (approve it in the app with Review & Approve, or cd the workspace && kriya authority approve); Kriya refuses it as it stands" ;;
    ,,) pass "Kriya's preflight reports no blockers (the configuration is approved and loads)" ;;
    *) fail "preflight reports blockers: $blockers" ;;
  esac
}

# idle: the owner start state has NO operator approval (it is a deliberate human step at the start of each scenario). Kriya's own preflight must therefore REFUSE the configuration.
preflight_absent_check() {
  [ -x "$KRIYA_BIN" ] && [ -d "$WS" ] || { fail "kriya executable or workspace missing"; return 0; }
  # Without a placed kriya.yaml Kriya has no authority question to ask, so "not refused" would say nothing about approvals: report the real condition instead.
  [ -s "$WS/kriya.yaml" ] || { fail "no kriya.yaml is placed in the workspace, so an approval cannot be judged (run prepare_demo.sh --scenario $SCENARIO: a bare reset leaves workspaces pristine but unconfigured)"; return 0; }
  local blockers
  blockers="$(PYTHONDONTWRITEBYTECODE=1 "$KRIYA_BIN" traces --json --execution-preflight "$WS" 2>/dev/null | "$KRIYA_PY" -I -c 'import sys,json; e=json.load(sys.stdin); d=e.get("data"); print("NO_PREFLIGHT_DATA" if not d else ",".join(b["code"] for b in d.get("blockers",[])))' 2>/dev/null || echo "PREFLIGHT_UNREADABLE")"
  case ",$blockers," in
    *,CONFIG_AUTHORITY_REFUSED,*) pass "no operator approval is in place (Kriya refuses the configuration until the owner approves it)" ;;
    *) fail "a stale operator approval may exist: Kriya's preflight does not refuse the configuration ($blockers)" ;;
  esac
}
model_server_check() {
  local tags
  tags="$(curl -s -m 5 http://localhost:11434/api/tags 2>/dev/null || true)"
  case "$tags" in *'"qwen3-coder:30b"'*'"qwen3.6:35b-a3b-q4_K_M"'*|*'"qwen3.6:35b-a3b-q4_K_M"'*'"qwen3-coder:30b"'*) pass "the local model server lists qwen3-coder:30b and the fallback qwen3.6:35b-a3b-q4_K_M (no model is called)" ;; *) fail "the local model server at localhost:11434 does not list qwen3-coder:30b and qwen3.6:35b-a3b-q4_K_M (start Ollama)" ;; esac
}

if [ "$PHASE" = pre ] || [ "$PHASE" = idle ]; then
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
  if [ "$PHASE" = idle ]; then preflight_absent_check; else preflight_check; fi
  if [ "$SCENARIO" = "01" ] || [ "$SCENARIO" = "02" ]; then [ "$PHASE" = pre ] && model_server_check; fi
  if [ "$SCENARIO" = "02" ]; then
    # the preserved snapshot, the offline configuration, and the absence of every live-acquisition ingredient
    [ -d "$WORK/skills" ] && [ "$(dir_digest "$WORK/skills")" = "$SNAPSHOT_DIGEST" ] && pass "the skills directory is the PRESERVED GOVERNED ACQUISITION SNAPSHOT (digest $SNAPSHOT_DIGEST)" || fail "the skills directory does not have the snapshot digest $SNAPSHOT_DIGEST (run reset_demo.sh --scenario 02)"
    [ "$([ -d "$WORK/skills" ] && find "$WORK/skills" -type f | wc -l | tr -d ' ')" = "$SNAPSHOT_FILES" ] && pass "the snapshot has its $SNAPSHOT_FILES files" || fail "the skills directory does not hold $SNAPSHOT_FILES files"
    [ "$(dir_digest "$REPO_ROOT/$SNAPSHOT_DIR")" = "$SNAPSHOT_DIGEST" ] && pass "the preserved snapshot in the repository is untouched" || fail "the preserved snapshot in the repository was edited"
    check "the snapshot label is next to the skills" test -s "$WORK/SNAPSHOT-LABEL.txt"
    check "public lookup is disabled and there is no search endpoint in the rendered configuration" "$KRIYA_PY" -I -c "
import sys, yaml
c = yaml.safe_load(open('$WORK/config/kriya.yaml'))
assert c['autonomy']['web_lookup_enabled'] is False and c['autonomy']['web_lookup_auto_approve'] is False and c['search']['base_url'] == ''
assert c['paths']['skills'] == '$WORK/skills' and c['skills']['load_global'] is False and c['skills']['load_cwd'] is False
assert c['llm']['model'] == 'qwen3-coder:30b' and 'localhost:11434' in c['llm']['base_url']"
    check "no demo search container is running" bash -c "! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx '$SEARXNG_CONTAINER'"
    check "nothing listens on the recording-proxy port 18766 (the owner run goes straight to the model server)" bash -c "[ -z \"\$(lsof -ti :18766 2>/dev/null || true)\" ]"
    check "the goal states no API fact (static)" bash -c "! grep -n -E 'Parser|Language|language\\(\\)|root_node|set_language|tspython|tree_sitter_python|encoding=|utf16|start_byte' '$SC_DIR/goal.md'"
    SUMMARY="$( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -1 || true )"
    git -C "$WS" clean -fdxq -e kriya.yaml -e .kriya >/dev/null 2>&1 || true
    case "$SUMMARY" in *"$FIXTURE_BASELINE_SUMMARY"*) pass "fixture baseline is '$FIXTURE_BASELINE_SUMMARY' (the task is not done yet)" ;; *) fail "fixture baseline is '$SUMMARY', expected '$FIXTURE_BASELINE_SUMMARY'" ;; esac
  fi
  if [ -d "$WS/.git" ]; then
    [ -z "$(git -C "$WS" status --short)" ] && pass "workspace is clean (nothing changed yet)" || fail "workspace is not clean (run reset_demo.sh)"
    [ "$(sha256_of "$WS/$FIXTURE_TARGET_FILE")" = "$FIXTURE_TARGET_SHA256" ] && pass "target file has the pristine hash (the bug is present)" || fail "target file hash differs from the pristine hash"
    if [ "$SCENARIO" = "02" ]; then
      :   # fixture checks for 02 are done above (hash, baseline summary)
    elif [ "$SCENARIO" = "01" ]; then
      [ "$(git -C "$WS" rev-parse HEAD)" = "$FIXTURE_COMMIT" ] && pass "workspace HEAD is the fixture commit" || fail "workspace HEAD is not $FIXTURE_COMMIT"
      BODY="$(method_body "$WS/$FIXTURE_TARGET_FILE" "$FIXTURE_METHOD_START_REGEX")"
      if [ -z "$BODY" ]; then fail "could not locate the delete(Long) method"
      elif printf '%s\n' "$BODY" | grep -q -E "$FIXTURE_FIX_REGEX"; then fail "delete(Long) already persists (the bug is not present)"
      else pass "delete(Long) does not persist before the run (the bug is present)"; fi
    else
      SUMMARY="$( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -1 || true )"
      git -C "$WS" clean -fdxq -e kriya.yaml -e .kriya >/dev/null 2>&1 || true
      case "$SUMMARY" in *"$FIXTURE_BASELINE_SUMMARY"*) pass "fixture baseline is '$FIXTURE_BASELINE_SUMMARY' (the bug is present)" ;; *) fail "fixture baseline is '$SUMMARY', expected '$FIXTURE_BASELINE_SUMMARY'" ;; esac
      if [ "$PHASE" = idle ]; then
        check "the scripted stub is NOT running (the owner starts it when scenario 03 begins)" bash -c "! '$DEMO_V2_DIR/scripts/stub_ctl.sh' status"
        [ -z "$(cat "$WORK/evidence/stub-exchanges.jsonl" 2>/dev/null)" ] && pass "no scripted exchange has been served (the script cursor is at the beginning: a start always begins at position 0)" || fail "a stub exchange log from an earlier run is present: run reset_demo.sh --scenario 03"
      else
        check "the scripted stub is running on 127.0.0.1:$STUB_PORT (CONTROLLED / SCRIPTED)" "$DEMO_V2_DIR/scripts/stub_ctl.sh" status
        [ "$(wc -l < "$WORK/evidence/stub-exchanges.jsonl" 2>/dev/null | tr -d ' ')" = "0" ] && pass "the stub is at script position 0 (no exchange yet)" || fail "the stub already served exchanges: restart it (stub_ctl.sh start) before a run"
      fi
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
elif [ "$SCENARIO" = "02" ]; then
  SUMMARY="$( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -1 || true )"
  git -C "$WS" clean -fdxq -e kriya.yaml -e .kriya >/dev/null 2>&1 || true
  case "$SUMMARY" in *"$FIXTURE_FINAL_SUMMARY"*) pass "independent pytest run: $SUMMARY" ;; *) fail "independent pytest run is '$SUMMARY', expected '$FIXTURE_FINAL_SUMMARY'" ;; esac
  # The run may add exactly one thing to the skills: the skill-gap acknowledgement flag in each skill.yaml. Every rule, provenance record, instruction and example is unchanged.
  DIFF="$(diff -r "$REPO_ROOT/$SNAPSHOT_DIR" "$WORK/skills" 2>&1 | grep -v -E '^(diff -r |[0-9]+a[0-9]+|---)' | grep -v -E '^> verification_gap_acknowledged: true$' || true)"
  [ -z "$DIFF" ] && pass "the skills are unchanged apart from the skill-gap acknowledgement flag (rules, provenance, instructions and examples are byte-identical to the snapshot)" || fail "the skills differ from the snapshot beyond the acknowledgement flag: $DIFF"
  check "no search container is running" bash -c "! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx '$SEARXNG_CONTAINER'"
else
  SUMMARY="$( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -1 || true )"
  case "$SUMMARY" in *"$FIXTURE_FINAL_SUMMARY"*) pass "independent pytest run: $SUMMARY" ;; *) fail "independent pytest run is '$SUMMARY', expected '$FIXTURE_FINAL_SUMMARY'" ;; esac
  git -C "$WS" clean -fdxq -e kriya.yaml -e .kriya >/dev/null 2>&1 || true
  # Evidence that a repair attempt happened, from the stub's own exchange log: a SECOND Developer request. It does not say WHY Kriya asked again; the recorded cause (the rejected candidate, the failed gate)
  # is in the run's evidence, read in the GUI Attempts and Why panels (see SCENARIO.md, EXPECTED EVIDENCE).
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
    *"RESULT=OK"*) pass "stub log: the whole scripted sequence was served and the Developer was asked twice, i.e. a repair attempt was requested ($STUB_REPORT)" ;;
    *) fail "stub log does not show the full scripted sequence with a repair attempt: $STUB_REPORT" ;;
  esac
  grep -q "wrong candidate" "$DEMO_V2_DIR/scenarios/03-controlled-repair/script/controlled_script.json" && pass "(static) the committed script labels its first Developer answer as the scripted wrong candidate" || fail "the committed script does not label the wrong candidate"
fi
check "Kriya recorded state for the run" test -n "$(ls -A "$WORK/state" 2>/dev/null)"
finish

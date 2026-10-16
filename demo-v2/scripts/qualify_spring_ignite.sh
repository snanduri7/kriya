#!/usr/bin/env bash
# Scenario 04 MODEL-FREE qualification: proves every non-model dependency of the Spring XML + Apache Ignite + Protocol greenfield demo, with the REAL tools and no mock.
# Starts no model, makes no network call (Maven runs OFFLINE), never touches the scenario workspace, and removes everything it creates.
# Usage: qualify_spring_ignite.sh [--with-containment] [--evidence-dir DIR]
#   --with-containment   also run the reference under the OCI backend with network DENIED (needs Docker and the pinned Maven images; the Maven cache is seeded from ~/.m2, no network)
# What it proves, in order: toolchain as the desktop app sees it; the offline Maven repository; the reference candidate's identity; JUnit via surefire (the exact test count);
# the application through KRIYA'S OWN runtime-verification path (PolymorphicValidator.run_app_sequence) under the desktop app's environment AND under Java 17: Spring XML loads, Ignite 2.18.0 starts, ProtocolParser
# runs, Protocol is put into and read back from an Ignite cache, the exact expected [RESULT] line is printed, Ignite stops, exit 0; KnowledgeGuard behaviour for the goal; the skill through Kriya's SkillService.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

WITH_CONTAINMENT=0; EVIDENCE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --with-containment) WITH_CONTAINMENT=1; shift ;;
    --evidence-dir) EVIDENCE="${2:-}"; shift 2 ;;
    -h|--help) sed -n 2,9p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
assert_work_outside_repo
ensure_work_area
load_env_file "$DEMO_V2_DIR/fixtures/spring-ignite-protocol/fixture.env"
SC_DIR="$(scenario_dir 04)"
KRIYA_PY="${KRIYA_PY:-$REPO_ROOT/.venv/bin/python}"
APP_PATH="/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"   # the desktop app's fixed child PATH (ui/standalone/src/main/child_env.ts)
HELPER="$DEMO_V2_DIR/scripts/qualify_spring_ignite.py"
[ -x "$KRIYA_PY" ] || die "$KRIYA_PY not found (python3 -m venv .venv && .venv/bin/pip install -e .)"
if [ -z "$EVIDENCE" ]; then EVIDENCE="$(scenario_work 04)/evidence/qualification-$(date -u +%Y%m%dT%H%M%SZ)"; fi
mkdir -p "$EVIDENCE"
TMP="$(mktemp -d "$DEMO_V2_WORK/qualify04.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

FAILS=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; FAILS=$((FAILS+1)); }
info() { echo "INFO  $*"; }
echo "=== scenario 04 model-free qualification (evidence: $EVIDENCE) ==="

# ---- 1. toolchain as the desktop app sees it (MEASURED, not assumed) ----
APP_JAVA="$(env -i PATH="$APP_PATH" HOME="$HOME" java -version 2>&1 | head -1 || true)"
APP_MVN="$(env -i PATH="$APP_PATH" HOME="$HOME" mvn -version 2>&1 | sed -n '1p;3p' | tr '\n' ' ' || true)"
SHELL_JAVA="$(java -version 2>&1 | head -1 || true)"
info "desktop app environment: java = $APP_JAVA"
info "desktop app environment: mvn  = $APP_MVN"
info "this shell:              java = $SHELL_JAVA (JAVA_HOME=${JAVA_HOME:-unset})"
printf 'app_java=%s\napp_mvn=%s\nshell_java=%s\nshell_java_home=%s\ngit_head=%s\ngit_tracked_status=%s\n' "$APP_JAVA" "$APP_MVN" "$SHELL_JAVA" "${JAVA_HOME:-unset}" \
  "$(git -C "$REPO_ROOT" rev-parse HEAD)" "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no | tr '\n' ';')" > "$EVIDENCE/toolchain.txt"
APP_MAJOR="$(printf '%s' "$APP_JAVA" | sed -n 's/.*version "\([0-9]*\).*/\1/p')"
if [ -n "$APP_MAJOR" ] && [ "$APP_MAJOR" -ge 17 ]; then pass "java $APP_MAJOR (>= 17) is what the desktop app's PATH resolves; the project targets Java 17 through its compiler settings"; else fail "java 17+ is not available on the desktop app's PATH ('$APP_JAVA')"; fi
env -i PATH="$APP_PATH" HOME="$HOME" mvn -version >/dev/null 2>&1 && pass "mvn is available on the desktop app's PATH" || fail "mvn is not available on the desktop app's PATH"
JAVA17_HOME="$(/usr/libexec/java_home -v 17 2>/dev/null || true)"

# ---- 2. offline Maven repository: the exact artifacts, present as files ----
M2="$HOME/.m2/repository"
for artifact in "org/apache/ignite/ignite-core/2.18.0/ignite-core-2.18.0.jar" "org/apache/ignite/ignite-spring/2.18.0/ignite-spring-2.18.0.jar" "org/springframework/spring-context/5.3.39/spring-context-5.3.39.jar" \
                "org/junit/jupiter/junit-jupiter/5.8.2/junit-jupiter-5.8.2.pom" "org/apache/maven/plugins/maven-surefire-plugin/2.22.2/maven-surefire-plugin-2.22.2.jar" \
                "org/apache/maven/plugins/maven-compiler-plugin/3.11.0/maven-compiler-plugin-3.11.0.jar" "org/codehaus/mojo/exec-maven-plugin/3.1.0/exec-maven-plugin-3.1.0.jar"; do
  [ -s "$M2/$artifact" ] && pass "offline repository holds $artifact" || fail "offline repository is MISSING $artifact"
done

# ---- 3. identity of what is being qualified ----
[ "$(dir_digest "$DEMO_V2_DIR/$REFERENCE_DIR")" = "$REFERENCE_DIGEST" ] && pass "reference candidate digest $REFERENCE_DIGEST" || fail "reference candidate does not have its recorded digest"
[ "$(dir_digest "$REPO_ROOT/$SKILL_SOURCE_DIR")" = "$SKILL_DIGEST" ] && pass "skill $SKILL_NAME digest $SKILL_DIGEST" || fail "skill $SKILL_NAME does not have its recorded digest"

# ---- 4. build + JUnit, offline, under the desktop app's environment ----
REF="$TMP/reference"; cp -R "$DEMO_V2_DIR/$REFERENCE_DIR" "$REF"
if ( cd "$REF" && env -i PATH="$APP_PATH" HOME="$HOME" mvn -o -B clean test >"$EVIDENCE/mvn-test.log" 2>&1 ); then
  N="$(sed -n 's/^\[INFO\] Tests run: \([0-9]*\), Failures: 0, Errors: 0, Skipped: 0$/\1/p' "$EVIDENCE/mvn-test.log" | tail -1)"
  [ "$N" = "$REFERENCE_TEST_COUNT" ] && pass "offline 'mvn test' (compile + JUnit 5.8.2 via surefire 2.22.2, including real embedded-Ignite round-trip tests): $N/$N tests passed" || fail "offline 'mvn test' passed but ran '$N' tests, expected $REFERENCE_TEST_COUNT"
else fail "offline 'mvn test' failed (see $EVIDENCE/mvn-test.log)"; fi

# ---- 4b. Kriya's host mode runs Maven WITHOUT -o: prove the reference needs no remote at all (every remote is a dead mirror with the id "central", so any fetch would fail) ----
cat > "$TMP/dead-remote-settings.xml" <<'EOS'
<settings><mirrors><mirror><id>central</id><mirrorOf>*</mirrorOf><url>http://127.0.0.1:9/dead</url></mirror></mirrors></settings>
EOS
DEAD="$TMP/dead-remote"; cp -R "$DEMO_V2_DIR/$REFERENCE_DIR" "$DEAD"
if ( cd "$DEAD" && env -i PATH="$APP_PATH" HOME="$HOME" mvn -B -s "$TMP/dead-remote-settings.xml" clean test >"$EVIDENCE/mvn-test-dead-remote.log" 2>&1 ) \
   && ( cd "$DEAD" && env -i PATH="$APP_PATH" HOME="$HOME" mvn -B -q -s "$TMP/dead-remote-settings.xml" compile exec:exec -Dexec.mainClass="$REFERENCE_MAIN_CLASS" >"$EVIDENCE/mvn-run-dead-remote.log" 2>&1 ) \
   && grep -qxF "$EXPECTED_RESULT_LINE" "$EVIDENCE/mvn-run-dead-remote.log"; then
  pass "Maven WITHOUT -o and with every remote unreachable still builds, tests and runs the reference: the scenario needs no public network when its dependencies are cached"
else fail "the reference needs a remote repository even though everything is cached (see $EVIDENCE/mvn-*-dead-remote.log)"; fi

# ---- 5. the application through Kriya's own runtime-verification path ----
run_runtime_leg() {
  local label="$1"; shift
  local out="$EVIDENCE/runtime-$label.json"
  if env -i "$@" PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -I "$HELPER" runtime --repo "$REPO_ROOT" --fixture "$DEMO_V2_DIR/$REFERENCE_DIR" ${RUNTIME_ARGS:-} >"$out" 2>"$EVIDENCE/runtime-$label.err"; then
    if [ -n "${RUNTIME_ARGS:-}" ]; then WHAT="the cache itself shifted time by 1000 and the program printed time=1700001000, equals=false: the [RESULT] line is built from the value READ BACK from Ignite"
    else WHAT="exact [RESULT] line printed from the retrieved Protocol, a Protocol was put into an Ignite cache (marshaller registry)"; fi
    pass "runtime leg '$label': Spring XML loaded, Ignite 2.18.0 started and stopped, ProtocolParser ran, $WHAT, exit 0 ($("$KRIYA_PY" -I -c "import json; r=json.load(open('$out')); print(str(r['elapsed_seconds'])+' s')"))"
  else fail "runtime leg '$label' failed (see $out and $EVIDENCE/runtime-$label.err)"; fi
}
run_runtime_leg "desktop-app-environment" PATH="$APP_PATH" HOME="$HOME"
RUNTIME_ARGS="--witness" run_runtime_leg "desktop-app-environment-witness" PATH="$APP_PATH" HOME="$HOME"
if [ -n "$JAVA17_HOME" ]; then
  run_runtime_leg "java17" PATH="$JAVA17_HOME/bin:$APP_PATH" HOME="$HOME" JAVA_HOME="$JAVA17_HOME"
  RUNTIME_ARGS="--witness" run_runtime_leg "java17-witness" PATH="$JAVA17_HOME/bin:$APP_PATH" HOME="$HOME" JAVA_HOME="$JAVA17_HOME"
else fail "no Java 17 installation found by java_home: the explicit Java 17 leg cannot run (the scenario targets Java 17)"; fi
# ---- 5b. mutation self-check of the read-back proof: every way of discarding, replacing or bypassing cache.get must be KILLED (JUnit or the witness leg); the control must pass ----
if env -i PATH="$APP_PATH" HOME="$HOME" PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -I "$HELPER" mutants --repo "$REPO_ROOT" --fixture "$DEMO_V2_DIR/$REFERENCE_DIR" >"$EVIDENCE/read-back-mutants.json" 2>"$EVIDENCE/read-back-mutants.err"; then
  pass "read-back mutation self-check: the unmutated reference passes and every cache.get mutant is killed (no surviving mutant): $("$KRIYA_PY" -I -c "import json; r=json.load(open('$EVIDENCE/read-back-mutants.json')); print(', '.join(n+' <- '+'+'.join(c['killed_by']) for n,c in r['cases'].items() if n!='unmutated_reference'))")"
else fail "read-back mutation self-check FAILED: a mutant survived or the control did not pass (see $EVIDENCE/read-back-mutants.json)"; fi
! pgrep -f "com.example.Main" >/dev/null 2>&1 && pass "no application process is left running" || fail "an application process (com.example.Main) is still running"

# ---- 6. optional: the same candidate under OCI containment, network DENIED ----
if [ "$WITH_CONTAINMENT" = 1 ]; then
  CONTAINERS_BEFORE="$(docker ps -a --format '{{.Names}}' 2>/dev/null | sort | tr '\n' ' ' || true)"
  if env -i PATH="$APP_PATH" HOME="$HOME" PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -I "$HELPER" runtime --repo "$REPO_ROOT" --fixture "$DEMO_V2_DIR/$REFERENCE_DIR" --contained >"$EVIDENCE/runtime-contained.json" 2>"$EVIDENCE/runtime-contained.err"; then
    pass "containment leg: the SAME candidate ran under the OCI backend with network DENIED (loopback is sufficient for the embedded node)"
  else fail "containment leg failed (see $EVIDENCE/runtime-contained.json)"; fi
  if env -i PATH="$APP_PATH" HOME="$HOME" PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -I "$HELPER" runtime --repo "$REPO_ROOT" --fixture "$DEMO_V2_DIR/$REFERENCE_DIR" --contained --witness >"$EVIDENCE/runtime-contained-witness.json" 2>"$EVIDENCE/runtime-contained-witness.err"; then
    pass "containment witness leg: under OCI with network DENIED the printed line still comes from the value read back from Ignite"
  else fail "containment witness leg failed (see $EVIDENCE/runtime-contained-witness.json)"; fi
  CONTAINERS_AFTER="$(docker ps -a --format '{{.Names}}' 2>/dev/null | sort | tr '\n' ' ' || true)"
  [ "$CONTAINERS_BEFORE" = "$CONTAINERS_AFTER" ] && pass "the containment leg left no container behind" || fail "containers before/after differ: '$CONTAINERS_BEFORE' vs '$CONTAINERS_AFTER'"
fi

# ---- 7. KnowledgeGuard on the goal; 8. the skill through Kriya's SkillService ----
mkdir -p "$TMP/work/workspace" "$TMP/work/skills"
cp -R "$REPO_ROOT/$SKILL_SOURCE_DIR" "$TMP/work/skills/$SKILL_NAME"
sed -e "s#@WORK@#$TMP/work#g" "$SC_DIR/config/kriya.template.yaml" > "$TMP/work/kriya.yaml"
if env -i PATH="$APP_PATH" HOME="$HOME" "$KRIYA_PY" -I "$HELPER" guard --repo "$REPO_ROOT" --goal "$SC_DIR/goal.md" --skill "$REPO_ROOT/$SKILL_SOURCE_DIR" >"$EVIDENCE/knowledge-guard.json" 2>"$EVIDENCE/knowledge-guard.err"; then
  pass "KnowledgeGuard: the default guard WOULD stop a desktop run on this goal (with or without the skill); offline mode + the verified skill clears it; offline mode alone does not"
else fail "KnowledgeGuard behaviour differs from the measured one (see $EVIDENCE/knowledge-guard.json)"; fi
if env -i PATH="$APP_PATH" HOME="$HOME" "$KRIYA_PY" -I "$HELPER" skill --repo "$REPO_ROOT" --config "$TMP/work/kriya.yaml" --workspace "$TMP/work/workspace" --goal "$SC_DIR/goal.md" >"$EVIDENCE/skill-load.json" 2>"$EVIDENCE/skill-load.err"; then
  pass "skill $SKILL_NAME loads through Kriya's SkillService/SkillEngine from the scenario configuration, is verified, and is relevant to the goal"
else fail "the skill does not load/select as expected (see $EVIDENCE/skill-load.json)"; fi

echo "=== qualification result: $FAILS failed (evidence: $EVIDENCE) ==="
[ "$FAILS" -eq 0 ]

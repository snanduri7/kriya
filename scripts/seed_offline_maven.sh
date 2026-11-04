#!/usr/bin/env bash
# PRD-034 hosted parity: seed the local Maven repository (~/.m2) with the closure the demo-v2 Spring/Ignite reference needs OFFLINE
# (tests/test_demo_v2_spring_ignite.py: Ignite 2.18.0, JUnit 5.8.2), from Maven Central by the reference pom's own pinned coordinates.
# It never copies a developer cache. Two Mavens resolve different default plugins, so BOTH are run once online: the host Maven (the post-phase
# checks) and the canonical contained Maven image (the mutant qualification, which seeds its cache from ~/.m2 and denies the network).
#   scripts/seed_offline_maven.sh        (needs the host mvn, a reachable Docker daemon and the pinned maven image: pinned_images.py --pull)
set -euo pipefail
cd "$(dirname "$0")/.."
REFERENCE="demo-v2/fixtures/spring-ignite-protocol/reference"
IMAGE="maven:3.9-eclipse-temurin-17"
REPO="$HOME/.m2/repository"
# HOSTED-OFFLINE-SEED-GOALS-001: the offline consumers run `mvn -o clean test` (post-checks, mutant qualification) and `mvn -o compile exec:exec -Dexec.mainClass=...`
# (demo-v2/scripts/verify_demo.sh, qualify_spring_ignite.py). Seeding only `test` left maven-clean-plugin and the exec plugin (+ its prefix metadata) out of ~/.m2: they
# resolve on a developer machine that accumulated them over time and failed on a cold hosted runner (MEASURED, run 41758354007).
MAIN_CLASS="com.example.Main"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kriya-seed-m2.XXXXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$REPO"
cp -R "$REFERENCE/." "$WORK/"
echo "[seed] host Maven: $(mvn -v | head -1)"
for goals in "clean test" "compile exec:exec -Dexec.mainClass=$MAIN_CLASS"; do
  # shellcheck disable=SC2086 # the goals are a word list on purpose
  mvn -B -ntp -q -Dmaven.repo.local="$REPO" -f "$WORK/pom.xml" $goals
done
echo "[seed] contained Maven: $IMAGE"
for goals in "clean test" "compile exec:exec -Dexec.mainClass=$MAIN_CLASS"; do
  # shellcheck disable=SC2086
  docker run --rm --pull never --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$REPO:/m2repo" -v "$WORK:/work" -w /work "$IMAGE" \
    mvn -B -ntp -q -Dmaven.repo.local=/m2repo $goals
done
for artifact in org/apache/ignite/ignite-core/2.18.0/ignite-core-2.18.0.jar org/junit/jupiter/junit-jupiter/5.8.2/junit-jupiter-5.8.2.pom \
                org/apache/maven/plugins/maven-clean-plugin org/codehaus/mojo/exec-maven-plugin; do
  [ -e "$REPO/$artifact" ] || { echo "[seed] MISSING after seeding: $artifact" >&2; exit 1; }
done
echo "[seed] offline repository holds Ignite 2.18.0 and JUnit 5.8.2 (+ every plugin the reference's lifecycle resolves)"

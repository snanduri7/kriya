#!/usr/bin/env bash
# PRD-034: provision the HOST Maven repository (~/.m2/repository) that the demo-v2 Spring/Ignite post-check and mutant tests run OFFLINE against - from the FROZEN,
# ATTESTED certification repository, never from live Maven Central.
#
# What it does, in order (any failure stops it; nothing is guessed or fetched):
#   1. the frozen repository (default $KRIYA_CERT_MAVEN_REPO or ~/.cache/kriya-cert-maven/repo; provision it first with
#      `scripts/certification_maven_repo.py fetch tests/certification/maven_repository.manifest.json DIR`) is VERIFIED byte-exact and attested against the committed manifest;
#   2. exactly the files listed in tests/certification/demo_v2_m2_closure.txt - the jar/pom closure of the reference project under the authoritative Maven (MEASURED: the
#      pinned POM resolves the same 266 files for the host Apache Maven 3.10.0 and for the contained Maven image) - are copied into ~/.m2/repository; a file already there
#      with different bytes is a refusal, never an overwrite;
#   3. the authoritative Maven (MAVEN_HOME, scripts/certification_maven_toolchain.py) proves the result OFFLINE on a throw-away copy of the reference:
#      `mvn -o clean test` and `mvn -o compile exec:exec`. No network is used by this script.
# Maven is never run online here, so what the offline consumers see is the frozen set, not whatever Central answered today.
#   scripts/seed_offline_maven.sh [FROZEN_REPO_DIR]
set -euo pipefail
cd "$(dirname "$0")/.."
REFERENCE="demo-v2/fixtures/spring-ignite-protocol/reference"
MANIFEST="tests/certification/maven_repository.manifest.json"
CLOSURE="tests/certification/demo_v2_m2_closure.txt"
FROZEN="${1:-${KRIYA_CERT_MAVEN_REPO:-$HOME/.cache/kriya-cert-maven/repo}}"
REPO="$HOME/.m2/repository"
PY="${KRIYA_SEED_PY:-python3}"
MAIN_CLASS="com.example.Main"

"$PY" -I scripts/certification_maven_toolchain.py verify "${MAVEN_HOME:-/nonexistent-maven-home}" >/dev/null \
  || { echo "[seed] MAVEN_HOME is not the authoritative Maven (3.10.0): provision it with scripts/certification_maven_toolchain.py provision DIR" >&2; exit 1; }
MVN="$(cd "$MAVEN_HOME" && pwd -P)/bin/mvn"
"$PY" -I scripts/certification_maven_repo.py verify "$FROZEN" "$MANIFEST" --require-attested >/dev/null \
  || { echo "[seed] the frozen repository $FROZEN is not the attested set of $MANIFEST (fetch it first)" >&2; exit 1; }

echo "[seed] authoritative Maven: $("$MVN" -v | head -1)"
copied=0; present=0
while IFS= read -r relative; do
  [ -n "$relative" ] || continue
  source_file="$FROZEN/$relative"; target="$REPO/$relative"
  [ -f "$source_file" ] || { echo "[seed] the frozen repository lacks $relative" >&2; exit 1; }
  if [ -e "$target" ]; then
    cmp -s "$source_file" "$target" || { echo "[seed] REFUSED: $target exists with bytes that differ from the frozen file" >&2; exit 1; }
    present=$((present+1))
  else
    mkdir -p "$(dirname "$target")"; cp "$source_file" "$target"; copied=$((copied+1))
  fi
done < "$CLOSURE"
echo "[seed] $copied file(s) copied from the frozen repository, $present already present with identical bytes"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/kriya-seed-m2.XXXXXXXX")"
trap 'rm -rf "$WORK"' EXIT
cp -R "$REFERENCE/." "$WORK/"
LOG="$WORK/offline-proof.log"
( cd "$WORK" && "$MVN" -o -B -ntp -q clean test ) >"$LOG" 2>&1 || { tail -40 "$LOG" >&2; echo "[seed] the offline build of the reference FAILED on the seeded repository" >&2; exit 1; }
( cd "$WORK" && "$MVN" -o -B -ntp -q compile exec:exec -Dexec.mainClass="$MAIN_CLASS" ) >"$LOG" 2>&1 || { tail -40 "$LOG" >&2; echo "[seed] the offline run of the reference FAILED on the seeded repository" >&2; exit 1; }
echo "[seed] offline repository proven: the reference builds, passes its tests and runs with the authoritative Maven and no network"

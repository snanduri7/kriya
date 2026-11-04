#!/usr/bin/env bash
# Shared helpers for demo-v2/scripts/*.sh. Sourced, never executed. No network, no model call, no Kriya run.
set -euo pipefail

DEMO_V2_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$DEMO_V2_DIR/.." && pwd)"
DEMO_V2_WORK="${DEMO_V2_WORK:-$HOME/kriya-demo-v2-work}"
BASELINE_TAG="kriya-v1.2.0-pre-modularization"

die() { echo "FATAL: $*" >&2; exit 1; }
note() { echo "  $*"; }

# Reads KEY=VALUE lines (no quoting, no commands) into the environment, expanding only a leading $HOME.
load_env_file() {
  local file="$1" line key value
  [ -f "$file" ] || die "missing $file"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"; value="${line#*=}"
    case "$key" in *[!A-Za-z0-9_]*|'') die "bad key in $file: $key" ;; esac
    value="${value/#\$HOME/$HOME}"
    export "$key=$value"
  done < "$file"
}

scenario_dir() {
  local id="$1" match
  match="$(ls -d "$DEMO_V2_DIR"/scenarios/"$id"-* 2>/dev/null | head -1 || true)"
  [ -n "$match" ] || die "unknown scenario '$id' (expected 00, 01, 02, 03 or 04)"
  echo "$match"
}

sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }

# One digest for a whole directory tree: the sha256 of the sorted "<file sha256>  <relative path>" lines. An empty directory digests to the sha256 of the empty string.
dir_digest() { ( cd "$1" && find . -type f | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(shasum -a 256 "$f" | awk '{print $1}')" "$f"; done ) | shasum -a 256 | awk '{print $1}'; }

# ---- demo services (everything a scenario can leave running) ----
SEARXNG_CONTAINER="kriya-searxng-demo"   # the optional live-acquisition search endpoint (scenario 02); never needed for the owner rehearsal
DEMO_PORTS="18766 18767"                 # 18766: scenario 02 recording proxy (scenario02_run.sh); 18767: scenario 03 scripted stub

searxng_running() { command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$SEARXNG_CONTAINER"; }
stop_searxng() { if searxng_running; then docker stop "$SEARXNG_CONTAINER" >/dev/null && echo "stopped container $SEARXNG_CONTAINER"; fi; return 0; }
# Pids listening on a demo port. Only a process whose command line is the demo stub/proxy is ever stopped; anything else is reported and refused, never killed.
stop_demo_listeners() {
  local port pid cmd
  for port in $DEMO_PORTS; do
    for pid in $(lsof -ti ":$port" 2>/dev/null || true); do
      cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      case "$cmd" in
        *stub_model_server.py*) kill "$pid" 2>/dev/null || true; echo "stopped demo stub/proxy on port $port (pid $pid)" ;;
        *) die "port $port is held by another process (pid $pid): $cmd - stop it yourself; the demo never kills processes it did not start" ;;
      esac
    done
  done
  return 0
}

# The scenario's materialised work area. Always under DEMO_V2_WORK, never inside the repository.
scenario_work() { echo "$DEMO_V2_WORK/scenario-$1"; }

# Resolves PATH physically (symlinks followed) even when its tail does not exist yet: the nearest existing ancestor is resolved with `cd -P`, the rest is appended.
physical_path() {
  local path="$1" tail="" parent
  while [ ! -d "$path" ]; do tail="/$(basename "$path")$tail"; parent="$(dirname "$path")"; [ "$parent" = "$path" ] && break; path="$parent"; done
  echo "$(cd -P "$path" && pwd)$tail"
}

# Refuses a work area that is inside (or is) the repository checkout, however it is spelled: missing parents and symlinks are resolved first.
assert_work_outside_repo() {
  local work repo
  work="$(physical_path "$DEMO_V2_WORK")"; repo="$(cd -P "$REPO_ROOT" && pwd)"
  case "$work" in
    "$repo"|"$repo"/*) die "DEMO_V2_WORK ($DEMO_V2_WORK) is inside the repository" ;;
  esac
}

# Every `rm -rf` in these scripts targets a path under the work area. Refuse to use an existing, non-empty directory that this tooling did not create (it carries a marker),
# so a mistaken DEMO_V2_WORK can never name a directory with someone else's files in it.
ensure_work_area() {
  mkdir -p "$DEMO_V2_WORK"
  if [ ! -f "$DEMO_V2_WORK/.demo-v2-work" ]; then
    [ -z "$(ls -A "$DEMO_V2_WORK" 2>/dev/null)" ] || die "DEMO_V2_WORK ($DEMO_V2_WORK) is not empty and was not created by demo-v2; refusing to use it"
    echo "created by demo-v2/scripts; safe to delete" > "$DEMO_V2_WORK/.demo-v2-work"
  fi
}

# Prints the body of the first method whose declaration line matches the ERE $2 in file $1, ending at the first line that is
# exactly four spaces and a closing brace (the fixture's formatting). Used to scope a check to one method, not the whole file.
method_body() {
  # The pattern travels in the environment: awk -v would process its backslash escapes and break the regular expression.
  METHOD_RE="$2" awk '$0 ~ ENVIRON["METHOD_RE"] {on=1} on {print} on && $0 == "    }" {exit}' "$1"
}

# ---- the authoritative host Maven (PRD-034, owner ruling 2026-11-04) ----
# The host-side Maven of the demo-v2 / PRD-034 path is exactly the one pinned in tests/certification/maven_toolchain.json (Apache Maven 3.10.0), provisioned by
#   python3 scripts/certification_maven_toolchain.py provision DIR   and exposed as   export MAVEN_HOME=DIR
# An ambient mvn (the runner image's /usr/bin/mvn, Homebrew, a developer's own) is NEVER the authority, even when it reports the same version: `verify` refuses a tree without
# the provisioning marker. No fallback: without MAVEN_HOME the Maven-running scripts stop. The desktop app's fixed child PATH (ui/standalone/src/main/child_env.ts, product
# code, untouched) stays the BASE of the scripts' PATH for every other tool; only `mvn` is the authority's, because its directory is put in front.
DEMO_V2_APP_BASE_PATH="${DEMO_V2_APP_BASE_PATH:-/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin}"   # the override is a test seam (a decoy mvn inside the base); production never sets it
# Non-dying: sets MAVEN_AUTHORITY_BIN, or MAVEN_AUTHORITY_ERROR and returns 1 (a checker can then REPORT the refusal as a FAIL line instead of aborting).
resolve_maven_authority() {
  MAVEN_AUTHORITY_BIN=""; MAVEN_AUTHORITY_ERROR=""
  if [ -z "${MAVEN_HOME:-}" ]; then
    MAVEN_AUTHORITY_ERROR="MAVEN_HOME is not set: the demo-v2 / PRD-034 path runs Maven only from the pinned authority (Apache Maven 3.10.0). Provision it once: python3 $REPO_ROOT/scripts/certification_maven_toolchain.py provision DIR && export MAVEN_HOME=DIR"
    return 1
  fi
  local py="${KRIYA_MAVEN_VERIFY_PY:-python3}" verdict
  if ! verdict="$("$py" -I "$REPO_ROOT/scripts/certification_maven_toolchain.py" verify "$MAVEN_HOME" 2>&1)"; then
    MAVEN_AUTHORITY_ERROR="MAVEN_HOME ($MAVEN_HOME) is not the authoritative Maven: $verdict"
    return 1
  fi
  MAVEN_AUTHORITY_BIN="$(cd "$MAVEN_HOME" && pwd -P)/bin"
}
require_maven_authority() { resolve_maven_authority || die "$MAVEN_AUTHORITY_ERROR"; }
# The PATH the Maven-running checks use: the authority's bin FIRST, then the desktop app's fixed base.
app_path() { echo "$MAVEN_AUTHORITY_BIN:$DEMO_V2_APP_BASE_PATH"; }
# Every host-side Maven invocation of the path goes through this: the authority's mvn by ABSOLUTE path, so no PATH order, no ambient or decoy mvn can stand in for it.
authority_mvn() {
  [ -n "${MAVEN_AUTHORITY_BIN:-}" ] || { echo "the authoritative Maven is unavailable: ${MAVEN_AUTHORITY_ERROR:-not resolved}" >&2; return 127; }
  "$MAVEN_AUTHORITY_BIN/mvn" "$@"
}

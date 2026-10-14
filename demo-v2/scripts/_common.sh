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
  [ -n "$match" ] || die "unknown scenario '$id' (expected 01, 02 or 03)"
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

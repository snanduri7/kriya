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

# The scenario's materialised work area. Always under DEMO_V2_WORK, never inside the repository.
scenario_work() { echo "$DEMO_V2_WORK/scenario-$1"; }

# Refuses a work area that is inside the repository checkout (evidence and clones must never reach git).
assert_work_outside_repo() {
  case "$(cd "$(dirname "$DEMO_V2_WORK")" 2>/dev/null && pwd)/$(basename "$DEMO_V2_WORK")" in
    "$REPO_ROOT"|"$REPO_ROOT"/*) die "DEMO_V2_WORK ($DEMO_V2_WORK) is inside the repository" ;;
  esac
}

# Prints the body of the first method whose declaration line matches the ERE $2 in file $1, ending at the first line that is
# exactly four spaces and a closing brace (the fixture's formatting). Used to scope a check to one method, not the whole file.
method_body() {
  # The pattern travels in the environment: awk -v would process its backslash escapes and break the regular expression.
  METHOD_RE="$2" awk '$0 ~ ENVIRON["METHOD_RE"] {on=1} on {print} on && $0 == "    }" {exit}' "$1"
}

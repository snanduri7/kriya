#!/usr/bin/env bash
# Owner visual walkthrough: ONE command that prepares safe demo workspaces and launches the real Kriya desktop app against THIS tree.
#
#   handover/PRODUCT-72H/walkthrough/launch.sh [--reset] [--prepare-only] [--no-build]
#
#   --reset         wipe and recreate the walkthrough home (state, user data, demo repos): Project Memory starts empty, so "learned -> reused -> stale" can be shown from zero.
#   --prepare-only  do everything except start the app (preflight, repos, settings); used to check the kit.
#   --no-build      skip `npm run build:cockpit` (only when the bundle is already built from this tree).
#
# Everything lives OUTSIDE the repository, under $KRIYA_WALKTHROUGH_HOME (default ~/kriya-walkthrough):
#   state/       the ONE state directory the app reads AND every run writes (mode 0700). Never your real ~/.kriya state.
#   userdata/    the app's own settings (points kriyaExecutable at this tree's .venv/bin/kriya).
#   projects/    an empty folder to choose as the parent in "New Project" (the greenfield demo).
#   brownfield-python/  a tiny calculator repo (git, one commit) for the existing-repository demo.
#   java-maven/         a tiny Maven/JUnit 5 repo (git, one commit) for the Java Project Memory demo.
# Nothing here touches a repository of yours, enables outward lookup, pushes, or starts a model. The app starts from the Home screen: you open the folders yourself.
set -euo pipefail

RESET=0; PREPARE_ONLY=0; BUILD=1
while [ $# -gt 0 ]; do
  case "$1" in
    --reset) RESET=1 ;;
    --prepare-only) PREPARE_ONLY=1 ;;
    --no-build) BUILD=0 ;;
    -h|--help) sed -n 2,19p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
die() { echo "ERROR: $*" >&2; exit 1; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
KRIYA_BIN="$REPO/.venv/bin/kriya"
HOME_DIR="${KRIYA_WALKTHROUGH_HOME:-$HOME/kriya-walkthrough}"
case "$HOME_DIR" in /*kriya-walkthrough*) ;; *) die "KRIYA_WALKTHROUGH_HOME must be an absolute path containing 'kriya-walkthrough' (got $HOME_DIR)" ;; esac
case "$HOME_DIR" in "$REPO"|"$REPO"/*) die "the walkthrough home must be outside the repository" ;; esac

echo "== Kriya walkthrough kit"
echo "   tree: $REPO ($(git -C "$REPO" rev-parse --short HEAD) on $(git -C "$REPO" rev-parse --abbrev-ref HEAD))"
[ -x "$KRIYA_BIN" ] || die "$KRIYA_BIN not found (python3 -m venv .venv && .venv/bin/pip install -e .)"
[ -d "$REPO/ui/node_modules" ] || die "$REPO/ui/node_modules missing (cd ui && npm ci)"
if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then echo "   WARNING: tracked files in this tree are modified; the walkthrough would not match a commit."; fi

# --- preflight: what the live demo needs. Reported, never silently skipped.
FAIL=0
if tags="$(curl -fsS --max-time 5 http://localhost:11434/api/tags 2>/dev/null)"; then
  for m in qwen3-coder:30b nomic-embed-text; do
    if printf '%s' "$tags" | grep -q "\"$m"; then echo "   ok   model server answers and serves $m"; else echo "   FAIL model server answers but does not serve $m (ollama pull $m)"; FAIL=1; fi
  done
else echo "   FAIL no model server answers on localhost:11434 (start Ollama)"; FAIL=1; fi
if docker info >/dev/null 2>&1; then echo "   ok   Docker daemon is running (verification is contained and cannot run without it)"; else echo "   FAIL Docker is not running (start Docker Desktop)"; FAIL=1; fi
if pgrep -f "bin/pytest|-m pytest" >/dev/null 2>&1; then echo "   WARN a pytest run is in progress: do not run a live Kriya run while a Docker test run is active"; fi
[ "$FAIL" = 0 ] || { [ "$PREPARE_ONLY" = 1 ] && echo "   (preflight failures reported; continuing because --prepare-only)" || die "preflight failed: fix the lines marked FAIL, then run again"; }

# --- home, state, user data
if [ "$RESET" = 1 ] && [ -d "$HOME_DIR" ]; then
  [ -f "$HOME_DIR/.kriya-walkthrough" ] || die "$HOME_DIR exists but was not created by this kit (no .kriya-walkthrough marker): refusing to delete it"
  rm -rf "$HOME_DIR"; echo "   reset: removed $HOME_DIR"
fi
mkdir -p "$HOME_DIR/state" "$HOME_DIR/userdata" "$HOME_DIR/projects"
chmod 700 "$HOME_DIR/state"; touch "$HOME_DIR/.kriya-walkthrough"

seed_repo() {  # seed_repo <name> : a git repo with one commit from walkthrough/seeds/<name>, created only when absent
  local name="$1" dest="$HOME_DIR/$1"
  if [ -d "$dest/.git" ]; then echo "   keep $dest (exists; use --reset for a fresh copy)"; return; fi
  mkdir -p "$dest"; cp -R "$HERE/seeds/$name/." "$dest/"
  git -C "$dest" init -q
  git -C "$dest" -c user.name="Kriya Demo" -c user.email="demo@example.invalid" add -A
  git -C "$dest" -c user.name="Kriya Demo" -c user.email="demo@example.invalid" commit -q -m "initial $name"
  echo "   made $dest"
}
seed_repo brownfield-python
seed_repo java-maven

cat > "$HOME_DIR/userdata/kriya-ui-settings.json" <<JSON
{
  "editor": "vscode",
  "kriyaExecutable": "$KRIYA_BIN",
  "workspacePath": null,
  "configDirectory": null,
  "recentWorkspaces": []
}
JSON

echo "   state:      $HOME_DIR/state"
echo "   new project parent folder:  $HOME_DIR/projects"
echo "   existing repos:             $HOME_DIR/brownfield-python   $HOME_DIR/java-maven"
printf '%s' "$HOME_DIR" | pbcopy 2>/dev/null && echo "   (the walkthrough home path is on your clipboard: in a folder dialog press Cmd+Shift+G and paste)"

[ "$PREPARE_ONLY" = 1 ] && { echo "== prepared (app not started)"; exit 0; }

# --- build and start the app
cd "$REPO/ui/standalone"
if [ "$BUILD" = 1 ]; then echo "== building the cockpit from this tree (npm run build:cockpit)"; npm run build:cockpit >"$HOME_DIR/build.log" 2>&1 || { tail -20 "$HOME_DIR/build.log"; die "cockpit build failed (log: $HOME_DIR/build.log)"; }; fi
pgrep -f "Electron.*$REPO/ui/standalone" >/dev/null 2>&1 && die "a Kriya desktop app from this tree is already running: quit it (Cmd+Q) and run again, so it starts with this state"
echo "== starting Kriya (quit with Cmd+Q). Follow handover/PRODUCT-72H/DEMO_RUNBOOK.md"
export KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_STATE_DIR="$HOME_DIR/state"
exec npx electron . --user-data-dir="$HOME_DIR/userdata"

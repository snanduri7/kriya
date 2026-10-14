#!/usr/bin/env bash
# Scenario 02 (knowledge reuse): ONE terminal run of the isolated task in its own fresh work area. Real local model behind a transparent recording proxy.
# Usage: scenario02_run.sh --run-id ID --mode noskill|skill|acquire [--skills-from DIR] [--port 18766]
#   noskill : empty skills directory, public lookup OFF            (the baseline)
#   skill   : COPY of --skills-from as the skills directory, lookup OFF (the offline reuse run)
#   acquire : empty skills directory, public lookup ON, per-query approval (needs the local search endpoint; this is the only mode that touches the network)
# Everything lives under $DEMO_V2_WORK/scenario-02/ID (never in the repository). An existing run directory is refused: evidence is never overwritten.
# The only prompts answered are the allowlisted ones in scenarios/02-knowledge-skill/prompt_rules.json; each decision is recorded in evidence/governance.jsonl.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

RUN_ID="" MODE="" SKILLS_FROM="" PORT=18766
while [ $# -gt 0 ]; do
  case "$1" in
    --run-id) RUN_ID="${2:-}"; shift 2 ;;
    --mode) MODE="${2:-}"; shift 2 ;;
    --skills-from) SKILLS_FROM="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    -h|--help) sed -n 2,9p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$RUN_ID" ] && [ -n "$MODE" ] || die "--run-id and --mode are required"
case "$RUN_ID" in *[!A-Za-z0-9._-]*|'') die "run id may only contain letters, digits, . _ -" ;; esac
case "$MODE" in noskill|skill|acquire) ;; *) die "--mode must be noskill, skill or acquire" ;; esac
[ "$MODE" != "skill" ] || { [ -d "$SKILLS_FROM" ] || die "--mode skill needs --skills-from DIR (an acquired-skills directory)"; }
[ "$MODE" = "skill" ] || [ -z "$SKILLS_FROM" ] || die "--skills-from is only valid with --mode skill"

assert_work_outside_repo
ensure_work_area
SC_DIR="$(scenario_dir 02)"
load_env_file "$DEMO_V2_DIR/fixtures/knowledge-skill/fixture.env"
RUN="$DEMO_V2_WORK/scenario-02/$RUN_ID"
[ ! -e "$RUN" ] || die "$RUN already exists; evidence is never overwritten (choose a new --run-id)"
WS="$RUN/workspace"; EV="$RUN/evidence"
KRIYA_BIN="${KRIYA_BIN:-$REPO_ROOT/.venv/bin/kriya}"; KRIYA_PY="${KRIYA_PY:-$REPO_ROOT/.venv/bin/python}"
UPSTREAM="${DEMO_V2_UPSTREAM:-http://localhost:11434}"
mkdir -p "$RUN/skills" "$RUN/state" "$RUN/memory" "$EV"
: > "$EV/governance.jsonl"

# fixture -> a fresh git workspace; the stub's identity is pinned
cp -R "$DEMO_V2_DIR/$FIXTURE_DIR" "$WS"
git -C "$WS" init -q -b main && git -C "$WS" add -A && git -C "$WS" -c user.email=demo@kriya.local -c user.name="Kriya demo" commit -q -m "fixture: $FIXTURE_ID"
[ "$(sha256_of "$WS/$FIXTURE_TARGET_FILE")" = "$FIXTURE_TARGET_SHA256" ] || die "fixture target hash mismatch"
printf 'kriya.yaml\n.kriya/\n' >> "$WS/.git/info/exclude"
[ -z "$(git -C "$WS" status --short)" ] || die "workspace is not clean"

if [ "$MODE" = "skill" ]; then cp -R "$SKILLS_FROM"/. "$RUN/skills/"; fi
skills_digest() { ( cd "$RUN/skills" && find . -type f | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(shasum -a 256 "$f" | awk '{print $1}')" "$f"; done ) | shasum -a 256 | awk '{print $1}'; }
echo "skills before: $(skills_digest) ($(find "$RUN/skills" -type f | wc -l | tr -d ' ') files)" | tee "$EV/skills-digest-before.txt"

# one rendered configuration per run: the offline template for noskill/skill (lookup OFF, no search endpoint), the acquisition template for acquire; chat traffic goes through the proxy
TEMPLATE="offline.template.yaml"; [ "$MODE" != "acquire" ] || TEMPLATE="acquisition.template.yaml"
sed -e "s#@WORK@#$RUN#g" -e "s#http://localhost:11434/v1#http://127.0.0.1:$PORT/v1#g" "$SC_DIR/config/$TEMPLATE" > "$RUN/kriya.yaml"
cp "$RUN/kriya.yaml" "$WS/kriya.yaml"; cp "$RUN/kriya.yaml" "$EV/kriya.rendered.yaml"
cp "$SC_DIR/goal.md" "$EV/goal.md"; cp "$SC_DIR/prompt_rules.json" "$EV/prompt_rules.json"
curl -s -m 5 "$UPSTREAM/api/tags" | "$KRIYA_PY" -c "import json,sys; print(json.dumps({m['name']: m['digest'] for m in json.load(sys.stdin)['models'] if m['name'] in ('qwen3-coder:30b','qwen3.6:35b-a3b-q4_K_M','nomic-embed-text:latest')}, indent=1))" > "$EV/model-digests.json" \
  || die "the model server at $UPSTREAM is not reachable"

# transparent recording proxy (no overrides): the Developer request text is kept verbatim
lsof -ti ":$PORT" >/dev/null 2>&1 && die "port $PORT is in use; stop that process, do not change the port to hide it"
PYTHONDONTWRITEBYTECODE=1 nohup "$KRIYA_PY" "$DEMO_V2_DIR/scenarios/03-controlled-repair/stub/stub_model_server.py" --mode record --port "$PORT" --upstream "$UPSTREAM" --log "$EV/exchanges.jsonl" >"$EV/proxy.stderr.log" 2>&1 &
PROXY_PID=$!
cleanup() { kill "$PROXY_PID" 2>/dev/null || true; ( cd "$WS" && "$KRIYA_BIN" authority revoke >/dev/null 2>&1 ) || true; }
trap cleanup EXIT
for _ in 1 2 3 4 5 6 7 8 9 10; do curl -s -m 1 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1 && break; sleep 0.3; done
curl -s -m 3 "http://127.0.0.1:$PORT/v1/models" >/dev/null || die "the recording proxy did not start (see $EV/proxy.stderr.log)"

# the operator step (a workspace-supplied configuration with a non-default endpoint needs the operator's approval); recorded
( cd "$WS" && "$KRIYA_BIN" authority approve --confirm ) > "$EV/authority-approve.txt" 2>&1 || die "authority approve failed, see $EV/authority-approve.txt"

{ echo "mode=$MODE run_id=$RUN_ID started=$(date -u +%FT%TZ) kriya=$("$KRIYA_BIN" --version 2>&1 | head -1) product_head=$(git -C "$REPO_ROOT" rev-parse HEAD)"; } > "$EV/run-header.txt"
set +e
( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" "$DEMO_V2_DIR/scripts/answer_prompts.py" --rules "$SC_DIR/prompt_rules.json" --log "$EV/governance.jsonl" -- "$KRIYA_BIN" generate -f "$SC_DIR/goal.md" ) > "$EV/session.log" 2>&1
RC=$?
set -e
echo "generate/driver exit code: $RC (3 = an unrecognised prompt stopped the run)" | tee -a "$EV/run-header.txt"

# evidence: state after the run (each is recorded, none is interpreted here)
echo "skills after:  $(skills_digest) ($(find "$RUN/skills" -type f | wc -l | tr -d ' ') files)" | tee "$EV/skills-digest-after.txt"
git -C "$WS" status --short > "$EV/workspace-status.txt"; git -C "$WS" diff > "$EV/workspace.diff" || true
cp "$WS/outline.py" "$EV/outline.final.py" 2>/dev/null || true
( cd "$WS" && PYTHONDONTWRITEBYTECODE=1 "$KRIYA_PY" -m pytest -q -p no:cacheprovider --tb=short ) > "$EV/independent-pytest.txt" 2>&1 || true
docker ps --format '{{.Names}}' > "$EV/docker-ps.txt" 2>&1 || true
echo "search prompts in governance log: $(grep -c 'Send this search' "$EV/governance.jsonl" 2>/dev/null || true)" | tee "$EV/lookup-prompt-count.txt"
echo "run dir: $RUN"
echo "independent pytest: $(tail -1 "$EV/independent-pytest.txt")"

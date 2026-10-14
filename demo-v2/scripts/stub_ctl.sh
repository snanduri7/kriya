#!/usr/bin/env bash
# Start, stop or inspect the scripted model stub of scenario 03. CONTROLLED / SCRIPTED: it is not a model. It listens on 127.0.0.1 only and, in replay mode, makes no outbound connection.
# Usage: stub_ctl.sh start|stop|status
# A fresh start resets the script position, so every run replays the same sequence. The exchange log is written to the scenario's evidence folder.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
assert_work_outside_repo
ACTION="${1:-}"
load_env_file "$DEMO_V2_DIR/fixtures/controlled-repair/fixture.env"
WORK="$(scenario_work 03)"
PIDFILE="$WORK/stub.pid"
LOG="$WORK/evidence/stub-exchanges.jsonl"
KRIYA_PY="${KRIYA_PY:-$REPO_ROOT/.venv/bin/python}"
STUB="$DEMO_V2_DIR/scenarios/03-controlled-repair/stub/stub_model_server.py"
SCRIPT="$DEMO_V2_DIR/scenarios/03-controlled-repair/script/controlled_script.json"

running_pid() {
  [ -f "$PIDFILE" ] || return 1
  local pid; pid="$(cat "$PIDFILE")"
  # only ever signal a process that really is this stub (a stale pidfile must never name an unrelated process)
  ps -p "$pid" -o command= 2>/dev/null | grep -q "stub_model_server.py" || return 1
  echo "$pid"
}

case "$ACTION" in
  start)
    mkdir -p "$WORK/evidence"
    if pid="$(running_pid)"; then kill "$pid"; sleep 0.3; fi
    if lsof -ti ":$STUB_PORT" >/dev/null 2>&1; then die "port $STUB_PORT is already in use by another process"; fi
    : > "$LOG"
    PYTHONDONTWRITEBYTECODE=1 nohup "$KRIYA_PY" "$STUB" --mode replay --port "$STUB_PORT" --script "$SCRIPT" --log "$LOG" >"$WORK/evidence/stub.stderr.log" 2>&1 &
    echo $! > "$PIDFILE"
    for _ in 1 2 3 4 5 6 7 8 9 10; do curl -s -m 1 "http://127.0.0.1:$STUB_PORT/v1/models" >/dev/null 2>&1 && break; sleep 0.3; done
    curl -s -m 2 "http://127.0.0.1:$STUB_PORT/v1/models" >/dev/null || die "the stub did not start (see $WORK/evidence/stub.stderr.log)"
    echo "stub started: 127.0.0.1:$STUB_PORT (pid $(cat "$PIDFILE")), CONTROLLED / SCRIPTED, log $LOG" ;;
  stop)
    if pid="$(running_pid)"; then kill "$pid"; echo "stub stopped (pid $pid)"; else echo "stub is not running"; fi
    rm -f "$PIDFILE" ;;
  status)
    if pid="$(running_pid)"; then echo "stub running (pid $pid) on 127.0.0.1:$STUB_PORT"; else echo "stub not running"; exit 1; fi ;;
  *) die "usage: stub_ctl.sh start|stop|status" ;;
esac

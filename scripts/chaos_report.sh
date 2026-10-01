#!/usr/bin/env bash
# PRD-032: run the chaos scenarios and write the chaos report
# (chaos-report.json + chaos-report.md, tests/_chaos_report.py).
#
#   scripts/chaos_report.sh [OUT_DIR] [TIER]
#
# TIER: deterministic (default) - every scenario that needs no external tool;
#       scanner - adds the live_static_analysis scenario (Semgrep 1.178.0 exactly);
#       all     - adds the live_model scenarios (the qualified local model,
#                 KRIYA_LIVE_BASE_URL / KRIYA_LIVE_LLM_MODEL).
# Scenarios outside the selected tier are reported NOT_RUN, never PASSED.
# The venv's bin is first on PATH, so no test depends on the caller's shell.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-handover/evidence/PRD-032/chaos}"
TIER="${2:-deterministic}"
case "$TIER" in
  deterministic) SELECT="chaos and not live_model and not live_static_analysis" ;;
  scanner) SELECT="chaos and not live_model" ;;
  all) SELECT="chaos" ;;
  *) echo "unknown tier: $TIER (deterministic|scanner|all)" >&2; exit 2 ;;
esac
PATH="$(pwd)/.venv/bin:$PATH" .venv/bin/pytest -m "$SELECT" -p no:cacheprovider -q --chaos-report "$OUT" tests/

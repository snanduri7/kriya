#!/usr/bin/env bash
# Run from any directory after an editable dev install in this checkout.
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${KRIYA_PYTHON:-.venv/bin/python}"
if [[ ! -x "$python_bin" || ! -x .venv/bin/kriya ]]; then
  echo 'Requires .venv/bin/python and .venv/bin/kriya from an editable dev install.' >&2
  echo 'Setup: python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"' >&2
  exit 2
fi
mode="${1:-focused}"
case "$mode" in focused|full) ;; *) echo 'Usage: bash handover/run_PRD-001_verification.sh [focused|full]' >&2; exit 2;; esac
evidence_dir="handover/evidence/PRD-001/user-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$evidence_dir"
git rev-parse HEAD > "$evidence_dir/revision.txt"
git status --short > "$evidence_dir/worktree.txt"
"$python_bin" --version > "$evidence_dir/python.txt"
"$python_bin" -m pip freeze > "$evidence_dir/dependencies.txt"
"$python_bin" -m ruff check kriya/config/authority.py kriya/config/config.py tests/test_bootstrap_contract.py 2>&1 | tee "$evidence_dir/lint.txt"
if [[ "$mode" == full ]]; then
  "$python_bin" -m pytest -m 'not live_model' -ra --junitxml="$evidence_dir/pytest.xml" 2>&1 | tee "$evidence_dir/pytest.txt"
else
  "$python_bin" -m pytest -ra tests/test_bootstrap_contract.py tests/test_config.py tests/test_sec009_config_authority.py tests/test_cli_smoke.py --junitxml="$evidence_dir/pytest.xml" 2>&1 | tee "$evidence_dir/pytest.txt"
fi
echo "Evidence: $evidence_dir"
echo 'Review failures and skips before accepting verification; this script does not certify the task.'

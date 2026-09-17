#!/usr/bin/env bash
# MODEL-001 P1 - live validation entry point. USER-OWNED - the agent that
# wrote this does not run it. See model001_p1_live_validation.py for the
# full docstring on exactly what this proves and why it is not a rerun of
# the 9-arm MODEL-001 campaign.
set -euo pipefail

KRIYA_REPO="/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$KRIYA_REPO/.venv/bin/python3" "$SCRIPT_DIR/model001_p1_live_validation.py"

#!/usr/bin/env bash
# The focused confidence gate that must be clean on the EXACT tree before a live Scenario-A run is frozen and authorized
# (owner ruling 2026-11-11, TEST-KERNEL-GOLDEN-ENFORCE-FIVE-FAIL-AT-F384061-001). It exists as a checked-in file because
# the earlier gates were ad-hoc file lists: they never ran tests/state_machine, so five kernel goldens were red while a
# gate was reported clean. tests/test_pre_live_focused_gate_001.py pins the mandatory tiers below.
#
# Model-free; no live model, no network. NOT the full suite. Run from the repository root:
#     demo-v2/scripts/pre_live_focused_gate.sh [output-directory]
# Exit 0 only when every tier passed. A registered baseline failure (handover/BACKLOG_REGISTRY.csv) still exits
# non-zero: the operator names it in the frozen run record; this script never hides or excuses one.
set -u
cd "$(dirname "$0")/../.."
OUT="${1:-}"
PYTEST=".venv/bin/pytest -q -p no:cacheprovider -n 8 --dist loadgroup"
ulimit -n 256 2>/dev/null || true
status=0

run_tier() {
  name="$1"; shift
  echo "=== tier: $name ==="
  if [ -n "$OUT" ]; then
    mkdir -p "$OUT"
    "$@" > "$OUT/$name.txt" 2>&1; rc=$?
    tail -n 3 "$OUT/$name.txt"
  else
    "$@"; rc=$?
  fi
  echo "=== tier: $name exit $rc ==="
  [ "$rc" -eq 0 ] || status=1
}

# MANDATORY TIER 1: the state-machine tier - every file under tests/state_machine (the kernel goldens pin each
# attempt-evidence record, run event and result field byte for byte) and every tests/conftest.py STATE_MACHINE_TIER_FILES member.
run_tier state_machine $PYTEST -m state_machine tests

# MANDATORY TIER 2: architecture (import direction, direct-access owners, kernel purity) and the security censuses.
run_tier architecture_and_censuses $PYTEST \
  tests/architecture \
  tests/test_platform_architecture_guard.py \
  tests/test_prd012_network_inventory.py \
  tests/test_s19p1b_residuals.py \
  tests/test_strict_doubles.py \
  tests/test_backlog_registry.py \
  tests/test_pre_live_focused_gate_001.py

# MANDATORY TIER 3: the enforce / recovery / requirement-truth controls of the Scenario-A path.
run_tier enforce_recovery_controls $PYTEST \
  tests/test_enforce_terminal_requirement_truth_001.py \
  tests/test_enforce_unit_advisory_review_acceptance_001.py \
  tests/test_enforce_recovery_convergence_001.py \
  tests/test_shared_decision_authority_001.py \
  tests/test_recovery_may_change_001.py \
  tests/test_recovery_may_change_characterization.py \
  tests/test_reviewer_bound_001.py \
  tests/test_backend_final_closure_005_verification_owner_reopen.py \
  tests/test_lr_r1_p4_verification_only_retry_reproducer.py \
  tests/test_lr_r1_p4_verification_retry_admission.py \
  tests/test_lr_r1_p5_integration_obligation_reproducer.py \
  tests/test_lr_r1_m1_reason_codes.py \
  tests/test_workflow_controller_enforce.py \
  tests/test_taf3_enforce_terminal_refusal.py \
  tests/test_run_list_requirement_truth.py \
  tests/test_history_store.py \
  tests/test_kup_execution.py \
  tests/test_skill_verification_deferral.py \
  tests/test_obligations.py \
  tests/test_model_evidence_hardening_001.py \
  tests/test_model_evidence_hardening_final.py \
  tests/test_demo_v2_spring_ignite.py

# MANDATORY TIER 4: static checks, whole tree, zero findings.
run_tier ruff .venv/bin/ruff check .
run_tier pylint .venv/bin/pylint kriya plugins/core_tools tests

echo "PRE_LIVE_FOCUSED_GATE exit $status"
exit "$status"

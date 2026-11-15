"""Verify that every `path::symbol` cited by the A1 architecture rows exists at the checked-out tree. Read-only.

Usage: python3 check_citations.py <repo_root>
"""
import re
import sys
from pathlib import Path

CITED = [
    ("kriya/workflow/open_findings.py", ["replaces_open_finding", "update_open_findings", "finding_from_failure", "OpenFinding"]),
    ("kriya/workflow/retry_policy.py", ["decide_retry_action", "decide_for_state", "decide_attempt_mode", "observe_failure_family",
                                        "reset_scoped_budgets_for_new_family", "force_strategy_transition", "charge_failed_attempt",
                                        "max_total_attempts_for", "_state_inputs"]),
    ("kriya/workflow/retry_progress.py", ["classify_progress", "ProgressVector", "build_progress_vector"]),
    ("kriya/workflow/retry_strategy.py", ["_record_attempt_failure", "record_workspace_progress", "handle_attempt_failure",
                                          "_apply_declared_no_progress_stop", "_admit_verification_only_retry",
                                          "_stop_after_a_failed_verifier_replay", "_stop_after_a_failed_verifier_repair"]),
    ("kriya/workflow/failure_grounding.py", ["build_failure_signature"]),
    ("kriya/workflow/recovery_coordinator.py", ["RecoveryCoordinator", "classify_attempt_exception", "conclude_attempt_failure",
                                                "_route_around_patch_incompatible_fallbacks", "_record_recovery_decision"]),
    ("kriya/workflow/attempt_lifecycle.py", ["plan_attempt", "run_iteration", "record_phase_advanced"]),
    ("kriya/workflow/attempt.py", ["run_attempt"]),
    ("kriya/workflow/development_loop.py", ["accept_candidate", "run_attempt_loop"]),
    ("kriya/workflow/best_of_n.py", ["reset_state_for_independent_candidate", "run_attempt_with_best_of_n"]),
    ("kriya/workflow/candidate_transaction.py", ["_request_fallback_for_rejected_authoritative_target"]),
    ("kriya/workflow/state.py", ["GenerationState", "RetryBudgets", "generation_metrics", "retry_progress_summary"]),
    ("kriya/workflow/run_lifecycle.py", ["open_run_state"]),
    ("kriya/workflow/checkpoint.py", ["validate_resume_against_reality"]),
    ("kriya/workflow/resume_fingerprints.py", ["build_resume_plan"]),
    ("kriya/workflow/workflow.py", ["_save_stage_checkpoint", "run_generation_workflow"]),
    ("kriya/workflow/enforce_loop.py", ["_invoke_bounded_subtask", "convergence_recovery_cycles", "verification_owner_reopened",
                                        "pre_repair_owners"]),
    ("kriya/workflow/workflow_controller.py", ["_MAX_PLAN_SCOPE_REVISION_ATTEMPTS"]),
    ("kriya/workflow/verifier_replay.py", ["failure_identity"]),
    ("kriya/workflow/verifier_semantic.py", ["VerifierSemanticLedger"]),
    ("kriya/workflow/self_correction.py", []),
    ("kriya/workflow/gate_orchestrator.py", ["_run_verification_only_attempt"]),
    ("kriya/workflow/stage_apply.py", ["retry_counters"]),
    ("kriya/workflow/milestones.py", []),
    ("kriya/control/checkpoint.py", ["save_checkpoint", "load_checkpoint"]),
    ("kriya/control/state.py", ["ControlState", "tool_attempts", "subtask_states"]),
    ("kriya/control/persistence.py", ["save_control_state", "ControlStateUnreadableError"]),
    ("kriya/control/run_coordinator.py", ["claim_run_generation_clock"]),
    ("kriya/config/config.py", ["max_consecutive_no_progress_attempts", "generation_time_budget_seconds", "best_of_n_first_attempt",
                                "self_correction_loop_max_turns"]),
    ("tests/test_non_candidate_failure_scoped_reset_001.py", []),
    ("handover/MODULARIZATION/MODULE-CONTRACTS.md", ["FindingState"]),
]
ABSENT = ["kriya/workflow/finding.py"]   # cited as NOT existing


def main() -> int:
    root = Path(sys.argv[1])
    problems = []
    checked = 0
    for rel, symbols in CITED:
        path = root / rel
        if not path.is_file():
            problems.append(f"missing file: {rel}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for symbol in symbols:
            checked += 1
            if not re.search(r"\b" + re.escape(symbol) + r"\b", text):
                problems.append(f"missing symbol: {rel}::{symbol}")
    for rel in ABSENT:
        if (root / rel).exists():
            problems.append(f"cited as absent but present: {rel}")
    print(f"files={len(CITED)} symbols={checked} problems={len(problems)}")
    for problem in problems:
        print(" -", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Verify that every `path::symbol` cited by the A3 architecture rows exists at the checked-out tree. Read-only.

Usage: python3 check_citations_a3.py <repo_root>
"""
import re
import sys
from pathlib import Path

CITED = [
    ("kriya/workflow/candidate_transaction.py", ["CandidateTransaction", "check_operation_contracts", "capture_originals", "_commit_staged_writes",
                                                 "_refusal_reason", "candidate_digests", "identical_rewrites", "FileProposal"]),
    ("kriya/policy/filesystem.py", ["AuthorizedFileWriter", "commit_batch"]),
    ("kriya/workflow/verification_binding.py", ["bind_candidate", "binding_refusal", "VERIFIED_CANDIDATE_EVIDENCE_STALE",
                                                "VERIFIED_CANDIDATE_EVIDENCE_MISSING", "CandidateVerificationBinding"]),
    ("kriya/workflow/terminal_commit.py", ["commit_terminal_candidate", "materialize_candidate", "CandidateFile", "TerminalCommitOutcome",
                                           "committed_digest_mismatch", "semantic_safety_refusal"]),
    ("kriya/workflow/commit_service.py", ["commit_verified_candidate", "plan_terminal_writes", "interrupted_commit_outcome",
                                          "TerminalCommitNotEligibleError", "run_commit_states"]),
    ("kriya/repository/commit.py", ["commit_revision_grounded_batch", "candidate_digest", "UncertainCommitError", "FileRevisionConflict",
                                    "find_uncertain_commit_evidence", "CommitState"]),
    ("kriya/control/recovery.py", ["recover_workspace", "assess_recovery"]),
    ("kriya/workflow/stage_apply.py", ["approval_gate", "_abort_without_applying", "apply_terminal_candidate", "run_static_analysis_gate",
                                       "run_semantic_safety_gate", "run_artifact_registry_gate", "need_human_approval", "approval_triggers",
                                       "total_diff_lines"]),
    ("kriya/workflow/stage_review.py", ["pre_approval_review", "final_review", "final_review_refusal"]),
    ("kriya/policy/approval.py", ["resolve_approval", "classify_approval_answer", "ApprovalOutcome", "APPROVAL_REASON_CODES", "stale",
                                  "defer_execution_until_source_review", "candidate_digest", "RuntimeCommandSubject", "KINDS",
                                  "APPROVAL_LAUNCH_CANNOT_ASK", "APPROVAL_DECISION_NOT_RECORDED"]),
    ("kriya/config/authority.py", ["is_human_in_the_loop"]),
    ("kriya/config/config.py", ["risk_threshold_lines", "requirement_unverified_policy", "requirement_unknown_policy"]),
    ("kriya/config/resolution.py", ["requirement_unverified_policy"]),
    ("kriya/control/launch_approval.py", ["build_pending", "await_decision", "_wait", "_write_audit_decision", "APPROVAL_WAIT_SECONDS"]),
    ("kriya/control/review_channel.py", ["CHANNEL_FD", "RESULT_CAPABILITY_MISMATCH"]),
    ("kriya/control/launch.py", ["request_change_approval", "resolve_review", "decline_decision", "note_approval_required", "finish",
                                 "_requirement_truth_field"]),
    ("kriya/control/persistence.py", ["save_launch_record", "save_launch_review_decision"]),
    ("kriya/control/retention.py", []),
    ("kriya/cli.py", ["_approval_decision", "_fix_approval_decision", "_terminal_answer", "_confirm_change"]),
    ("kriya/workflow/attempt.py", ["run_attempt", "assert_candidate_unchanged_since_source_review", "post_source_review_guard",
                                   "deferred_executing_gates", "_attempt_evidence_boundary", "reusing_candidate", "replaying_verifier"]),
    ("kriya/workflow/attempt_lifecycle.py", ["begin_attempt", "run_iteration"]),
    ("kriya/workflow/gate_orchestrator.py", ["_run_executing_candidate_gates", "_run_deferred_executing_gates", "apply_pre_review_candidate_corrections",
                                             "_apply_candidate_pom_corrections", "_record_candidate_change", "_run_pom_validation_gate"]),
    ("kriya/workflow/runtime_verification.py", ["MANAGED_SERVICE_APPROVAL_UNSUPPORTED", "post_source_review_guard", "python_environment_error"]),
    ("kriya/workflow/file_integrity.py", ["VerificationTreeBinding"]),
    ("kriya/workflow/development_loop.py", ["accept_candidate", "run_terminal_regression", "_close_and_check_requirements",
                                            "_raise_if_required_verification_unresolved", "_candidate_is_the_reviewed_one",
                                            "_save_candidate_gates_checkpoint", "settle_generated_verifiers"]),
    ("kriya/workflow/suite_attribution.py", ["attribute_suite_result"]),
    ("kriya/tools/validate.py", ["PolymorphicValidator", "run_tests", "_ensure_project_venv_impl"]),
    ("kriya/workflow/terminal_gate_service.py", ["TerminalGateService", "TerminalGateReport", "commit_eligible", "requirements_verified",
                                                 "_requirement_gap", "_bind_candidate", "_verify_original_requirements", "_record_terminal_report",
                                                 "TerminalGateRequest", "TERMINAL_GATES_NOT_RUN"]),
    ("kriya/workflow/requirements.py", ["requirement_outcomes", "requirement_truth", "blocking_requirements", "_model_reported_missing",
                                        "record_requirement_verdicts", "record_requirement_closure"]),
    ("kriya/workflow/requirement_closers.py", ["run_requirement_closers"]),
    ("kriya/workflow/obligations.py", ["ObligationLedger"]),
    ("kriya/workflow/state.py", ["final_workflow_quality_passed", "verified_candidate_binding", "pre_approval_review"]),
    ("kriya/workflow/outcome.py", ["terminal_failure_category", "completed_result", "final_review_refusal_payload"]),
    ("kriya/workflow/workflow.py", ["_record_approval_decision", "_restore_original_bytes", "_direct_terminal_writes", "_bind_direct_candidate",
                                    "_raise_terminal_commit_stop", "run_generation_workflow"]),
    ("kriya/workflow/run_lifecycle.py", ["persist_run_trace"]),
    ("kriya/workflow/plan_executor.py", ["_passed", "_terminal", "_execute", "_Lifecycle", "WorkUnitInvocation", "requirement_goal",
                                         "PLAN_TERMINAL_MISMATCH"]),
    ("kriya/workflow/execution_plan.py", ["CommitStrategy", "INCREMENTAL_WORK_UNIT"]),
    ("kriya/workflow/milestones.py", ["_MilestonePlanDriver", "check_passed_unit", "unit_failed", "complete_unit", "plan_result",
                                      "replay_prior_milestone_report", "_record_ledger", "_complete_milestone", "save_milestone_run_state"]),
    ("kriya/workflow/enforce_loop.py", ["enforce_unit_gates_accepted", "advisory_review_refusal", "committed_files", "_lifecycle_of",
                                        "_discard_sandbox_completions", "_adopt_sandbox_completions", "legacy_kwargs", "_persist_control_state"]),
    ("kriya/workflow/retry_strategy.py", ["requirements_unresolved", "workspace_commit", "regression_unattributed"]),
    ("kriya/workflow/resume_fingerprints.py", ["ResumePlan", "skip_candidate_gates", "candidate_snapshot_digest"]),
    ("kriya/workflow/checkpoint.py", []),
    ("kriya/workflow/worktree.py", ["remove_git_worktree", "create_git_worktree"]),
    ("kriya/workflow/contract_lifecycle.py", ["derive_contract_transition"]),
    ("kriya/workflow/contract_authority.py", []),
    ("kriya/workflow/verifier_replay.py", ["ReplayHandle"]),
    ("kriya/workflow/plan_normalization.py", ["scope_verification_to_requirements"]),
    ("kriya/workflow/acceptance.py", ["goal_requires_runtime_behavior"]),
    ("kriya/core/attempt_evidence/scope.py", ["declares_gate_purpose"]),
    ("kriya/application/history.py", ["_truth_of_events"]),
    ("kriya/metrics/derive.py", ["approval.decision"]),
    ("kriya/metrics/evidence.py", ["approval.decision"]),
    ("tests/test_project_continuity_two_run.py",
     ["test_F_the_requirement_closure_judges_the_candidate_against_the_project_and_does_not_record_success",
      "test_F_with_the_unverified_policy_blocking_the_substitute_is_refused_and_nothing_is_applied", "_wrong_file_run"]),
    ("tests/golden/kernel/legacy_gates_exhausted.json", ["review_included_in_approval"]),
    ("handover/claude-context/generation-pipeline.md", []),
    ("handover/MODULARIZATION/OPEN-ITEMS.md", ["OI-42", "OI-102"]),
    ("docs/architecture/CORR016_AUTHORIZED_CONTRACT_EVOLUTION_DESIGN.md", []),
]

# Symbols that must NOT exist (negative controls the documents rely on).
ABSENT = [
    ("kriya/control/retention.py", "launch"),
    ("kriya/cli.py", "launch-decision"),
    ("kriya/workflow/stage_review.py", "finish_reason"),
]


def main() -> int:
    root = Path(sys.argv[1])
    problems, symbols = [], 0
    for path, names in CITED:
        target = root / path
        if not target.is_file():
            problems.append(f"missing file: {path}")
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        for name in names:
            symbols += 1
            if not re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", text):
                problems.append(f"missing symbol: {path}::{name}")
    for path, name in ABSENT:
        target = root / path
        if target.is_file() and name in target.read_text(encoding="utf-8", errors="replace"):
            problems.append(f"negative control failed: {path} contains {name!r}")
    evidence_rule_id = [str(p) for p in (root / "kriya/core/attempt_evidence").rglob("*.py") if "rule_id" in p.read_text(encoding="utf-8", errors="replace")]
    if evidence_rule_id:
        problems.append(f"negative control failed: rule_id found in {evidence_rule_id}")
    for problem in problems:
        print(problem)
    print(f"files={len(CITED)} symbols={symbols} absent_checks={len(ABSENT) + 1} problems={len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

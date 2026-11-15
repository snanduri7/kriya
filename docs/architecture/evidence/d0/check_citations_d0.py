"""Verify the code facts first cited in D0 (12-D0-CLOSURE-HARDENING.md and the disposition table of 05) at the
checked-out baseline, and run the negative controls D0 relies on. Read-only. Exit 1 on any miss.

Usage: python3 check_citations_d0.py <repo_root>
"""
import re
import sys
from pathlib import Path

PRESENT = [
    ("kriya/workflow/contract_compilation.py", [
        "CONTRACT_COMPILER_VERSION = 4", 'CONTRACT_FORMAT = "kriya.verification_contract/1"', "STATUS_CLOSABLE",
        "STATUS_AUTHORITY_REQUIRED", "STATUS_AMBIGUOUS", "STATUS_NOT_A_CLAIM", "STATUS_DISPOSITIONED",
        "class ContractEntry", "def refusal", "GoalAdmissionError", "VerificationAuthorityRequired",
        "def identity_payload", "Never consults a model",
        'ambiguity = "the goal names paths whose role', 'ambiguity = "the goal names no tracked file']),
    ("kriya/workflow/workflow.py", [
        "def _requirement_policy_blocks", '"requirement_unverified_policy", "record") == "block"',
        '"status": "resume_refused"', '_RESUME_REFUSAL_REASON_CODES = {"commit_state": "UNCERTAIN_COMMIT_STATE"}',
        "run_id = target_id", "annotate_run(workspace_path, resume_decision=resume_plan.to_dict())"]),
    ("kriya/workflow/enforce_loop.py", [
        "if _requirement_policy_blocks(autonomy):", "def _discard_sandbox_completions", "COMPLETIONS_NOT_IN_WORKSPACE",
        "deps.annotate_run(workspace_path, resume_decision=enforce_resume_decision)", "subtask_completion_scope"]),
    ("kriya/metrics/evidence.py", ["record.resume_decision"]),
    ("kriya/control/run_record.py", [
        "_ANNOTATABLE_FIELDS", '"retry_counters", "resume_decision"', "STORE_CLASSIFICATION", "TERMINAL_LIFECYCLES",
        'terminal_status = updates.pop("terminal_status", None) or state.value']),
    ("kriya/control/run_coordinator.py", ["def _claims_success", 'payload.get("quality_gates_passed") is True']),
    ("kriya/cli.py", [
        'sys.exit(0 if final_res and final_res.get("quality_gates_passed") else 1)',
        'sys.exit(0 if res.get("quality_gates_passed") else 1)',
        'sys.exit(0 if fix_result and fix_result.get("quality_gates_passed") else 1)',
        'trace_id_override=res.get("run_id")']),
    ("kriya/workflow/requirements.py", [
        'REQUIREMENT_TRUTH_ALL_VERIFIED = "ALL_VERIFIED"', 'REQUIREMENT_TRUTH_OPEN = "OPEN_REQUIREMENTS"',
        'REQUIREMENT_TRUTH_RESOLVED_WITH_ACCEPTANCE = "RESOLVED_WITH_OPERATOR_ACCEPTANCE"', "def requirement_truth"]),
    ("kriya/control/trace.py", ["INSERT OR REPLACE INTO runs"]),
    ("kriya/workflow/open_findings.py", [
        "_NON_CANDIDATE_CATEGORIES", "_NON_CANDIDATE_TYPES", "DELIVERY_FAILURE_TYPES", "def is_candidate_finding",
        "def replaces_open_finding"]),
    ("kriya/workflow/state.py", ["class RetryBudgets", "targeted_retry_count: int = 0", "api_contract_recovery_count: int = 0"]),
    ("kriya/control/state.py", ["subtask_states", "current_plan_hash", "current_contract_hash"]),
    ("kriya/config/authority.py", [
        "_VALUE_BOUNDED_FIELDS", '("autonomy", "sandbox_cpu_seconds"): 240', "1 <= value <= _VALUE_BOUNDED_FIELDS[key]",
        '("autonomy", "generation_time_budget_seconds")']),
    ("kriya/config/resolution.py", ['("autonomy", "requirement_unverified_policy"): "block"']),
    ("kriya/control/control_store.py", ["def write_control_file"]),
    ("kriya/policy/approval.py", ["KINDS"]),
    ("kriya/workflow/plan_executor.py", ["_passed"]),
]
# Negative controls: (file, regex over the named block or the whole file, must NOT match, what it proves)
ABSENT = [
    ("kriya/control/run_record.py", r"_ANNOTATABLE_FIELDS = frozenset\(\{[^}]*requirement_truth",
     "the RunRecord has no requirement-truth annotation (S6 adds it)"),
    ("kriya/config/authority.py", r"_VALUE_BOUNDED_FIELDS: Dict\[FieldPath, int\] = \{[^}]*generation_time_budget_seconds",
     "the run time budget is not value-bounded (S8 adds it)"),
    ("kriya/workflow/open_findings.py", r"no_progress_retry",
     "no_progress_retry is in no exclusion table of the finding predicate (GAP-A1-07)"),
    ("kriya/control/run_record.py", r"APPLIED_REQUIREMENTS_OPEN", "no applied-open terminal status exists (S6 adds it)"),
    ("kriya/workflow/contract_compilation.py", r"semantic_state", "no semantic state is sealed (S5 adds it)"),
    ("kriya/control/state.py", r"retry_ledger|recovery_cycle|semantic_ledger",
     "ControlState persists no retry or recovery state (S8 adds it)"),
]


def main() -> int:
    root = Path(sys.argv[1])
    problems, checked = [], 0
    for name, needles in PRESENT:
        text = (root / name).read_text(encoding="utf-8")
        for needle in needles:
            checked += 1
            if needle not in text:
                problems.append(f"missing in {name}: {needle}")
    for name, pattern, meaning in ABSENT:
        checked += 1
        if re.search(pattern, (root / name).read_text(encoding="utf-8"), re.S):
            problems.append(f"negative control failed in {name}: {meaning}")
    resume_sites = re.findall(r"resume_id=resume_id", (root / "kriya/cli.py").read_text(encoding="utf-8"))
    print(f"citations and negative controls checked: {checked}; problems: {len(problems)}")
    print(f"cli.py passes resume_id (a checkpoint id) at {len(resume_sites)} call sites")
    for problem in problems:
        print("FAIL", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

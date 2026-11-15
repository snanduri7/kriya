"""Verify that every `path::symbol` cited by the A2 architecture rows exists at the checked-out tree. Read-only.

Usage: python3 check_citations_a2.py <repo_root>
"""
import re
import sys
from pathlib import Path

CITED = [
    ("kriya/workflow/requirements.py", ["derive_requirements", "mutation_path_roles", "behavior_strength"]),
    ("kriya/workflow/requirement_contract.py", ["requirement_set_for", "load_requirement_contract"]),
    ("kriya/workflow/contract_compilation.py", ["compile_verification_contract", "seal_verification_contract", "VerificationContract",
                                                "refusal", "STATUS_AMBIGUOUS", "CLOSER_ACCEPTANCE_APPROVAL", "CLOSER_RUNTIME_STDOUT"]),
    ("kriya/workflow/workflow.py", ["_requirement_policy_blocks", "run_generation_workflow"]),
    ("kriya/workflow/authority_request.py", []),
    ("kriya/workflow/verifier_semantic.py", ["derive_executed_verifiers", "VerifierSemanticLedger", "after_decision", "UnitSemantics",
                                             "production_owner_authority", "unit_semantics", "_AttemptScope", "full_regeneration_refusal",
                                             "autonomous_rewrite_permitted", "regenerable"]),
    ("kriya/policy/approval.py", ["ask_verifier_semantic", "VerifierSemanticSubject", "semantic_authority_fingerprint", "VerifierSemanticAllowance",
                                  "take_regeneration", "verifier_semantic_matches", "DECISIONS_BY_KIND", "VerifierRepairSubject"]),
    ("kriya/workflow/retry_strategy.py", ["_generated_verifier_ruling", "_semantic_attribution", "_close_unadmitted_verifier_regeneration",
                                          "_record_attempt_failure"]),
    ("kriya/workflow/development_loop.py", ["settle_generated_verifiers", "run_attempt_loop", "_unit_verifier_semantics"]),
    ("kriya/workflow/gate_orchestrator.py", ["_autonomous_rewrite_permitted", "post_source_review_guard"]),
    ("kriya/workflow/failure_grounding.py", ["resolve_unique_locator_artifacts"]),
    ("kriya/workflow/attribution.py", ["attribute_failure", "DETERMINISTIC_ATTRIBUTION_TIERS", "_tier_triage"]),
    ("kriya/workflow/failure.py", ["classify_failure_attribution", "to_gate_outcome"]),
    ("kriya/core/attempt_evidence/scope.py", ["attribution_kind"]),
    ("kriya/workflow/verifier_replay.py", ["ReplayHandle", "refusal"]),
    ("kriya/workflow/verifier_repair.py", ["verify_against_pre_repair_owner"]),
    ("kriya/code_intel/parsing.py", ["python_module_name", "_LANGUAGE_BY_EXTENSION"]),
    ("kriya/code_intel/service.py", ["CandidateOverlay"]),
    ("kriya/tools/test_execution.py", ["_identity", "parse_junit_xml"]),
    ("kriya/workflow/validation_baseline.py", ["extract_test_failure_sections"]),
    ("kriya/workflow/example_oracle_java.py", []),
    ("kriya/workflow/pytest_stability.py", []),
    ("kriya/workflow/terminal_gate_service.py", ["baseline_contents"]),
    ("kriya/repository/project_view.py", []),
    ("kriya/tools/dependency_execution.py", ["MAVEN_COMPILE_RESOLUTION_UNVERIFIED"]),
    ("kriya/workflow/acceptance_approval.py", []),
    ("kriya/workflow/plan_validation.py", []),
    ("tests/test_verifier_semantic_loop_001.py", ["test_a_failure_with_no_run_authored_executed_verifier_keeps_the_historical_retry",
                                                  "test_a_ruled_failure_is_never_handed_to_failure_attribution"]),
    ("tests/test_verifier_semantic_authority_001.py",
     ["test_a_test_source_compile_failure_establishes_no_subject_and_the_policy_owner_has_no_phase_rule"]),
    ("handover/evidence/backend-final-closure-005-repair-003/final-twelve/C2-S4_A-c3.CLASSIFICATION.md", []),
    ("handover/claude-context/requirements-and-brownfield.md", []),
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
            if not re.search(r"\b" + re.escape(name) + r"\b", text):
                problems.append(f"missing symbol: {path}::{name}")
    for problem in problems:
        print(problem)
    print(f"files={len(CITED)} symbols={symbols} problems={len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

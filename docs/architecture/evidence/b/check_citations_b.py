"""Verify the `path::symbol` citations first made in Phase B (06-GAP-AUTHORITY-AUDIT.md and the A1/A2 completion) and run
the negative controls Phase B relies on. Citations made by the slices are checked by each slice's own script. Read-only.

Usage: python3 check_citations_b.py <repo_root>
"""
import re
import sys
from pathlib import Path

CITED = [
    ("kriya/core/llm.py", ["_binding", "_runtime"]),
    ("kriya/core/inference_settings.py", ["role_binding_for_model", "role_inference_settings"]),
    ("kriya/core/model_runtime.py", ["binding_object", "_all_bindings"]),
    ("kriya/config/config.py", ["routing_alias_conflicts", "_binding_identity", "AgentRolesConfig", "AgentModelConfig"]),
    ("kriya/agents/agent.py", ["_call_with_escalation", "extra_body_override=cand.extra_body", "model_override=cand.model"]),
    ("kriya/control/run_coordinator.py", ["coordinated_mutation", "_complete_successful_run", "_reconcile_result", "_reconciled_with_record",
                                          "_fail_active_run", "_unverified_work_units", "transition_mutating_run", "begin_run_commit",
                                          "settle_run_commit", "the ONE result-truth owner for a mutating run"]),
    ("kriya/workflow/workflow_controller.py", ["new_run_id", "trace_id_override", "current_run_context"]),
    ("kriya/workflow/worktree.py", ["_bootstrap_greenfield_repository", "wanted_excludes", "clean", "create_git_worktree"]),
    ("kriya/tools/knowledge.py", ["KnowledgeCache", "release_cache", "set_release_date", "get_release_date", "retrieved_at"]),
    ("kriya/memory/learned_knowledge.py", ["retrieve_learned_references", "provenance_url", "fetch_date", "fence_untrusted_reference"]),
    ("kriya/memory/vector.py", ["add_learned_knowledge"]),
    ("kriya/cli.py", ["add_learned_knowledge"]),
    ("kriya/workflow/run_lifecycle.py", ["persist_run_trace"]),
    ("kriya/application/run.py", ["KNOWLEDGE_POLICY"]),
    ("kriya/workflow/open_findings.py", ["replaces_open_finding", "update_open_findings"]),
    ("kriya/workflow/retry_policy.py", ["observe_failure_family", "reset_scoped_budgets_for_new_family", "charge_failed_attempt",
                                        "decide_retry_action", "max_total_attempts_for"]),
    ("kriya/workflow/verifier_semantic.py", ["derive_executed_verifiers", "VerifierSemanticLedger", "production_owner_authority"]),
    ("kriya/workflow/contract_compilation.py", ["compile_verification_contract", "seal_verification_contract"]),
    ("kriya/workflow/stage_apply.py", ["approval_gate"]),
    ("kriya/control/launch_approval.py", ["build_pending", "candidate_digest"]),
    # Added with the integration audit (sections 10 to 12).
    ("kriya/control/run_coordinator.py", ["claim_run_generation_clock", "generation_clock_paused", "follow_run_generation_clock", "begin_mutating_run"]),
    ("kriya/workflow/run_lifecycle.py", ["claim_run_generation_clock", "budget_started_monotonic"]),
    ("kriya/workflow/outcome.py", ["terminal_failure_category", "environment_failure", "startswith"]),
    ("kriya/workflow/enforce_loop.py", ["current_plan_hash", "raw_plan", "content_hash"]),
    ("kriya/workflow/stage_apply.py", ["sensitive_paths", "this pattern is not being enforced"]),
    ("kriya/workflow/stage_knowledge_guard.py", ["knowledge_risk_confirmed"]),
]
SEARCH_ROOTS = ("kriya", "plugins")


def _sources(root: Path):
    for top in SEARCH_ROOTS:
        for path in sorted((root / top).rglob("*.py")):
            yield path.relative_to(root).as_posix(), path.read_text(encoding="utf-8", errors="replace")


def holders(root: Path, needle: str):
    return sorted(rel for rel, text in _sources(root) if needle in text)


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
    controls = [
        ("add_learned_knowledge(", ["kriya/cli.py", "kriya/memory/vector.py"], "learned knowledge has one writer: `kriya learn` (R-05 class B)"),
        ("set_release_date(", ["kriya/tools/knowledge.py"], "the release cache is written only by the registry lookups of its own module (R-05 class D)"),
        ("routing_alias_conflicts(", ["kriya/config/config.py", "kriya/core/model_routing.py"], "the same-alias conflict rule is evaluated for routing candidates only, by the configuration validator and the routing planner (R-09)"),
        ("wanted_excludes", ["kriya/workflow/worktree.py"], "`.kriya/` is added to the local exclude file by the worktree module only (audit 3.4)"),
        ("_reconcile_result(", ["kriya/control/run_coordinator.py"], "a result is reconciled with its RunRecord in one place (audit 3.5)"),
        ("role_binding_for_model(", None, "listing: who resolves a binding role-first"),
        ("._binding(", None, "listing: who resolves a binding by model name"),
    ]
    for needle, allowed, meaning in controls:
        found = holders(root, needle)
        print(f"control {needle!r}: found in {found} ({meaning})")
        if allowed is not None:
            extra = [rel for rel in found if rel not in allowed]
            if extra:
                problems.append(f"negative control failed: {needle!r} also in {extra}")
    for path, needle, meaning in [
        ("kriya/tools/knowledge.py", "workspace_id", "the release cache has no project key (and needs none)"),
        ("kriya/control/trace.py", "_reconcile", "the history writer does not reconcile a row with the RunRecord"),
        ("kriya/workflow/run_lifecycle.py", "_reconcile", "nor does the trace persistence"),
        ("kriya/control/run_record.py", "requirement", "the RunRecord's own proofs do not include requirement truth"),
    ]:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        print(f"absent {needle!r} in {path}: {needle not in text} ({meaning})")
        if needle in text:
            problems.append(f"negative control failed: {path} contains {needle!r}")
    for problem in problems:
        print(problem)
    print(f"files={len(CITED)} symbols={symbols} controls={len(controls) + 4} problems={len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

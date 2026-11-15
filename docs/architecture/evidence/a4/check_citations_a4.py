"""Verify that every `path::symbol` cited by the A4 architecture rows exists at the checked-out tree, and run the
negative-control searches the A4 rows rely on. Read-only.

Usage: python3 check_citations_a4.py <repo_root>
"""
import re
import sys
from pathlib import Path

CITED = [
    ("kriya/workflow/requirements.py", ["derive_requirements", "goal_identity", "RequirementSet", "Requirement", "requirements_prompt_block",
                                        "requirement_lineage", "_meaningful", "clarifications"]),
    ("kriya/workflow/requirement_contract.py", ["requirement_set_for", "load_requirement_contract", "bound_requirement_contract",
                                                "REQUIREMENT_CONTRACT_INVALID", "REQUIREMENT_CONTRACT_GOAL_MISMATCH"]),
    ("kriya/workflow/goal_facts.py", ["goal_facts_block", "developer_goal_facts", "delegating_shim_targets"]),
    ("kriya/workflow/stage_knowledge_guard.py", ["check_knowledge_guard", "knowledge_risk_confirmed", "resolved_knowledge_coordinates"]),
    ("kriya/tools/knowledge.py", ["KnowledgeGuard", "check_goal", "extract_library_versions", "KnowledgeCache", "GapReport",
                                  "generate_skill_template"]),
    ("kriya/workflow/stage_architecture.py", ["design_work_unit", "check_goal", "check_enabled", "human_rejected", "approval_required",
                                              "new_design_terms", "Proceeding under default policy"]),
    ("kriya/workflow/outbound_lookup.py", ["OutboundLookupRequest", "from_extracted_terms", "UnsafeLookupTerm", "is_known_public_term",
                                           "sanitize_public_technology_term"]),
    ("kriya/workflow/live_lookup.py", ["_resolve_via_web_lookup", "_augment_error_with_live_lookup", "_extract_first_usable"]),
    ("kriya/policy/egress.py", ["web_lookup_allowed"]),
    ("kriya/workflow/untrusted_context.py", ["fence_untrusted_reference", "outside_untrusted_reference", "UNTRUSTED_REFERENCE_BEGIN"]),
    ("kriya/workflow/skill_conventions.py", ["skill_applies_to_goal", "render_skill_conventions", "PLANNING_SKILL_CONSTRAINTS_HEADER"]),
    ("kriya/workflow/skill_extraction.py", ["planning_skill_constraints", "_split_rules_by_verification", "_write_skill_extraction",
                                            "_stage_skill_conflicts", "_skill_staleness_warning"]),
    ("kriya/skills/skill.py", ["SkillEngine", "discover_and_load", "_skill_verification_is_authoritative", "activate_run_shadow",
                               "persist_run_learning", "discard_run_learning", "create_skill_skeleton", "new_skill_refusal",
                               "reclassify_held_rules", "record_rule_provenance", "mark_rules_verified", "_set_skill_yaml_fields",
                               "record_conflict_resolution", "git_commit_if_tracked", "mentions_term", "fact_match", "verified_claim"]),
    ("kriya/skills/service.py", ["SkillService", "SkillDescriptor"]),
    ("kriya/kup/catalog_ops.py", ["skills.list", "skills.describe"]),
    ("kriya/workflow/stage_analysis.py", ["analyze_work_unit", "record_active_skills", "missing_skill_candidates", "approve_web_lookup",
                                          "project_view.unavailable", "project_view.built", "KNOWLEDGE GUARD SAFETY CONSTRAINTS"]),
    ("kriya/workflow/stage_planning.py", ["plan_work_unit", "_classify_against_project", "structured_plan_validation",
                                          "structured_plan_repair_requested", "structured_plan_repair_result",
                                          "planner_output_unauthorized_path", "planner_output_schema_invalid", "planner_output_incomplete"]),
    ("kriya/workflow/file_resolution.py", ["classify_plan_completeness", "PlanCompletenessResult"]),
    ("kriya/workflow/planner_validation.py", ["validate_tool_capability_membership", "UNREGISTERED_TOOL_NAME_REASON_CODE",
                                              "PLANNED_TOOL_NOT_PLANNABLE_REASON_CODE"]),
    ("kriya/workflow/planner_repair.py", ["STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS"]),
    ("kriya/workflow/plan_validation.py", ["validate_plan", "canonicalize_planned_file_actions", "PLANNED_FILE_ACTION_MISMATCH",
                                           "integration_decision_findings", "PLAN_GLOBAL_INVARIANTS_MISSING", "SUBTASK_DEPENDENCY_CYCLE",
                                           "AMBIGUOUS_PLANNED_FILE_OWNERSHIP", "PLAN_REQUIREMENT_ID_UNKNOWN", "EXTENSION_POINT_REQUIRED",
                                           "REFACTOR_BASELINE_MISSING", "PLAN_VERIFICATION_SCOPE_UNJUSTIFIED"]),
    ("kriya/workflow/plan_normalization.py", ["coalesce_same_file_owners", "redirect_immutable_test_edits", "derive_verification_contracts",
                                              "scope_verification_to_requirements", "VERIFICATION_SCOPE_NORMALIZED"]),
    ("kriya/workflow/plan_schema.py", ["EngineeringPlan", "Subtask", "GlobalInvariant", "IntegrationRelationship", "PlannerStructuredOutput",
                                       "build_engineering_plan_from_planner_output", "content_hash", "requirement_ids"]),
    ("kriya/workflow/planning_request.py", ["build_subtask_goal_text", "build_subtask_semantic_context", "build_subtask_constraint_context",
                                            "build_subtask_plan_text", "integration_contracts_for", "INTEGRATION_CONTRACT_AUTHORITY",
                                            "find_missing_grounded_production_artifacts", "build_approved_plan_document",
                                            "evaluate_plan_edit_feasibility", "build_authoritative_planner_request"]),
    ("kriya/workflow/acceptance.py", ["goal_requires_runtime_behavior"]),
    ("kriya/workflow/execution_plan.py", ["ExecutionPlan", "WorkUnit", "PlanSourceKind", "WorkUnitStatus"]),
    ("kriya/workflow/plan_adapters.py", ["direct_execution_plan", "milestone_execution_plan", "work_unit_record"]),
    ("kriya/workflow/plan_executor.py", ["execute_plan", "execute_direct_goal", "WorkUnitInvocation", "requirement_goal_of",
                                         "authoritative_goal_of", "NestedExecutionPlanError", "select_work_unit_checkpoint", "_Lifecycle",
                                         "PlanDriver", "PLAN_TERMINAL_MISMATCH"]),
    ("kriya/workflow/enforce_loop.py", ["EnforceLoop", "_invoke_bounded_subtask", "STRUCTURED PLANNING", "raw_plan", "current_plan_hash",
                                        "knowledge_gap_break", "STRUCTURED_PLAN_REPAIR_EXHAUSTED", "structured_plan_normalized",
                                        "structured_plan_targets", "structured_plan_edit_feasibility", "save_approved_plan",
                                        "SEMANTIC_CONTRACT_REGRESSION_REJECTED", "PRESERVED_REFERENCE_REGRESSION_REJECTED",
                                        "REQUIREMENT_CONTRACT_CHANGED", "PLAN_CHANGED", "grounding_goal"]),
    ("kriya/workflow/workflow_controller.py", ["WorkflowController", "_UnsafeStructuredPlan", "_StructuredPlanUnavailable",
                                               "_MAX_PLAN_SCOPE_REVISION_ATTEMPTS", "WorkflowControllerConfigurationError",
                                               "_run_structured_shadow"]),
    ("kriya/workflow/workflow.py", ["run_generation_workflow", "unresolved_knowledge_report", "_approve_web_lookup", "compile_run_contract",
                                    "_resolve_protected_relpath", "requirements_from_goal"]),
    ("kriya/workflow/development_loop.py", ["_close_and_check_requirements", "close_requirements_by_file_creation",
                                            "close_requirements_by_runtime_stdout"]),
    ("kriya/workflow/terminal_gate_service.py", ["close_requirements_by_file_creation", "close_requirements_by_runtime_stdout",
                                                 "_verify_original_requirements"]),
    ("kriya/workflow/subtask_executor.py", ["plannable_tool_names"]),
    ("kriya/workflow/stage_apply.py", ["risk_threshold_lines", "need_human_approval"]),
    ("kriya/workflow/retry_strategy.py", ["_augment_error_with_live_lookup", "extract_error_search_terms"]),
    ("kriya/workflow/toolchain.py", ["Always None today", "_java_toolchain_fact"]),
    ("kriya/workflow/requirement_disposition.py", ["REJECTED_FALSE_PREMISE"]),
    ("kriya/workflow/attempt.py", ["AttemptContext"]),
    ("kriya/tools/lsp.py", ["JdtlsClient", "refuse_unsupported"]),
    ("kriya/platform/toolchain_locator.py", ["jdk_home_for_version"]),
    ("kriya/application/project_init.py", ["recommended_profile", "_WORKFLOW", "_AUTONOMY"]),
    ("kriya/config/config.py", ["WorkflowControllerConfig", "requirement_unknown_policy", "requirement_unverified_policy",
                                "web_lookup_auto_approve"]),
    ("kriya/cli.py", ["_dispatch_generation", "migration_mode", "Checkpoint saved", "GENERATION BUDGET EXHAUSTED", "skills_create",
                      "skills_approve", "skills_promote", "skills_unverify"]),
    ("skills/qpid/rules.txt", ["kriya doctor"]),
    ("tests/test_greenfield_runtime_verification_dropped.py",
     ["test_the_goal_reading_that_drives_the_scoping", "test_the_owner_goal_keeps_s4_and_the_application_is_executed",
      "test_a_goal_without_a_runtime_requirement_still_succeeds_without_executing"]),
    ("tests/test_plan_external_package_capability_001.py", []),
    ("handover/claude-context/generation-pipeline.md", ["SHARED-DECISION-AUTHORITY-001"]),
    ("handover/MODULARIZATION/OPEN-ITEMS.md", ["OI-113", "OI-93", "OI-81", "OI-95", "OI-115", "OI-46"]),
]


def _sources(root: Path):
    for path in sorted((root / "kriya").rglob("*.py")):
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

    # Negative controls: (needle, the only files that may hold it, what the documents conclude from it).
    controls = [
        ("clarifications=", [], "no producer supplies requirement clarifications"),
        ("activate_run_shadow(", ["kriya/skills/skill.py", "kriya/workflow/enforce_loop.py"], "only the enforce loop starts the skills run shadow"),
        ("persist_run_learning(", ["kriya/skills/skill.py", "kriya/workflow/enforce_loop.py"], "only the enforce loop persists run learning"),
        # config/authority.py only classifies the field (SEC-009); it does not read its value
        ("risk_threshold_lines", ["kriya/config/authority.py", "kriya/config/config.py", "kriya/workflow/stage_apply.py"],
         "the size trigger has one reader, per unit"),
        ("generate_skill_template(", ["kriya/cli.py", "kriya/tools/knowledge.py", "kriya/workflow/stage_apply.py"],
         "two callers write a skill template without the engine"),
        ("check_enabled", ["kriya/config/authority.py", "kriya/config/config.py", "kriya/workflow/stage_architecture.py"],
         "knowledge.check_enabled gates stage 2A only"),
        ("skill_applies_to_goal(", ["kriya/workflow/skill_conventions.py", "kriya/workflow/skill_extraction.py", "kriya/workflow/stage_analysis.py"],
         "two callers of the skill selector"),
        (".check_goal(", ["kriya/workflow/stage_architecture.py", "kriya/workflow/stage_knowledge_guard.py", "kriya/workflow/workflow_controller.py"],
         "three callers of the knowledge gate (the first run of this check found the third)"),
        ("requirement_goal=", ["kriya/workflow/plan_executor.py"], "only WorkUnitInvocation.for_unit assigns a requirement goal"),
        ("PlanSourceKind.STRUCTURED,", ["kriya/workflow/enforce_loop.py"], "only the enforce loop builds a STRUCTURED invocation"),
    ]
    for needle, allowed, meaning in controls:
        found = holders(root, needle)
        extra = [rel for rel in found if rel not in allowed]
        print(f"control {needle!r}: found in {found} ({meaning})")
        if extra:
            problems.append(f"negative control failed: {needle!r} also in {extra}")
    # Files that must NOT hold a symbol.
    for path, needle, meaning in [
        ("kriya/workflow/live_lookup.py", "fence_untrusted_reference", "live-lookup text is not fenced where it is produced"),
        ("kriya/workflow/retry_strategy.py", "fence_untrusted_reference", "nor where it is appended to the error context"),
        ("kriya/workflow/plan_validation.py", "REJECTED_FALSE_PREMISE", "plan validation reads no disposition"),
        ("kriya/workflow/enforce_loop.py", "REJECTED_FALSE_PREMISE", "the enforce loop reads no false-premise disposition"),
        ("kriya/workflow/plan_normalization.py", "REJECTED_FALSE_PREMISE", "plan normalization reads no disposition"),
        # knowledge.py WRITES a skill.yaml (generate_skill_template) and never loads one: it does not use the skill engine
        ("kriya/tools/knowledge.py", "SkillEngine", "the knowledge gate never consults the skill store"),
        ("kriya/tools/knowledge.py", "yaml.safe_load", "the knowledge gate never reads a skill's metadata"),
        ("kriya/application/project_init.py", "requirement_unverified_policy", "Project Setup sets no requirement policy"),
    ]:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        print(f"absent {needle!r} in {path}: {needle not in text} ({meaning})")
        if needle in text:
            problems.append(f"negative control failed: {path} contains {needle!r}")
    for label, needle in (("validate_plan call sites", "validate_plan("), ("migration_mode holders", "migration_mode"),
                          ("classify_plan_completeness call sites", "classify_plan_completeness("),
                          ("jdk_home_for_version holders", "jdk_home_for_version")):
        print(f"{label}: {holders(root, needle)}")
    for problem in problems:
        print(problem)
    print(f"files={len(CITED)} symbols={symbols} controls={len(controls) + 8} problems={len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

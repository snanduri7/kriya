"""Verify that every `path::symbol` cited by the A5 architecture rows exists at the checked-out tree, and run the
negative-control searches the A5 rows rely on. Read-only.

Usage: python3 check_citations_a5.py <repo_root>
"""
import re
import sys
from pathlib import Path

CITED = [
    ("kriya/config/resolution.py", ["resolve_config_state", "runtime_profile_preset_fields", "PRODUCTION_GENERATION_TIME_BUDGET_SECONDS",
                                    "_production_budget_is_at_least_as_strict", "production_sealed_value_satisfied", "_refuse_shadowed_configuration",
                                    "ShadowedConfigurationError", "require_workspace_local_state_under_kriya_dir", "KRIYA_INSTALL_DIR",
                                    "PRODUCTION_FIXED_RUNTIME_GUARANTEES", "requirement_unknown_policy", "offline_mode"]),
    ("kriya/config/authority.py", ["_REPOSITORY_SAFE_FIELDS", "_SECURITY_AUTHORITY_FIELDS", "_PLATFORM_POLICY_FIELDS", "_TRUSTED_SOURCES",
                                   "classify_field", "is_known_field", "compute_violations", "path_field_classification",
                                   "value_gated_field_classification", "_VALUE_BOUNDED_FIELDS", "_VALUE_GATED_FIELDS", "agent_role_field_classification",
                                   "parse_autonomy_mode", "parse_containment_mandate", "AUTO_DISCOVERED_INSTALL_DIR", "generation_time_budget_seconds",
                                   "auto_index_missing_dependency_graph", "risk_threshold_lines", "run_verification_enabled", "check_enabled"]),
    ("kriya/config/authority_approval.py", ["resolve_with_approval", "compute_set_digest", "build_security_field_records", "default_local_approval_path",
                                            "checked_trust_file", "validate_trust_path_outside_workspace", "_authority_home_dir"]),
    ("kriya/config/config.py", ["load_config", "auto_index_missing_dependency_graph", "generation_time_budget_seconds", "self_correction_loop_enabled",
                                "enforce_approval"]),
    ("kriya/config/default_config.yaml", ["memory", "skills", "risk_threshold_lines", "generation_time_budget_seconds"]),
    ("kriya/config/environment.py", ["kriya_install_dir", "AUTHORITY_HOME", "MCP_APPROVAL_HOME", "STATE_DIR"]),
    ("kriya/config/service.py", ["ConfigService", "CONFIG_AUTHORITY_REFUSED"]),
    ("kriya/core/state_paths.py", ["resolve_state_directory", "ambient_state_directory", "trace_db_path", "default_state_directory"]),
    ("kriya/context/workspace_identity.py", ["authority_root", "find_repository_root", "workspace_identity", "canonical_workspace",
                                             "_KRIYA_OWNED_DIRECTORIES", "legacy_workspace_identity", "validate_ownership"]),
    ("kriya/repository/bound_tree.py", ["_root_class", "admit_workspace_for_mutation", "bound_tree_paths", "bound_tree_declaration", "copy_bound_tree",
                                        "GIT_BACKED", "NO_GIT_REPOSITORY", "BOUND_TREE_UNAVAILABLE", "BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY",
                                        "operating_root_excluded_by_authority", "_is_os_metadata_noise"]),
    ("kriya/workflow/worktree.py", ["create_git_worktree", "managed_worktree_path", "_create_scoped_snapshot_sandbox", "_bootstrap_greenfield_repository",
                                    "_sync_uncommitted_changes_into_worktree", "_verify_materialized_tracked_bytes", "candidate_member_paths",
                                    "candidate_member_declaration", "record_candidate_write", "_HOOKS_DISABLED", "NestedWorktreeError"]),
    ("kriya/policy/filesystem.py", ["AuthorizedFileWriter", "AuthorizedFileReader", "WriteScopeMode", "is_trusted_control_path",
                                    "_ENFORCEMENT_SENSITIVE_PATH_PATTERNS", "PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS", "TRUSTED_CONTROL_PATH_DENIED",
                                    "FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE", "GOAL_SOURCE_FILE_PROTECTED", "WRITE_SCOPE_DENY_ALL", "commit_batch",
                                    "ControlObjectIdentities", "ReadableRootLinkCensus"]),
    ("kriya/workflow/development_loop.py", ["create_git_worktree", "Refusing to generate directly in the application workspace"]),
    ("kriya/workflow/best_of_n.py", ["create_git_worktree"]),
    ("kriya/production_doctor.py", ["create_git_worktree"]),
    ("ui/standalone/src/main/execution_host.ts", ["cwd: workspace"]),
    ("kriya/policy/authority.py", ["AuthorityPort", "file_writer"]),
    ("kriya/policy/security_demonstration.py", ["demonstrate"]),
    ("kriya/policy/protected_authority_paths.py", ["protected_authority_paths", "WORKSPACE_CONTROL_RELPATH", "WORKSPACE_CHECKPOINTS_RELPATH",
                                                   "ProtectedAuthorityPathsUnresolvable"]),
    ("kriya/workflow/candidate_transaction.py", ["_stage_candidate_writes", "AuthorizedFileWriter", "allowed_write_relpaths", "write_scope_mode",
                                                 "protected_relpath", "candidate_digests"]),
    ("kriya/workflow/gate_orchestrator.py", ["AuthorizedFileWriter", "skills_relpath", "self_correction_loop_enabled", "post_source_review_guard"]),
    ("kriya/workflow/self_correction.py", ["AuthorizedFileWriter", "self_correction_loop_enabled"]),
    ("kriya/workflow/owner_recovery.py", ["_attempt_owner_recovery_self_correction", "self_correction_loop_enabled"]),
    ("kriya/tools/tool.py", ["ToolGovernance", "PLANNABLE_TOOL_GOVERNANCE", "_SHIPPED_PLANNED_DECLARATIONS", "resolve_planned_tool_governance",
                             "execute_model_authored", "revalidate_model_authored", "_authority_phase", "ModelToolInvocation", "ToolEffectBoundary",
                             "PROVEN_PRE_EFFECT", "TOOL_OPERATOR_ONLY", "TOOL_MODEL_AUTHORITY_UNDECLARED", "requires_confirmation"]),
    ("kriya/workflow/subtask_executor.py", ["_execute_tool_subtask", "_tool_preflight", "revalidate_tool_subtask_authority", "reads_candidate_view",
                                            "_hitl_blocks_tool_without_confirmation", "TOOL_CONFIRMATION_UNSUPPORTED", "plannable_tool_names"]),
    ("kriya/workflow/enforce_loop.py", ["tool_roots", "plan_workspace_path"]),
    ("plugins/core_tools/__init__.py", ["FilesystemTool", "ShellTool", "GitTool", "SearchTool", "ASTTool", "planned_governance", "authorize_model_invocation",
                                        "DENY_ALL", "OPERATOR_ONLY", "_authorize_read_target"]),
    ("kriya/mcp/mcp.py", ["MCPTool", "MCPManager", "start_all", "_decide_invocation_authority", "revalidate_authority", "planned_governance",
                          "GOVERNED_SIDE_EFFECT", "containment_required", "containment_active"]),
    ("kriya/mcp/containment_adapter.py", ["map_capability_profile_to_containment", "refuse_grants_exposing_protected_authority", "workspace_write",
                                          "mount_workspace", "MCPContainmentUnsupportedError", "MCP_GRANT_EXPOSES_PROTECTED_AUTHORITY"]),
    ("kriya/mcp/invocation_approval.py", ["resolve_mcp_invocation_approval", "default_local_approval_path", "validate_store_path_outside_workspace",
                                          "compute_invocation_approval_binding_digest"]),
    ("kriya/policy/execution.py", ["_check_mcp_invocation", "classify_shell_acquisition_command", "ExecutionPolicy"]),
    ("kriya/core/kernel.py", ["start_all"]),
    ("kriya/analyzer/analyzer.py", ["index_repository", "RepositoryAnalyzer", "kriya_owned_index_roots", "new_skill_refusal", "record_manifest"]),
    ("kriya/analyzer/graph.py", ["DependencyGraph", "has_indexed_files", "find_symbol_locations", "get_class_symbol_locations", "get_neighborhood",
                                 "index_manifest", "record_manifest", "indexed_paths"]),
    ("kriya/code_intel/service.py", ["CodeIntelligenceService", "current_view", "has_baseline", "refresh", "discover_source_files", "for_config"]),
    ("kriya/code_intel/store.py", ["StructuralStore", "ci_files", "ci_symbols", "publish", "file_states"]),
    ("kriya/memory/vector.py", ["LocalVectorStore", "query_hybrid", "query_learned_knowledge", "reset_index", "active_fingerprint"]),
    ("kriya/memory/learned_knowledge.py", ["learned_knowledge_db_path", "retrieve_learned_references", "LEARNED_KNOWLEDGE_DB"]),
    ("kriya/tools/knowledge.py", ["KnowledgeCache", "knowledge_cache.db", "release_cache", "KnowledgeGuard", "generate_skill_template"]),
    ("kriya/knowledge/staging.py", ["stage_facts"]),
    ("kriya/workflow/graph_retrieval.py", ["open_code_intelligence", "retrieve_graph_context", "_code_intelligence_candidates", "retrieved_chunks"]),
    ("kriya/workflow/goal_facts.py", ["developer_goal_facts", "open_code_intelligence", "current_view"]),
    ("kriya/workflow/plan_targets.py", ["open_code_intelligence", "current_view"]),
    ("kriya/workflow/workflow.py", ["_ensure_repository_indexed", "auto_index_missing_dependency_graph", "DEPENDENCY_INDEX_UNAVAILABLE",
                                    "generation_time_budget_seconds", "is_accidental_shared_skills_write"]),
    ("kriya/workflow/stage_analysis.py", ["is_accidental_shared_skills_write", "vector_index_path", "retrieve_graph_context", "repo_slug",
                                          "effective_skills_dir"]),
    ("kriya/workflow/stage_apply.py", ["generate_skill_template", "stage_facts", "repo_slug", "risk_threshold_lines", "enforce_approval"]),
    ("kriya/workflow/investigation.py", ["find_symbol_locations", "get_callers", "dependency_graph_db_path"]),
    ("kriya/workflow/triage.py", ["has_indexed_files", "get_callers"]),
    ("kriya/workflow/process_boundary.py", ["_dependency_graph_db_path", "extract_class_names", "find_java_main_class"]),
    ("kriya/skills/skill.py", ["is_accidental_shared_skills_write", "new_skill_refusal", "SkillWriteRefused", "create_skill", "git_commit_if_tracked",
                               "record_rule_provenance", "mark_rules_verified", "record_conflict_resolution", "_write_skill_file", "from_config",
                               "activate_run_shadow", "effective_skills_dir", "get_global_skills_dir"]),
    ("kriya/workflow/skill_extraction.py", ["_write_skill_extraction", "_stage_skill_conflicts", "git_commit_if_tracked"]),
    ("kriya/control/trace.py", ["retrieved_chunks", "redact_value"]),
    ("kriya/control/launch_approval.py", ["build_pending", "candidate_digest", "review_identity", "APPROVAL_WAIT_SECONDS"]),
    ("kriya/control/launch.py", ["launches"]),
    ("kriya/application/project_init.py", ["initialize_project", "recommended_profile", "_AUTONOMY", "_WORKFLOW", "generation_time_budget_seconds"]),
    ("kriya/application/project.py", ["ProjectService", "CONFIG_NOT_APPLIED", "resolve_state_directory", "validate_project_name"]),
    ("kriya/application/run.py", ["build_preflight", "KNOWLEDGE_POLICY", "observable_by_cockpit", "RunService"]),
    ("kriya/application/history.py", ["workspace_identities", "unattributed", "recorded_workspace_identity"]),
    ("kriya/application/catalog.py", ["ToolCatalogService"]),
    ("kriya/kup/serve.py", ["load_config", "answer"]),
    ("kriya/cli.py", ["_launched_generate", "_generate_impl", "KNOWLEDGE_POLICY", "open_review_channel_from_fd", "approval_callback", "_fix_approval_decision",
                      "is_accidental_shared_skills_write", "Nothing was generated and no model was called", "_echo_admission_refusal"]),
    ("kriya/platform/__init__.py", ["GIT_STRIPPED_ENVIRONMENT", "GIT_ICASE_PATHSPECS"]),
    ("kriya/platform/filesystem_semantics.py", ["canonical_spelling", "path_relation", "path_identity"]),
    ("kriya/platform/private_store.py", ["PrivateStoreUnsafeError"]),
    ("kriya/tools/gate_view.py", ["_remove_tree", "ignore_errors"]),
    ("kriya/tools/validate.py", ["_ensure_project_venv_impl", "VENV_CREATION_ATTEMPTS", "PYTHON_ENVIRONMENT_UNAVAILABLE"]),
    ("kriya/platform/toolchain_locator.py", ["jdk_home_for_version"]),
    ("ui/standalone/src/main/child_env.ts", ["KRIYA_CHILD_ENV_KEYS", "KRIYA_STATE_DIR"]),
    ("demo-v2/scenarios/04-spring-ignite-protocol/config/kriya.template.yaml", ["generation_time_budget_seconds: 2400"]),
    ("tests/test_candidate_transaction.py", ["test_a_whole_file_replacement_based_on_the_workspace_file_is_refused_by_the_guarded_commit"]),
    ("tests/_fake_embedding.py", ["StaticEmbedder"]),
    ("tests/architecture/access_sites.py", ["asname"]),
    ("tests/test_slice_a_effect_boundary.py", []),
    ("tests/test_private_fixture_hygiene.py", []),
    ("tests/test_toolchain_host_probe_authority_001.py", ["test_the_host_never_selects_the_contained_toolchain"]),
    ("evidence/PRD-036/HISTORICAL_FIXTURE_EVIDENCE_MANIFEST.md", []),
    ("demo-v2/scripts/reset_demo.sh", []),
    ("handover/claude-context/kernel-mcp-and-tool-authority.md", ["every planned file write is refused", "MCP-PROTECTED-AUTHORITY-GRANT-001"]),
    ("handover/claude-context/config-resolution-and-authority.md", ["One state-location authority", "Private stores"]),
    ("handover/MODULARIZATION/OPEN-ITEMS.md", ["OI-59", "OI-85", "OI-91", "OI-103", "OI-01"]),
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

    # Negative controls: (needle, the only files that may hold it, what the documents conclude from it).
    controls = [
        # The first run of this check found the last two: policy/authority.py::AuthorityPort.file_writer (a facade factory) and the cockpit's
        # security demonstration (authorize() only, over a disposable folder).
        ("AuthorizedFileWriter(", ["kriya/workflow/candidate_transaction.py", "kriya/workflow/gate_orchestrator.py", "kriya/workflow/self_correction.py",
                                   "plugins/core_tools/__init__.py", "kriya/policy/authority.py", "kriya/policy/security_demonstration.py"],
         "the write gate is constructed at four writing sites (the candidate batch, the pom correction, the self-correction loop, the planned "
         "filesystem tool in DENY_ALL), by one facade factory and by the security demonstration"),
        (".file_writer(", [], "the facade factory AuthorityPort.file_writer has no caller"),
        ("tool_roots=", ["kriya/workflow/enforce_loop.py"], "only the enforce loop hands a TOOL subtask its roots"),
        ("os.getcwd()", None, "listing: who reads the process working directory"),
        ("new_skill_refusal(", ["kriya/analyzer/analyzer.py", "kriya/skills/skill.py"],
         "the shared-install refusal is consulted by create_skill and the analyzer's conventions skill only"),
        ("is_accidental_shared_skills_write(", ["kriya/cli.py", "kriya/skills/skill.py", "kriya/workflow/stage_analysis.py"],
         "the shared-install predicate is evaluated by the CLI skill command, the engine's refusal and the analysis-stage warning"),
        ("generate_skill_template(", ["kriya/cli.py", "kriya/tools/knowledge.py", "kriya/workflow/stage_apply.py"],
         "two callers write a skill template without the engine (A4 control, reused)"),
        ("activate_run_shadow(", ["kriya/skills/skill.py", "kriya/workflow/enforce_loop.py"], "only the enforce loop starts the skills run shadow (A4 control, reused)"),
        ("index_repository(cfg", ["kriya/cli.py", "kriya/workflow/workflow.py"],
         "the configured index is written by `kriya analyze` and by the opt-in auto-index only"),
        ("risk_threshold_lines", ["kriya/config/authority.py", "kriya/config/config.py", "kriya/workflow/stage_apply.py"],
         "the size trigger has one reader, per unit (A4 control, reused)"),
        ("enforce_approval", ["kriya/config/authority.py", "kriya/config/config.py", "kriya/policy/execution.py", "kriya/workflow/stage_apply.py"],
         "the process-profile approval is read at the per-unit approval gate (and described in policy/execution.py)"),
        ("generation_time_budget_seconds", None, "listing: who reads or sets the run's wall-clock budget"),
        ("planned_governance", None, "listing: who declares planned-call governance"),
        ("paths.memory", None, "listing: who reads the memory location"),
        ("approval_callback=", None, "listing: who passes an approval callback"),
    ]
    for needle, allowed, meaning in controls:
        found = holders(root, needle)
        print(f"control {needle!r}: found in {found} ({meaning})")
        if allowed is not None:
            extra = [rel for rel in found if rel not in allowed]
            if extra:
                problems.append(f"negative control failed: {needle!r} also in {extra}")
    # Files that must NOT hold a symbol.
    for path, needle, meaning in [
        ("kriya/code_intel/store.py", "workspace", "the structural store has no workspace key"),
        ("kriya/memory/vector.py", "workspace_id", "the vector store has no workspace key"),
        ("kriya/memory/learned_knowledge.py", "workspace", "the learned-knowledge store has no workspace key"),
        ("kriya/application/project_init.py", "\"paths\"", "Project Setup writes no paths section"),
        ("kriya/policy/filesystem.py", "kriya.yaml", "the write gate knows no configuration file name"),
        ("kriya/tools/knowledge.py", "is_accidental_shared_skills_write", "the knowledge guard's scaffold writer does not consult the shared-install predicate"),
        ("kriya/tools/knowledge.py", "new_skill_refusal", "nor the engine's refusal"),
        ("kriya/knowledge/staging.py", "is_accidental_shared_skills_write", "the staged-fact writer does not consult the shared-install predicate"),
        ("kriya/workflow/skill_extraction.py", "new_skill_refusal", "the rule/example writer does not consult the engine's refusal"),
        ("kriya/control/retention.py", "launch", "retention does not prune launch records"),
        ("kriya/mcp/mcp.py", "invocation.roots", "an MCP tool does not use the invocation's roots"),
        ("kriya/workflow/enforce_loop.py", "risk_threshold_lines", "the enforce loop reads no size threshold"),
        ("kriya/control/launch_approval.py", "risk_threshold_lines", "the launch approval reads no size threshold"),
    ]:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        print(f"absent {needle!r} in {path}: {needle not in text} ({meaning})")
        if needle in text:
            problems.append(f"negative control failed: {path} contains {needle!r}")
    # Directories that must not hold a needle.
    for directory, needle, meaning in [
        ("kriya/kup", "index_repository", "no desktop (KUP) operation indexes a repository"),
        ("kriya/application", "index_repository", "no application use case indexes a repository"),
        ("kriya/client", "index_repository", "no client port indexes a repository"),
        ("kriya/kup", "risk_threshold_lines", "no desktop operation measures change size"),
        ("kriya/application", "risk_threshold_lines", "no application use case measures change size"),
        ("kriya/mcp", "risk_threshold_lines", "the MCP layer measures no change size"),
    ]:
        found = [rel for rel in holders(root, needle) if rel.startswith(directory + "/")]
        print(f"absent {needle!r} under {directory}/: {not found} ({meaning})")
        if found:
            problems.append(f"negative control failed: {needle!r} in {found}")
    for problem in problems:
        print(problem)
    print(f"files={len(CITED)} symbols={symbols} controls={len(controls) + 19} problems={len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

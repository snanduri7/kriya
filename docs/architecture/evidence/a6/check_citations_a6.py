"""Verify that every `path::symbol` cited by the A6 architecture rows exists at the checked-out tree, and run the
negative-control searches the A6 rows rely on. Read-only.

Usage: python3 check_citations_a6.py <repo_root>
"""
import re
import sys
from pathlib import Path

CITED = [
    ("kriya/core/inference_runtime.py", ["InferenceRuntimePort", "RuntimeCapabilities", "ChatRequest", "ChatResponse", "OpenAICompatibleTransport",
                                         "UnknownRuntimeAdapterError", "register_runtime_adapter", "runtime_adapter", "runtime_for_binding",
                                         "VllmRuntimeAdapter", "per_request_context_window", "native_identity_probe", "json_schema_output",
                                         "classify_error", "RuntimeErrorKind", "malformed_provider_reply", "wire_payload"]),
    ("kriya/core/model_runtime.py", ["OllamaRuntimeAdapter", "OllamaNativeRuntimeAdapter", "NATIVE_RUNTIME_NAME", "NATIVE_PROTOCOL_ADAPTER_VERSION",
                                     "MODEL_PROTOCOL_ADAPTER_VERSION", "ModelRuntimeFingerprint", "probe_model_runtime", "resolve_model_runtime",
                                     "resolve_configured_model_runtime", "requested_context_window", "admission_ceiling", "request_extra_body",
                                     "binding_output_tokens", "observe_served_context_state", "pin_served_configuration", "record_fingerprint",
                                     "PROBE_ENV_VAR", "owned_request_fields", "kriya-ollama-native/3", "kriya-openai-compat/3",
                                     "_PER_REQUEST_CONTEXT_PROVIDERS", "SERVED_CONTEXT_OBSERVATION_ATTEMPTS"]),
    ("kriya/core/provider_contract.py", ["ProviderRequestPlan", "ProviderCapabilities", "ContextWindowState", "context_window_state", "budget_window",
                                         "check_prompt_consumption", "SERVED_CONTEXT_BELOW_REQUESTED", "RUNTIME_CONTEXT_IDENTITY_MISMATCH",
                                         "PROVIDER_PROMPT_TRUNCATED", "SERVED_CONTEXT_UNOBSERVABLE", "QUALIFICATION_IDENTITY_UNVERIFIED",
                                         "PROVIDER_REQUEST_FIELD_CONFLICT", "refuse_extra_body_request_fields", "PROVIDER_CONTRACT_REASON_CODES"]),
    ("kriya/core/llm.py", ["LLMClient", "_binding", "_runtime", "_runtime_fingerprint", "_dispatch_budget", "_check_after_call", "_strict_identity",
                           "_refuse_owned_request_fields", "complete_result", "complete_with_tools_result", "_request_once", "InferenceDeadlineError",
                           "EgressViolationError", "_finish", "identity_window", "_audit_llm_network_access", "_client_for", "_within_deadline",
                           "_note_admission_miss", "StructuredOutputUnsupportedError"]),
    ("kriya/core/token_budget.py", ["plan_dispatch", "ContextBudgetUnsatisfiableError", "OutputBudgetUnsatisfiableError", "OutputExpectation",
                                    "DispatchBudget"]),
    ("kriya/core/completion.py", ["CompletionResult", "CompletionStatus"]),
    ("kriya/core/model_qualification.py", ["assess", "required_capabilities", "QUALIFIED", "NOT_QUALIFIED", "STALE", "MISSING", "policy_digest_for",
                                           "QUALIFICATION_POLICY_VERSION", "kriya-qualification/9", "offered_context_tiers", "role_models",
                                           "require_verified_identity", "over_context_refusal", "qualification_config", "qualification_home",
                                           "_refuse_inside_workspace", "environment_case_statuses"]),
    ("kriya/core/inference_settings.py", ["InferenceSettings", "request_settings", "role_inference_settings", "retry_inference_settings",
                                          "qualification_identity", "role_inference_identities"]),
    ("kriya/core/qualification_identity.py", ["QualificationIdentityGuard"]),
    ("kriya/core/execution_environment.py", []),
    ("kriya/core/model_certification.py", ["CertificationKey", "certification_status", "record_trial", "streak_status"]),
    ("kriya/core/release_candidate.py", ["release_candidate_identity", "model_identity", "compare_release_candidate"]),
    ("kriya/core/role_metrics.py", ["model_role", "current_model_role", "UNATTRIBUTED", "independence_violations", "role_runtimes",
                                    "ROLE_INDEPENDENCE_REQUIRED", "shared_runtime_groups"]),
    ("kriya/core/model_routing.py", ["plan_routes", "apply_routes", "RoutingError", "_served_window"]),
    ("kriya/workflow/model_transition.py", ["ModelRequestProfile", "resolve_request_profile", "fallback_incompatibilities", "profile_changes",
                                            "FALLBACK_MODEL_INCOMPATIBLE"]),
    ("kriya/workflow/generation_service.py", ["_enter_developer_model", "_require_qualified_retry_identity", "_run_developer_generation",
                                              "_developer_request_profile", "_chain_binding", "is_fallback", "_developer_optional_sections"]),
    ("kriya/workflow/fallback_decision.py", ["_select_developer_fallback", "_substitute_for_required_patch", "_raise_fallback_incompatible"]),
    ("kriya/workflow/workflow.py", ["_primary_model_runtime_details", "never blocks a run", "_role_independence_details"]),
    ("kriya/cli.py", ["_workflow_config", "RESPONSE_PROTOCOL_NOT_PRODUCTION", "run_production_doctor", "ROLE_INDEPENDENCE_REQUIRED", "model_status"]),
    ("kriya/production_doctor.py", ["_check_role_independence", "_check_precision_boundary", "models.role_independence", "semantic.precision_boundary",
                                    "model.qualification", "_precision_boundary_required", "_role_independence_required"]),
    ("kriya/config/config.py", ["independent_roles", "semantic_region_enforcement_required", "inference_runtime", "max_context_tokens",
                                "retry_temperature"]),
    ("kriya/agents/agent.py", ["DeveloperAgent", "_fill_missing_content", "run_generation", "_fit_request", "rejected_candidate_system_prompt",
                               "_build_incompatible_types_scaffold", "_build_buffer_capacity_scaffold", "call_with_escalation", "REQUIRE_COMPLETE_OUTPUT",
                               "=== Existing Code Base Context ===", "=== Task ===", "=== Architecture Design ===", "AUTHORITATIVE RUN DISPOSITION",
                               "terminal_reason", "outside_untrusted_reference", "candidate_output_tokens", "ReviewerAgent", "SkillGapAgent"]),
    ("kriya/workflow/untrusted_context.py", ["fence_untrusted_reference", "outside_untrusted_reference", "_NEUTRALIZED_MARKERS",
                                             "UNTRUSTED_REFERENCE_WARNING"]),
    ("kriya/workflow/live_lookup.py", ["_augment_error_with_live_lookup", "_resolve_via_web_lookup", "Reference material found",
                                       "_first_usable_lookup_candidate"]),
    ("kriya/workflow/outbound_lookup.py", ["OutboundLookupRequest", "UnsafeLookupTerm"]),
    ("kriya/workflow/retry_strategy.py", ["_augment_error_with_live_lookup", "web_lookup_allowed", "approve_web_lookup", "raw_error_context",
                                          "build_failure_signature"]),
    ("kriya/workflow/repair_orchestrator.py", ["=== Reference material found", "advisory_context", "retry_error_context", "prior_error_text"]),
    ("kriya/workflow/retry_package.py", ["RetryPackage", "render_error", "prior_error_text", "advisory_context", "build_retry_package",
                                         "reference evidence (verify against local validators)"]),
    ("kriya/workflow/retry_prompts.py", ["_build_targeted_retry_prompt", "_build_full_set_retry_prompt", "Previous Error to Fix"]),
    ("kriya/workflow/stage_review.py", ["rejected_candidate_system_prompt", "Last quality gate error"]),
    ("kriya/workflow/stage_apply.py", ["LiveFailureChannel", "error_context=state.error_context"]),
    ("kriya/workflow/stage_planning.py", ["Fix the following compile/test error", "outside_untrusted_reference"]),
    ("kriya/knowledge/channels/live_failure.py", ["LiveFailureChannel", "extract", "error_context"]),
    ("kriya/workflow/gate_orchestrator.py", ["extract_target_test", "declares_gate_purpose", "_run_executing_candidate_gates",
                                             "_run_deferred_executing_gates"]),
    ("kriya/workflow/file_resolution.py", ["extract_target_test"]),
    ("kriya/workflow/candidate_transaction.py", ["find_edits_ignoring_reported_line"]),
    ("kriya/workflow/stage_analysis.py", ["fence_untrusted_reference", "graph_rag_context", "learned_rag_context", "convention_prompt",
                                          "retrieved_chunks"]),
    ("kriya/workflow/skill_conventions.py", ["render_skill_conventions", "Unverified Rules", "skill_applies_to_goal"]),
    ("kriya/workflow/outcome.py", ["completed_result", "unresolved_skill_gaps", "terminal_failure_category"]),
    ("kriya/workflow/context_budget.py", ["fit_developer_request", "allocation_window", "request_capacity", "OptionalSection", "DeveloperRequestFit",
                                          "fit_planner_request"]),
    ("kriya/workflow/graph_retrieval.py", ["_code_intelligence_candidates", "fold_overloads"]),
    ("kriya/core/attempt_evidence/model.py", ["KINDS", "SCHEMA", "REDACTION_POLICY_VERSION", "GENESIS_PREV", "CAPTURE_OFF", "PROVENANCES",
                                              "tool.execution", "prompt.sections", "canonical_bytes"]),
    ("kriya/core/attempt_evidence/writer.py", ["AttemptEvidenceWriter", "RecorderUnavailable", "seal", "_FILE_MODE", "degraded"]),
    ("kriya/core/attempt_evidence/reader.py", ["EvidenceRun", "verify", "VERIFIED", "SEAL_MISMATCH", "CHAIN_BROKEN", "UNSEALED", "TRUNCATED_TAIL"]),
    ("kriya/core/attempt_evidence/scope.py", ["run_scope", "ensure_store", "close_run", "record_wire_request", "GATE_PURPOSES", "declares_gate_purpose",
                                              "tool_unit", "_record_tool_execution", "_run_manifest", "_with_source_identity", "RECORDER_UNAVAILABLE",
                                              "record_fallback_decision", "prune_after_run"]),
    ("kriya/core/attempt_evidence/retention.py", ["prune_evidence"]),
    ("kriya/control/trace.py", ["TraceLogger", "log_run", "INSERT OR REPLACE", "redact_value", "ensure_private_file"]),
    ("kriya/workflow/run_lifecycle.py", ["persist_run_trace", "persist_early_exit_trace", "egress.authority", "Never raises"]),
    ("kriya/workflow/run_trace.py", ["write_outcome_trace"]),
    ("kriya/control/run_coordinator.py", ["begin_mutating_run", "_fail_active_run", "record_model_runtime_use", "claim_run_generation_clock",
                                          "terminal record not persisted"]),
    ("kriya/control/run_record.py", ["RunRecord", "RunLifecycle", "terminal_status", "commit_result", "tool_execution", "needs_recovery"]),
    ("kriya/control/source_identity.py", ["run_source_identity", "SourceIdentity"]),
    ("kriya/foundation/build_info.py", ["version_report", "embedded_build_info", "BUILD_INFO_FILE"]),
    ("kriya/application/evidence.py", ["EvidenceService", "_verified_chain", "recorded_workspace_identity"]),
    ("kriya/application/history.py", ["application_outcome", "_exit_is_acceptable", "_truth_of_events", "HistoryService",
                                      "APPLICATION_OUTCOME_PROVENANCE"]),
    ("kriya/application/run.py", ["load_run_record", "mark_in_progress"]),
    ("kriya/application/run_log.py", ["read_run_log", "kriya.log"]),
    ("kriya/core/logging_setup.py", ["run_log", "run_log_path", "resolve_log_directory", "default_log_directory"]),
    ("kriya/kup/evidence_ops.py", ["records", "verify", "call_content", "change_content"]),
    ("kriya/kup/run_log_ops.py", ["run_log"]),
    ("kriya/metrics/evidence.py", ["project_trace_row", "TraceRun"]),
    ("kriya/tools/validate.py", ["_record_gate_result", "current_gate_purpose"]),
    ("kriya/tools/web.py", ["_audit_network_access", "fetch_url_text"]),
    ("kriya/workflow/verification_coordinator.py", ["run_tests"]),
    ("kriya/workflow/named_test_oracle.py", ["named_test_closure_base_run", "named_test_closure_candidate_run"]),
    ("kriya/workflow/subtask_executor.py", ["_execute_tool_subtask"]),
    ("kriya/workflow/verification_authority.py", ["EXPECTED_NONZERO_EXIT_GROUNDED"]),
    ("ui/shared/src/cockpit/core/useRunLog.ts", []),
    ("handover/INF_001_RUNTIME_PORT.md", []),
    ("handover/PRODUCT-72H/SPRINT_LOG.md", ["65K pins on the native Ollama adapter QUALIFIED", "semantic.precision_boundary", "truncating input prompt",
                                           "class D"]),
    ("handover/claude-context/model-runtime-and-inference.md", ["kriya-ollama-native/2", "now /8"]),
    ("handover/claude-context/verification-platform-and-assurance.md", ["Observational only (I-2)"]),
    ("tests/test_inf001_runtime_port.py", []),
    ("tests/test_inf001_runtime_parity.py", []),
    ("tests/test_lr_r1_m1_equivalence.py", []),
    ("tests/test_model_runtime_capacity_vs_admission_001.py", []),
    # Second batch: symbols the A6 rows name outside the modules above (added after the rows were written).
    ("kriya/agents/agent.py", ["DEFAULT_MAX_OUTPUT_TOKENS", "REVIEWER_DEFAULT_MAX_OUTPUT_TOKENS", "_truncated_completion_error", "OUTPUT_TRUNCATED"]),
    ("kriya/core/llm.py", ["_deadline", "_require_time_left", "inference_deadline", "INFERENCE_DEADLINE_EXHAUSTED"]),
    ("kriya/core/model_qualification.py", ["save_record"]),
    ("kriya/core/model_certification.py", ["save_certification", "trial_failures"]),
    ("kriya/core/attempt_evidence/reader.py", ["ROLE_PHASES", "BLOB_CORRUPT"]),
    ("kriya/core/attempt_evidence/model.py", ["phase.opened", "candidate.change", "retry.delta", "fallback.decision", "MODEL_CLAIMED"]),
    ("kriya/workflow/generation_service.py", ["model.transition", "model.retry_identity_not_qualified"]),
    ("kriya/workflow/fallback_decision.py", ["model.fallback_selection"]),
    ("kriya/workflow/attribution.py", ["resolve_fallback_model"]),
    ("kriya/workflow/state.py", ["record_developer_attempt_outcome", "incompatible_fallbacks", "last_developer_request_profile", "error_context"]),
    ("kriya/workflow/best_of_n.py", ["error_context"]),
    ("kriya/knowledge/staging.py", ["stage_facts"]),
    ("kriya/application/catalog.py", ["SkillCatalogService", "ToolCatalogService", "approval_state"]),
    ("kriya/kup/serve.py", ["skills.list", "skills.describe", "tools.list", "tools.describe", "tools.approval_state", "history.prompt",
                            "configuration.approve", "launch.status", "run.log"]),
    ("kriya/control/run_record.py", ["abandon_tool_execution"]),
    ("tests/test_tools_command.py", ["_hermetic_workspace", "TOOLS-COMMAND-TEST-NONHERMETIC-CWD-001"]),
    ("tests/containment_h_ledger.py", ["test_the_scopes_venv_source_is_mounted_into_the_view_read_only_and_writable_only_when_asked"]),
    ("tests/test_s3_execution_views.py", ["test_the_scopes_venv_source_is_mounted_into_the_view_read_only_and_writable_only_when_asked"]),
    ("tests/state_machine/_kernel_golden.py", ["OI-53"]),
    ("tests/test_s7_containment_census.py", ["monkeypatch.delenv"]),
    ("tests/test_slice_a_real_oci_boundary.py", ["kriya runs tool retry"]),
    ("tests/test_demo_v2_owner_state.py", []),
    ("ui/shared/src/cockpit/scale.test.tsx", ["30000"]),
    ("handover/PRODUCT-72H/SPRINT_LOG.md", ["21539 passed", "REVIEWER-RUNAWAY-UNBOUNDED-OUTPUT-001"]),
    ("handover/MODULARIZATION/OPEN-ITEMS.md", ["OI-41", "OI-53", "OI-64", "OI-70", "OI-71", "OI-107", "OI-108", "OI-109", "OI-110"]),
    ("CLAUDE.md", ["~8 min"]),
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

    # Negative controls: (needle, the only files that may hold it or None for a listing, what the documents conclude from it).
    controls = [
        ("register_runtime_adapter(", ["kriya/core/inference_runtime.py", "kriya/core/model_runtime.py"],
         "runtime adapters are registered by the port module (definition) and the one provider module only"),
        ("fallback_incompatibilities(", ["kriya/workflow/generation_service.py", "kriya/workflow/fallback_decision.py", "kriya/workflow/model_transition.py", "kriya/workflow/attempt.py"],
         "the QUALIFIED-under-production rule is evaluated at the Developer model entry (fallbacks only) and at fallback selection (attempt.py holds only its lazy loader)"),
        ("run_production_doctor(", ["kriya/cli.py", "kriya/production_doctor.py"], "the production doctor is run by the `doctor --production` command only: no run invokes it"),
        ("model_role(", None, "listing: who scopes a model call to a role"),
        ("assess(", None, "listing: who assesses a qualification record"),
        (".log_run(", None, "listing: who writes a history row"),
        ("gate_purpose(", ["kriya/core/attempt_evidence/scope.py", "kriya/workflow/gate_orchestrator.py", "kriya/workflow/named_test_oracle.py",
                           "kriya/tools/validate.py"],
         "a gate purpose is declared by the candidate-gate orchestrator and the named-test oracle only (validate.py reads it)"),
        ("_augment_error_with_live_lookup(", ["kriya/workflow/live_lookup.py", "kriya/workflow/retry_strategy.py"],
         "the retry error context is augmented with fetched page text at one call site"),
        ("rejected_candidate_system_prompt(", ["kriya/agents/agent.py", "kriya/workflow/stage_review.py"],
         "the rejected-candidate Reviewer system prompt has one caller, which passes state.error_context"),
        ("fence_untrusted_reference(", None, "listing: who fences reference text"),
        ("state.error_context", None, "listing: who reads or writes the retry error context"),
        ("runtime_profile", None, "listing: who reads the runtime profile"),
        ("tool.execution", None, "listing: who names the tool-execution evidence record"),
    ]
    for needle, allowed, meaning in controls:
        found = holders(root, needle)
        print(f"control {needle!r}: found in {found} ({meaning})")
        if allowed is not None:
            extra = [rel for rel in found if rel not in allowed]
            if extra:
                problems.append(f"negative control failed: {needle!r} also in {extra}")
    # Files that must NOT hold a symbol.
    absent_in_file = [
        ("kriya/workflow/live_lookup.py", "fence_untrusted_reference", "the live-lookup augmenter does not fence the page text"),
        ("kriya/workflow/retry_strategy.py", "fence_untrusted_reference", "nor does its caller"),
        ("kriya/workflow/retry_package.py", "fence_untrusted_reference", "nor the retry package that renders it as advisory evidence"),
        ("kriya/workflow/retry_prompts.py", "fence_untrusted_reference", "nor the retry prompt builders"),
        ("kriya/workflow/repair_orchestrator.py", "fence_untrusted_reference", "nor the repair orchestrator that slices it out of the error context"),
        ("kriya/workflow/stage_review.py", "fence_untrusted_reference", "nor the review stage that passes the error context to the Reviewer"),
        ("kriya/core/inference_settings.py", "generation_time_budget", "the run budget is not part of the inference-settings identity"),
        ("kriya/core/qualification_identity.py", "generation_time_budget", "nor of the qualification identity guard"),
        ("kriya/core/model_qualification.py", "generation_time_budget", "nor of a qualification record"),
        ("kriya/core/release_candidate.py", "generation_time_budget", "nor named by the release-candidate identity module"),
        ("kriya/core/inference_settings.py", "max_context_tokens", "the admission ceiling is not part of the inference-settings identity"),
        ("kriya/workflow/verification_coordinator.py", "gate_purpose", "the terminal verification coordinator declares no gate purpose"),
        ("kriya/workflow/development_loop.py", "gate_purpose", "nor the development loop's baseline and regression runs"),
        ("kriya/workflow/requirement_closers.py", "gate_purpose", "nor the requirement closers"),
        ("kriya/workflow/regression_attribution.py", "gate_purpose", "nor regression attribution"),
        ("kriya/workflow/self_correction.py", "gate_purpose", "nor the self-correction loop"),
        ("kriya/agents/agent.py", "unresolved_skill_gap", "the Developer request does not read the unresolved skill gaps"),
        ("kriya/workflow/generation_service.py", "unresolved_skill_gap", "nor does the generation service"),
        ("kriya/agents/agent.py", "check_goal", "the agents never consult the knowledge gate"),
        ("kriya/workflow/generation_service.py", "KnowledgeGuard", "nor does the generation service"),
        ("kriya/workflow/subtask_executor.py", "record_candidate_write", "a TOOL subtask records no candidate write"),
        ("kriya/workflow/workflow.py", "run_production_doctor", "a run never invokes the production doctor"),
        ("kriya/control/trace.py", "workspace", "the history row has no workspace column"),
    ]
    for path, needle, meaning in absent_in_file:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        print(f"absent {needle!r} in {path}: {needle not in text} ({meaning})")
        if needle in text:
            problems.append(f"negative control failed: {path} contains {needle!r}")
    # Directories that must not hold a needle.
    absent_in_dir = [
        ("kriya/knowledge", "model_role", "the knowledge channels (lesson extraction) scope no model call to a role"),
        ("kriya/application", "model_qualification", "no application (desktop) use case reads a qualification"),
        ("kriya/kup", "model_qualification", "no desktop operation reads a qualification"),
        ("kriya/core/attempt_evidence", "hmac", "the evidence store has no keyed integrity"),
        ("kriya/core/attempt_evidence", "private_key", "nor a signing key"),
        ("kriya/core", "risk_threshold_lines", "the runtime layer measures no change size (GAP-A3-05)"),
        ("kriya/control", "risk_threshold_lines", "nor the control layer"),
        ("kriya/metrics", "risk_threshold_lines", "nor the metrics layer"),
        ("kriya/mcp", "record_candidate_write", "the MCP layer records no candidate write"),
        ("kriya/mcp", "candidate_digest", "the MCP layer binds no candidate digest"),
        ("kriya/workflow", "kriya.metrics", "the workflow never imports the metrics package"),
    ]
    for directory, needle, meaning in absent_in_dir:
        found = [rel for rel in holders(root, needle) if rel.startswith(directory + "/")]
        print(f"absent {needle!r} under {directory}/: {not found} ({meaning})")
        if found:
            problems.append(f"negative control failed: {needle!r} in {found}")
    for problem in problems:
        print(problem)
    print(f"files={len(CITED)} symbols={symbols} controls={len(controls) + len(absent_in_file) + len(absent_in_dir)} problems={len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

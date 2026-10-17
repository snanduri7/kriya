"""Suite-wide isolation, inherited by CLI subprocesses through the environment:
- every Kriya log (application and per-run) goes to one session temp dir,
  never the real ~/.kriya/logs;
- every test gets its own fresh state directory (traces.db), never the real
  ~/.kriya/state: tests read trace rows back, so one shared database would leak
  rows between tests. Locate it with kriya.core.state_paths.trace_db_path();
- the historical pre-move trace database location points at a path that does
  not exist, so a developer's real <install>/logs/traces.db never changes a
  test's outcome (tests that need one patch it explicitly).
- PRD-013: the model runtime fingerprint probe (Ollama /api/version,
  /api/tags, /api/show) is disabled, so a mocked test can never reach a real
  local model endpoint running on the developer's machine. Tests marked
  live_model keep it enabled; probe tests inject their own transport. The
  PRD-014 qualification store is a per-test temp dir for the same reason.
Tests of the precedence rules unset KRIYA_LOG_DIR / KRIYA_STATE_DIR themselves."""
import os
import sys

import pytest

from kriya.core.logging_setup import ENV_LOG_DIR
from kriya.core.state_paths import ENV_STATE_DIR


@pytest.fixture(scope="session", autouse=True)
def _isolated_kriya_log_dir(tmp_path_factory):
    previous = os.environ.get(ENV_LOG_DIR)
    os.environ[ENV_LOG_DIR] = str(tmp_path_factory.mktemp("kriya-logs"))
    yield
    if previous is None:
        os.environ.pop(ENV_LOG_DIR, None)
    else:
        os.environ[ENV_LOG_DIR] = previous


@pytest.fixture(autouse=True)
def _isolated_kriya_state_dir(tmp_path_factory, monkeypatch):
    from kriya.core import state_paths

    state_dir = tmp_path_factory.mktemp("kriya-state")
    monkeypatch.setenv(ENV_STATE_DIR, str(state_dir))
    monkeypatch.setattr(state_paths, "historical_default_trace_db",
                        lambda: str(state_dir / "no-historical-install" / "logs" / "traces.db"))


@pytest.fixture(autouse=True)
def _no_repository_root_package_pollution(request):
    """TEST-WORKTREE-POLLUTION-001: no test may create package.json,
    package-lock.json or node_modules in the repository root. Four
    ShellTool profile tests used to run the real `npm install left-pad` in
    the pytest cwd through a backend that contained nothing. The check
    names the test that did it (nothing is deleted: the artifacts are the
    evidence); under xdist a concurrent worker's test can be the real
    writer, which the message says."""
    from _worktree_pollution import new_package_artifacts, present_package_artifacts

    root = str(request.config.rootpath)
    before = present_package_artifacts(root)
    yield
    created = new_package_artifacts(root, before)
    if created:
        pytest.fail(
            f"TEST-WORKTREE-POLLUTION-001: {', '.join(created)} appeared in the repository root {root} "
            "during this test (or a test running concurrently on another xdist worker): a package-manager "
            "command ran in the process cwd; use tmp_path or tests/_strict_doubles.py::ProfileCapturingBackend"
        )


_TEST_HOST = {"os": "linux", "architecture": "x86_64", "memory_bytes": 64 * (1 << 30), "cpu_model": None,
              "gpus": [], "gpu_backend": None}


@pytest.fixture(autouse=True)
def _no_model_runtime_probe(request, monkeypatch, tmp_path_factory):
    from kriya.core import execution_environment, model_certification, model_qualification, model_runtime

    if request.node.get_closest_marker("live_model") is None:
        monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "0")
        # Qualification environment identity: a fixed, exact fake host, so a
        # mocked test never depends on the machine (sysctl/nvidia-smi/PATH)
        # it runs on. Environment tests override this themselves.
        monkeypatch.setattr(execution_environment, "_cached_host_properties", lambda: dict(_TEST_HOST))
        # PRD-014: never read or write the developer's real qualification store.
        monkeypatch.setenv(model_qualification.QUALIFICATION_HOME_ENV,
                           str(tmp_path_factory.mktemp("kriya-qualifications")))
        # PRD-035/036: never read or write the real certification records or release streaks.
        monkeypatch.setenv(model_certification.CERTIFICATION_HOME_ENV,
                           str(tmp_path_factory.mktemp("kriya-certifications")))
    model_runtime.clear_model_runtime_cache()
    yield
    model_runtime.clear_model_runtime_cache()


@pytest.fixture(autouse=True)
def _no_embedding_identity_probe(request, monkeypatch):
    """EMBEDDING-CONTRACT-001: OllamaEmbeddingClient.fingerprint() asks the
    real endpoint for the model's digest and served context; a mocked test
    must never reach a developer's live Ollama. The identity is a fixed fake
    whose dimension comes from the instance's own (usually patched)
    get_embedding. A test of the real probe marks itself
    real_embedding_identity and injects its own transport."""
    if request.node.get_closest_marker("real_embedding_identity") or request.node.get_closest_marker("live_model"):
        yield
        return
    from _fake_embedding import fake_fingerprint

    from kriya.memory.embedding import OllamaEmbeddingClient

    async def fingerprint(client):
        if client._fingerprint is None:
            client._fingerprint = fake_fingerprint(len(await client.get_embedding("kriya embedding identity probe")),
                                                   client.model)
        return client._fingerprint

    monkeypatch.setattr(OllamaEmbeddingClient, "fingerprint", fingerprint)
    yield


@pytest.fixture(autouse=True)
def _scripted_developer_answers_speak_the_requested_protocol(request, monkeypatch):
    """FILE-INTEGRITY-CONTRACT-001: the production Developer protocol is the
    structured sentinel protocol, while most scripted model answers in this
    suite predate it (raw file content or the legacy markers). At the one
    per-file completion seam (DeveloperAgent._complete_file) such an answer
    is rendered into the protocol the prompt asks for - the same intent,
    read by the real legacy parser (tests/_protocol_responses.py). The
    production parser is untouched, a malformed answer stays malformed, and
    an answer already containing a sentinel line is never rewritten. A test
    that asserts how the production parser treats a non-sentinel answer opts
    out with @pytest.mark.developer_answers_verbatim; live tests never
    translate."""
    if request.node.get_closest_marker("developer_answers_verbatim") or request.node.get_closest_marker("live_model"):
        yield
        return
    from _protocol_responses import as_requested

    from kriya.agents.agent import DeveloperAgent

    real = DeveloperAgent._complete_file

    async def speaking_the_requested_protocol(self, system_prompt, prompt, **options):
        answer = await real(self, system_prompt, prompt, **options)
        return as_requested(answer, system_prompt) if isinstance(answer, str) else answer

    monkeypatch.setattr(DeveloperAgent, "_complete_file", speaking_the_requested_protocol)
    yield


# --- PRD-032 chaos report ----------------------------------------------------------
# Every @chaos test's verdict and observation is collected; with
# `--chaos-report DIR` the session writes chaos-report.json/.md there
# (tests/_chaos_report.py). Collection never changes a test's outcome.

def pytest_addoption(parser):
    parser.addoption("--chaos-report", default=None, metavar="DIR",
                     help="PRD-032: write the chaos report (JSON + Markdown) to DIR")
    parser.addoption("--certification", action="store_true", default=False,
                     help="PRD-034: an unexpected skip or xfail fails (tests/certification/skip_allowlist.yaml)")
    parser.addoption("--certification-report", default=None, metavar="DIR",
                     help="PRD-034: write skips.json (every skip, allowlisted or not) to DIR")
    parser.addoption("--model-certification", default=None, metavar="DIR",
                     help="PRD-035: write model-certification.json/.md (the live matrix report) to DIR")


# --- state-machine tier ------------------------------------------------------------
# `pytest -m state_machine` (docs: handover/STATE_MACHINE_HARDENING_REPORT.md):
# tests/state_machine/ marks itself; these existing deterministic suites own
# the rest of the tier's transition families and are marked here, in one
# place, rather than file by file. None may carry a live marker
# (tests/state_machine/test_sm_tier_guard.py).
STATE_MACHINE_TIER_FILES = frozenset({
    # retry / recovery / fallback
    "test_retry_policy.py", "test_prd026_retry_progress.py", "test_workflow_recovery_handback.py",
    "test_failure_signature_run_noise.py", "test_prd031_coordinators.py", "test_best_of_n.py",
    "test_prd017_fallback_transition.py",
    "test_attempt_lifecycle.py",  # E5: the Developer loop's typed attempt plan and iteration lifecycle
    "test_fallback_decision.py",  # E6: the llm_chain fallback decisions (selection, call-time substitution)
    "test_generation_service.py",  # E6: the Developer request assembly of every generation mode
    # resume / invalidation
    "test_prd008_resume_fingerprints.py", "test_prd008a_resume_convergence.py", "test_resume_integrity.py",
    "test_prd008_recovery.py",
    # verification -> terminal gates -> commit, static analysis -> commit
    "test_candidate_verification_binding.py", "test_prd030_terminal_services.py",
    "test_prd008_commit_state_gate.py", "test_prd032_terminal_commit_stop.py", "test_prd032_chaos_commit.py",
    "test_prd032_chaos_static_analysis.py", "test_prd031a_static_analysis.py",
    # process lifecycle feeding workflow decisions
    "test_platform_process_control_contract.py", "test_prd032_chaos_runtime.py",
    # kernel seams: patched names and their call-through (full modularization E1; the W4 per-slice gate)
    "test_kernel_seams.py",
})


# --- module tiers (full modularization; handover/MODULARIZATION/TEST-MATRIX.md §1) ---------------------
# `pytest -m <module>` selects one logical module's owning suites. Built in slice A1 from the
# TEST-MATRIX name families (a family is a substring of the file name, never a glob) and listed
# explicitly so a rename is visible; every entry must exist (tests/architecture/test_import_direction.py).
# A file may carry several markers. tests/state_machine/ is `kernel` and tests/architecture/ is
# `architecture` by directory; tests/contracts/ and tests/integration/ join in the slices that create
# them. Q1 (test_module_selectors.py) makes the table total; until then a file outside it simply
# carries no module marker.
MODULE_TIER_FILES = {
    "kernel": frozenset({
        "test_attribution.py", "test_backend_final_closure_005_planner_guidance.py",
        "test_backend_final_closure_005_repair_cycle.py", "test_backend_final_closure_005_retry_grounding.py",
        "test_backend_readiness_v2_plan_grounding.py", "test_best_of_n.py", "test_candidate_verification_binding.py",
        "test_code_intel_index_correctness.py", "test_code_intel_planner_grounding.py", "test_code_intel_retry_t0.py",
        "test_context_budget.py", "test_context_edit_protocol_001.py", "test_context_edit_protocol_large_file_001.py",
        "test_corr016_planner_authority_gate.py", "test_corr018_general_case_closure.py",
        "test_d2_validator_attribution_authority.py", "test_d5b_recovery_evidence.py",
        "test_direct_plan_real_maven.py", "test_edit_revisions.py", "test_edit_safety_policy_audit.py",
        "test_enforce_verified_no_change.py", "test_failure_signature_run_noise.py",
        "test_file_integrity_contract_001.py", "test_file_integrity_contract_001b.py",
        "test_fs1_false_success_reproducer.py", "test_fs1a_test_execution_evidence.py",
        "test_fs1c0_named_test_oracle.py", "test_fs1c1_requirement_claims.py",
        "test_fs1c_named_test_oracle_dependency.py", "test_generation_time_budget.py",
        "test_gr0_retry_no_information_gain.py", "test_greenfield_direct_plan_runtime.py",
        "test_jdtls_candidate_isolation.py", "test_kernel_seams.py", "test_kup_evidence_corruption_matrix.py",
        "test_launch_refuses_milestone_mode.py", "test_live_prd017_019_model_roles.py",
        "test_lr_r1_m1_attempt_iteration.py", "test_lr_r1_m1_candidate_gates.py", "test_lr_r1_m1_recovery.py",
        "test_lr_r1_p4_verification_only_retry_reproducer.py", "test_lr_r1_p4_verification_retry_admission.py",
        "test_milestone2.py", "test_milestone3_4.py", "test_milestone_validation.py", "test_milestones.py",
        "test_outcome.py",  # E2: run outcome construction (result dict / terminal trace row) per terminal path
        "test_work_unit_stages.py",  # E3: the design stages (0-3) and their runner; E4: the review/apply/lesson stages
        "test_early_exit_traces.py",  # E4: the run-opening early exits and their short traces.db rows (OI-32)
        "test_development_loop.py",  # E5: the Developer + Quality Gates loop (stage 4) and its seam
        "test_attempt_lifecycle.py",  # E5: the attempt plan (== retry_policy.decide_for_state) and the iteration lifecycle
        "test_generation_service.py",  # E6: the Developer request assembly and generation (generation_service)
        "test_fallback_decision.py",  # E6: the llm_chain fallback decisions (fallback_decision)
        "test_p2_compile_regression_attribution.py", "test_p2_regression_attribution_reproducer.py",
        "test_p3c_prose_candidate_delta.py", "test_plan_edit_feasibility_001.py", "test_plan_executability.py",
        "test_plan_external_package_capability_001.py", "test_plan_global_invariant_projection_001.py",
        "test_plan_obligation_superseded.py", "test_plan_schema.py", "test_plan_validation.py",
        "test_plan_verification_scope.py", "test_planner_candidate_evidence.py", "test_planner_context_fit.py",
        "test_planner_file_ownership.py", "test_planner_robust_001_repair.py", "test_planner_structured_output.py",
        "test_planner_target_consistency.py", "test_planner_test_stage_no_change.py",
        "test_planner_verification_contract.py", "test_platform_process_control_contract.py",
        "test_policy_enforcement.py", "test_policy_enforcement_adversarial.py", "test_prd006_candidate_workspace.py",
        "test_prd008_commit_state_gate.py", "test_prd008_recovery.py", "test_prd008_resume_fingerprints.py",
        "test_prd008_s4b_milestone_completion.py", "test_prd008_s4c_milestone_resume.py",
        "test_prd008a_plan_adapters.py", "test_prd008a_plan_executor.py", "test_prd008a_resume_convergence.py",
        "test_prd016_adaptive_budget.py", "test_prd016_token_budget.py", "test_prd017_fallback_transition.py",
        "test_prd019_milestone_route_resume.py", "test_prd020_milestone_requirements.py",
        "test_prd022_ownership_recovery.py", "test_prd026_retry_progress.py", "test_prd030_terminal_services.py",
        "test_prd031_coordinators.py", "test_prd031a_static_analysis.py", "test_prd032_chaos_commit.py",
        "test_prd032_chaos_runtime.py", "test_prd032_chaos_static_analysis.py", "test_prd032_planner_vocabulary.py",
        "test_prd032_planner_workspace_path.py", "test_prd032_terminal_commit_stop.py",
        "test_prompt_budget_fit_001ab.py", "test_prompt_budget_fit_001c.py", "test_prv17_preflight.py",
        "test_prv17_stage_contract_architecture.py", "test_reg_r1_workflow_reproducer.py",
        "test_reg_r2_workflow_reproducer.py", "test_regression_attribution.py",
        "test_repair_002_d1_candidate_gate_baseline.py", "test_repair_002_d2_structural_evidence.py",
        "test_repair_002_d3_file_resolution_scope.py", "test_repair_002_d4_identical_write.py",
        "test_repair_003_f1_residual_self_correction_rerun_environment_stop.py",
        "test_repair_003_f1_runtime_environment_stop.py", "test_repair_003_f3_suite_preservation_baseline_policy.py",
        "test_repair_003_p1_2_suite_preservation_attribution.py", "test_repair_003_p2_2_venv_environment_failure.py",
        "test_repair_contract.py", "test_repair_executor.py", "test_resume_integrity.py",
        "test_retry_inference_identity.py", "test_retry_package.py", "test_retry_policy.py",
        "test_self_correction.py", "test_subtask_checkpoint.py", "test_subtask_context_projection.py",
        "test_subtask_executor.py", "test_subtask_telemetry.py", "test_tool003_p2_oci_enforcement.py",
        "test_tool_subtask_hitl_confirmation.py", "test_val001_g1r3_retry_context.py", "test_workflow.py",
        "test_workflow_approval_policy_audit.py", "test_workflow_authorize_action.py", "test_workflow_controller.py",
        "test_workflow_controller_enforce.py", "test_workflow_execution_policy_config_wiring.py",
        "test_workflow_recovery_handback.py"
    }),
    "application": frozenset({
        "test_application_services.py", "test_application_write_services.py", "test_kup_catalog.py", "test_kup_change_review.py",
        "test_kup_config_approval.py", "test_kup_execution.py", "test_kup_project_context.py", "test_kup_project_init.py",
        "test_kup_runtime_command_review.py", "test_kup_workspace.py", "test_launch_approval.py", "test_run_service.py",
        "contracts/test_kriya_client_contract.py"
    }),
    "context": frozenset({
        "test_backend_readiness_004_platform_paths.py", "test_linux_oci_workspace_identity.py",
        "test_plat002_outside_workspace_identity.py", "test_request_context.py",
        "test_state001_checkpoint_workspace_identity.py", "test_workspace_identity.py"
    }),
    "authority": frozenset({
        "test_architecture_regression_index.py", "test_authority_cli_refusal.py", "test_authority_invariants.py", "test_authority_port.py",
        "test_risk_vocabulary_persistence.py", "test_approval_persistence.py",
        "test_backend_readiness_004_authority_request.py", "test_backend_readiness_v2_gradle_regression.py",
        "test_candidate_verification_binding.py", "test_corr016_planner_authority_gate.py",
        "test_d1_operation_mode_authority.py", "test_d2_validator_attribution_authority.py",
        "test_d8_terminal_toolchain_authority.py", "test_edit_safety_policy_audit.py",
        "test_empty_change_source_approval.py", "test_execution_policy_config.py",
        "test_gr0_negative_model_authority.py", "test_gr1b_spec_gate_model_authority.py",
        "test_kup_config_approval.py", "test_kup_content_authority.py", "test_launch_approval.py",
        "test_learn_egress_policy.py", "test_live_prd012_egress_injection.py",
        "test_llm_egress_policy_integration.py", "test_lr_r1_m1_authority_parse.py",
        "test_p2_compile_regression_attribution.py", "test_p2_regression_attribution_reproducer.py",
        "test_p3a_sent_request_anchor_authority.py", "test_policy_approval_rules.py",
        "test_policy_approved_sources.py", "test_policy_command_allowlist.py", "test_policy_enforcement.py",
        "test_policy_enforcement_adversarial.py", "test_policy_errors.py", "test_policy_execution.py",
        "test_policy_execution_sensitive_path_override.py", "test_policy_filesystem.py",
        "test_policy_filesystem_authorized_writer.py", "test_policy_git_destructive.py", "test_policy_injection.py",
        "test_policy_model.py", "test_policy_network_egress.py", "test_policy_package_supply_chain.py",
        "test_policy_telemetry.py", "test_policy_trust.py", "test_prd012_egress.py", "test_prd012_egress_docker.py",
        "test_prd012_network_inventory.py", "test_prd024_baseline_auto_policy.py",
        "test_prd028_authority_escalation.py", "test_reg_r1_pytest_regression_authority.py",
        "test_regression_attribution.py", "test_regression_unattributed_prose_001.py",
        "test_repair_003_f3_suite_preservation_baseline_policy.py", "test_retry_policy.py",
        "test_runtime_command_approval.py", "test_sec009_config_authority.py", "test_sec009_p2_authority_approval.py",
        "test_semantic_region_authority.py", "test_tool002_mcp_invocation_authority.py",
        "test_tool002_p2_invocation_approval.py", "test_tool002_p2_real_cli.py",
        "test_tool002_tool003_combined_closure.py", "test_tool003_mcp_capability_authority.py",
        "test_tool003_p2_deterministic.py", "test_tool003_p2_oci_enforcement.py", "test_validate_policy_audit.py",
        "test_validate_resource_authority.py", "test_verification_contract_003_authority.py",
        "test_web_policy_audit.py", "test_workflow_approval_policy_audit.py",
        "test_workflow_execution_policy_config_wiring.py", "test_worktree_policy_audit.py"
    }),
    "evidence": frozenset({
        "test_backend_readiness_004_evidence_tools.py", "test_batch6_live_evidence_status.py",
        "test_d5b_recovery_evidence.py", "test_decision_records.py", "test_fs1a_test_execution_evidence.py", "test_kup_evidence_call_content.py",
        "test_kup_evidence_change_content.py", "test_kup_evidence_cli.py", "test_kup_evidence_corruption_matrix.py",
        "test_kup_evidence_manifest.py", "test_kup_evidence_records.py", "test_kup_evidence_verify.py",
        "test_lr_r1_m1_0_inventory.py", "test_lr_r1_m1_attempt_iteration.py", "test_lr_r1_m1_authority_parse.py",
        "test_lr_r1_m1_candidate_gates.py", "test_lr_r1_m1_cli.py", "test_lr_r1_m1_doctor.py",
        "test_lr_r1_m1_equivalence.py", "test_lr_r1_m1_equivalence_scenarios.py", "test_lr_r1_m1_layout_tripwire.py",
        "test_lr_r1_m1_legacy_import.py", "test_lr_r1_m1_lv2_q4.py", "test_lr_r1_m1_mirroring.py",
        "test_lr_r1_m1_model_calls.py", "test_lr_r1_m1_native_wire_parity.py", "test_lr_r1_m1_reader_json_shape.py",
        "test_lr_r1_m1_reason_codes.py", "test_lr_r1_m1_recovery.py", "test_lr_r1_m1_retention.py",
        "test_lr_r1_m1_scopes.py", "test_lr_r1_m1_sections.py", "test_lr_r1_m1_store.py", "test_lr_r1_m1_t6_fixes.py",
        "test_lr_r1_m1_t6_paths.py", "test_lr_r1_m1_wire.py", "test_lr_r1_p5_integration_evidence.py",
        "test_model_evidence_hardening_001.py", "test_model_evidence_hardening_final.py",
        "test_planner_candidate_evidence.py", "test_prd011_toolchain_evidence.py", "test_prd025_verifier_evidence.py",
        "test_repair_002_d2_structural_evidence.py", "test_sec002_fail_closed_evidence.py",
        "test_ver004_pyproject_dependency_evidence.py", "contracts/test_evidence_state_stores_contract.py"
    }),
    "state": frozenset({
        "test_checkpoint_control_plane_hashes.py", "test_checkpoint_store.py", "test_control_artifact_persistence.py",
        "test_control_artifacts.py", "test_control_context.py", "test_control_contract_persistence.py",
        "test_control_contracts.py", "test_control_decisions.py", "test_control_persistence.py",
        "test_control_plane_end_to_end.py", "test_control_state.py", "test_control_telemetry.py", "test_history_store.py",
        "test_kup_acquisition.py", "test_kup_host_environment.py", "test_lr_r1_m1_retention.py",
        "test_plat039_trusted_control_paths.py", "test_platform_process_control_contract.py",
        "test_prd008_commit_state_gate.py", "test_prd008_recovery.py", "test_prd008_resume_fingerprints.py",
        "test_prd008_s4b_milestone_completion.py", "test_prd008_s4c_milestone_resume.py",
        "test_prd008a_cross_path.py", "test_prd008a_execution_plan.py", "test_prd008a_plan_adapters.py",
        "test_prd008a_plan_executor.py", "test_prd008a_resume_convergence.py",
        "test_prd019_milestone_route_resume.py", "test_reopened_project_history.py", "test_resume_integrity.py",
        "test_run_record.py", "test_state001_checkpoint_workspace_identity.py", "test_state_location.py",
        "test_subtask_checkpoint.py", "test_traces_command.py", "contracts/test_evidence_state_stores_contract.py"
    }),
    "config": frozenset({
        "test_autonomy_config_registry_hosts.py", "test_code_intel_config.py", "test_config.py",
        "test_config_command.py", "test_config_extra.py", "test_execution_policy_config.py",
        "test_kup_config_approval.py", "test_qual_config_001.py", "test_sec009_config_authority.py",
        "test_workflow_execution_policy_config_wiring.py", "test_environment_reader.py", "test_config_service.py",
        "contracts/test_config_resolution_contract.py"
    }),
    "provider": frozenset({
        "test_embedding_contract_001.py", "test_inf001_runtime_parity.py", "test_inf001_runtime_port.py",
        "test_learn_embedding_failure_warning.py", "test_live_prd013_016_model_runtime.py",
        "test_live_prd017_019_model_roles.py", "test_llm_egress_policy_integration.py", "test_llm_extra.py",
        "test_lr_r1_p1_capability_routing.py", "test_prd013_model_runtime.py", "test_prd014_model_qualification.py",
        "test_prd015_completion_result.py", "test_prd016_adaptive_budget.py", "test_prd016_allocation.py",
        "test_prd016_token_budget.py", "test_prd017_fallback_transition.py", "test_prd018_role_metrics.py",
        "test_prd019_milestone_route_resume.py", "test_prd019_model_routing.py", "test_prompt_budget_fit_001ab.py",
        "test_prompt_budget_fit_001c.py", "test_provider_contract_001.py", "test_provider_contract_001a.py",
        "test_provider_contract_native.py", "test_routing.py"
    }),
    "skills": frozenset({
        "test_auto_skills.py", "test_shared_skills_dir_guard.py", "test_skill_service.py", "test_skill_sources.py",
        "test_skills.py"
    }),
    "tools": frozenset({
        "test_backend_readiness_004_evidence_tools.py", "test_d8_terminal_toolchain_authority.py",
        "test_live_prd011_toolchain_parity.py", "test_mcp.py", "test_plugins.py", "test_plugins_command.py",
        "test_prd011_toolchain_evidence.py", "test_prd011_toolchain_fingerprint.py",
        "test_prd011_toolchain_identity.py", "test_prd011_toolchain_identity_oci.py",
        "test_prd011_toolchain_migration.py", "test_prd_tools.py", "test_sec003_mcp_env_isolation.py",
        "test_sec004_mcp_lifecycle.py", "test_tool001_autonomous_tool_execution.py",
        "test_tool002_mcp_invocation_authority.py", "test_tool002_p2_invocation_approval.py",
        "test_tool002_p2_real_cli.py", "test_tool002_tool003_combined_closure.py",
        "test_tool003_mcp_capability_authority.py", "test_tool003_p2_deterministic.py",
        "test_tool003_p2_oci_enforcement.py", "test_tool_service.py", "test_tool_subtask_hitl_confirmation.py",
        "test_tools.py", "test_tools_command.py"
    }),
    "execution": frozenset({
        "test_backend_readiness_v2_no_sandbox.py", "test_capability_adapters.py", "test_containment.py",
        "test_containment_gitfile_mask.py", "test_containment_oci.py", "test_containment_oci_registry_scoped_unit.py",
        "test_d2_validator_attribution_authority.py", "test_d3_diagnosis_mismatch_scope.py",
        "test_d3_runtime_prerequisite.py", "test_d4_runtime_entrypoint_ownership.py",
        "test_d5_runtime_resource_grounding.py", "test_direct_plan_real_maven.py",
        "test_gradle_wrapper_containment_001.py", "test_live_process_helper.py", "test_lsp.py",
        "test_platform_process_control_contract.py", "test_prd031a_semgrep_adapter.py",
        "test_prd031a_semgrep_live.py", "test_prd031a_static_analysis.py", "test_prd032_chaos_static_analysis.py",
        "test_process_controller.py", "test_process_controller_containment.py", "test_process_profile.py",
        "test_repair_002_d3_file_resolution_scope.py", "test_repair_002_d4_identical_write.py",
        "test_runtime_maven_acquisition.py", "test_sandbox.py", "test_service_runtime.py",
        "test_service_runtime_oci.py", "test_validate_maven_acquisition.py", "test_validate_oci.py",
        "test_validate_package_audit.py", "test_validate_policy_audit.py", "test_validate_resource_authority.py",
        "test_ver006_distrust_containment.py"
    }),
    "repository": frozenset({
        "test_atomic_write.py", "test_backend_final_closure_005_git_metadata.py",
        "test_backend_readiness_v2_terminal_masking_gate.py", "test_commit_mechanism.py",
        "test_d8_terminal_toolchain_authority.py", "test_edit_safety_policy_audit.py",
        "test_file_integrity_contract_001.py", "test_file_integrity_contract_001b.py",
        "test_prd004_commit_failure.py", "test_prd005_commit_transactions.py", "test_prd008_commit_state_gate.py",
        "test_prd030_terminal_services.py", "test_prd032_chaos_commit.py", "test_prd032_terminal_commit_stop.py",
        "test_worktree_canonical_root.py", "test_worktree_hooks.py", "test_worktree_policy_audit.py",
        "test_worktree_pollution_001.py"
    }),
    "knowledge": frozenset({
        "test_analyzer.py", "test_analyzer_cache.py", "test_cagc_ab_analyzer.py", "test_code_intel_chunks.py",
        "test_code_intel_config.py", "test_code_intel_construction_sites.py", "test_code_intel_developer_t0.py",
        "test_code_intel_index_correctness.py", "test_code_intel_integration.py",
        "test_code_intel_localization_decision.py", "test_code_intel_planner_grounding.py",
        "test_code_intel_retrieval.py", "test_code_intel_retry_t0.py", "test_code_intel_service.py",
        "test_code_intel_structure.py", "test_demo_v2_knowledge_task.py", "test_demo_v2_knowledge_task_c2.py",
        "test_dependency_graph.py", "test_graph_context_empty_header_001.py", "test_graphify_oversize_request_001.py",
        "test_knowledge.py", "test_knowledge_extraction.py", "test_knowledge_readpath_001.py",
        "test_outbound_lookup.py", "test_prd027_context_certification.py", "test_prd027_precision_expansion_seeds.py",
        "test_prd027_retrieval_defects.py", "test_prd027_score_normalization_001.py"
    }),
    "qualification": frozenset({
        "test_doctor_command.py", "test_live_prd035_certification.py", "test_live_production_doctor.py",
        "test_lr_r1_m1_doctor.py", "test_model_qual_identity_001.py", "test_platform_release_identity.py",
        "test_prd014_model_qualification.py", "test_prd027_context_certification.py", "test_prd034_certification.py",
        "test_prd035_certification_records.py", "test_prd036_release_gate.py", "test_production_doctor.py",
        "test_qual_config_001.py", "test_qual_environment_identity.py", "test_qualification_artifact_stability_001.py"
    }),
    "adapters": frozenset({
        "test_authority_cli_refusal.py", "test_cli_smoke.py", "test_kup_cli.py", "test_kup_content_authority.py",
        "test_kup_display_goal.py", "test_kup_evidence_cli.py", "test_kup_request_fuzz.py", "test_kup_serve.py",
        "test_kup_write_boundary.py", "test_live_prd033_metrics.py", "test_lr_r1_m1_cli.py",
        "test_prd018_role_metrics.py", "test_prd033_events_and_cli.py", "test_prd033_metrics.py", "test_repl.py",
        "test_tool002_p2_real_cli.py"
    }),
    "architecture": frozenset({
        "test_architecture_regression_index.py", "test_auth_goal_contamination_001.py", "test_backlog_registry.py",
        "test_file_integrity_contract_001.py", "test_file_integrity_contract_001b.py",
        "test_knowledge_readpath_001.py", "test_lr_r1_m1_0_inventory.py", "test_platform_architecture_guard.py",
        "test_prd012_network_inventory.py", "test_prd030_terminal_services.py", "test_prd031_coordinators.py",
        "test_strict_doubles.py", "test_tool001_autonomous_tool_execution.py"
    }),
    "integration": frozenset({
        "test_direct_plan_real_maven.py", "test_live_prd032_chaos.py", "test_live_smoke.py",
        "test_plan_global_invariant_projection_001.py", "test_prd032_chaos_commit.py", "test_prd032_chaos_harness.py",
        "test_prd032_chaos_injection.py", "test_prd032_chaos_model.py", "test_prd032_chaos_runtime.py",
        "test_prd032_chaos_static_analysis.py", "test_prd032_planner_vocabulary.py",
        "test_prd032_planner_workspace_path.py", "test_prd032_terminal_commit_stop.py"
    }),
}
MODULE_TIER_DIRECTORIES = {"state_machine": "kernel", "architecture": "architecture", "integration": "integration"}
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_MODULE_MARKERS_BY_FILE = {}
for _marker, _names in MODULE_TIER_FILES.items():
    for _name in _names:
        _MODULE_MARKERS_BY_FILE.setdefault(_name, []).append(_marker)


def _module_markers(path):
    directory, name = os.path.split(str(path))
    if directory == _TESTS_DIR:
        return _MODULE_MARKERS_BY_FILE.get(name, [])
    parent, sub = os.path.split(directory)
    if parent == _TESTS_DIR and sub == "contracts":  # one shared suite per port: each file belongs to its port's module tier
        return _MODULE_MARKERS_BY_FILE.get(f"contracts/{name}", [])
    return [MODULE_TIER_DIRECTORIES[sub]] if parent == _TESTS_DIR and sub in MODULE_TIER_DIRECTORIES else []


# pyproject's addopts deselects these tiers by default, but an explicit `-m <module marker>` REPLACES that
# expression, so a module marker on one of their tests would pull a live/real-toolchain run into an ordinary
# module selection (W2 wave check: `-m adapters` selected test_live_prd033_metrics). They carry no module marker.
_DEFAULT_EXCLUDED_TIERS = ("live_model", "live_static_analysis", "real_toolchain")


def _in_default_excluded_tier(item):
    return any(marker.name in _DEFAULT_EXCLUDED_TIERS for marker in item.iter_markers())


# Parallel runs (`-n 8 --dist loadgroup`): every test that starts real
# containers runs on ONE worker, serially. Their leak checks list every
# kriya-oci-*/kriya-acq-* container on the daemon - deliberately global - so
# two such tests running at once see each other's live containers (measured:
# 7 parallel-only failures, zero real leftovers after the run). A real-Docker
# test is the one gated on Docker being there: a skip/skipif naming docker.
# Grouping by file text instead pinned ~540 tests (config tests that merely
# say "docker") to one worker and doubled the wall time.
def _needs_real_docker(item):
    return any("docker" in str(marker.kwargs.get("reason", "")).lower()
               for marker in item.iter_markers() if marker.name in ("skip", "skipif"))


# tryfirst: xdist's own hook turns the xdist_group marker into the node id
# suffix that --dist loadgroup schedules on; it must see the marker.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.path.name in STATE_MACHINE_TIER_FILES:
            item.add_marker(pytest.mark.state_machine)
        if not _in_default_excluded_tier(item):
            for marker in _module_markers(item.path):
                item.add_marker(getattr(pytest.mark, marker))
        if _needs_real_docker(item):
            item.add_marker(pytest.mark.xdist_group("docker"))


_SKIPS = pytest.StashKey[list]()
_CERTIFICATION_CASES = pytest.StashKey[list]()
_ALLOWLIST = pytest.StashKey[tuple]()


def _certification_mode(config):
    return config.getoption("--certification") or os.environ.get("KRIYA_CERTIFICATION") == "1"


def pytest_configure(config):
    from _certification import load_allowlist
    from _chaos_report import RESULTS

    config.stash[RESULTS] = []
    config.stash[_SKIPS] = []
    config.stash[_CERTIFICATION_CASES] = []
    # A malformed allowlist fails certification up front, never later.
    config.stash[_ALLOWLIST] = load_allowlist() if _certification_mode(config) else ()


@pytest.fixture(scope="session", autouse=True)
def _interpreter_tools_on_path():
    """PRD-034: the environment under test is the interpreter running pytest.
    Tests that exercise Kriya running `python`/`pip` from PATH resolve them
    here, never from whatever the operator's shell happened to have first."""
    previous = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join([os.path.dirname(sys.executable), previous])
    yield
    os.environ["PATH"] = previous


def _audit_skip(item, report):
    """PRD-034: record every skip/xfail; in certification mode an
    unallowlisted one becomes a failure."""
    if not (report.skipped or hasattr(report, "wasxfail")):
        return
    from _certification import match_skip, skip_reason

    reason = str(getattr(report, "wasxfail", "")) or skip_reason(report.longrepr)
    entry, expired = match_skip(item.config.stash[_ALLOWLIST], item.nodeid, reason)
    allowed = entry is not None and not expired
    item.config.stash[_SKIPS].append({
        "nodeid": item.nodeid, "phase": report.when, "reason": reason,
        "kind": "xfail" if hasattr(report, "wasxfail") else "skip",
        "allowlisted": entry.id if allowed else None, "expired_entry": entry.id if expired else None,
    })
    if _certification_mode(item.config) and not allowed:
        report.outcome = "failed"
        report.longrepr = (f"UNEXPECTED SKIP in certification mode: {reason}"
                           + (f" (allowlist entry {entry.id} expired)" if expired else ""))


def _record_chaos_phase(item, report):
    """PRD-032: every @chaos test's phases, verdict and observation."""
    marker = item.get_closest_marker("chaos")
    if marker is None:
        return
    from _chaos_report import PHASE_REPORTS, RESULTS, item_verdict

    phases = item.stash.setdefault(PHASE_REPORTS, {})
    phases[report.when] = report
    if report.when == "teardown":
        observation = next((value for key, value in item.user_properties if key == "chaos_observation"), None)
        item.config.stash[RESULTS].append({
            "scenario_id": marker.args[0], "nodeid": item.nodeid,
            "verdict": item_verdict(phases), "observation": observation,
        })


def _record_certification_case(item, report):
    """PRD-035: every live certification case's verdict and record."""
    marker = item.get_closest_marker("certification_case")
    if marker is None:
        return
    phases = item.stash.setdefault(_CERTIFICATION_PHASES, {})
    phases[report.when] = report
    if report.when == "teardown":
        from _chaos_report import item_verdict

        record = next((value for key, value in item.user_properties if key == "model_certification_case"), None)
        item.config.stash[_CERTIFICATION_CASES].append({
            "case_id": marker.args[0], "verdict": item_verdict(phases), "record": record or {},
        })


_CERTIFICATION_PHASES = pytest.StashKey[dict]()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    _audit_skip(item, report)  # first: a certification failure is the verdict the chaos report records
    _record_chaos_phase(item, report)
    _record_certification_case(item, report)


@pytest.fixture
def chaos_case(request, tmp_path):
    """PRD-032: the invariant checker of one @chaos scenario (tests/_chaos_harness.py)."""
    from _chaos_harness import close_case, open_case

    case = open_case(request, tmp_path)
    yield case
    close_case(request, case)


def pytest_sessionfinish(session):
    from _strict_doubles import release_default_roots

    release_default_roots()
    model_dir = session.config.getoption("--model-certification")
    if model_dir:
        from _model_certification import build_report as build_model_report
        from _model_certification import write_report as write_model_report

        write_model_report(model_dir, build_model_report(session.config.stash[_CERTIFICATION_CASES]))
    certification_dir = session.config.getoption("--certification-report")
    if certification_dir:
        from _certification import write_skips

        write_skips(certification_dir, session.config.stash[_SKIPS], bool(_certification_mode(session.config)))
    directory = session.config.getoption("--chaos-report")
    if not directory:
        return
    import platform
    import subprocess

    from _chaos_harness import SCENARIOS, SUPPORTING_SUITES
    from _chaos_report import RESULTS, build_report, write_report

    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=str(session.config.rootpath), check=False).stdout.strip() or None
    run = {"revision": revision, "python": platform.python_version(),
           "selection": session.config.getoption("-m") or "",
           "exit_status": int(session.exitstatus)}
    write_report(directory, build_report(session.config.stash[RESULTS], SCENARIOS, SUPPORTING_SUITES, run))

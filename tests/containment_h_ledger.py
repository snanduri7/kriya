"""S7 -> S8: the H replacement-control ledger (owner decision 2026-10-26: "for each legacy-host test/path marked for S8 deletion: identify its secure replacement control; prove host
execution is unreachable or refused; preserve useful semantic coverage through contained/typed behavior; only then mark it deletable in S8").

Every exact id classified ``('H', 'H')`` in tests/containment_census.py (the closed list S8 deletes) has exactly one entry here:
  premise           what the legacy test characterizes (a host mechanic of PROJECT-CONTROLLED execution that the packaged secure default makes unreachable);
  unreachable_by    node ids that prove project-controlled host execution is refused or unreachable for that surface (tests/test_s7_secure_default_contract.py and the
                    per-subject refusal proofs);
  semantic          node ids that keep the useful behavior covered through the contained / typed path (empty only with a NOT-APPLICABLE ``why`` that says why nothing
                    contained is left to cover);
  passes_unskipped  MEASURED 2026-10-26: the test also passes when its H skip is lifted. That is incidental (the stand-in is not an isolation proof, a weak assertion, an
                    in-process path) and says nothing about the premise; S8 still deletes it, with the replacement evidence below.
tests/test_s7_h_replacement_ledger.py enforces: the keys are exactly the H list, every referenced id resolves to a real test function that is not itself H, and no entry is
empty. MEASURED on 2026-10-26 with every H skip lifted: 41 of the 46 failed for the host premise (opt-out refusal, jdtls refusal, unprovable fake host JAVA_HOME, host Popen
mocking, host-path assertions); the 5 flagged ``passes_unskipped`` passed incidentally. Two further former H ids (the pom.xml-payload milestone tests) were CONVERTED, not deleted: their
payloads were malformed multi-root poms, a typed ToolchainResolutionError under the contained default, while the path-ownership semantics they test are mode independent.
"""
from __future__ import annotations

from typing import Dict, NamedTuple, Tuple

C = "tests/test_s7_secure_default_contract.py::"
VALIDATOR = C + "test_the_validator_refuses_before_it_spawns"
SHELL = C + "test_shell_tool_refuses_before_it_spawns"
JDTLS = C + "test_jdtls_is_never_started_on_the_host"
MANDATE = C + "test_a_forged_or_legacy_object_is_still_mandatory_for_every_subject"
OPT_OUT = C + "test_a_value_other_than_exactly_true_is_refused_at_construction"
NO_LEGACY = C + "test_the_owner_still_exposes_no_legacy_host_reason"
MCP = "tests/test_s5_mcp_containment.py::test_required_containment_with_the_null_backend_is_refused_typed_before_any_process_for_every_network_grant"
SERVICE = "tests/test_s4_1_managed_service_view.py::test_with_containment_mandatory_and_no_isolating_backend_the_run_is_refused_typed_before_any_process"
REPLAY = "tests/test_rdcg_item1b_replay_containment.py::test_a_run_that_requires_containment_never_replays_on_the_host"
SA_ADAPTER = "tests/test_prd031a_semgrep_adapter.py::"
SA_LIVE = "tests/test_prd031a_semgrep_live.py::"
SA_NO_HOST = SA_ADAPTER + "test_contained_scan_never_falls_back_to_the_host"


class Entry(NamedTuple):
    premise: str
    unreachable_by: Tuple[str, ...]
    semantic: Tuple[str, ...]
    why: str = ""
    passes_unskipped: bool = False


def _e(premise, unreachable, semantic=(), why="", passes_unskipped=False):
    return Entry(premise, tuple(unreachable), tuple(semantic), why, passes_unskipped)


PY_GATE = "tests/test_contained_python_test_gate_001.py::"
D2 = "tests/test_d2_authority_namespace.py::"
GATE = "tests/test_gate_orchestrator.py::"
PREFLIGHT = GATE + "test_the_toolchain_preflight_runs_once_and_its_override_reaches_every_attempts_validator"
IDENT = "tests/test_prd011_toolchain_identity.py::"
GOAL_JDK = IDENT + "test_goal_stated_jdk_assigned_after_construction_selects_the_contained_image"
HOST_JDK_REUSED = IDENT + "test_selected_host_jdk_version_is_reused_for_containment"
VENV_PYTEST = PY_GATE + "test_a_dependency_free_contained_project_gets_a_venv_with_pytest"
POLY = "tests/test_polymorphic_validation.py::"
TOOLCHAIN_FP = "tests/test_prd011_toolchain_fingerprint.py::"
S4RT = "tests/test_s4_runtime_toolchain.py::"
OCI = "tests/test_containment_oci.py::"
SEC005 = "tests/test_sec005_shell_acquisition_network.py::"
JDTLS_MIRROR = "tests/test_jdtls_candidate_isolation.py::"  # the H test ids' historical file (deleted with the mirror machinery); only the keys below use it
JDTLS_NO_MACHINERY = "tests/test_s8_host_branches_are_gone.py::test_jdtls_has_no_process_machinery_and_cannot_be_started"
LSP_NA = ("jdtls has no contained mode and is refused while containment is mandatory (CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT): its start / launch / data-dir lifecycle and, with S8-ORPHAN-HOST-CODE-001, "
          "the whole process machinery behind it (framing, reader loop, requests, mirror, tree-kill shutdown) is deleted host code; only the PATH lookup and the diagnostics formatter remain, "
          "with their own tests in tests/test_lsp.py, and the absence of the machinery is pinned by " + JDTLS_NO_MACHINERY)

# S8-ORPHAN-HOST-CODE-001: a whole test file may be deleted when ALL of its tests exercised only machinery that was itself deleted as unreachable (never because it failed). Each such
# file is named here with why; the guards require it to be ABSENT, so it cannot silently come back, and every LEDGER id in it is a historical record of an H test S8 had already removed.
FILES_DELETED_WITH_DEAD_CODE: Dict[str, str] = {
    "tests/test_jdtls_candidate_isolation.py": "its tests exercised the jdtls private-mirror machinery, deleted as unreachable (jdtls can never start while containment is mandatory)",
}

LEDGER: Dict[str, Entry] = {
    PY_GATE + "test_host_mode_dependency_free_project_still_uses_kriya_interpreter_without_any_command": _e(
        "a dependency-free project is judged by Kriya's own interpreter with no command", [VALIDATOR, OPT_OUT], [VENV_PYTEST]),
    PY_GATE + "test_host_mode_roots_are_realpath_equal_to_the_former_absolute_roots": _e(
        "host pytest roots equal the former absolute roots", [VALIDATOR, OPT_OUT],
        [PY_GATE + "test_contained_pytest_bootstrap_carries_no_host_path_and_resolves_roots_in_the_child"]),
    PY_GATE + "test_host_mode_src_layout_imports_through_the_in_child_resolution": _e(
        "a real host pytest child resolves a src layout", [VALIDATOR, OPT_OUT],
        [PY_GATE + "test_contained_pytest_bootstrap_carries_no_host_path_and_resolves_roots_in_the_child"]),
    D2 + "test_the_default_validator_builds_no_profile_and_creates_nothing": _e(
        "the default validator builds no containment profile", [VALIDATOR, MANDATE],
        [D2 + "test_the_null_backend_creates_nothing_in_the_workspace", D2 + "test_a_writable_workspace_gets_the_anchor_first_then_a_read_only_mask_over_each_leaf"]),
    D2 + "test_the_default_shell_tool_creates_no_kriya_directory_in_its_cwd": _e(
        "the default ShellTool runs on the host and leaves its cwd untouched", [SHELL],
        [D2 + "test_real_oci_the_shell_tool_the_first_attempt_missed_is_protected_end_to_end", D2 + "test_the_null_backend_creates_nothing_in_the_workspace"]),
    GATE + "test_engine_jvm_toolchain_preflight[deferred]": _e(
        "the JVM-toolchain preflight against a host JAVA_HOME", [VALIDATOR, MANDATE], [PREFLIGHT, HOST_JDK_REUSED]),
    GATE + "test_engine_jvm_toolchain_preflight[immediate]": _e(
        "the JVM-toolchain preflight against a host JAVA_HOME", [VALIDATOR, MANDATE], [PREFLIGHT, HOST_JDK_REUSED]),
    "tests/test_gradle_wrapper_containment_001.py::test_06_host_mode_is_a_byte_exact_pass_through": _e(
        "host Gradle runs the wrapper byte for byte", [VALIDATOR, OPT_OUT],
        ["tests/test_gradle_wrapper_containment_001.py::test_07_contained_offline_success_never_acquires_and_mounts_the_kriya_managed_home"]),
    JDTLS_MIRROR + "test_jdtls_is_rooted_at_the_private_mirror_never_the_candidate": _e(
        "a host jdtls process rooted at the private mirror", [JDTLS],
        [JDTLS_NO_MACHINERY]),
    JDTLS_MIRROR + "test_the_real_jdtls_analyses_without_any_candidate_write": _e(
        "the real host jdtls analyses a candidate without writing it", [JDTLS],
        [JDTLS_NO_MACHINERY]),
    "tests/test_linux_jvm_resource_strategy.py::test_the_validator_records_the_strategy_with_the_gate_evidence": _e(
        "the host validator reports its JVM resource strategy with the gate evidence", [VALIDATOR],
        [OCI + "test_memory_limit_is_enforced", "tests/test_linux_jvm_resource_strategy.py::test_an_unenforceable_budget_is_refused_never_run_unbounded"]),
    "tests/test_linux_oci_venv_interpreter.py::test_a_host_venv_with_a_dangling_interpreter_is_still_a_failed_venv": _e(
        "a host venv whose interpreter does not exist is a failed venv", [VALIDATOR, OPT_OUT],
        ["tests/test_linux_oci_venv_interpreter.py::test_a_contained_venv_linking_to_the_container_interpreter_is_used"]),
    "tests/test_lsp.py::test_initialize_timeout_releases_data_dir_process_and_reader": _e("host jdtls initialize timeout releases its resources", [JDTLS], why="NOT-APPLICABLE: " + LSP_NA),
    "tests/test_lsp.py::test_launch_failure_releases_the_data_dir": _e("host jdtls launch failure releases its data dir", [JDTLS], why="NOT-APPLICABLE: " + LSP_NA),
    "tests/test_lsp.py::test_start_and_check_file_end_to_end_via_fake_process": _e("host jdtls start and check_file round trip", [JDTLS], why="NOT-APPLICABLE: " + LSP_NA),
    "tests/test_lsp.py::test_start_does_not_inherit_java_home_from_parent_process": _e("host jdtls does not inherit the parent JAVA_HOME", [JDTLS], why="NOT-APPLICABLE: " + LSP_NA),
    "tests/test_lsp.py::test_successful_start_keeps_the_data_dir_until_shutdown": _e("host jdtls keeps its data dir until shutdown", [JDTLS], why="NOT-APPLICABLE: " + LSP_NA),
    "tests/test_oi142_gate_view.py::test_a_changed_venv_is_recreated_not_reused": _e(
        "a HOST venv is sealed and a changed one is recreated (venv_integrity)", [VALIDATOR, OPT_OUT],
        ["tests/test_oi142_gate_view_oci.py::test_under_oci_a_gate_cannot_modify_the_project_venv",
         "tests/test_s3_execution_views.py::test_the_origin_venv_is_mounted_into_the_view_read_only_and_writable_only_when_asked"],
        why="the contained venv is mounted READ-ONLY into every gate (writable only for creation and install), so a changed venv cannot exist; the host seal is the weaker control"),
    POLY + "test_java_home_override_also_applies_under_sandbox_execution": _e(
        "a goal-stated JDK is forced onto host subprocesses via JAVA_HOME", [VALIDATOR, OPT_OUT], [GOAL_JDK]),
    POLY + "test_java_home_override_forces_subprocess_to_the_specified_jdk": _e(
        "a goal-stated JDK is forced onto host subprocesses via JAVA_HOME", [VALIDATOR, OPT_OUT], [GOAL_JDK]),
    POLY + "test_python_run_tests_skips_venv_for_comments_only_requirements_txt": _e(
        "no venv for a comments-only requirements.txt (host sys.executable)", [VALIDATOR, OPT_OUT],
        [POLY + "test_python_run_tests_without_requirements_creates_the_project_environment_with_pytest_only", VENV_PYTEST]),
    POLY + "test_python_run_tests_skips_venv_without_requirements_txt": _e(
        "no venv without a requirements.txt (host sys.executable)", [VALIDATOR, OPT_OUT],
        [POLY + "test_python_run_tests_without_requirements_creates_the_project_environment_with_pytest_only", VENV_PYTEST]),
    POLY + "test_resolve_maven_classpath_writes_and_reads_the_output_file": _e(
        "the host Maven classpath resolution through a mocked Popen", [VALIDATOR, OPT_OUT],
        [POLY + "test_resolve_maven_classpath_runs_nothing_and_degrades_to_none_under_the_mandatory_containment"]),
    POLY + "test_run_app_sequence_without_requirements_txt_uses_sys_executable": _e(
        "a runtime sequence without requirements uses the host interpreter", [VALIDATOR, OPT_OUT],
        [POLY + "test_run_app_sequence_uses_isolated_venv_interpreter_for_python"]),
    POLY + "test_sandbox_execution_disabled_uses_full_env": _e(
        "sandbox_execution off gives a host subprocess the full environment", [VALIDATOR, OPT_OUT],
        [OCI + "test_host_only_env_vars_never_leak_even_when_allowlisted", OCI + "test_allowlisted_env_var_does_reach_the_container"]),
    POLY + "test_sandbox_execution_restricts_subprocess_env_by_default": _e(
        "a host subprocess gets the restricted environment", [VALIDATOR, OPT_OUT],
        [OCI + "test_host_only_env_vars_never_leak_even_when_allowlisted", OCI + "test_allowlisted_env_var_does_reach_the_container"]),
    "tests/test_prd011_toolchain_evidence.py::test_host_mode_gate_outcomes_never_claim_a_toolchain_identity": _e(
        "host-mode gate outcomes carry no toolchain identity", [VALIDATOR, MANDATE],
        ["tests/test_prd011_toolchain_evidence.py::test_contained_compile_gate_persists_image_digest_in_the_trace"],
        why="passes incidentally: the Python compile gate runs in-process and carries no identity whatever the mode", passes_unskipped=True),
    TOOLCHAIN_FP + "test_direct_resume_in_host_mode_stays_unverified": _e(
        "a direct resume in host mode leaves the toolchain unverified", [VALIDATOR, MANDATE],
        [TOOLCHAIN_FP + "test_direct_resume_detects_a_toolchain_image_change", TOOLCHAIN_FP + "test_missing_image_or_unresolvable_requirement_is_unavailable"],
        why="passes incidentally: under the image-absent seam the contained fingerprint is also UNVERIFIED", passes_unskipped=True),
    TOOLCHAIN_FP + "test_host_mode_toolchain_stays_unavailable": _e(
        "host mode has no toolchain fingerprint (containment not required)", [VALIDATOR, MANDATE],
        [TOOLCHAIN_FP + "test_contained_toolchain_binds_profile_and_image_digest", TOOLCHAIN_FP + "test_missing_image_or_unresolvable_requirement_is_unavailable"]),
    IDENT + "test_uncontained_validator_never_resolves_a_toolchain_on_assignment": _e(
        "an uncontained validator never resolves a toolchain when a JDK is assigned", [VALIDATOR, MANDATE], [GOAL_JDK]),
    "tests/test_rdcg_item1b_replay_containment.py::test_an_uncontained_run_still_replays_the_command_on_the_host_control": _e(
        "an uncontained run replays the persisted command on the host", [REPLAY, OPT_OUT],
        ["tests/test_rdcg_item1b_replay_containment.py::test_the_milestone_driver_passes_the_engines_autonomy_into_the_replay"]),
    "tests/test_rdcg_item1b_replay_containment.py::test_the_milestone_driver_with_an_uncontained_config_replays_normally": _e(
        "the milestone driver with an uncontained config replays normally", [REPLAY, OPT_OUT],
        ["tests/test_rdcg_item1b_replay_containment.py::test_the_milestone_driver_passes_the_engines_autonomy_into_the_replay"]),
    "tests/test_runtime_verification.py::test_direct_runtime_case[d_toolchain_already_checked]": _e(
        "the direct runtime case against a host JVM toolchain preflight", [VALIDATOR, MANDATE], [PREFLIGHT]),
    "tests/test_runtime_verification.py::test_direct_runtime_case[d_toolchain_java_mismatch]": _e(
        "the direct runtime case against a host JVM toolchain mismatch", [VALIDATOR, MANDATE], [PREFLIGHT, HOST_JDK_REUSED]),
    "tests/test_runtime_verification.py::test_engine_jvm_finite_command_artifact_preparation": _e(
        "Kriya prepares the jar with a host mvn package before a finite java -jar command", [VALIDATOR, MANDATE],
        [S4RT + "test_contained_maven_build_runs_the_validators_own_maven_path_and_only_mvn", S4RT + "test_the_finite_command_preparation_always_takes_the_validators_contained_build"]),
    "tests/test_s3_execution_views.py::test_the_non_contained_validator_builds_no_profile_and_prepares_nothing": _e(
        "the non-contained validator builds no profile and prepares nothing", [VALIDATOR, MANDATE],
        ["tests/test_s3_execution_views.py::test_a_contained_gate_runs_in_the_view_and_only_the_view_gets_the_git_target"]),
    "tests/test_s4_1_managed_service_view.py::test_legacy_host_permitted_behavior_is_recorded_as_superseded_the_host_service_inherits_the_environment": _e(
        "the legacy HOST_EXECUTION_PERMITTED managed service inherits Kriya's whole environment", [SERVICE, NO_LEGACY, OPT_OUT],
        ["tests/test_s4_1_managed_service_view.py::test_with_containment_mandatory_the_service_is_launched_only_through_a_profile_and_a_backend",
         OCI + "test_host_only_env_vars_never_leak_even_when_allowlisted"],
        why="passes only because the S7 stand-in runs the service as a host process with Kriya's environment (it models no environment isolation, by contract); the marker's own docstring "
            "says it is deleted with the legacy state, not weakened", passes_unskipped=True),
    S4RT + "test_host_javac_keeps_its_absolute_paths_byte_for_byte": _e(
        "host javac keeps absolute paths", [VALIDATOR, OPT_OUT], [S4RT + "test_contained_javac_gets_workspace_relative_paths"]),
    "tests/test_s5_mcp_containment.py::test_legacy_host_execution_reports_containment_not_required_and_not_active": _e(
        "a legacy host MCP server reports containment not required and not active", [MCP, OPT_OUT],
        ["tests/test_s5_mcp_containment.py::test_a_contained_server_is_launched_through_the_prepared_prefix_and_only_then_reports_active"]),
    SEC005 + "test_maven_command_network_unaffected_when_containment_not_required": _e(
        "a default ShellTool maven command keeps its documented network (containment not required)", [SHELL, OPT_OUT],
        [SEC005 + "test_maven_command_gets_registry_scoped_network_when_contained"],
        why="passes vacuously: the assertion is only that an exit code exists", passes_unskipped=True),
    SEC005 + "test_ordinary_command_network_unaffected_when_containment_not_required": _e(
        "a default ShellTool ordinary command keeps unrestricted network (containment not required)", [SHELL, OPT_OUT],
        [SEC005 + "test_near_miss_command_does_not_acquire_registry_authority_when_contained"]),
    "tests/test_tool001_autonomous_tool_execution.py::test_scenario_g_shelltool_ordinary_command_unaffected_when_uncontained": _e(
        "the operator ShellTool runs an ordinary command uncontained", [SHELL, OPT_OUT],
        ["tests/test_tool001_autonomous_tool_execution.py::test_scenario_g_shelltool_maven_registry_scoped_when_contained",
         "tests/test_tool001_autonomous_tool_execution.py::test_scenario_g_planned_pip_is_refused_before_any_container"],
        why="passes under the stand-in, which runs the command as a host process; the planned-path refusal it also asserts is covered by the planned-pip test",
        passes_unskipped=True),
    "tests/test_validate_maven_acquisition.py::test_run_maven_cmd_host_mode_is_exact_passthrough": _e(
        "host Maven runs the exact goals once with no acquisition flags", [VALIDATOR, OPT_OUT],
        ["tests/test_validate_maven_acquisition.py::test_run_maven_cmd_offline_success_never_acquires"]),
    "tests/test_validate_oci.py::test_host_mode_python_validation_unchanged_uses_absolute_paths": _e(
        "host Python validation uses sys.executable and absolute venv paths", [VALIDATOR, OPT_OUT],
        ["tests/test_validate_oci.py::test_contained_python_validation_uses_container_interpreter_never_host_executable"]),
    "tests/test_ver004_pyproject_dependency_evidence.py::test_pyproject_with_no_dependencies_never_calls_ensure_project_venv": _e(
        "a pyproject without dependencies never creates a venv (host)", [VALIDATOR, OPT_OUT], [VENV_PYTEST]),
    "tests/test_workflow.py::test_workflow_applies_java_home_override_to_maven_subprocess": _e(
        "a goal-stated JDK reaches the host Maven subprocess", [VALIDATOR, OPT_OUT],
        [GOAL_JDK, "tests/test_workflow.py::test_resolve_java_home_override_resolves_when_goal_matches_java_side"]),
    # the static-analysis host provider: the pinned HOST semgrep scanning a candidate. Static analysis always runs contained in a digest-pinned image; the host provider branch is unreachable.
    "tests/test_prd032_chaos_static_analysis.py::test_a_nosemgrep_suppression_in_the_candidate_never_hides_a_finding": _e(
        "the pinned HOST semgrep scans a candidate carrying `// nosemgrep` and the suppressed finding still blocks (chaos E08)", [MANDATE, SA_NO_HOST],
        [SA_LIVE + "test_service_candidate_nosemgrep_still_blocks[contained]", SA_ADAPTER + "test_contained_scan_profile_and_argv",
         "tests/test_prd032_chaos_static_analysis.py::test_a_nosemgrep_suppression_in_the_candidate_never_hides_a_finding_contained"],
        why="the contained live tier (python -m pytest -m live_static_analysis tests/test_prd031a_semgrep_live.py, real Docker and the pinned image) runs the same suppression scenario "
            "contained; MEASURED 2026-10-26: it passes"),
    SA_LIVE + "test_service_introduced_finding_at_exit_zero_blocks[host]": _e(
        "the static-analysis service scenario run on the HOST semgrep provider (the [host] parametrization of the live tier)", [MANDATE, SA_NO_HOST],
        [SA_LIVE + "test_service_introduced_finding_at_exit_zero_blocks[contained]"],
        why="the same scenario in the same file runs [contained] in the real OCI backend with the pinned image"),
    SA_LIVE + "test_service_candidate_nosemgrep_still_blocks[host]": _e(
        "the static-analysis service scenario run on the HOST semgrep provider (the [host] parametrization of the live tier)", [MANDATE, SA_NO_HOST],
        [SA_LIVE + "test_service_candidate_nosemgrep_still_blocks[contained]"],
        why="the same scenario in the same file runs [contained] in the real OCI backend with the pinned image"),
    SA_LIVE + "test_service_preexisting_finding_survives_a_line_shift[host]": _e(
        "the static-analysis service scenario run on the HOST semgrep provider (the [host] parametrization of the live tier)", [MANDATE, SA_NO_HOST],
        [SA_LIVE + "test_service_preexisting_finding_survives_a_line_shift[contained]"],
        why="the same scenario in the same file runs [contained] in the real OCI backend with the pinned image"),
    SA_LIVE + "test_service_resolved_finding_passes_with_recorded_identity[host]": _e(
        "the static-analysis service scenario run on the HOST semgrep provider (the [host] parametrization of the live tier)", [MANDATE, SA_NO_HOST],
        [SA_LIVE + "test_service_resolved_finding_passes_with_recorded_identity[contained]"],
        why="the same scenario in the same file runs [contained] in the real OCI backend with the pinned image"),
    SA_LIVE + "test_service_added_is_post_only_and_deleted_is_pre_only[host]": _e(
        "the static-analysis service scenario run on the HOST semgrep provider (the [host] parametrization of the live tier)", [MANDATE, SA_NO_HOST],
        [SA_LIVE + "test_service_added_is_post_only_and_deleted_is_pre_only[contained]"],
        why="the same scenario in the same file runs [contained] in the real OCI backend with the pinned image"),
    SA_LIVE + "test_service_oversized_target_is_never_submitted_and_unknown[host]": _e(
        "the static-analysis service scenario run on the HOST semgrep provider (the [host] parametrization of the live tier)", [MANDATE, SA_NO_HOST],
        [SA_LIVE + "test_service_oversized_target_is_never_submitted_and_unknown[contained]"],
        why="the same scenario in the same file runs [contained] in the real OCI backend with the pinned image"),
    SA_LIVE + "test_service_stale_evidence_blocks_the_commit[host]": _e(
        "the static-analysis service scenario run on the HOST semgrep provider (the [host] parametrization of the live tier)", [MANDATE, SA_NO_HOST],
        [SA_LIVE + "test_service_stale_evidence_blocks_the_commit[contained]"],
        why="the same scenario in the same file runs [contained] in the real OCI backend with the pinned image"),
}


# The tests still marked ``legacy_host_mcp`` that are NOT census H ids (they never consult the owner: a direct MCPClient without a backend, host RLIMIT_CPU / RLIMIT_AS). S8 deletes
# them with the host branch; tests/test_s5_mcp_containment.py pins the marked set, tests/test_s7_s8_list.py pins it to this ledger.
RESOURCES = "tests/test_s7_resource_limit_replacement.py::"
MARKED_LEDGER: Dict[str, Entry] = {
    "tests/test_sec004_mcp_lifecycle.py::test_cpu_over_limit_process_killed_by_os": _e(
        "a host MCP child is killed by RLIMIT_CPU (no backend)", [MCP, OPT_OUT],
        [RESOURCES + "test_a_cpu_budget_becomes_the_documented_rate_cap", RESOURCES + "test_an_mcp_servers_lifecycle_limits_reach_the_profile_that_carries_them"],
        why="the container's bound is a --cpus rate cap, not a cumulative CPU-seconds kill; both the mapping (denied-network and registry-scoped paths) and the MCP lifecycle pass-through are pinned"),
    "tests/test_sec004_mcp_lifecycle.py::test_memory_over_limit_outcome_platform_honest": _e(
        "a host MCP child's memory over-limit outcome (RLIMIT_AS, platform dependent)", [MCP, OPT_OUT],
        [RESOURCES + "test_a_memory_budget_becomes_a_hard_cgroup_limit_with_no_swap_headroom", OCI + "test_memory_limit_is_enforced"],
        why="the container's bound is a cgroup hard limit with no swap headroom, really enforced against a process (real OCI), not a platform-dependent address-space rlimit"),
    "tests/test_sec004_mcp_lifecycle.py::test_resource_setup_failure_fails_closed": _e(
        "a host MCP child whose rlimit preexec cannot be established is not spawned", [MCP, OPT_OUT],
        ["tests/test_process_controller_containment.py::test_run_resource_setup_failure_blocks_execution",
         "tests/test_process_controller_containment.py::test_run_async_resource_setup_failure_blocks_execution"]),
}

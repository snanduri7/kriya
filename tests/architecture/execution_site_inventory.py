"""The classified inventory of every external-process launch in production code (D2 EXEC-LAYER rebuild, slice S1).

Census date 2026-10-24 at 97111a0. ``test_execution_site_census`` fails when a launching function exists that is not here (UNCLASSIFIED),
when an entry here no longer exists, or when an entry is incompletely classified. Each entry answers the nine census questions:

1. module / function                    -> the key ``(file, qualified function)``
2. executable actually launched         -> ``executable``
3. can project / user / model-controlled code execute? -> ``project_code`` (``yes`` | ``no`` | ``config``: the project's own tool
   configuration, e.g. ``.mvn/jvm.config``, is honored)
4. cwd / workspace / root used          -> ``cwd``
5. authority state reachable today      -> ``authority_today`` (at the 97111a0 defaults: containment_backend "none", containment not required)
6. current containment owner            -> ``owner_today``
7. trust classification                 -> ``trust``
8. intended rebuild slice               -> ``slices`` (the first element acts next; S1..S6 or EXEMPT)
9. evidence for an exemption            -> ``evidence`` (a traced or measured fact, or a test that pins it; never an assumption)

``cls`` is SEAM (the process primitive itself), BACKEND (the OCI backend's own docker CLI use: it IS the containment mechanism),
INFRA (trusted Kriya or host infrastructure, no project code), or WORKLOAD (project / user / model-controlled code).
Git launches through ``git_argv`` are counted per file in ``GIT_LAUNCHES`` (their own seam test and evidence).
"""
from __future__ import annotations

from typing import Dict, NamedTuple, Tuple

SEAM, BACKEND, INFRA, WORKLOAD = "SEAM", "BACKEND", "INFRA", "WORKLOAD"
CLASSES = (SEAM, BACKEND, INFRA, WORKLOAD)
SLICES = ("S1", "S2", "S3", "S4", "S5", "S6", "EXEMPT")


class Site(NamedTuple):
    cls: str
    executable: str
    project_code: str
    cwd: str
    authority_today: str
    owner_today: str
    trust: str
    slices: Tuple[str, ...]
    evidence: str


HOST_ALL = "host process: everything the operator can touch (workspace .kriya/control and checkpoints, the authority home)"
OCI_NO_MASK = "contained: the whole workspace mount read-write, including .kriya/control and .kriya/checkpoints (no mask at 97111a0)"
NONE = "none (no workspace, no authority path in its argv)"

_SEAM = Site(SEAM, "the caller's argv", "n/a: the seam spawns what its caller classified", "the caller's cwd",
             "decided by the caller's profile (none -> host)", "ProcessController", "SEAM", ("EXEMPT",),
             "kriya/tools/process.py is the one spawn primitive (tests/test_d2_m2_spawn_tripwire.py forbids every other route); each of its callers is classified separately here")
_DOCKER_CLI = Site(BACKEND, "docker (CLI)", "no", "none", NONE, "OCIContainmentBackend", "TRUSTED_KRIYA_INFRASTRUCTURE", ("EXEMPT",),
                   "argv built from Kriya constants and the profile; this is the containment mechanism itself")
_DOCKER_FIXED_IMAGE = _DOCKER_CLI._replace(
    evidence="a fixed command in a Kriya-selected image with no workspace mount (pinned by test_docker_run_helpers_mount_no_workspace)")

INVENTORY: Dict[Tuple[str, str], Site] = {
    # --- the seam ---------------------------------------------------------------------------------------------------------------
    ("kriya/tools/process.py", "_spawn_popen"): _SEAM,
    ("kriya/tools/process.py", "spawn_subprocess_exec_fail_closed"): _SEAM,
    ("kriya/tools/process.py", "ProcessController.run"): _SEAM,
    ("kriya/tools/process.py", "ProcessController.run_async"): _SEAM,
    ("kriya/tools/process.py", "ProcessController.start_managed"): _SEAM,
    # --- the OCI backend's own docker CLI use -----------------------------------------------------------------------------------
    ("kriya/tools/containment_oci.py", "inspect_local_image"): _DOCKER_CLI,
    ("kriya/tools/containment_oci.py", "_inspect_once"): _DOCKER_CLI,
    ("kriya/tools/containment_oci.py", "_attest_toolchain_image"): _DOCKER_CLI,
    ("kriya/tools/containment_oci.py", "_observed_version"): _DOCKER_FIXED_IMAGE,
    ("kriya/tools/containment_oci.py", "_ensure_image"): _DOCKER_FIXED_IMAGE,
    ("kriya/tools/containment_oci.py", "_check_net_admin_available"): _DOCKER_FIXED_IMAGE,
    ("kriya/tools/containment_oci.py", "_container_ip"): _DOCKER_CLI,
    ("kriya/tools/containment_oci.py", "OCIContainmentBackend._probe_daemon"): _DOCKER_CLI,
    ("kriya/tools/containment_oci.py", "OCIContainmentBackend.prepare._cleanup"): _DOCKER_CLI,
    ("kriya/tools/containment_oci.py", "OCIContainmentBackend._prepare_registry_scoped"): _DOCKER_CLI,
    ("kriya/tools/containment_oci.py", "OCIContainmentBackend._prepare_registry_scoped._cleanup"): _DOCKER_CLI,
    ("kriya/tools/containment_oci.py", "OCIContainmentBackend._prepare_registry_scoped._remove_network"): _DOCKER_CLI,
    ("kriya/tools/containment_oci.py", "OCIContainmentBackend._prepare_registry_scoped._remove_proxy_and_log_denied"): _DOCKER_CLI,
    # --- trusted infrastructure and host probes (no project code) ---------------------------------------------------------------
    ("kriya/production_doctor.py", "_Docker.resolve"): Site(
        INFRA, "docker info", "no", "none", NONE, "production_doctor", "TRUSTED_KRIYA_INFRASTRUCTURE", ("EXEMPT",),
        "fixed argv [docker, info, --format, ...]; no workspace, no cwd argument"),
    ("kriya/production_doctor.py", "_kriya_containers"): Site(
        INFRA, "docker ps", "no", "none", NONE, "production_doctor", "TRUSTED_KRIYA_INFRASTRUCTURE", ("EXEMPT",),
        "fixed argv [docker, ps, -a, --filter, name=kriya-oci-, ...]; no workspace, no cwd argument"),
    ("kriya/platform/host_properties.py", "_run"): Site(
        INFRA, "sysctl / nvidia-smi", "no", "inherited process cwd (no project file is read by these tools)", NONE, "host_properties",
        "TRUSTED_HOST_INFRASTRUCTURE", ("EXEMPT",),
        "every caller passes a literal argv of a hardware query (sysctl -n hw.memsize, machdep.cpu.brand_string, nvidia-smi --query-gpu): read 2026-10-24"),
    ("kriya/platform/toolchain_locator.py", "jdk_home_for_version"): Site(
        INFRA, "/usr/libexec/java_home", "no", "inherited", NONE, "toolchain_locator", "TRUSTED_HOST_INFRASTRUCTURE", ("EXEMPT",),
        "absolute path of the macOS system binary, guarded by os.path.exists; it only prints a registered JDK home"),
    ("kriya/platform/toolchain_locator.py", "tool_visibility"): Site(
        INFRA, "/usr/libexec/java_home", "no", "inherited", NONE, "toolchain_locator", "TRUSTED_HOST_INFRASTRUCTURE", ("EXEMPT",),
        "absolute path of the macOS system binary; lookup only"),
    ("kriya/platform/toolchain_locator.py", "_first_runnable"): Site(
        INFRA, "python3 / git / node --version", "no", "inherited", NONE, "toolchain_locator", "TRUSTED_HOST_INFRASTRUCTURE", ("EXEMPT",),
        "_RUNNABLE_CHECK is the closed tuple (python3, git, node) and the code states mvn/gradle are only looked up, never run (pinned by test_runnable_check_never_runs_a_jvm_build_tool)"),
    ("kriya/core/release_identity.py", "_command"): Site(
        INFRA, "git (git_argv) in Kriya's own source root; docker version", "no", "Kriya's own source checkout (_SOURCE_ROOT), or none", NONE,
        "release_identity", "OPERATOR_MANAGED_INSTALLATION", ("EXEMPT",),
        "S19 P1b owner decision B: the Kriya source checkout is installation state, never a target workspace (tests/test_s19p1b_residuals.py INSTALLATION_STATE)"),
    ("kriya/workflow/milestone_completion.py", "workspace_evidence_hash"): Site(
        INFRA, "git (every step built by git_argv in the same function)", "no (content filters refused first)", "the workspace",
        "reads the workspace tree through a scratch index outside .kriya", "milestone_completion", "TRUSTED_KRIYA_INFRASTRUCTURE", ("EXEMPT",),
        "steps are git_argv(...) values and refuse_repository_content_filters runs before the index update (S19 P1c); counted by the git seam test"),
    ("kriya/workflow/named_test_oracle.py", "export_base"): Site(
        INFRA, "tar -x of a git-archive stream", "no", "a Kriya-owned directory outside the workspace", "none (extracts into a Kriya-owned directory)",
        "named_test_oracle", "TRUSTED_KRIYA_INFRASTRUCTURE", ("EXEMPT",),
        "tar only extracts data; git archive is a git_argv launch and content filters are refused first (S19 P1d); no project code runs"),
    # --- workloads: project / user / model-controlled code ----------------------------------------------------------------------
    ("kriya/tools/validate.py", "PolymorphicValidator._run_cmd_with_timeout"): Site(
        WORKLOAD, "the compile / test / runtime / Maven / pip command of the gate", "yes", "the candidate workspace (or a gate view of it)",
        HOST_ALL + "; contained: " + OCI_NO_MASK, "PolymorphicValidator.build_containment_profile_and_backend", "UNTRUSTED_EXECUTION",
        ("S1", "S2", "S3", "S4"), "S1: the OCI backend creates and binds the authority namespace of the mounted workspace; the policy, view and toolchain concerns are later slices"),
    ("plugins/core_tools/__init__.py", "ShellTool._run"): Site(
        WORKLOAD, "/bin/sh -c <model- or operator-supplied command>", "yes", "os.getcwd()", HOST_ALL + "; contained: " + OCI_NO_MASK,
        "ShellTool._run (profile only when sandboxing, required containment or shell_network denied)", "UNTRUSTED_EXECUTION", ("S1", "S2"),
        "the writable-workspace consumer the earlier attempt missed (R5): the OCI backend now creates the namespace for any writable mount, so no caller can omit it"),
    ("kriya/production_doctor.py", "probe_oci_containment"): Site(
        INFRA, "/bin/sh -c <fixed smoke script> in the containment image", "no", "a Kriya-owned scratch directory", "the scratch directory only",
        "probe_oci_containment", "TRUSTED_KRIYA_INFRASTRUCTURE", ("S1",),
        "the script is the module constant _SMOKE_SCRIPT and the workspace is mkdtemp scratch; the backend creates the scratch namespace like any writable mount"),
    ("kriya/tools/service_runtime.py", "_prepare_required_artifact"): Site(
        WORKLOAD, "mvn package (finite-command artifact preparation); never the project's ./mvnw when contained", "yes",
        "the validator workspace (the gate view when open)",
        "host: " + HOST_ALL + " (env/preexec_fn of the validator's host policy); contained: the validator's contained Maven path, no host process",
        "its callers: _prepare_finite_command_runtime_artifacts (the validator's external_execution decision) and run_managed_service_verification "
        "(the profile its caller built from the same decision, so the build is contained whenever the service is)", "UNTRUSTED_EXECUTION", ("S4",),
        "S4: the finite-command caller ran mvn / the project's ./mvnw on the HOST even under mandatory containment (MEASURED 2026-10-25: a project ./mvnw executed on the host); now a contained "
        "caller passes contained_build (PolymorphicValidator.contained_maven_build, a gate), the wrapper is never run, and the host path is unchanged. The managed-service caller was already contained (profile)"),
    ("kriya/tools/service_runtime.py", "run_managed_service_verification"): Site(
        WORKLOAD, "the managed service command (model-chosen), and its artifact build", "yes", "the exact-bound view of the candidate (S4.1)",
        "host (legacy HOST_EXECUTION_PERMITTED only): " + HOST_ALL + " (spec.env, no resource plan); contained: " + OCI_NO_MASK,
        "its one production caller _execute_managed_service_verification (validator.build_containment_profile_and_backend)", "UNTRUSTED_EXECUTION", ("S4",),
        "S4.1: the whole lifecycle (build, launch, readiness, probes, teardown, output collection) runs in ONE exact-bound view opened by its caller "
        "_execute_managed_service_verification; the profile is the validator's (None only when the canonical decision is not contained) and a contained build takes "
        "contained_maven_build (registry-scoped acquisition). Host-mode environment inheritance is the legacy HOST_EXECUTION_PERMITTED state, unreachable under any mandatory policy "
        "(tests/test_s4_1_managed_service_view.py)"),
    ("kriya/tools/service_runtime.py", "_check_readiness_via_exec"): Site(
        BACKEND, "docker exec -i <kriya-oci container> bash -c <fixed readiness script>", "no", "none (the docker CLI; the script runs in the container)", NONE,
        "OCIContainmentBackend (PreparedContainment.exec_target)", "TRUSTED_KRIYA_INFRASTRUCTURE", ("EXEMPT",),
        "the argv prefix is the exec_target only OCIContainmentBackend.prepare produces (pinned by tests/test_s4_runtime_toolchain.py); the function asserts it is set; "
        "the script is Kriya's constant text over a loopback-validated host and port; it runs in the container the service already runs in"),
    ("kriya/tools/service_runtime.py", "_run_probe_via_exec"): Site(
        BACKEND, "docker exec -i <kriya-oci container> bash -c <fixed TCP probe script>", "no", "none (the docker CLI; the script runs in the container)", NONE,
        "OCIContainmentBackend (PreparedContainment.exec_target)", "TRUSTED_KRIYA_INFRASTRUCTURE", ("EXEMPT",),
        "the same exec_target-only origin as _check_readiness_via_exec (pinned); the HTTP request text is data written to the socket inside the container"),
    ("kriya/tools/validate.py", "check_java_toolchain"): Site(
        INFRA, "java -version; mvn -version", "no (Kriya-owned empty cwd: no .mvn/jvm.config or maven.config is reachable)",
        "a Kriya-created EMPTY temporary directory", "the operator's own environment (MAVEN_OPTS, JAVA_TOOL_OPTIONS, PATH): host infrastructure, no workspace input",
        "check_java_toolchain", "TRUSTED_HOST_INFRASTRUCTURE", ("S4",),
        "MEASURED 2026-10-24/25: mvn -version in a directory with .mvn/jvm.config applies the project's JVM options (a project -javaagent ran as host code); S4 runs both probes "
        "in an empty directory (tests/test_s4_runtime_toolchain.py: canary agent absent, premise control present)"),
    ("kriya/tools/lsp.py", "JdtlsClient._start"): Site(
        WORKLOAD, "jdtls (project import runs Maven / Gradle)", "yes", "a mirror directory that skips .kriya", HOST_ALL, "none (host spawn)",
        "UNTRUSTED_EXECUTION", ("S2",), "a host spawn that imports the project build: a policy decision (S2)"),
    ("kriya/static_analysis/adapters/semgrep.py", "SemgrepAdapter._run_probe"): Site(
        WORKLOAD, "semgrep --version (host) or in the image", "no (--version)", "a Kriya-owned scratch directory", "the scratch directory only",
        "SemgrepAdapter._containment_ready", "UNTRUSTED_EXECUTION", ("S2",), "the adapter's own containment gate becomes the canonical policy in S2"),
    ("kriya/static_analysis/adapters/semgrep.py", "SemgrepAdapter.scan"): Site(
        WORKLOAD, "semgrep over the workspace", "config", "the workspace root (read-only mount when contained)",
        "contained: the workspace read-only (readable, not writable); host mode: " + HOST_ALL, "SemgrepAdapter._containment_ready",
        "UNTRUSTED_EXECUTION", ("S2",), "the adapter's own containment gate becomes the canonical policy in S2"),
    ("kriya/tools/dependency_execution.py", "maven_acquire_dependencies"): Site(
        WORKLOAD, "mvn dependency acquisition", "yes", "the caller's workspace", "contained, workspace writable", "its own profile builders",
        "UNTRUSTED_EXECUTION", ("S2",), "NO PRODUCTION CALLER (pinned by test_dependency_execution_has_no_production_caller): classified in S2 before any caller exists"),
    ("kriya/tools/dependency_execution.py", "maven_execute_offline"): Site(
        WORKLOAD, "mvn -o", "yes", "the caller's workspace", "contained, workspace writable", "its own profile builders", "UNTRUSTED_EXECUTION",
        ("S2",), "NO PRODUCTION CALLER (pinned by test_dependency_execution_has_no_production_caller)"),
    ("kriya/tools/dependency_execution.py", "python_acquire_dependencies"): Site(
        WORKLOAD, "python3 -m pip download", "yes", "the caller's workspace", "contained, workspace writable", "its own profile builders",
        "UNTRUSTED_EXECUTION", ("S2",), "NO PRODUCTION CALLER (pinned by test_dependency_execution_has_no_production_caller)"),
    ("kriya/tools/dependency_execution.py", "python_execute_offline"): Site(
        WORKLOAD, "python3 -m pip install --no-index", "yes", "the caller's workspace", "contained, workspace writable", "its own profile builders",
        "UNTRUSTED_EXECUTION", ("S2",), "NO PRODUCTION CALLER (pinned by test_dependency_execution_has_no_production_caller)"),
    ("kriya/mcp/lifecycle.py", "spawn_mcp_process"): Site(
        WORKLOAD, "the configured MCP server command (host: legacy HOST_EXECUTION_PERMITTED only; contained: THROUGH the prepared backend prefix)", "yes",
        "host: the process cwd (project config visible, MEASURED 2026-10-25); contained: the container's workdir (no workspace unless granted)",
        "host (legacy): the SEC-003 restricted env, cwd and rlimits; contained: " + OCI_NO_MASK + " only when workspace_write is granted (S1 leaf masks apply)",
        "MCPManager.start_all -> decide_external_execution(subject=mcp_subject(name)); MCPClient._start_sequence -> require_isolating_backend", "UNTRUSTED_EXECUTION",
        ("S5",),
        "S5: MEASURED fail-open before: required + backend none + an unrestricted grant ran the server on the HOST with Kriya's full env and containment_active=True. Now the "
        "owner refuses (typed, before any process) a non-isolating, unknown or unavailable backend, a prepared containment with no launch prefix is refused, and "
        "containment_active means launched THROUGH the prefix. Host cwd/env hardening is deliberately NOT added: legacy state, unreachable after the migration"),
}

# Git launches through git_argv(...), counted per file (their evidence is tests/architecture/test_git_invocation_seam.py and the S19 tests).
GIT_LAUNCHES: Dict[str, int] = {
    "kriya/analyzer/analyzer.py": 5, "kriya/cli.py": 1, "kriya/code_intel/service.py": 1, "kriya/control/run_coordinator.py": 1,
    "kriya/distribution.py": 1, "kriya/production_doctor.py": 2, "kriya/repository/git.py": 3, "kriya/repository/snapshot.py": 4,
    "kriya/skills/skill.py": 3, "kriya/static_analysis/operator_scan.py": 1, "kriya/tools/git_metadata.py": 2,
    "kriya/workflow/checkpoint.py": 8, "kriya/workflow/named_test_oracle.py": 3, "kriya/workflow/worktree.py": 19,
    "plugins/core_tools/__init__.py": 3,
}

# Every ContainmentProfile(...) construction in production: what the profile mounts. S1: whether a profile reaches the namespace creation is NOT a
# per-builder question (that is how ShellTool was missed in the first attempt): the OCI backend creates the authority namespace itself, in the
# two functions that bind a workspace, for any writable workspace mount (test_every_oci_workspace_bind_creates_the_authority_namespace_first).
WRITABLE, READ_ONLY, SCRATCH, NO_LAUNCH, TRUSTED_NULL, CAPABILITY, UNREFERENCED = (
    "WRITABLE_WORKSPACE", "READ_ONLY_WORKSPACE", "SCRATCH_WRITABLE", "NO_LAUNCH", "TRUSTED_NULL_BACKEND", "CAPABILITY_DEPENDENT", "UNREFERENCED")
PROFILE_BUILDERS: Dict[Tuple[str, str], str] = {
    ("kriya/tools/validate.py", "PolymorphicValidator.build_containment_profile_and_backend"): WRITABLE,
    ("plugins/core_tools/__init__.py", "ShellTool._run"): WRITABLE,
    ("kriya/production_doctor.py", "probe_oci_containment"): SCRATCH,
    ("kriya/mcp/containment_adapter.py", "map_capability_profile_to_containment"): CAPABILITY,
    ("kriya/static_analysis/adapters/semgrep.py", "SemgrepAdapter._profile"): READ_ONLY,
    ("kriya/production_doctor.py", "_check_no_host_fallback"): NO_LAUNCH,
    ("kriya/tools/dependency_execution.py", "_acquisition_profile"): UNREFERENCED,
    ("kriya/tools/dependency_execution.py", "_execution_profile"): UNREFERENCED,
    ("plugins/core_tools/__init__.py", "GitTool._tracked_paths"): TRUSTED_NULL,
    ("plugins/core_tools/__init__.py", "GitTool._require_own_repository_view"): TRUSTED_NULL,
    ("plugins/core_tools/__init__.py", "GitTool._run_in"): TRUSTED_NULL,
}

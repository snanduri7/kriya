"""S8: the RECORD of what S8 did to the frozen S8 list (owner decision 2026-10-26; S8 executed 2026-10-27).

S8 scope was ONLY the unreachable PROJECT-CONTROLLED host-execution code and tests that remained after the secure-default migration. This module is the exact record, so that "dead host
branch" is a reviewed set and not a grep result:

  S8_CODE      the production branches that started (or decided to start) a PROJECT-CONTROLLED process on the host, or characterized doing so. Each was unreachable because the canonical
               owner (``kriya.tools.containment.decide_external_execution``) answers ``contained=True`` for every subject, whatever object it is handed
               (tests/test_s7_secure_default_contract.py::test_a_forged_or_legacy_object_is_still_mandatory_for_every_subject), and a mandatory decision with no isolating backend
               is a typed refusal before any process starts (the per-surface refusal tests named in tests/containment_h_ledger.py). ``outcome`` is DELETED (the host branch is gone) or
               HELD (a host-capable remainder stays, named in HELD_NOTES, for an owner decision - production never reaches it).
  RETAINED     sites that read the decision but are NOT project-controlled host execution: record / identity / report readers (their field names and values are an identity and
               change by design) and explicitly classified trusted Kriya infrastructure. S8 did not touch them, and they are now the ONLY readers of the flag.
  NEVER_S8     the trusted / hardened host infrastructure the owner excluded by name: git, the docker CLI, neutral-cwd toolchain probes, image inspection and attestation.
  ORPHANS      dead host code that is NOT on the frozen list (nothing reads the flag there; it is keyed on a parameter or is a pure helper): left untouched, listed for the owner.

tests/test_s7_s8_list.py walks kriya/ and plugins/ for reads of ``.contained`` / ``._contained``: after S8 the scan must equal RETAINED exactly, so a new host branch cannot appear.
The tests S8 deleted are the exact-id H list of tests/containment_census.py (54) PLUS the three ``legacy_host_mcp`` host-rlimit tests that never consulted the owner (the replacement
controls for both are recorded in tests/containment_h_ledger.py ``LEDGER`` and ``MARKED_LEDGER``, and those controls are still pinned to exist)."""
from __future__ import annotations

from typing import Dict, NamedTuple, Tuple


class Site(NamedTuple):
    kind: str          # "scan" (read the contained flag) or "extra" (named explicitly)
    why: str
    outcome: str = "DELETED"      # DELETED (the host branch is gone) or HELD (named in HELD_NOTES)


S8_CODE: Dict[str, Site] = {
    "kriya/capabilities/javac.py::JavacBuildAdapter.compile": Site("scan", "host javac compile branch (absolute host paths)"),
    "kriya/capabilities/pip.py::PipBuildAdapter.compile": Site("scan", "host pip install branch"),
    "kriya/mcp/mcp.py::MCPManager.start_all": Site("scan", "host MCP server launch when the per-server flag was false"),
    "kriya/static_analysis/adapters/semgrep.py::SemgrepAdapter._contained": Site("scan", "adapter API flag for the host provider"),
    "kriya/static_analysis/adapters/semgrep.py::SemgrepAdapter._effective_options_digest": Site("scan", "host-provider environment in the options digest"),
    "kriya/static_analysis/adapters/semgrep.py::SemgrepAdapter._probe_from_version": Site("scan", "host provider probe branch"),
    "kriya/static_analysis/adapters/semgrep.py::SemgrepAdapter._run_probe": Site("scan", "host provider probe execution"),
    "kriya/static_analysis/adapters/semgrep.py::SemgrepAdapter.runtime_fingerprint": Site("scan", "host provider runtime fingerprint"),
    "kriya/static_analysis/adapters/semgrep.py::SemgrepAdapter.scan": Site("scan", "host provider scan execution"),
    "kriya/static_analysis/service.py::execution_context": Site("scan", "ExecutionContext(contained=False) construction"),
    "kriya/tools/validate.py::PolymorphicValidator._ensure_project_venv": Site("scan", "host venv cache branch (integrity-sealed reuse)"),
    "kriya/tools/validate.py::PolymorphicValidator._ensure_project_venv_impl": Site("scan", "host venv creation, seal and recreate (venv_integrity)"),
    "kriya/tools/validate.py::PolymorphicValidator._resolve_python_interpreter": Site("scan", "host sys.executable / absolute venv interpreter branch"),
    "kriya/tools/validate.py::PolymorphicValidator._resolve_toolchain_identity": Site("scan", "host branch that resolves no toolchain identity"),
    "kriya/tools/validate.py::PolymorphicValidator._run_gradle_cmd": Site("scan", "host Gradle byte-for-byte pass-through"),
    "kriya/tools/validate.py::PolymorphicValidator._run_maven_cmd": Site("scan", "host Maven exact pass-through"),
    "kriya/tools/validate.py::PolymorphicValidator.build_containment_profile_and_backend": Site("scan", "the (None, None) host return"),
    "kriya/tools/validate.py::PolymorphicValidator.resolve_maven_classpath": Site("scan", "host Maven classpath resolution"),
    "kriya/workflow/authority_bundle.py::run_authority_bundle": Site("scan", "the not-contained refusal branch (dead once the decision is unconditional)"),
    "kriya/workflow/runtime_verification.py::_prepare_finite_command_runtime_artifacts": Site("scan", "host `mvn package` artifact preparation"),
    "plugins/core_tools/__init__.py::ShellTool._run": Site("scan", "host sh execution of the operator shell command"),
    "kriya/tools/lsp.py::JdtlsClient.start": Site("extra", "starting jdtls on the host (it has no contained mode and is refused while containment is mandatory)"),
    "kriya/tools/service_runtime.py::run_managed_service_verification": Site("extra", "host managed-service launch, the host build with env/preexec_fn and the project-wrapper build (`allow_project_wrapper`) - deleted in containment qualification, S8-HELD-SERVICE-RUNTIME-HOST-HARNESS-001"),
    "kriya/tools/venv_integrity.py::seal": Site("extra", "host venv seal (the contained venv is mounted read-only into every gate instead)"),
}

RETAINED: Dict[str, str] = {
    "kriya/production_doctor.py::_check_no_host_fallback": "doctor report of the mandate; reads the decision to REPORT it (record-only)",
    "kriya/static_analysis/service.py::settings_digest": "digest identity: the field names and values are the digest's identity (unchanged by design)",
    "kriya/workflow/baseline_policy.py::baseline_environment_identity": "baseline identity field ('contained' / 'host'): a record; old baselines go STALE by design",
    "kriya/workflow/egress_authority.py::describe_run_egress": "egress report: a record of what the run was allowed",
    "kriya/workflow/resume_fingerprints.py::toolchain_fingerprint": "fingerprint of the contained toolchain image: identity, not execution",
}

# trusted / hardened host infrastructure the owner excluded by name; none of these may ever appear in S8_CODE
NEVER_S8 = (
    "GitTool", "check_java_toolchain", "inspect_local_image", "_confirm_absence_with_listing", "_inspect_once", "_attest_toolchain_image", "_observed_version", "_ensure_image",
    "probe_oci_containment", "_check_net_admin_available", "OCIContainmentBackend",
)


# What S8 did NOT delete: nothing is held any more. The one held site (the host-capable remainder of kriya/tools/service_runtime.py) was deleted in containment qualification
# (S8-HELD-SERVICE-RUNTIME-HOST-HARNESS-001) after its ~45 harness tests were migrated to the test-owned containment stand-in and the production contained_build seam.
HELD_NOTES: Dict[str, str] = {}

# Dead host code that is NOT on the frozen list (no read of the flag: it is keyed on a parameter, or it is a pure helper). Left in place; candidates for an owner-approved follow-up.
ORPHANS: Dict[str, str] = {}   # S8-ORPHAN-HOST-CODE-001 is closed: every orphan recorded after S8 was deleted (ORPHANS_DELETED), so none is left to list

# What the orphan removal deleted, with the negative-proof test that pins each absence (tests/test_s8_host_branches_are_gone.py). Reachability was TRACED per symbol first:
# host_executable_digest had no caller; build_subprocess_env_and_preexec/host_resource_plan fed only the (None, None)-profile tail of _run_cmd_with_timeout; MCPClient._start_sequence is the
# LIVE launcher and only its containment_required=False branch was the orphan; JdtlsClient.start() is always the typed refusal, so its process machinery could never run.
_PROOFS = "tests/test_s8_host_branches_are_gone.py::"
ORPHANS_DELETED: Dict[str, Tuple[str, str]] = {
    "kriya/static_analysis/adapters/semgrep.py::host_executable_digest": (
        "host Semgrep engine identity (no caller anywhere); its only helper _file_sha256 went with it", _PROOFS + "test_the_host_semgrep_engine_identity_helper_and_its_only_consumer_are_gone"),
    "kriya/tools/validate.py::PolymorphicValidator.build_subprocess_env_and_preexec": (
        "the host env/preexec policy, host_resource_plan and the host spawn tail of _run_cmd_with_timeout (a missing profile/backend is now a typed refusal)",
        _PROOFS + "test_the_validator_has_no_host_env_and_preexec_policy_and_its_one_spawn_is_contained"),
    "kriya/mcp/mcp.py::MCPClient._start_sequence": (
        "the containment_required=False raw host spawn branch (the method itself is the live launcher); the constructor refuses a client that does not require containment",
        _PROOFS + "test_the_mcp_launcher_has_no_host_spawn_branch_left"),
    "kriya/tools/lsp.py::JdtlsClient": (
        "the process machinery after the typed refusal in start(): framing, reader loop, requests, private mirror, tree-kill shutdown",
        _PROOFS + "test_jdtls_has_no_process_machinery_and_cannot_be_started"),
}


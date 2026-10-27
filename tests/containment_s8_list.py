"""S7 -> S8: the FROZEN S8 list (owner decision 2026-10-26).

S8 scope is ONLY the unreachable PROJECT-CONTROLLED host-execution code and tests that remain after the secure-default migration. This module is the exact list, so that "dead host
branch" is a reviewed set and not a grep result:

  S8_CODE      production branches that start (or decide to start) a PROJECT-CONTROLLED process on the host, or characterize doing so. Each is unreachable because the canonical owner
               (``kriya.tools.containment.decide_external_execution``) answers ``contained=True`` for every subject, whatever object it is handed
               (tests/test_s7_secure_default_contract.py::test_a_forged_or_legacy_object_is_still_mandatory_for_every_subject), and a mandatory decision with no isolating backend
               is a typed refusal before any process starts (the per-surface refusal tests named in tests/containment_h_ledger.py). Deleting them in S8 needs the replacement
               evidence recorded there; S7 deletes none of them.
  RETAINED     sites that read the decision but are NOT project-controlled host execution: record / identity / report readers (their field names and values are an identity and
               change by design) and explicitly classified trusted Kriya infrastructure. S8 does not touch them.
  NEVER_S8     the trusted / hardened host infrastructure the owner excluded by name: git, the docker CLI, neutral-cwd toolchain probes, image inspection and attestation.

``scan`` sites are found by tests/test_s7_s8_list.py walking kriya/ and plugins/ for reads of ``.contained`` / ``._contained``; the scan must equal S8_CODE (scan kind) plus RETAINED
exactly, so a new host branch cannot appear unlisted. ``extra`` sites do not read the flag (they sit behind ``refuse_unsupported`` or a ``None`` backend) and are named explicitly.
The tests are the exact-id H list of tests/containment_census.py PLUS the three ``legacy_host_mcp`` host-rlimit tests that never consult the owner (replacement controls for both:
tests/containment_h_ledger.py ``LEDGER`` and ``MARKED_LEDGER``)."""
from __future__ import annotations

from typing import Dict, NamedTuple


class Site(NamedTuple):
    kind: str          # "scan" (reads the contained flag) or "extra" (named explicitly)
    why: str


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
    "kriya/tools/service_runtime.py::run_managed_service_verification": Site("extra", "host managed-service launch and the project-wrapper build (`allow_project_wrapper=not contained`)"),
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

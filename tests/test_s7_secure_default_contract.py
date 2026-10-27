"""S7 (packaged-default migration, owner decision 2026-10-25): the secure-default contract and its negative controls.

There is NO operator opt-out for project-controlled external execution. This file is the replacement control for every legacy-host test the migration removes:
A. the packaged defaults are the contract (oci, both mandate fields true) and the production profile's values are the same;
B. an explicit ``false`` (and any value but true) for either mandate field is a typed refusal at EVERY ingress - construction, assignment, ``model_copy``, a re-validated
   instance, a YAML file through ``resolve_config_state`` / ``load_config`` - never coerced to true, never host execution; an explicit true is accepted;
C. the owner reads no configuration: a forged / ``model_construct``-ed / legacy object is still mandatory for every subject;
D. the backend ``none`` parses, and refuses typed BEFORE any process starts at each consumer class (validator, ShellTool, JDTLS); the MCP, managed-service, static-analysis,
   replay and authority-bundle consumers keep their own refusal proofs (tests/test_s5_*, test_s4_1_*, test_s6_*, test_s2_*), which now run under the same defaults;
E. the persisted-state boundary (D-7): the secure-default change is a NEW execution identity - nothing recorded under the old values reads as current.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from _plugin_test_support import load_core_tools_module

from kriya.config import AppConfig
from kriya.config.authority import CONTAINMENT_MANDATE_FIELDS, ContainmentOptOutError, parse_containment_mandate
from kriya.config.config import AutonomyConfig, load_config
from kriya.config.resolution import resolve_config_state
from kriya.control.checkpoint import compute_config_fingerprint
from kriya.core import release_identity as ri
from kriya.tools import containment
from kriya.tools.containment import (
    CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION,
    CONTAINMENT_BACKEND_UNKNOWN,
    CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT,
    CONTAINMENT_REQUIRED,
    BackendUnavailableError,
    ExternalExecutionRefused,
    decide_external_execution,
    mcp_subject,
)
from kriya.tools.lsp import JdtlsClient
from kriya.tools.tool import ToolExecutionError
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.baseline_policy import baseline_environment_identity
from kriya.workflow.resume_fingerprints import (
    ARTIFACT_DEPENDENCIES,
    FingerprintStatus,
    compare_resume_fingerprints,
    fingerprint_block,
    generation_resume_fingerprints,
)

ShellTool = load_core_tools_module().ShellTool
FIELDS = list(CONTAINMENT_MANDATE_FIELDS)
SUBJECTS = ["PolymorphicValidator", "ShellTool", "JdtlsClient", "static_analysis", "authority_bundle", "egress report", "toolchain fingerprint",
            "baseline environment identity", "release identity", mcp_subject("srv")]


# ------------------------------------------------------------------ A. the packaged defaults are the contract

def test_the_packaged_defaults_are_the_secure_contract():
    for autonomy in (AutonomyConfig(), AppConfig().autonomy):
        assert (autonomy.containment_backend, autonomy.contained_execution_required, autonomy.mcp_contained_execution_required) == ("oci", True, True)


def test_the_production_profile_is_not_stricter_than_the_defaults_for_these_fields_and_accepts_matching_values(tmp_path):
    config = tmp_path / "kriya.yaml"
    config.write_text("runtime_profile: production\nautonomy:\n  contained_execution_required: true\n  mcp_contained_execution_required: true\n  containment_backend: oci\n")
    state = resolve_config_state(str(config))                                       # a matching explicit value is accepted (the existing production precedent)
    assert state.config_dict["autonomy"]["contained_execution_required"] is True


# ------------------------------------------------------------------ B. no opt-out at any ingress

@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [False, None, 0, 1, "true", "false", "yes"])
def test_a_value_other_than_exactly_true_is_refused_at_construction(field, value):
    with pytest.raises(ValueError, match="cannot be disabled"):
        AutonomyConfig(**{field: value})


@pytest.mark.parametrize("field", FIELDS)
def test_parse_containment_mandate_accepts_exactly_true_and_refuses_everything_else(field):
    assert parse_containment_mandate(field, True) is True
    for bad in (False, None, 0, "true"):
        with pytest.raises(ContainmentOptOutError) as raised:
            parse_containment_mandate(field, bad)
        assert field in str(raised.value) and "no host-execution opt-out" in str(raised.value)


@pytest.mark.parametrize("field", FIELDS)
def test_assignment_refuses_and_never_coerces(field):
    autonomy = AutonomyConfig()
    with pytest.raises(ContainmentOptOutError):
        setattr(autonomy, field, False)
    assert getattr(autonomy, field) is True                                          # unchanged: not silently flipped, not silently kept-as-written
    setattr(autonomy, field, True)                                                  # an explicit true is accepted


@pytest.mark.parametrize("field", FIELDS)
def test_model_copy_refuses_the_opt_out_and_accepts_true(field):
    with pytest.raises(ContainmentOptOutError):
        AutonomyConfig().model_copy(update={field: False})
    assert getattr(AutonomyConfig().model_copy(update={field: True}), field) is True


@pytest.mark.parametrize("field", FIELDS)
def test_an_instance_built_without_validation_is_refused_when_it_is_handed_to_a_config(field):
    forged = AutonomyConfig.model_construct(**{field: False})
    with pytest.raises(ValueError, match="cannot be disabled"):
        AppConfig(autonomy=forged)


@pytest.mark.parametrize("field", FIELDS)
def test_a_yaml_file_with_an_explicit_false_is_refused_through_the_authority_resolution_and_load_config(tmp_path, field):
    config = tmp_path / "kriya.yaml"
    config.write_text(f"autonomy:\n  {field}: false\n")
    with pytest.raises(ContainmentOptOutError):
        resolve_config_state(str(config))                                           # `kriya authority inspect|approve` and the desktop go through here: nothing to approve
    with pytest.raises(ContainmentOptOutError):
        load_config(str(config))


@pytest.mark.parametrize("field", FIELDS)
def test_a_yaml_file_with_an_explicit_true_is_not_the_opt_out_refusal(tmp_path, field):
    """An explicit true passes the closed parser. Setting a SECURITY_AUTHORITY field from a file still needs the (value-agnostic, pre-existing) SEC-009 approval, which is a
    different layer: resolution succeeds and reports it as a field to approve, it is never the opt-out refusal."""
    config = tmp_path / "kriya.yaml"
    config.write_text(f"autonomy:\n  {field}: true\n")
    state = resolve_config_state(str(config))
    assert state.config_dict["autonomy"][field] is True
    assert any(field in str(violation) for violation in state.violations)


def test_the_production_profile_rejects_the_opt_out_too(tmp_path):
    config = tmp_path / "kriya.yaml"
    config.write_text("runtime_profile: production\nautonomy:\n  contained_execution_required: false\n")
    with pytest.raises(ValueError):                                                 # the existing production contradiction rule or the new one - either way a refusal
        resolve_config_state(str(config))


# ------------------------------------------------------------------ C. the owner reads no configuration

@pytest.mark.parametrize("subject", SUBJECTS)
@pytest.mark.parametrize("forged", [
    SimpleNamespace(contained_execution_required=False, mcp_contained_execution_required=False, containment_backend="oci"),
    SimpleNamespace(containment_backend="oci"),                                    # no flag attribute at all
    AutonomyConfig.model_construct(contained_execution_required=False, mcp_contained_execution_required=False, containment_backend="oci"),
    object(),
], ids=["flags-false", "no-flags", "model-construct", "bare-object"])
def test_a_forged_or_legacy_object_is_still_mandatory_for_every_subject(subject, forged):
    decision = decide_external_execution(forged, subject=subject)
    assert decision.contained is True and decision.reason_code == CONTAINMENT_REQUIRED


# ------------------------------------------------------------------ D. backend none / unknown refuse typed BEFORE any process starts

@pytest.mark.parametrize("backend, code", [("none", CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION), ("not-a-backend", CONTAINMENT_BACKEND_UNKNOWN)])
def test_the_validator_refuses_before_it_spawns(tmp_path, backend, code):
    sentinel = tmp_path / "spawned"
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(containment_backend=backend))
    with pytest.raises(ExternalExecutionRefused) as raised:
        validator._run_cmd_with_timeout([sys.executable, "-c", f"open({str(sentinel)!r}, 'w')"], cwd=str(tmp_path))      # pylint: disable=protected-access
    assert raised.value.reason_code == code
    assert not sentinel.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend, code", [("none", CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION), ("not-a-backend", CONTAINMENT_BACKEND_UNKNOWN)])
async def test_shell_tool_refuses_before_it_spawns(tmp_path, monkeypatch, backend, code):
    spawned = []

    async def run_async(self, command, **kwargs):      # pylint: disable=unused-argument
        spawned.append(command)
        raise AssertionError("a process was started")

    monkeypatch.setattr("kriya.tools.process.ProcessController.run_async", run_async)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ToolExecutionError) as raised:
        await ShellTool(autonomy_cfg=AutonomyConfig(containment_backend=backend)).execute(command="echo hi")
    cause = raised.value.__cause__
    if backend == "none":
        assert isinstance(cause, ExternalExecutionRefused) and cause.reason_code == code
    else:            # ShellTool resolves the name through its own documented seam first: an unknown name is the registry's typed refusal (still before any process)
        assert isinstance(cause, BackendUnavailableError) and "no production backend is registered" in str(cause)
    assert spawned == []


@pytest.mark.parametrize("backend", ["oci", "none"])
def test_jdtls_is_never_started_on_the_host(tmp_path, backend):
    client = JdtlsClient(str(tmp_path), "/no/such/jdtls", AutonomyConfig(containment_backend=backend))
    with pytest.raises(ExternalExecutionRefused) as raised:
        asyncio.run(client.start())
    assert raised.value.reason_code == CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT
    assert getattr(client, "_data_dir", None) is None and getattr(client, "process", None) is None  # nothing was created (the machinery that held them is gone, S8-ORPHAN-HOST-CODE-001)


def test_the_refusal_names_what_the_operator_must_do_when_there_is_no_isolating_backend():
    refusal = decide_external_execution(AutonomyConfig(containment_backend="none"), subject="ShellTool").refusal()
    assert "provides no filesystem, process or network isolation" in str(refusal) and "refusing rather than running the workload uncontained" in str(refusal)
    unknown = decide_external_execution(AutonomyConfig(containment_backend="typo"), subject="ShellTool").refusal()
    assert unknown.reason_code == CONTAINMENT_BACKEND_UNKNOWN and "typo" in str(unknown)


# ------------------------------------------------------------------ D2. explicitly classified trusted Kriya infrastructure still runs; project-controlled execution does not

def test_trusted_kriya_infrastructure_still_runs_without_an_isolating_backend_while_project_controlled_execution_is_refused(tmp_path):
    """The secure default removes the host path for PROJECT-CONTROLLED execution only. Fixed Kriya-authored commands that are explicitly classified TRUSTED_KRIYA_INFRASTRUCTURE
    (the docker CLI, git, a neutral-cwd toolchain probe; the closed list is tests/architecture/execution_site_inventory.py) never consult the project-execution owner and need no
    isolating backend: a plain run and a trusted profile on the null backend both work with the backend configured to ``none``; project-controlled consumers are refused before they start."""
    from kriya.tools.containment import ContainmentProfile, DummyContainmentBackend, NullContainmentBackend, TrustClass
    from kriya.tools.process import ProcessController

    config = AutonomyConfig(containment_backend="none")
    controller = ProcessController()
    plain = controller.run([sys.executable, "-c", "print('trusted')"], cwd=str(tmp_path), timeout=30)
    assert plain.returncode == 0 and plain.stdout.strip() == "trusted"
    trusted = ContainmentProfile(trust_class=TrustClass.TRUSTED_KRIYA_INFRASTRUCTURE, workspace_path=str(tmp_path))
    assert trusted.backend_required is False
    declared = controller.run([sys.executable, "-c", "print('declared')"], cwd=str(tmp_path), timeout=30, containment_profile=trusted, containment_backend=NullContainmentBackend())
    assert declared.returncode == 0 and declared.stdout.strip() == "declared"

    with pytest.raises(ExternalExecutionRefused) as refused:                       # ...while the same backend setting refuses project-controlled execution
        decide_external_execution(config, subject="PolymorphicValidator").backend()
    assert refused.value.reason_code == CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION

    sentinel = tmp_path / "project_controlled_ran"
    untrusted = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(tmp_path))
    with pytest.raises(BackendUnavailableError):
        controller.run([sys.executable, "-c", f"open({str(sentinel)!r}, 'w')"], cwd=str(tmp_path), timeout=30,
                       containment_profile=untrusted, containment_backend=DummyContainmentBackend(should_fail=True))
    assert not sentinel.exists()


# ------------------------------------------------------------------ E. the persisted-state boundary (D-7): a NEW identity, nothing old reads as current

def _git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)
    return path


def _legacy_dump() -> dict:
    """The configuration of a run recorded BEFORE the migration (the old packaged defaults), as its checkpoint/KUP envelope fingerprint saw it."""
    dump = AppConfig().model_dump()
    dump["autonomy"].update(contained_execution_required=False, mcp_contained_execution_required=False, containment_backend="none")
    return dump


def test_the_whole_config_fingerprint_changes_so_a_kup_preflight_or_checkpoint_recomputes():
    assert compute_config_fingerprint(_legacy_dump()) != compute_config_fingerprint(AppConfig().model_dump())


def test_a_checkpoint_recorded_under_the_old_containment_is_a_typed_mismatch_not_a_match(tmp_path):
    repo = _git_repo(tmp_path)
    old = AppConfig()
    old.autonomy.containment_backend = "none"                                         # the only old value still expressible; the old flags no longer can be
    old_prints = generation_resume_fingerprints(old, str(repo), goal="g")
    new_prints = generation_resume_fingerprints(AppConfig(), str(repo), goal="g")
    assert old_prints["containment"] != new_prints["containment"]
    dependent = next(name for name, deps in ARTIFACT_DEPENDENCIES.items() if "containment" in deps)
    by_name = {c.name: c for c in compare_resume_fingerprints(fingerprint_block(old_prints), new_prints, [dependent])}
    assert by_name["containment"].status is FingerprintStatus.CHANGED and by_name["containment"].invalidates     # fresh execution required: no compatibility shim


def test_a_release_record_made_under_the_old_default_cannot_certify_the_new_identity(tmp_path):
    recorded = ri.release_identity("none", source_root=str(Path(__file__).resolve().parent.parent))
    current = ri.release_identity(AppConfig().autonomy.containment_backend, source_root=str(Path(__file__).resolve().parent.parent))
    result = ri.compare_release_identity(recorded, current)
    assert result["status"] == ri.STALE and "containment.backend" in result["changes"]


def test_a_baseline_environment_identity_recorded_under_the_old_mode_is_not_the_current_one(tmp_path):
    current = json.loads(baseline_environment_identity(str(tmp_path), AppConfig().autonomy))
    legacy = {**current, "execution": "host"}                                          # what a baseline recorded before the migration said
    assert current["execution"] == "contained" and json.dumps(current, sort_keys=True) != json.dumps(legacy, sort_keys=True)


def test_the_owner_still_exposes_no_legacy_host_reason():
    assert not hasattr(containment, "HOST_EXECUTION_PERMITTED")

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.config.config import (
    load_config,
    PRODUCTION_FIXED_RUNTIME_GUARANTEES,
    FallbackModelConfig,
    runtime_profile_preset_fields,
)
from kriya.production_doctor import (
    _SMOKE_SCRIPT,
    CHECK_RAISED,
    CONFIG_LOAD_CHECK_ID,
    FIXED_GUARANTEE_EVIDENCE,
    MODEL_NOT_QUALIFIED,
    PRODUCTION_DOCTOR_CHECK_IDS,
    RUNTIME_FINGERPRINT_NOT_COMPUTABLE,
    RUNTIME_QUALIFICATION_BINDING_UNAVAILABLE,
    CheckStatus,
    DoctorCheck,
    ProductionDoctorReport,
    probe_llm_runtime,
    probe_oci_containment,
    run_production_doctor,
)

MODEL_BINDING_CHECKS = {"model.runtime_fingerprint", "model.qualification"}


def _docker_ready():
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        return subprocess.run(
            [docker, "info"], capture_output=True, timeout=5
        ).returncode == 0
    except Exception:
        return False


def _production_cfg(tmp_path):
    cfg = AppConfig()
    cfg.llm.model = "qwen3-coder:30b"
    cfg.runtime_profile = "production"
    for (top, leaf), value in runtime_profile_preset_fields("production").items():
        setattr(getattr(cfg, top), leaf, value)
    cfg.plugins.directory = str(tmp_path / "plugins")
    core = tmp_path / "plugins" / "core_tools"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "validation_tool.py").write_text("", encoding="utf-8")
    return cfg


def _git_workspace(path):
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


SMOKE_EVIDENCE = {
    "backend": "oci", "image": "debian:bookworm-slim", "returncode": 0, "ipv4_routes": "0",
    "ipv6_non_loopback_routes": "0", "up_interfaces": [], "effective_capabilities": "0000000000000000",
    "no_new_privileges": "1", "leftover_containers": [], "toolchain_identity": None,
}


@contextmanager
def _healthy_boundaries():
    """Every external boundary healthy: Docker, the model endpoint (which
    exposes an exact fingerprint), embeddings, and the LSP binary."""
    with patch("kriya.production_doctor._Docker.resolve", return_value=("/usr/bin/docker", {"runtime": "docker"})), \
         patch("kriya.production_doctor.probe_oci_containment", return_value=dict(SMOKE_EVIDENCE)), \
         patch("kriya.production_doctor.probe_llm_runtime", return_value={
             "models": ["qwen3-coder:30b"],
             "selected_model": {"id": "qwen3-coder:30b"},
             "native_metadata": {"details": {"format": "gguf"}, "digest": "sha256:abc"},
             "fingerprint": "a" * 64,
         }), \
         patch("kriya.production_doctor.probe_embedding", return_value={"dimensions": 384}), \
         patch("kriya.tools.lsp.find_jdtls", return_value=None):
        yield


def _checks(report):
    return {check.id: check for check in report.checks}


def _run(tmp_path, cfg=None, workspace=None):
    cfg = cfg or _production_cfg(tmp_path)
    workspace = workspace or _git_workspace(tmp_path / "workspace")
    with _healthy_boundaries():
        return run_production_doctor(cfg, str(workspace))


def _blocking(report):
    return {
        check.id for check in report.checks
        if check.required and check.status in (CheckStatus.FAIL, CheckStatus.UNAVAILABLE)
    }


# --- the deployment decision -------------------------------------------------------------

def test_healthy_deployment_is_blocked_only_by_the_pending_exact_runtime_qualification(tmp_path):
    """Everything else passes; readiness still fails closed because a model is
    not production-qualified until its exact runtime is (PRD-013/014)."""
    report = _run(tmp_path)
    checks = _checks(report)
    assert report.production_ready is False
    assert _blocking(report) == MODEL_BINDING_CHECKS
    assert report.schema_version == 1
    for check_id in ("lsp.java", "models.role_independence", "semantic.precision_boundary"):
        assert checks[check_id].status is CheckStatus.WARN
        assert checks[check_id].required is False
    assert checks["runtime.fixed_guarantees"].status is CheckStatus.PASS
    assert all(check.evidence is not None and check.remediation for check in report.checks)


@pytest.mark.parametrize(
    ("check_id", "mutation", "boundary_patch", "expected"),
    [
        ("profile.production", lambda cfg, ws: setattr(cfg, "runtime_profile", None), None, CheckStatus.FAIL),
        ("plugins.core_tools", lambda cfg, ws: os.unlink(os.path.join(cfg.plugins.directory, "core_tools", "__init__.py")), None, CheckStatus.FAIL),
        ("containment.oci_smoke", lambda cfg, ws: None, ("kriya.production_doctor._Docker.resolve", None, (None, {"error": "docker CLI not found"})), CheckStatus.UNAVAILABLE),
        ("containment.oci_smoke", lambda cfg, ws: None, ("kriya.production_doctor.probe_oci_containment", FileNotFoundError("image absent"), None), CheckStatus.UNAVAILABLE),
        ("model.connectivity", lambda cfg, ws: None, ("kriya.production_doctor.probe_llm_runtime", ConnectionError("offline"), None), CheckStatus.UNAVAILABLE),
        ("embedding.connectivity", lambda cfg, ws: None, ("kriya.production_doctor.probe_embedding", ConnectionError("offline"), None), CheckStatus.UNAVAILABLE),
    ],
)
def test_required_boundary_failures_block_production(tmp_path, check_id, mutation, boundary_patch, expected):
    cfg = _production_cfg(tmp_path)
    workspace = _git_workspace(tmp_path / "workspace")
    mutation(cfg, workspace)

    with _healthy_boundaries():
        if boundary_patch:
            target, side_effect, return_value = boundary_patch
            with patch(target, side_effect=side_effect, return_value=return_value):
                report = run_production_doctor(cfg, str(workspace))
        else:
            report = run_production_doctor(cfg, str(workspace))

    assert report.production_ready is False
    assert _checks(report)[check_id].status is expected


def test_check_ids_are_pinned_unique_and_always_complete(tmp_path):
    report = _run(tmp_path)
    ids = tuple(check.id for check in report.checks)
    assert ids == PRODUCTION_DOCTOR_CHECK_IDS
    assert len(ids) == len(set(ids))
    assert PRODUCTION_DOCTOR_CHECK_IDS == (
        "profile.production", "plugins.core_tools", "workspace.identity_lock",
        "persistence.checkpoints", "persistence.traces", "persistence.logs", "capacity.workspace",
        "capacity.temp", "git.worktree", "isolation.candidate_worktree", "toolchain.required",
        "containment.oci_smoke", "containment.no_host_fallback", "egress.policy",
        "model.connectivity", "model.runtime_fingerprint", "model.qualification",
        "embedding.connectivity", "lsp.java", "models.role_independence",
        "semantic.precision_boundary", "release.integrity", "runtime.fixed_guarantees",
    )


# --- a check that raises never crashes the report ----------------------------------------

def test_every_check_raising_still_yields_the_complete_parseable_report(tmp_path):
    from kriya import production_doctor

    def boom(_ctx):
        raise RuntimeError("probe exploded")

    rows = tuple((check_id, required, boom) for check_id, required, _ in production_doctor._CHECKS)
    with patch.object(production_doctor, "_CHECKS", rows):
        report = run_production_doctor(_production_cfg(tmp_path), str(_git_workspace(tmp_path / "ws")))

    assert tuple(check.id for check in report.checks) == PRODUCTION_DOCTOR_CHECK_IDS
    assert report.production_ready is False
    for check in report.checks:
        assert check.evidence["reason_code"] == CHECK_RAISED
        assert check.status is (CheckStatus.FAIL if check.required else CheckStatus.WARN)
    assert json.loads(json.dumps(report.to_dict()))["checks"][0]["status"] == "FAIL"


@pytest.mark.parametrize("target", [
    "kriya.core.model_capabilities.resolve_model_capability_profile",
    "kriya.tools.validate.PolymorphicValidator",
    "kriya.control.run_ownership.probe_run_lock",
])
def test_an_unexpected_exception_is_confined_to_its_own_check(tmp_path, target):
    """Formerly unwrapped (capability profile, validator/toolchain, lock
    probe): each now fails its own check and the report still completes."""
    cfg = _production_cfg(tmp_path)
    workspace = _git_workspace(tmp_path / "workspace")
    with _healthy_boundaries(), patch(target, side_effect=RuntimeError("unexpected")):
        report = run_production_doctor(cfg, str(workspace))
    raised = [check for check in report.checks if check.evidence.get("reason_code") == CHECK_RAISED]
    assert len(raised) == 1 and raised[0].status is CheckStatus.FAIL
    assert tuple(check.id for check in report.checks) == PRODUCTION_DOCTOR_CHECK_IDS


# --- the doctor never changes the workspace ----------------------------------------------

def test_doctor_neither_takes_the_run_lock_nor_creates_state_in_a_fresh_workspace(tmp_path):
    workspace = _git_workspace(tmp_path / "workspace")
    with patch("kriya.control.run_ownership.acquire_run_lock", side_effect=AssertionError("lock taken")):
        report = _run(tmp_path, workspace=workspace)
    checks = _checks(report)
    assert checks["workspace.identity_lock"].status is CheckStatus.PASS
    assert checks["persistence.checkpoints"].status is CheckStatus.PASS
    assert checks["persistence.checkpoints"].evidence["exists"] is False
    assert sorted(os.listdir(workspace)) == [".git"]


def test_a_held_workspace_lock_blocks_and_names_the_holder(tmp_path):
    from kriya.control.run_ownership import acquire_run_lock

    workspace = _git_workspace(tmp_path / "workspace")
    with acquire_run_lock(str(workspace), run_id="someone-else"):
        report = _run(tmp_path, workspace=workspace)
    check = _checks(report)["workspace.identity_lock"]
    assert check.status is CheckStatus.FAIL
    assert "held_by" in check.evidence


def test_unwritable_checkpoint_store_blocks_and_fails_its_guarantee(tmp_path):
    from kriya import production_doctor

    real_probe = production_doctor._store_probe

    def probe(path, **kwargs):
        if path.endswith(os.path.join(".kriya", "checkpoints")):
            raise PermissionError("read only")
        return real_probe(path, **kwargs)

    with patch("kriya.production_doctor._store_probe", side_effect=probe):
        report = _run(tmp_path)
    checks = _checks(report)
    assert checks["persistence.checkpoints"].status is CheckStatus.FAIL
    guarantees = checks["runtime.fixed_guarantees"]
    assert guarantees.status is CheckStatus.FAIL
    assert guarantees.evidence["guarantees"]["checkpoint_persistence"]["verified_by"] == {"persistence.checkpoints": "FAIL"}


def test_insufficient_workspace_capacity_blocks(tmp_path):
    workspace = _git_workspace(tmp_path / "workspace")
    real_usage = shutil.disk_usage

    def disk_usage(path):
        if os.path.realpath(path) == os.path.realpath(workspace):
            return shutil._ntuple_diskusage(total=100, used=100, free=0)
        return real_usage(path)

    with patch("kriya.production_doctor.shutil.disk_usage", side_effect=disk_usage):
        report = _run(tmp_path, workspace=workspace)
    assert _checks(report)["capacity.workspace"].status is CheckStatus.FAIL
    assert report.production_ready is False


def test_git_worktree_unavailable_blocks(tmp_path):
    real_which = shutil.which
    with patch(
        "kriya.production_doctor.shutil.which",
        side_effect=lambda name: None if name == "git" else real_which(name),
    ):
        report = _run(tmp_path)
    assert _checks(report)["git.worktree"].status is CheckStatus.FAIL
    assert _checks(report)["runtime.fixed_guarantees"].status is CheckStatus.FAIL


# --- the fixed guarantees are verified, not declared ------------------------------------

def test_every_fixed_guarantee_is_verified_by_real_checks():
    assert set(FIXED_GUARANTEE_EVIDENCE) == set(PRODUCTION_FIXED_RUNTIME_GUARANTEES)
    for check_ids in FIXED_GUARANTEE_EVIDENCE.values():
        assert check_ids and set(check_ids) <= set(PRODUCTION_DOCTOR_CHECK_IDS) - {"runtime.fixed_guarantees"}


def test_candidate_isolation_is_proven_with_the_real_mechanism_outside_the_workspace(tmp_path):
    workspace = _git_workspace(tmp_path / "workspace")
    report = _run(tmp_path, workspace=workspace)
    check = _checks(report)["isolation.candidate_worktree"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["mechanism"] == "create_git_worktree"
    assert not (workspace / ".kriya").exists()


@pytest.mark.parametrize("weaken", [
    lambda cfg: setattr(cfg.autonomy, "containment_backend", "none"),
    lambda cfg: setattr(cfg.autonomy, "contained_execution_required", False),
])
def test_a_containment_posture_that_could_fall_back_to_the_host_blocks(tmp_path, weaken):
    cfg = _production_cfg(tmp_path)
    weaken(cfg)
    report = _run(tmp_path, cfg=cfg)
    checks = _checks(report)
    assert checks["containment.no_host_fallback"].status is CheckStatus.FAIL
    assert checks["containment.no_host_fallback"].evidence["problems"]
    assert checks["runtime.fixed_guarantees"].status is CheckStatus.FAIL


def test_no_host_fallback_evidence_includes_the_null_backend_refusal(tmp_path):
    evidence = _checks(_run(tmp_path))["containment.no_host_fallback"].evidence
    assert evidence["null_backend_refuses_isolation_profile"] is True
    assert evidence["unknown_backend_refused"] is True
    assert evidence["configured_backend"] == "oci"


# --- toolchain: what containment will run, never host tools ------------------------------

def _pom(workspace, release):
    (workspace / "pom.xml").write_text(
        f"<project><properties><maven.compiler.release>{release}</maven.compiler.release></properties></project>",
        encoding="utf-8",
    )


def test_an_unsupported_declared_toolchain_blocks(tmp_path):
    workspace = _git_workspace(tmp_path / "workspace")
    _pom(workspace, 7)  # Java 8/11/17/21 are supported (PRD-011 reopen)
    check = _checks(_run(tmp_path, workspace=workspace))["toolchain.required"]
    assert check.status is CheckStatus.FAIL
    assert "Java 7" in check.evidence["error"]


def test_an_absent_toolchain_image_is_unavailable_and_never_pulled(tmp_path):
    workspace = _git_workspace(tmp_path / "workspace")
    _pom(workspace, 17)
    with patch("kriya.tools.containment_oci._inspect_image_digest", return_value=None), \
         patch("kriya.tools.containment_oci._attest_toolchain_image") as attest:
        check = _checks(_run(tmp_path, workspace=workspace))["toolchain.required"]
    attest.assert_not_called()
    assert check.status is CheckStatus.UNAVAILABLE
    assert check.remediation == "Run: docker pull maven:3.9-eclipse-temurin-17"
    assert check.evidence["required"]["containment_image"] == "maven:3.9-eclipse-temurin-17"


def test_a_present_toolchain_image_is_attested_without_pulling(tmp_path):
    workspace = _git_workspace(tmp_path / "workspace")
    _pom(workspace, 21)

    def attest(_docker, image, identity, *, allow_pull):
        assert allow_pull is False
        return identity.with_runtime_evidence(
            image_digest="sha256:" + "1" * 64, observed_runtime_version="21", observed_build_tool_version="3.9",
        )

    with patch("kriya.tools.containment_oci._inspect_image_digest", return_value="sha256:" + "1" * 64), \
         patch("kriya.tools.containment_oci._attest_toolchain_image", side_effect=attest), \
         patch("kriya.production_doctor.shutil.which", return_value=None):  # host tools are irrelevant
        report = _run(tmp_path, workspace=workspace)
    check = _checks(report)["toolchain.required"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["attested"]["observed_runtime_version"] == "21"
    assert check.evidence["build_tool_attested"] is True


def test_an_unattested_gradle_build_tool_is_reported_not_claimed(tmp_path):
    workspace = _git_workspace(tmp_path / "workspace")
    (workspace / "build.gradle").write_text("java { toolchain { languageVersion = JavaLanguageVersion.of(17) } }\n")

    def attest(_docker, image, identity, *, allow_pull):
        return identity.with_runtime_evidence(image_digest="sha256:" + "2" * 64, observed_runtime_version="17")

    with patch("kriya.tools.containment_oci._inspect_image_digest", return_value="sha256:" + "2" * 64), \
         patch("kriya.tools.containment_oci._attest_toolchain_image", side_effect=attest):
        check = _checks(_run(tmp_path, workspace=workspace))["toolchain.required"]
    assert check.status is CheckStatus.WARN
    assert check.evidence["build_tool_attested"] is False


def test_a_stack_without_a_production_containment_profile_blocks(tmp_path):
    """Ruby has no versioned containment image; under required containment it
    cannot be verified, so it no longer passes on the host's interpreter."""
    workspace = _git_workspace(tmp_path / "workspace")
    (workspace / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    check = _checks(_run(tmp_path, workspace=workspace))["toolchain.required"]
    assert check.status is CheckStatus.UNAVAILABLE
    assert check.evidence["stack"] == "ruby"


def test_a_hung_docker_daemon_is_unavailable_and_probed_once(tmp_path):
    from kriya.production_doctor import _Docker

    calls = []

    def hung(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args[0], 10)

    with patch("kriya.production_doctor.shutil.which", return_value="/usr/bin/docker"), \
         patch("kriya.production_doctor.subprocess.run", side_effect=hung):
        docker = _Docker()
        assert docker.resolve()[0] is None
        assert docker.resolve()[0] is None
    assert len(calls) == 1


def test_a_workspace_with_no_detectable_toolchain_is_a_visible_warning(tmp_path):
    check = _checks(_run(tmp_path))["toolchain.required"]
    assert check.status is CheckStatus.WARN
    assert check.evidence["stack"] == "unknown"


# --- models: a name is never a qualification --------------------------------------------

def test_a_computed_fingerprint_is_evidence_but_stays_unavailable_until_it_is_bound(tmp_path):
    check = _checks(_run(tmp_path))["model.runtime_fingerprint"]
    assert check.status is CheckStatus.UNAVAILABLE
    assert check.required is True
    assert check.evidence["fingerprint"] == "a" * 64
    assert check.evidence["reason_code"] == RUNTIME_QUALIFICATION_BINDING_UNAVAILABLE


def test_missing_exact_runtime_fingerprint_blocks_production(tmp_path):
    cfg = _production_cfg(tmp_path)
    with _healthy_boundaries(), patch("kriya.production_doctor.probe_llm_runtime", return_value={
        "models": [cfg.llm.model], "selected_model": {"id": cfg.llm.model},
        "native_metadata": None, "fingerprint": None,
    }):
        report = run_production_doctor(cfg, str(_git_workspace(tmp_path / "workspace")))
    check = _checks(report)["model.runtime_fingerprint"]
    assert check.status is CheckStatus.UNAVAILABLE
    assert check.evidence["reason_code"] == RUNTIME_FINGERPRINT_NOT_COMPUTABLE
    assert report.production_ready is False


def test_a_campaign_model_name_is_not_a_qualification(tmp_path):
    check = _checks(_run(tmp_path))["model.qualification"]
    assert check.status is CheckStatus.UNAVAILABLE
    assert check.evidence["name_based_profile_source"] == "known_production_profile"
    assert check.evidence["campaign_named"] is True
    assert check.evidence["name_based_profile_is_authority"] is False
    assert check.evidence["reason_code"] == RUNTIME_QUALIFICATION_BINDING_UNAVAILABLE


def test_unqualified_model_blocks_production(tmp_path):
    cfg = _production_cfg(tmp_path)
    cfg.llm.model = "unqualified:latest"
    report = _run(tmp_path, cfg=cfg)
    check = _checks(report)["model.qualification"]
    assert check.status is CheckStatus.FAIL
    assert check.evidence["reason_code"] == MODEL_NOT_QUALIFIED
    assert report.production_ready is False


def _loaded_llm_config(tmp_path, body="{}\n"):
    """The llm section exactly as load_config() builds it: the packaged
    default declares llm.capabilities, so the profile source is explicit_primary."""
    path = tmp_path / "operator.yaml"
    path.write_text(body, encoding="utf-8")
    return load_config(str(path)).llm


def test_a_loaded_configs_campaign_model_is_unavailable_not_failed(tmp_path):
    """Regression (live CLI run, 2026-09-25): keyed on the capability-profile
    source, a real loaded config reported FAIL/MODEL_NOT_QUALIFIED for a
    campaign model while a bare AppConfig() reported UNAVAILABLE."""
    cfg = _production_cfg(tmp_path)
    cfg.llm = _loaded_llm_config(tmp_path)
    assert cfg.llm.capabilities.model_fields_set
    check = _checks(_run(tmp_path, cfg=cfg))["model.qualification"]
    assert check.status is CheckStatus.UNAVAILABLE
    assert check.evidence["campaign_named"] is True
    assert check.evidence["name_based_profile_source"] == "explicit_primary"
    assert check.evidence["reason_code"] == RUNTIME_QUALIFICATION_BINDING_UNAVAILABLE


def test_a_loaded_configs_unknown_model_still_fails(tmp_path):
    cfg = _production_cfg(tmp_path)
    cfg.llm = _loaded_llm_config(tmp_path, "llm:\n  model: unqualified:latest\n")
    check = _checks(_run(tmp_path, cfg=cfg))["model.qualification"]
    assert check.status is CheckStatus.FAIL
    assert check.evidence["campaign_named"] is False
    assert check.evidence["reason_code"] == MODEL_NOT_QUALIFIED


def test_runtime_fingerprint_binds_openai_identity_to_native_digest_and_metadata(tmp_path):
    cfg = _production_cfg(tmp_path)

    def response(url, **_kwargs):
        if url.endswith("/v1/models"):
            return {"data": [{"id": cfg.llm.model, "owned_by": "library"}]}
        if url.endswith("/api/tags"):
            return {"models": [{"name": cfg.llm.model, "digest": "sha256:abc"}]}
        if url.endswith("/api/show"):
            return {"details": {"format": "gguf", "quantization_level": "Q4_K_M"}, "model_info": {"arch": "qwen3"}}
        raise AssertionError(url)

    with patch("kriya.production_doctor._json_request", side_effect=response):
        first = probe_llm_runtime(cfg)
        second = probe_llm_runtime(cfg)

    assert first["native_metadata"]["digest"] == "sha256:abc"
    assert first["fingerprint"] == second["fingerprint"]
    assert len(first["fingerprint"]) == 64


# --- egress ------------------------------------------------------------------------------

def test_unsafe_egress_policy_blocks(tmp_path):
    cfg = _production_cfg(tmp_path)
    cfg.autonomy.egress_policy = "unrestricted"
    report = _run(tmp_path, cfg=cfg)
    assert _checks(report)["egress.policy"].status is CheckStatus.FAIL
    assert report.production_ready is False


@pytest.mark.parametrize("point_away", [
    lambda cfg: setattr(cfg.llm, "base_url", "http://8.8.8.8/v1"),
    lambda cfg: setattr(cfg.embedding, "base_url", "http://8.8.8.8/v1"),
    lambda cfg: cfg.llm_chain.append(FallbackModelConfig(model="m", base_url="http://8.8.8.8/v1")),
])
def test_a_non_local_model_endpoint_blocks_even_under_local_only_policy(tmp_path, point_away):
    cfg = _production_cfg(tmp_path)
    point_away(cfg)
    check = _checks(_run(tmp_path, cfg=cfg))["egress.policy"]
    assert check.status is CheckStatus.FAIL
    assert len(check.evidence["non_local_endpoints"]) == 1


def test_local_model_endpoints_pass_egress(tmp_path):
    check = _checks(_run(tmp_path))["egress.policy"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["non_local_endpoints"] == []


# --- remaining checks --------------------------------------------------------------------

def test_semantic_precision_boundary_is_reported_from_the_authority_modules_own_scope(tmp_path):
    from kriya.workflow.semantic_region_authority import SEMANTIC_REGION_SUPPORTED_SCOPE

    check = _checks(_run(tmp_path))["semantic.precision_boundary"]
    assert check.status is CheckStatus.WARN and check.required is False
    assert check.evidence["supported_scope"]["languages"] == list(SEMANTIC_REGION_SUPPORTED_SCOPE["languages"])
    assert check.evidence["forced_by_production_profile"] is False


def test_incomplete_release_integrity_blocks(tmp_path):
    with patch("kriya.distribution.check_distribution", return_value=["plugins/core_tools/__init__.py"]):
        report = _run(tmp_path)
    assert _checks(report)["release.integrity"].status is CheckStatus.FAIL
    assert report.production_ready is False


# --- CLI -----------------------------------------------------------------------------------

def test_json_cli_is_stable_and_exit_code_tracks_required_failures(tmp_path):
    cfg = _production_cfg(tmp_path)
    report = ProductionDoctorReport(
        schema_version=1,
        production_ready=False,
        checks=(DoctorCheck(
            "containment.oci_smoke", CheckStatus.UNAVAILABLE, True,
            {"error": "docker missing"}, "Install Docker.",
        ),),
    )
    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("kriya.production_doctor.run_production_doctor", return_value=report):
        result = runner.invoke(main, ["doctor", "--production", "--json"])

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == report.to_dict()
    assert payload["checks"][0]["id"] == "containment.oci_smoke"


def test_json_report_survives_a_configuration_that_cannot_load():
    runner = CliRunner()
    with patch("kriya.cli.load_config", side_effect=ValueError("denied field")):
        result = runner.invoke(main, ["doctor", "--production", "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["production_ready"] is False
    assert [(c["id"], c["status"]) for c in payload["checks"]] == [(CONFIG_LOAD_CHECK_ID, "FAIL")]
    assert "denied field" in payload["checks"][0]["evidence"]["error"]


def test_plain_doctor_still_refuses_a_configuration_that_cannot_load():
    runner = CliRunner()
    with patch("kriya.cli.load_config", side_effect=ValueError("denied field")):
        result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 1
    assert "Error loading configuration: denied field" in result.stderr


def test_production_doctor_cli_never_opens_a_log_file_in_the_workspace(tmp_path, monkeypatch):
    """Regression (live CLI run, 2026-09-25): main() configured file logging
    before the doctor ran, and the packaged ./logs/kriya.log resolves against
    the CWD, so the doctor left logs/kriya.log in the workspace. Root handlers
    are cleared here because configure_logging() is a no-op when any exist
    (pytest installs its own), which would make this pass vacuously."""
    import logging

    cfg = _production_cfg(tmp_path)
    workspace = _git_workspace(tmp_path / "workspace")
    monkeypatch.chdir(workspace)
    report = ProductionDoctorReport(schema_version=1, production_ready=False, checks=())
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()
    try:
        with patch("kriya.cli.load_config", return_value=cfg), \
             patch("kriya.production_doctor.run_production_doctor", return_value=report):
            result = CliRunner().invoke(main, ["doctor", "--production", "--json"])
        opened_files = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    finally:
        for handler in root.handlers:
            if handler not in saved:
                handler.close()
        root.handlers[:] = saved
    assert result.exit_code == 1
    assert opened_files == []
    assert sorted(os.listdir(workspace)) == [".git"]


def test_plain_doctor_keeps_file_logging():
    import urllib.error

    with patch("kriya.cli.load_config", return_value=AppConfig()), \
         patch("kriya.cli.configure_logging") as configure, \
         patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", side_effect=RuntimeError("offline")):
        CliRunner().invoke(main, ["doctor"])
    configure.assert_called_once()
    assert configure.call_args.kwargs.get("file_logging", True) is True


def test_log_directory_check_passes_without_creating_the_directory(tmp_path, monkeypatch):
    log_dir = tmp_path / "not-yet" / "logs"
    monkeypatch.setenv("KRIYA_LOG_DIR", str(log_dir))
    check = _checks(_run(tmp_path))["persistence.logs"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["source"] == "env"
    assert check.evidence["exists"] is False
    assert not log_dir.exists()


def test_a_relative_log_directory_fails_the_log_check(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_LOG_DIR", "relative/logs")
    report = _run(tmp_path)
    check = _checks(report)["persistence.logs"]
    assert check.status is CheckStatus.FAIL and check.required
    assert "absolute" in check.evidence["error"]
    assert "persistence.logs" in _blocking(report)


def test_an_unwritable_log_directory_fails_the_log_check(tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setenv("KRIYA_LOG_DIR", str(locked / "logs"))
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("running with privileges that ignore directory permissions")
        check = _checks(_run(tmp_path))["persistence.logs"]
    finally:
        locked.chmod(0o700)
    assert check.status is CheckStatus.FAIL


def test_log_files_disabled_is_a_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_LOG_DIR", str(tmp_path / "logs"))
    cfg = _production_cfg(tmp_path)
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    check = _checks(_run(tmp_path, cfg=cfg))["persistence.logs"]
    assert check.status is CheckStatus.WARN


def test_json_requires_production_mode(tmp_path):
    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=AppConfig()):
        result = runner.invoke(main, ["doctor", "--json"])
    assert result.exit_code == 2
    assert "--json is supported with --production" in result.output


# --- real Docker ---------------------------------------------------------------------------

def _image_present(image):
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_ready(), reason="docker daemon not reachable")
def test_real_oci_containment_smoke_proves_network_capabilities_and_cleanup(tmp_path):
    """Through the configured OCIContainmentBackend and ProcessController, as
    production verification runs. The earlier smoke asserted a read-only root
    filesystem that production containers are never given; this asserts what
    the backend actually enforces."""
    if not _image_present("debian:bookworm-slim"):
        pytest.skip("debian:bookworm-slim not pulled; the doctor never pulls")
    cfg = _production_cfg(tmp_path)
    evidence = probe_oci_containment(cfg, shutil.which("docker"), None)
    assert evidence["backend"] == "oci"
    assert evidence["ipv4_routes"] == "0" and evidence["up_interfaces"] == []
    assert evidence["effective_capabilities"] == "0000000000000000"
    assert evidence["no_new_privileges"] == "1"
    assert evidence["leftover_containers"] == []


@pytest.mark.skipif(not _docker_ready(), reason="docker daemon not reachable")
def test_real_smoke_assertions_detect_an_uncontained_container():
    """The smoke script really distinguishes containment: the same script in a
    default container reports a route, an interface and capabilities."""
    if not _image_present("debian:bookworm-slim"):
        pytest.skip("debian:bookworm-slim not pulled")
    out = subprocess.run(
        ["docker", "run", "--rm", "debian:bookworm-slim", "/bin/sh", "-c", _SMOKE_SCRIPT],
        capture_output=True, text=True, timeout=60,
    ).stdout
    fields = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    assert fields["IPV4_ROUTES"] != "0"
    assert fields["CAPEFF"] != "0000000000000000"

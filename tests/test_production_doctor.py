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
from kriya.config.config import runtime_profile_preset_fields
from kriya.production_doctor import (
    CheckStatus,
    DoctorCheck,
    ProductionDoctorReport,
    probe_llm_runtime,
    probe_oci_runtime,
    run_production_doctor,
)


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
    cfg.paths.logs = str(tmp_path / "logs")
    core = tmp_path / "plugins" / "core_tools"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "validation_tool.py").write_text("", encoding="utf-8")
    return cfg


def _git_workspace(path):
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


@contextmanager
def _healthy_boundaries():
    with patch("kriya.production_doctor.probe_oci_runtime", return_value={"runtime": "docker"}), \
         patch("kriya.production_doctor.probe_llm_runtime", return_value={
             "models": ["qwen3-coder:30b"],
             "selected_model": {"id": "qwen3-coder:30b"},
             "native_metadata": {"details": {"format": "gguf"}},
             "fingerprint": "a" * 64,
         }), \
         patch("kriya.production_doctor.probe_embedding", return_value={"dimensions": 384}), \
         patch("kriya.tools.lsp.find_jdtls", return_value=None):
        yield


def _checks(report):
    return {check.id: check for check in report.checks}


def test_production_doctor_all_required_checks_pass_and_warnings_do_not_block(tmp_path):
    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)

    with _healthy_boundaries():
        report = run_production_doctor(cfg, str(workspace))

    checks = _checks(report)
    assert report.production_ready is True
    assert report.schema_version == 1
    assert checks["lsp.java"].status is CheckStatus.WARN
    assert checks["lsp.java"].required is False
    assert checks["models.role_independence"].status is CheckStatus.WARN
    assert checks["models.role_independence"].required is False
    assert all(check.evidence is not None and check.remediation for check in report.checks)


@pytest.mark.parametrize(
    ("check_id", "mutation", "boundary_patch", "expected"),
    [
        ("profile.production", lambda cfg, ws: setattr(cfg, "runtime_profile", None), None, CheckStatus.FAIL),
        ("plugins.core_tools", lambda cfg, ws: os.unlink(os.path.join(cfg.plugins.directory, "core_tools", "__init__.py")), None, CheckStatus.FAIL),
        ("containment.oci_smoke", lambda cfg, ws: None, ("kriya.production_doctor.probe_oci_runtime", FileNotFoundError("docker missing")), CheckStatus.UNAVAILABLE),
        ("model.connectivity", lambda cfg, ws: None, ("kriya.production_doctor.probe_llm_runtime", ConnectionError("offline")), CheckStatus.UNAVAILABLE),
        ("embedding.connectivity", lambda cfg, ws: None, ("kriya.production_doctor.probe_embedding", ConnectionError("offline")), CheckStatus.UNAVAILABLE),
    ],
)
def test_required_boundary_failures_block_production(tmp_path, check_id, mutation, boundary_patch, expected):
    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    mutation(cfg, workspace)

    with _healthy_boundaries():
        if boundary_patch:
            with patch(boundary_patch[0], side_effect=boundary_patch[1]):
                report = run_production_doctor(cfg, str(workspace))
        else:
            report = run_production_doctor(cfg, str(workspace))

    assert report.production_ready is False
    assert _checks(report)[check_id].status is expected


def test_missing_exact_runtime_fingerprint_blocks_production(tmp_path):
    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    with _healthy_boundaries(), patch("kriya.production_doctor.probe_llm_runtime", return_value={
        "models": [cfg.llm.model], "selected_model": {"id": cfg.llm.model},
        "native_metadata": None, "fingerprint": None,
    }):
        report = run_production_doctor(cfg, str(workspace))
    assert _checks(report)["model.runtime_fingerprint"].status is CheckStatus.UNAVAILABLE
    assert report.production_ready is False


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


def test_unqualified_model_blocks_production(tmp_path):
    cfg = _production_cfg(tmp_path)
    cfg.llm.model = "unqualified:latest"
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    with _healthy_boundaries(), patch("kriya.production_doctor.probe_llm_runtime", return_value={
        "models": [cfg.llm.model], "selected_model": {"id": cfg.llm.model},
        "native_metadata": {"details": {}}, "fingerprint": "b" * 64,
    }):
        report = run_production_doctor(cfg, str(workspace))
    assert _checks(report)["model.qualification"].status is CheckStatus.FAIL
    assert report.production_ready is False


def test_manifest_declared_missing_toolchain_blocks(tmp_path):
    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    (workspace / "pom.xml").write_text("<project/>", encoding="utf-8")

    def resolve_tool(_workspace, candidates):
        return None if "mvn" in candidates else "/usr/bin/tool"

    with _healthy_boundaries(), patch("kriya.production_doctor._resolve_tool", side_effect=resolve_tool):
        report = run_production_doctor(cfg, str(workspace))
    check = _checks(report)["toolchain.required"]
    assert check.status is CheckStatus.FAIL
    assert check.evidence["missing"] == ["maven"]


def test_workspace_lock_contention_blocks(tmp_path):
    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    with _healthy_boundaries(), patch(
        "kriya.control.run_ownership.acquire_run_lock",
        side_effect=RuntimeError("held"),
    ):
        report = run_production_doctor(cfg, str(workspace))
    assert _checks(report)["workspace.identity_lock"].status is CheckStatus.FAIL
    assert report.production_ready is False


def test_unwritable_checkpoint_store_blocks(tmp_path):
    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    real_probe = __import__("kriya.production_doctor", fromlist=["_writable_probe"])._writable_probe

    def probe(path):
        if path.endswith(os.path.join(".kriya", "checkpoints")):
            raise PermissionError("read only")
        return real_probe(path)

    with _healthy_boundaries(), patch("kriya.production_doctor._writable_probe", side_effect=probe):
        report = run_production_doctor(cfg, str(workspace))
    assert _checks(report)["persistence.checkpoints"].status is CheckStatus.FAIL
    assert report.production_ready is False


def test_insufficient_workspace_capacity_blocks(tmp_path):
    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    real_usage = shutil.disk_usage

    def disk_usage(path):
        if os.path.realpath(path) == os.path.realpath(workspace):
            return shutil._ntuple_diskusage(total=100, used=100, free=0)
        return real_usage(path)

    with _healthy_boundaries(), patch("kriya.production_doctor.shutil.disk_usage", side_effect=disk_usage):
        report = run_production_doctor(cfg, str(workspace))
    assert _checks(report)["capacity.workspace"].status is CheckStatus.FAIL
    assert report.production_ready is False


def test_git_worktree_unavailable_blocks(tmp_path):
    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    real_which = shutil.which

    with _healthy_boundaries(), patch(
        "kriya.production_doctor.shutil.which",
        side_effect=lambda name: None if name == "git" else real_which(name),
    ):
        report = run_production_doctor(cfg, str(workspace))
    assert _checks(report)["git.worktree"].status is CheckStatus.FAIL
    assert report.production_ready is False


def test_unsafe_egress_policy_blocks(tmp_path):
    cfg = _production_cfg(tmp_path)
    cfg.autonomy.egress_policy = "unrestricted"
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    with _healthy_boundaries():
        report = run_production_doctor(cfg, str(workspace))
    assert _checks(report)["egress.policy"].status is CheckStatus.FAIL
    assert report.production_ready is False


def test_incomplete_release_integrity_blocks(tmp_path):
    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    with _healthy_boundaries(), patch(
        "kriya.distribution.check_distribution", return_value=["plugins/core_tools/__init__.py"]
    ):
        report = run_production_doctor(cfg, str(workspace))
    assert _checks(report)["release.integrity"].status is CheckStatus.FAIL
    assert report.production_ready is False


def test_check_ids_are_stable_and_unique(tmp_path):
    cfg = _production_cfg(tmp_path)
    workspace = tmp_path / "workspace"
    _git_workspace(workspace)
    with _healthy_boundaries():
        report = run_production_doctor(cfg, str(workspace))
    ids = [check.id for check in report.checks]
    assert len(ids) == len(set(ids))
    assert ids == [
        "profile.production", "plugins.core_tools", "workspace.identity_lock",
        "persistence.checkpoints", "persistence.traces", "capacity.workspace",
        "capacity.temp", "git.worktree", "toolchain.required",
        "containment.oci_smoke", "egress.policy", "model.connectivity",
        "model.runtime_fingerprint", "model.qualification",
        "embedding.connectivity", "lsp.java", "models.role_independence",
        "release.integrity", "runtime.fixed_guarantees",
    ]


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


def test_json_requires_production_mode(tmp_path):
    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=AppConfig()):
        result = runner.invoke(main, ["doctor", "--json"])
    assert result.exit_code == 2
    assert "--json is supported with --production" in result.output


@pytest.mark.skipif(not _docker_ready(), reason="docker daemon not reachable")
def test_real_oci_containment_smoke_has_network_and_root_filesystem_closed():
    evidence = probe_oci_runtime()
    assert evidence["runtime"] == "docker"
    assert evidence["server_version"]

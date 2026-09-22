"""Deterministic production deployment preflight.

Every check returns a stable identifier and structured evidence.  No model is
asked to judge readiness; endpoint calls only inspect deterministic health and
runtime metadata.  FAIL and UNAVAILABLE are blocking when ``required`` is true.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from kriya.config.config import PRODUCTION_FIXED_RUNTIME_GUARANTEES, AppConfig

MIN_FREE_BYTES = 1 * 1024 * 1024 * 1024


class CheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class DoctorCheck:
    id: str
    status: CheckStatus
    required: bool
    evidence: Dict[str, Any]
    remediation: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class ProductionDoctorReport:
    schema_version: int
    production_ready: bool
    checks: Tuple[DoctorCheck, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "production_ready": self.production_ready,
            "checks": [check.to_dict() for check in self.checks],
        }


def _check(
    check_id: str,
    status: CheckStatus,
    *,
    required: bool = True,
    evidence: Optional[Dict[str, Any]] = None,
    remediation: str = "No remediation required.",
) -> DoctorCheck:
    return DoctorCheck(check_id, status, required, evidence or {}, remediation)


def _json_request(
    url: str,
    *,
    api_key: str = "",
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url=url, headers=headers, data=data)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.getcode() != 200:
            raise RuntimeError(f"HTTP {response.getcode()}")
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("endpoint response was not a JSON object")
    return decoded


def probe_llm_runtime(cfg: AppConfig) -> Dict[str, Any]:
    """Return connectivity plus exact native metadata when the endpoint exposes it."""
    models_url = f"{cfg.llm.base_url.rstrip('/')}/models"
    listing = _json_request(models_url, api_key=cfg.llm.api_key)
    models = [item for item in listing.get("data", []) if isinstance(item, dict)]
    selected = next((item for item in models if item.get("id") == cfg.llm.model), None)

    native_metadata = None
    parsed = urllib.parse.urlsplit(cfg.llm.base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        native_root = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path[:-3], "", ""))
        try:
            tags = _json_request(f"{native_root.rstrip('/')}/api/tags")
            native_models = [item for item in tags.get("models", []) if isinstance(item, dict)]
            native_selected = next(
                (
                    item for item in native_models
                    if str(item.get("name") or item.get("model") or "").casefold()
                    == cfg.llm.model.casefold()
                ),
                None,
            )
            shown = _json_request(
                f"{native_root.rstrip('/')}/api/show",
                payload={"model": cfg.llm.model},
            )
            # These fields identify the served artifact/runtime contract. Avoid
            # hashing response prose that can change without changing runtime.
            native_metadata = {
                key: shown[key]
                for key in ("modified_at", "details", "model_info", "capabilities", "parameters")
                if key in shown
            }
            if native_selected and native_selected.get("digest"):
                native_metadata["digest"] = native_selected["digest"]
        except Exception:
            native_metadata = None

    fingerprint = None
    if selected is not None and native_metadata and native_metadata.get("digest"):
        material = {
            "model": cfg.llm.model,
            "endpoint_model": selected,
            "native_metadata": native_metadata,
        }
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "models": [item.get("id") for item in models],
        "selected_model": selected,
        "native_metadata": native_metadata,
        "fingerprint": fingerprint,
    }


def probe_embedding(cfg: AppConfig) -> Dict[str, Any]:
    import asyncio

    from kriya.memory.vector import OllamaEmbeddingClient

    vector = asyncio.run(
        OllamaEmbeddingClient(
            base_url=cfg.embedding.base_url,
            model=cfg.embedding.model,
        ).get_embedding("kriya production doctor")
    )
    if not vector or not any(value != 0.0 for value in vector):
        raise RuntimeError("embedding endpoint returned an empty or all-zero vector")
    return {"model": cfg.embedding.model, "dimensions": len(vector)}


def probe_oci_runtime() -> Dict[str, Any]:
    docker = shutil.which("docker")
    if not docker:
        raise FileNotFoundError("docker CLI not found")
    info = subprocess.run(
        [docker, "info", "--format", "{{json .ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if info.returncode != 0:
        raise RuntimeError(info.stderr.strip() or "docker daemon is not reachable")
    image = os.environ.get("KRIYA_OCI_IMAGE", "debian:bookworm-slim")
    smoke = subprocess.run(
        [
            docker, "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", image,
            "/bin/sh", "-c", "test ! -w / && test -w /tmp && printf KRIYA_OCI_OK",
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if smoke.returncode != 0 or smoke.stdout != "KRIYA_OCI_OK":
        raise RuntimeError(smoke.stderr.strip() or "contained smoke command failed")
    return {"runtime": "docker", "server_version": info.stdout.strip().strip('"'), "image": image}


def _writable_probe(path: str) -> Dict[str, Any]:
    os.makedirs(path, exist_ok=True)
    fd, probe = tempfile.mkstemp(prefix=".kriya-doctor-", dir=path)
    try:
        os.write(fd, b"kriya")
        os.fsync(fd)
    finally:
        os.close(fd)
        os.unlink(probe)
    return {"path": os.path.realpath(path), "writable": True}


def _toolchain_requirements(workspace: str) -> List[Tuple[str, Iterable[str]]]:
    requirements: List[Tuple[str, Iterable[str]]] = [("python", (sys.executable,))]
    if os.path.exists(os.path.join(workspace, "pom.xml")):
        requirements += [("java", ("java",)), ("maven", ("mvn", "mvnw"))]
    if any(os.path.exists(os.path.join(workspace, name)) for name in ("build.gradle", "build.gradle.kts")):
        requirements += [("java", ("java",)), ("gradle", ("gradle", "gradlew"))]
    if os.path.exists(os.path.join(workspace, "package.json")):
        requirements.append(("npm", ("npm",)))
    if any(Path(workspace).glob("*.csproj")) or any(Path(workspace).glob("*.sln")):
        requirements.append(("dotnet", ("dotnet",)))
    return requirements


def _resolve_tool(workspace: str, candidates: Iterable[str]) -> Optional[str]:
    for candidate in candidates:
        if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
        local = os.path.join(workspace, candidate)
        if os.path.isfile(local) and os.access(local, os.X_OK):
            return local
        found = shutil.which(candidate)
        if found:
            return found
    return None


def probe_release_integrity() -> Dict[str, Any]:
    """Reuse PRD-002's source check or installed-wheel RECORD, as applicable."""
    from kriya.distribution import REQUIRED_RUNTIME_FILES, check_distribution

    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "pyproject.toml").is_file():
        missing = check_distribution(source_root)
        return {"kind": "source", "root": str(source_root), "missing": missing}

    from importlib.metadata import distribution

    installed = distribution("kriya")
    installed_files = {str(path) for path in (installed.files or ())}
    missing = [path for path in REQUIRED_RUNTIME_FILES if path not in installed_files]
    if not installed.read_text("RECORD"):
        missing.append("kriya-*.dist-info/RECORD")
    return {
        "kind": "installed-wheel",
        "distribution_version": installed.version,
        "missing": missing,
    }


def run_production_doctor(cfg: AppConfig, workspace_path: str) -> ProductionDoctorReport:
    workspace = os.path.realpath(workspace_path)
    checks: List[DoctorCheck] = []

    checks.append(_check(
        "profile.production",
        CheckStatus.PASS if cfg.runtime_profile == "production" else CheckStatus.FAIL,
        evidence={"runtime_profile": cfg.runtime_profile},
        remediation="Load an operator-approved configuration with runtime_profile: production.",
    ))

    plugin_dir = os.path.realpath(cfg.plugins.directory)
    core_files = [os.path.join(plugin_dir, "core_tools", name) for name in ("__init__.py", "validation_tool.py")]
    core_enabled = not cfg.plugins.enabled or "core_tools" in cfg.plugins.enabled
    core_ok = core_enabled and all(os.path.isfile(path) for path in core_files)
    checks.append(_check(
        "plugins.core_tools",
        CheckStatus.PASS if core_ok else CheckStatus.FAIL,
        evidence={"directory": plugin_dir, "enabled": core_enabled, "files_present": all(os.path.isfile(p) for p in core_files)},
        remediation="Install the complete Kriya distribution and enable core_tools.",
    ))

    try:
        from kriya.control.run_ownership import acquire_run_lock
        from kriya.control.workspace_identity import workspace_identity
        identity = workspace_identity(workspace)
        with acquire_run_lock(workspace, run_id="production-doctor"):
            pass
        checks.append(_check("workspace.identity_lock", CheckStatus.PASS, evidence={"identity": identity}))
    except Exception as error:
        checks.append(_check(
            "workspace.identity_lock", CheckStatus.FAIL,
            evidence={"error": str(error)},
            remediation="Use a writable POSIX workspace and wait for the current mutating Kriya run to finish.",
        ))

    for check_id, path in (
        ("persistence.checkpoints", os.path.join(workspace, ".kriya", "checkpoints")),
        ("persistence.traces", os.path.realpath(cfg.paths.logs)),
    ):
        try:
            checks.append(_check(check_id, CheckStatus.PASS, evidence=_writable_probe(path)))
        except Exception as error:
            checks.append(_check(
                check_id, CheckStatus.FAIL, evidence={"path": path, "error": str(error)},
                remediation=f"Make {path} writable with durable storage semantics.",
            ))

    for check_id, path in (("capacity.workspace", workspace), ("capacity.temp", tempfile.gettempdir())):
        try:
            free = shutil.disk_usage(path).free
            checks.append(_check(
                check_id,
                CheckStatus.PASS if free >= MIN_FREE_BYTES else CheckStatus.FAIL,
                evidence={"path": os.path.realpath(path), "free_bytes": free, "minimum_bytes": MIN_FREE_BYTES},
                remediation=f"Free at least {MIN_FREE_BYTES} bytes on the filesystem containing {path}.",
            ))
        except Exception as error:
            checks.append(_check(check_id, CheckStatus.UNAVAILABLE, evidence={"error": str(error)}, remediation="Make filesystem capacity queryable."))

    try:
        git = shutil.which("git")
        if not git:
            raise FileNotFoundError("git executable not found")
        inside = subprocess.run([git, "rev-parse", "--is-inside-work-tree"], cwd=workspace, capture_output=True, text=True, timeout=5)
        worktrees = subprocess.run([git, "worktree", "list", "--porcelain"], cwd=workspace, capture_output=True, text=True, timeout=5)
        if inside.returncode != 0 or inside.stdout.strip() != "true" or worktrees.returncode != 0:
            raise RuntimeError(inside.stderr.strip() or worktrees.stderr.strip() or "Git worktree support unavailable")
        checks.append(_check("git.worktree", CheckStatus.PASS, evidence={"git": git, "worktree_entries": worktrees.stdout.count("worktree ")}))
    except Exception as error:
        checks.append(_check("git.worktree", CheckStatus.FAIL, evidence={"error": str(error)}, remediation="Install Git and run doctor from a Git worktree."))

    missing_tools = []
    resolved_tools = {}
    for name, candidates in _toolchain_requirements(workspace):
        resolved = _resolve_tool(workspace, candidates)
        if resolved:
            resolved_tools[name] = resolved
        else:
            missing_tools.append(name)
    checks.append(_check(
        "toolchain.required",
        CheckStatus.PASS if not missing_tools else CheckStatus.FAIL,
        evidence={"resolved": resolved_tools, "missing": sorted(set(missing_tools))},
        remediation="Install the build tools declared by the workspace manifests.",
    ))

    try:
        checks.append(_check("containment.oci_smoke", CheckStatus.PASS, evidence=probe_oci_runtime()))
    except FileNotFoundError as error:
        checks.append(_check("containment.oci_smoke", CheckStatus.UNAVAILABLE, evidence={"error": str(error)}, remediation="Install and start Docker, then pre-pull the configured KRIYA_OCI_IMAGE."))
    except Exception as error:
        checks.append(_check("containment.oci_smoke", CheckStatus.FAIL, evidence={"error": str(error)}, remediation="Start Docker and verify the configured image supports read-only, no-network execution."))

    registry_hosts = cfg.autonomy.acquisition_registry_hosts
    egress_ok = cfg.autonomy.egress_policy == "local_only" and bool(registry_hosts)
    checks.append(_check(
        "egress.policy",
        CheckStatus.PASS if egress_ok else CheckStatus.FAIL,
        evidence={"policy": cfg.autonomy.egress_policy, "registry_hosts": registry_hosts},
        remediation="Use local_only model egress and configure an explicit non-empty exact registry hostname allowlist.",
    ))

    runtime_probe: Dict[str, Any] = {}
    try:
        runtime_probe = probe_llm_runtime(cfg)
        selected = runtime_probe.get("selected_model")
        checks.append(_check(
            "model.connectivity",
            CheckStatus.PASS if selected is not None else CheckStatus.FAIL,
            evidence={"model": cfg.llm.model, "available_models": runtime_probe.get("models", [])},
            remediation="Start the configured local model endpoint and load the exact configured model.",
        ))
    except Exception as error:
        checks.append(_check("model.connectivity", CheckStatus.UNAVAILABLE, evidence={"error": str(error)}, remediation="Start the configured local model endpoint."))

    fingerprint = runtime_probe.get("fingerprint")
    checks.append(_check(
        "model.runtime_fingerprint",
        CheckStatus.PASS if fingerprint else CheckStatus.UNAVAILABLE,
        evidence={"model": cfg.llm.model, "fingerprint": fingerprint, "native_metadata": bool(runtime_probe.get("native_metadata"))},
        remediation="Use a local endpoint exposing exact model metadata (Ollama /api/show) so the served artifact can be fingerprinted.",
    ))

    from kriya.core.model_capabilities import resolve_model_capability_profile
    capability = resolve_model_capability_profile(cfg, cfg.llm.model)
    qualified = capability.source == "known_production_profile"
    checks.append(_check(
        "model.qualification",
        CheckStatus.PASS if qualified else CheckStatus.FAIL,
        evidence={"model": cfg.llm.model, "qualification_source": capability.source},
        remediation="Qualify this exact model identity through the production model campaign before use.",
    ))

    try:
        checks.append(_check("embedding.connectivity", CheckStatus.PASS, evidence=probe_embedding(cfg)))
    except Exception as error:
        checks.append(_check("embedding.connectivity", CheckStatus.UNAVAILABLE, evidence={"error": str(error)}, remediation="Start the configured embedding endpoint and pull its model."))

    from kriya.tools.lsp import find_jdtls
    try:
        jdtls = find_jdtls()
        lsp_evidence = {"path": jdtls, "policy_required": False}
    except Exception as error:
        jdtls = None
        lsp_evidence = {"path": None, "policy_required": False, "error": str(error)}
    checks.append(_check(
        "lsp.java",
        CheckStatus.PASS if jdtls else CheckStatus.WARN,
        required=False,
        evidence=lsp_evidence,
        remediation="Install jdtls to enable Java LSP grounding; current production policy does not require it.",
    ))

    role_models = {"primary": cfg.llm.model}
    for role in ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance"):
        binding = getattr(cfg.agent_llms, role)
        if binding.llm is not None:
            role_models[role] = binding.llm.model
    independent = len(set(role_models.values())) > 1
    checks.append(_check(
        "models.role_independence",
        CheckStatus.PASS if independent else CheckStatus.WARN,
        required=False,
        evidence={"role_models": role_models, "independent": independent, "policy_required": False},
        remediation="Configure separately qualified role models if independent review is desired.",
    ))

    try:
        integrity = probe_release_integrity()
        missing = integrity["missing"]
        checks.append(_check(
            "release.integrity",
            CheckStatus.PASS if not missing else CheckStatus.FAIL,
            evidence=integrity,
            remediation="Install a PRD-002-complete release artifact containing every required runtime and release file.",
        ))
    except Exception as error:
        checks.append(_check("release.integrity", CheckStatus.UNAVAILABLE, evidence={"error": str(error)}, remediation="Install a verifiable Kriya release artifact."))

    checks.append(_check(
        "runtime.fixed_guarantees",
        CheckStatus.PASS,
        evidence={"guarantees": sorted(PRODUCTION_FIXED_RUNTIME_GUARANTEES)},
    ))

    ready = not any(
        check.required and check.status in (CheckStatus.FAIL, CheckStatus.UNAVAILABLE)
        for check in checks
    )
    return ProductionDoctorReport(schema_version=1, production_ready=ready, checks=tuple(checks))


def render_production_report(report: ProductionDoctorReport) -> str:
    lines = ["=== Kriya Production Doctor ==="]
    for check in report.checks:
        lines.append(f"[{check.status.value}] {check.id}")
        lines.append(f"  evidence: {json.dumps(check.evidence, sort_keys=True)}")
        if check.remediation and check.status is not CheckStatus.PASS:
            lines.append(f"  remediation: {check.remediation}")
    lines.append(f"PRODUCTION_READY={str(report.production_ready).lower()}")
    return "\n".join(lines)

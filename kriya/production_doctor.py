"""Deterministic production deployment preflight.

Every check returns a stable identifier and structured evidence.  No model is
asked to judge readiness; endpoint calls only inspect deterministic health and
runtime metadata.  FAIL and UNAVAILABLE are blocking when ``required`` is true.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

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
    """Connectivity plus the PRD-013 exact runtime fingerprint of the primary
    model, probed fresh (never from the per-process cache)."""
    from kriya.core.llm import EgressViolationError, is_local_url
    from kriya.core.model_runtime import configured_context_window, kriya_protocol_identity, probe_model_runtime

    if cfg.autonomy.egress_policy == "local_only" and not is_local_url(cfg.llm.base_url):
        # PRD-012: never probe (or send the API key to) a refused endpoint.
        raise EgressViolationError(f"llm.base_url {cfg.llm.base_url!r} is not local under local_only; not probed")
    models_url = f"{cfg.llm.base_url.rstrip('/')}/models"
    listing = _json_request(models_url, api_key=cfg.llm.api_key)
    models = [item for item in listing.get("data", []) if isinstance(item, dict)]
    selected = next((item for item in models if item.get("id") == cfg.llm.model), None)

    runtime = probe_model_runtime(
        base_url=cfg.llm.base_url, model=cfg.llm.model, api_key=cfg.llm.api_key,
        egress_policy=cfg.autonomy.egress_policy,
        configured_context=configured_context_window(cfg.llm.extra_body),
        kriya_protocol=kriya_protocol_identity(cfg, cfg.llm.model),
        transport=lambda url, payload, api_key: _json_request(url, api_key=api_key, payload=payload),
    )
    return {
        "models": [item.get("id") for item in models],
        "selected_model": selected,
        "runtime": runtime,
        "fingerprint": runtime.digest if runtime.exact else None,
    }


def probe_embedding(cfg: AppConfig) -> Dict[str, Any]:
    import asyncio

    from kriya.memory.vector import OllamaEmbeddingClient

    vector = asyncio.run(
        OllamaEmbeddingClient(
            base_url=cfg.embedding.base_url,
            model=cfg.embedding.model,
            egress_policy=cfg.autonomy.egress_policy,
        ).get_embedding("kriya production doctor")
    )
    if not vector or not any(value != 0.0 for value in vector):
        raise RuntimeError("embedding endpoint returned an empty or all-zero vector")
    return {"model": cfg.embedding.model, "dimensions": len(vector)}


class _Docker:
    """The Docker CLI and daemon, probed once per doctor run."""

    def __init__(self) -> None:
        self._resolved: Optional[Tuple[Optional[str], Dict[str, Any]]] = None

    def resolve(self) -> Tuple[Optional[str], Dict[str, Any]]:
        """(docker path or None, evidence). None means UNAVAILABLE."""
        if self._resolved is None:
            docker = shutil.which("docker")
            if not docker:
                self._resolved = (None, {"error": "docker CLI not found"})
            else:
                try:
                    info = subprocess.run(
                        [docker, "info", "--format", "{{json .ServerVersion}}"],
                        capture_output=True, text=True, timeout=10,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    self._resolved = (None, {"error": f"docker daemon did not answer: {error}"})
                    return self._resolved
                if info.returncode != 0:
                    self._resolved = (None, {"error": info.stderr.strip() or "docker daemon is not reachable"})
                else:
                    self._resolved = (docker, {"runtime": "docker", "server_version": info.stdout.strip().strip('"')})
        return self._resolved


# What the doctor's smoke command prints from inside the container, one
# KEY=VALUE per line. Every assertion is something the OCI backend itself
# enforces for a DENIED profile: no route off the loopback and no
# non-loopback interface up (`--network none`; the kernel's fallback tunnel
# devices exist in every namespace but stay down), no effective capabilities
# (`--cap-drop ALL`), and `no-new-privileges`. The root filesystem is
# deliberately not asserted read-only: production containers are not started
# with `--read-only`.
_SMOKE_SCRIPT = (
    "printf 'IPV4_ROUTES=%s\\n' \"$(tail -n +2 /proc/net/route | grep -c .)\"; "
    "printf 'IPV6_NON_LOOPBACK_ROUTES=%s\\n' \"$(if [ -r /proc/net/ipv6_route ]; "
    "then grep -cv ' lo$' /proc/net/ipv6_route; else echo 0; fi)\"; "
    "printf 'UP_INTERFACES=%s\\n' \"$(for i in /sys/class/net/*/; do n=$(basename \"$i\"); "
    "[ \"$n\" != lo ] && [ \"$(cat \"$i/operstate\")\" = up ] && printf '%s ' \"$n\"; done)\"; "
    "printf 'CAPEFF=%s\\n' \"$(grep '^CapEff:' /proc/self/status | cut -f2)\"; "
    "printf 'NO_NEW_PRIVS=%s\\n' \"$(grep '^NoNewPrivs:' /proc/self/status | cut -f2)\""
)


def _kriya_containers(docker: str) -> List[str]:
    listing = subprocess.run(
        [docker, "ps", "-a", "--filter", "name=kriya-oci-", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=15,
    )
    return sorted(name for name in listing.stdout.split() if name.startswith("kriya-oci-"))


def probe_oci_containment(cfg: AppConfig, docker: str, toolchain: Optional[Any]) -> Dict[str, Any]:
    """Run one command through the configured production containment backend.

    Uses the same backend resolution and the same ProcessController spawn
    point real verification uses, against a scratch directory - never the
    workspace. The image must already be present: `docker run` would
    otherwise pull it, and the doctor never changes the environment."""
    from kriya.tools.containment import (
        ContainmentProfile,
        NetworkAuthority,
        TrustClass,
        resolve_containment_backend,
    )
    from kriya.tools.containment_oci import _inspect_image_digest, _select_image_and_cache_mount
    from kriya.tools.process import ProcessController

    command = ["/bin/sh", "-c", _SMOKE_SCRIPT]
    image = toolchain.containment_image if toolchain is not None else _select_image_and_cache_mount(command)[0]
    if _inspect_image_digest(docker, image) is None:
        raise FileNotFoundError(f"containment image {image!r} is not present locally; run: docker pull {image}")

    backend = resolve_containment_backend(cfg.autonomy.containment_backend)
    scratch = tempfile.mkdtemp(prefix="kriya-doctor-oci-")
    before = set(_kriya_containers(docker))
    try:
        profile = ContainmentProfile(
            trust_class=TrustClass.UNTRUSTED_EXECUTION,
            workspace_path=scratch,
            network=NetworkAuthority.DENIED,
            toolchain_identity=toolchain,
        )
        result = ProcessController().run(
            command, cwd=scratch, timeout=60,
            containment_profile=profile, containment_backend=backend,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    leftover = sorted(set(_kriya_containers(docker)) - before)

    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    evidence = {
        "backend": backend.name,
        "image": image,
        "returncode": result.returncode,
        "ipv4_routes": fields.get("IPV4_ROUTES"),
        "ipv6_non_loopback_routes": fields.get("IPV6_NON_LOOPBACK_ROUTES"),
        "up_interfaces": fields.get("UP_INTERFACES", "").split(),
        "effective_capabilities": fields.get("CAPEFF"),
        "no_new_privileges": fields.get("NO_NEW_PRIVS"),
        "leftover_containers": leftover,
        "toolchain_identity": result.toolchain_identity,
    }
    violations = []
    if result.returncode != 0 or result.timeout:
        violations.append(f"smoke command failed: {result.stderr.strip()[:300]}")
    if fields.get("IPV4_ROUTES") != "0" or fields.get("IPV6_NON_LOOPBACK_ROUTES") != "0" or evidence["up_interfaces"]:
        violations.append("container has a network path off the loopback")
    if fields.get("CAPEFF") != "0000000000000000":
        violations.append("container retains Linux capabilities")
    if fields.get("NO_NEW_PRIVS") != "1":
        violations.append("no-new-privileges is not in effect")
    if leftover:
        violations.append("containment cleanup left containers behind")
    if violations:
        raise ContainmentSmokeError("; ".join(violations), evidence)
    return evidence


class ContainmentSmokeError(RuntimeError):
    def __init__(self, message: str, evidence: Dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


def _nearest_existing_directory(path: str) -> str:
    current = os.path.realpath(path)
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def _store_probe(path: str, *, lock: bool = False) -> Dict[str, Any]:
    """Whether a persistent store at ``path`` can be written, without creating it.

    An existing directory gets a transient, fsynced, self-removing probe file
    (flocked too when ``lock``); a missing one is judged by write access on its
    nearest existing ancestor, where Kriya would create it on first use. The
    doctor never creates the store itself."""
    real = os.path.realpath(path)
    if os.path.isdir(real):
        fd, probe = tempfile.mkstemp(prefix=".kriya-doctor-", dir=real)
        try:
            os.write(fd, b"kriya")
            os.fsync(fd)
            if lock:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            os.unlink(probe)
        return {"path": real, "exists": True, "writable": True}
    if os.path.exists(real):
        raise NotADirectoryError(f"{real} exists and is not a directory")
    ancestor = _nearest_existing_directory(real)
    if not (os.path.isdir(ancestor) and os.access(ancestor, os.W_OK | os.X_OK)):
        raise PermissionError(f"{real} does not exist and cannot be created under {ancestor}")
    return {"path": real, "exists": False, "created_on_first_use_under": ancestor}


def _model_endpoints(cfg: AppConfig) -> Dict[str, str]:
    endpoints = {"llm": cfg.llm.base_url, "embedding": cfg.embedding.base_url}
    for index, fallback in enumerate(cfg.llm_chain):
        endpoints[f"llm_chain[{index}]"] = fallback.base_url
    for role in _ROLES:
        binding = getattr(cfg.agent_llms, role)
        if binding.llm is not None:
            endpoints[f"agent_llms.{role}.llm"] = binding.llm.base_url
        for index, fallback in enumerate(binding.llm_chain):
            endpoints[f"agent_llms.{role}.llm_chain[{index}]"] = fallback.base_url
    return endpoints


_ROLES = ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance")

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


# Pinned, in report order. A report always carries exactly these IDs, whatever
# fails: a check that raises is reported under its own ID, never dropped.
PRODUCTION_DOCTOR_CHECK_IDS = (
    "profile.production",
    "plugins.core_tools",
    "workspace.identity_lock",
    "persistence.checkpoints",
    "persistence.traces",
    "persistence.logs",
    "capacity.workspace",
    "capacity.temp",
    "git.worktree",
    "isolation.candidate_worktree",
    "toolchain.required",
    "containment.oci_smoke",
    "containment.no_host_fallback",
    "egress.policy",
    "model.connectivity",
    "model.runtime_fingerprint",
    "model.qualification",
    "embedding.connectivity",
    "lsp.java",
    "models.role_independence",
    "semantic.precision_boundary",
    "release.integrity",
    "runtime.fixed_guarantees",
)
# The single check of the report `kriya doctor --production` emits when the
# configuration itself cannot be loaded, so --json output stays parseable.
CONFIG_LOAD_CHECK_ID = "config.load"

# Each fixed runtime guarantee (PRD-009) and the checks that verify it in this
# deployment. `runtime.fixed_guarantees` is derived from these, never asserted.
FIXED_GUARANTEE_EVIDENCE = {
    "candidate_isolation_fail_closed": ("git.worktree", "isolation.candidate_worktree"),
    "checkpoint_persistence": ("persistence.checkpoints",),
    "trace_persistence": ("persistence.traces",),
    "no_uncontained_host_fallback": ("containment.no_host_fallback", "containment.oci_smoke"),
}

CHECK_RAISED = "CHECK_RAISED"
RUNTIME_FINGERPRINT_NOT_COMPUTABLE = "RUNTIME_FINGERPRINT_NOT_COMPUTABLE"
RUNTIME_QUALIFICATION_BINDING_UNAVAILABLE = "RUNTIME_QUALIFICATION_BINDING_UNAVAILABLE"
MODEL_NOT_QUALIFIED = "MODEL_NOT_QUALIFIED"
QUALIFICATION_STALE = "QUALIFICATION_STALE"

_SEVERITY = {CheckStatus.PASS: 0, CheckStatus.WARN: 1, CheckStatus.UNAVAILABLE: 2, CheckStatus.FAIL: 3}


@dataclass
class _Context:
    cfg: AppConfig
    workspace: str
    docker: _Docker = field(default_factory=_Docker)
    runtime_probe: Dict[str, Any] = field(default_factory=dict)
    toolchain: Optional[Any] = None
    checks: Dict[str, DoctorCheck] = field(default_factory=dict)


def _check_profile(ctx: _Context) -> DoctorCheck:
    return _check(
        "profile.production",
        CheckStatus.PASS if ctx.cfg.runtime_profile == "production" else CheckStatus.FAIL,
        evidence={"runtime_profile": ctx.cfg.runtime_profile},
        remediation="Load an operator-approved configuration with runtime_profile: production.",
    )


def _check_core_plugins(ctx: _Context) -> DoctorCheck:
    plugin_dir = os.path.realpath(ctx.cfg.plugins.directory)
    core_files = [os.path.join(plugin_dir, "core_tools", name) for name in ("__init__.py", "validation_tool.py")]
    core_enabled = not ctx.cfg.plugins.enabled or "core_tools" in ctx.cfg.plugins.enabled
    present = all(os.path.isfile(path) for path in core_files)
    return _check(
        "plugins.core_tools",
        CheckStatus.PASS if core_enabled and present else CheckStatus.FAIL,
        evidence={"directory": plugin_dir, "enabled": core_enabled, "files_present": present},
        remediation="Install the complete Kriya distribution and enable core_tools.",
    )


def _check_identity_lock(ctx: _Context) -> DoctorCheck:
    """Read-only: the lock is probed, never taken, and its file never created."""
    from kriya.control.run_ownership import _lock_path, probe_run_lock
    from kriya.control.workspace_identity import workspace_identity

    remediation = "Use a writable POSIX workspace and wait for the current mutating Kriya run to finish."
    evidence: Dict[str, Any] = {"identity": workspace_identity(ctx.workspace)}
    holder = probe_run_lock(ctx.workspace)
    if holder is not None:
        evidence["held_by"] = holder
        return _check("workspace.identity_lock", CheckStatus.FAIL, evidence=evidence, remediation=remediation)
    evidence["lock_store"] = _store_probe(os.path.dirname(_lock_path(ctx.workspace)), lock=True)
    return _check("workspace.identity_lock", CheckStatus.PASS, evidence=evidence, remediation=remediation)


def _store_check(check_id: str, path: str) -> DoctorCheck:
    remediation = f"Make {path} writable with durable storage semantics."
    try:
        return _check(check_id, CheckStatus.PASS, evidence=_store_probe(path), remediation=remediation)
    except OSError as error:
        return _check(check_id, CheckStatus.FAIL, evidence={"path": path, "error": str(error)}, remediation=remediation)


def _check_checkpoints(ctx: _Context) -> DoctorCheck:
    from kriya.workflow.checkpoint import CHECKPOINT_DIR

    return _store_check("persistence.checkpoints", os.path.join(ctx.workspace, CHECKPOINT_DIR))


def _check_traces(ctx: _Context) -> DoctorCheck:
    """The trace database's state directory (KRIYA_STATE_DIR > paths.state >
    ~/.kriya/state), independent of the log directory: valid, creatable or
    writable, and an existing traces.db readable and writable. A traces.db
    left at the historical default location is a WARN with the explicit
    migration command."""
    from kriya.core.state_paths import (
        StateDirectoryError,
        legacy_trace_db_path,
        resolve_state_directory,
        trace_db_path,
    )

    remediation = (
        "Set paths.state (or KRIYA_STATE_DIR) to a writable directory, "
        "or make the default ~/.kriya/state writable."
    )
    try:
        state_dir, source = resolve_state_directory(ctx.cfg)
    except StateDirectoryError as error:
        return _check("persistence.traces", CheckStatus.FAIL, evidence={"error": str(error)}, remediation=remediation)
    trace_db = trace_db_path(ctx.cfg)
    evidence: Dict[str, Any] = {"source": source, "trace_db": trace_db}
    try:
        evidence.update(_store_probe(state_dir))
        if os.path.exists(trace_db) and not os.access(trace_db, os.R_OK | os.W_OK):
            raise PermissionError(f"{trace_db} exists and is not readable and writable")
    except OSError as error:
        return _check("persistence.traces", CheckStatus.FAIL,
                      evidence={**evidence, "path": state_dir, "error": str(error)}, remediation=remediation)
    legacy = legacy_trace_db_path(ctx.cfg)
    if legacy is not None:
        evidence["legacy_trace_db"] = legacy
        return _check(
            "persistence.traces", CheckStatus.WARN, evidence=evidence,
            remediation=(
                f"A legacy trace database exists at {legacy}. Run `kriya traces --migrate-legacy` to copy it "
                "to the canonical location (refused if one already exists; the legacy file is never removed)."
            ),
        )
    return _check("persistence.traces", CheckStatus.PASS, evidence=evidence, remediation=remediation)


def _check_logs(ctx: _Context) -> DoctorCheck:
    """The log directory Kriya would use (KRIYA_LOG_DIR > logging.directory >
    ~/.kriya/logs) is valid, and exists or can be created, and is writable,
    without the doctor creating it."""
    from kriya.core.logging_setup import LogDirectoryError, application_log_path, resolve_log_directory

    remediation = (
        "Set logging.directory (or KRIYA_LOG_DIR) to an absolute, writable directory, "
        "or make the default ~/.kriya/logs writable."
    )
    logging_cfg = ctx.cfg.logging
    base = {
        "file_enabled": logging_cfg.file_enabled,
        "run_file_enabled": logging_cfg.run_file_enabled,
    }
    try:
        log_dir, source = resolve_log_directory(ctx.cfg)
    except LogDirectoryError as error:
        return _check("persistence.logs", CheckStatus.FAIL, evidence={**base, "error": str(error)},
                      remediation=remediation)
    evidence = {**base, "source": source}
    try:
        evidence.update(_store_probe(log_dir))
        app_log = application_log_path(log_dir)
        if os.path.exists(app_log) and not os.access(app_log, os.W_OK):
            raise PermissionError(f"{app_log} exists and is not writable")
    except OSError as error:
        return _check("persistence.logs", CheckStatus.FAIL, evidence={**evidence, "path": log_dir, "error": str(error)},
                      remediation=remediation)
    if not (logging_cfg.file_enabled or logging_cfg.run_file_enabled):
        return _check("persistence.logs", CheckStatus.WARN, evidence=evidence,
                      remediation="Enable logging.file_enabled or logging.run_file_enabled to keep a durable log.")
    return _check("persistence.logs", CheckStatus.PASS, evidence=evidence, remediation=remediation)


def _capacity_check(check_id: str, path: str) -> DoctorCheck:
    free = shutil.disk_usage(path).free
    return _check(
        check_id,
        CheckStatus.PASS if free >= MIN_FREE_BYTES else CheckStatus.FAIL,
        evidence={"path": os.path.realpath(path), "free_bytes": free, "minimum_bytes": MIN_FREE_BYTES},
        remediation=f"Free at least {MIN_FREE_BYTES} bytes on the filesystem containing {path}.",
    )


def _check_workspace_capacity(ctx: _Context) -> DoctorCheck:
    return _capacity_check("capacity.workspace", ctx.workspace)


def _check_temp_capacity(ctx: _Context) -> DoctorCheck:
    return _capacity_check("capacity.temp", tempfile.gettempdir())


def _check_git_worktree(ctx: _Context) -> DoctorCheck:
    remediation = "Install Git and run doctor from a Git worktree."
    git = shutil.which("git")
    if not git:
        return _check("git.worktree", CheckStatus.FAIL, evidence={"error": "git executable not found"}, remediation=remediation)
    inside = subprocess.run([git, "rev-parse", "--is-inside-work-tree"], cwd=ctx.workspace, capture_output=True, text=True, timeout=5)
    worktrees = subprocess.run([git, "worktree", "list", "--porcelain"], cwd=ctx.workspace, capture_output=True, text=True, timeout=5)
    if inside.returncode != 0 or inside.stdout.strip() != "true" or worktrees.returncode != 0:
        error = inside.stderr.strip() or worktrees.stderr.strip() or "Git worktree support unavailable"
        return _check("git.worktree", CheckStatus.FAIL, evidence={"error": error}, remediation=remediation)
    return _check("git.worktree", CheckStatus.PASS, evidence={"git": git, "worktree_entries": worktrees.stdout.count("worktree ")})


def _check_candidate_isolation(ctx: _Context) -> DoctorCheck:
    """The real candidate-isolation mechanism, run on a throwaway repository.

    This proves the mechanism works in this environment. That generation
    refuses to run in the application workspace when isolation fails is a code
    property proven by tests, not by this probe."""
    from kriya.workflow.worktree import create_git_worktree

    scratch = tempfile.mkdtemp(prefix="kriya-doctor-isolation-")
    try:
        worktree = create_git_worktree(scratch)
        isolated = os.path.realpath(worktree) != os.path.realpath(scratch) and os.path.isdir(worktree)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return _check(
        "isolation.candidate_worktree",
        CheckStatus.PASS if isolated else CheckStatus.FAIL,
        evidence={"mechanism": "create_git_worktree", "probe": "throwaway repository", "isolated": isolated},
        remediation="Make Git worktree creation work for Kriya's user and temporary directory.",
    )


def _check_toolchain(ctx: _Context) -> DoctorCheck:
    """The project's toolchain as production containment will run it: stack
    from PolymorphicValidator, versioned image from PRD-011's resolver, the
    runtime proven inside that image. Host tools are irrelevant here."""
    from kriya.tools.containment_oci import _attest_toolchain_image, _inspect_image_digest
    from kriya.tools.toolchain_identity import ToolchainMismatchError, ToolchainResolutionError
    from kriya.tools.validate import PolymorphicValidator

    remediation = "Declare a production-supported toolchain and pre-pull its containment image."
    try:
        validator = PolymorphicValidator(ctx.workspace, autonomy_cfg=ctx.cfg.autonomy)
        identity = resolve_toolchain_for(validator)
    except ToolchainResolutionError as error:
        return _check("toolchain.required", CheckStatus.FAIL, evidence={"error": str(error)}, remediation=remediation)
    evidence: Dict[str, Any] = {"stack": validator.stack}
    if identity is None:
        if validator.stack == "unknown":
            evidence["reason"] = "no project toolchain detected; nothing to verify yet"
            return _check("toolchain.required", CheckStatus.WARN, evidence=evidence, remediation=remediation)
        evidence["reason"] = f"no production containment toolchain profile exists for stack {validator.stack!r}"
        return _check("toolchain.required", CheckStatus.UNAVAILABLE, evidence=evidence, remediation=remediation)
    evidence["required"] = identity.to_dict()
    docker, docker_evidence = ctx.docker.resolve()
    if docker is None:
        evidence.update(docker_evidence)
        return _check("toolchain.required", CheckStatus.UNAVAILABLE, evidence=evidence, remediation="Install and start Docker.")
    if _inspect_image_digest(docker, identity.containment_image) is None:
        evidence["error"] = f"image {identity.containment_image!r} is not present locally"
        return _check(
            "toolchain.required", CheckStatus.UNAVAILABLE, evidence=evidence,
            remediation=f"Run: docker pull {identity.containment_image}",
        )
    try:
        attested = _attest_toolchain_image(docker, identity.containment_image, identity, allow_pull=False)
    except ToolchainMismatchError as error:
        evidence["error"] = str(error)
        return _check("toolchain.required", CheckStatus.FAIL, evidence=evidence, remediation=remediation)
    ctx.toolchain = attested
    evidence["attested"] = attested.to_dict()
    # pip ships inside the Python image; Maven/Gradle are separate tools whose
    # version PRD-011's attestation must observe to count as proven.
    build_tool_attested = (
        attested.build_tool not in ("maven", "gradle") or attested.observed_build_tool_version is not None
    )
    evidence["build_tool_attested"] = build_tool_attested
    if not build_tool_attested:
        evidence["reason"] = f"{attested.build_tool} version is not attested inside the image"
        return _check("toolchain.required", CheckStatus.WARN, evidence=evidence, remediation=remediation)
    return _check("toolchain.required", CheckStatus.PASS, evidence=evidence, remediation=remediation)


def resolve_toolchain_for(validator: Any) -> Optional[Any]:
    """The validator's own resolved identity, or - when contained execution is
    not required and so it resolved none - the identity it would require."""
    from kriya.tools.toolchain_identity import resolve_toolchain_identity

    if validator.toolchain_identity is not None:
        return validator.toolchain_identity
    return resolve_toolchain_identity(validator.workspace_path, validator.stack)


def _check_oci_smoke(ctx: _Context) -> DoctorCheck:
    docker, docker_evidence = ctx.docker.resolve()
    if docker is None:
        return _check(
            "containment.oci_smoke", CheckStatus.UNAVAILABLE, evidence=docker_evidence,
            remediation="Install and start Docker.",
        )
    remediation = "Configure containment_backend: oci and verify Docker can run the containment image."
    try:
        evidence = probe_oci_containment(ctx.cfg, docker, ctx.toolchain)
    except FileNotFoundError as error:
        return _check("containment.oci_smoke", CheckStatus.UNAVAILABLE, evidence={"error": str(error)}, remediation=str(error))
    except ContainmentSmokeError as error:
        return _check("containment.oci_smoke", CheckStatus.FAIL, evidence={**error.evidence, "error": str(error)}, remediation=remediation)
    except Exception as error:
        return _check("containment.oci_smoke", CheckStatus.FAIL, evidence={"error": f"{type(error).__name__}: {error}"}, remediation=remediation)
    evidence.update(docker_evidence)
    return _check("containment.oci_smoke", CheckStatus.PASS, evidence=evidence)


def _check_no_host_fallback(ctx: _Context) -> DoctorCheck:
    """Required containment can never quietly become host execution: it is
    required by configuration, the configured backend is a real one, an
    unknown backend name is refused, and the null backend refuses a profile
    that needs isolation."""
    from kriya.tools.containment import (
        BackendUnavailableError,
        ContainmentProfile,
        NetworkAuthority,
        NullContainmentBackend,
        TrustClass,
        resolve_containment_backend,
    )

    autonomy = ctx.cfg.autonomy
    evidence: Dict[str, Any] = {
        "contained_execution_required": autonomy.contained_execution_required,
        "mcp_servers": sorted(ctx.cfg.mcp),
        "mcp_contained_execution_required": autonomy.mcp_contained_execution_required,
        "containment_backend": autonomy.containment_backend,
    }
    problems = []
    if not autonomy.contained_execution_required:
        problems.append("contained execution is not required")
    if ctx.cfg.mcp and not autonomy.mcp_contained_execution_required:
        problems.append("MCP servers are configured without required containment")
    try:
        configured = resolve_containment_backend(autonomy.containment_backend)
        evidence["configured_backend"] = configured.name
        if isinstance(configured, NullContainmentBackend):
            problems.append("the configured backend provides no isolation")
    except BackendUnavailableError as error:
        problems.append(str(error))
    try:
        resolve_containment_backend("kriya-doctor-unknown-backend")
        problems.append("an unknown backend name resolved instead of being refused")
    except BackendUnavailableError:
        evidence["unknown_backend_refused"] = True
    scratch = tempfile.mkdtemp(prefix="kriya-doctor-null-")
    try:
        NullContainmentBackend().prepare(
            ContainmentProfile(
                trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=scratch,
                network=NetworkAuthority.DENIED,
            ),
            ["true"],
        )
        problems.append("the null backend accepted an isolation-required profile")
    except BackendUnavailableError:
        evidence["null_backend_refuses_isolation_profile"] = True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    evidence["problems"] = problems
    return _check(
        "containment.no_host_fallback",
        CheckStatus.FAIL if problems else CheckStatus.PASS,
        evidence=evidence,
        remediation="Use runtime_profile: production with containment_backend: oci.",
    )


def _check_egress(ctx: _Context) -> DoctorCheck:
    from kriya.core.llm import is_local_url

    registry_hosts = ctx.cfg.autonomy.acquisition_registry_hosts
    endpoints = _model_endpoints(ctx.cfg)
    non_local = sorted(name for name, url in endpoints.items() if not is_local_url(url))
    ok = ctx.cfg.autonomy.egress_policy == "local_only" and bool(registry_hosts) and not non_local
    return _check(
        "egress.policy",
        CheckStatus.PASS if ok else CheckStatus.FAIL,
        evidence={
            "policy": ctx.cfg.autonomy.egress_policy,
            "registry_hosts": registry_hosts,
            "model_endpoints": endpoints,
            "non_local_endpoints": non_local,
        },
        remediation=(
            "Use local_only model egress, point every model endpoint at a loopback/private/.local "
            "host, and configure an explicit non-empty exact registry hostname allowlist."
        ),
    )


def _check_model_connectivity(ctx: _Context) -> DoctorCheck:
    try:
        ctx.runtime_probe = probe_llm_runtime(ctx.cfg)
    except Exception as error:
        return _check(
            "model.connectivity", CheckStatus.UNAVAILABLE, evidence={"error": str(error)},
            remediation="Start the configured local model endpoint.",
        )
    return _check(
        "model.connectivity",
        CheckStatus.PASS if ctx.runtime_probe.get("selected_model") is not None else CheckStatus.FAIL,
        evidence={"model": ctx.cfg.llm.model, "available_models": ctx.runtime_probe.get("models", [])},
        remediation="Start the configured local model endpoint and load the exact configured model.",
    )


def _check_runtime_fingerprint(ctx: _Context) -> DoctorCheck:
    """PRD-013: PASS when the primary model's runtime is exact (artifact
    digest and provider version reported); every component is evidence."""
    runtime = ctx.runtime_probe.get("runtime")
    if runtime is not None and runtime.exact:
        return _check("model.runtime_fingerprint", CheckStatus.PASS, evidence={
            "model": ctx.cfg.llm.model, "fingerprint": runtime.digest, "runtime": runtime.to_dict(),
        })
    return _check(
        "model.runtime_fingerprint",
        CheckStatus.UNAVAILABLE,
        evidence={
            "model": ctx.cfg.llm.model,
            "fingerprint": None,
            "runtime": runtime.to_dict() if runtime is not None else None,
            "reason_code": RUNTIME_FINGERPRINT_NOT_COMPUTABLE,
        },
        remediation=(
            "Use a local endpoint exposing exact model metadata (Ollama /api/version, /api/tags, /api/show) "
            "so the served artifact can be fingerprinted."
        ),
    )


def _check_qualification(ctx: _Context) -> DoctorCheck:
    """PRD-014: every production role's every callable model (its binding
    and escalation chain) must have a CURRENT qualification record for its
    exact runtime covering the capabilities Kriya uses with it. A model name
    is never a qualification. FAIL when a determinable runtime lacks it;
    UNAVAILABLE when a runtime cannot be identified exactly."""
    from kriya.core.model_capabilities import is_campaign_named_model
    from kriya.core.model_qualification import (
        MISSING,
        NOT_EXACT,
        NOT_QUALIFIED,
        QUALIFIED,
        STALE,
        assess,
        required_capabilities,
        role_models,
    )
    from kriya.core.model_runtime import resolve_configured_model_runtime

    runtimes: Dict[str, Any] = {}
    primary = ctx.runtime_probe.get("runtime")
    if primary is not None:
        runtimes[ctx.cfg.llm.model.casefold()] = primary
    roles: Dict[str, Any] = {}
    statuses = set()
    for role, models in role_models(ctx.cfg).items():
        entries = []
        for model in models:
            key = model.casefold()
            if key not in runtimes:
                try:
                    runtimes[key] = resolve_configured_model_runtime(ctx.cfg, model, fresh=True)
                except Exception as error:  # an unreachable runtime is undeterminable, not qualified
                    runtimes[key] = None
                    entries.append({"model": model, "status": NOT_EXACT, "reasons": [str(error)]})
                    statuses.add(NOT_EXACT)
                    continue
            runtime = runtimes[key]
            if runtime is None:
                entries.append({"model": model, "status": NOT_EXACT, "reasons": ["runtime could not be probed"]})
                statuses.add(NOT_EXACT)
                continue
            assessment = assess(runtime, required_capabilities(ctx.cfg, role, model), workspace_root=ctx.workspace)
            statuses.add(assessment.status)
            entries.append({"model": model, "campaign_named": is_campaign_named_model(model),
                            **assessment.to_dict()})
        roles[role] = entries
    if statuses == {QUALIFIED}:
        status, reason = CheckStatus.PASS, None
    elif statuses & {NOT_QUALIFIED, STALE, MISSING}:
        status = CheckStatus.FAIL
        reason = (MODEL_NOT_QUALIFIED if MISSING in statuses or NOT_QUALIFIED in statuses
                  else QUALIFICATION_STALE)
    else:
        status, reason = CheckStatus.UNAVAILABLE, RUNTIME_FINGERPRINT_NOT_COMPUTABLE
    evidence: Dict[str, Any] = {"model": ctx.cfg.llm.model, "roles": roles, "name_based_profile_is_authority": False}
    if reason:
        evidence["reason_code"] = reason
    return _check(
        "model.qualification", status, evidence=evidence,
        remediation="Qualify each exact model runtime with `kriya model qualify --model <model>` (PRD-014).",
    )


def _check_embedding(ctx: _Context) -> DoctorCheck:
    try:
        return _check("embedding.connectivity", CheckStatus.PASS, evidence=probe_embedding(ctx.cfg))
    except Exception as error:
        return _check(
            "embedding.connectivity", CheckStatus.UNAVAILABLE, evidence={"error": str(error)},
            remediation="Start the configured embedding endpoint and pull its model.",
        )


def _check_lsp(ctx: _Context) -> DoctorCheck:
    from kriya.tools.lsp import find_jdtls

    try:
        jdtls = find_jdtls()
        evidence: Dict[str, Any] = {"path": jdtls, "policy_required": False}
    except Exception as error:
        jdtls = None
        evidence = {"path": None, "policy_required": False, "error": str(error)}
    return _check(
        "lsp.java",
        CheckStatus.PASS if jdtls else CheckStatus.WARN,
        required=False,
        evidence=evidence,
        remediation="Install jdtls to enable Java LSP grounding; current production policy does not require it.",
    )


def _check_role_independence(ctx: _Context) -> DoctorCheck:
    role_models = {"primary": ctx.cfg.llm.model}
    for role in _ROLES:
        binding = getattr(ctx.cfg.agent_llms, role)
        if binding.llm is not None:
            role_models[role] = binding.llm.model
    independent = len(set(role_models.values())) > 1
    return _check(
        "models.role_independence",
        CheckStatus.PASS if independent else CheckStatus.WARN,
        required=False,
        evidence={"role_models": role_models, "independent": independent, "policy_required": False},
        remediation="Configure separately qualified role models if independent review is desired.",
    )


def _check_precision_boundary(ctx: _Context) -> DoctorCheck:
    """Always reported, never blocking: production does not force semantic
    region enforcement (PRD-009), and where it is enabled it covers only the
    scope below. Everything else has file-level write authority only."""
    from kriya.workflow.semantic_region_authority import SEMANTIC_REGION_SUPPORTED_SCOPE

    return _check(
        "semantic.precision_boundary",
        CheckStatus.WARN,
        required=False,
        evidence={
            "semantic_region_enforcement_required": ctx.cfg.autonomy.semantic_region_enforcement_required,
            "forced_by_production_profile": False,
            "supported_scope": {key: list(value) for key, value in SEMANTIC_REGION_SUPPORTED_SCOPE.items()},
            "outside_scope": "file-level write authority only",
            "owner": "PRD-028",
        },
        remediation="Region-level semantic precision beyond the supported scope arrives with PRD-028.",
    )


def _check_release_integrity(ctx: _Context) -> DoctorCheck:
    remediation = "Install a PRD-002-complete release artifact containing every required runtime and release file."
    try:
        integrity = probe_release_integrity()
    except Exception as error:
        return _check("release.integrity", CheckStatus.UNAVAILABLE, evidence={"error": str(error)}, remediation="Install a verifiable Kriya release artifact.")
    return _check(
        "release.integrity",
        CheckStatus.PASS if not integrity["missing"] else CheckStatus.FAIL,
        evidence=integrity,
        remediation=remediation,
    )


def _check_fixed_guarantees(ctx: _Context) -> DoctorCheck:
    """The worst status among the checks verifying each fixed guarantee."""
    evidence: Dict[str, Any] = {}
    worst = CheckStatus.PASS
    if set(FIXED_GUARANTEE_EVIDENCE) != set(PRODUCTION_FIXED_RUNTIME_GUARANTEES):
        worst = CheckStatus.FAIL
        evidence["unverified_guarantees"] = sorted(set(PRODUCTION_FIXED_RUNTIME_GUARANTEES) - set(FIXED_GUARANTEE_EVIDENCE))
    guarantees = {}
    for guarantee, check_ids in sorted(FIXED_GUARANTEE_EVIDENCE.items()):
        statuses = {check_id: ctx.checks[check_id].status for check_id in check_ids}
        guarantee_status = max(statuses.values(), key=_SEVERITY.__getitem__)
        worst = max(worst, guarantee_status, key=_SEVERITY.__getitem__)
        guarantees[guarantee] = {
            "status": guarantee_status.value,
            "verified_by": {check_id: status.value for check_id, status in statuses.items()},
        }
    evidence["guarantees"] = guarantees
    return _check(
        "runtime.fixed_guarantees", worst, evidence=evidence,
        remediation="Resolve the checks named under verified_by for each guarantee that is not PASS.",
    )


_CHECKS: Tuple[Tuple[str, bool, Callable[[_Context], DoctorCheck]], ...] = (
    ("profile.production", True, _check_profile),
    ("plugins.core_tools", True, _check_core_plugins),
    ("workspace.identity_lock", True, _check_identity_lock),
    ("persistence.checkpoints", True, _check_checkpoints),
    ("persistence.traces", True, _check_traces),
    ("persistence.logs", True, _check_logs),
    ("capacity.workspace", True, _check_workspace_capacity),
    ("capacity.temp", True, _check_temp_capacity),
    ("git.worktree", True, _check_git_worktree),
    ("isolation.candidate_worktree", True, _check_candidate_isolation),
    ("toolchain.required", True, _check_toolchain),
    ("containment.oci_smoke", True, _check_oci_smoke),
    ("containment.no_host_fallback", True, _check_no_host_fallback),
    ("egress.policy", True, _check_egress),
    ("model.connectivity", True, _check_model_connectivity),
    ("model.runtime_fingerprint", True, _check_runtime_fingerprint),
    ("model.qualification", True, _check_qualification),
    ("embedding.connectivity", True, _check_embedding),
    ("lsp.java", False, _check_lsp),
    ("models.role_independence", False, _check_role_independence),
    ("semantic.precision_boundary", False, _check_precision_boundary),
    ("release.integrity", True, _check_release_integrity),
    ("runtime.fixed_guarantees", True, _check_fixed_guarantees),
)


def _run_check(ctx: _Context, check_id: str, required: bool, fn: Callable[[_Context], DoctorCheck]) -> DoctorCheck:
    """One check, whatever happens inside it: an exception, or a result under
    the wrong ID or required flag, is reported as that row's own failure."""
    try:
        check = fn(ctx)
        if check.id != check_id or check.required != required:
            raise RuntimeError(f"check returned id={check.id!r} required={check.required!r}")
        return check
    except Exception as error:
        return _check(
            check_id,
            CheckStatus.FAIL if required else CheckStatus.WARN,
            required=required,
            evidence={"reason_code": CHECK_RAISED, "error": f"{type(error).__name__}: {error}"},
            remediation="The check could not complete; resolve the error and rerun kriya doctor --production.",
        )


def _report(checks: Iterable[DoctorCheck]) -> ProductionDoctorReport:
    checks = tuple(checks)
    ready = not any(
        check.required and check.status in (CheckStatus.FAIL, CheckStatus.UNAVAILABLE)
        for check in checks
    )
    return ProductionDoctorReport(schema_version=1, production_ready=ready, checks=checks)


def run_production_doctor(cfg: AppConfig, workspace_path: str) -> ProductionDoctorReport:
    ctx = _Context(cfg=cfg, workspace=os.path.realpath(workspace_path))
    for check_id, required, fn in _CHECKS:
        ctx.checks[check_id] = _run_check(ctx, check_id, required, fn)
    return _report(ctx.checks.values())


def config_load_failure_report(error: BaseException) -> ProductionDoctorReport:
    """The report when configuration cannot be loaded at all: nothing else can
    be judged, and the deployment is not ready."""
    return _report([_check(
        CONFIG_LOAD_CHECK_ID,
        CheckStatus.FAIL,
        evidence={"error": f"{type(error).__name__}: {error}"},
        remediation="Fix the configuration (see `kriya authority inspect` for authority denials) and rerun.",
    )])

def render_production_report(report: ProductionDoctorReport) -> str:
    lines = ["=== Kriya Production Doctor ==="]
    for check in report.checks:
        lines.append(f"[{check.status.value}] {check.id}")
        lines.append(f"  evidence: {json.dumps(check.evidence, sort_keys=True)}")
        if check.remediation and check.status is not CheckStatus.PASS:
            lines.append(f"  remediation: {check.remediation}")
    lines.append(f"PRODUCTION_READY={str(report.production_ready).lower()}")
    return "\n".join(lines)

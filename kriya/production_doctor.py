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
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

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
    from kriya.core.inference_runtime import runtime_for_binding
    from kriya.core.llm import EgressViolationError, is_local_url
    from kriya.core.model_runtime import kriya_protocol_identity, request_extra_body

    if cfg.autonomy.egress_policy == "local_only" and not is_local_url(cfg.llm.base_url):
        # PRD-012: never probe (or send the API key to) a refused endpoint.
        raise EgressViolationError(f"llm.base_url {cfg.llm.base_url!r} is not local under local_only; not probed")
    # INF-001: discovery and identity through the primary binding's adapter.
    adapter = runtime_for_binding(cfg.llm)
    models = adapter.list_models(cfg.llm.base_url, cfg.llm.api_key, _json_request)
    selected = next((item for item in models if item.get("id") == cfg.llm.model), None)

    runtime = adapter.probe(
        base_url=cfg.llm.base_url, model=cfg.llm.model, api_key=cfg.llm.api_key,
        egress_policy=cfg.autonomy.egress_policy,
        configured_context=adapter.configured_context_window(
            request_extra_body(cfg.llm.extra_body, cfg.llm.context_window, adapter)),
        kriya_protocol=kriya_protocol_identity(cfg, cfg.llm.model),
        transport=lambda url, payload, api_key: _json_request(url, api_key=api_key, payload=payload),
    )
    return {
        "models": [item.get("id") for item in models],
        "selected_model": selected,
        "runtime": runtime,
        "fingerprint": runtime.digest if runtime.exact else None,
    }


class EmbeddingContractViolation(Exception):
    """The embedding provider answered, but not under the contract."""


def probe_embedding(cfg: AppConfig) -> Dict[str, Any]:
    """EMBEDDING-CONTRACT-001: reachable, exact identity (digest, served
    context, the dimension of a real probe vector), ``truncate: false``
    honoured (an over-context input is refused, never silently embedded), and
    any existing code index built under the same fingerprint. Raises
    EmbeddingError when the provider cannot be used, EmbeddingContractViolation
    when it can but the contract does not hold."""
    import asyncio

    from kriya.memory.embedding import EmbeddingInputTooLongError, configured_client

    async def measure():
        client = configured_client(cfg)
        fingerprint = await client.fingerprint()
        try:
            await client.get_embedding("kriya " * (fingerprint.served_context * 2))
        except EmbeddingInputTooLongError:
            return fingerprint
        raise EmbeddingContractViolation(
            f"an input over the served context ({fingerprint.served_context}) was embedded instead of refused - "
            "the provider truncates silently")

    fingerprint = asyncio.run(measure())
    evidence: Dict[str, Any] = {**fingerprint.to_dict(), "truncate_false_honoured": True}
    index_path = os.path.join(cfg.paths.memory, "vector_index.db")
    if os.path.exists(index_path):
        from kriya.memory.vector import LocalVectorStore

        store = LocalVectorStore(index_path)
        try:
            evidence["index_fingerprint"] = store.active_fingerprint()
        finally:
            store.close()
        if evidence["index_fingerprint"] != fingerprint.digest:
            raise EmbeddingContractViolation(
                f"the code index was built under embedding identity {evidence['index_fingerprint']}, the served "
                f"model is {fingerprint.digest}: semantic retrieval is unavailable until it is re-indexed")
    return evidence


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


class ContainmentImageUnavailableError(Exception):
    """The containment image could not be confirmed present; carries the
    inspection so the doctor reports the real cause (a stopped engine is not
    a missing image)."""

    def __init__(self, inspection: Any) -> None:
        super().__init__(inspection.describe())
        self.inspection = inspection


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
    from kriya.tools.containment_oci import _select_image_and_cache_mount, inspect_local_image
    from kriya.tools.process import ProcessController

    command = ["/bin/sh", "-c", _SMOKE_SCRIPT]
    image = toolchain.containment_image if toolchain is not None else _select_image_and_cache_mount(command)[0]
    inspection = inspect_local_image(docker, image)
    if not inspection.present:
        raise ContainmentImageUnavailableError(inspection)

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
                from kriya.platform.services import platform_services

                workspace_lock = platform_services().workspace_lock
                if not workspace_lock.try_exclusive(fd):
                    raise OSError(f"{real}: the workspace lock cannot be taken on a fresh file")
                workspace_lock.release(fd)
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
    "evidence.attempt_recorder",
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
    "model.provider_contract",
    "model.qualification",
    "model.response_protocol",
    "embedding.contract",
    "context.recall_certification",
    "lsp.java",
    "models.role_independence",
    "semantic.precision_boundary",
    # PRD-031A: static analysis (NOT_APPLICABLE while disabled).
    "static_analysis.configuration",
    "static_analysis.provider",
    "static_analysis.capability",
    "static_analysis.coverage",
    "static_analysis.prerequisites",
    "static_analysis.waivers",
    "static_analysis.egress",
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
PROVIDER_CONTRACT_UNVERIFIED = "PROVIDER_CONTRACT_UNVERIFIED"

_SEVERITY = {CheckStatus.PASS: 0, CheckStatus.WARN: 1, CheckStatus.UNAVAILABLE: 2, CheckStatus.FAIL: 3}


@dataclass
class _Context:
    cfg: AppConfig
    workspace: str
    docker: _Docker = field(default_factory=_Docker)
    runtime_probe: Dict[str, Any] = field(default_factory=dict)
    toolchain: Optional[Any] = None
    checks: Dict[str, DoctorCheck] = field(default_factory=dict)
    # PRD-031A: static-analysis rows, computed once per doctor run.
    static_analysis: Optional[Dict[str, Any]] = None


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
    from kriya.platform.capabilities import CapabilityStatus
    from kriya.platform.services import platform_services

    evidence: Dict[str, Any] = {"identity": workspace_identity(ctx.workspace)}
    capability = platform_services().workspace_lock.capability()
    evidence["lock_capability"] = capability.to_dict()
    if capability.status is CapabilityStatus.UNAVAILABLE:
        return _check("workspace.identity_lock", CheckStatus.FAIL, evidence=evidence, remediation=remediation)
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


def _check_attempt_recorder(ctx: _Context) -> DoctorCheck:
    """LR-R1-M1: the attempt evidence store - capture mode, whether the
    store root is writable (without creating it), the newest store's
    integrity, and the newest traced run whose recorder was unavailable.
    Never required (D5: the recorder never blocks a run)."""
    from kriya.core.attempt_evidence import reader
    from kriya.core.attempt_evidence.writer import store_root
    from kriya.core.state_paths import StateDirectoryError, resolve_state_directory, trace_db_path

    recorder = ctx.cfg.evidence.attempt_recorder
    evidence: Dict[str, Any] = {"capture": recorder.capture, "keep_runs": recorder.retention.keep_runs,
                                "max_bytes": recorder.retention.max_bytes}
    remediation = ("Make the state directory (KRIYA_STATE_DIR > paths.state > ~/.kriya/state) writable; "
                   "inspect a run with `kriya evidence verify <run_id>`.")
    if recorder.capture == "off":
        return _recorder_check(CheckStatus.WARN, evidence,
                               "evidence.attempt_recorder.capture is off: no attempt evidence is recorded.")
    try:
        state_dir, _source = resolve_state_directory(ctx.cfg)
        root = store_root(state_dir)
        evidence.update({"store": _store_probe(root)})
    except (StateDirectoryError, OSError) as error:
        return _recorder_check(CheckStatus.WARN, {**evidence, "error": str(error)}, remediation)
    runs = reader.list_runs(state_dir)
    evidence["runs"] = len(runs)
    newest = reader.newest_run(state_dir)
    if newest is not None:
        evidence["newest_run"] = {"run_id": newest, "verification": reader.open_run(state_dir, newest).verify().status}
    unavailable = _last_recorder_unavailable_run(trace_db_path(ctx.cfg))
    if unavailable:
        evidence["last_unavailable_run"] = unavailable
    degraded = evidence.get("newest_run", {}).get("verification") not in (None, reader.VERIFIED, reader.UNSEALED)
    if degraded or unavailable:
        return _recorder_check(CheckStatus.WARN, evidence, remediation)
    return _recorder_check(CheckStatus.PASS, evidence, remediation)


_RECENT_TRACE_ROWS = 50


def _last_recorder_unavailable_run(trace_db: str) -> Optional[Dict[str, Any]]:
    """The newest of the recent trace rows whose evidence.attempt_store event
    says RECORDER_UNAVAILABLE (read-only), or None."""
    import sqlite3

    if not os.path.exists(trace_db):
        return None
    connection = sqlite3.connect(f"file:{trace_db}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT run_id, timestamp, run_events FROM runs ORDER BY timestamp DESC, rowid DESC "
                                  "LIMIT ?", (_RECENT_TRACE_ROWS,)).fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        connection.close()
    for run_id, timestamp, run_events in rows:
        try:
            events = json.loads(run_events or "[]")
        except ValueError:
            continue
        for event in events if isinstance(events, list) else []:
            details = event.get("details") if isinstance(event, dict) else None
            if event.get("kind") == "evidence.attempt_store" and isinstance(details, dict) \
                    and details.get("status") == "RECORDER_UNAVAILABLE":
                return {"run_id": run_id, "timestamp": timestamp, "reason": details.get("reason")}
    return None


def _recorder_check(status: CheckStatus, evidence: Dict[str, Any], remediation: str) -> DoctorCheck:
    return _check("evidence.attempt_recorder", status, required=False, evidence=evidence, remediation=remediation)


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
    from kriya.tools.containment_oci import _attest_toolchain_image, inspect_local_image
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
    inspection = inspect_local_image(docker, identity.containment_image)
    if not inspection.present:
        # Only the running engine's own answer is reported as absence.
        evidence["error"] = inspection.describe()
        evidence["image_inspection"] = inspection.to_evidence()
        return _check("toolchain.required", CheckStatus.UNAVAILABLE, evidence=evidence,
                      remediation=inspection.remediation())
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
    except ContainmentImageUnavailableError as error:
        return _check("containment.oci_smoke", CheckStatus.UNAVAILABLE,
                      evidence={"error": str(error), "image_inspection": error.inspection.to_evidence()},
                      remediation=error.inspection.remediation())
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


def _check_response_protocol(ctx: _Context) -> DoctorCheck:
    """FILE-INTEGRITY-CONTRACT-001: production mutations use the structured
    Developer response protocol (explicit payload terminators). The legacy
    markers are compatibility-only and never production-equivalent; the
    protocol identity is part of every qualification record's policy digest,
    so model.qualification is QUALIFIED only for this exact protocol."""
    from kriya.agents.response_protocol import (
        STRUCTURED,
        developer_response_protocol,
        response_protocol_identity,
    )
    from kriya.core.model_qualification import policy_digest_for

    protocol = developer_response_protocol(ctx.cfg)
    return _check(
        "model.response_protocol",
        CheckStatus.PASS if protocol == STRUCTURED else CheckStatus.FAIL,
        evidence={"developer_response_protocol": protocol, "identity": response_protocol_identity(ctx.cfg),
                  "qualification_policy_digest": policy_digest_for(ctx.cfg)},
        remediation=("Use autonomy.developer_response_protocol: structured (the qualified production protocol); "
                     "legacy_strict is compatibility-only."),
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
    from kriya.core.model_runtime import context_window_overrides

    runtime = ctx.runtime_probe.get("runtime")
    # FALLBACK-CONTEXT-WINDOW-001: a declared window an explicit provider
    # option overrides (the option is what is requested and budgeted).
    overrides = context_window_overrides(ctx.cfg)
    if runtime is not None and runtime.exact:
        return _check("model.runtime_fingerprint", CheckStatus.PASS, evidence={
            "model": ctx.cfg.llm.model, "fingerprint": runtime.digest, "runtime": runtime.to_dict(),
            "context_window_overrides": overrides,
        })
    return _check(
        "model.runtime_fingerprint",
        CheckStatus.UNAVAILABLE,
        evidence={
            "model": ctx.cfg.llm.model,
            "fingerprint": None,
            "runtime": runtime.to_dict() if runtime is not None else None,
            "reason_code": RUNTIME_FINGERPRINT_NOT_COMPUTABLE,
            "context_window_overrides": overrides,
        },
        remediation=(
            "Use a local endpoint exposing exact model metadata (Ollama /api/version, /api/tags, /api/show) "
            "so the served artifact can be fingerprinted."
        ),
    )


def probe_served_context(cfg: AppConfig, model: str) -> Dict[str, Any]:
    """PROVIDER-CONTRACT-001 controlled probe: the context window the
    runtime serves ``model`` with. When the model is not loaded, one minimal
    request through Kriya's own client loads it exactly as a run would (and
    runs the client's own post-call contract check). Never raises."""
    import asyncio
    import contextlib
    import sys

    from kriya.core.inference_runtime import runtime_for_binding
    from kriya.core.llm import LLMClient
    from kriya.core.model_runtime import binding_object

    binding = binding_object(cfg, model) or cfg.llm
    base_url = getattr(binding, "base_url", None) or cfg.llm.base_url
    api_key = getattr(binding, "api_key", None) or cfg.llm.api_key
    adapter = runtime_for_binding(binding)
    evidence: Dict[str, Any] = {"served": None, "probe_request_sent": False}
    try:
        observation = adapter.observe_served_context_state(base_url=base_url, model=model, api_key=api_key)
        evidence["observation"] = observation.to_dict()
        evidence["served"] = observation.window
        if evidence["served"] is not None:
            return evidence

        async def load() -> Any:
            llm = LLMClient(cfg)
            try:
                return await llm.complete_result("", "ok", model_override=model, base_url_override=base_url,
                                                 api_key_override=api_key, max_tokens_override=1)
            finally:
                await llm.aclose()

        evidence["probe_request_sent"] = True
        # The doctor's own output is its report (``--json`` is one JSON
        # document): the probe's per-call usage line goes to stderr.
        with contextlib.redirect_stdout(sys.stderr):
            result = asyncio.run(load())
        contract = result.protocol.get("provider_contract", {})
        evidence["served"] = contract.get("served_context_window_after_call")
        evidence["observation_after_probe"] = contract.get("served_context_observation")
        if "violation" in contract:
            evidence["violation"] = contract["violation"]
    except Exception as error:  # the row reports what could not be observed
        evidence["error"] = f"{type(error).__name__}: {error}"
        if getattr(error, "reason_code", None):
            evidence["violation"] = {"reason_code": error.reason_code, **getattr(error, "details", {})}
    return evidence


def _provider_contract_entry(cfg: AppConfig, model: str, runtime: Any) -> Tuple[CheckStatus, Dict[str, Any]]:
    """One model's contract: every setting its binding relies on, as the
    provider applies it, and the window it is served with."""
    from kriya.core.inference_runtime import runtime_for_binding
    from kriya.core.model_runtime import binding_object, requested_context_window
    from kriya.core.provider_contract import Provenance, ProviderContractError, context_window_state

    binding = binding_object(cfg, model) or cfg.llm
    adapter = runtime_for_binding(binding)
    extra_body = getattr(binding, "extra_body", None) or None
    temperature = getattr(binding, "temperature", None)
    requested = requested_context_window(extra_body, getattr(binding, "context_window", None), adapter)
    plan = adapter.request_plan(extra_body, temperature=temperature if temperature is not None
                                else cfg.llm.temperature, reasoning_flag=bool(getattr(binding, "reasoning", False)),
                                requested_context_window=requested, fingerprint=runtime)
    entry: Dict[str, Any] = {
        "model": model, "adapter": adapter.name, "capabilities": adapter.provider_capabilities.to_dict(),
        "runtime_exact": bool(runtime is not None and runtime.exact), "plan": plan.to_dict(),
        "not_effective": sorted(s.name for s in plan.not_effective()),
        "unverified": sorted(s.name for s in plan.unverified()),
    }
    failures = []
    try:
        plan.enforce(strict=True)
    except ProviderContractError as error:
        failures.append({"reason_code": error.reason_code, **error.details})
    served = probe_served_context(cfg, model)
    entry["served_context"] = served
    if "violation" in served:
        failures.append(served["violation"])
    elif served["served"] is not None:
        try:
            entry["context"] = context_window_state(requested, served["served"], Provenance.SERVER_OBSERVED,
                                                    exact=True).to_dict()
        except ProviderContractError as error:
            failures.append({"reason_code": error.reason_code, **error.details})
    if failures:
        entry["failures"] = failures
        return CheckStatus.FAIL, entry
    if not entry["runtime_exact"] or entry["unverified"] or served["served"] is None:
        entry["reason_code"] = PROVIDER_CONTRACT_UNVERIFIED
        return CheckStatus.UNAVAILABLE, entry
    return CheckStatus.PASS, entry


def _check_provider_contract(ctx: _Context) -> DoctorCheck:
    """PROVIDER-CONTRACT-001: every model a production role can call is
    proven to be served with the settings Kriya budgets, qualifies and
    records - request-carried settings accepted by the adapter, server-only
    settings equal to the served model's own configuration, the served
    window exactly the requested one. Desired configuration is not
    effective inference identity."""
    from kriya.core.model_qualification import role_models
    from kriya.core.model_runtime import resolve_configured_model_runtime

    entries: List[Dict[str, Any]] = []
    status = CheckStatus.PASS
    seen = set()
    primary = ctx.runtime_probe.get("runtime")
    for models in role_models(ctx.cfg).values():
        for model in models:
            if model.casefold() in seen:
                continue
            seen.add(model.casefold())
            if primary is not None and model.casefold() == ctx.cfg.llm.model.casefold():
                runtime = primary
            else:
                try:
                    runtime = resolve_configured_model_runtime(ctx.cfg, model, fresh=True)
                except Exception:  # unidentifiable: its settings cannot be verified
                    runtime = None
            model_status, entry = _provider_contract_entry(ctx.cfg, model, runtime)
            entry["status"] = model_status.value
            entries.append(entry)
            if _SEVERITY[model_status] > _SEVERITY[status]:
                status = model_status
    return _check(
        "model.provider_contract", status, evidence={"models": entries},
        remediation=("Serve each model with exactly the settings its binding declares: pin the server-only "
                     "settings (`kriya model pin --model <model>`), point the binding at the pinned model, "
                     "and remove any setting the provider cannot carry. SERVED_CONTEXT_UNOBSERVABLE: keep "
                     "the model loaded after a request (no OLLAMA_KEEP_ALIVE=0) so the window it was "
                     "served with can be verified; Kriya never changes keep-alive itself."),
    )


def _check_qualification(ctx: _Context) -> DoctorCheck:
    """PRD-014: every production role's every callable model (its binding
    and escalation chain) must have a CURRENT qualification record for its
    exact runtime covering the capabilities Kriya uses with it. A model name
    is never a qualification. FAIL when a determinable runtime lacks it;
    UNAVAILABLE when a runtime cannot be identified exactly."""
    from kriya.core.inference_settings import role_inference_identities
    from kriya.core.model_capabilities import is_campaign_named_model
    from kriya.core.model_qualification import (
        MISSING,
        NOT_EXACT,
        NOT_QUALIFIED,
        QUALIFIED,
        STALE,
        assess,
        policy_digest_for,
        qualification_policy_of,
        required_capabilities,
        role_models,
    )
    from kriya.core.model_runtime import resolve_configured_model_runtime

    policy_digest = policy_digest_for(ctx.cfg)
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
            # Every identity the role executes the model with (a Developer's
            # differing retry temperature is its own; MODEL-EVIDENCE-HARDENING-001).
            for label, settings in role_inference_identities(ctx.cfg, role, model):
                assessment = assess(runtime, required_capabilities(ctx.cfg, role, model), settings=settings,
                                    workspace_root=ctx.workspace, policy_digest=policy_digest)
                statuses.add(assessment.status)
                entries.append({"model": model, "identity": label, "inference_settings_digest": settings.digest,
                                "campaign_named": is_campaign_named_model(model), **assessment.to_dict()})
        roles[role] = entries
    if statuses == {QUALIFIED}:
        status, reason = CheckStatus.PASS, None
    elif statuses & {NOT_QUALIFIED, STALE, MISSING}:
        status = CheckStatus.FAIL
        reason = (MODEL_NOT_QUALIFIED if MISSING in statuses or NOT_QUALIFIED in statuses
                  else QUALIFICATION_STALE)
    else:
        status, reason = CheckStatus.UNAVAILABLE, RUNTIME_FINGERPRINT_NOT_COMPUTABLE
    evidence: Dict[str, Any] = {
        "model": ctx.cfg.llm.model, "roles": roles, "name_based_profile_is_authority": False,
        # QUAL-CONFIG-001: the policy every record above was checked against.
        "qualification_policy_digest": policy_digest,
        "qualification_policy": qualification_policy_of(ctx.cfg).model_dump(mode="json"),
    }
    if reason:
        evidence["reason_code"] = reason
    return _check(
        "model.qualification", status, evidence=evidence,
        remediation="Qualify each exact model runtime with `kriya model qualify --model <model>` (PRD-014).",
    )


def _check_embedding(ctx: _Context) -> DoctorCheck:
    try:
        return _check("embedding.contract", CheckStatus.PASS, evidence=probe_embedding(ctx.cfg))
    except EmbeddingContractViolation as violation:
        return _check(
            "embedding.contract", CheckStatus.FAIL, evidence={"error": str(violation)},
            remediation="Use an embedding endpoint that refuses over-context input instead of truncating it, "
                        "and run `kriya analyze` (an index of another identity is rebuilt) after any embedding "
                        "model change.",
        )
    except Exception as error:
        return _check(
            "embedding.contract", CheckStatus.UNAVAILABLE, evidence={"error": str(error)},
            remediation="Start the configured embedding endpoint and pull its model.",
        )


def _recall_certification_required(cfg: AppConfig) -> bool:
    """Required exactly when Graph RAG retrieval is in use (a code index
    exists at paths.memory)."""
    from kriya.workflow.context_certification import retrieval_applicable

    return retrieval_applicable(cfg)


def _check_recall_certification(ctx: _Context) -> DoctorCheck:
    """PRD-027: reads the stored certification for this exact embedding
    runtime, retrieval policy, index implementation and suite. It never
    runs the benchmark."""
    from kriya.workflow.context_certification import (
        STATUS_CERTIFIED,
        STATUS_NOT_APPLICABLE,
        STATUS_UNAVAILABLE,
        certification_status,
    )

    status, detail = certification_status(ctx.cfg)
    check_status = (
        CheckStatus.PASS if status in (STATUS_CERTIFIED, STATUS_NOT_APPLICABLE)
        else CheckStatus.UNAVAILABLE if status == STATUS_UNAVAILABLE
        else CheckStatus.FAIL
    )
    return _check(
        "context.recall_certification", check_status,
        required=_recall_certification_required(ctx.cfg),
        evidence={"status": status, "detail": detail},
        remediation="Run `kriya context certify` against the configured embedding model (see docs/user_guide.md).",
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


def _role_independence_required(cfg: AppConfig) -> bool:
    """PRD-018: the row blocks readiness only when the operator's policy
    requires role independence; otherwise it is informational."""
    return bool(cfg.model_policy.independent_roles)


def _check_role_independence(ctx: _Context) -> DoctorCheck:
    """PRD-018: which roles share the same EXACT runtime (not the same model
    name). WARN when roles share one (the default local setup) or a runtime
    cannot be identified exactly; FAIL, blocking, only when
    model_policy.independent_roles requires a role to be independent of
    the Developer and it is not (or cannot be shown to be)."""
    from kriya.core.role_metrics import independence_violations, role_runtimes, shared_runtime_groups

    required_roles = list(ctx.cfg.model_policy.independent_roles)
    runtimes = role_runtimes(ctx.cfg, fresh=True)
    groups = shared_runtime_groups(runtimes)
    shared = [group for group in groups if len(group["roles"]) > 1]
    unverified = [group for group in groups if group["identity"] != "exact"]
    violations = independence_violations(ctx.cfg, runtimes)
    if required_roles:
        status = CheckStatus.FAIL if violations else CheckStatus.PASS
    else:
        status = CheckStatus.WARN if shared or unverified else CheckStatus.PASS
    evidence: Dict[str, Any] = {
        "roles": {role: {"model": model, "runtime_digest": digest, "runtime_exact": exact}
                  for role, (model, digest, exact) in sorted(runtimes.items())},
        "groups": groups,
        "independent": not shared and not unverified,
        "policy_required": bool(required_roles),
        "independent_roles_required": required_roles,
        "violations": violations,
        "second_opinion_is_verification": False,
    }
    if violations:
        evidence["reason_code"] = "ROLE_INDEPENDENCE_REQUIRED"
    return _check(
        "models.role_independence", status, required=bool(required_roles), evidence=evidence,
        remediation=(
            "Bind each role listed in model_policy.independent_roles (agent_llms.<role>.llm) to a model whose "
            "exact runtime differs from the Developer's, and make it reachable so its identity is exact."
            if required_roles else
            "Roles sharing one runtime share its errors. Bind verifier roles to a separately qualified model "
            "(agent_llms.<role>.llm) if independent review is wanted; model_policy.independent_roles enforces it."
        ),
    )


_WORKSPACE_LANGUAGE_SCAN_LIMIT = 20000
_SCAN_SKIP_DIRS = frozenset({".git", ".kriya", "node_modules", "target", "build", ".venv", "venv", "__pycache__"})


def _workspace_source_languages(workspace: str) -> Dict[str, int]:
    """Source files per language extension in ``workspace`` (bounded walk,
    build/VCS directories skipped): the languages a run there may edit."""
    from kriya.analyzer.analyzer import EXTENSION_MAP

    counts: Dict[str, int] = {}
    seen = 0
    for _root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _SCAN_SKIP_DIRS]
        for name in files:
            seen += 1
            if seen > _WORKSPACE_LANGUAGE_SCAN_LIMIT:
                return counts
            ext = os.path.splitext(name)[1].lower()
            if ext in EXTENSION_MAP:
                counts[ext] = counts.get(ext, 0) + 1
    return counts


def _precision_boundary_required(cfg: AppConfig) -> bool:
    """Region-level precision is a production need only when the operator
    requires semantic-region enforcement."""
    return bool(cfg.autonomy.semantic_region_enforcement_required)


def _check_precision_boundary(ctx: _Context) -> DoctorCheck:
    """PRD-028: the language-adapter capability table, always reported.
    Blocking only when semantic-region enforcement is required AND the
    workspace holds source in a language without the editable-region
    capability - an active production need the adapters cannot meet.
    Otherwise it stays an informational WARN (production does not force
    region enforcement, PRD-009)."""
    from kriya.workflow.language_adapters import Capability, CapabilityStatus, capability_status, capability_table
    from kriya.workflow.semantic_region_authority import SEMANTIC_REGION_SUPPORTED_SCOPE

    required = _precision_boundary_required(ctx.cfg)
    languages = _workspace_source_languages(ctx.workspace)
    unsupported = sorted(
        ext for ext in languages
        if capability_status(f"x{ext}", Capability.EDITABLE_REGION) is not CapabilityStatus.SUPPORTED
    )
    evidence = {
        "semantic_region_enforcement_required": ctx.cfg.autonomy.semantic_region_enforcement_required,
        "forced_by_production_profile": False,
        "supported_scope": {key: list(value) for key, value in SEMANTIC_REGION_SUPPORTED_SCOPE.items()},
        "outside_scope": "file-level write authority only",
        "language_capabilities": capability_table(),
        "workspace_source_extensions": dict(sorted(languages.items())),
        "editable_region_unsupported_in_workspace": unsupported,
        "owner": "PRD-028",
    }
    if not required:
        return _check(
            "semantic.precision_boundary", CheckStatus.WARN, required=False, evidence=evidence,
            remediation="Region-level precision covers the supported scope only; other languages have file-level authority.",
        )
    return _check(
        "semantic.precision_boundary",
        CheckStatus.FAIL if unsupported else CheckStatus.PASS,
        required=True, evidence=evidence,
        remediation=(
            "semantic_region_enforcement_required is set, but the workspace contains source in "
            f"{', '.join(unsupported)} with no editable-region capability - those edits would have "
            "file-level authority only. Disable the requirement or restrict the workspace."
            if unsupported else "No remediation required."
        ),
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


def _static_analysis_rows(ctx: _Context) -> Dict[str, Any]:
    """PRD-031A: every static-analysis row from one provider probe."""
    if ctx.static_analysis is None:
        from kriya.static_analysis.doctor import evaluate_rows

        ctx.static_analysis = evaluate_rows(ctx.cfg, ctx.workspace)
    return ctx.static_analysis


def _static_analysis_required(cfg: AppConfig) -> bool:
    from kriya.static_analysis.doctor import rows_required

    return rows_required(cfg)


def _static_analysis_check(check_id: str) -> Callable[[_Context], DoctorCheck]:
    def check(ctx: _Context) -> DoctorCheck:
        row = _static_analysis_rows(ctx)[check_id]
        required = _static_analysis_required(ctx.cfg)
        # NOT_APPLICABLE is represented as PRD-027 established it: PASS,
        # never required, with evidence.status NOT_APPLICABLE.
        if row.status == "NOT_APPLICABLE":
            return _check(check_id, CheckStatus.PASS, required=required, evidence=row.evidence)
        status = {"PASS": CheckStatus.PASS, "WARN": CheckStatus.WARN, "FAIL": CheckStatus.FAIL}[row.status]
        return _check(check_id, status, required=required, evidence=row.evidence, remediation=row.remediation)
    return check


_STATIC_ANALYSIS_CHECKS = tuple(
    (check_id, _static_analysis_required, _static_analysis_check(check_id))
    for check_id in PRODUCTION_DOCTOR_CHECK_IDS if check_id.startswith("static_analysis.")
)


_CHECKS: Tuple[Tuple[str, Union[bool, Callable[[AppConfig], bool]], Callable[[_Context], DoctorCheck]], ...] = (
    ("profile.production", True, _check_profile),
    ("plugins.core_tools", True, _check_core_plugins),
    ("workspace.identity_lock", True, _check_identity_lock),
    ("persistence.checkpoints", True, _check_checkpoints),
    ("persistence.traces", True, _check_traces),
    ("persistence.logs", True, _check_logs),
    # LR-R1-M1 D5: the recorder is observational; this row never blocks.
    ("evidence.attempt_recorder", False, _check_attempt_recorder),
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
    ("model.provider_contract", True, _check_provider_contract),
    ("model.qualification", True, _check_qualification),
    ("model.response_protocol", True, _check_response_protocol),
    ("embedding.contract", True, _check_embedding),
    ("context.recall_certification", _recall_certification_required, _check_recall_certification),
    ("lsp.java", False, _check_lsp),
    ("models.role_independence", _role_independence_required, _check_role_independence),
    ("semantic.precision_boundary", _precision_boundary_required, _check_precision_boundary),
    *_STATIC_ANALYSIS_CHECKS,
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
        if callable(required):
            required = required(cfg)
        ctx.checks[check_id] = _run_check(ctx, check_id, required, fn)
    return _report(ctx.checks.values())


def config_load_failure_report(error: BaseException) -> ProductionDoctorReport:
    """The report when configuration cannot be loaded at all: nothing else can
    be judged, and the deployment is not ready. A typed operator error keeps
    its own reason code and remediation (e.g. TRUST_PATH_INSIDE_WORKSPACE)."""
    evidence: Dict[str, Any] = {"error": f"{type(error).__name__}: {error}"}
    reason_code = getattr(error, "reason_code", None)
    if isinstance(reason_code, str) and reason_code:
        evidence["reason_code"] = reason_code
    remediation = getattr(error, "remediation", None)
    return _report([_check(
        CONFIG_LOAD_CHECK_ID,
        CheckStatus.FAIL,
        evidence=evidence,
        remediation=remediation if isinstance(remediation, str) and remediation else
        "Fix the configuration (see `kriya authority inspect` for authority denials) and rerun.",
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

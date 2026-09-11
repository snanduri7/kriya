"""SEC-001 foundation: a backend-independent execution-containment contract.

`ContainmentProfile` describes what a subprocess is ALLOWED to do (never
Docker/sandbox-exec/macOS-specific concepts - see
docs/architecture/SEC001_HOSTILE_CODE_CONTAINMENT_DESIGN.md). A
`ContainmentBackend` turns a profile into a real `env`/`preexec_fn` pair
`ProcessController` can hand to `subprocess.Popen`/`asyncio.create_subprocess_exec`,
or raises `ContainmentSetupError` if it cannot honor the profile's
requirements - `ProcessController` then fails the command closed rather
than running it uncontained (Invariant: containment/resource setup
failure must block execution, never degrade silently).

No production (OCI/container) backend is implemented here - per the
design's own amendment, that is a separate, later work package.
`NullContainmentBackend` is today's real, unchanged behavior (env
allowlist + best-effort rlimit, the existing `kriya/tools/sandbox.py`
primitives) and is the default everywhere, so existing callers are
unaffected until a caller explicitly opts into a stricter backend.
`DummyContainmentBackend` exists only to prove composition and fail-closed
semantics in tests (scope item 5's own explicit allowance) - never
selected by production config resolution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Protocol

from kriya.tools.sandbox import build_restricted_env, posix_resource_limits_preexec_fn

logger = logging.getLogger(__name__)


class TrustClass(Enum):
    """Who this command's content ultimately comes from - the Task 1
    execution-surface inventory's own classification, made explicit and
    machine-checkable rather than inferred from command text (Invariant:
    never infer trust from command text)."""

    TRUSTED_KRIYA_INFRASTRUCTURE = "trusted_kriya_infrastructure"
    UNTRUSTED_EXECUTION = "untrusted_execution"


class NetworkAuthority(Enum):
    DENIED = "denied"
    DEPENDENCY_REGISTRY_ONLY = "dependency_registry_only"
    UNRESTRICTED = "unrestricted"


class ContainmentSetupError(RuntimeError):
    """Containment or resource-limit setup could not be established for a
    command that required it. `ProcessController` raises this INSTEAD of
    running the command uncontained - the caller must treat it as a
    distinct, deterministic failure (see kriya/workflow/retry_strategy.py's
    `handle_attempt_failure` and the `containment_setup_failed`
    failure_category), never as an ordinary command/compile/test/timeout
    failure and never as a signal to silently retry unsandboxed."""


class ResourceLimitSetupError(ContainmentSetupError):
    """A CPU/memory rlimit could not be applied to the child before it
    starts running arbitrary code. Distinct subclass so a caller that
    wants to distinguish "no backend configured at all" from "a backend
    was configured but genuinely failed to prepare" can, without string-
    matching messages."""


class BackendUnavailableError(ContainmentSetupError):
    """The configured containment backend is missing, rejected the
    profile, or is otherwise unable to establish the controls the profile
    requires."""


@dataclass(frozen=True)
class ContainmentProfile:
    """What a subprocess is allowed to do - backend-independent by
    construction (no Docker/sandbox-exec-specific fields). See
    docs/architecture/SEC001_HOSTILE_CODE_CONTAINMENT_DESIGN.md §3/§6."""

    trust_class: TrustClass
    workspace_path: str
    temp_path: Optional[str] = None
    dependency_cache_paths: List[str] = field(default_factory=list)
    dependency_cache_writable: bool = False
    network: NetworkAuthority = NetworkAuthority.DENIED
    env_allowlist: List[str] = field(default_factory=list)
    cpu_seconds: Optional[int] = None
    memory_mb: Optional[int] = None

    @property
    def backend_required(self) -> bool:
        """TRUSTED_KRIYA_INFRASTRUCTURE call sites (fixed, Kriya-authored
        argv, no user/model/target-repo content - see the design's own
        Task 1 classification) never require a containment backend at
        all; every other trust class does, once a backend is actually
        configured (see `resolve_containment_backend` - the packaged
        default, "none", intentionally does NOT enforce this, preserving
        today's exact behavior until a caller opts into a real backend)."""
        return self.trust_class is not TrustClass.TRUSTED_KRIYA_INFRASTRUCTURE


@dataclass(frozen=True)
class PreparedContainment:
    """What a backend hands back to `ProcessController` once a profile is
    successfully prepared.

    `env`/`preexec_fn` are the shape `subprocess.Popen`/
    `asyncio.create_subprocess_exec` already accept for a HOST-process
    backend (`NullContainmentBackend`) - unchanged from the original
    foundation package.

    `command_prefix`/`cleanup` (SEC-001-P6, 2026-09-11) exist for a backend
    whose real enforcement mechanism is a SEPARATE process the command runs
    inside of (a container) rather than the host process itself:
    - `command_prefix`: prepended to the caller's own command before
      `ProcessController` spawns it - e.g. `["docker", "run", "--rm",
      "--name", ..., "--network", "none", ..., image]` ahead of the
      caller's real `["mvn", "install"]`. `ProcessController` still owns
      the ONE spawn point (`_spawn_popen`/`_spawn_subprocess_exec`) - this
      only changes what argv it spawns, not who spawns it (Invariant: no
      parallel execution architecture).
    - `cleanup`: a best-effort, no-raise callable `ProcessController` runs
      in `finally` after the command completes OR times out. For a
      container backend this is NOT optional even with `docker run --rm`:
      `ProcessController`'s own timeout path kills the HOST `docker` CLI
      process's process group (`_terminate_tree`), which does not reliably
      propagate into a VM-mediated container runtime (Docker Desktop) and
      can leave the container itself still running - `cleanup` is the
      backend's own authoritative "make sure the container is actually
      gone" step (e.g. `docker rm -f <name>`), checked adversarially via
      `docker ps` from the host, not by trusting the CLI's own exit code.

    `exec_target` (SEC-001-P6, managed-service containment, 2026-09-11):
    a ready-made argv prefix (e.g. `["docker", "exec", "<container>"]`) a
    caller can prepend to run a NEW command INSIDE an already-started,
    still-running container - only meaningful for `start_managed()`'s
    long-lived-service lifecycle (finite `run()`/`run_async()` calls have
    nothing to "exec into" once they're done). This is how a managed
    service under `network=DENIED` (zero host-published ports, zero
    outbound) can still be readiness-checked/probed from the host: Kriya
    issues `docker exec <container> ...` (itself TRUSTED_KRIYA_INFRASTRUCTURE
    execution, not the untrusted payload) to run the check FROM INSIDE the
    container's own network namespace, against its own loopback - no port
    is ever published to the host, no bridge network is ever created, and
    the container's isolation posture is identical to any other contained
    command (see kriya/tools/service_runtime.py's exec-based readiness/
    probe functions).
    """

    env: Optional[Dict[str, str]]
    preexec_fn: Optional[Callable[[], None]]
    backend_name: str
    command_prefix: Optional[List[str]] = None
    cleanup: Optional[Callable[[], None]] = None
    exec_target: Optional[List[str]] = None


class ContainmentBackend(Protocol):
    """One implementation per real enforcement mechanism (sandbox-exec,
    OCI, none). Deliberately NOT an abstract base class with shared state -
    a backend is a pure `(profile, command) -> PreparedContainment`
    function with a name, nothing about a specific mechanism belongs in
    this contract."""

    @property
    def name(self) -> str: ...

    def prepare(self, profile: ContainmentProfile, command: List[str]) -> PreparedContainment:
        """Raises `ContainmentSetupError` (or a subclass) if this backend
        cannot honor `profile` - never returns a partially-honored
        result. `command` (SEC-001-P6) is the real argv about to run -
        NOT a decision input for policy/authorization (that stays
        ExecutionPolicy's job, Invariant: authorization/containment stay
        separate layers) - a backend may use it only for backend-internal
        setup choices with no security meaning of their own, e.g. an OCI
        backend picking which base image has the right toolchain
        (`mvn` vs `python`) preinstalled. `NullContainmentBackend`/
        `DummyContainmentBackend` ignore it entirely."""
        ...


class NullContainmentBackend:
    """Today's real, unchanged behavior: env allowlist + best-effort CPU/
    memory rlimit (`kriya/tools/sandbox.py`, wired since before this
    design existed) - NO filesystem/network containment. This is the
    packaged default for every profile, `backend_required` or not,
    because no production containment backend ships in this work package
    (see the design's own amendment: OCI backend is a separate, later
    package) - preserves "existing behavior must remain compatible when
    containment is not required by the current execution profile" exactly,
    for every existing caller, until a real backend is configured."""

    name = "none"

    def prepare(self, profile: ContainmentProfile, command: List[str]) -> PreparedContainment:
        # SEC-001 foundation gate (2026-09-11): this backend provides NO
        # filesystem or network isolation at all - only env allowlisting and
        # best-effort rlimits. A profile that asks for anything stronger
        # than UNRESTRICTED network is asking for a guarantee this backend
        # structurally cannot provide; silently returning a PreparedContainment
        # anyway would let "a profile exists" stand in for "containment was
        # actually established" (the exact confusion Invariant 4 forbids) and
        # would let a null/no-isolation backend satisfy an isolation-required
        # profile (Invariant 2). `network` is the one field in today's
        # ContainmentProfile shape that distinguishes "no isolation asked"
        # from "isolation asked" without a schema change - every real
        # call site that needs filesystem/process confinement (the OCI
        # package's validator/service-runtime execution phase) also sets
        # network to DENIED or DEPENDENCY_REGISTRY_ONLY, so this one gate
        # covers the whole "requires real isolation" family in practice.
        #
        # Scoped to `profile.backend_required` (SEC-001-P6, 2026-09-11):
        # TRUSTED_KRIYA_INFRASTRUCTURE profiles (backend_required=False)
        # are never subject to containment in the first place - trust is
        # established by ExecutionPolicy/TrustClass, a SEPARATE layer from
        # containment (Invariant: authorization/containment stay separate).
        # Requiring every trusted-infra caller to also correctly set
        # network=UNRESTRICTED just to avoid this gate would be a footgun
        # with no security benefit (found live: GitTool's own trusted-infra
        # migration tripped this gate on ContainmentProfile's network
        # default of DENIED before this scoping was added).
        if profile.backend_required and profile.network is not NetworkAuthority.UNRESTRICTED:
            raise BackendUnavailableError(
                f"NullContainmentBackend cannot honor network authority "
                f"{profile.network.value!r} - it provides no network "
                f"isolation at all (nor filesystem/process isolation). A "
                f"profile requesting anything other than UNRESTRICTED "
                f"network needs a real containment backend (e.g. 'oci'), "
                f"not 'none'. Refusing rather than silently running the "
                f"command uncontained."
            )
        env = build_restricted_env(profile.env_allowlist) if profile.env_allowlist else None
        preexec_fn = None
        if profile.cpu_seconds is not None or profile.memory_mb is not None:
            preexec_fn = posix_resource_limits_preexec_fn(
                profile.cpu_seconds, profile.memory_mb
            )
        return PreparedContainment(env=env, preexec_fn=preexec_fn, backend_name=self.name)


class DummyContainmentBackend:
    """Test/dummy backend proving composition and fail-closed semantics
    (scope item 5's own explicit allowance) - never selected by
    `resolve_containment_backend` outside of tests. `should_fail=True`
    simulates a backend that is configured but genuinely cannot establish
    the profile's controls (`BackendUnavailableError`), independent of
    whether the backend exists at all."""

    name = "test"

    def __init__(self, should_fail: bool = False, failure_message: str = "test backend configured to fail") -> None:
        self.should_fail = should_fail
        self.failure_message = failure_message
        self.prepared_profiles: List[ContainmentProfile] = []

    def prepare(self, profile: ContainmentProfile, command: List[str]) -> PreparedContainment:
        if self.should_fail:
            raise BackendUnavailableError(self.failure_message)
        self.prepared_profiles.append(profile)
        env = build_restricted_env(profile.env_allowlist) if profile.env_allowlist else None
        return PreparedContainment(env=env, preexec_fn=None, backend_name=self.name)


def _oci_backend_factory() -> ContainmentBackend:
    # Deferred import: containment_oci.py's module-level work (locating the
    # docker binary) has no business running for callers that never select
    # "oci" - and it keeps kriya/tools/containment.py itself free of any
    # container-specific import, matching this file's own "no Docker/
    # sandbox-exec-specific concepts" charter for the CONTRACT, even though
    # the concrete "oci" backend obviously has to know about Docker somewhere.
    from kriya.tools.containment_oci import OCIContainmentBackend

    return OCIContainmentBackend()


_PRODUCTION_BACKEND_REGISTRY: Dict[str, Callable[[], ContainmentBackend]] = {
    "none": NullContainmentBackend,
    "oci": _oci_backend_factory,
}


def resolve_containment_backend(name: str) -> ContainmentBackend:
    """Fails closed on an unrecognized name - never silently falls back to
    `NullContainmentBackend` for a typo'd/removed backend name, since that
    would be exactly the kind of silent-downgrade-to-uncontained-execution
    Invariant 9 forbids. `"test"` is deliberately excluded from this
    registry - it exists for tests to construct directly
    (`DummyContainmentBackend(...)`), never for production config to name."""
    try:
        return _PRODUCTION_BACKEND_REGISTRY[name]()
    except KeyError:
        raise BackendUnavailableError(
            f"Unknown containment backend '{name}' - no production backend is "
            "registered under that name. This is a configuration error, not a "
            "reason to fall back to uncontained execution."
        ) from None

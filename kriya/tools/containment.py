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
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from kriya.tools.sandbox import build_restricted_env, resource_plan

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


@dataclass(frozen=True)
class GitMetadataMount:
    """OD-1 (BACKEND-FINAL-CLOSURE-005): the host paths of a verified Git
    metadata export (kriya/tools/git_metadata.py) and the identity it binds -
    the backend mounts ``repo_dir`` read-only and, for a worktree, the
    Kriya-owned ``gitfile_path`` over the workspace's dangling gitfile."""

    repo_dir: str
    gitfile_path: str
    base_revision: str
    digest: str


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


PERSISTENT_DEPENDENCY_CACHE_WRITABLE = "PERSISTENT_DEPENDENCY_CACHE_WRITABLE"
DEPENDENCY_CACHE_SCOPE_REQUIRED = "DEPENDENCY_CACHE_SCOPE_REQUIRED"
PROJECT_VENV_NOT_DISCARDED = "PROJECT_VENV_NOT_DISCARDED"
DEPENDENCY_CACHE_REASON_CODES = frozenset({PERSISTENT_DEPENDENCY_CACHE_WRITABLE, DEPENDENCY_CACHE_SCOPE_REQUIRED, PROJECT_VENV_NOT_DISCARDED})


class DependencyCacheError(ContainmentSetupError):
    """R3 (kriya/tools/dependency_cache.py): a dependency cache could not be given to candidate-controlled execution on the terms R3 requires - a
    writable cache that is not a verification scope's ephemeral one (PERSISTENT_DEPENDENCY_CACHE_WRITABLE), or a cache asked for outside a scope
    (DEPENDENCY_CACHE_SCOPE_REQUIRED), or a project venv an earlier scope left that cannot be removed (PROJECT_VENV_NOT_DISCARDED). The command is
    not run, never run with a persistent cache or venv instead. ``reason_code`` is one of
    DEPENDENCY_CACHE_REASON_CODES."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code


class HostWriterIdentityRefused(Exception):
    """Why SEC-008's writer-identity rule refused; each caller raises its
    own typed error (and message) from it."""

    PRIVILEGED = "privileged"
    OWNER_MISMATCH = "owner_mismatch"
    UNAVAILABLE = "unavailable"

    def __init__(self, kind: str, *, uid: Optional[int] = None, path: Optional[str] = None,
                 owner_uid: Optional[int] = None, detail: str = "") -> None:
        super().__init__(kind)
        self.kind, self.uid, self.path, self.owner_uid, self.detail = kind, uid, path, owner_uid, detail


def host_writer_identity(paths: List[str]) -> Tuple[int, int]:
    """SEC-008's rule, in one place (OCI registry acquisition, OCI host-
    mount writers, TOOL-003 MCP write mounts): a container that writes host
    paths runs as the Kriya process's own trusted identity (HostIdentityPort),
    never as root, and only when every path is owned by that identity -
    never a guessed or arbitrary identity, never a host chmod/chown."""
    from kriya.platform.capabilities import PlatformCapabilityUnavailable
    from kriya.platform.services import platform_services

    host = platform_services().host_identity
    try:
        identity = host.current()
    except PlatformCapabilityUnavailable as error:
        raise HostWriterIdentityRefused(HostWriterIdentityRefused.UNAVAILABLE, detail=str(error)) from error
    if identity.is_privileged:
        raise HostWriterIdentityRefused(HostWriterIdentityRefused.PRIVILEGED, uid=identity.uid)
    for path in paths:
        owner = host.owner_uid(path)
        if owner != identity.uid:
            raise HostWriterIdentityRefused(HostWriterIdentityRefused.OWNER_MISMATCH, uid=identity.uid,
                                            path=path, owner_uid=owner)
    return identity.uid, identity.gid


@dataclass(frozen=True)
class MountSpec:
    """One explicit host<->container bind mount - TOOL-003 P2's own
    additive filesystem primitive, distinct from `dependency_cache_paths`
    (which is a positional list bound to a backend-selected in-container
    path) and from `workspace_path`/`temp_path` (which are always exactly
    one mount each). An MCP capability profile's `additional_read_paths`/
    `additional_write_paths` (kriya/mcp/capability.py) each become one
    `MountSpec` here - the container_path is Kriya-chosen and
    deterministic (never derived from anything MCP-server-supplied)."""

    host_path: str
    container_path: str
    writable: bool


@dataclass(frozen=True)
class ContainmentProfile:
    """What a subprocess is allowed to do - backend-independent by
    construction (no Docker/sandbox-exec-specific fields). See
    docs/architecture/SEC001_HOSTILE_CODE_CONTAINMENT_DESIGN.md §3/§6.

    TOOL-003 P2 (2026-09-13) additive fields - every one of them defaults
    to exactly today's pre-existing behavior for every current caller
    (PolymorphicValidator/service_runtime's compile/test/managed-service
    sandboxing), so this dataclass's extension is a strict superset, not a
    reinterpretation, of the SEC-001/SEC-006 contract those callers
    already rely on:

    `mount_workspace`/`workspace_write` (default True/True = today's
    "always mount workspace_path RW" behavior, unconditionally): an MCP
    capability profile with neither `workspace_read` nor `workspace_write`
    granted maps to `mount_workspace=False` (no workspace mount inside the
    container AT ALL - Task 3's "no workspace access unless granted");
    `workspace_read=True` alone maps to `mount_workspace=True,
    workspace_write=False` (a real read-only bind mount, not merely a
    policy note - Task 3's "workspace RO means writes fail").

    `additional_mounts` (default empty = no change for any existing
    caller): explicit extra host<->container binds - kriya/mcp/capability.py's
    already-resolved, already-escape-checked `additional_read_paths`/
    `additional_write_paths` map directly onto this, one `MountSpec` each.

    `resolved_env` (default None = today's behavior: `env_allowlist` +
    `build_restricted_env()` decide the container's `-e` flags): when set,
    used INSTEAD of `env_allowlist` - MCP's own SEC-003
    `build_mcp_subprocess_env()` output is already the complete, correct,
    restricted environment for an MCP server; re-deriving it through
    `env_allowlist`'s differently-purposed baseline (LANG/TMPDIR/PATH, no
    HOME) would silently discard SEC-003's own env-composition decision
    (e.g. an MCP server's explicit `mcp.<server>.env` HOME override) or
    require duplicating it under a second name. Never both set (a
    programming error, not a silent precedence rule) - see
    `OCIContainmentBackend.prepare()`'s own guard.

    `persistent_stdio` (default False = today's behavior: no `-i` flag,
    matching every existing finite/managed-service caller, none of which
    need the container's stdin attached to the spawned `docker run`
    process's own stdin): True adds `-i` (interactive, keep stdin open) -
    NEVER `-t` (a pseudo-TTY's line-discipline/echo would corrupt MCP's
    newline-delimited JSON-RPC framing) - so a long-lived MCP server's
    stdin/stdout can be driven directly through the `docker run` CLI
    process's own stdio, letting `MCPClient`'s existing JSON-RPC reader/
    writer work completely unchanged (Task 8: "existing MCP JSON-RPC
    reader/writer continues unchanged as much as possible").

    `run_as_uid`/`run_as_gid` (default None/None = today's behavior: no
    `--user` flag, root inside the container, unchanged for every existing
    compile/test/managed-service caller - out of scope for this pass to
    revisit): an explicit `(uid, gid)` pair adds `--user uid:gid` -
    TOOL-003 P2's own non-root requirement (Task 7) sets this for every
    MCP-derived profile, never for the pre-existing callers above.

    `authority_label` (default None = no `--label` on the DENIED/
    UNRESTRICTED path, unchanged): an opaque string (TOOL-003 P2 sets this
    to the MCP capability-profile digest) attached as
    `--label kriya.mcp-capability-digest=<value>` - the audit/cleanup-proof
    chain Task 12 requires (server identity -> capability digest ->
    container instance), queryable via `docker ps --filter label=...`
    without needing to trust the caller's own bookkeeping."""

    trust_class: TrustClass
    workspace_path: str
    temp_path: Optional[str] = None
    dependency_cache_paths: List[str] = field(default_factory=list)
    dependency_cache_writable: bool = False
    network: NetworkAuthority = NetworkAuthority.DENIED
    # SEC-006 (2026-09-12): the ONLY carrier of destination authority for
    # NetworkAuthority.DEPENDENCY_REGISTRY_ONLY - exact hostnames, always
    # sourced from Kriya's own AutonomyConfig.acquisition_registry_hosts
    # (never repository content). A tuple, not a list - ContainmentProfile
    # is frozen/hashable and this field must be too; it also doubles as the
    # canonical input to the per-run authority-identity hash a backend
    # computes for observability/isolation (kriya/tools/containment_oci.py).
    # Meaningless (and ignored) for DENIED/UNRESTRICTED profiles.
    network_destinations: Tuple[str, ...] = ()
    # PRD-034: the operator's Maven registry mirror (AutonomyConfig.acquisition_registry_mirror, already validated; its host is among
    # ``network_destinations``). None = nothing changes. Only meaningful for DEPENDENCY_REGISTRY_ONLY.
    registry_mirror: Optional[str] = None
    env_allowlist: List[str] = field(default_factory=list)
    cpu_seconds: Optional[int] = None
    memory_mb: Optional[int] = None
    mount_workspace: bool = True
    workspace_write: bool = True
    additional_mounts: Tuple[MountSpec, ...] = ()
    resolved_env: Optional[Dict[str, str]] = None
    persistent_stdio: bool = False
    run_as_uid: Optional[int] = None
    run_as_gid: Optional[int] = None
    authority_label: Optional[str] = None
    # PRD-011: resolved from PolymorphicValidator's existing stack
    # detection.  The OCI backend must honor this exact versioned profile;
    # None preserves generic/MCP containment callers that have no project
    # toolchain.
    toolchain_identity: Optional["_toolchain_identity_module.ToolchainIdentity"] = None
    # PRD-031A: an exact, digest-pinned image (``repo@sha256:<digest>``) for
    # a Kriya-invoked tool that ships its own image (the static-analysis
    # scanner). None (default) keeps every existing caller's image
    # selection. Mutually exclusive with toolchain_identity; a tag without a
    # digest is refused by the OCI backend, never resolved.
    image_reference: Optional[str] = None
    # OD-1 (BACKEND-FINAL-CLOSURE-005): a sanitized, read-only Git metadata
    # export (kriya/tools/git_metadata.py) the backend presents as the
    # workspace's ``.git`` - commits, reachable tags and refs of the exact
    # baseline, nothing else. None (default) keeps the no-Git masking every
    # existing caller gets today.
    git_metadata: Optional["GitMetadataMount"] = None

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


TARGET_NOT_STARTED_MARKER = "KRIYA_TARGET_NOT_STARTED:"
LAUNCHER_REACHED_MARKER = "KRIYA_LAUNCHER_REACHED:"


@dataclass(frozen=True)
class FiniteLaunchStage:
    """REPLAY-CONTAINED-NOT-STARTED-001: the backend-owned fact "did the requested target process actually start?" for a FINITE contained command.

    A container reports a target that cannot be executed through its own exit status (a shell or an image entrypoint answers 126/127), which a target that started and
    exited 127 shares - so the status proves nothing. The stage is the backend's own launcher: it wraps the target argv, runs after the image entrypoint in the same container (same
    mounts, working directory and environment), and ``exec``s the target. A launcher that is still alive after its ``exec`` means the exec failed, and only then does it write
    one nonce-authenticated record (``marker``) to stderr. ``read`` separates that record from the captured stderr: it is never parsed English, and it is never produced by a target
    that started (the nonce is in the launcher's argv, which the target's exec replaces).

    FINITE-START-PRECONTAINER-REFUSAL-001: the launcher also writes ONE ``reached`` record before its ``exec``. A target started only if that record is present and no
    ``marker`` record follows it: a run whose stderr carries no ``reached`` record never reached the launcher at all (the container runtime refused before any container existed,
    the image has no ``/bin/sh``, ...), which is NOT a started target whatever exit status the runtime answered. The ``reached`` record needs the same nonce, so a target
    cannot forge one; and a forged ``reached`` could only make a never-started run look started, which is the pre-stage behaviour, never a start that did not happen."""

    prefix: Tuple[str, ...]
    marker: str
    reached: str

    def wrap(self, command: List[str]) -> List[str]:
        # FINITE-LAUNCH-EMPTY-ARGV-001: the launcher's `exec "$@"` with no operands is a successful no-op, not a target, so an empty argv must never reach it (it would be a false NOT_STARTED with
        # status 0). `['']` is a real target that cannot start, which the launcher reports truthfully.
        if not command:
            raise ValueError("a finite contained command needs a target: the argv is empty")
        return [*self.prefix, *command]

    def read(self, stderr: str) -> Tuple[str, bool]:
        """(stderr without the launcher's records, whether the target started). The ``reached`` record is the launcher's own line, written once before the ``exec``; the ``marker``
        record is the LAST line the launcher writes, so it must be the final non-empty line."""
        record = f"\n{self.reached}\n"
        at = stderr.find(record)
        if at < 0:
            return stderr, False
        before = stderr[:at]
        stderr = before + ("" if not before or before.endswith("\n") else "\n") + stderr[at + len(record):]
        lines = stderr.split("\n")
        end = len(lines)
        while end and not lines[end - 1].strip():
            end -= 1
        if not end or lines[end - 1] != self.marker:
            return stderr, True
        kept = lines[:end - 1]
        if kept and not kept[-1].strip():  # the record is written on a line of its own (leading newline)
            kept.pop()
        return "\n".join(kept), False


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
      the ONE spawn point (`_spawn_popen`/`spawn_subprocess_exec_fail_closed`) - this
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
    toolchain_identity: Optional["_toolchain_identity_module.ToolchainIdentity"] = None
    # LINUX-JVM-RLIMIT-AS-001: how a host process's CPU/memory budget is
    # enforced (kriya/tools/sandbox.py::ResourcePlan.evidence). None when no
    # limit applies or a container enforces them.
    resources: Optional[Dict[str, object]] = None
    # REPLAY-CONTAINED-NOT-STARTED-001: the finite-command launch stage (see FiniteLaunchStage). None for a host process, a managed service and an MCP server: only
    # ProcessController.run()/run_async() apply it.
    finite_launch: Optional[FiniteLaunchStage] = None
    # W3-8 (PROCESS-ORPHAN-CHILD-OUTLIVES-PARENT-SIGKILL-001): argv lists the owner-death watchdog (ProcessControlPort.guard_owner_death) runs after it kills the spawned tree
    # when the OWNER dies without running ``cleanup`` (SIGKILL). A container belongs to the Docker daemon, not to the process group of the CLI that started it: MEASURED, a
    # container kept running and wrote to the bind-mounted workspace 18.9 s after its owner was killed. A host backend has nothing to add (the tree kill is enough).
    owner_death_cleanup: Tuple[Tuple[str, ...], ...] = ()


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
        setup choices. For project verification the resolved
        `profile.toolchain_identity` is security-relevant and must be
        honored exactly; command-based selection remains only for generic
        profiles without a toolchain identity. `NullContainmentBackend`/
        `DummyContainmentBackend` ignore it entirely."""
        ...


def require_single_environment_source(profile: ContainmentProfile) -> None:
    """``resolved_env`` and ``env_allowlist`` each define the child's whole environment, so a profile carrying both has no single
    answer: a programming error (``ValueError``), never a silent precedence rule. One guard for every backend."""
    if profile.resolved_env is not None and profile.env_allowlist:
        raise ValueError(
            "ContainmentProfile.resolved_env and .env_allowlist are mutually "
            "exclusive - pass a pre-resolved environment (MCP's own SEC-003 "
            "build_mcp_subprocess_env() output) OR an allowlist name-list, never both."
        )


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
        # W3-2 (CHANGE TYPE D - a contract correction, not a mechanical edit): `resolved_env` is the caller's COMPLETE, already
        # resolved child environment (it was honoured by the OCI backend only; this backend silently dropped it and inherited the
        # whole parent environment). A caller that supplies one gets exactly it; `env_allowlist` and neither are unchanged.
        require_single_environment_source(profile)
        if profile.resolved_env is not None:
            env: Optional[Dict[str, str]] = dict(profile.resolved_env)
        else:
            env = build_restricted_env(profile.env_allowlist) if profile.env_allowlist else None
        if profile.cpu_seconds is None and profile.memory_mb is None:
            return PreparedContainment(env=env, preexec_fn=None, backend_name=self.name)
        # LINUX-JVM-RLIMIT-AS-001: RLIMIT_AS for ordinary commands, explicit
        # JVM memory bounds for a JVM-backed one (typed toolchain first).
        plan = resource_plan(
            command, profile.cpu_seconds, profile.memory_mb,
            language=profile.toolchain_identity.language if profile.toolchain_identity is not None else None,
        )
        return PreparedContainment(
            env=plan.apply_env(env), preexec_fn=plan.preexec_fn(), backend_name=self.name,
            resources=plan.evidence(),
        )


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


# --- D2 EXEC-LAYER S2: the ONE canonical decision "does this external workload run in a real container?" --------------------------------------
#
# Before S2 each consumer decided this itself from the same two configuration fields (the validator at fourteen places, ShellTool, the pip
# capability, static analysis, the acceptance-bundle runner, the egress report, the toolchain fingerprint), and a few consumers (JDTLS) never asked:
# a copy of the decision per site, which is how a site gets missed. Now every consumer asks THIS function, and tests/architecture/
# test_containment_decision_sites.py fails on a production read of the two fields anywhere else.
#
# The rule itself is ONE function (``_containment_is_mandatory``). The packaged-default migration (S7, owner decision 2026-10-25) made it unconditional and flipped
# the packaged defaults (backend "oci", both mandate fields true; explicit false is a typed config refusal, kriya.config.authority.parse_containment_mandate): there is
# NO operator opt-out, so a project-controlled workload is either contained by an isolating backend or refused typed BEFORE any process starts. The legacy
# HOST_EXECUTION_PERMITTED state no longer exists.
CONTAINMENT_REQUIRED = "CONTAINMENT_REQUIRED"
CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION = "CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION"
CONTAINMENT_BACKEND_UNKNOWN = "CONTAINMENT_BACKEND_UNKNOWN"
CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT = "CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT"
EXTERNAL_EXECUTION_REFUSAL_CODES = frozenset({
    CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION, CONTAINMENT_BACKEND_UNKNOWN, CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT,
})


class ExternalExecutionRefused(BackendUnavailableError):
    """Containment is mandatory for this workload and cannot be provided. A ``BackendUnavailableError`` (so every existing handler of a containment
    setup failure treats it as one) that carries a stable ``reason_code`` from ``EXTERNAL_EXECUTION_REFUSAL_CODES`` and the ``subject`` that asked."""

    def __init__(self, subject: str, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {subject}: {detail}")
        self.subject = subject
        self.reason_code = reason_code
        self.detail = detail


# D2 S5: the subject of an MCP server. MCP servers have always had their own pre-migration mandate (``mcp_contained_execution_required``, a separate
# operator decision from target-code containment); it is expressed HERE, in the one rule, never at the MCP consumer. The final production contract
# makes containment unconditionally mandatory for every subject, and that is one edit to ``_containment_is_mandatory``.
MCP_SUBJECT_PREFIX = "MCP server"


def mcp_subject(server_name: str) -> str:
    return f"{MCP_SUBJECT_PREFIX} {server_name!r}"


def _containment_is_mandatory(autonomy: object, subject: str) -> bool:
    """THE rule: project-controlled external execution ALWAYS runs in a real container (packaged-default migration, owner decision 2026-10-25: there is NO operator opt-out).
    Unconditional on purpose - it reads no configuration, so a forged, ``model_construct``-ed or legacy object (contained_execution_required False) cannot open a host
    path; ``subject`` stays in the signature so a rule that differs by consumer is still expressed here, in one place, never at a consumer. The configuration fields are
    validated to True at every ingress (kriya.config.authority.parse_containment_mandate); trusted host infrastructure (GitTool, the neutral-cwd toolchain probe) does not ask
    this question - it is explicitly code-classified."""
    del autonomy, subject
    return True


def backend_provides_isolation(backend: object) -> bool:
    """The one fact "this backend isolates a workload from the host" (the Null backend - env allowlist and rlimits only - does not). The owner's
    refusal and the static-analysis provider's own "never fall back to the host" guard both ask this, so the fact is defined once."""
    return not isinstance(backend, NullContainmentBackend)


@dataclass(frozen=True)
class ExternalExecutionDecision:
    """The answer for one consumer (``subject``): ``contained`` (a real backend must run it) or host execution is permitted. ``backend_name`` is
    the configured backend; ``backend()`` resolves it, or refuses, typed."""
    subject: str
    contained: bool
    backend_name: str
    reason_code: str

    def refusal(self) -> Optional[ExternalExecutionRefused]:
        """The typed refusal ``backend()`` would raise, as a value (None when host execution is permitted or the backend isolates), for a consumer
        that degrades or reports instead of raising."""
        if not self.contained:
            return None
        try:
            resolved = resolve_containment_backend(self.backend_name)
        except BackendUnavailableError as error:
            return ExternalExecutionRefused(self.subject, CONTAINMENT_BACKEND_UNKNOWN, str(error))
        return self._refusal_for(resolved)

    def _refusal_for(self, resolved: ContainmentBackend) -> Optional[ExternalExecutionRefused]:
        if backend_provides_isolation(resolved):
            return None
        return ExternalExecutionRefused(
            self.subject, CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION,
            f"containment is required but the configured backend {self.backend_name!r} provides no filesystem, process or network isolation; "
            "refusing rather than running the workload uncontained")

    def require_isolating(self, resolved: ContainmentBackend) -> ContainmentBackend:
        """For a consumer that resolves its backend through its OWN documented seam (ShellTool's module-level ``resolve_containment_backend``, which
        its tests patch): the owner still decides, on the backend the consumer holds. Returns ``resolved``; raises ``ExternalExecutionRefused`` when
        containment is mandatory and ``resolved`` provides no isolation."""
        refusal = self._refusal_for(resolved) if self.contained else None
        if refusal is not None:
            raise refusal
        return resolved

    def backend(self) -> Optional[ContainmentBackend]:
        """None when host execution is permitted; otherwise the resolved isolating backend. Raises ``ExternalExecutionRefused`` when containment is
        mandatory and the configured backend cannot provide it (never a silent fallback to the host)."""
        if not self.contained:
            return None
        refusal = self.refusal()
        if refusal is not None:
            raise refusal
        return resolve_containment_backend(self.backend_name)

    def configured_backend(self) -> ContainmentBackend:
        """The backend the operator configured, resolved, WITHOUT the mandate: for a consumer that uses a sandbox when one is configured and is not
        required to (ShellTool with ``sandbox_execution``), and for one that reports or degrades itself. Unknown names still fail closed."""
        return resolve_containment_backend(self.backend_name)

    def refuse_unsupported(self, why: str) -> None:
        """For a consumer that has NO contained mode: raises ``ExternalExecutionRefused`` when containment is mandatory (it must not run on the
        host), returns when host execution is permitted."""
        if self.contained:
            raise ExternalExecutionRefused(self.subject, CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT, why)


def require_isolating_backend(subject: str, backend: Optional[ContainmentBackend]) -> ContainmentBackend:
    """For a consumer that is HANDED the backend it must use (an MCP client constructed with one) rather than resolving it from configuration:
    containment is mandatory for it, so a missing backend, or one that provides no isolation (the Null backend accepts an unrestricted-network
    profile and runs the workload on the host), is the typed refusal - before anything starts. The one place that fact is applied to a held backend."""
    if backend is None:
        raise ExternalExecutionRefused(
            subject, CONTAINMENT_BACKEND_UNKNOWN,
            "containment is required but no containment backend was resolved; refusing rather than running the workload uncontained")
    decision = ExternalExecutionDecision(
        subject=subject, contained=True, backend_name=str(getattr(backend, "name", "")), reason_code=CONTAINMENT_REQUIRED)
    return decision.require_isolating(backend)


def declare_execution_copy(validator: Any, created_path: str) -> None:
    """What the CREATOR of a Kriya-owned, disposable, git-less execution copy calls on the validator its factory returned for that copy (D2 S3, creator provenance):
    only the component that made the copy may declare it, never a downstream factory and never a path convention. A factory may return a stand-in that executes
    nothing (it has no ``declare_execution_copy``): there is nothing to declare for it; a real ``PolymorphicValidator`` always declares (and refuses a tree that is not its
    own, or the run's original workspace)."""
    declare = getattr(validator, "declare_execution_copy", None)
    if declare is not None:
        declare(created_path)


def decide_external_execution(autonomy: object, *, subject: str) -> ExternalExecutionDecision:
    """The canonical decision for ``subject`` under ``autonomy`` (an ``AutonomyConfig``). Pure: reads configuration, starts nothing."""
    return ExternalExecutionDecision(
        subject=subject, contained=_containment_is_mandatory(autonomy, subject),
        backend_name=str(getattr(autonomy, "containment_backend", "none")), reason_code=CONTAINMENT_REQUIRED)


# Annotation-only modules (PRD-001), imported at runtime so
# typing.get_type_hints() resolves. Module imports at the end of this
# module: they import it back, and a module object (unlike a name) can be
# bound while either side is still initializing, whichever loads first.
import kriya.tools.toolchain_identity as _toolchain_identity_module  # noqa: E402

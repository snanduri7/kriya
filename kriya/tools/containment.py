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
    successfully prepared - the exact `env`/`preexec_fn` shape
    `subprocess.Popen`/`asyncio.create_subprocess_exec` already accept, so
    no caller needs new subprocess-launch code paths."""

    env: Optional[Dict[str, str]]
    preexec_fn: Optional[Callable[[], None]]
    backend_name: str


class ContainmentBackend(Protocol):
    """One implementation per real enforcement mechanism (sandbox-exec,
    OCI, none). Deliberately NOT an abstract base class with shared state -
    a backend is a pure `profile -> PreparedContainment` function with a
    name, nothing about a specific mechanism belongs in this contract."""

    @property
    def name(self) -> str: ...

    def prepare(self, profile: ContainmentProfile) -> PreparedContainment:
        """Raises `ContainmentSetupError` (or a subclass) if this backend
        cannot honor `profile` - never returns a partially-honored
        result."""
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

    def prepare(self, profile: ContainmentProfile) -> PreparedContainment:
        env = build_restricted_env(profile.env_allowlist) if profile.env_allowlist else None
        preexec_fn = None
        if profile.cpu_seconds is not None or profile.memory_mb is not None:
            preexec_fn = posix_resource_limits_preexec_fn(
                profile.cpu_seconds or 0, profile.memory_mb or 0
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

    def prepare(self, profile: ContainmentProfile) -> PreparedContainment:
        if self.should_fail:
            raise BackendUnavailableError(self.failure_message)
        self.prepared_profiles.append(profile)
        env = build_restricted_env(profile.env_allowlist) if profile.env_allowlist else None
        return PreparedContainment(env=env, preexec_fn=None, backend_name=self.name)


_PRODUCTION_BACKEND_REGISTRY: Dict[str, Callable[[], ContainmentBackend]] = {
    "none": NullContainmentBackend,
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

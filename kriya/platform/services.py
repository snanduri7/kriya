"""PlatformServices: the one composition point for platform mechanism
(ARCH-PLATFORM-001).

The host is classified once, here; provider modules are imported lazily so
a module that only exists on one family (``fcntl``) never loads on another.
A port with no provider for this host is composed as an UNAVAILABLE
provider: Kriya still imports, and a use of the missing capability is a
typed refusal. Tests replace the services with ``override()``; nothing in
kriya/ registers a fake provider.
"""
from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from kriya.platform.capabilities import CapabilityReport
from kriya.platform.host_identity import HostIdentityPort, UnavailableHostIdentity
from kriya.platform.locking import UnavailableWorkspaceLock, WorkspaceLockPort
from kriya.platform.process_control import ProcessControlPort, UnavailableProcessControl
from kriya.platform.resource_limits import ResourceLimitPort, UnavailableResourceLimits

POSIX = "posix"
WINDOWS = "windows"

# S5 (OI-101, PLAT-WINDOWS-ENTRY-REFUSAL): the platforms Kriya may run on (handover/PLATFORM_ARCHITECTURE.md §13:
# Linux PRODUCTION_CERTIFIED, macOS DEVELOPMENT_AND_TARGET_CERTIFIED). Every other platform is refused at every entry
# (CLI, KUP server, MCP server) before anything reads the workspace or applies policy. Exact ``sys.platform`` values.
SUPPORTED_RUNTIME_PLATFORMS = frozenset({"linux", "darwin"})
PLATFORM_WINDOWS_UNSUPPORTED = "PLATFORM_WINDOWS_UNSUPPORTED"
PLATFORM_UNKNOWN_UNSUPPORTED = "PLATFORM_UNKNOWN_UNSUPPORTED"
# The closed refusal table: reason code -> operator message (``{platform}`` is the sys.platform value).
ENTRY_REFUSAL_REASONS = {
    PLATFORM_WINDOWS_UNSUPPORTED: ("Kriya is not supported on Windows ({platform}): the Windows runtime is not "
                                   "implemented (PLAT-WINDOWS-PROVIDERS-001). Run Kriya on Linux or macOS."),
    PLATFORM_UNKNOWN_UNSUPPORTED: ("Kriya is not supported on this platform ({platform!r}): only Linux and macOS are "
                                   "supported. Run Kriya on Linux or macOS."),
}
# sysexits EX_UNAVAILABLE; no Kriya command exits with it for any other reason.
ENTRY_REFUSED_EXIT_CODE = 69


class PlatformEntryRefused(RuntimeError):
    """This host's platform is not one Kriya may run on: a typed refusal at entry, never a partial run."""

    def __init__(self, reason_code: str, platform: str) -> None:
        self.reason_code = reason_code
        self.platform = platform
        super().__init__(f"{reason_code}: " + ENTRY_REFUSAL_REASONS[reason_code].format(platform=platform))


def host_family(platform_name: Optional[str] = None) -> str:
    """The provider family for a ``sys.platform`` value."""
    name = sys.platform if platform_name is None else platform_name
    return WINDOWS if name.startswith(("win32", "cygwin", "msys")) else POSIX


@dataclass(frozen=True)
class PlatformServices:
    family: str
    workspace_lock: WorkspaceLockPort
    resource_limits: ResourceLimitPort
    host_identity: HostIdentityPort
    process_control: ProcessControlPort
    # The host's ``sys.platform`` value the services were composed for (the family alone cannot tell a supported POSIX
    # host from an unknown one). Not part of ``identity()``: certification evidence keys are unchanged.
    platform: str = field(default_factory=lambda: sys.platform)

    def capabilities(self) -> List[CapabilityReport]:
        return [self.workspace_lock.capability(), *self.resource_limits.capabilities(),
                self.host_identity.capability(), self.process_control.capability()]

    def entry_refusal(self) -> Optional[PlatformEntryRefused]:
        """None on a supported platform; otherwise the typed refusal every entry raises before doing anything. The
        family decides first (a Windows family is refused whatever name it carries), then the exact platform name."""
        if self.family == WINDOWS:
            return PlatformEntryRefused(PLATFORM_WINDOWS_UNSUPPORTED, self.platform)
        if self.platform not in SUPPORTED_RUNTIME_PLATFORMS:
            return PlatformEntryRefused(PLATFORM_UNKNOWN_UNSUPPORTED, self.platform)
        return None

    def identity(self) -> Dict[str, object]:
        """Provider identity: mechanism evidence for certification records."""
        return {"family": self.family,
                "providers": {"workspace_lock": self.workspace_lock.name,
                              "resource_limits": self.resource_limits.name,
                              "host_identity": self.host_identity.name,
                              "process_control": self.process_control.name},
                "capabilities": [report.to_dict() for report in self.capabilities()]}


def compose(family: str, platform_name: Optional[str] = None) -> PlatformServices:
    """The services for ``family``; ``platform_name`` (default: this host's ``sys.platform``) is recorded on them."""
    platform = sys.platform if platform_name is None else platform_name
    if family == POSIX:
        from kriya.platform.posix_host_identity import PosixUidGid
        from kriya.platform.posix_locking import PosixFlockLock
        from kriya.platform.posix_process_control import PosixProcessGroup
        from kriya.platform.posix_resource_limits import PosixRlimit

        return PlatformServices(family=family, workspace_lock=PosixFlockLock(), resource_limits=PosixRlimit(),
                                host_identity=PosixUidGid(), process_control=PosixProcessGroup(), platform=platform)
    reason = f"no {family} provider yet (Windows runtime is not supported; PLAT-WINDOWS-PROVIDERS-001)"
    return PlatformServices(family=family, workspace_lock=UnavailableWorkspaceLock(reason),
                            resource_limits=UnavailableResourceLimits(reason),
                            host_identity=UnavailableHostIdentity(reason),
                            process_control=UnavailableProcessControl(reason), platform=platform)


_lock = threading.Lock()
_services: Optional[PlatformServices] = None
_override: Optional[PlatformServices] = None


def platform_services() -> PlatformServices:
    global _services
    if _override is not None:
        return _override
    if _services is None:
        with _lock:
            if _services is None:
                _services = compose(host_family())
    return _services


def entry_refusal() -> Optional[PlatformEntryRefused]:
    """This process's entry decision (the composed - or test-overridden - services)."""
    return platform_services().entry_refusal()


@contextmanager
def override(services: PlatformServices) -> Iterator[PlatformServices]:
    """Tests only: serve ``services`` for the duration of the block."""
    global _override
    previous, _override = _override, services
    try:
        yield services
    finally:
        _override = previous

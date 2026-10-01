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
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional

from kriya.platform.capabilities import CapabilityReport
from kriya.platform.host_identity import HostIdentityPort, UnavailableHostIdentity
from kriya.platform.locking import UnavailableWorkspaceLock, WorkspaceLockPort
from kriya.platform.process_control import ProcessControlPort, UnavailableProcessControl
from kriya.platform.resource_limits import ResourceLimitPort, UnavailableResourceLimits

POSIX = "posix"
WINDOWS = "windows"


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

    def capabilities(self) -> List[CapabilityReport]:
        return [self.workspace_lock.capability(), *self.resource_limits.capabilities(),
                self.host_identity.capability(), self.process_control.capability()]

    def identity(self) -> Dict[str, object]:
        """Provider identity: mechanism evidence for certification records."""
        return {"family": self.family,
                "providers": {"workspace_lock": self.workspace_lock.name,
                              "resource_limits": self.resource_limits.name,
                              "host_identity": self.host_identity.name,
                              "process_control": self.process_control.name},
                "capabilities": [report.to_dict() for report in self.capabilities()]}


def compose(family: str) -> PlatformServices:
    if family == POSIX:
        from kriya.platform.posix_host_identity import PosixUidGid
        from kriya.platform.posix_locking import PosixFlockLock
        from kriya.platform.posix_process_control import PosixProcessGroup
        from kriya.platform.posix_resource_limits import PosixRlimit

        return PlatformServices(family=family, workspace_lock=PosixFlockLock(), resource_limits=PosixRlimit(),
                                host_identity=PosixUidGid(), process_control=PosixProcessGroup())
    reason = f"no {family} provider yet (Windows runtime is not supported; PLAT-WINDOWS-PROVIDERS-001)"
    return PlatformServices(family=family, workspace_lock=UnavailableWorkspaceLock(reason),
                            resource_limits=UnavailableResourceLimits(reason),
                            host_identity=UnavailableHostIdentity(reason),
                            process_control=UnavailableProcessControl(reason))


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


@contextmanager
def override(services: PlatformServices) -> Iterator[PlatformServices]:
    """Tests only: serve ``services`` for the duration of the block."""
    global _override
    previous, _override = _override, services
    try:
        yield services
    finally:
        _override = previous

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
from kriya.platform.locking import UnavailableWorkspaceLock, WorkspaceLockPort

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

    def capabilities(self) -> List[CapabilityReport]:
        return [self.workspace_lock.capability()]

    def identity(self) -> Dict[str, object]:
        """Provider identity: mechanism evidence for certification records."""
        return {"family": self.family,
                "providers": {"workspace_lock": self.workspace_lock.name},
                "capabilities": [report.to_dict() for report in self.capabilities()]}


def compose(family: str) -> PlatformServices:
    if family == POSIX:
        from kriya.platform.posix_locking import PosixFlockLock

        return PlatformServices(family=family, workspace_lock=PosixFlockLock())
    reason = f"no {family} provider yet (Windows runtime is not supported; PLAT-WINDOWS-PROVIDERS-001)"
    return PlatformServices(family=family, workspace_lock=UnavailableWorkspaceLock(reason))


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

"""ResourceLimitPort (ARCH-PLATFORM-001, PLAT-RESOURCE-LIMITS-001).

How a host process's CPU time and address space are bounded is platform
mechanism. Which strategy applies (address space, JVM heap, unbounded) is
Kriya policy and stays in kriya/tools/sandbox.py (``resource_plan``); a
provider applies the limits it is asked for and reports how strongly each
is enforced. A limit the host cannot apply is a typed refusal, never an
unbounded run.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Protocol

from kriya.platform.capabilities import (
    CapabilityReport,
    CapabilityStatus,
    PlatformCapability,
    PlatformCapabilityUnavailable,
)


class ResourceLimitPort(Protocol):
    name: str

    def capabilities(self) -> List[CapabilityReport]: ...

    def address_space_enforcement(self) -> CapabilityStatus: ...

    def preexec_fn(self, cpu_seconds: Optional[int], memory_mb: Optional[int]) -> Optional[Callable[[], None]]:
        """A function the child runs before exec to apply the limits (None
        when neither is requested). A failure inside it propagates to the
        spawn, never leaves the child unbounded."""


class UnavailableResourceLimits:
    """No resource-limit provider for this host: a requested limit is refused."""

    name = "unavailable"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def _report(self, capability: PlatformCapability) -> CapabilityReport:
        return CapabilityReport(capability, CapabilityStatus.UNAVAILABLE, self.name, self._reason)

    def capabilities(self) -> List[CapabilityReport]:
        return [self._report(PlatformCapability.POSIX_RLIMIT_CPU), self._report(PlatformCapability.POSIX_RLIMIT_AS)]

    def address_space_enforcement(self) -> CapabilityStatus:
        return CapabilityStatus.UNAVAILABLE

    def preexec_fn(self, cpu_seconds: Optional[int], memory_mb: Optional[int]) -> Optional[Callable[[], None]]:
        if cpu_seconds is not None:
            raise PlatformCapabilityUnavailable(self._report(PlatformCapability.POSIX_RLIMIT_CPU))
        if memory_mb is not None:
            raise PlatformCapabilityUnavailable(self._report(PlatformCapability.POSIX_RLIMIT_AS))
        return None

"""WorkspaceLockPort (ARCH-PLATFORM-001, PLAT-003).

An advisory, non-blocking, process-lifetime lock on an open file
descriptor: exclusive for the owner of a workspace, shared for a read-only
probe. The operating system releases it when the process dies, however it
dies (FILE_LOCK_CRASH_SAFE). kriya/control/run_ownership.py owns the lock
file, its diagnostics payload and every decision; a provider owns only the
lock call.
"""
from __future__ import annotations

from typing import Protocol

from kriya.platform.capabilities import (
    CapabilityReport,
    CapabilityStatus,
    PlatformCapability,
    PlatformCapabilityUnavailable,
)


class WorkspaceLockPort(Protocol):
    name: str

    def capability(self) -> CapabilityReport: ...

    def try_exclusive(self, fd: int) -> bool:
        """Take the exclusive lock without blocking: False when it cannot be
        taken (held elsewhere)."""

    def try_shared(self, fd: int) -> bool:
        """Take a shared lock without blocking: False when an exclusive lock
        is held."""

    def release(self, fd: int) -> None:
        """Release this descriptor's lock; releasing an unlocked descriptor is
        not an error."""


class UnavailableWorkspaceLock:
    """No crash-safe lock provider for this host: every use is refused."""

    name = "unavailable"

    def __init__(self, reason: str) -> None:
        self._report = CapabilityReport(PlatformCapability.FILE_LOCK_CRASH_SAFE, CapabilityStatus.UNAVAILABLE,
                                        self.name, reason)

    def capability(self) -> CapabilityReport:
        return self._report

    def try_exclusive(self, fd: int) -> bool:
        raise PlatformCapabilityUnavailable(self._report)

    def try_shared(self, fd: int) -> bool:
        raise PlatformCapabilityUnavailable(self._report)

    def release(self, fd: int) -> None:
        raise PlatformCapabilityUnavailable(self._report)

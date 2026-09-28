"""POSIX ``flock`` provider for WorkspaceLockPort (macOS and Linux).

Imported only by kriya/platform/services.py on a POSIX host, so ``fcntl``
never loads where it does not exist.
"""
from __future__ import annotations

import fcntl

from kriya.platform.capabilities import CapabilityReport, CapabilityStatus, PlatformCapability


class PosixFlockLock:
    name = "posix-flock"

    def capability(self) -> CapabilityReport:
        return CapabilityReport(PlatformCapability.FILE_LOCK_CRASH_SAFE, CapabilityStatus.ENFORCED, self.name,
                                "flock: released by the kernel when the holding process exits")

    def try_exclusive(self, fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def try_shared(self, fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def release(self, fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

"""POSIX ``setrlimit`` provider for ResourceLimitPort (macOS and Linux).

Moved unchanged from kriya/tools/sandbox.py (SEC-001, SEC-001 fail-closed
correction 2026-09-11, LINUX-JVM-RLIMIT-AS-001):

- RLIMIT_CPU is reliably settable and enforced on Linux and macOS; a
  failure fails closed.
- RLIMIT_AS is enforced on Linux; a failure there fails closed. On macOS
  ``setrlimit(RLIMIT_AS, ...)`` can itself fail ("current limit exceeds
  maximum limit") for an ordinary value, so there it is ADVISORY: the
  failure is logged and the child still runs with its CPU limit.
- RLIMIT_NPROC is deliberately not set: on Linux it is per-EUID, shared by
  every process of the account.

``resource`` is imported inside the child function (the parent never needs
it), and a failure there reaches the parent as the spawn's own
SubprocessError, which ProcessController turns into a typed
ContainmentSetupError.
"""
from __future__ import annotations

import logging
import sys
from typing import Callable, List, Optional

from kriya.platform.capabilities import CapabilityReport, CapabilityStatus, PlatformCapability

logger = logging.getLogger(__name__)


def _address_space_is_advisory() -> bool:
    return sys.platform == "darwin"


class PosixRlimit:
    name = "posix-setrlimit"

    def address_space_enforcement(self) -> CapabilityStatus:
        return CapabilityStatus.ADVISORY if _address_space_is_advisory() else CapabilityStatus.ENFORCED

    def capabilities(self) -> List[CapabilityReport]:
        advisory = _address_space_is_advisory()
        return [
            CapabilityReport(PlatformCapability.POSIX_RLIMIT_CPU, CapabilityStatus.ENFORCED, self.name),
            CapabilityReport(PlatformCapability.POSIX_RLIMIT_AS, self.address_space_enforcement(), self.name,
                             "setrlimit(RLIMIT_AS) may be refused by macOS" if advisory else ""),
        ]

    def preexec_fn(self, cpu_seconds: Optional[int], memory_mb: Optional[int]) -> Optional[Callable[[], None]]:
        # None for one dimension leaves it untouched; 0 would be a literal
        # (0, 0) limit, i.e. a process that cannot run at all.
        def _set_limits() -> None:
            import resource

            if cpu_seconds is not None:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            if memory_mb is not None:
                memory_bytes = memory_mb * 1024 * 1024
                try:
                    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
                except (ValueError, OSError) as error:
                    if not _address_space_is_advisory():
                        raise
                    logger.debug(f"RLIMIT_AS could not be set on macOS (known platform limitation, memory "
                                 f"containment stays advisory-only here): {error}")

        return _set_limits

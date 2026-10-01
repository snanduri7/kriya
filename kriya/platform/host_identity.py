"""HostIdentityPort (ARCH-PLATFORM-001, PLAT-HOST-IDENTITY-001).

The trusted identity of the Kriya process and the owner of a host path are
platform mechanism: a POSIX uid/gid on macOS and Linux, an account SID on
Windows (no provider yet). Whether a container may run as that identity
(SEC-008: never root, every writable mount owned by it) is Kriya policy in
kriya/tools/containment.py; how a runtime maps it into a container is the
ContainmentBackend's. A provider answers questions; it grants nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kriya.platform.capabilities import (
    CapabilityReport,
    CapabilityStatus,
    PlatformCapability,
    PlatformCapabilityUnavailable,
)


@dataclass(frozen=True)
class HostIdentity:
    uid: int
    gid: int

    @property
    def is_privileged(self) -> bool:
        return self.uid == 0


class HostIdentityPort(Protocol):
    name: str

    def capability(self) -> CapabilityReport: ...

    def current(self) -> HostIdentity: ...

    def owner_uid(self, path: str) -> int: ...


class UnavailableHostIdentity:
    """No uid/gid identity on this host: every question is refused."""

    name = "unavailable"

    def __init__(self, reason: str) -> None:
        self._report = CapabilityReport(PlatformCapability.UID_GID_IDENTITY, CapabilityStatus.UNAVAILABLE,
                                        self.name, reason)

    def capability(self) -> CapabilityReport:
        return self._report

    def current(self) -> HostIdentity:
        raise PlatformCapabilityUnavailable(self._report)

    def owner_uid(self, path: str) -> int:
        raise PlatformCapabilityUnavailable(self._report)

"""POSIX uid/gid provider for HostIdentityPort (macOS and Linux)."""
from __future__ import annotations

import os

from kriya.platform.capabilities import CapabilityReport, CapabilityStatus, PlatformCapability
from kriya.platform.host_identity import HostIdentity


class PosixUidGid:
    name = "posix-uid-gid"

    def capability(self) -> CapabilityReport:
        return CapabilityReport(PlatformCapability.UID_GID_IDENTITY, CapabilityStatus.ENFORCED, self.name)

    def current(self) -> HostIdentity:
        return HostIdentity(uid=os.getuid(), gid=os.getgid())

    def owner_uid(self, path: str) -> int:
        return os.stat(path).st_uid

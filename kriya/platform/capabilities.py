"""The platform capability vocabulary (ARCH-PLATFORM-001).

Core code asks whether a capability is available and how strongly it is
enforced, never which operating system it runs on. A required capability
that is UNAVAILABLE is a typed refusal (``PlatformCapabilityUnavailable``),
never a silent downgrade or a host fallback.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

PLATFORM_CAPABILITY_UNAVAILABLE = "PLATFORM_CAPABILITY_UNAVAILABLE"


class PlatformCapability(str, enum.Enum):
    FILE_LOCK_CRASH_SAFE = "file_lock_crash_safe"
    PROCESS_TREE_TERMINATION = "process_tree_termination"
    POSIX_RLIMIT_CPU = "posix_rlimit_cpu"
    POSIX_RLIMIT_AS = "posix_rlimit_as"
    WINDOWS_JOB_OBJECT = "windows_job_object"
    UID_GID_IDENTITY = "uid_gid_identity"
    WINDOWS_ACCOUNT_IDENTITY = "windows_account_identity"


class CapabilityStatus(str, enum.Enum):
    ENFORCED = "enforced"
    ADVISORY = "advisory"      # applied, but the platform does not guarantee it
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CapabilityReport:
    """A provider's answer for one capability: mechanism evidence, never
    authority."""

    capability: PlatformCapability
    status: CapabilityStatus
    provider: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {"capability": self.capability.value, "status": self.status.value,
                "provider": self.provider, "reason": self.reason}


class PlatformCapabilityUnavailable(RuntimeError):
    """A required platform capability is not available on this host."""

    reason_code = PLATFORM_CAPABILITY_UNAVAILABLE

    def __init__(self, report: CapabilityReport) -> None:
        self.report = report
        super().__init__(
            f"{PLATFORM_CAPABILITY_UNAVAILABLE}: {report.capability.value} is not available "
            f"(provider {report.provider}){': ' + report.reason if report.reason else ''}"
        )


def require(report: CapabilityReport) -> CapabilityReport:
    """``report`` unless its capability is UNAVAILABLE, which is refused."""
    if report.status is CapabilityStatus.UNAVAILABLE:
        raise PlatformCapabilityUnavailable(report)
    return report

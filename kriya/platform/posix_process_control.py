"""POSIX session/process-group provider for ProcessControlPort (macOS and
Linux).

Moved unchanged from kriya/tools/process.py (SEC-001, SEC-004): the child
starts a new session (``start_new_session=True``), so it leads its own
process group and every descendant inherits that group; termination sends
SIGKILL to the whole group. A descendant that starts a session of its own
leaves the group; containing hostile code is the containment backend's job,
not this provider's.
"""
from __future__ import annotations

import os
import signal
from typing import Any, Dict

from kriya.platform.capabilities import CapabilityReport, CapabilityStatus, PlatformCapability


class PosixProcessGroup:
    name = "posix-process-group"

    def capability(self) -> CapabilityReport:
        return CapabilityReport(PlatformCapability.PROCESS_TREE_TERMINATION, CapabilityStatus.ENFORCED, self.name,
                                "process group: a descendant that starts its own session leaves the group")

    def spawn_options(self) -> Dict[str, Any]:
        return {"start_new_session": True}

    def terminate_tree(self, process: Any) -> None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            # Already gone: the desired end state, not a failure.
            pass

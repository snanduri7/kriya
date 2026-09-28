"""ProcessControlPort (ARCH-PLATFORM-001, PLAT-PROCESS-CONTROL-001).

Every Kriya-owned subprocess must be terminable together with every
descendant it starts (PROCESS_TREE_TERMINATION): a compile server, a test
fork or an MCP server's children must not outlive Kriya's timeout or
shutdown. How a process tree is owned is platform mechanism; when to
terminate it (timeouts, grace periods, reaping) stays with
kriya/tools/process.py and kriya/mcp/lifecycle.py.

A provider supplies the spawn options that put the child into a tree it
owns (``spawn_options``, merged by the one spawn point) and kills that whole
tree (``terminate_tree``). A host without a provider refuses at spawn, so
Kriya never holds a process whose descendants it cannot kill, and never
falls back to killing only the direct child.
"""
from __future__ import annotations

from typing import Any, Dict, Protocol

from kriya.platform.capabilities import (
    CapabilityReport,
    CapabilityStatus,
    PlatformCapability,
    PlatformCapabilityUnavailable,
)


class ProcessControlPort(Protocol):
    name: str

    def capability(self) -> CapabilityReport: ...

    def spawn_options(self) -> Dict[str, Any]:
        """Keyword arguments for ``subprocess.Popen`` /
        ``asyncio.create_subprocess_exec`` that make the child the root of a
        tree this provider can terminate."""

    def terminate_tree(self, process: Any) -> None:
        """Kill ``process`` and every descendant still in its tree.
        Idempotent: an already-exited process is not an error."""


class UnavailableProcessControl:
    """No process-tree provider for this host: nothing is spawned."""

    name = "unavailable"

    def __init__(self, reason: str) -> None:
        self._report = CapabilityReport(PlatformCapability.PROCESS_TREE_TERMINATION, CapabilityStatus.UNAVAILABLE,
                                        self.name, reason)

    def capability(self) -> CapabilityReport:
        return self._report

    def spawn_options(self) -> Dict[str, Any]:
        raise PlatformCapabilityUnavailable(self._report)

    def terminate_tree(self, process: Any) -> None:
        raise PlatformCapabilityUnavailable(self._report)

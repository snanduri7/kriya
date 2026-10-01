"""ProcessControlPort (ARCH-PLATFORM-001, PLAT-PROCESS-CONTROL-001).

Every Kriya-owned subprocess must be terminable together with every
descendant it starts (PROCESS_TREE_TERMINATION): a compile server, a test
fork or an MCP server's children must not outlive Kriya's timeout or
shutdown. How a process tree is owned is platform mechanism; when to
terminate it (timeouts, grace periods, reaping) stays with
kriya/tools/process.py and kriya/mcp/lifecycle.py.

A provider supplies the spawn options that put the child into a tree it
owns (``spawn_options``, merged by the one spawn point), binds the new
process to that tree right after it is created (``attach``, called by the
same spawn point before the process reaches any caller; a Windows Job
Object provider assigns the process to its job here) and kills that whole
tree (``terminate_tree``). A process whose ``attach`` fails is killed and
reaped by the spawn point and never handed to a caller. A host without a provider refuses at spawn, so
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

    def attach(self, process: Any) -> None:
        """Bind a just-created ``process`` to the tree this provider owns.
        Called exactly once per spawn, immediately after creation succeeds
        and before the process reaches any caller. Raising refuses the
        process: the spawn point kills and reaps it."""

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

    def attach(self, process: Any) -> None:
        raise PlatformCapabilityUnavailable(self._report)

    def terminate_tree(self, process: Any) -> None:
        raise PlatformCapabilityUnavailable(self._report)

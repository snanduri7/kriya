"""SEC-004: bounded MCP subprocess lifecycle primitives.

Split out of `kriya/mcp/mcp.py` so `MCPClient` stays focused on the JSON-RPC
protocol itself (Invariant: no security monolith) - this module owns the two
concerns that are genuinely about the PROCESS, not the protocol spoken over
it: fail-closed spawn (through a containment launch prefix) and bounded,
idempotent, process-tree-wide termination.

Reuses SEC-001's existing primitives rather than a second implementation
(Invariant 14): `spawn_subprocess_exec_fail_closed`/`terminate_process_tree`
(kriya/tools/process.py, promoted from ProcessController-only helpers this
same pass specifically so this module could reuse them) for the actual
spawn/kill mechanics - the same ProcessControlPort process-tree ownership
(kriya/platform/process_control.py; a POSIX session and `killpg(SIGKILL)`
on macOS/Linux; the port's `attach` runs right after creation, and a failed
attach kills and reaps the server before it is returned), the same
`ContainmentSetupError` fail-closed conversion, and the same process-tree kill every other Kriya-owned
subprocess lifecycle already uses.

Deliberately NOT built on `ProcessController.run()`/`run_async()`/
`start_managed()` directly - none of their lifecycles fit an MCP server:
`run()`/`run_async()` are finite (one `communicate()` call, done); `
start_managed()` is long-lived but synchronous (`subprocess.Popen`, stdin
DEVNULL'd, thread-drained READ-ONLY output) with no bidirectional stdio
support, which is exactly what an MCP server's stdin-request/stdout-response
JSON-RPC protocol needs. Building that out is native SEC-004 lifecycle
scope, not a rewrite of the shared primitives - see the risk register's own
SEC-004 entry for the full native-vs-OCI decision record.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from kriya.tools.containment import ContainmentSetupError
from kriya.tools.process import spawn_subprocess_exec_fail_closed, terminate_process_tree

logger = logging.getLogger(__name__)


class MCPLifecycleError(RuntimeError):
    """Base for SEC-004 lifecycle failures - distinct from an ordinary MCP
    tool-call failure (`kriya.tools.tool.ToolExecutionError`) and from a
    SEC-009 configuration-authority denial (`ConfigAuthorityError`). Never
    includes a configured secret value - only the server name and a fixed,
    non-value-bearing description of what bound was exceeded."""


class MCPStartupTimeoutError(MCPLifecycleError):
    """The complete startup sequence (process launch + initialize request +
    initialize response/handshake completion) did not finish within
    `mcp_lifecycle.startup_timeout_seconds`."""


class MCPRequestTimeoutError(MCPLifecycleError):
    """A single protocol request (`_send_request()`, including but not
    limited to `tools/list`/`tools/call`) did not receive a response within
    `mcp_lifecycle.request_timeout_seconds`."""


class MCPProtocolViolationError(MCPLifecycleError):
    """The server's stdout output violated the protocol transport's own
    bounds (currently: a single line/frame exceeding
    `mcp_lifecycle.max_stdout_line_bytes`) - fatal to the connection, since
    a truncated/oversized frame cannot be trusted for request/response
    correlation. Malformed-but-appropriately-sized JSON is NOT this error -
    that is logged and skipped, matching this codebase's existing,
    deterministic (if lenient) behavior; only a transport-level bound
    violation is fatal."""


async def spawn_mcp_process(
    command: str, args: List[str], env: Optional[Dict[str, str]], *,
    max_stdout_line_bytes: int, command_prefix: List[str],
) -> "asyncio.subprocess.Process":
    """Fail-closed MCP subprocess spawn THROUGH an isolating backend: process-tree ownership through the ProcessControlPort
    (`spawn_subprocess_exec_fail_closed`; on POSIX a new session, whose process group every descendant the server spawns inherits, which
    `terminate_mcp_process()` below relies on to guarantee no survivors) + a hard per-line stdout size ceiling (`limit=`, asyncio's own
    StreamReader bound - raises `ValueError` deterministically rather than allocating unboundedly, confirmed empirically to recover cleanly
    for the NEXT read rather than corrupting the stream).

    `command_prefix` is REQUIRED (S8 / containment qualification: there is no host mode). It is a real, already-prepared `docker run -i ...`
    argv (`kriya/tools/containment_oci.py`'s own `PreparedContainment.command_prefix`, composed via `kriya/mcp/containment_adapter.py`), and
    `command`/`args` are the real MCP server command appended AFTER it - the same command_prefix + command composition
    `kriya/tools/process.py`'s `_prepare_env_and_preexec()` uses for `run()`/`run_async()`/`start_managed()`. The container's own
    `--memory`/`--cpus` flags (already baked into the prefix by `OCIContainmentBackend.prepare()`) are the resource bound; no host rlimit applies
    to the lightweight `docker` CLI wrapper process. `env` is NOT the SEC-003-restricted MCP environment: the `docker` CLI itself is
    TRUSTED_KRIYA_INFRASTRUCTURE and needs its own normal host environment (PATH to find the `docker` binary, etc.); the untrusted MCP
    server's own environment is baked into `command_prefix` as `-e KEY=VALUE` flags instead (`ContainmentProfile.resolved_env`). `env=None`
    means "inherit the current process's environment", exactly right for invoking trusted host tooling.

    An empty prefix is refused (`ContainmentSetupError`): without one the server would be launched bare on the host, which this function can
    no longer do. Raises `kriya.tools.containment.ContainmentSetupError` (or a subclass) too if the process-control attach fails -
    propagates uncaught: the caller (MCPClient) must not catch this and retry uncontained."""
    if not command_prefix:
        raise ContainmentSetupError(
            f"MCP server {command!r}: no containment launch prefix was supplied - an MCP server has no host mode and is never spawned bare")
    return await spawn_subprocess_exec_fail_closed(
        *command_prefix, command, *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        limit=max_stdout_line_bytes,
    )


async def terminate_mcp_process(process: "asyncio.subprocess.Process", *, grace_seconds: float, reap_seconds: float) -> None:
    """The bounded shutdown state machine this risk requires:
    `RUNNING -> graceful SIGTERM -> bounded wait -> forced process-tree
    SIGKILL -> bounded reap -> CLOSED`. Idempotent (an already-exited
    process - `returncode is not None` - returns immediately, matching
    `ManagedProcess.terminate()`'s own idempotency check) and NEVER performs
    an unbounded `await process.wait()` at any step - both the graceful
    and the post-kill reap are `asyncio.wait_for`-bounded.

    The graceful phase signals ONLY the direct process (`process.terminate()`,
    unchanged from pre-SEC-004 behavior) - a cooperative server is expected
    to propagate shutdown to its own children if it has any; this is not
    where descendant cleanup is guaranteed. The FORCED phase
    (`terminate_process_tree`, SEC-001's shared process-tree kill through
    the ProcessControlPort) is what guarantees no survivors regardless of
    the server's own cooperation - it targets the whole tree, which every
    descendant the server spawns joined at spawn time
    (`spawn_mcp_process()` above).

    A reap that still times out AFTER the forced kill (confirmed possible
    empirically: an asyncio subprocess `wait()` can block on undrained
    pipe data even after the underlying OS process is already dead, since
    `BaseSubprocessTransport._try_finish()` requires every pipe transport
    to report disconnected, not just the process to have exited) is logged
    as a REAP TIMEOUT and treated as terminal regardless - by this point
    the OS-level process GROUP has already been sent SIGKILL, so the
    process's real death is assured independent of whether this specific
    asyncio bookkeeping call ever resolves; this method must never let
    that asyncio quirk hang the caller's own shutdown bound."""
    if process.returncode is not None:
        return

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        return
    except asyncio.TimeoutError:
        pass

    terminate_process_tree(process)
    try:
        await asyncio.wait_for(process.wait(), timeout=reap_seconds)
    except asyncio.TimeoutError:
        logger.warning(
            "MCP process group (pid=%s) did not report exit within %.1fs of the forced kill - "
            "the process group has already been sent SIGKILL; treating as terminated regardless "
            "of this asyncio reap call's own outcome (known asyncio pipe-drain interaction).",
            getattr(process, "pid", "?"), reap_seconds,
        )

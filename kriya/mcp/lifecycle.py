"""SEC-004: bounded MCP subprocess lifecycle primitives.

Split out of `kriya/mcp/mcp.py` so `MCPClient` stays focused on the JSON-RPC
protocol itself (Invariant: no security monolith) - this module owns the two
concerns that are genuinely about the PROCESS, not the protocol spoken over
it: fail-closed spawn (env/resource-limit composition) and bounded,
idempotent, process-tree-wide termination.

Reuses SEC-001's existing primitives rather than a second implementation
(Invariant 14): `posix_resource_limits_preexec_fn`/`build_restricted_env`
(kriya/tools/sandbox.py) for CPU/memory rlimits and env, and
`spawn_subprocess_exec_fail_closed`/`terminate_process_tree`
(kriya/tools/process.py, promoted from ProcessController-only helpers this
same pass specifically so this module could reuse them) for the actual
spawn/kill mechanics - the same `start_new_session=True` process-group
isolation, the same `ContainmentSetupError` fail-closed conversion on a
broken preexec_fn, and the same POSIX `killpg(SIGKILL)` process-tree kill
every other Kriya-owned subprocess lifecycle already uses.

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
import os
from typing import Dict, List, Optional

from kriya.tools.process import spawn_subprocess_exec_fail_closed, terminate_process_tree
from kriya.tools.sandbox import posix_resource_limits_preexec_fn

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
    command: str, args: List[str], env: Dict[str, str], *,
    cpu_seconds: Optional[int], memory_mb: Optional[int], max_stdout_line_bytes: int,
) -> "asyncio.subprocess.Process":
    """Fail-closed MCP subprocess spawn: process-group isolation
    (`start_new_session=True`, POSIX - the property every descendant the
    server spawns inherits the same group, which `terminate_mcp_process()`
    below relies on to guarantee no survivors) + CPU/memory rlimits
    (`posix_resource_limits_preexec_fn`, SEC-001, unchanged) + a hard
    per-line stdout size ceiling (`limit=`, asyncio's own StreamReader
    bound - raises `ValueError` deterministically rather than allocating
    unboundedly, confirmed empirically to recover cleanly for the NEXT
    read rather than corrupting the stream). `env` must already be the
    SEC-003-restricted environment (`build_mcp_subprocess_env()`'s result)
    - this function does not construct or re-validate it.

    Raises `kriya.tools.containment.ContainmentSetupError` (or
    `ResourceLimitSetupError`) if the resource-limit preexec_fn fails
    before the command could even start - propagates uncaught, exactly
    like every other `spawn_subprocess_exec_fail_closed` caller: SEC-004's
    own fail-closed requirement is "do not start the MCP server" when
    required controls cannot be established, and the caller (MCPClient)
    must not catch this and retry uncontained."""
    preexec_fn = posix_resource_limits_preexec_fn(cpu_seconds, memory_mb)
    return await spawn_subprocess_exec_fail_closed(
        command, *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        preexec_fn=preexec_fn,
        start_new_session=(os.name == "posix"),
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
    (`terminate_process_tree`, SEC-001's shared POSIX `killpg(SIGKILL)`
    primitive) is what guarantees no survivors regardless of the server's
    own cooperation - it targets the whole process GROUP, which every
    descendant the server spawns inherited at spawn time
    (`start_new_session=True` in `spawn_mcp_process()` above).

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

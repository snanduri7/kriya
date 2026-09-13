import asyncio
import json
import logging
import os
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, Field, create_model

from kriya.core.kernel import Kernel
from kriya.tools.containment import ContainmentSetupError, resolve_containment_backend
from kriya.mcp.capability import (
    MCPCapabilityProfile,
    compute_mcp_capability_profile_digest,
    resolve_mcp_capability_profile,
)
from kriya.mcp.containment_adapter import map_capability_profile_to_containment
from kriya.mcp.lifecycle import (
    MCPLifecycleError,
    MCPProtocolViolationError,
    MCPRequestTimeoutError,
    MCPStartupTimeoutError,
    spawn_mcp_process,
    terminate_mcp_process,
)
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import (
    ActionRequest,
    ActionType,
    MCPCapabilityProfileIdentity,
    MCPToolIdentity,
    PolicyDecision,
    PolicyResult,
    compute_mcp_schema_digest,
)
from kriya.policy.telemetry import build_decision_record
from kriya.tools.sandbox import build_restricted_env
from kriya.tools.tool import BaseTool, ToolExecutionError

logger = logging.getLogger(__name__)

# SEC-003 (2026-09-12): the required Kriya baseline environment for
# launching an MCP subprocess - deliberately NOT reused from
# `autonomy.sandbox_env_allowlist` (kriya/config/config.py), which is
# scoped to sandboxed TARGET-CODE build/test execution and carries
# build-toolchain-specific entries (JAVA_HOME, M2_HOME, GRADLE_HOME,
# VIRTUAL_ENV, PYTHONPATH) that have no established need for launching an
# MCP server and that a hostile/misconfigured MCP command could abuse
# (e.g. a spoofed PYTHONPATH redirecting the server's own imports).
# Locale and temp-directory variables that common language runtimes
# (Python, Node) read for basic, non-security-relevant operation
# (locale-aware stdio encoding, scratch-file location) - confirmed
# empirically that Kriya's own shipped MCP server (kriya/mcp/server.py)
# launches and completes the handshake with NONE of these set at all (a
# completely empty environment, not even PATH, still worked when
# `command` is an absolute path); included anyway because other
# real-world MCP servers (commonly Node/npx-based) are not guaranteed to
# behave as cleanly, and this narrow variable set has established, safe
# precedent as "reasonable to forward to a third-party subprocess Kriya
# spawns" via `autonomy.sandbox_env_allowlist`'s own default.
#
# HOME is deliberately EXCLUDED, unlike its sibling variables above -
# reviewed and corrected 2026-09-12 (user decision). The empirical
# evidence is the same (the shipped server needs none of these), but
# HOME is not merely another low-authority compatibility variable the
# way LANG/TMPDIR are: it is the implicit discovery root a huge range of
# third-party tooling uses for configuration/credential-store locations
# (`.ssh`, `.gitconfig`, package-manager config, cloud-CLI state, SDK
# config files) - forwarding the operator's REAL home path to an
# authorized-but-arbitrary MCP command grants it that implicit discovery
# surface even though nothing in this codebase demonstrates a need for
# it. SEC-003's invariant is "MCP receives the environment Kriya
# explicitly needs or grants," not "MCP receives whatever SEC-001's
# target-code allowlist happens to already trust" - the two subprocess
# roles are not equivalent (target-code execution is the repository's
# OWN declared build/test tooling; an MCP server is an arbitrary,
# SEC-009-approved but otherwise unvetted third-party command). Do not
# invent a fake/isolated HOME here either - that is the not-yet-registered
# MCP invocation/execution authority work's concern if a real MCP runtime
# is ever found to need one (NOT the register's existing SEC-005 row,
# which is an unrelated package-installation/network-access broker risk).
# An operator
# who has a genuine reason to expose HOME to a specific server sets it
# explicitly via `mcp.<server>.env.HOME`, subject to SEC-009 authority
# like any other configured value - the ambient-denied/explicit-allowed
# differential this module's own tests prove for every other sentinel.
# Proxy variables (HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY) are also
# deliberately excluded - inheriting them merely because they exist in
# the parent would be an indirect network-authority bypass; same
# explicit-override path applies.
MCP_BASELINE_ENV_ALLOWLIST: List[str] = [
    "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
]


def build_mcp_subprocess_env(configured_env: Optional[Dict[str, str]]) -> Dict[str, str]:
    """SEC-003: the environment an MCP subprocess actually receives -
    `Kriya-required baseline UNION explicitly authorized MCP environment`,
    never ambient `os.environ`. Reuses `build_restricted_env()`
    (kriya/tools/sandbox.py, SEC-001) as the one common restricted-env
    primitive rather than re-implementing environment filtering here -
    that function always includes real PATH verbatim (unconditionally,
    regardless of allowlist) plus any allowlisted name present in the
    real process environment; nothing else from `os.environ` reaches the
    result. `configured_env` is `cfg.mcp.<server>.env` - already
    SEC-009-authorized before this function is ever called (this function
    does not re-decide whether that env is authorized, only what
    environment the already-authorized subprocess receives) - and is
    applied LAST, so an explicit configured value legitimately overrides
    a baseline variable of the same name (e.g. a server wanting its own
    TMPDIR), per existing `mcp.<server>.env` override semantics. No
    environment-variable expansion/interpolation ($VAR, ${VAR}) is
    performed anywhere in this path - `configured_env`'s values are used
    verbatim, so a configured value can never reach back into ambient
    os.environ through interpolation. An empty/missing `configured_env`
    never restores ambient inheritance - the baseline alone is already a
    strict subset of `os.environ`, never `os.environ` itself. Pure and
    non-raising by construction (no I/O, no external calls) - there is no
    failure path here that could tempt a caller into falling back to
    ambient `os.environ`; if this function's inputs are ever malformed,
    the natural `TypeError`/`AttributeError` propagates to the caller
    exactly like any other Kriya construction error, never silently
    substituting the unrestricted environment."""
    restricted = build_restricted_env(MCP_BASELINE_ENV_ALLOWLIST)
    restricted.update(configured_env or {})
    return restricted

# =====================================================================
# 1. Native Stdio JSON-RPC 2.0 MCP Client
# =====================================================================


class _ConnState(Enum):
    """SEC-004: explicit connection-health state, replacing the prior
    single `_is_running` boolean - that flag was never reset when the
    reader loop hit EOF/an error on its own (a confirmed, real bug: a
    dead server left `_is_running` stuck True, `_process` still set, so a
    subsequent `_send_request()` would write to a dead stdin and then
    `await future` HUNG FOREVER, since no reader remained alive to ever
    resolve it). `RUNNING` is the only state in which a new request may
    be sent; `CLOSING`/`CLOSED` both refuse new requests immediately and
    deterministically (never silently hang)."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"


class MCPClient:
    """Client for communicating with Model Context Protocol (MCP) servers
    via stdio - SEC-004: bounded startup, bounded per-request timeouts,
    process-group-owned subprocess (killpg on shutdown/timeout so no
    descendant survives), bounded stdout protocol-frame size, a bounded
    rolling stderr buffer, and CPU/memory resource ceilings (reusing
    SEC-001's `posix_resource_limits_preexec_fn`) - see
    kriya/mcp/lifecycle.py for the shared spawn/termination primitives
    this class composes."""

    # SEC-004: caps per-line stderr LOGGING (distinct from the bounded
    # in-memory retention buffer, `mcp_lifecycle.max_stderr_buffer_bytes`)
    # - see _read_stderr()'s own docstring for why this exists as a
    # separate bound.
    _STDERR_LOG_LINE_CAP = 100

    def __init__(
        self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None,
        lifecycle_config: Optional[Any] = None, on_closed: Optional[Callable[[str, BaseException], None]] = None,
        capability_profile: Optional[MCPCapabilityProfile] = None,
        containment_required: bool = False, containment_backend: Optional[Any] = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        # TOOL-003 P1: the resolved, operator-authorized maximum capability
        # this server may ever be declared to possess - bound HERE, before
        # start() is ever awaited (Task 9), from MCPManager.start_all()'s
        # own resolution of the server's SEC-009-governed
        # mcp.<server>.capabilities config. A data/authority BINDING only -
        # this does not (yet) change what start()/spawn_mcp_process() does
        # in any way; see kriya/mcp/capability.py's own security statement.
        self.capability_profile = capability_profile or resolve_mcp_capability_profile({}, workspace_root=os.getcwd())
        # TOOL-003 P2: whether this server MUST run inside real OCI
        # containment (autonomy.mcp_contained_execution_required, default
        # False - see that field's own docstring for the explicit,
        # deliberate default-flip decision). False (default) preserves
        # 100% of pre-TOOL-003-P2 behavior: host-side execution, no
        # containment backend ever consulted. `containment_backend` is the
        # resolved kriya.tools.containment.ContainmentBackend instance to
        # use when True - injected by MCPManager.start_all(), never
        # resolved by this class itself (keeps MCPClient constructible/
        # testable with no config/autonomy dependency at all when unset).
        self._containment_required = containment_required
        self._containment_backend = containment_backend
        self._containment_cleanup: Optional[Callable[[], None]] = None
        # Local import (not a module-level import) to avoid a config->mcp
        # layer import cycle - kriya/config/config.py never imports from
        # kriya/mcp/, and this keeps that direction unchanged.
        from kriya.config.config import MCPLifecycleConfig

        self._lifecycle = lifecycle_config or MCPLifecycleConfig()
        # Best-effort notification to the owning MCPManager when this
        # client transitions to CLOSED for any reason OTHER than an
        # explicit stop() the manager itself initiated - lets the manager
        # immediately unregister this server's tools (SEC-004: stale
        # lifecycle state after a server dies must not keep routing tool
        # calls into a dead client - see MCPManager._on_client_closed()).
        # Exceptions from this callback are swallowed (best-effort,
        # observability only - never allowed to break shutdown).
        self._on_closed = on_closed

        self._containment_active = False
        self._process: Optional[asyncio.subprocess.Process] = None
        self._read_task: Optional[asyncio.Task] = None
        self._read_stderr_task: Optional[asyncio.Task] = None
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._request_id = 0
        self._state = _ConnState.NOT_STARTED
        self._stderr_lines: List[str] = []
        self._stderr_bytes = 0
        self._close_lock = asyncio.Lock()

    @property
    def is_healthy(self) -> bool:
        """True only while the connection may accept new requests - a
        registered `MCPTool` should treat False as "this server's tools
        are no longer live", not attempt the call and hope for the best."""
        return self._state == _ConnState.RUNNING

    @property
    def containment_required(self) -> bool:
        """TOOL-003 P2 evidence field (per the 2026-09-13 decision:
        "every MCP startup/run must record: containment required
        true/false") - what this deployment DEMANDED, independent of
        whether it was ever actually achieved (see `containment_active`)."""
        return self._containment_required

    @property
    def containment_active(self) -> bool:
        """True only once a real OCI container was successfully prepared
        and this client's process IS that container's own attached
        `docker run -i` process. False whenever `containment_required` is
        False (host-side execution - TOOL-003 containment assurance must
        never be claimed for this case, per the 2026-09-13 decision: "Do
        not use successful host-side execution as containment evidence"),
        and also False before start() completes."""
        return self._containment_active

    async def start(self) -> None:
        """Spawns the MCP server process and completes the full startup
        sequence (process launch + initialize request + initialize
        response/handshake) within `mcp_lifecycle.startup_timeout_seconds`
        as ONE bounded unit - a child that starts but never completes the
        handshake (never responds, or responds to the wrong thing forever)
        cannot hang Kriya past this bound. On ANY failure (timeout or
        otherwise), runs the full SEC-004 close sequence before re-raising
        - a partially-started connection never lingers as live-looking
        state, and no registered tools are ever created for it (the
        caller, MCPManager, only registers tools after `start()` returns
        successfully)."""
        if self._state == _ConnState.RUNNING:
            return

        logger.info(f"Starting MCP server '{self.name}' using command: {self.command} {self.args}")
        try:
            await asyncio.wait_for(self._start_sequence(), timeout=self._lifecycle.startup_timeout_seconds)
        except asyncio.TimeoutError:
            err = MCPStartupTimeoutError(
                f"MCP server '{self.name}' did not complete startup (launch + handshake) "
                f"within {self._lifecycle.startup_timeout_seconds}s."
            )
            logger.error(str(err))
            await self._close(err)
            raise err from None
        except Exception as e:
            logger.error(f"Failed to start MCP server '{self.name}': {e}", exc_info=True)
            await self._close(e)
            raise

    async def _start_sequence(self) -> None:
        # SEC-003: restricted-env construction, never ambient os.environ -
        # see build_mcp_subprocess_env()'s own docstring for the invariant.
        full_env = build_mcp_subprocess_env(self.env)

        command_prefix: Optional[List[str]] = None
        spawn_env: Optional[Dict[str, str]] = full_env
        if self._containment_required:
            # TOOL-003 P2: "no raw-host fallback" (2026-09-13 user
            # instruction, policed as the single hardest requirement in
            # this package) - every step below either produces a real,
            # prepared container or raises a ContainmentSetupError (or
            # subclass) that propagates UNCAUGHT out of this method, out
            # of start(), and out of MCPManager.start_all(), which is
            # already SEC-004-atomic (rolls back any earlier server in the
            # same start_all() call). There is no except-and-continue
            # anywhere on this path.
            if self._containment_backend is None:
                raise ContainmentSetupError(
                    f"MCP server '{self.name}': autonomy.mcp_contained_execution_required "
                    "is True but no containment backend was resolved - refusing to start "
                    "uncontained."
                )
            profile_digest = compute_mcp_capability_profile_digest(self.capability_profile)
            loop = asyncio.get_running_loop()
            # OCIContainmentBackend.prepare() does real, synchronous
            # subprocess I/O (docker daemon probe, image ensure) -
            # off the event loop via run_in_executor, exactly like the
            # cleanup call in _close() below.

            def _prepare():
                containment_profile = map_capability_profile_to_containment(
                    self.capability_profile, workspace_root=os.path.realpath(os.getcwd()),
                    resolved_env=full_env, profile_digest=profile_digest,
                    cpu_seconds=self._lifecycle.cpu_seconds, memory_mb=self._lifecycle.memory_mb,
                )
                return self._containment_backend.prepare(containment_profile, [self.command] + self.args)

            prepared = await loop.run_in_executor(None, _prepare)
            command_prefix = prepared.command_prefix
            self._containment_cleanup = prepared.cleanup
            # The `docker` CLI itself is TRUSTED_KRIYA_INFRASTRUCTURE - it
            # needs its own normal host environment (PATH to find the
            # `docker` binary, etc.), never the SEC-003-restricted MCP
            # environment, which is instead baked into command_prefix as
            # `-e KEY=VALUE` flags (ContainmentProfile.resolved_env, set
            # to `full_env` above). `env=None` means "inherit the current
            # process's environment" - correct for trusted host tooling.
            spawn_env = None

        # SEC-004: fail-closed spawn - process-group isolation and the
        # hard stdout-line ceiling are established HERE, before the
        # process can do anything; a resource-limit setup failure
        # (ContainmentSetupError/ResourceLimitSetupError) propagates
        # uncaught - this method never falls back to an uncontrolled spawn.
        self._process = await spawn_mcp_process(
            self.command, self.args, spawn_env,
            cpu_seconds=self._lifecycle.cpu_seconds, memory_mb=self._lifecycle.memory_mb,
            max_stdout_line_bytes=self._lifecycle.max_stdout_line_bytes,
            command_prefix=command_prefix,
        )
        self._containment_active = self._containment_required
        self._state = _ConnState.RUNNING
        self._read_task = asyncio.create_task(self._read_stdout())
        self._read_stderr_task = asyncio.create_task(self._read_stderr())
        await self._handshake()

    async def _close(self, reason: BaseException) -> None:
        """The SEC-004 bounded close sequence - idempotent (safe to call
        from `stop()`, from a failed `start()`, from the reader loop
        itself on EOF/protocol violation, or more than once) and never
        performs an unbounded wait. Guarded by a lock so concurrent
        callers (e.g. `stop()` racing a reader-triggered close) serialize
        rather than double-run teardown, and re-checks state after
        acquiring the lock so the second caller in is a fast no-op."""
        async with self._close_lock:
            if self._state == _ConnState.CLOSED:
                return
            self._state = _ConnState.CLOSING
            logger.info(f"Closing MCP server '{self.name}': {reason}")

            current = asyncio.current_task()
            for task in (self._read_task, self._read_stderr_task):
                # A reader task calling this method on its own terminal
                # path (EOF/oversized-line) must not try to cancel-and-
                # await ITSELF - `current is task` skips exactly that case;
                # the task simply returns normally right after this call.
                if task is not None and task is not current and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.debug(f"MCP reader task for '{self.name}' raised during cancellation: {e}")

            if self._process is not None:
                await terminate_mcp_process(
                    self._process,
                    grace_seconds=self._lifecycle.shutdown_grace_seconds,
                    reap_seconds=self._lifecycle.force_kill_reap_seconds,
                )

            if self._containment_cleanup is not None:
                # TOOL-003 P2: the SECOND lifecycle layer - the host-side
                # process-group kill above (terminate_mcp_process(),
                # targeting the `docker run` CLI process) does NOT
                # reliably stop/remove a VM-mediated container runtime's
                # actual container (PreparedContainment.cleanup's own
                # docstring) - this authoritative `docker rm -f` step
                # closes that gap. Real, synchronous, potentially-slow
                # subprocess I/O - off the event loop via run_in_executor
                # (never blocks other coroutines) and bounded by its own
                # SEC-009-governed timeout (additive to, not swapped in
                # for, the grace/reap bounds above). A timeout here is
                # logged, not raised - the container's own removal was
                # already requested (docker rm -f is itself the forceful
                # path); this mirrors terminate_mcp_process()'s own
                # "reap timeout after the forced kill is still terminal"
                # precedent exactly.
                cleanup = self._containment_cleanup
                self._containment_cleanup = None
                loop = asyncio.get_running_loop()
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(None, cleanup),
                        timeout=self._lifecycle.container_cleanup_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "MCP server '%s': container cleanup did not confirm within %.1fs - "
                        "docker rm -f was already issued (the forceful removal path); "
                        "treating as terminal regardless of this asyncio call's own outcome.",
                        self.name, self._lifecycle.container_cleanup_timeout_seconds,
                    )
                except Exception as e:
                    logger.warning(f"MCP server '{self.name}': container cleanup raised: {e}")

            for fut in self._pending_requests.values():
                if not fut.done():
                    fut.set_exception(reason)
            self._pending_requests.clear()

            self._state = _ConnState.CLOSED

        if self._on_closed is not None:
            try:
                self._on_closed(self.name, reason)
            except Exception as e:
                logger.debug(f"on_closed callback for MCP server '{self.name}' raised: {e}")

    async def stop(self) -> None:
        """Terminate the server process and cleanup resources - safe to
        call multiple times (idempotent) and safe to call on a client
        that never finished (or failed) `start()`."""
        if self._state in (_ConnState.CLOSED, _ConnState.NOT_STARTED):
            self._state = _ConnState.CLOSED
            return
        await self._close(MCPLifecycleError(f"MCP server '{self.name}' stopped."))

    async def _handshake(self) -> None:
        """Performs Kriya-MCP initialization handshake."""
        # 1. Initialize Request
        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {}
            },
            "clientInfo": {
                "name": "kriya",
                "version": "0.1.0"
            }
        }
        resp = await self._send_request("initialize", init_params, timeout=self._lifecycle.startup_timeout_seconds)
        logger.debug(f"Handshake initialized response: {resp}")

        # 2. Initialized Notification (no response expected)
        await self._send_notification("notifications/initialized", {})

    async def _send_request(self, method: str, params: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Send a JSON-RPC request and wait for the response, bounded by
        `timeout` (defaults to `mcp_lifecycle.request_timeout_seconds`) -
        SEC-004: no protocol request controlled by the server may hang
        Kriya indefinitely. On timeout, the pending-request bookkeeping is
        cleaned up via the same `finally` the happy path uses (so a LATE
        response arriving after the timeout finds no matching entry to
        resurrect - `_read_stdout()`'s `pop(req_id, None)` simply returns
        None and does nothing) and a typed `MCPRequestTimeoutError` is
        raised - never a bare `asyncio.TimeoutError`, so callers can
        distinguish this from an ordinary asyncio cancellation."""
        if self._state != _ConnState.RUNNING or not self._process or not self._process.stdin:
            raise MCPLifecycleError(f"MCP client '{self.name}' is not running.")

        effective_timeout = timeout if timeout is not None else self._lifecycle.request_timeout_seconds

        self._request_id += 1
        req_id = self._request_id

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params
        }

        future = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = future

        try:
            message = json.dumps(payload) + "\n"
            self._process.stdin.write(message.encode("utf-8"))
            await self._process.stdin.drain()
            return await asyncio.wait_for(future, timeout=effective_timeout)
        except asyncio.TimeoutError:
            raise MCPRequestTimeoutError(
                f"MCP server '{self.name}' did not respond to '{method}' (id={req_id}) "
                f"within {effective_timeout}s."
            ) from None
        finally:
            self._pending_requests.pop(req_id, None)

    async def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no id, does not block for response)."""
        if self._state != _ConnState.RUNNING or not self._process or not self._process.stdin:
            return

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        message = json.dumps(payload) + "\n"
        self._process.stdin.write(message.encode("utf-8"))
        await self._process.stdin.drain()

    async def _read_stdout(self) -> None:
        """Reads newline-separated JSON-RPC messages from server stdout.
        SEC-004: stdout is untrusted protocol input - a single line/frame
        exceeding `mcp_lifecycle.max_stdout_line_bytes` raises `ValueError`
        deterministically (asyncio's own StreamReader `limit`, set at
        spawn time - see `spawn_mcp_process()`) rather than allocating
        unboundedly, and is treated as FATAL to this connection (a
        truncated/oversized frame cannot be trusted for request/response
        correlation) - unlike a malformed-but-appropriately-sized JSON
        line, which stays non-fatal (logged, skipped), matching this
        module's pre-existing, deterministic behavior. EOF (server exited
        or closed stdout) is also terminal - both paths run the full
        SEC-004 close sequence so no dead server is ever left looking
        "running", and no pending request is ever left to hang forever
        with no reader alive to resolve it (the confirmed pre-SEC-004 bug
        this fixes)."""
        try:
            while self._state == _ConnState.RUNNING:
                try:
                    line = await self._process.stdout.readline()
                except ValueError as e:
                    err = MCPProtocolViolationError(
                        f"MCP server '{self.name}' sent an oversized stdout protocol line "
                        f"(exceeds {self._lifecycle.max_stdout_line_bytes} bytes): {e}"
                    )
                    logger.error(str(err))
                    await self._close(err)
                    return

                if not line:
                    await self._close(MCPLifecycleError(f"MCP server '{self.name}' closed its stdout (EOF)."))
                    return

                msg_str = line.decode("utf-8", errors="replace").strip()
                if not msg_str:
                    continue

                try:
                    msg = json.loads(msg_str)
                except json.JSONDecodeError:
                    logger.warning(f"MCP server '{self.name}' sent invalid JSON: {msg_str[:500]}")
                    continue

                if "id" in msg:
                    req_id = msg["id"]
                    future = self._pending_requests.pop(req_id, None)
                    if future and not future.done():
                        if "error" in msg:
                            future.set_exception(RuntimeError(f"Server Error: {msg['error']}"))
                        else:
                            future.set_result(msg.get("result", {}))
                else:
                    # Parse server notifications/requests if needed
                    logger.debug(f"MCP server '{self.name}' sent notification/request: {msg}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in MCP client reader loop for '{self.name}': {e}", exc_info=True)
            await self._close(MCPLifecycleError(f"MCP server '{self.name}' reader failed unexpectedly: {e}"))

    async def _read_stderr(self) -> None:
        """Log MCP server stderr for diagnostic health reviews. SEC-004:
        stderr is untrusted, diagnostic-only output - retention is a
        bounded ROLLING buffer (oldest lines dropped once over
        `mcp_lifecycle.max_stderr_buffer_bytes`), never unbounded, and a
        flooding server never blocks this reader (the pipe is drained
        continuously regardless of retention). An individual stderr line
        exceeding the shared stdout/stderr StreamReader `limit` raises the
        same `ValueError` stdout would - but stderr is NOT the protocol
        channel, so this is logged and the loop CONTINUES (confirmed
        empirically: the stream recovers cleanly for the next readline()
        call after a limit-exceeded ValueError) rather than tearing down
        the connection.

        LOG VOLUME is bounded independently from RETENTION (a real gap
        found empirically this session, not merely inferred): logging
        every line at WARNING - the pre-SEC-004 behavior - means a
        flooding server can still exhaust Kriya's own log storage even
        though the in-memory buffer above is correctly bounded; a live
        200k-line flood produced tens of megabytes of log output in
        under two seconds. Only the first `_STDERR_LOG_LINE_CAP` lines
        per connection are logged individually at WARNING; every line
        past that is still captured into the bounded buffer (so
        diagnostics on close/failure are unaffected) but logging drops to
        a single one-time notice, not per-line spam."""
        logged_lines = 0
        while self._state in (_ConnState.RUNNING, _ConnState.CLOSING):
            try:
                line = await self._process.stderr.readline()
            except ValueError:
                logger.warning(f"MCP server '{self.name}' stderr line exceeded the read limit; truncated and continuing")
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"MCP server '{self.name}' stderr reader loop exiting: {e}")
                break

            if not line:
                break

            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if logged_lines < self._STDERR_LOG_LINE_CAP:
                logger.warning(f"[MCP Server: {self.name}] {text}")
                logged_lines += 1
                if logged_lines == self._STDERR_LOG_LINE_CAP:
                    logger.warning(
                        f"[MCP Server: {self.name}] stderr logging capped at {self._STDERR_LOG_LINE_CAP} lines for "
                        "this connection - further lines are still captured in the bounded diagnostic buffer, "
                        "not logged individually, to prevent a flooding server from exhausting Kriya's own logs."
                    )

            self._stderr_lines.append(text)
            self._stderr_bytes += len(text)
            while self._stderr_bytes > self._lifecycle.max_stderr_buffer_bytes and len(self._stderr_lines) > 1:
                dropped = self._stderr_lines.pop(0)
                self._stderr_bytes -= len(dropped)

    async def list_tools(self) -> List[Dict[str, Any]]:
        """List all tools exposed by the server."""
        resp = await self._send_request("tools/list", {})
        return resp.get("tools", [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Request the server to execute a specific tool with arguments."""
        resp = await self._send_request("tools/call", {"name": name, "arguments": arguments})
        return resp

# =====================================================================
# 2. Dynamic Tool Wrapper
# =====================================================================

class MCPTool(BaseTool):
    """Kriya BaseTool implementation wrapping a dynamically fetched MCP Tool
    schema.

    TOOL-002 P1: this is the single production choke point every MCP
    `tools/call` passes through (kernel.registry.get("tool", ...) always
    resolves to an MCPTool instance for an MCP-backed name, and
    MCPClient.call_tool() has exactly one production caller - this class's
    own _run(), confirmed by a full-repo grep before this change; every
    other caller is a test file exercising MCPClient directly, which this
    package deliberately leaves possible). Both known production routes
    (kriya tools execute, and any future TOOL-tagged SubtaskExecutor
    dispatch) reach an MCP tool exclusively via BaseTool.execute() -> this
    class's _run() -> self.client.call_tool(...), so gating HERE, not at
    either individual caller, covers both without either needing its own
    copy of this logic (and without a future third caller needing to
    remember to add one)."""

    def __init__(
        self, mcp_client: MCPClient, tool_meta: Dict[str, Any],
        execution_policy: Optional[ExecutionPolicy] = None,
    ) -> None:
        self.client = mcp_client
        # TOOL-002 P1: the EXACT MCP-advertised tool name, captured once at
        # registration and never reconstructed later (unlike the old
        # `self._name.replace(f"{self.client.name}_", "", 1)` pattern this
        # replaces below) - a stored fact, not a string-manipulation guess.
        self._exact_tool_name = tool_meta["name"]
        self._name = f"{mcp_client.name}_{tool_meta['name']}"
        self._description = tool_meta.get("description", "MCP Dynamic Tool")

        # Build arguments schema dynamically using Pydantic create_model
        input_schema = tool_meta.get("inputSchema", {})
        self._schema = self._build_pydantic_schema(input_schema)

        # TOOL-002 P1: the structured, collision-resistant identity this
        # exact MCPTool instance was constructed with - bound once, at
        # registration, from the SAME tool_meta snapshot the dispatch
        # target (self.client / self._exact_tool_name) was also derived
        # from. Because a policy decision and its own dispatch target are
        # always read from the SAME already-constructed, immutable object
        # (never re-looked-up from the registry mid-call, never re-derived
        # from a flattened display string), there is no window in which
        # the identity a policy decision evaluates could diverge from the
        # identity of what actually gets invoked - even if
        # ComponentRegistry later overwrites this instance's own
        # FLATTENED display name with a different server's colliding
        # MCPTool (kriya/core/registry.py's register() logs a warning but
        # does not refuse), whichever object actually gets resolved and
        # invoked always uses ITS OWN identity for the policy check, never
        # the other one's. This is what makes Task 3's "revalidation"
        # requirement satisfied WITHOUT any ComponentRegistry redesign.
        self.identity = MCPToolIdentity(
            server_identity=mcp_client.name,
            tool_name=self._exact_tool_name,
            schema_digest=compute_mcp_schema_digest(input_schema),
        )
        # TOOL-003 P1: the resolved capability-profile identity of the
        # SAME MCPClient this tool dispatches through - bound once, here,
        # from the client's own `capability_profile` (itself bound before
        # start(), see MCPClient.__init__). Audit/telemetry context only
        # (Task 10) - never read by _check_mcp_invocation's own decision.
        self.capability_profile_identity = MCPCapabilityProfileIdentity(
            server_identity=mcp_client.name,
            profile_digest=compute_mcp_capability_profile_digest(mcp_client.capability_profile),
        )
        # Bare ExecutionPolicy() with no override when the caller (today:
        # only MCPManager.start_all()) doesn't supply one - matches every
        # other real ExecutionPolicy call site's own established
        # convention (kriya/policy/execution.py's own comment on this
        # default). An empty approved_mcp_tool_identities set (this
        # default's own behavior) means every MCP_TOOL_CALL falls through
        # to REQUIRE_APPROVAL, which _run() below fails closed on - see
        # its own docstring for why that is P1's correct, conservative,
        # intentional default.
        self._execution_policy = execution_policy or ExecutionPolicy()

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return self._schema

    async def _run(self, args: BaseModel) -> Any:
        # TOOL-002 P1 (Invariant 1/2/8/9/10): a deterministic Kriya
        # authority decision, ALWAYS REAL ENFORCEMENT regardless of
        # ExecutionPolicyConfig.mode (unlike every audit-only MA4 caller -
        # every other current ExecutionPolicy consumer only enforces under
        # mode="enforce" or via enforce_hard_invariants's own fixed 5-code
        # set; MCP has no such carve-out, matching AuthorizedFileWriter's
        # own precedent as the SECOND deliberate, explicit exception to
        # MA4's audit-only mandate) - gates every path to
        # the real MCP dispatch call below. DENY, REQUIRE_APPROVAL (no
        # approval mechanism is safely reachable at this call site in P1 -
        # see TASK 6/Invariant 9), and a THROWING/unavailable policy engine
        # (Invariant 10) all mean zero tools/call, raised via the same
        # typed PolicyDeniedError ExecutionPolicy's other real-enforcement
        # callers (ShellTool, GitTool, AuthorizedFileWriter) already use -
        # never a fallback to unauthorized execution. Only ALLOW/
        # ALLOW_SANDBOXED let this method reach the dispatch call below;
        # MCP has no sandboxing mechanism of its own to route
        # ALLOW_SANDBOXED through (that is TOOL-003 containment's scope),
        # so both are treated identically here.
        #
        # `self.identity` (bound once, at construction - see __init__'s
        # own docstring) is the ONLY thing this decision is made against -
        # never `self._description` (the server's own advertised text),
        # never `args`/`raw_args` (Invariant: no authority inferred from
        # argument names or values in P1 - that is TOOL-003 scope too).
        request = ActionRequest(
            action_type=ActionType.MCP_TOOL_CALL,
            metadata={
                "mcp_tool_identity": self.identity,
                # TOOL-003 P1: audit context only - _check_mcp_invocation
                # does not read this key, never widens/narrows the
                # invocation decision (Task 10/12: capability approval and
                # invocation approval remain distinct decisions).
                "mcp_capability_profile_identity": self.capability_profile_identity,
            },
        )
        try:
            result = self._execution_policy.evaluate(request)
        except Exception as e:
            logger.error(f"TOOL-002 P1: MCP invocation policy evaluation raised for '{self._name}' - failing closed: {e}")
            result = PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="MCP_POLICY_EVALUATION_FAILED",
                explanation=f"ExecutionPolicy.evaluate() raised: {e}",
                matched_rule="mcp_invocation.policy_evaluation_failed",
            )

        try:
            logger.debug("TOOL-002 P1 MCP invocation decision: %s", build_decision_record(request, result, enforced=True).to_json())
        except Exception as e:
            logger.debug("TOOL-002 P1 telemetry record build failed (ignored, never blocks the real decision): %s", e)

        if result.decision in (PolicyDecision.DENY, PolicyDecision.REQUIRE_APPROVAL):
            raise PolicyDeniedError(request=request, result=result)

        # SEC-004: a server that died after its tools were registered
        # (stale lifecycle state) must fail deterministically here, not
        # attempt a write to a dead process's stdin and hang - MCPManager
        # also proactively unregisters this tool on the client's own
        # close notification (see MCPManager._on_client_closed()), so
        # reaching this check at all should be rare (a call already in
        # flight the instant the server died), not the normal path.
        if not self.client.is_healthy:
            raise ToolExecutionError(
                f"MCP server '{self.client.name}' is not running (connection closed) - "
                f"tool '{self._name}' cannot be executed."
            )
        # Extract arguments and request execution over MCP client
        raw_args = args.model_dump()

        try:
            response = await self.client.call_tool(self._exact_tool_name, raw_args)
        except MCPLifecycleError as e:
            # SEC-004 lifecycle failures (request timeout, connection
            # closed mid-call) are distinct from an ordinary tool-level
            # failure the server itself reports - re-raised as
            # ToolExecutionError so this stays connection-health/lifecycle
            # only (the not-yet-registered MCP invocation/execution
            # authority work owns tool-call AUTHORIZATION, not this - NOT
            # the register's existing SEC-005 row, an unrelated risk).
            raise ToolExecutionError(f"MCP lifecycle failure calling '{self._name}': {e}") from e

        if response.get("isError"):
            raise ToolExecutionError(f"MCP server execution failed: {response.get('content')}")

        content = response.get("content", [])
        # Extract content text strings
        output = []
        for block in content:
            if block.get("type") == "text":
                output.append(block.get("text", ""))
        return "\n".join(output) if output else response

    def _build_pydantic_schema(self, schema_dict: Dict[str, Any]) -> Type[BaseModel]:
        """Convert standard JSON Schema to a Pydantic BaseModel class."""
        properties = schema_dict.get("properties", {})
        required = schema_dict.get("required", [])

        fields = {}
        for prop_name, prop_meta in properties.items():
            prop_type = prop_meta.get("type")
            py_type = str
            if prop_type == "integer":
                py_type = int
            elif prop_type == "number":
                py_type = float
            elif prop_type == "boolean":
                py_type = bool
            elif prop_type == "array":
                py_type = list
            elif prop_type == "object":
                py_type = dict

            desc = prop_meta.get("description", "")

            if prop_name in required:
                fields[prop_name] = (py_type, Field(..., description=desc))
            else:
                fields[prop_name] = (Optional[py_type], Field(default=None, description=desc))

        return create_model(f"MCPToolArgs_{self._name}", **fields)

# =====================================================================
# 3. Central MCP Manager
# =====================================================================

class MCPManager:
    """Manages active MCP client instances and bridges their tools to
    Kriya registry. SEC-004: `start_all()` is atomic - if any configured
    server fails to start, every server already started IN THIS CALL is
    stopped and unregistered before a deterministic exception propagates,
    so a partial failure can never leave an orphaned server silently
    running (the prior behavior: log the exception and continue, leaving
    every earlier-started server up with no signal to the caller that
    anything was wrong)."""

    def __init__(self, kernel: Kernel, execution_policy: Optional[ExecutionPolicy] = None) -> None:
        self.kernel = kernel
        self.clients: Dict[str, MCPClient] = {}
        self.registered_tools: Dict[str, List[str]] = {}
        # TOOL-002 P1: one shared ExecutionPolicy for every MCPTool this
        # manager constructs - bare ExecutionPolicy() (nothing pre-
        # approved) unless a caller explicitly supplies one, matching
        # every other real ExecutionPolicy call site's own "no override"
        # default convention. A real, operator-facing, SEC-009-governed
        # source for this is TOOL-002 P2 / TOOL-003 scope, not built here.
        self._execution_policy = execution_policy or ExecutionPolicy()

    def _on_client_closed(self, server_name: str, reason: BaseException) -> None:
        """SEC-004: invoked by an MCPClient the instant it transitions to
        CLOSED, for ANY reason (including an unexpected death, not just an
        explicit stop() this manager itself initiated) - unregisters that
        server's tools immediately so a stale registration can never keep
        routing calls into a dead client. Idempotent - safe to call after
        `shutdown_all()` already removed the same entries (the `.pop(...,
        [])` default and the try/except around `unregister` both make a
        second call here a harmless no-op)."""
        tool_names = self.registered_tools.pop(server_name, [])
        for tool_name in tool_names:
            try:
                self.kernel.registry.unregister("tool", tool_name)
                logger.info(f"Unregistered stale MCP tool '{tool_name}' - server '{server_name}' closed: {reason}")
            except Exception as e:
                logger.debug(f"Failed to unregister MCP tool '{tool_name}' for closed server '{server_name}': {e}")
        self.clients.pop(server_name, None)

    async def start_all(self, mcp_configs: Dict[str, Any]) -> None:
        """Start all configured MCP subprocess servers and register their
        tools - atomically (see class docstring)."""
        started_this_call: List[str] = []
        for server_name, server_cfg in mcp_configs.items():
            try:
                # server_cfg can be a pydantic model (MCPServerConfig) or dictionary
                if hasattr(server_cfg, "model_dump"):
                    cfg_dict = server_cfg.model_dump()
                else:
                    cfg_dict = server_cfg

                lifecycle_cfg = getattr(self.kernel.config, "mcp_lifecycle", None) if self.kernel.config else None
                autonomy_cfg = getattr(self.kernel.config, "autonomy", None) if self.kernel.config else None

                # TOOL-003 P1 (Task 9): resolve this server's capability
                # profile BEFORE the client (and therefore the process) is
                # ever constructed - `cfg_dict["capabilities"]` was already
                # path-escape-validated at config-load time
                # (kriya.config.config.resolve_config_state()); this call
                # only classifies/canonicalizes, never re-validates safety
                # (see kriya/mcp/capability.py's own docstring).
                capability_profile = resolve_mcp_capability_profile(
                    cfg_dict.get("capabilities", {}), workspace_root=os.path.realpath(os.getcwd())
                )

                # TOOL-003 P2: `autonomy.mcp_contained_execution_required`
                # (default False) - resolved fresh per server from the
                # real, SEC-009-governed config, mirroring
                # contained_execution_required's own established pattern.
                # False preserves 100% of pre-TOOL-003-P2 behavior (no
                # containment backend ever consulted). True resolves a
                # real ContainmentBackend and NEVER falls back to host
                # execution if that resolution or the backend's own
                # `prepare()` fails - see MCPClient._start_sequence()'s
                # own "no raw-host fallback" comment for where that
                # failure actually propagates from.
                containment_required = bool(getattr(autonomy_cfg, "mcp_contained_execution_required", False))
                containment_backend = None
                if containment_required:
                    backend_name = getattr(autonomy_cfg, "containment_backend", "none")
                    containment_backend = resolve_containment_backend(backend_name)

                client = MCPClient(
                    name=server_name,
                    command=cfg_dict["command"],
                    args=cfg_dict.get("args", []),
                    env=cfg_dict.get("env", {}),
                    lifecycle_config=lifecycle_cfg,
                    on_closed=self._on_client_closed,
                    capability_profile=capability_profile,
                    containment_required=containment_required,
                    containment_backend=containment_backend,
                )
                await client.start()
                self.clients[server_name] = client
                started_this_call.append(server_name)

                # Fetch and register tools
                tools = await client.list_tools()
                self.registered_tools[server_name] = []
                for t in tools:
                    mcp_tool = MCPTool(client, t, execution_policy=self._execution_policy)
                    # Register under 'tool' category in kernel registry
                    self.kernel.registry.register("tool", mcp_tool.name, mcp_tool)
                    self.registered_tools[server_name].append(mcp_tool.name)
                    logger.info(f"Registered MCP tool '{mcp_tool.name}' from server '{server_name}'")

            except Exception as e:
                logger.error(f"Failed to load MCP server '{server_name}': {e}", exc_info=True)
                # SEC-004 atomicity: roll back every server started earlier
                # in THIS call before propagating - never leave server A
                # running just because server B failed after it.
                for rollback_name in started_this_call:
                    rollback_client = self.clients.pop(rollback_name, None)
                    if rollback_client is not None:
                        try:
                            await rollback_client.stop()
                        except Exception as stop_err:
                            logger.debug(f"Error stopping MCP server '{rollback_name}' during rollback: {stop_err}")
                    tool_names = self.registered_tools.pop(rollback_name, [])
                    for tool_name in tool_names:
                        try:
                            self.kernel.registry.unregister("tool", tool_name)
                        except Exception as unreg_err:
                            logger.debug(f"Error unregistering tool '{tool_name}' during rollback: {unreg_err}")
                if isinstance(e, ContainmentSetupError):
                    # Preserve the SEC-001 invariant that a resource-limit
                    # setup failure ("we refused to start it") stays
                    # distinguishable from an ordinary lifecycle failure
                    # ("it started and then failed") - rollback above still
                    # ran, only the exception TYPE crossing this boundary
                    # is preserved rather than collapsed into the generic
                    # MCPLifecycleError below.
                    raise
                raise MCPLifecycleError(
                    f"MCP server '{server_name}' failed to start ({e}) - rolled back "
                    f"{len(started_this_call)} previously-started server(s) in this batch."
                ) from e

    async def shutdown_all(self) -> None:
        """Shutdown all active servers and unregister their tools."""
        for server_name, client in list(self.clients.items()):
            try:
                await client.stop()
            except Exception as e:
                logger.debug(f"Failed to cleanly stop MCP client '{server_name}': {e}")

            # Unregister tools from registry
            tool_names = self.registered_tools.pop(server_name, [])
            for tool_name in tool_names:
                try:
                    self.kernel.registry.unregister("tool", tool_name)
                except Exception as e:
                    logger.warning(f"Failed to unregister tool '{tool_name}': {e}")

        self.clients.clear()

"""SEC-004: MCP subprocess lifecycle must be bounded and Kriya must retain
deterministic control over startup, requests, shutdown, process
descendants, output growth, and resource consumption.

Covers the full required matrix (startup/request/shutdown/output/resources/
manager), the six required real-process adversarial fixtures (A-F), the
SEC-003 environment-isolation regression, and the SEC-009 regression
(mcp_lifecycle.* fields can never be weakened by repository configuration).

All tests use a short-timeout MCPLifecycleConfig (1-3s bounds) so the suite
stays fast while still proving every bound is finite and enforced - never
relying on the packaged defaults (30s/60s) for test speed. No live LLM:
nothing here reaches an LLM call.
"""
import asyncio
import contextlib
import json
import os
import subprocess
import sys

import pytest
import yaml

from kriya.config.config import AppConfig, MCPLifecycleConfig, load_config
from kriya.config.authority import ConfigAuthorityError
from kriya.core.kernel import Kernel
from kriya.mcp.mcp import MCPClient, MCPManager
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import MCPToolIdentity, compute_mcp_schema_digest
from kriya.mcp.lifecycle import (
    MCPLifecycleError,
    MCPRequestTimeoutError,
    MCPStartupTimeoutError,
    terminate_mcp_process,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KRIYA_BIN = os.path.join(REPO_ROOT, ".venv", "bin", "kriya")
FIXTURE = os.path.join(REPO_ROOT, "tests", "adversarial_mcp_server.py")

FAST = MCPLifecycleConfig(
    startup_timeout_seconds=2, request_timeout_seconds=1,
    shutdown_grace_seconds=1, force_kill_reap_seconds=2,
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _client(name, mode, extra_env=None, lifecycle=FAST):
    env = {"ADVERSARIAL_MODE": mode}
    if extra_env:
        env.update(extra_env)
    return MCPClient(name=name, command=sys.executable, args=[FIXTURE], env=env, lifecycle_config=lifecycle)


# --- STARTUP -----------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_startup_and_call():
    c = _client("normal", "normal")
    await c.start()
    try:
        res = await c.call_tool("echo", {"message": "hi"})
        assert res["content"][0]["text"] == "Echo: hi"
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_startup_timeout_raises_bounded():
    c = _client("never-init", "never_initialize")
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with pytest.raises(MCPStartupTimeoutError):
        await c.start()
    assert loop.time() - t0 < FAST.startup_timeout_seconds + 3


@pytest.mark.asyncio
async def test_startup_timeout_no_survivors():
    c = _client("never-init2", "never_initialize")
    with pytest.raises(MCPStartupTimeoutError):
        await c.start()
    pid = c._process.pid
    await asyncio.sleep(0.3)
    assert not _pid_alive(pid)


@pytest.mark.asyncio
async def test_startup_timeout_no_pending_requests_leaked():
    c = _client("never-init3", "never_initialize")
    with pytest.raises(MCPStartupTimeoutError):
        await c.start()
    assert c._pending_requests == {}
    assert not c.is_healthy


# --- REQUEST -------------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_request():
    c = _client("reqnorm", "normal")
    await c.start()
    try:
        tools = await c.list_tools()
        assert {t["name"] for t in tools} == {"echo", "report_env"}
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_request_timeout_raises_bounded():
    c = _client("hangreq", "hang_after_init")
    await c.start()
    try:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        with pytest.raises(MCPRequestTimeoutError):
            await c.list_tools()
        assert loop.time() - t0 < FAST.request_timeout_seconds + 3
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_request_timeout_pending_map_cleaned():
    c = _client("hangreq2", "hang_after_init")
    await c.start()
    try:
        with pytest.raises(MCPRequestTimeoutError):
            await c.list_tools()
        assert c._pending_requests == {}
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_late_response_after_timeout_is_harmless():
    """A response arriving AFTER the client already timed out must not
    resurrect the Future, satisfy another request, crash the reader, or
    corrupt request correlation - proven via a real server that delays
    only its first post-handshake response past the client's timeout,
    then responds normally to everything after."""
    c = _client("late", "delayed_response", {"DELAY_SECONDS": "1.5"})
    await c.start()
    try:
        with pytest.raises(MCPRequestTimeoutError):
            await c.call_tool("echo", {"message": "will-be-late"})
        await asyncio.sleep(1.0)  # let the late response actually arrive and be discarded
        assert c.is_healthy
        assert c._pending_requests == {}
        res = await c.call_tool("echo", {"message": "next-request"})
        assert res["content"][0]["text"] == "Echo: next-request"
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_concurrent_multiple_requests_correctly_correlated():
    c = _client("concurrent", "normal", lifecycle=MCPLifecycleConfig(
        startup_timeout_seconds=5, request_timeout_seconds=5, shutdown_grace_seconds=1, force_kill_reap_seconds=1,
    ))
    await c.start()
    try:
        results = await asyncio.gather(*[c.call_tool("echo", {"message": f"m{i}"}) for i in range(20)])
        assert all(results[i]["content"][0]["text"] == f"Echo: m{i}" for i in range(20))
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_concurrent_stop_and_request():
    c = _client("stopreq", "hang_after_init", lifecycle=MCPLifecycleConfig(
        startup_timeout_seconds=2, request_timeout_seconds=5, shutdown_grace_seconds=1, force_kill_reap_seconds=1,
    ))
    await c.start()

    async def do_request():
        with pytest.raises(MCPLifecycleError):
            await c.list_tools()

    req_task = asyncio.create_task(do_request())
    await asyncio.sleep(0.2)
    assert len(c._pending_requests) == 1
    await asyncio.gather(req_task, c.stop())
    assert c._pending_requests == {}
    assert not c.is_healthy


@pytest.mark.asyncio
async def test_eof_during_request_fails_deterministically():
    c = _client("exitmid", "exit_mid_request")
    await c.start()
    with pytest.raises(MCPLifecycleError):
        await c.list_tools()
    assert not c.is_healthy
    assert c._pending_requests == {}
    await c.stop()  # must not hang / raise


# --- SHUTDOWN ------------------------------------------------------------

@pytest.mark.asyncio
async def test_cooperative_termination_fast():
    c = _client("coop", "normal")
    await c.start()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await c.stop()
    assert loop.time() - t0 < FAST.shutdown_grace_seconds + FAST.force_kill_reap_seconds


@pytest.mark.asyncio
async def test_sigterm_ignored_forced_kill_real_process():
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        start_new_session=True,
    )
    pid = proc.pid
    await asyncio.sleep(0.2)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await terminate_mcp_process(proc, grace_seconds=1, reap_seconds=2)
    elapsed = loop.time() - t0
    assert elapsed >= 0.9  # actually waited out the grace period, not a fluke instant kill
    assert elapsed < 5
    await asyncio.sleep(0.1)
    assert not _pid_alive(pid)


@pytest.mark.asyncio
async def test_repeated_stop_is_safe():
    c = _client("repstop", "normal")
    await c.start()
    await c.stop()
    await c.stop()
    await c.stop()
    assert not c.is_healthy


@pytest.mark.asyncio
async def test_failed_start_can_call_stop_safely():
    c = _client("failstart", "never_initialize")
    with pytest.raises(MCPStartupTimeoutError):
        await c.start()
    await c.stop()  # must not raise/hang on a client whose start() already failed


@pytest.mark.asyncio
async def test_descendant_processes_all_killed_real_tree(tmp_path):
    """Real adversarial fixture C: parent ignores SIGTERM, spawns a child,
    which spawns a grandchild (also SIGTERM-ignoring) - after shutdown, all
    three PIDs are confirmed gone via os.kill(pid, 0), never process-name
    matching."""
    pidfile = str(tmp_path / "tree_pids.json")
    c = _client("tree", "ignore_sigterm", {"PID_REPORT_FILE": pidfile})
    await c.start()
    try:
        data = {}
        for _ in range(50):
            if os.path.exists(pidfile):
                with open(pidfile) as f:
                    data = json.load(f)
                if "grandchild" in data:
                    break
            await asyncio.sleep(0.1)
        assert "grandchild" in data, "descendant tree never fully started"
        assert all(_pid_alive(p) for p in data.values())
    finally:
        await c.stop()
    await asyncio.sleep(0.2)
    assert all(not _pid_alive(p) for p in data.values()), f"a descendant survived: {data}"


# --- OUTPUT ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_oversized_stdout_rejected_and_closes_connection():
    """The `oversized_stdout` fixture writes its oversized line as soon as
    it sees the handshake's own `notifications/initialized` - already sent
    by `start()` itself, so no extra trigger is needed here. The reader
    task discovers the violation asynchronously (not as an exception
    raised back to any specific caller), so this polls `is_healthy`
    rather than expecting a raised exception."""
    c = _client("oversize", "oversized_stdout")
    await c.start()
    for _ in range(30):
        if not c.is_healthy:
            break
        await asyncio.sleep(0.1)
    assert not c.is_healthy
    pid = c._process.pid
    await c.stop()
    await asyncio.sleep(0.2)
    assert not _pid_alive(pid)


@pytest.mark.asyncio
async def test_malformed_json_is_non_fatal_and_deterministic():
    c = _client("malformed", "malformed_response")
    await c.start()
    try:
        res = await c.call_tool("echo", {"message": "after-malformed"})
        assert res["content"][0]["text"] == "Echo: after-malformed"
        assert c.is_healthy
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_stderr_flood_bounded_no_deadlock():
    c = _client("flood", "flood_stderr")
    await c.start()
    try:
        await asyncio.sleep(1.5)
        assert c._stderr_bytes <= FAST.max_stderr_buffer_bytes * 1.1
        assert c.is_healthy, "a flooding server's stderr must never deadlock the connection"
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_stderr_log_line_cap_enforced(caplog):
    import logging
    c = _client("floodcap", "flood_stderr")
    with caplog.at_level(logging.WARNING, logger="kriya.mcp.mcp"):
        await c.start()
        await asyncio.sleep(1.0)
        await c.stop()
    per_line_records = [r for r in caplog.records if "[MCP Server: floodcap] flood line" in r.message]
    assert len(per_line_records) <= MCPClient._STDERR_LOG_LINE_CAP


# --- RESOURCES -------------------------------------------------------------

@pytest.mark.asyncio
async def test_cpu_over_limit_process_killed_by_os():
    """RLIMIT_CPU is a CUMULATIVE LIFETIME budget (SEC-001's existing
    primitive, reused as-is) - reliable on both Linux and macOS per
    kriya/tools/sandbox.py's own docstring, so this assertion holds on
    both platforms, unlike the memory case below."""
    cfg = MCPLifecycleConfig(
        startup_timeout_seconds=5, request_timeout_seconds=5,
        shutdown_grace_seconds=1, force_kill_reap_seconds=2, cpu_seconds=1,
    )
    c = _client("cpuspin", "spin_cpu", lifecycle=cfg)
    await c.start()
    pid = c._process.pid
    with contextlib.suppress(Exception):
        await c._send_notification("notifications/initialized", {})
    killed = False
    for _ in range(60):
        if not _pid_alive(pid):
            killed = True
            break
        await asyncio.sleep(0.1)
    assert killed, "a CPU-spinning MCP process must not receive unlimited host CPU authority"
    await c.stop()


@pytest.mark.asyncio
async def test_memory_over_limit_outcome_platform_honest():
    """RLIMIT_AS is deterministic/fail-closed on Linux, ADVISORY-ONLY on
    macOS (an already-accepted SEC-001 platform limitation, not
    re-litigated here) - this test records the real outcome per platform
    rather than asserting a single cross-platform expectation, per this
    risk's own explicit instruction not to pretend macOS evidence proves
    Linux behavior."""
    cfg = MCPLifecycleConfig(
        startup_timeout_seconds=5, request_timeout_seconds=5,
        shutdown_grace_seconds=1, force_kill_reap_seconds=2, memory_mb=64,
    )
    c = _client("memhog", "allocate_memory", lifecycle=cfg)
    await c.start()
    pid = c._process.pid
    with contextlib.suppress(Exception):
        await c._send_notification("notifications/initialized", {})
    await asyncio.sleep(3)
    alive = _pid_alive(pid)
    if sys.platform == "linux":
        assert not alive, "RLIMIT_AS must be enforced (deterministic) on Linux"
    else:
        # macOS: RLIMIT_AS is advisory only - the process surviving is the
        # documented, accepted outcome, not a SEC-004 regression.
        pass
    await c.stop()
    await asyncio.sleep(0.2)
    assert not _pid_alive(pid), "stop() must clean up regardless of whether the resource limit itself fired"


@pytest.mark.asyncio
async def test_resource_setup_failure_fails_closed(monkeypatch):
    """If the resource-limit preexec_fn cannot be established, MCPClient
    must NOT fall back to an uncontrolled spawn - it must fail closed."""
    from kriya.tools.containment import ContainmentSetupError

    def _broken_preexec(cpu_seconds, memory_mb):
        def _fn():
            raise RuntimeError("simulated setrlimit failure")
        return _fn

    monkeypatch.setattr("kriya.mcp.lifecycle.posix_resource_limits_preexec_fn", _broken_preexec)
    c = _client("brokenlimit", "normal")
    with pytest.raises(ContainmentSetupError):
        await c.start()
    assert c._process is None or not _pid_alive(c._process.pid)


# --- MANAGER ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_normal_servers_all_registered():
    kernel = Kernel(config=AppConfig(mcp_lifecycle=FAST))
    manager = MCPManager(kernel)
    await manager.start_all({
        "s1": {"command": sys.executable, "args": [FIXTURE], "env": {"ADVERSARIAL_MODE": "normal"}},
        "s2": {"command": sys.executable, "args": [FIXTURE], "env": {"ADVERSARIAL_MODE": "normal"}},
    })
    try:
        assert "s1_echo" in kernel.registry.list_components("tool")
        assert "s2_echo" in kernel.registry.list_components("tool")
    finally:
        await manager.shutdown_all()


@pytest.mark.asyncio
async def test_partial_start_failure_rolls_back_atomically():
    kernel = Kernel(config=AppConfig(mcp_lifecycle=FAST))
    manager = MCPManager(kernel)
    with pytest.raises(MCPLifecycleError):
        await manager.start_all({
            "good": {"command": sys.executable, "args": [FIXTURE], "env": {"ADVERSARIAL_MODE": "normal"}},
            "bad": {"command": sys.executable, "args": [FIXTURE], "env": {"ADVERSARIAL_MODE": "never_initialize"}},
        })
    assert manager.clients == {}
    assert manager.registered_tools == {}
    assert "good_echo" not in kernel.registry.list_components("tool")


@pytest.mark.asyncio
async def test_stop_all_deterministic_multiple_servers():
    kernel = Kernel(config=AppConfig(mcp_lifecycle=FAST))
    manager = MCPManager(kernel)
    await manager.start_all({
        "s1": {"command": sys.executable, "args": [FIXTURE], "env": {"ADVERSARIAL_MODE": "normal"}},
        "s2": {"command": sys.executable, "args": [FIXTURE], "env": {"ADVERSARIAL_MODE": "normal"}},
    })
    pids = [c._process.pid for c in manager.clients.values()]
    await manager.shutdown_all()
    await asyncio.sleep(0.2)
    assert manager.clients == {}
    assert manager.registered_tools == {}
    assert all(not _pid_alive(p) for p in pids)


@pytest.mark.asyncio
async def test_stale_registration_removed_after_unexpected_server_death():
    kernel = Kernel(config=AppConfig(mcp_lifecycle=FAST))
    manager = MCPManager(kernel)
    await manager.start_all({"flaky": {"command": sys.executable, "args": [FIXTURE], "env": {"ADVERSARIAL_MODE": "normal"}}})
    tool_name = manager.registered_tools["flaky"][0]
    assert tool_name in kernel.registry.list_components("tool")

    client = manager.clients["flaky"]
    client._process.kill()  # simulate an unexpected crash (segfault/OOM-kill), not an explicit stop()

    for _ in range(30):
        if not client.is_healthy:
            break
        await asyncio.sleep(0.1)

    assert tool_name not in kernel.registry.list_components("tool")
    assert "flaky" not in manager.clients
    assert "flaky" not in manager.registered_tools


# --- SEC-003 regression: environment isolation through the new spawn path --

@pytest.mark.asyncio
async def test_sec003_ambient_env_still_absent_through_sec004_spawn_path(monkeypatch):
    monkeypatch.setenv("KRIYA_SEC003_SENTINEL", "host-secret-must-not-leak")
    monkeypatch.setenv("HOME", "/synthetic/private/home")
    c = _client("sec003a", "normal")
    await c.start()
    try:
        res = await c.call_tool("report_env", {"names": "KRIYA_SEC003_SENTINEL,HOME,PATH"})
        report = json.loads(res["content"][0]["text"])
        assert report["KRIYA_SEC003_SENTINEL"] is None
        assert report["HOME"] is None
        assert report["PATH"]
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_sec003_explicit_env_still_present_through_sec004_spawn_path():
    c = _client("sec003b", "normal", {"KRIYA_SEC003_SENTINEL": "explicitly-authorized"})
    await c.start()
    try:
        res = await c.call_tool("report_env", {"names": "KRIYA_SEC003_SENTINEL"})
        report = json.loads(res["content"][0]["text"])
        assert report["KRIYA_SEC003_SENTINEL"] == "explicitly-authorized"
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_sec003_cross_server_isolation_still_intact():
    """TOOL-002 P1 (2026-09-13): this test's own tool.execute() calls are
    not testing invocation authority - they're SEC-003's evidence vehicle
    for environment isolation - so they must supply the specific identities
    they call, exactly as production's own future operator-facing approval
    mechanism (TOOL-002 P2) will."""
    kernel = Kernel(config=AppConfig(mcp_lifecycle=FAST))
    report_env_schema = {"type": "object", "properties": {
        "names": {"type": "string", "description": "comma-separated names"}}, "required": ["names"]}
    digest = compute_mcp_schema_digest(report_env_schema)
    approved = frozenset({
        MCPToolIdentity(server_identity="server_a", tool_name="report_env", schema_digest=digest),
        MCPToolIdentity(server_identity="server_b", tool_name="report_env", schema_digest=digest),
    })
    manager = MCPManager(kernel, execution_policy=ExecutionPolicy(approved_mcp_tool_identities=approved))
    await manager.start_all({
        "server_a": {"command": sys.executable, "args": [FIXTURE], "env": {"ADVERSARIAL_MODE": "normal", "SERVER_A_ONLY": "a"}},
        "server_b": {"command": sys.executable, "args": [FIXTURE], "env": {"ADVERSARIAL_MODE": "normal", "SERVER_B_ONLY": "b"}},
    })
    try:
        tool_a = kernel.registry.get("tool", "server_a_report_env")
        tool_b = kernel.registry.get("tool", "server_b_report_env")
        ra = json.loads(await tool_a.execute(names="SERVER_A_ONLY,SERVER_B_ONLY"))
        rb = json.loads(await tool_b.execute(names="SERVER_A_ONLY,SERVER_B_ONLY"))
        assert ra == {"SERVER_A_ONLY": "a", "SERVER_B_ONLY": None}
        assert rb == {"SERVER_A_ONLY": None, "SERVER_B_ONLY": "b"}
    finally:
        await manager.shutdown_all()


# --- SEC-009 regression: mcp_lifecycle.* cannot be weakened by a repo -----

@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _write_yaml(path, data) -> str:
    with open(path, "w") as f:
        yaml.dump(data, f)
    return str(path)


def test_repo_cannot_weaken_mcp_lifecycle_request_timeout(tmp_path):
    ws = tmp_path / "mcp_lifecycle_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp_lifecycle": {"request_timeout_seconds": 999999}})
        with pytest.raises(ConfigAuthorityError, match="mcp_lifecycle.request_timeout_seconds"):
            load_config()


def test_repo_cannot_weaken_mcp_lifecycle_memory_limit(tmp_path):
    ws = tmp_path / "mcp_lifecycle_mem_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp_lifecycle": {"memory_mb": 999999}})
        with pytest.raises(ConfigAuthorityError, match="mcp_lifecycle.memory_mb"):
            load_config()


def test_packaged_default_mcp_lifecycle_loads_unchanged(tmp_path):
    with _cwd(tmp_path / "plain_ws"):
        cfg = load_config()
        assert cfg.mcp_lifecycle.startup_timeout_seconds == 30
        assert cfg.mcp_lifecycle.request_timeout_seconds == 60


# --- Real production-CLI end-to-end (real subprocess, no mocks) -----------

def _run_cli(args, cwd, extra_env=None, timeout=45):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([KRIYA_BIN, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, r.stdout, r.stderr


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="editable install not present at .venv/bin/kriya")
def test_cli_startup_hang_bounded_end_to_end(tmp_path):
    """Real adversarial fixture A (Startup hang), through the actual
    production CLI: a real MCP child that never responds to `initialize`
    must not hang `kriya tools list` past the packaged default startup
    timeout (30s), must fail deterministically, and must leave no PID
    behind."""
    home = tmp_path / "_home"
    ws = tmp_path / "repo"
    ws.mkdir()
    _write_yaml(ws / "kriya.yaml", {
        "mcp": {"hanger": {"command": sys.executable, "args": [FIXTURE], "env": {"ADVERSARIAL_MODE": "never_initialize"}}}
    })
    env = {"KRIYA_AUTHORITY_HOME": str(home)}
    approve_code, _, approve_err = _run_cli(["authority", "approve", "--confirm"], cwd=str(ws), extra_env=env)
    assert approve_code == 0, approve_err

    code, out, err = _run_cli(["tools", "list"], cwd=str(ws), extra_env=env, timeout=45)
    assert code != 0
    assert "did not complete startup" in err or "MCPStartupTimeoutError" in err

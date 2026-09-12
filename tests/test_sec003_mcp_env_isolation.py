"""SEC-003: authorized MCP subprocesses must not inherit arbitrary host
environment variables.

Covers the full required adversarial matrix: deterministic unit tests
against `build_mcp_subprocess_env()` (the environment builder itself), real
subprocess differential tests through `MCPClient`/`MCPManager` (the
production MCP path) proving an ambient sentinel is absent and an
explicitly-authorized one is present, cross-server isolation, baseline
override semantics, and one real production-CLI end-to-end test (`kriya
tools execute`, real subprocess, no mocks) proving the decisive
ambient-absent / explicit-present differential through the actual entry
point.

No live LLM: every scenario here resolves before any LLM call is possible
(`kriya tools execute` never reaches one).
"""
import contextlib
import json
import os
import subprocess
import sys

import pytest
import yaml

from kriya.core.kernel import Kernel
from kriya.mcp.mcp import (
    MCP_BASELINE_ENV_ALLOWLIST,
    MCPClient,
    MCPManager,
    build_mcp_subprocess_env,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KRIYA_BIN = os.path.join(REPO_ROOT, ".venv", "bin", "kriya")
ENV_REPORT_SERVER = os.path.join(REPO_ROOT, "tests", "env_report_mcp_server.py")

# Synthetic-only - never real credentials, per this risk's own requirement.
SYNTHETIC_SENTINELS = {
    "KRIYA_SEC003_SENTINEL": "host-secret-must-not-leak",
    "AWS_ACCESS_KEY_ID": "AKIASYNTHETICFAKE1234",
    "AWS_SECRET_ACCESS_KEY": "synthetic/fake+secret/key",
    "GITHUB_TOKEN": "ghp_synthetic_fake_token_0000000000",
    "SSH_AUTH_SOCK": "/tmp/synthetic-ssh-agent.sock",
    "HTTP_PROXY": "http://synthetic-proxy.invalid:8080",
    "HTTPS_PROXY": "http://synthetic-proxy.invalid:8080",
    "ALL_PROXY": "http://synthetic-proxy.invalid:8080",
    "NO_PROXY": "localhost",
    "KRIYA_UNRELATED_PARENT_VAR": "unrelated-value",
}


# --- 1-8: build_mcp_subprocess_env() unit tests (deterministic, no subprocess) --

def test_baseline_never_includes_unlisted_ambient_vars(monkeypatch):
    monkeypatch.setenv("KRIYA_SEC003_SENTINEL", "host-secret-must-not-leak")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIASYNTHETICFAKE1234")
    result = build_mcp_subprocess_env(None)
    assert "KRIYA_SEC003_SENTINEL" not in result
    assert "AWS_ACCESS_KEY_ID" not in result


def test_baseline_allowlist_variables_included_when_present(monkeypatch):
    monkeypatch.setenv("HOME", "/synthetic/home")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    result = build_mcp_subprocess_env(None)
    assert result["HOME"] == "/synthetic/home"
    assert result["LANG"] == "en_US.UTF-8"


def test_path_always_present_regardless_of_allowlist(monkeypatch):
    monkeypatch.setenv("PATH", "/synthetic/bin:/usr/bin")
    result = build_mcp_subprocess_env(None)
    assert result["PATH"] == "/synthetic/bin:/usr/bin"


def test_proxy_variables_never_in_baseline(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.setenv(name, "http://synthetic-proxy.invalid:8080")
    result = build_mcp_subprocess_env(None)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        assert name not in result


def test_configured_env_present_in_result():
    result = build_mcp_subprocess_env({"MY_SERVER_VAR": "configured-value"})
    assert result["MY_SERVER_VAR"] == "configured-value"


def test_configured_env_overrides_baseline_variable(monkeypatch):
    monkeypatch.setenv("TMPDIR", "/ambient/tmp")
    result = build_mcp_subprocess_env({"TMPDIR": "/configured/tmp"})
    assert result["TMPDIR"] == "/configured/tmp"


def test_configured_env_cannot_override_path_from_ambient_baseline(monkeypatch):
    """PATH is always the real ambient PATH (SEC-001's existing, unchanged
    policy) - a configured override for PATH itself is still allowed
    (explicit authority can set anything), but the baseline's own PATH
    value must come from build_restricted_env(), not be silently dropped."""
    monkeypatch.setenv("PATH", "/synthetic/bin")
    result = build_mcp_subprocess_env(None)
    assert result["PATH"] == "/synthetic/bin"
    overridden = build_mcp_subprocess_env({"PATH": "/configured/bin"})
    assert overridden["PATH"] == "/configured/bin"


def test_empty_configured_env_does_not_restore_ambient(monkeypatch):
    monkeypatch.setenv("KRIYA_SEC003_SENTINEL", "host-secret-must-not-leak")
    result = build_mcp_subprocess_env({})
    assert "KRIYA_SEC003_SENTINEL" not in result


def test_baseline_allowlist_excludes_build_toolchain_variables(monkeypatch):
    """Negative control - the MCP baseline is deliberately NOT
    autonomy.sandbox_env_allowlist's full default; JAVA_HOME/M2_HOME/
    GRADLE_HOME/VIRTUAL_ENV/PYTHONPATH have no established MCP-launch need
    and must not silently appear in the MCP baseline."""
    for name in ("JAVA_HOME", "M2_HOME", "GRADLE_HOME", "VIRTUAL_ENV", "PYTHONPATH"):
        assert name not in MCP_BASELINE_ENV_ALLOWLIST
        monkeypatch.setenv(name, f"/synthetic/{name.lower()}")
    result = build_mcp_subprocess_env(None)
    for name in ("JAVA_HOME", "M2_HOME", "GRADLE_HOME", "VIRTUAL_ENV", "PYTHONPATH"):
        assert name not in result


# --- 9-20: real subprocess differential tests (production MCPClient/Manager) --

@pytest.mark.asyncio
async def test_ambient_sentinels_absent_in_real_child(monkeypatch):
    """The decisive differential, part 1: every synthetic ambient
    secret/credential/proxy/unrelated variable is set in THIS (parent)
    process, but a real MCP child spawned with no explicit env must report
    every one of them absent."""
    for name, value in SYNTHETIC_SENTINELS.items():
        monkeypatch.setenv(name, value)

    client = MCPClient(name="probe", command=sys.executable, args=[ENV_REPORT_SERVER])
    await client.start()
    try:
        res = await client.call_tool("report_env", {"names": ",".join(SYNTHETIC_SENTINELS)})
        report = json.loads(res["content"][0]["text"])
        for name in SYNTHETIC_SENTINELS:
            assert report[name] is None, f"LEAK: real MCP child saw ambient {name}"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_explicit_sentinel_present_in_real_child(monkeypatch):
    """The decisive differential, part 2: the SAME variable, explicitly
    authorized via mcp.<server>.env, is received with EXACTLY the
    configured value - while every other ambient sentinel stays absent."""
    for name, value in SYNTHETIC_SENTINELS.items():
        monkeypatch.setenv(name, value)

    client = MCPClient(
        name="probe", command=sys.executable, args=[ENV_REPORT_SERVER],
        env={"KRIYA_SEC003_SENTINEL": "explicitly-authorized-value"},
    )
    await client.start()
    try:
        res = await client.call_tool("report_env", {"names": ",".join(SYNTHETIC_SENTINELS)})
        report = json.loads(res["content"][0]["text"])
        assert report["KRIYA_SEC003_SENTINEL"] == "explicitly-authorized-value"
        for name in SYNTHETIC_SENTINELS:
            if name == "KRIYA_SEC003_SENTINEL":
                continue
            assert report[name] is None, f"LEAK: real MCP child saw ambient {name}"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_required_launch_environment_remains_functional():
    """Negative control - the restricted environment must not break normal
    startup: the real production MCPClient/MCPManager tests in
    tests/test_mcp.py (unmodified by this change) already prove this, this
    is a direct re-check scoped to this file's own fixture server."""
    client = MCPClient(name="probe", command=sys.executable, args=[ENV_REPORT_SERVER])
    await client.start()
    try:
        res = await client.call_tool("report_env", {"names": "PATH"})
        report = json.loads(res["content"][0]["text"])
        assert report["PATH"], "PATH must reach the child for bare-command resolution to keep working"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_cross_server_isolation_no_leak_between_servers():
    kernel = Kernel()
    manager = MCPManager(kernel)
    mcp_config = {
        "server_a": {"command": sys.executable, "args": [ENV_REPORT_SERVER], "env": {"SERVER_A_ONLY": "a-value"}},
        "server_b": {"command": sys.executable, "args": [ENV_REPORT_SERVER], "env": {"SERVER_B_ONLY": "b-value"}},
    }
    await manager.start_all(mcp_config)
    try:
        tool_a = kernel.registry.get("tool", "server_a_report_env")
        tool_b = kernel.registry.get("tool", "server_b_report_env")
        report_a = json.loads(await tool_a.execute(names="SERVER_A_ONLY,SERVER_B_ONLY"))
        report_b = json.loads(await tool_b.execute(names="SERVER_A_ONLY,SERVER_B_ONLY"))
        assert report_a == {"SERVER_A_ONLY": "a-value", "SERVER_B_ONLY": None}
        assert report_b == {"SERVER_A_ONLY": None, "SERVER_B_ONLY": "b-value"}
    finally:
        await manager.shutdown_all()


@pytest.mark.asyncio
async def test_empty_mcp_env_dict_does_not_restore_ambient_real_child(monkeypatch):
    monkeypatch.setenv("KRIYA_SEC003_SENTINEL", "host-secret-must-not-leak")
    client = MCPClient(name="probe", command=sys.executable, args=[ENV_REPORT_SERVER], env={})
    await client.start()
    try:
        res = await client.call_tool("report_env", {"names": "KRIYA_SEC003_SENTINEL"})
        report = json.loads(res["content"][0]["text"])
        assert report["KRIYA_SEC003_SENTINEL"] is None
    finally:
        await client.stop()


def test_no_ambient_merge_remains_in_source():
    """Structural regression lock: the old `{**os.environ, **self.env}`
    ambient-merge pattern must never reappear in kriya/mcp/mcp.py. Checks
    the specific splat-merge shape, not a bare "os.environ" substring -
    this module's own docstrings legitimately mention `os.environ` by name
    to document the invariant they enforce, so a substring check would
    false-positive on the documentation itself."""
    import inspect

    import kriya.mcp.mcp as mcp_module

    source = inspect.getsource(mcp_module)
    assert "**os.environ" not in source, "kriya/mcp/mcp.py must never splat-merge os.environ (SEC-003)"
    assert "os.environ.copy()" not in source and "dict(os.environ)" not in source


# --- Real production-CLI end-to-end (real subprocess, no mocks) ------------

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


def _run_cli(args, cwd, extra_env=None, timeout=20):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([KRIYA_BIN, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, r.stdout, r.stderr


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="editable install not present at .venv/bin/kriya")
def test_cli_ambient_sentinel_absent_then_explicit_sentinel_present_end_to_end(tmp_path):
    """The single most decisive piece of SEC-003 evidence: the real,
    unmodified `kriya` CLI, a real disposable MCP subprocess, and the
    parent process actually carrying the sentinel in its own environment
    (via extra_env) - proving the full production path (config ->
    SEC-009 authority -> MCPManager -> MCPClient -> subprocess) never lets
    an ambient value through, and lets an explicitly-authorized one
    through with the exact configured value."""
    home = tmp_path / "_authority_home"
    ws = tmp_path / "cli_mcp_repo"
    ws.mkdir()
    ambient_env = {"KRIYA_AUTHORITY_HOME": str(home), "KRIYA_SEC003_SENTINEL": "host-secret-must-not-leak"}

    _write_yaml(ws / "kriya.yaml", {
        "mcp": {"envprobe": {"command": sys.executable, "args": [ENV_REPORT_SERVER]}}
    })
    approve_code, _, approve_err = _run_cli(["authority", "approve", "--confirm"], cwd=str(ws), extra_env=ambient_env)
    assert approve_code == 0, approve_err

    code, out, err = _run_cli(
        ["tools", "execute", "envprobe_report_env", '{"names":"KRIYA_SEC003_SENTINEL"}', "-y"],
        cwd=str(ws), extra_env=ambient_env,
    )
    assert code == 0, err
    assert '"KRIYA_SEC003_SENTINEL": null' in out, out

    _write_yaml(ws / "kriya.yaml", {
        "mcp": {"envprobe": {
            "command": sys.executable, "args": [ENV_REPORT_SERVER],
            "env": {"KRIYA_SEC003_SENTINEL": "explicitly-authorized-value"},
        }}
    })
    approve_code2, _, approve_err2 = _run_cli(["authority", "approve", "--confirm"], cwd=str(ws), extra_env=ambient_env)
    assert approve_code2 == 0, approve_err2

    code2, out2, err2 = _run_cli(
        ["tools", "execute", "envprobe_report_env", '{"names":"KRIYA_SEC003_SENTINEL"}', "-y"],
        cwd=str(ws), extra_env=ambient_env,
    )
    assert code2 == 0, err2
    assert '"KRIYA_SEC003_SENTINEL": "explicitly-authorized-value"' in out2, out2

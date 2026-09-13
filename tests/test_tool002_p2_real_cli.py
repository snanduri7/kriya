"""TOOL-002 P2: real, unmodified production CLI evidence (Task 13/14/15).

Task 13's required sequence (A-F), driven through the real `kriya` binary
exactly as SEC-003's own real-CLI test does (same _cwd/_write_yaml/_run_cli
harness) - proving the durable approval mechanism restores the production
capability TOOL-002 P1 disclosed as lost, with EXACTLY-ONE/ZERO tools/call
counted server-side via the fixture's own CALL_LOG_FILE (never inferred
from the client's own stdout).

Task 14 (approved malicious tool remains capability-constrained under real
OCI containment) is exercised directly through MCPManager/ExecutionPolicy
wired with the SAME production resolver `kriya mcp approve` itself writes
to - Task 14 does not require driving it through subprocess CLI calls the
way Task 13 explicitly does; what matters is that the real, production
`_check_mcp_invocation` durable-approval code path is exercised together
with real Docker containment, which this achieves without extra process-
spawn overhead. Requires a real Docker daemon - skipped otherwise.
"""
import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KRIYA_BIN = os.path.join(REPO_ROOT, ".venv", "bin", "kriya")
FIXTURE = os.path.join(REPO_ROOT, "tests", "tool002_mcp_fixture.py")


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


def _run_cli(args, cwd, extra_env=None, timeout=30):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([KRIYA_BIN, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, r.stdout, r.stderr


def _call_log_lines(path) -> list:
    if not os.path.isfile(path):
        return []
    with open(path) as f:
        return [ln for ln in f.read().splitlines() if ln.strip()]


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="editable install not present at .venv/bin/kriya")
def test_real_cli_full_lifecycle_a_through_f(tmp_path):
    """Required tests 29/30/31/32 + Task 6 (-y cannot approve) + Task 13's
    full A-F sequence, all through the real, unmodified `kriya` binary."""
    ws = tmp_path / "repo"
    ws.mkdir()
    call_log = tmp_path / "calls.log"
    env = {
        "KRIYA_AUTHORITY_HOME": str(tmp_path / "_authority_home"),
        "KRIYA_MCP_APPROVAL_HOME": str(tmp_path / "_mcp_approval_home"),
    }
    # SEC-003: CALL_LOG_FILE must be an explicitly configured mcp.<server>.env
    # value, not an ambient env var - build_mcp_subprocess_env() never
    # forwards ambient os.environ, by design.
    _write_yaml(ws / "kriya.yaml", {
        "mcp": {"fixture": {
            "command": sys.executable, "args": [FIXTURE],
            "env": {"CALL_LOG_FILE": str(call_log)},
        }},
    })

    code, _, err = _run_cli(["authority", "approve", "--confirm"], cwd=str(ws), extra_env=env)
    assert code == 0, err

    # A - unapproved: REQUIRE_APPROVAL, zero tools/call, zero side effect.
    code, out, err = _run_cli(
        ["tools", "execute", "fixture_echo", '{"message":"a"}'], cwd=str(ws), extra_env=env,
    )
    assert "MCP_TOOL_REQUIRES_APPROVAL" in (out + err)
    assert _call_log_lines(call_log) == []

    # B - -y without approval: still denied, still zero tools/call. This is
    # Task 6's own required proof: -y is NOT blanket MCP approval.
    code, out, err = _run_cli(
        ["tools", "execute", "fixture_echo", '{"message":"a"}', "-y"], cwd=str(ws), extra_env=env,
    )
    assert "MCP_TOOL_REQUIRES_APPROVAL" in (out + err)
    assert _call_log_lines(call_log) == []

    # C - explicit approval through the production CLI.
    code, out, err = _run_cli(["mcp", "approve", "fixture_echo", "--confirm"], cwd=str(ws), extra_env=env)
    assert code == 0, err
    assert "Approved" in out
    approval_path_line = [l for l in out.splitlines() if "written to" in l]
    assert approval_path_line, out
    written_path = approval_path_line[0].split("written to", 1)[1].strip().rstrip(".")
    assert os.path.isfile(written_path)
    assert os.path.realpath(os.path.dirname(written_path)) == os.path.realpath(str(tmp_path / "_mcp_approval_home"))

    # D - execute: exactly one tools/call, expected result.
    code, out, err = _run_cli(
        ["tools", "execute", "fixture_echo", '{"message":"hello"}', "-y"], cwd=str(ws), extra_env=env,
    )
    assert code == 0, err
    assert "Echo: hello" in out
    assert _call_log_lines(call_log) == ["echo"]

    # E - fresh process, same persisted approval: proves DURABILITY, not
    # same-process in-memory state (a brand-new `kriya` process is spawned
    # for every _run_cli call already, but this second call additionally
    # follows the first's own kernel teardown, closing any doubt).
    code, out, err = _run_cli(
        ["tools", "execute", "fixture_echo", '{"message":"again"}', "-y"], cwd=str(ws), extra_env=env,
    )
    assert code == 0, err
    assert "Echo: again" in out
    assert _call_log_lines(call_log) == ["echo", "echo"]

    # F - revoke: fresh invocation denied, zero NEW tools/call.
    code, out, err = _run_cli(["mcp", "revoke", "fixture_echo"], cwd=str(ws), extra_env=env)
    assert code == 0, err
    assert "Revoked" in out

    code, out, err = _run_cli(
        ["tools", "execute", "fixture_echo", '{"message":"a"}', "-y"], cwd=str(ws), extra_env=env,
    )
    assert "MCP_TOOL_REQUIRES_APPROVAL" in (out + err)
    assert _call_log_lines(call_log) == ["echo", "echo"]  # unchanged - no new call


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="editable install not present at .venv/bin/kriya")
def test_real_cli_revoke_then_deny_does_not_require_restart(tmp_path):
    """Task 11: revocation must take effect on the very next invocation,
    with no restart of Kriya required - proven here by NOT restarting
    anything special between revoke and the next execute (every `kriya`
    CLI invocation is already its own fresh process, so this specifically
    confirms revocation is visible immediately to the NEXT such process,
    not merely 'eventually' after some warm state expires)."""
    ws = tmp_path / "repo"
    ws.mkdir()
    call_log = tmp_path / "calls.log"
    env = {
        "KRIYA_AUTHORITY_HOME": str(tmp_path / "_authority_home"),
        "KRIYA_MCP_APPROVAL_HOME": str(tmp_path / "_mcp_approval_home"),
    }
    _write_yaml(ws / "kriya.yaml", {"mcp": {"fixture": {
        "command": sys.executable, "args": [FIXTURE], "env": {"CALL_LOG_FILE": str(call_log)},
    }}})
    assert _run_cli(["authority", "approve", "--confirm"], cwd=str(ws), extra_env=env)[0] == 0
    assert _run_cli(["mcp", "approve", "fixture_echo", "--confirm"], cwd=str(ws), extra_env=env)[0] == 0
    code, out, _ = _run_cli(["tools", "execute", "fixture_echo", '{"message":"x"}', "-y"], cwd=str(ws), extra_env=env)
    assert code == 0 and "Echo: x" in out
    assert _run_cli(["mcp", "revoke", "fixture_echo"], cwd=str(ws), extra_env=env)[0] == 0
    code, out, err = _run_cli(["tools", "execute", "fixture_echo", '{"message":"x"}', "-y"], cwd=str(ws), extra_env=env)
    assert "MCP_TOOL_REQUIRES_APPROVAL" in (out + err)
    assert _call_log_lines(call_log) == ["echo"]


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="editable install not present at .venv/bin/kriya")
def test_real_cli_capability_profile_drift_invalidates_approval(tmp_path):
    """Task 8's mandatory scenario, proven end-to-end through the real CLI
    (not just at the store/digest level - see test_tool002_p2_invocation_
    approval.py's test_capability_profile_digest_drift_denies for that
    lower-level proof): approve a tool under capability profile A, then
    change the SAME server's mcp.fixture.capabilities so it resolves to a
    DIFFERENT profile digest B - the old durable approval must no longer
    validate, denying the very next invocation with zero new tools/call."""
    ws = tmp_path / "repo"
    ws.mkdir()
    call_log = tmp_path / "calls.log"
    env = {
        "KRIYA_AUTHORITY_HOME": str(tmp_path / "_authority_home"),
        "KRIYA_MCP_APPROVAL_HOME": str(tmp_path / "_mcp_approval_home"),
    }
    _write_yaml(ws / "kriya.yaml", {"mcp": {"fixture": {
        "command": sys.executable, "args": [FIXTURE], "env": {"CALL_LOG_FILE": str(call_log)},
    }}})
    assert _run_cli(["authority", "approve", "--confirm"], cwd=str(ws), extra_env=env)[0] == 0
    assert _run_cli(["mcp", "approve", "fixture_echo", "--confirm"], cwd=str(ws), extra_env=env)[0] == 0

    code, out, err = _run_cli(["tools", "execute", "fixture_echo", '{"message":"x"}', "-y"], cwd=str(ws), extra_env=env)
    assert code == 0, err
    assert "Echo: x" in out
    assert _call_log_lines(call_log) == ["echo"]

    # Same server, same tool, same schema - ONLY the capability profile
    # changes (mcp.* is SEC-009 SECURITY_AUTHORITY, so it must be
    # re-approved there too before Kriya will even load this config).
    _write_yaml(ws / "kriya.yaml", {"mcp": {"fixture": {
        "command": sys.executable, "args": [FIXTURE], "env": {"CALL_LOG_FILE": str(call_log)},
        "capabilities": {"workspace_read": True},
    }}})
    assert _run_cli(["authority", "approve", "--confirm"], cwd=str(ws), extra_env=env)[0] == 0

    code, out, err = _run_cli(["mcp", "inspect"], cwd=str(ws), extra_env=env)
    assert code == 0, err
    assert "NOT APPROVED" in out, out

    code, out, err = _run_cli(["tools", "execute", "fixture_echo", '{"message":"x"}', "-y"], cwd=str(ws), extra_env=env)
    assert "MCP_TOOL_REQUIRES_APPROVAL" in (out + err)
    assert _call_log_lines(call_log) == ["echo"]  # unchanged - no new call


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="editable install not present at .venv/bin/kriya")
def test_real_cli_host_mode_explicitly_labeled_non_contained(tmp_path):
    """Required test 27 / Task 15: with containment disabled (the default),
    `kriya mcp inspect` must explicitly show containment as NOT active -
    never silent omission, never a claim of TOOL-003 protection."""
    ws = tmp_path / "repo"
    ws.mkdir()
    env = {
        "KRIYA_AUTHORITY_HOME": str(tmp_path / "_authority_home"),
        "KRIYA_MCP_APPROVAL_HOME": str(tmp_path / "_mcp_approval_home"),
    }
    _write_yaml(ws / "kriya.yaml", {"mcp": {"fixture": {"command": sys.executable, "args": [FIXTURE]}}})
    assert _run_cli(["authority", "approve", "--confirm"], cwd=str(ws), extra_env=env)[0] == 0
    code, out, err = _run_cli(["mcp", "inspect"], cwd=str(ws), extra_env=env)
    assert code == 0, err
    assert "containment required:  False" in out
    assert "containment active:    False" in out


# =====================================================================
# Task 14 - real OCI containment + durable approval, combined
# =====================================================================

pytestmark_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")


def _docker_reachable() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


@pytestmark_docker
@pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")
def test_real_contained_durably_approved_malicious_tool_still_capability_constrained(tmp_path):
    """Required test 33 / Task 14 - the combined control-plane proof:
    operator invocation approval does not mean unrestricted process
    authority. Approves (via the SAME production durable-approval module
    `kriya mcp approve` itself writes through) a benignly-named tool whose
    real handler secretly attempts an unauthorized write AND an
    unauthorized connect. TOOL-002 must ALLOW (exactly one tools/call);
    TOOL-003's real OCI containment must still block both hidden effects."""
    from kriya.config.config import AppConfig, AutonomyConfig, MCPLifecycleConfig
    from kriya.core.kernel import Kernel
    from kriya.mcp.capability import compute_mcp_capability_profile_digest, resolve_mcp_capability_profile
    from kriya.mcp.invocation_approval import add_approval, empty_artifact, save_approval_artifact, default_local_approval_path
    from kriya.mcp.mcp import MCPManager
    from kriya.policy.execution import ExecutionPolicy
    from kriya.policy.model import MCPToolIdentity, compute_mcp_schema_digest
    from kriya.control.workspace_identity import workspace_identity

    tests_dir = os.path.join(REPO_ROOT, "tests")
    # The container has no access to the host filesystem outside its own
    # mounts - `additional_read_paths: [tests_dir]` below mounts it
    # read-only at this fixed, deterministic container path (see
    # kriya/mcp/containment_adapter.py's own convention, and
    # test_tool003_p2_oci_enforcement.py's identical FIXTURE_IN_CONTAINER
    # constant) - the command run INSIDE the container must reference
    # this container-side path, never the host-absolute `fixture` path.
    fixture_in_container = "/kriya/mcp/extra/ro/0/tool003_p2_capability_fixture.py"

    outside_target = str(tmp_path / "outside_write_target.txt")
    listener_host, listener_port = "127.0.0.1", "58123"

    ws = tmp_path / "repo"
    with _cwd(ws):
        workspace_root = os.path.realpath(os.getcwd())
        capability_config = {"additional_read_paths": [tests_dir]}  # no write, no network authority
        profile = resolve_mcp_capability_profile(capability_config, workspace_root=workspace_root)
        profile_digest = compute_mcp_capability_profile_digest(profile)

        schema = {"type": "object", "properties": {}, "required": []}
        identity = MCPToolIdentity(
            server_identity="fixture", tool_name="check_service_status",
            schema_digest=compute_mcp_schema_digest(schema),
        )

        wid = workspace_identity(workspace_root)
        artifact = add_approval(empty_artifact(wid), identity, profile_digest)
        save_approval_artifact(default_local_approval_path(workspace_root), artifact)

        cfg = AppConfig(
            autonomy=AutonomyConfig(mcp_contained_execution_required=True, containment_backend="oci"),
            mcp_lifecycle=MCPLifecycleConfig(),
        )
        kernel = Kernel(config=cfg)  # production default: real durable resolver wired in MCPManager.__init__
        server_cfg = {
            "command": "python3", "args": [fixture_in_container],
            "env": {"MALICIOUS_WRITE_TARGET": outside_target,
                    "MALICIOUS_CONNECT_HOST": listener_host, "MALICIOUS_CONNECT_PORT": listener_port},
            "capabilities": capability_config,
        }

        async def run():
            await kernel.mcp.start_all({"fixture": server_cfg})
            try:
                tool = kernel.registry.get("tool", "fixture_check_service_status")
                result = await tool.execute()
                return result
            finally:
                await kernel.mcp.shutdown_all()

        result = asyncio.run(run())
        payload = json.loads(result)
        assert payload["status"] == "ok"  # TOOL-002 ALLOWed the call
        assert "write_failed" in payload["_hidden_write"] or "no_target_configured" == payload["_hidden_write"]
        assert not os.path.exists(outside_target), "TOOL-003 containment must block the hidden unauthorized write"
        assert "connect_failed" in payload["_hidden_connect"] or payload["_hidden_connect"] == "no_target_configured"

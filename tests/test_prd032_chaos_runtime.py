"""PRD-032 family C: tool, runtime and filesystem failures (deterministic).

Every failure is injected at the one seam that owns it: a real hanging
process under ProcessController, a real adversarial MCP server behind
MCPManager and the governed tool path, a real docker CLI whose daemon is
unreachable, the PRD-031A gate over the test fake provider, the real Semgrep
adapter's output parser, the commit seam's own os.replace / staged write,
and the runtime port for a disappearing model endpoint.
"""
import asyncio
import errno
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    assert_bounded_retry,
    assert_no_false_pass,
    audit_run_records,
    benign_roles,
    chaos,
    chaos_config,
    chaos_engine,
    git_workspace,
    run_direct,
    static_analysis_config,
    typed_failure,
)
from _fake_inference_runtime import FakeServerError
from _fake_static_analysis import FakeKnobs, FakeRegistration

from kriya.config.config import AppConfig, MCPLifecycleConfig
from kriya.core.kernel import Kernel
from kriya.mcp.mcp import MCPManager
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import MCPToolIdentity, compute_mcp_schema_digest
from kriya.static_analysis import model as sa_model
from kriya.static_analysis.adapters.semgrep import interpret_output
from kriya.static_analysis.model import ScanStatus
from kriya.tools.containment import BackendUnavailableError, ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import OCIContainmentBackend
from kriya.tools.process import ProcessController
from kriya.tools.tool import ToolExecutionError

GOAL = "add sub to calc.py"
FIXTURE = str(Path(__file__).resolve().parent / "adversarial_mcp_server.py")
FAST = MCPLifecycleConfig(startup_timeout_seconds=5, request_timeout_seconds=2,
                          shutdown_grace_seconds=1, force_kill_reap_seconds=2)


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(tmp_path / "trusted" / "mcp"))
    monkeypatch.setenv("KRIYA_STATIC_ANALYSIS_HOME", str(tmp_path / "trusted" / "waivers"))


@chaos("C01")
def test_a_hung_command_times_out_and_its_whole_tree_is_reaped(chaos_case, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    pids = work / "pids.json"
    script = (
        "import json, os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pids)!r}, 'w').write(json.dumps([os.getpid(), child.pid]))\n"
        "time.sleep(120)\n"
    )
    chaos_case.arm()
    started = time.monotonic()
    result = ProcessController(reap_timeout=5).run([sys.executable, "-c", script], cwd=str(work), timeout=2)
    elapsed = time.monotonic() - started
    assert result.timeout is True and result.returncode == -1
    assert "[TIMEOUT]" in result.stderr
    assert elapsed < 15, elapsed
    spawned = json.loads(pids.read_text())
    for _ in range(50):
        if not any(_pid_alive(pid) for pid in spawned):
            break
        time.sleep(0.1)
    assert not any(_pid_alive(pid) for pid in spawned), "orphaned process survived the timeout"
    chaos_case.assert_tree(allowed={"work/pids.json"})
    chaos_case.observe("PROCESS_TIMEOUT", returncode=result.returncode, tree_reaped=True)


def _echo_identity(server):
    schema = {"type": "object", "properties": {"message": {"type": "string", "description": "Message to echo."}},
              "required": ["message"]}
    return MCPToolIdentity(server_identity=server, tool_name="echo", schema_digest=compute_mcp_schema_digest(schema))


@chaos("C02")
def test_a_malformed_mcp_response_is_tolerated_and_authority_is_unchanged(chaos_case, tmp_path):
    chaos_case.arm()

    async def scenario():
        kernel = Kernel(config=AppConfig(mcp_lifecycle=FAST))
        manager = MCPManager(kernel, execution_policy=ExecutionPolicy(
            approved_mcp_tool_identities=frozenset({_echo_identity("hostile")})))
        await manager.start_all({"hostile": {"command": sys.executable, "args": [FIXTURE],
                                             "env": {"ADVERSARIAL_MODE": "malformed_response"}}})
        try:
            echoed = await kernel.registry.get("tool", "hostile_echo").execute(message="after-garbage")
            with pytest.raises(ToolExecutionError) as denied:
                await kernel.registry.get("tool", "hostile_report_env").execute(names="HOME")
            assert isinstance(denied.value.__cause__, PolicyDeniedError)
            return echoed
        finally:
            await manager.shutdown_all()

    echoed = asyncio.run(scenario())
    chaos_case.assert_tree()
    assert "Echo: after-garbage" in str(echoed)
    chaos_case.observe("MALFORMED_LINE_TOLERATED_UNAPPROVED_DENIED", approved_call="ok", unapproved_call="PolicyDeniedError")


@chaos("C03")
def test_an_mcp_server_that_disappears_is_unregistered_and_fails_closed(chaos_case, tmp_path):
    chaos_case.arm()

    async def scenario():
        kernel = Kernel(config=AppConfig(mcp_lifecycle=FAST))
        manager = MCPManager(kernel, execution_policy=ExecutionPolicy(
            approved_mcp_tool_identities=frozenset({_echo_identity("flaky")})))
        await manager.start_all({"flaky": {"command": sys.executable, "args": [FIXTURE],
                                           "env": {"ADVERSARIAL_MODE": "normal"}}})
        stale_tool = kernel.registry.get("tool", "flaky_echo")
        client = manager.clients["flaky"]
        client._process.kill()  # the server dies mid-session
        for _ in range(50):
            if not client.is_healthy:
                break
            await asyncio.sleep(0.1)
        registered = kernel.registry.list_components("tool")
        with pytest.raises(Exception) as failure:
            await stale_tool.execute(message="anyone there?")
        await manager.shutdown_all()
        return registered, type(failure.value).__name__

    registered, error = asyncio.run(scenario())
    chaos_case.assert_tree()
    assert "flaky_echo" not in registered
    chaos_case.observe("SERVER_GONE_TOOL_UNREGISTERED", stale_call=error)


@chaos("C04")
def test_docker_disappearing_never_falls_back_to_the_host(chaos_case, tmp_path, monkeypatch):
    if shutil.which("docker") is None:
        pytest.fail("C04 needs the docker CLI on PATH (the daemon itself is made unreachable on purpose)")
    monkeypatch.setenv("DOCKER_HOST", f"unix://{tmp_path / 'no-daemon.sock'}")
    marker = tmp_path / "host-side-effect"
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, network=NetworkAuthority.DENIED,
                                 workspace_path=str(tmp_path))
    chaos_case.arm()
    with pytest.raises(BackendUnavailableError) as refused:
        ProcessController().run(["sh", "-c", f"touch {marker}"], cwd=str(tmp_path), timeout=30,
                                containment_profile=profile, containment_backend=OCIContainmentBackend())
    chaos_case.assert_tree()
    assert not marker.exists()
    # The same through the pipeline: containment required, daemon gone.
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    runtime = ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer" else benign_roles(role, request))
    cfg = chaos_config(contained_execution_required=True, containment_backend="oci")
    chaos_case.arm()
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg), GOAL, workspace)
    chaos_case.assert_tree()
    assert_no_false_pass(result)
    audit = audit_run_records(workspace)
    assert result.get("environment_failure"), sorted(result)
    chaos_case.observe(typed_failure(result), process_refusal=type(refused.value).__name__,
                       attempts=assert_bounded_retry(result), **audit.evidence())


def _static_pipeline(tmp_path, developer_text, knobs):
    workspace = git_workspace(tmp_path, {"calc.py": CALC})
    runtime = ChaosRuntime(lambda role, request: developer_text if role == "developer" else benign_roles(role, request))
    return workspace, runtime, knobs


def _run_static(chaos_case, workspace, runtime, knobs, **static):
    """The pipeline with the gate on the fake provider (the configuration is
    validated while the provider is registered)."""
    chaos_case.arm()
    with FakeRegistration(knobs) as fake, RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(static_analysis_config(**static)), GOAL, workspace)
    return result, fake


def _blocked(chaos_case, workspace, result, outcome):
    chaos_case.assert_tree()
    assert_no_false_pass(result)
    audit = audit_run_records(workspace)
    static = result.get("static_analysis") or {}
    assert static.get("outcome") == outcome, static
    assert result.get("accepted_risk") is not True
    assert Path(workspace, "calc.py").read_text() == CALC
    return audit, static


@chaos("C05")
def test_a_required_analyzer_that_is_unavailable_blocks_the_commit(chaos_case, tmp_path):
    workspace, runtime, knobs = _static_pipeline(tmp_path, CALC_WITH_SUB, FakeKnobs(probe_failure=sa_model.PROVIDER_PROBE_FAILED))
    result, _ = _run_static(chaos_case, workspace, runtime, knobs)
    audit, static = _blocked(chaos_case, workspace, result, "UNAVAILABLE")
    chaos_case.observe(typed_failure(result), static_outcome=static["outcome"],
                       reason_codes=sorted(static.get("reason_codes", [])), **audit.evidence())


@chaos("C06")
def test_malformed_scanner_output_is_never_a_pass(chaos_case, tmp_path):
    chaos_case.arm()
    parsed = [interpret_output(stdout, 0, ["calc.py"], frozenset({"r"}), str(tmp_path)).status
              for stdout in ("", "not json {{{", '{"results": []', '{"results": [], "paths": {}}',
                             '{"results": {}, "paths": {"scanned": []}, "errors": []}')]
    assert parsed == [ScanStatus.MALFORMED_OUTPUT] * 5
    workspace, runtime, knobs = _static_pipeline(tmp_path, CALC_WITH_SUB, FakeKnobs(status={"post": ScanStatus.MALFORMED_OUTPUT}))
    result, _ = _run_static(chaos_case, workspace, runtime, knobs)
    audit, static = _blocked(chaos_case, workspace, result, "UNKNOWN")
    chaos_case.observe(typed_failure(result), adapter_status="MALFORMED_OUTPUT", static_outcome=static["outcome"],
                       **audit.evidence())


@chaos("C07")
def test_an_unconfirmed_required_target_is_unknown_and_blocks(chaos_case, tmp_path):
    workspace, runtime, knobs = _static_pipeline(tmp_path, CALC_WITH_SUB, FakeKnobs(unconfirmed=("calc.py",)))
    result, _ = _run_static(chaos_case, workspace, runtime, knobs)
    audit, static = _blocked(chaos_case, workspace, result, "UNKNOWN")
    chaos_case.observe(typed_failure(result), static_outcome=static["outcome"], **audit.evidence())


def _real_workspace_target(workspace, path):
    """A path in the real workspace, not in the candidate worktree (which
    lives under .kriya/ and is written through the same staging code)."""
    relative = os.path.relpath(os.path.realpath(str(path)), str(workspace))
    return not relative.startswith((os.pardir, ".kriya"))


TWO_FILE_DESIGN = "Design: sub in calc.py, a constant in helper.py.\n```json\n" + json.dumps(
    {"files": ["calc.py", "helper.py"]}) + "\n```\n"
HELPER = "X = 1\n"
HELPER_NEW = "X = 2\n"


def _two_file_responder(role, request):
    from _chaos_harness import requested_file

    if role == "architect":
        return TWO_FILE_DESIGN
    if role == "developer":
        return HELPER_NEW if requested_file(request) == "helper.py" else CALC_WITH_SUB
    return benign_roles(role, request)


def _commit_failure_run(chaos_case, tmp_path, monkeypatch, fail):
    """The two-file pipeline with ``fail(src, dst)`` consulted on every
    staged-file replace the commit makes (an OSError it returns is raised
    instead of replacing)."""
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "helper.py": HELPER})
    runtime = ChaosRuntime(_two_file_responder)
    real_replace = os.replace

    def replace(src, dst, *args, **kwargs):
        if os.path.basename(str(src)).startswith(".kriya-stage-") and _real_workspace_target(workspace, dst):
            error = fail(src, dst)
            if error is not None:
                raise error
        return real_replace(src, dst, *args, **kwargs)

    chaos_case.arm()
    with RuntimeRegistration(runtime):
        monkeypatch.setattr(os, "replace", replace)
        try:
            result = run_direct(chaos_engine(chaos_config()), GOAL, workspace)
        finally:
            monkeypatch.setattr(os, "replace", real_replace)
    return workspace, result


@chaos("C08")
def test_an_io_error_inside_the_commit_is_settled_from_evidence(chaos_case, tmp_path, monkeypatch):
    calls = {"n": 0}

    def fail(src, dst):
        # The first staged file lands; the second replace fails.
        calls["n"] += 1
        return OSError(errno.EIO, "injected I/O error") if calls["n"] == 2 else None

    workspace, result = _commit_failure_run(chaos_case, tmp_path, monkeypatch, fail)
    assert calls["n"] >= 2, "the commit never reached its second replace"
    chaos_case.assert_tree()
    assert_no_false_pass(result)
    audit = audit_run_records(workspace)
    # Never silently partial: the file that had landed is restored.
    assert Path(workspace, "calc.py").read_text() == CALC and Path(workspace, "helper.py").read_text() == HELPER
    assert set(audit.commit_results) <= {"ROLLED_BACK", "NOT_COMMITTED"}, audit
    chaos_case.observe(typed_failure(result), **audit.evidence())


@chaos("C09")
def test_disk_full_while_staging_leaves_the_workspace_unchanged(chaos_case, tmp_path, monkeypatch):
    from kriya.workflow import edit_safety

    workspace = git_workspace(tmp_path, {"calc.py": CALC, "helper.py": HELPER})
    runtime = ChaosRuntime(_two_file_responder)
    staged = {"n": 0}
    real_mkstemp = edit_safety.tempfile.mkstemp

    def full_disk(*args, **kwargs):
        if str(kwargs.get("prefix", "")).startswith(".kriya-stage-") and _real_workspace_target(workspace, kwargs.get("dir", "")):
            staged["n"] += 1
            if staged["n"] == 2:
                raise OSError(errno.ENOSPC, "No space left on device")
        return real_mkstemp(*args, **kwargs)

    chaos_case.arm()
    with RuntimeRegistration(runtime):
        monkeypatch.setattr(edit_safety.tempfile, "mkstemp", full_disk)
        result = run_direct(chaos_engine(chaos_config()), GOAL, workspace)
    assert staged["n"] >= 2, "the commit never staged its second file"
    chaos_case.assert_tree()
    assert_no_false_pass(result)
    audit = audit_run_records(workspace)
    assert Path(workspace, "calc.py").read_text() == CALC and Path(workspace, "helper.py").read_text() == HELPER
    leftovers = [p.name for p in workspace.iterdir() if p.name.startswith(".kriya-stage-")]
    assert leftovers == [], leftovers
    assert set(audit.commit_results) <= {"ROLLED_BACK", "NOT_COMMITTED"}, audit
    chaos_case.observe(typed_failure(result), **audit.evidence())


@chaos("C10")
def test_a_disappearing_model_endpoint_is_a_typed_failure(chaos_case, tmp_path):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})

    def responder(role, request):
        if role == "developer":
            return FakeServerError("connection refused: endpoint gone")
        return benign_roles(role, request)

    runtime = ChaosRuntime(responder)
    chaos_case.arm()
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), GOAL, workspace)
    chaos_case.assert_tree()
    assert_no_false_pass(result)
    audit = audit_run_records(workspace)
    attempts = assert_bounded_retry(result)
    chaos_case.observe(typed_failure(result), attempts=attempts, model_requests=runtime.count("developer"),
                       **audit.evidence())

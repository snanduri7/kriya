"""D2 EXEC-LAYER rebuild S5: MCP execution asks the canonical external-execution decision owner (kriya/tools/containment.py).

MEASURED before the change (2026-10-25, scratchpad s5/c2.py): ``mcp_contained_execution_required=True`` + ``containment_backend="none"`` + a server whose
declared network is ``unrestricted`` made the Null backend ACCEPT the profile, so the MCP server ran on the HOST, in the project cwd, with Kriya's FULL
environment (``env=None`` inherits), while the client reported ``containment_active=True``: a fail-open hole and false containment evidence. The same
setup with the default ``denied`` network was refused only because the Null backend happens to refuse that network.

Now ``MCPManager.start_all`` takes its decision and its backend from the owner (``decide_external_execution(..., subject=mcp_subject(name))``): mandatory
containment with a non-isolating, unknown or unavailable backend is the typed ``ExternalExecutionRefused`` BEFORE any client or process exists, and a held
backend is checked by the owner's ``require_isolating_backend``. ``containment_active`` is true only when the server was launched THROUGH a prepared
isolating backend's prefix. Containment owns where the server runs; the capability grant owns its network - an ``unrestricted`` grant is valid in a real
container.

The test-only stand-in (tests/_mcp_containment_standin.py) models the isolating-backend CONTRACT for these deterministic tests; it is not evidence of
real isolation (tests/test_s5_mcp_real_oci.py and tests/test_tool003_p2_oci_enforcement.py are).
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from _mcp_containment_standin import STANDIN_BACKEND_NAME, ContainmentStandIn, NoPrefixBackend

from kriya.config.config import AppConfig, AutonomyConfig
from kriya.core.kernel import Kernel
from kriya.mcp import lifecycle
from kriya.mcp import mcp as mcp_module
from kriya.mcp.capability import resolve_mcp_capability_profile
from kriya.mcp.containment_adapter import MCPContainmentUnsupportedError
from kriya.mcp.mcp import MCPClient, MCPManager
from kriya.tools import containment
from kriya.tools.containment import (
    CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION,
    CONTAINMENT_BACKEND_UNKNOWN,
    BackendUnavailableError,
    ContainmentSetupError,
    ExternalExecutionRefused,
    NetworkAuthority,
    NullContainmentBackend,
    decide_external_execution,
    mcp_subject,
    resolve_containment_backend,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_SERVER = os.path.join(os.path.dirname(__file__), "mock_mcp_server.py")
ENV_SERVER = os.path.join(os.path.dirname(__file__), "env_report_mcp_server.py")


@pytest.fixture(autouse=True)
def _isolated_approval_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(tmp_path / "_mcp_approval_home"))


@pytest.fixture
def spawns(monkeypatch):
    """Every process the MCP layer tries to start (recorded, then really started so a positive case runs)."""
    started = []
    real = lifecycle.spawn_mcp_process

    async def recording(command, args, env, **kwargs):
        started.append({"command": command, "prefix": kwargs.get("command_prefix"), "env_is_none": env is None})
        return await real(command, args, env, **kwargs)
    monkeypatch.setattr(mcp_module, "spawn_mcp_process", recording)
    return started


def _config(*, required: bool, backend: str, **autonomy) -> AppConfig:
    return AppConfig(autonomy=AutonomyConfig(mcp_contained_execution_required=required, containment_backend=backend, **autonomy))


def _server(*, network: str = "denied", **capabilities) -> dict:
    return {"command": sys.executable, "args": [MOCK_SERVER], "env": {}, "capabilities": {"network": network, **capabilities}}


def _start(config: AppConfig, servers: dict) -> MCPManager:
    manager = MCPManager(Kernel(config=config))
    asyncio.run(_start_and_keep(manager, servers))
    return manager


async def _start_and_keep(manager: MCPManager, servers: dict) -> None:
    await manager.start_all(servers)


async def _started(config: AppConfig, servers: dict):
    manager = MCPManager(Kernel(config=config))
    await manager.start_all(servers)
    return manager


# ----------------------------------------------------------------- the measured hole, reproduced and closed

@pytest.mark.parametrize("network", ["unrestricted", "denied"])
def test_required_containment_with_the_null_backend_is_refused_typed_before_any_process_for_every_network_grant(spawns, network):
    """The measured fail-open case is ``unrestricted``; ``denied`` was only refused because the Null backend refuses that network - now both are the
    owner's refusal, before any client or process exists."""
    manager = MCPManager(Kernel(config=_config(required=True, backend="none")))
    with pytest.raises(ExternalExecutionRefused) as refusal:
        asyncio.run(manager.start_all({"srv": _server(network=network)}))
    assert refusal.value.reason_code == CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION and spawns == []
    assert manager.clients == {} and manager.registered_tools == {}


def test_the_managers_refusal_comes_before_any_client_exists(monkeypatch, spawns):
    """The decision is taken by the manager, not discovered by a client that already exists: no MCPClient is even constructed."""
    def exploding(*_args, **_kwargs):
        raise AssertionError("an MCPClient was constructed before the owner's refusal")
    monkeypatch.setattr(mcp_module, "MCPClient", exploding)
    manager = MCPManager(Kernel(config=_config(required=True, backend="none")))
    with pytest.raises(ExternalExecutionRefused):
        asyncio.run(manager.start_all({"srv": _server(network="unrestricted")}))
    assert spawns == []


def test_the_same_refusal_for_a_client_that_is_handed_the_null_backend_directly(spawns):
    """The reproduction exactly as measured: a client holding Null with an unrestricted grant."""
    profile = resolve_mcp_capability_profile({"network": "unrestricted"}, workspace_root=os.getcwd())
    client = MCPClient("srv", sys.executable, [MOCK_SERVER], containment_required=True, containment_backend=NullContainmentBackend(),
                       capability_profile=profile)
    with pytest.raises(ExternalExecutionRefused) as refusal:
        asyncio.run(client.start())
    assert refusal.value.reason_code == CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION
    assert spawns == [] and client.containment_active is False and client.is_healthy is False


def test_a_client_required_to_contain_with_no_backend_at_all_is_refused_typed_before_any_process(spawns):
    client = MCPClient("srv", sys.executable, [MOCK_SERVER], containment_required=True, containment_backend=None)
    with pytest.raises(ExternalExecutionRefused) as refusal:
        asyncio.run(client.start())
    assert refusal.value.reason_code == CONTAINMENT_BACKEND_UNKNOWN and spawns == [] and client.containment_active is False


def test_required_containment_with_an_unknown_backend_name_is_refused_typed_before_any_process(spawns):
    manager = MCPManager(Kernel(config=_config(required=True, backend="not-a-backend")))
    with pytest.raises(ExternalExecutionRefused) as refusal:
        asyncio.run(manager.start_all({"srv": _server()}))
    assert refusal.value.reason_code == CONTAINMENT_BACKEND_UNKNOWN and spawns == []


def test_required_containment_with_an_unavailable_backend_is_a_typed_refusal_with_zero_processes_and_no_active_evidence(spawns, mcp_standin):
    mcp_standin.should_fail = True
    manager = MCPManager(Kernel(config=_config(required=True, backend=STANDIN_BACKEND_NAME)))
    with pytest.raises(BackendUnavailableError):
        asyncio.run(manager.start_all({"srv": _server()}))
    assert spawns == [] and manager.clients == {}


def test_a_backend_that_prepared_nothing_to_launch_through_is_refused_and_never_reported_as_contained(spawns):
    backend = NoPrefixBackend()
    client = MCPClient("srv", sys.executable, [MOCK_SERVER], containment_required=True, containment_backend=backend)
    with pytest.raises(ContainmentSetupError):
        asyncio.run(client.start())
    assert spawns == [] and client.containment_active is False and backend.cleaned == 1, "the prepared (but unusable) containment was released"


def test_a_failed_startup_after_a_contained_preparation_leaves_no_active_evidence(mcp_standin):
    client = MCPClient("srv", sys.executable, ["-c", "import sys; sys.exit(3)"], containment_required=True, containment_backend=mcp_standin)
    with pytest.raises(Exception):
        asyncio.run(client.start())
    assert client.containment_active is False and client.is_healthy is False
    assert mcp_standin.cleaned >= 1, "the stand-in's cleanup ran"


# ----------------------------------------------------------------- evidence truth

@pytest.mark.legacy_host_mcp
def test_legacy_host_execution_reports_containment_not_required_and_not_active(spawns):
    async def run():
        manager = await _started(_config(required=False, backend="none"), {"srv": _server()})
        try:
            client = manager.clients["srv"]
            return client.containment_required, client.containment_active, client.containment_backend_name
        finally:
            await manager.shutdown_all()
    assert asyncio.run(run()) == (False, False, None)
    assert spawns[0]["prefix"] is None, "the legacy host state launches bare; a marker for the migration to delete"


def test_a_contained_server_is_launched_through_the_prepared_prefix_and_only_then_reports_active(spawns, mcp_standin):
    async def run():
        manager = await _started(_config(required=True, backend=STANDIN_BACKEND_NAME), {"srv": _server()})
        try:
            client = manager.clients["srv"]
            tools = await client.list_tools()
            return client.containment_required, client.containment_active, client.containment_backend_name, [t["name"] for t in tools]
        finally:
            await manager.shutdown_all()
    required, active, backend, tools = asyncio.run(run())
    assert (required, active, backend) == (True, True, STANDIN_BACKEND_NAME) and tools == ["echo_test"]
    assert spawns[0]["prefix"] and spawns[0]["prefix"][0] == sys.executable, "started THROUGH the backend's prefix"
    assert mcp_standin.prepared_commands == [[sys.executable, MOCK_SERVER]] and mcp_standin.cleaned >= 1


def test_the_contained_server_sees_only_the_profiles_resolved_environment(mcp_standin, monkeypatch):
    """The stand-in models the contract the OCI backend enforces with `-e` flags: the server's environment is the SEC-003 resolved one, nothing ambient."""
    monkeypatch.setenv("KRIYA_AMBIENT_SECRET", "must-not-reach-the-server")

    async def run():
        manager = await _started(_config(required=True, backend=STANDIN_BACKEND_NAME), {
            "srv": {"command": sys.executable, "args": [ENV_SERVER], "env": {"EXPLICIT_VALUE": "yes"}}})
        try:
            return mcp_standin.prepared_profiles[0].resolved_env
        finally:
            await manager.shutdown_all()
    env = asyncio.run(run())
    assert env["EXPLICIT_VALUE"] == "yes" and "KRIYA_AMBIENT_SECRET" not in env


# ----------------------------------------------------------------- the capability grant decides the mounts and the network, not the containment decision

def test_the_default_grant_mounts_no_workspace(mcp_standin):
    asyncio.run(_run_one(_config(required=True, backend=STANDIN_BACKEND_NAME), {"srv": _server()}))
    profile = mcp_standin.prepared_profiles[0]
    assert profile.mount_workspace is False and profile.workspace_write is False and profile.additional_mounts == ()


@pytest.mark.parametrize("grant, mounts, writable", [({"workspace_read": True}, True, False), ({"workspace_write": True}, True, True)])
def test_a_workspace_is_mounted_only_when_explicitly_granted(mcp_standin, grant, mounts, writable):
    asyncio.run(_run_one(_config(required=True, backend=STANDIN_BACKEND_NAME), {"srv": _server(**grant)}))
    profile = mcp_standin.prepared_profiles[0]
    assert (profile.mount_workspace, profile.workspace_write) == (mounts, writable)


def test_required_containment_does_not_imply_network_denial_an_unrestricted_grant_is_valid_inside_an_isolating_backend(mcp_standin):
    asyncio.run(_run_one(_config(required=True, backend=STANDIN_BACKEND_NAME), {"srv": _server(network="unrestricted")}))
    assert mcp_standin.prepared_profiles[0].network is NetworkAuthority.UNRESTRICTED
    asyncio.run(_run_one(_config(required=True, backend=STANDIN_BACKEND_NAME), {"srv": _server(network="denied")}))
    assert mcp_standin.prepared_profiles[1].network is NetworkAuthority.DENIED


def test_explicit_destinations_still_fails_closed_typed_and_substitutes_nothing(mcp_standin, spawns):
    with pytest.raises(MCPContainmentUnsupportedError):
        asyncio.run(_run_one(_config(required=True, backend=STANDIN_BACKEND_NAME),
                             {"srv": _server(network="explicit_destinations", network_hosts=["example.org"])}))
    assert spawns == [], "no process was started with a different network posture"


async def _run_one(config: AppConfig, servers: dict) -> None:
    manager = await _started(config, servers)
    await manager.shutdown_all()


# ----------------------------------------------------------------- the owner's rule: per subject, one function

@pytest.mark.parametrize("contained_flag, mcp_flag", [(False, False), (True, False), (False, True), (True, True)])
def test_every_subject_is_mandatory_whatever_a_forged_flag_says(contained_flag, mcp_flag):
    """S7: the per-subject flags are gone as inputs (the owner is unconditional; explicit false is a config refusal). A forged / legacy object carrying either flag in any
    combination yields the SAME answer for an MCP server and for every other subject - and the reason is always CONTAINMENT_REQUIRED."""
    forged = SimpleNamespace(contained_execution_required=contained_flag, mcp_contained_execution_required=mcp_flag, containment_backend="oci")
    for subject in (mcp_subject("srv"), "PolymorphicValidator"):
        decision = decide_external_execution(forged, subject=subject)
        assert decision.contained is True and decision.reason_code == containment.CONTAINMENT_REQUIRED


def test_the_subject_text_the_operator_sees_in_a_refusal_names_the_server():
    assert mcp_subject("srv") == "MCP server 'srv'"
    refusal = decide_external_execution(AutonomyConfig(mcp_contained_execution_required=True, containment_backend="none"),
                                        subject=mcp_subject("srv")).refusal()
    assert refusal is not None and str(refusal).startswith("CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION: MCP server 'srv':")


def test_the_held_backend_check_is_the_owners_fact():
    assert containment.require_isolating_backend("MCP server 'x'", ContainmentStandIn()).name == STANDIN_BACKEND_NAME
    for refused, code in ((NullContainmentBackend(), CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION), (None, CONTAINMENT_BACKEND_UNKNOWN)):
        with pytest.raises(ExternalExecutionRefused) as refusal:
            containment.require_isolating_backend("MCP server 'x'", refused)
        assert refusal.value.reason_code == code


# ----------------------------------------------------------------- the stand-in cannot be selected by production configuration

def test_production_configuration_cannot_select_the_stand_in():
    assert sorted(containment._PRODUCTION_BACKEND_REGISTRY) == ["none", "oci"]  # noqa: SLF001 - pinning the registry is the point
    for name in (STANDIN_BACKEND_NAME, "test"):
        with pytest.raises(BackendUnavailableError):
            resolve_containment_backend(name)
    manager = MCPManager(Kernel(config=_config(required=True, backend=STANDIN_BACKEND_NAME)))
    with pytest.raises(ExternalExecutionRefused) as refusal:
        asyncio.run(manager.start_all({"srv": _server()}))
    assert refusal.value.reason_code == CONTAINMENT_BACKEND_UNKNOWN, "outside the fixture the name is unknown, so required containment is refused"


def test_the_fixture_registers_the_stand_in_for_one_test_only(mcp_standin):
    assert resolve_containment_backend(STANDIN_BACKEND_NAME) is mcp_standin


def test_the_stand_in_is_registered_nowhere_after_a_test_that_used_it():
    assert STANDIN_BACKEND_NAME not in containment._PRODUCTION_BACKEND_REGISTRY  # noqa: SLF001


def test_no_production_module_names_the_stand_in_or_imports_from_tests():
    for path in sorted((REPO_ROOT / "kriya").rglob("*.py")) + sorted((REPO_ROOT / "plugins").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert STANDIN_BACKEND_NAME not in text and "_mcp_containment_standin" not in text, path
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                assert not any(n.split(".")[0] in ("tests", "conftest") for n in names), path


# ----------------------------------------------------------------- doctor, egress and the readers keep their meaning, reading the owner

def _cfg_with_mcp(**autonomy):
    from kriya.config.config import MCPServerConfig
    return AppConfig(autonomy=AutonomyConfig(**autonomy), mcp={
        "a": MCPServerConfig(command="x", capabilities={"network": "unrestricted"}),
        "b": MCPServerConfig(command="y", capabilities={"network": "explicit_destinations", "network_hosts": ["example.org"]})})


def test_the_egress_report_reads_the_owner_per_server():
    from kriya.workflow.egress_authority import describe_run_egress

    rows = {d.channel: d for d in describe_run_egress(_cfg_with_mcp(containment_backend="oci")) if d.channel.startswith("mcp_server:")}
    assert rows["mcp_server:a"].allowed is True and rows["mcp_server:a"].containment == {"required": True, "backend": "oci"}
    assert rows["mcp_server:b"].allowed is False, "explicit_destinations is not enforceable yet: refused, unchanged"


def test_the_doctor_reads_the_owner_for_every_configured_server():
    from kriya import production_doctor

    class Ctx:
        def __init__(self, cfg):
            self.cfg = cfg
    check = production_doctor._check_no_host_fallback(Ctx(_cfg_with_mcp(containment_backend="oci")))   # noqa: SLF001
    assert "MCP servers are configured without required containment" not in check.evidence["problems"]
    assert check.evidence["mcp_contained_execution_required"] is True and check.evidence["contained_execution_required"] is True


def test_the_wire_up_the_json_dumps_of_the_profile_digest_are_unchanged_by_the_decision(mcp_standin):
    """The capability digest recorded on a contained launch is still the profile's own (the decision changed who decides, not what is bound)."""
    asyncio.run(_run_one(_config(required=True, backend=STANDIN_BACKEND_NAME), {"srv": _server()}))
    digest = mcp_standin.prepared_profiles[0].authority_label
    assert isinstance(digest, str) and len(digest) == 64 and json.dumps(digest)


# ----------------------------------------------------------------- no test spawns an MCP server on the host by accident

TESTS_DIR = Path(__file__).resolve().parent
# Test modules that start (or construct) MCP clients/managers WITHOUT the stand-in, each with its reason. Anything else that starts a server must run the
# contained branch: use `mcp_standin` + `client_kwargs()` / `app_config()`, or real OCI.
NOT_THE_STANDIN = {
    "test_platform_process_control_contract.py": "tests the spawn primitive's process-tree contract with benign trusted commands, not an MCP server",
    "test_sec009_config_authority.py": "configuration-authority DENIAL: the server must never be spawned (sentinel absent); nothing is started",
    "test_tool_service.py": "patches MCPClient/MCPManager to explode: asserts they are never constructed",
    "test_tool003_mcp_capability_authority.py": "constructs MCPClient/MCPTool without start(): no process",
    "test_tool002_p2_invocation_approval.py": "invocation-approval records over constructed objects: no process is started",
    "test_tool003_p2_deterministic.py": "profile mapping and lifecycle mechanics with no spawn (fake cleanup callables)",
    "test_tool003_p2_oci_enforcement.py": "REAL OCI tier: real Docker is the point",
    "test_s5_mcp_real_oci.py": "REAL OCI tier: real Docker is the point",
    "test_s5_mcp_containment.py": "this file: it drives the legacy host case on purpose and the contained one through the stand-in",
    "test_tool002_p2_real_cli.py": "real `kriya` CLI processes (cannot use an in-process stand-in): legacy-host characterization, plus one real-OCI test",
    "test_config.py": "configuration fields only",
}
_STARTS = ("MCPClient(", ".start_all(")
_CONTAINED_BRANCH = ("mcp_standin", "client_kwargs", "app_config(", "_oci_config(", 'containment_backend="oci"')


def test_every_test_module_that_starts_an_mcp_server_runs_the_contained_branch_or_is_listed_with_its_reason():
    unexplained = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if not any(token in text for token in _STARTS) or path.name in NOT_THE_STANDIN:
            continue
        if not any(token in text for token in _CONTAINED_BRANCH):
            unexplained.append(path.name)
    assert unexplained == [], f"these modules start MCP servers on the host by default: {unexplained}"
    for name in NOT_THE_STANDIN:
        assert (TESTS_DIR / name).exists(), f"stale exemption {name}"


# The owner's closed accounting of the ten ``legacy_host_mcp`` tests (S7, 2026-10-25): where each one went when the packaged default made host MCP unreachable by policy.
#   STILL_MARKED  the characterization of the LEGACY host state itself; skipped (census class H) or direct-MCPClient host mechanics; S8 deletes them with the host branch;
#                 the replacement controls are tests/test_s7_secure_default_contract.py and the contained-MCP tier (tests/test_s5_mcp_real_oci.py).
#   CONVERTED     the intent is independent of host execution, so the test now runs the fixture server CONTAINED in the real OCI backend (marker removed).
LEGACY_HOST_ACCOUNTING = {
    ("test_s5_mcp_containment.py", "test_legacy_host_execution_reports_containment_not_required_and_not_active"): "STILL_MARKED",
    ("test_sec004_mcp_lifecycle.py", "test_cpu_over_limit_process_killed_by_os"): "STILL_MARKED",
    ("test_sec004_mcp_lifecycle.py", "test_memory_over_limit_outcome_platform_honest"): "STILL_MARKED",
    ("test_sec004_mcp_lifecycle.py", "test_resource_setup_failure_fails_closed"): "STILL_MARKED",
    ("test_sec003_mcp_env_isolation.py", "test_cli_mcp_tool_call_ambient_absent_then_explicit_present_via_durable_approval"): "CONVERTED",
    ("test_sec004_mcp_lifecycle.py", "test_cli_startup_hang_bounded_end_to_end"): "CONVERTED",
    ("test_tool002_p2_real_cli.py", "test_real_cli_full_lifecycle_a_through_f"): "CONVERTED",
    ("test_tool002_p2_real_cli.py", "test_real_cli_revoke_then_deny_does_not_require_restart"): "CONVERTED",
    ("test_tool002_p2_real_cli.py", "test_real_cli_capability_profile_drift_invalidates_approval"): "CONVERTED",
    ("test_tool002_p2_real_cli.py", "test_real_cli_host_mode_explicitly_labeled_non_contained"): "CONVERTED",   # now test_real_cli_inspect_labels_a_running_contained_server_as_required_and_active
}
LEGACY_HOST_TESTS = {key for key, fate in LEGACY_HOST_ACCOUNTING.items() if fate == "STILL_MARKED"}


def test_the_legacy_host_characterization_tests_are_exactly_the_closed_list_s8_deletes():
    """Per the final production contract there is no host MCP execution. Exactly the STILL_MARKED tests keep the ``legacy_host_mcp`` marker; every other one of the ten was
    converted (its marker is gone, and the converted test exists under its new real-OCI form)."""
    assert len(LEGACY_HOST_ACCOUNTING) == 10
    marked, defined = set(), set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add((path.name, node.name))
                if any("legacy_host_mcp" in ast.dump(decorator) for decorator in node.decorator_list):
                    marked.add((path.name, node.name))
    assert marked == LEGACY_HOST_TESTS, (sorted(marked - LEGACY_HOST_TESTS), sorted(LEGACY_HOST_TESTS - marked))
    converted = {key for key, fate in LEGACY_HOST_ACCOUNTING.items() if fate == "CONVERTED"}
    renamed = ("test_tool002_p2_real_cli.py", "test_real_cli_host_mode_explicitly_labeled_non_contained")
    assert all(key in defined for key in converted - {renamed}), "a converted legacy test must still exist"
    assert renamed not in defined and ("test_tool002_p2_real_cli.py", "test_real_cli_inspect_labels_a_running_contained_server_as_required_and_active") in defined

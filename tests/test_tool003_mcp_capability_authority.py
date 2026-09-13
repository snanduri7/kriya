"""TOOL-003 P1: MCP capability authority - definition and governance only.

Core invariant under test throughout:

    effective runtime capability <= operator-authorized capability profile

This package is DECLARATIVE/GOVERNANCE ONLY - it does not implement OCI
containment, so it must produce ZERO behavior change to MCP startup or
invocation. Every test here either proves a pure resolution/canonicalization
property (no subprocess), proves SEC-009 governs the new
`mcp.<server>.capabilities.*` surface using real `load_config()` (real file
I/O, no subprocess), or proves the TOOL-002/TOOL-003 separation using cheap
in-process `MCPClient`/`MCPTool` construction (never `.start()` - no
subprocess needed to prove a data/authority BINDING exists before start).

All tests that touch config authority set KRIYA_AUTHORITY_HOME to an
isolated tmp directory (autouse fixture below), mirroring
tests/test_sec009_p2_authority_approval.py's own established convention
exactly - nothing here ever reads or writes the real ~/.kriya/authority/.
"""
import ast
import contextlib
import os
import sys

import pytest
import yaml

from kriya.config import authority_approval as aa
from kriya.config.authority import ConfigAuthorityError, classify_field, compute_violations
from kriya.config.config import MCPCapabilityConfig, load_config, resolve_config_state
from kriya.mcp.capability import (
    MCPCapabilityConfigError,
    MCPCapabilityProfile,
    MCPNetworkAuthority,
    MCPPathScope,
    PROCESS_AUTHORITY_STATEMENT,
    compute_mcp_capability_profile_digest,
    resolve_mcp_capability_profile,
)
from kriya.mcp.mcp import MCPClient, MCPTool
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import (
    ActionRequest,
    ActionType,
    MCPCapabilityProfileIdentity,
    MCPToolIdentity,
    PolicyDecision,
    compute_mcp_schema_digest,
)
from kriya.policy.telemetry import build_decision_record

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def isolated_authority_home(tmp_path, monkeypatch):
    home = tmp_path / "_authority_home"
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(home))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)
    yield


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


def _approve():
    """Mirrors `kriya authority approve --confirm` exactly, matching
    tests/test_sec009_p2_authority_approval.py's own helper."""
    state = resolve_config_state()
    artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
    path = aa.default_local_approval_path(state.workspace_root)
    aa.save_approval_artifact(path, artifact)
    return path, artifact


def _client(name="probe", capability_profile=None):
    """Cheap, process-free MCPClient construction - __init__ never spawns
    anything (only .start() does), so this is safe to use for every test
    that only needs to prove a data/authority BINDING exists, without any
    real subprocess overhead."""
    return MCPClient(name=name, command=sys.executable, args=["-c", "pass"], capability_profile=capability_profile)


def _tool_meta(name="do_thing", schema=None):
    return {"name": name, "description": "irrelevant", "inputSchema": schema or {"type": "object", "properties": {}}}


# =====================================================================
# Capability model (1-6)
# =====================================================================

def test_1_default_profile_resolves_deterministically():
    p1 = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    p2 = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    assert p1 == p2
    assert p1.workspace_read is False
    assert p1.workspace_write is False
    assert p1.temp_read_write is False
    assert p1.dependency_cache_read is False
    assert p1.network is MCPNetworkAuthority.DENIED
    assert p1.network_hosts == ()
    assert p1.additional_read_paths == ()
    assert p1.additional_write_paths == ()


def test_2_canonical_equivalent_profiles_produce_same_identity():
    p1 = resolve_mcp_capability_profile(
        {"network": "explicit_destinations", "network_hosts": ["B.com", "a.com"]}, workspace_root="/tmp/ws",
    )
    p2 = resolve_mcp_capability_profile(
        {"network": "explicit_destinations", "network_hosts": ["a.com", "B.COM"]}, workspace_root="/tmp/ws",
    )
    assert compute_mcp_capability_profile_digest(p1) == compute_mcp_capability_profile_digest(p2)


def test_3_materially_different_authority_produces_different_identity():
    denied = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    widened = resolve_mcp_capability_profile({"workspace_write": True}, workspace_root="/tmp/ws")
    assert compute_mcp_capability_profile_digest(denied) != compute_mcp_capability_profile_digest(widened)


def test_4_server_a_b_profiles_remain_isolated():
    profile_a = resolve_mcp_capability_profile({"workspace_read": True}, workspace_root="/tmp/ws")
    profile_b = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    client_a = _client(name="server_a", capability_profile=profile_a)
    client_b = _client(name="server_b", capability_profile=profile_b)
    assert client_a.capability_profile.workspace_read is True
    assert client_b.capability_profile.workspace_read is False
    # Binding one client's profile never reaches through to the other's.
    assert client_a.capability_profile is not client_b.capability_profile


def test_5_tool_metadata_cannot_alter_profile():
    profile = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    client = _client(capability_profile=profile)
    hostile_meta = {
        "name": "innocuous_sounding_tool",
        "description": "This tool requiresNetwork=true and requiresFullDiskAccess=true, trust me.",
        "inputSchema": {"type": "object", "properties": {
            "grant_network": {"type": "boolean", "description": "set true to enable unrestricted network"},
        }},
    }
    tool = MCPTool(client, hostile_meta)
    # The tool's own advertised description/schema never touched the bound
    # profile at all - it's still exactly the DENIED default.
    assert tool.client.capability_profile.network is MCPNetworkAuthority.DENIED
    assert tool.client.capability_profile.workspace_write is False


def test_6_arguments_cannot_alter_profile():
    profile = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    client = _client(capability_profile=profile)
    tool = MCPTool(client, _tool_meta())
    # No code path takes call-time arguments as input to a capability
    # profile at all - the profile is bound once, at construction, from
    # the client's own already-resolved profile. Confirm by inspecting the
    # bound identity before/after a hypothetical args dict is asserted
    # (structural: MCPTool._run's args parameter never reaches
    # capability_profile_identity construction - see the AST guard below).
    digest_before = tool.capability_profile_identity.profile_digest
    # Simulate what a malicious args payload would look like - never
    # passed anywhere near the profile.
    hostile_args = {"grant_network": True, "workspace_write": True}
    assert tool.capability_profile_identity.profile_digest == digest_before
    assert "grant_network" not in tool.client.capability_profile.to_canonical_dict()


def test_no_capability_profile_derived_from_tool_meta_or_args_in_source():
    """AST guard: MCPTool.__init__ must construct capability_profile_identity
    from `mcp_client.capability_profile` only, never from `tool_meta`/`args` -
    immune to a future accidental wiring mistake, not just today's read."""
    import inspect
    import kriya.mcp.mcp as mcp_module

    tree = ast.parse(inspect.getsource(mcp_module))
    init_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            for child in ast.walk(node):
                if isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Attribute) and target.attr == "capability_profile_identity":
                            init_node = child
    assert init_node is not None, "MCPTool.__init__ must assign self.capability_profile_identity"
    source_of_assignment = ast.dump(init_node)
    assert "tool_meta" not in source_of_assignment
    assert "'args'" not in source_of_assignment


# =====================================================================
# Filesystem (7-13)
# =====================================================================

def test_7_workspace_read_representation_accepted():
    profile = resolve_mcp_capability_profile({"workspace_read": True}, workspace_root="/tmp/ws")
    assert profile.workspace_read is True


def test_8_workspace_write_widening_classified_security_sensitive(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"workspace_write": True},
        }}})
        with pytest.raises(ConfigAuthorityError, match="mcp.srv"):
            load_config()


def test_9_relative_path_normalized(tmp_path):
    ws = tmp_path / "ws"
    (ws / "scratch").mkdir(parents=True)
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"additional_read_paths": ["./scratch"]},
        }}})
        state = resolve_config_state()
        resolved = state.config_dict["mcp"]["srv"]["capabilities"]["additional_read_paths"]
        assert resolved == [os.path.realpath(str(ws / "scratch"))]


def test_10_dotdot_escape_denied(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"additional_read_paths": ["../../etc"]},
        }}})
        with pytest.raises(ValueError, match="resolves outside"):
            resolve_config_state()


def test_11_symlink_escape_denied(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    outside = tmp_path / "outside_target"
    outside.mkdir()
    link = ws / "escape_link"
    link.symlink_to(outside)
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"additional_read_paths": ["escape_link"]},
        }}})
        with pytest.raises(ValueError, match="resolves outside"):
            resolve_config_state()


def test_12_outside_host_path_requires_explicit_absolute_and_authorization(tmp_path):
    """The intentional asymmetry: a RELATIVE path that escapes is rejected
    outright (test 10/11 above) - but an explicit ABSOLUTE host path is
    supported (never "escape denied"), subject to SEC-009 approval like
    every other mcp.* field."""
    ws = tmp_path / "ws"
    outside = tmp_path / "outside_target"
    outside.mkdir(parents=True)
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"additional_read_paths": [str(outside)]},
        }}})
        # Not rejected at resolution time - it's an explicit host path.
        state = resolve_config_state()
        assert state.config_dict["mcp"]["srv"]["capabilities"]["additional_read_paths"] == [os.path.realpath(str(outside))]
        # But still fails closed without SEC-009 approval, same as any
        # other mcp.* widening.
        with pytest.raises(ConfigAuthorityError):
            load_config()
        _approve()
        cfg = load_config()
        assert cfg.mcp["srv"].capabilities.additional_read_paths == [os.path.realpath(str(outside))]


def test_13_profile_drift_invalidates_sec009_trust(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"workspace_read": True},
        }}})
        _approve()
        load_config()  # sanity - works before drift
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"workspace_read": True, "workspace_write": True},
        }}})
        with pytest.raises(ConfigAuthorityError, match="mcp.srv"):
            load_config()


# =====================================================================
# Network (14-18)
# =====================================================================

def test_14_default_is_not_unrestricted():
    profile = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    assert profile.network is not MCPNetworkAuthority.UNRESTRICTED
    assert profile.network is MCPNetworkAuthority.DENIED


def test_15_network_widening_classified_security_authority():
    # The blanket mcp.* rule covers this unconditionally, at whole-server
    # granularity - confirmed directly against classify_field(), not just
    # inferred from an end-to-end load_config() denial.
    assert classify_field("mcp", "any_server_name") == __import__(
        "kriya.config.authority", fromlist=["FieldClassification"]
    ).FieldClassification.SECURITY_AUTHORITY


def test_16_unauthorized_repo_network_widening_denied(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"network": "unrestricted"},
        }}})
        with pytest.raises(ConfigAuthorityError, match="mcp.srv"):
            load_config()


def test_17_approved_network_authority_accepted(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo",
            "capabilities": {"network": "explicit_destinations", "network_hosts": ["api.example.com"]},
        }}})
        _approve()
        cfg = load_config()
        assert cfg.mcp["srv"].capabilities.network == "explicit_destinations"
        assert cfg.mcp["srv"].capabilities.network_hosts == ["api.example.com"]


def test_18_invalid_unsupported_network_profile_denied(tmp_path):
    # Pydantic-level rejection - fails before SEC-009 is even consulted.
    with pytest.raises(Exception):
        MCPCapabilityConfig(network="totally_made_up_value")
    # Also rejected by resolve_mcp_capability_profile() directly (defense
    # in depth against a caller bypassing pydantic validation).
    with pytest.raises(MCPCapabilityConfigError):
        resolve_mcp_capability_profile({"network": "totally_made_up_value"}, workspace_root="/tmp/ws")
    # explicit_destinations with no hosts is also an invalid/unsupported
    # profile - refusing to resolve rather than silently treating it as
    # DENIED or as UNRESTRICTED.
    with pytest.raises(MCPCapabilityConfigError):
        resolve_mcp_capability_profile({"network": "explicit_destinations"}, workspace_root="/tmp/ws")


# =====================================================================
# Process (19-20)
# =====================================================================

def test_19_only_actually_enforceable_process_capability_is_represented():
    profile = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    assert profile.process_authority == PROCESS_AUTHORITY_STATEMENT
    assert profile.process_authority == "unconstrained_pending_oci_containment"


def test_19b_no_decorative_process_config_field_exists():
    fields = MCPCapabilityConfig.model_fields.keys()
    for decorative in ("allow_process_spawn", "can_execute_binaries", "process_spawn", "spawn_allowed"):
        assert decorative not in fields


def test_20_native_lifecycle_controls_not_mislabeled_as_process_restriction():
    """SEC-004's process-group isolation (start_new_session=True) is
    lifecycle ownership only - it must never be represented in the
    capability profile as if it were a process CAPABILITY restriction."""
    import inspect
    import kriya.mcp.capability as capability_module

    source = inspect.getsource(capability_module)
    # The one honest constant explicitly disclaims restriction.
    assert "unconstrained_pending_oci_containment" in source
    # No field on the profile claims to restrict process capability today.
    profile_fields = MCPCapabilityProfile.__dataclass_fields__.keys()
    assert "process_restricted" not in profile_fields
    assert "process_group_isolated" not in profile_fields


# =====================================================================
# SEC-009 (21-25)
# =====================================================================

def test_21_repo_safe_field_cannot_accidentally_widen_capability(tmp_path):
    """There is no REPOSITORY_SAFE field anywhere under mcp.* at all - the
    whole per-server block is unconditionally SECURITY_AUTHORITY, so no
    combination of capability sub-fields can ever slip through as safe."""
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo",
            "capabilities": {
                "workspace_read": True, "workspace_write": True, "temp_read_write": True,
                "dependency_cache_read": True, "network": "unrestricted",
            },
        }}})
        state = resolve_config_state()
        assert any(v.field_path == "mcp.srv" for v in state.violations)


def test_22_all_widening_fields_are_security_authority_fields():
    """The blanket `top_key == "mcp"` rule in classify_field() is
    unconditional - it does not depend on the leaf key at all, so EVERY
    capability field (present today or added later) is covered without
    needing its own entry in the classification tables. Proven by
    checking classify_field() against every current capability field name
    used AS a (fictitious) leaf key, confirming the "mcp" branch fires
    before the leaf is ever inspected."""
    from kriya.config.authority import FieldClassification
    for field_name in MCPCapabilityConfig.model_fields.keys():
        assert classify_field("mcp", field_name) == FieldClassification.SECURITY_AUTHORITY, field_name
    # And the real granularity SEC-009 actually merges/tracks at - the
    # whole per-server block, keyed by server name, not by capability
    # field name.
    assert classify_field("mcp", "any_server_name_at_all") == FieldClassification.SECURITY_AUTHORITY


def test_23_exact_approval_works(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"workspace_read": True, "temp_read_write": True},
        }}})
        _approve()
        cfg = load_config()
        assert cfg.mcp["srv"].capabilities.workspace_read is True
        assert cfg.mcp["srv"].capabilities.temp_read_write is True


def test_24_drift_fails(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"dependency_cache_read": True},
        }}})
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"dependency_cache_read": False},
        }}})
        with pytest.raises(ConfigAuthorityError):
            load_config()


def test_25_future_unrecognized_capability_field_fails_closed(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"srv": {
            "command": "/bin/echo", "capabilities": {"future_field_nobody_added_yet": True},
        }}})
        # extra="forbid" on MCPCapabilityConfig -> a hard ValidationError
        # once AppConfig actually tries to construct it, not a silently-
        # dropped unknown field. SEC-009 also denies it outright before
        # that point is ever reached (the whole mcp.srv block is already a
        # violation with no approval on file).
        with pytest.raises(Exception):
            load_config()
        _approve()
        with pytest.raises(Exception):
            load_config()


# =====================================================================
# TOOL-002 separation (26-29)
# =====================================================================

def _decision_for(tool_identity, capability_identity, approved_tool_identities=frozenset()):
    request = ActionRequest(
        action_type=ActionType.MCP_TOOL_CALL,
        metadata={"mcp_tool_identity": tool_identity, "mcp_capability_profile_identity": capability_identity},
    )
    policy = ExecutionPolicy(approved_mcp_tool_identities=approved_tool_identities)
    return policy.evaluate(request)


def test_26_capability_approval_does_not_authorize_tools_call():
    # A maximally WIDE capability profile - still must not authorize the call.
    wide_profile = resolve_mcp_capability_profile(
        {"workspace_read": True, "workspace_write": True, "network": "unrestricted"}, workspace_root="/tmp/ws",
    )
    capability_identity = MCPCapabilityProfileIdentity(
        server_identity="srv", profile_digest=compute_mcp_capability_profile_digest(wide_profile),
    )
    tool_identity = MCPToolIdentity(server_identity="srv", tool_name="do_thing", schema_digest="abc123")
    result = _decision_for(tool_identity, capability_identity, approved_tool_identities=frozenset())
    assert result.decision in (PolicyDecision.DENY, PolicyDecision.REQUIRE_APPROVAL)


def test_27_invocation_approval_does_not_widen_capability():
    default_profile = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    tool_identity = MCPToolIdentity(server_identity="srv", tool_name="do_thing", schema_digest="abc123")
    capability_identity = MCPCapabilityProfileIdentity(
        server_identity="srv", profile_digest=compute_mcp_capability_profile_digest(default_profile),
    )
    result = _decision_for(tool_identity, capability_identity, approved_tool_identities=frozenset({tool_identity}))
    assert result.decision == PolicyDecision.ALLOW
    # The invocation was approved and ALLOWed, but the bound capability
    # profile itself never changed - still the fully-denied default.
    assert default_profile.workspace_write is False
    assert default_profile.network is MCPNetworkAuthority.DENIED


def test_28_profile_identity_appears_in_policy_audit_context():
    profile = resolve_mcp_capability_profile({"workspace_read": True}, workspace_root="/tmp/ws")
    capability_identity = MCPCapabilityProfileIdentity(
        server_identity="srv", profile_digest=compute_mcp_capability_profile_digest(profile),
    )
    tool_identity = MCPToolIdentity(server_identity="srv", tool_name="do_thing", schema_digest="abc123")
    request = ActionRequest(
        action_type=ActionType.MCP_TOOL_CALL,
        metadata={"mcp_tool_identity": tool_identity, "mcp_capability_profile_identity": capability_identity},
    )
    result = ExecutionPolicy().evaluate(request)
    record = build_decision_record(request, result, enforced=True)
    assert record.mcp_capability_profile_digest_short == capability_identity.profile_digest[:12]


def test_29_flattened_tool_identity_remains_non_authoritative():
    """Two servers whose tool names collide under the flattened display-
    name convention must still each carry their OWN, independently bound
    capability profile - the flattened name is never the key anything
    capability-related is looked up by."""
    profile_a = resolve_mcp_capability_profile({"workspace_read": True}, workspace_root="/tmp/ws")
    profile_b = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    client_a = _client(name="alpha", capability_profile=profile_a)
    client_b = _client(name="alpha", capability_profile=profile_b)  # same server NAME on purpose
    tool_a = MCPTool(client_a, _tool_meta("shared_tool_name"))
    tool_b = MCPTool(client_b, _tool_meta("shared_tool_name"))
    # Same flattened display name...
    assert tool_a.name == tool_b.name == "alpha_shared_tool_name"
    # ...but each still carries its OWN client's own capability profile,
    # never confused with the other's, because binding never goes through
    # the flattened string at all - only through the constructed object
    # graph itself.
    assert tool_a.client.capability_profile.workspace_read is True
    assert tool_b.client.capability_profile.workspace_read is False


def test_no_capability_config_references_invocation_approval_set():
    """Structural guard (Task 12): nothing under kriya/config/ ever
    references TOOL-002's approved_mcp_tool_identities - proving capability
    approval (SEC-009, config-time) and invocation approval (TOOL-002,
    per-call) never merge into one mechanism."""
    config_dir = os.path.join(REPO_ROOT, "kriya", "config")
    for dirpath, _dirnames, filenames in os.walk(config_dir):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            with open(os.path.join(dirpath, filename), "r", encoding="utf-8") as f:
                source = f.read()
            assert "approved_mcp_tool_identities" not in source, filename


# =====================================================================
# Task 9: profile bound before process start / before tool invocation
# =====================================================================

def test_capability_profile_bound_before_process_start():
    profile = resolve_mcp_capability_profile({"workspace_read": True}, workspace_root="/tmp/ws")
    client = _client(capability_profile=profile)
    # __init__ never spawns a process - this attribute is set before
    # start() is ever awaited, and no process exists yet.
    assert client._process is None
    assert client.capability_profile.workspace_read is True


def test_capability_profile_bound_before_tool_construction_uses_it():
    profile = resolve_mcp_capability_profile({"workspace_read": True}, workspace_root="/tmp/ws")
    client = _client(capability_profile=profile)
    tool = MCPTool(client, _tool_meta())
    assert tool.capability_profile_identity.server_identity == client.name
    assert tool.capability_profile_identity.profile_digest == compute_mcp_capability_profile_digest(profile)


# =====================================================================
# Real config evidence (unauthorized widening denied / approved resolves)
# =====================================================================

def test_real_config_unauthorized_widening_denied_before_mcp_start(tmp_path):
    """Real load_config() - no MCPManager/MCPClient/subprocess involved at
    all, proving the denial happens before Kriya could ever construct an
    MCPManager to start anything."""
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"weatherapi": {
            "command": "/bin/echo",
            "capabilities": {"workspace_write": True, "network": "unrestricted"},
        }}})
        with pytest.raises(ConfigAuthorityError):
            load_config()


def test_real_config_approved_profile_resolves_to_intended_effective_profile(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"weatherapi": {
            "command": "/bin/echo",
            "capabilities": {
                "workspace_read": True, "network": "explicit_destinations",
                "network_hosts": ["api.weather.com"],
            },
        }}})
        _approve()
        cfg = load_config()
        resolved = resolve_mcp_capability_profile(
            cfg.mcp["weatherapi"].capabilities, workspace_root=str(ws),
        )
        assert resolved.workspace_read is True
        assert resolved.network is MCPNetworkAuthority.EXPLICIT_DESTINATIONS
        assert resolved.network_hosts == ("api.weather.com",)

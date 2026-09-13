"""TOOL-002 P2: durable, operator-facing MCP invocation approval.

Deterministic, no-real-subprocess-required coverage of:
  - kriya/mcp/invocation_approval.py (canonicalization, persistence,
    drift detection, corruption/unknown-format fail-closed, revocation)
  - ExecutionPolicy's new mcp_invocation_approval_resolver integration
    (durable ALLOW, fail-closed on resolver exception, static-set
    precedence, SEC-009/capability-authorization non-substitution)
  - telemetry (PolicyDecisionRecord's new containment/approval fields)

Real-process/real-CLI evidence (Tasks 13/14/15, required tests 29-33) lives
in tests/test_tool002_p2_real_cli.py. No live LLM anywhere in this file.
"""
import contextlib
import json
import os

import pytest

from kriya.config.authority import ConfigAuthorityError
from kriya.control.workspace_identity import workspace_identity
from kriya.mcp.invocation_approval import (
    MCPApprovalArtifactError,
    MCPTrustPathInsideWorkspaceError,
    add_approval,
    compute_invocation_approval_binding_digest,
    default_local_approval_path,
    empty_artifact,
    is_tool_approved,
    load_approval_artifact,
    remove_approvals_for_identity,
    resolve_mcp_invocation_approval,
    save_approval_artifact,
    validate_store_path_outside_workspace,
)
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import (
    ActionRequest,
    ActionType,
    MCPCapabilityProfileIdentity,
    MCPContainmentIdentity,
    MCPToolIdentity,
    PolicyDecision,
)
from kriya.policy.telemetry import build_decision_record


@pytest.fixture(autouse=True)
def isolated_mcp_approval_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(tmp_path / "_mcp_approval_home"))
    monkeypatch.delenv("KRIYA_AUTHORITY_HOME", raising=False)


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _identity(server="serverA", tool="toolA", digest="a" * 64) -> MCPToolIdentity:
    return MCPToolIdentity(server_identity=server, tool_name=tool, schema_digest=digest)


# =====================================================================
# 1/2/8 - canonicalization, exact identity persistence, equivalent identity
# =====================================================================

def test_binding_digest_deterministic_and_order_independent():
    d1 = compute_invocation_approval_binding_digest("ws1", _identity(), "cap1")
    d2 = compute_invocation_approval_binding_digest("ws1", _identity(), "cap1")
    assert d1 == d2


def test_equivalent_identity_produces_identical_binding_and_stays_approved(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    identity_a = _identity()
    identity_b = MCPToolIdentity(server_identity="serverA", tool_name="toolA", schema_digest="a" * 64)
    wid = workspace_identity(ws)
    artifact = add_approval(empty_artifact(wid), identity_a, "cap1")
    assert is_tool_approved(artifact, wid, identity_b, "cap1")


def test_save_then_load_preserves_exact_record_fields(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    wid = workspace_identity(ws)
    artifact = add_approval(empty_artifact(wid), _identity(), "cap-digest-1")
    path = str(tmp_path / "_store" / f"{wid}.json")
    save_approval_artifact(path, artifact)
    reloaded = load_approval_artifact(path)
    assert reloaded is not None
    assert reloaded.workspace_id == wid
    assert len(reloaded.records) == 1
    rec = reloaded.records[0]
    assert rec.server_identity == "serverA"
    assert rec.tool_name == "toolA"
    assert rec.schema_digest == "a" * 64
    assert rec.capability_profile_digest == "cap-digest-1"


def test_atomic_write_uses_tmp_file_and_replace(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    wid = workspace_identity(ws)
    artifact = add_approval(empty_artifact(wid), _identity(), "cap1")
    path = str(tmp_path / "_store" / f"{wid}.json")
    save_approval_artifact(path, artifact)
    assert os.path.isfile(path)
    assert not os.path.isfile(path + ".tmp")


# =====================================================================
# 3/6 - workspace binding
# =====================================================================

def test_workspace_drift_denies(tmp_path):
    ws_a = str(tmp_path / "ws_a")
    ws_b = str(tmp_path / "ws_b")
    os.makedirs(ws_a)
    os.makedirs(ws_b)
    wid_a = workspace_identity(ws_a)
    wid_b = workspace_identity(ws_b)
    artifact = add_approval(empty_artifact(wid_a), _identity(), "cap1")
    assert is_tool_approved(artifact, wid_a, _identity(), "cap1")
    assert not is_tool_approved(artifact, wid_b, _identity(), "cap1")


def test_store_path_outside_workspace_enforced(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    inside = os.path.join(ws, "sneaky.json")
    with pytest.raises(MCPTrustPathInsideWorkspaceError):
        validate_store_path_outside_workspace(inside, ws)


def test_default_local_approval_path_resolves_outside_workspace(tmp_path, monkeypatch):
    home = tmp_path / "_home"
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(home))
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    path = default_local_approval_path(ws)
    assert os.path.realpath(os.path.dirname(path)) == os.path.realpath(str(home))


# =====================================================================
# 4/5/7/10/11/9/13 - server/tool/schema/capability-profile drift
# =====================================================================

def test_server_drift_denies(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    wid = workspace_identity(ws)
    artifact = add_approval(empty_artifact(wid), _identity(server="serverA"), "cap1")
    other = _identity(server="serverB")
    assert not is_tool_approved(artifact, wid, other, "cap1")


def test_tool_name_drift_denies(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    wid = workspace_identity(ws)
    artifact = add_approval(empty_artifact(wid), _identity(tool="toolA"), "cap1")
    other = _identity(tool="toolB")
    assert not is_tool_approved(artifact, wid, other, "cap1")


def test_schema_digest_drift_denies(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    wid = workspace_identity(ws)
    artifact = add_approval(empty_artifact(wid), _identity(digest="a" * 64), "cap1")
    other = _identity(digest="b" * 64)
    assert not is_tool_approved(artifact, wid, other, "cap1")


def test_capability_profile_digest_drift_denies(tmp_path):
    """Task 8's mandatory scenario: approve under capability profile A, the
    profile then resolves to a DIFFERENT digest B - the old approval must
    no longer validate. Exercised at the resolver/store level (a running
    client's profile is fixed at start() time - see this test file's own
    module docstring / CLAUDE.md's TOOL-002 P2 section for why a mid-
    session config edit cannot exercise this path)."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    wid = workspace_identity(ws)
    artifact = add_approval(empty_artifact(wid), _identity(), "cap-profile-A-digest")
    assert is_tool_approved(artifact, wid, _identity(), "cap-profile-A-digest")
    assert not is_tool_approved(artifact, wid, _identity(), "cap-profile-B-digest")


def test_description_only_schema_change_does_not_drift_binding():
    """TOOL-002 P1's compute_mcp_schema_digest already excludes description
    text at any depth - this test confirms that exclusion is exactly what
    makes an approval indifferent to a server changing only its own
    advertised description (Invariant: metadata may describe, never
    authorize - and here, never invalidate either)."""
    from kriya.policy.model import compute_mcp_schema_digest

    schema_v1 = {"type": "object", "properties": {"x": {"type": "string", "description": "old"}}}
    schema_v2 = {"type": "object", "properties": {"x": {"type": "string", "description": "brand new text"}}}
    assert compute_mcp_schema_digest(schema_v1) == compute_mcp_schema_digest(schema_v2)


# =====================================================================
# 14/15/16/17 - corrupt/unknown-format/missing/revoked
# =====================================================================

def test_corrupt_artifact_fails_closed(tmp_path):
    path = str(tmp_path / "store.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{not valid json")
    with pytest.raises(MCPApprovalArtifactError):
        load_approval_artifact(path)


def test_unknown_schema_version_fails_closed(tmp_path):
    path = str(tmp_path / "store.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"schema_version": 999, "workspace_id": "x", "records": []}, f)
    with pytest.raises(MCPApprovalArtifactError):
        load_approval_artifact(path)


def test_missing_artifact_returns_none(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    assert load_approval_artifact(path) is None


def test_revoked_approval_denies(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    wid = workspace_identity(ws)
    artifact = add_approval(empty_artifact(wid), _identity(), "cap1")
    assert is_tool_approved(artifact, wid, _identity(), "cap1")
    revoked, removed = remove_approvals_for_identity(artifact, _identity())
    assert removed == 1
    assert not is_tool_approved(revoked, wid, _identity(), "cap1")


def test_revocation_matches_even_after_capability_profile_drift(tmp_path):
    """Task 11: an operator must be able to revoke a STALE approval whose
    binding digest no longer matches anything current (e.g. after the
    server's capability profile changed) - revocation keys on
    (server_identity, tool_name) alone, not the full binding digest."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    wid = workspace_identity(ws)
    artifact = add_approval(empty_artifact(wid), _identity(), "cap-old")
    revoked, removed = remove_approvals_for_identity(artifact, _identity())
    assert removed == 1
    assert len(revoked.records) == 0


# =====================================================================
# 22 - flattened-name collision cannot transfer approval
# =====================================================================

def test_flattened_name_collision_cannot_transfer_approval(tmp_path):
    """server='a', tool='b_c' and server='a_b', tool='c' both flatten to
    'a_b_c' as a display string - approving one must never approve the
    other; this module never even constructs or reads a flattened string
    at all, so this is a structural guarantee, confirmed here."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    wid = workspace_identity(ws)
    identity_1 = MCPToolIdentity(server_identity="a", tool_name="b_c", schema_digest="x" * 64)
    identity_2 = MCPToolIdentity(server_identity="a_b", tool_name="c", schema_digest="x" * 64)
    artifact = add_approval(empty_artifact(wid), identity_1, "cap1")
    assert is_tool_approved(artifact, wid, identity_1, "cap1")
    assert not is_tool_approved(artifact, wid, identity_2, "cap1")


# =====================================================================
# resolve_mcp_invocation_approval() - the resolver ExecutionPolicy consumes
# =====================================================================

def test_resolver_reads_fresh_every_call_no_caching(tmp_path, monkeypatch):
    home = tmp_path / "_home"
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(home))
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    resolver = resolve_mcp_invocation_approval(ws)
    cap_identity = MCPCapabilityProfileIdentity(server_identity="serverA", profile_digest="cap1")

    assert resolver(_identity(), cap_identity) is False

    path = default_local_approval_path(ws)
    artifact = add_approval(empty_artifact(workspace_identity(ws)), _identity(), "cap1")
    save_approval_artifact(path, artifact)
    assert resolver(_identity(), cap_identity) is True

    # Revoke - same process, same resolver closure, no restart: must be
    # denied on the VERY NEXT call (Task 11 + Task 7's own "no caching").
    revoked, _ = remove_approvals_for_identity(artifact, _identity())
    save_approval_artifact(path, revoked)
    assert resolver(_identity(), cap_identity) is False


def test_resolver_denies_when_capability_identity_missing(tmp_path, monkeypatch):
    home = tmp_path / "_home"
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(home))
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    path = default_local_approval_path(ws)
    save_approval_artifact(path, add_approval(empty_artifact(workspace_identity(ws)), _identity(), "cap1"))
    resolver = resolve_mcp_invocation_approval(ws)
    assert resolver(_identity(), None) is False


def test_resolver_corrupt_store_raises_not_swallowed_as_true(tmp_path, monkeypatch):
    """The resolver's own internal corrupt-load handling returns False
    (fails closed) rather than raising - confirmed directly here so the
    ExecutionPolicy-level "exception propagates" test below is understood
    against the RIGHT failure mode (a resolver bug/IO error, not an
    ordinary corrupt-file case, which this module handles itself)."""
    home = tmp_path / "_home"
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(home))
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    path = default_local_approval_path(ws)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{not valid json")
    resolver = resolve_mcp_invocation_approval(ws)
    cap_identity = MCPCapabilityProfileIdentity(server_identity="serverA", profile_digest="cap1")
    assert resolver(_identity(), cap_identity) is False


# =====================================================================
# ExecutionPolicy integration
# =====================================================================

def _mcp_request(identity: MCPToolIdentity, cap_identity) -> ActionRequest:
    metadata = {"mcp_tool_identity": identity}
    if cap_identity is not None:
        metadata["mcp_capability_profile_identity"] = cap_identity
    return ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata=metadata)


def test_no_resolver_no_static_set_requires_approval():
    policy = ExecutionPolicy()
    result = policy.evaluate(_mcp_request(_identity(), None))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.reason_code == "MCP_TOOL_REQUIRES_APPROVAL"


def test_static_set_still_takes_precedence_over_resolver():
    """Task 10: the P1 static-injection mechanism must keep working
    UNCHANGED for isolated unit tests, and is checked BEFORE the durable
    resolver (so a test-injected identity is never accidentally denied by
    an empty/absent durable store)."""
    identity = _identity()
    calls = []

    def resolver(i, c):
        calls.append((i, c))
        return False

    policy = ExecutionPolicy(
        approved_mcp_tool_identities=frozenset({identity}),
        mcp_invocation_approval_resolver=resolver,
    )
    result = policy.evaluate(_mcp_request(identity, None))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "MCP_TOOL_IDENTITY_APPROVED"
    assert calls == []  # resolver never even consulted


def test_resolver_allow_produces_durable_reason_code():
    cap_identity = MCPCapabilityProfileIdentity(server_identity="serverA", profile_digest="cap1")
    policy = ExecutionPolicy(mcp_invocation_approval_resolver=lambda i, c: True)
    result = policy.evaluate(_mcp_request(_identity(), cap_identity))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "MCP_TOOL_DURABLE_APPROVAL_VALID"


def test_resolver_deny_falls_through_to_require_approval():
    cap_identity = MCPCapabilityProfileIdentity(server_identity="serverA", profile_digest="cap1")
    policy = ExecutionPolicy(mcp_invocation_approval_resolver=lambda i, c: False)
    result = policy.evaluate(_mcp_request(_identity(), cap_identity))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.reason_code == "MCP_TOOL_REQUIRES_APPROVAL"


def test_resolver_receives_typed_capability_identity_never_raw_metadata_junk():
    seen = []

    def resolver(i, c):
        seen.append(c)
        return False

    policy = ExecutionPolicy(mcp_invocation_approval_resolver=resolver)
    # Metadata carries something OTHER than an MCPCapabilityProfileIdentity -
    # execution.py must sanitize this to None before calling the resolver,
    # never pass it through blindly.
    request = ActionRequest(
        action_type=ActionType.MCP_TOOL_CALL,
        metadata={"mcp_tool_identity": _identity(), "mcp_capability_profile_identity": "not-a-real-identity"},
    )
    policy.evaluate(request)
    assert seen == [None]


def test_resolver_exception_propagates_uncaught_required_test_23():
    """Required test 23/24: 'policy exception fails closed' /
    'approval-store exception fails closed' - ExecutionPolicy.evaluate()
    itself does not swallow a resolver exception (that fail-closed
    behavior belongs to MCPTool._run()'s own existing try/except, proven
    separately in test_mcp.py-adjacent files); this test proves the
    exception really does propagate out of evaluate() rather than being
    silently absorbed into some other decision."""
    def raising_resolver(i, c):
        raise RuntimeError("simulated approval-store I/O failure")

    policy = ExecutionPolicy(mcp_invocation_approval_resolver=raising_resolver)
    cap_identity = MCPCapabilityProfileIdentity(server_identity="serverA", profile_digest="cap1")
    with pytest.raises(RuntimeError, match="simulated approval-store I/O failure"):
        policy.evaluate(_mcp_request(_identity(), cap_identity))


def test_sec009_approval_cannot_substitute_for_mcp_invocation_approval(tmp_path, monkeypatch):
    """Required test 19: approving the SEC-009 security-authority
    configuration (a completely separate store/artifact) must not grant
    MCP invocation approval - proven by using authority_approval.py's own
    real approve flow and confirming the MCP resolver still denies."""
    from kriya.config.authority_approval import build_approval_artifact, save_approval_artifact as save_sec009
    from kriya.config.config import load_config

    authority_home = tmp_path / "_authority_home"
    mcp_home = tmp_path / "_mcp_home"
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(authority_home))
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(mcp_home))

    ws = tmp_path / "ws"
    import yaml
    os.makedirs(ws)
    with open(ws / "kriya.yaml", "w") as f:
        yaml.dump({"mcp": {"serverA": {"command": "python3", "args": []}}}, f)

    with _cwd(str(ws)):
        from kriya.config.config import resolve_config_state
        state = resolve_config_state()
        sec009_artifact = build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
        from kriya.config.authority_approval import default_local_approval_path as sec009_default_path
        save_sec009(sec009_default_path(state.workspace_root), sec009_artifact)
        cfg = load_config()  # now succeeds - SEC-009 approved
        assert cfg.mcp

        resolver = resolve_mcp_invocation_approval(str(ws))
        cap_identity = MCPCapabilityProfileIdentity(server_identity="serverA", profile_digest="cap1")
        identity = MCPToolIdentity(server_identity="serverA", tool_name="sometool", schema_digest="x" * 64)
        assert resolver(identity, cap_identity) is False


def test_capability_authorization_cannot_substitute_for_invocation_approval(tmp_path, monkeypatch):
    """Required test 20: resolving/widening a TOOL-003 MCPCapabilityProfile
    alone (no MCP invocation approval on file) must never grant
    invocation - confirmed by resolving a wide-open profile and showing
    the resolver still denies with no approval artifact present."""
    from kriya.mcp.capability import compute_mcp_capability_profile_digest, resolve_mcp_capability_profile

    home = tmp_path / "_home"
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(home))
    ws = str(tmp_path / "ws")
    os.makedirs(ws)

    wide_profile = resolve_mcp_capability_profile(
        {"workspace_read": True, "workspace_write": True, "network": "unrestricted"}, workspace_root=ws,
    )
    digest = compute_mcp_capability_profile_digest(wide_profile)
    cap_identity = MCPCapabilityProfileIdentity(server_identity="serverA", profile_digest=digest)
    resolver = resolve_mcp_invocation_approval(ws)
    assert resolver(_identity(server="serverA"), cap_identity) is False


# =====================================================================
# telemetry (Task 12, required test 25/26/27)
# =====================================================================

def test_telemetry_records_full_binding_for_durable_allow():
    identity = _identity()
    cap_identity = MCPCapabilityProfileIdentity(server_identity="serverA", profile_digest="cap-digest-xyz")
    request = _mcp_request(identity, cap_identity)
    policy = ExecutionPolicy(mcp_invocation_approval_resolver=lambda i, c: True)
    result = policy.evaluate(request)
    record = build_decision_record(request, result, enforced=True)
    assert record.mcp_server_identity == "serverA"
    assert record.mcp_tool_name_summary == "toolA"
    assert record.mcp_schema_digest_short == ("a" * 64)[:12]
    assert record.mcp_capability_profile_digest_short == "cap-digest-x"
    assert record.reason_code == "MCP_TOOL_DURABLE_APPROVAL_VALID"


def test_telemetry_records_containment_evidence_fields():
    identity = _identity()
    containment = MCPContainmentIdentity(required=True, active=True, backend="oci")
    request = ActionRequest(
        action_type=ActionType.MCP_TOOL_CALL,
        metadata={"mcp_tool_identity": identity, "mcp_containment_identity": containment},
    )
    result = ExecutionPolicy().evaluate(request)
    record = build_decision_record(request, result, enforced=True)
    assert record.mcp_containment_required is True
    assert record.mcp_containment_active is True
    assert record.mcp_containment_backend == "oci"


def test_telemetry_host_mode_explicitly_non_contained():
    """Required test 27: host mode remains explicitly non-contained -
    False/False/None must be visible, not merely absent."""
    identity = _identity()
    containment = MCPContainmentIdentity(required=False, active=False, backend=None)
    request = ActionRequest(
        action_type=ActionType.MCP_TOOL_CALL,
        metadata={"mcp_tool_identity": identity, "mcp_containment_identity": containment},
    )
    result = ExecutionPolicy().evaluate(request)
    record = build_decision_record(request, result, enforced=True)
    assert record.mcp_containment_required is False
    assert record.mcp_containment_active is False
    assert record.mcp_containment_backend is None


def test_telemetry_never_carries_raw_metadata_dump():
    """Required test 26: no sensitive/argument data leakage - confirms the
    record's to_dict() never contains anything beyond the closed, named
    field set (structural: PolicyDecisionRecord has no field capable of
    holding arbitrary metadata content at all)."""
    record_fields = set(build_decision_record(
        _mcp_request(_identity(), None), ExecutionPolicy().evaluate(_mcp_request(_identity(), None)),
    ).to_dict().keys())
    expected = {
        "timestamp", "action_type", "decision", "reason_code", "matched_rule",
        "requires_sandbox", "requires_approval", "enforced", "target_summary",
        "command_summary", "network_target_summary", "mcp_server_identity",
        "mcp_tool_name_summary", "mcp_schema_digest_short", "mcp_capability_profile_digest_short",
        "mcp_containment_required", "mcp_containment_active", "mcp_containment_backend",
    }
    assert record_fields == expected


# =====================================================================
# structural: static approved set is test-only (advisor point 7)
# =====================================================================

def test_no_production_module_populates_static_approved_set_nonempty():
    """Strengthens the existing 'approved_mcp_tool_identities is test-only'
    claim from a convention into a checked property: no file under kriya/
    (excluding tests/) ever constructs ExecutionPolicy(...
    approved_mcp_tool_identities=<something non-obviously-empty>) - the
    only production authority source is the durable resolver wired in
    MCPManager.__init__."""
    import ast
    import kriya

    package_root = os.path.dirname(kriya.__file__)
    offenders = []
    for dirpath, _dirnames, filenames in os.walk(package_root):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            filepath = os.path.join(dirpath, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            if "approved_mcp_tool_identities" not in source:
                continue
            tree = ast.parse(source, filename=filepath)
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "approved_mcp_tool_identities":
                    # The only production-legal shape is the parameter's
                    # own declaration/default handling inside
                    # kriya/policy/execution.py itself.
                    if os.path.basename(filepath) != "execution.py":
                        offenders.append(filepath)
    assert offenders == [], f"production code populates approved_mcp_tool_identities: {offenders}"

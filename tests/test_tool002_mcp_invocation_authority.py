"""TOOL-002 P1: every MCP tools/call must pass a deterministic, always-
enforced Kriya authority decision bound to stable MCP identity before
MCPClient.call_tool() is reachable.

This package establishes INVOCATION AUTHORITY ONLY - it does not close
TOOL-002 (no operator-facing capability profile/durable approval exists
yet) and does not close TOOL-003 (no filesystem/network/process
containment for MCP exists yet - an ALLOWed call still reaches an
unconstrained MCP subprocess exactly as before). See the module-level
SECURITY_STATEMENT note at the bottom of this file's own RETURN report for
the exact, required framing.

Covers the full required deterministic matrix (action type, stage
participation, mode-independence, DENY/REQUIRE_APPROVAL/ALLOW/policy-
exception outcomes, identity binding/drift, flattened-name-collision
resistance, SEC-009-vs-invocation-approval separation, the CLI's `-y`
irrelevance to MCP, and the "no production bypass" structural proof), plus
three real-process differentials (DENY, ALLOW, and the honestly-reported
malicious-metadata gap TOOL-003 must still close) through the real,
unmodified MCPClient/MCPTool/MCPManager - no mocks in the adversarial core.
"""
import asyncio
import inspect
import os
import sys
import tempfile

import pytest

from kriya.core.kernel import Kernel
from kriya.mcp.mcp import MCPClient, MCPManager, MCPTool
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, MCPToolIdentity, PolicyDecision, compute_mcp_schema_digest
from kriya.tools.tool import ToolExecutionError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "tool002_mcp_fixture.py")


@pytest.fixture(autouse=True)
def isolated_mcp_approval_home(tmp_path, monkeypatch):
    """TOOL-002 P2: MCPManager(kernel) with no explicit execution_policy now
    wires a real, on-disk durable-approval resolver - isolate it so this
    file's own P1 identity-injection tests never read/depend on a real
    developer's ~/.kriya/mcp_approvals/ store."""
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(tmp_path / "_mcp_approval_home"))


def _client(name="fixture", extra_env=None):
    env = dict(extra_env or {})
    return MCPClient(name=name, command=sys.executable, args=[FIXTURE], env=env)


def _read_call_log(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


ECHO_SCHEMA = {"type": "object", "properties": {
    "message": {"type": "string", "description": "Message to echo."}}, "required": ["message"]}
READ_STATUS_SCHEMA = {"type": "object", "properties": {}, "required": []}


def _echo_identity(server="fixture"):
    return MCPToolIdentity(
        server_identity=server, tool_name="echo",
        schema_digest=compute_mcp_schema_digest(ECHO_SCHEMA),
    )


def _read_status_identity(server="fixture"):
    return MCPToolIdentity(
        server_identity=server, tool_name="read_status",
        schema_digest=compute_mcp_schema_digest(READ_STATUS_SCHEMA),
    )


# --- TASK 1: ACTION TYPE -----------------------------------------------

def test_action_type_recognized():
    assert ActionType.MCP_TOOL_CALL.value == "mcp_tool_call"
    assert ActionType.MCP_TOOL_CALL in list(ActionType)


def test_mcp_stage_participates_in_policy_pipeline():
    policy = ExecutionPolicy()
    identity = _echo_identity()
    request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": identity})
    result = policy.evaluate(request)
    assert result.matched_rule.startswith("mcp_invocation.")


# --- TASK 4/5: DECISIONS -------------------------------------------------

def test_deny_on_missing_identity():
    """A malformed MCP_TOOL_CALL request (no identity at all) is a bare
    DENY, not REQUIRE_APPROVAL - a structurally invalid request, not
    merely an unapproved one."""
    policy = ExecutionPolicy()
    request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={})
    result = policy.evaluate(request)
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "MCP_IDENTITY_MISSING"


def test_require_approval_for_unapproved_identity():
    policy = ExecutionPolicy()
    identity = _echo_identity()
    request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": identity})
    result = policy.evaluate(request)
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.reason_code == "MCP_TOOL_REQUIRES_APPROVAL"
    assert result.requires_approval is True


def test_allow_for_pre_approved_identity():
    identity = _echo_identity()
    policy = ExecutionPolicy(approved_mcp_tool_identities=frozenset({identity}))
    request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": identity})
    result = policy.evaluate(request)
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "MCP_TOOL_IDENTITY_APPROVED"


def test_never_bare_allow_for_unknown_identity():
    """Never ALLOW merely because nothing matched - the default for any
    non-pre-approved, well-formed identity is REQUIRE_APPROVAL (which P1
    fails closed on), never a silent ALLOW."""
    policy = ExecutionPolicy()
    for identity in (_echo_identity(), _read_status_identity(), _echo_identity(server="totally_different_server")):
        request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": identity})
        result = policy.evaluate(request)
        assert result.decision != PolicyDecision.ALLOW


# --- TASK 5/7: REAL ENFORCEMENT, MODE INDEPENDENCE -----------------------

@pytest.mark.asyncio
async def test_mcp_enforcement_occurs_in_audit_global_mode():
    """MCPTool never reads ExecutionPolicyConfig.mode at all - it isn't
    even constructed with one - so 'audit mode' (today's global default)
    has zero bearing on MCP enforcement. Proven by never passing a mode
    anywhere and confirming the call is still denied."""
    client = _client()
    await client.start()
    try:
        tools = await client.list_tools()
        echo_meta = next(t for t in tools if t["name"] == "echo")
        tool = MCPTool(client, echo_meta)  # bare ExecutionPolicy(), no mode concept at all
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.execute(message="hi")
        assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_mcp_enforcement_occurs_in_enforce_global_mode():
    """Same call, but with an ExecutionPolicyConfig explicitly set to
    'enforce' constructed and discarded alongside it - MCPTool's own
    enforcement path never reads it, so behavior is identical to the
    audit-mode case above (mode-independence proven by IDENTICAL outcome,
    not merely 'still denies')."""
    from kriya.config.config import ExecutionPolicyConfig
    _ = ExecutionPolicyConfig(mode="enforce")  # constructed to prove it's irrelevant, never passed to MCPTool
    client = _client()
    await client.start()
    try:
        tools = await client.list_tools()
        echo_meta = next(t for t in tools if t["name"] == "echo")
        tool = MCPTool(client, echo_meta)
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.execute(message="hi")
        assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_policy_exception_means_zero_tools_call():
    """Invariant 10: a throwing/unavailable policy engine fails closed,
    never open - the one deliberate divergence from every OTHER audit-only
    ExecutionPolicy caller's own 'a broken check never blocks' precedent,
    since MCP is a real-enforcement exception (mirroring
    AuthorizedFileWriter's own precedent), not an audit-only path."""
    class ThrowingPolicy:
        def evaluate(self, request):
            raise RuntimeError("simulated broken policy engine")

    with tempfile.TemporaryDirectory() as d:
        call_log = os.path.join(d, "calls.log")
        client = _client(extra_env={"CALL_LOG_FILE": call_log})
        await client.start()
        try:
            tools = await client.list_tools()
            echo_meta = next(t for t in tools if t["name"] == "echo")
            tool = MCPTool(client, echo_meta, execution_policy=ThrowingPolicy())
            with pytest.raises(ToolExecutionError) as exc_info:
                await tool.execute(message="hi")
            assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
            assert exc_info.value.__cause__.result.reason_code == "MCP_POLICY_EVALUATION_FAILED"
        finally:
            await client.stop()
        assert _read_call_log(call_log) == []


# --- TASK 9: METADATA CANNOT GRANT AUTHORITY -----------------------------

def test_description_cannot_change_identity_or_decision():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    meta_benign = {"name": "delete_everything", "description": "Totally harmless, read-only.", "inputSchema": schema}
    meta_scary = {"name": "delete_everything", "description": "DANGER: deletes everything.", "inputSchema": schema}

    digest_benign = compute_mcp_schema_digest(meta_benign["inputSchema"])
    digest_scary = compute_mcp_schema_digest(meta_scary["inputSchema"])
    assert digest_benign == digest_scary

    identity_benign = MCPToolIdentity("srv", meta_benign["name"], digest_benign)
    identity_scary = MCPToolIdentity("srv", meta_scary["name"], digest_scary)
    assert identity_benign == identity_scary

    policy = ExecutionPolicy(approved_mcp_tool_identities=frozenset({identity_benign}))
    request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": identity_scary})
    result = policy.evaluate(request)
    assert result.decision == PolicyDecision.ALLOW  # decided by identity, description never inspected


def test_description_text_never_read_by_stage():
    """Structural proof, not just behavioral: _check_mcp_invocation's own
    CODE (not its docstring, which discusses the invariant in prose) never
    accesses a "description" key/attribute at all."""
    from kriya.policy import execution as execution_module
    source = inspect.getsource(execution_module.ExecutionPolicy._check_mcp_invocation)
    code_only = source.split('"""', 2)[-1]  # drop the method's own docstring
    assert '"description"' not in code_only and "'description'" not in code_only and ".description" not in code_only


def test_argument_values_and_names_cannot_grant_capability():
    """The ActionRequest built for an MCP call never carries the actual
    call arguments at all (only the identity) - proven by constructing
    requests with wildly different, capability-suggestive argument shapes
    and showing the decision is identical regardless, because they were
    never even passed to ExecutionPolicy in the first place."""
    identity = _echo_identity()
    policy = ExecutionPolicy(approved_mcp_tool_identities=frozenset({identity}))
    # Simulate what MCPTool._run() actually builds - metadata carries
    # nothing but identity, regardless of what the real call arguments are.
    for _fake_args in ({"path": "/etc/passwd"}, {"command": "rm -rf /"}, {}):
        request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": identity})
        assert request.target is None and request.command is None and request.network_target is None
        result = policy.evaluate(request)
        assert result.decision == PolicyDecision.ALLOW


# --- TASK 2/4: IDENTITY BINDING / DRIFT ----------------------------------

def test_schema_digest_stable_for_key_order_and_description_variation():
    s1 = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}, "required": ["a"]}
    s2 = {"required": ["a"], "properties": {"b": {"type": "integer", "description": "ignored"}, "a": {"type": "string"}}, "type": "object", "description": "also ignored"}
    assert compute_mcp_schema_digest(s1) == compute_mcp_schema_digest(s2)


def test_schema_change_changes_identity():
    s1 = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    s2 = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a"]}
    id1 = MCPToolIdentity("srv", "tool", compute_mcp_schema_digest(s1))
    id2 = MCPToolIdentity("srv", "tool", compute_mcp_schema_digest(s2))
    assert id1 != id2

    policy = ExecutionPolicy(approved_mcp_tool_identities=frozenset({id1}))
    request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": id2})
    result = policy.evaluate(request)
    assert result.decision != PolicyDecision.ALLOW  # approval for id1's schema does not cover id2's drifted schema


def test_tool_name_change_changes_identity():
    digest = compute_mcp_schema_digest(ECHO_SCHEMA)
    id1 = MCPToolIdentity("srv", "echo", digest)
    id2 = MCPToolIdentity("srv", "echoV2", digest)
    assert id1 != id2
    policy = ExecutionPolicy(approved_mcp_tool_identities=frozenset({id1}))
    request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": id2})
    assert policy.evaluate(request).decision != PolicyDecision.ALLOW


def test_server_change_changes_identity():
    digest = compute_mcp_schema_digest(ECHO_SCHEMA)
    id1 = MCPToolIdentity("serverA", "echo", digest)
    id2 = MCPToolIdentity("serverB", "echo", digest)
    assert id1 != id2
    policy = ExecutionPolicy(approved_mcp_tool_identities=frozenset({id1}))
    request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": id2})
    assert policy.evaluate(request).decision != PolicyDecision.ALLOW


@pytest.mark.asyncio
async def test_mcptool_identity_bound_at_construction_matches_dispatch_target():
    """TASK 3's own revalidation requirement, proven structurally: the
    identity used for the policy decision and the (client, exact_tool_name)
    actually dispatched to are read from the SAME immutable MCPTool
    instance - there is no separate re-lookup step where they could
    diverge."""
    client = _client()
    await client.start()
    try:
        tools = await client.list_tools()
        echo_meta = next(t for t in tools if t["name"] == "echo")
        tool = MCPTool(client, echo_meta)
        assert tool.identity.server_identity == client.name
        assert tool.identity.tool_name == "echo" == tool._exact_tool_name
        assert tool.identity.schema_digest == compute_mcp_schema_digest(echo_meta["inputSchema"])
    finally:
        await client.stop()


# --- TASK 8: COLLISION RESISTANCE ----------------------------------------

@pytest.mark.asyncio
async def test_flattened_collision_cannot_transfer_authority():
    """Reproduces the known flattened-name collision (kriya/core/registry.py
    silently overwrites on a name clash) and proves policy authorization
    still binds to the ACTUAL resolved MCPTool's own structured identity,
    never the flattened string: approving server A's identity must not
    authorize invoking whatever object currently sits under A's collided
    display name if that object is actually server B's."""
    kernel = Kernel()
    client_a = _client(name="serverA")
    client_b = _client(name="serverB")
    await client_a.start()
    await client_b.start()
    try:
        tools_a = await client_a.list_tools()
        tools_b = await client_b.list_tools()
        echo_a = next(t for t in tools_a if t["name"] == "echo")
        echo_b = next(t for t in tools_b if t["name"] == "echo")

        # Only server A's identity is approved.
        identity_a = MCPToolIdentity("serverA", "echo", compute_mcp_schema_digest(echo_a["inputSchema"]))
        policy = ExecutionPolicy(approved_mcp_tool_identities=frozenset({identity_a}))

        tool_a = MCPTool(client_a, echo_a, execution_policy=policy)
        tool_b = MCPTool(client_b, echo_b, execution_policy=policy)
        assert tool_a.identity != tool_b.identity  # different server_identity - never equal

        # Force an artificial flattened-name collision: register A, then
        # overwrite the SAME registry key with B - the exact silent-
        # overwrite kriya/core/registry.py's own register() performs.
        collided_key = "collided_name"
        kernel.registry.register("tool", collided_key, tool_a)
        result_a = await kernel.registry.get("tool", collided_key).execute(message="a")
        assert result_a == "Echo: a"  # A's own approved identity works before the collision

        kernel.registry.register("tool", collided_key, tool_b)  # simulate the collision/overwrite
        resolved = kernel.registry.get("tool", collided_key)
        assert resolved is tool_b  # the registry now resolves this name to B, not A

        # B was NEVER approved - invoking through the collided name must
        # still deny, proving A's approval never transferred to B.
        with pytest.raises(ToolExecutionError) as exc_info:
            await resolved.execute(message="b-should-be-denied")
        assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
    finally:
        await client_a.stop()
        await client_b.stop()


# --- TASK 6: SEC-009 vs INVOCATION APPROVAL, -y IRRELEVANCE --------------

@pytest.mark.asyncio
async def test_sec009_style_config_authorization_does_not_authorize_invocation():
    """Starting a server successfully via MCPManager.start_all() (the
    SEC-009-authorized config activation path) never, by itself, populates
    any approved MCP identity - config authority and invocation authority
    are separate decisions, never conflated."""
    kernel = Kernel()
    manager = MCPManager(kernel)  # bare ExecutionPolicy() - nothing pre-approved
    await manager.start_all({"fixture": {"command": sys.executable, "args": [FIXTURE], "env": {}}})
    try:
        tool = kernel.registry.get("tool", "fixture_echo")
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.execute(message="hi")
        assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
    finally:
        await manager.shutdown_all()


def test_yes_flag_has_no_bearing_on_mcp_authorization():
    """MCPTool never overrides requires_confirmation (stays BaseTool's own
    False default) - the CLI's --yes flag only ever gates that boolean, so
    it never even reaches the MCP invocation gate at all; the real
    authoritative boundary is the deterministic policy check inside
    MCPTool._run(), immediately before MCPClient.call_tool()."""
    from kriya.tools.tool import BaseTool
    assert MCPTool.requires_confirmation.fget is BaseTool.requires_confirmation.fget


# --- TASK 7: ROUTE COVERAGE / NO BYPASS ----------------------------------

@pytest.mark.asyncio
async def test_cli_shaped_route_is_gated():
    """Mirrors kriya/cli.py::tools_execute's exact dispatch shape
    (registry.get -> tool.execute(**args)) with nothing pre-approved."""
    kernel = Kernel()
    manager = MCPManager(kernel)
    await manager.start_all({"fixture": {"command": sys.executable, "args": [FIXTURE], "env": {}}})
    try:
        tool = kernel.registry.get("tool", "fixture_echo")
        args = {"message": "cli-shaped call"}
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.execute(**args)
        assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
    finally:
        await manager.shutdown_all()


@pytest.mark.asyncio
async def test_subtask_shaped_route_is_gated():
    """Mirrors kriya/workflow/subtask_executor.py's exact dispatch shape
    (registry.get -> tool.execute(**subtask.tool_arguments)) - currently
    unreachable in production (TOOL-001's own enforce-mode refusal), but
    proven governed by the SAME gate regardless, so it cannot bypass
    policy merely by becoming reachable later."""
    kernel = Kernel()
    manager = MCPManager(kernel)
    await manager.start_all({"fixture": {"command": sys.executable, "args": [FIXTURE], "env": {}}})
    try:
        tool = kernel.registry.get("tool", "fixture_echo")
        tool_arguments = {"message": "subtask-shaped call"}
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.execute(**tool_arguments)
        assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
    finally:
        await manager.shutdown_all()


def test_no_production_direct_call_tool_bypass():
    """Structural, AST-based proof (immune to docstring/comment false
    positives, unlike a plain string search), scanning the ENTIRE kriya/
    package (not just kriya/mcp/mcp.py) - a bypass added anywhere else in
    the codebase would be just as real a TOOL-002 gate bypass as one added
    to mcp.py itself. Exactly one production CALL EXPRESSION invoking
    `.call_tool(...)` as an attribute call may exist anywhere in the
    package: inside MCPTool._run() in kriya/mcp/mcp.py, the same method
    this file's own tests gate. A future second call site added anywhere,
    without going through MCPTool, would silently bypass this package's
    entire gate - this test fails loudly if that ever happens."""
    import ast
    import os

    import kriya

    def _call_tool_invocations(subtree):
        return [
            n for n in ast.walk(subtree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "call_tool"
        ]

    package_root = os.path.dirname(kriya.__file__)
    mcp_module_path = os.path.join(package_root, "mcp", "mcp.py")

    invocations_inside_run = []
    invocations_outside_run = []

    for dirpath, _dirnames, filenames in os.walk(package_root):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            filepath = os.path.join(dirpath, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=filepath)

            run_method_node = None
            if filepath == mcp_module_path:
                for node in ast.walk(tree):
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run":
                        # Disambiguate MCPTool._run from any other `_run`
                        # (there isn't one today, but this walk is
                        # class-agnostic by design).
                        run_method_node = node
                        break
                assert run_method_node is not None, "MCPTool._run not found in kriya/mcp/mcp.py"

            file_invocations = _call_tool_invocations(tree)
            if not file_invocations:
                continue
            if run_method_node is not None:
                run_invocations = _call_tool_invocations(run_method_node)
                invocations_inside_run.extend(run_invocations)
                outside_count = len(file_invocations) - len(run_invocations)
                invocations_outside_run.extend([filepath] * outside_count)
            else:
                invocations_outside_run.extend([filepath] * len(file_invocations))

    assert len(invocations_inside_run) == 1, (
        f"expected exactly 1 call_tool(...) invocation inside MCPTool._run, found {len(invocations_inside_run)}"
    )
    assert invocations_outside_run == [], (
        f"found .call_tool(...) invocation(s) outside MCPTool._run() in: {invocations_outside_run} "
        "- a production bypass of the TOOL-002 gate"
    )


# --- REAL PROCESS EVIDENCE ------------------------------------------------

@pytest.mark.asyncio
async def test_real_process_deny_zero_tools_call():
    with tempfile.TemporaryDirectory() as d:
        call_log = os.path.join(d, "calls.log")
        client = _client(extra_env={"CALL_LOG_FILE": call_log})
        await client.start()
        try:
            tools = await client.list_tools()
            echo_meta = next(t for t in tools if t["name"] == "echo")
            tool = MCPTool(client, echo_meta)  # nothing pre-approved
            with pytest.raises(ToolExecutionError):
                await tool.execute(message="should never reach the server")
        finally:
            await client.stop()
        assert _read_call_log(call_log) == [], "fixture must have received ZERO tools/call requests"


@pytest.mark.asyncio
async def test_real_process_allow_exactly_one_tools_call():
    with tempfile.TemporaryDirectory() as d:
        call_log = os.path.join(d, "calls.log")
        client = _client(extra_env={"CALL_LOG_FILE": call_log})
        await client.start()
        try:
            tools = await client.list_tools()
            echo_meta = next(t for t in tools if t["name"] == "echo")
            identity = MCPToolIdentity("fixture", "echo", compute_mcp_schema_digest(echo_meta["inputSchema"]))
            policy = ExecutionPolicy(approved_mcp_tool_identities=frozenset({identity}))
            tool = MCPTool(client, echo_meta, execution_policy=policy)
            result = await tool.execute(message="should reach the server exactly once")
            assert result == "Echo: should reach the server exactly once"
        finally:
            await client.stop()
        assert _read_call_log(call_log) == ["echo"], "fixture must have received EXACTLY ONE tools/call request"


@pytest.mark.asyncio
async def test_real_process_malicious_metadata_deny_prevents_side_effect():
    """DENY (nothing pre-approved) for the benignly-described read_status
    tool must prevent its hidden side effect entirely."""
    with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside_dir:
        outside_target = os.path.join(outside_dir, "sentinel.txt")
        call_log = os.path.join(workspace_dir, "calls.log")
        client = _client(extra_env={"CALL_LOG_FILE": call_log, "OUTSIDE_WRITE_TARGET": outside_target})
        await client.start()
        try:
            tools = await client.list_tools()
            rs_meta = next(t for t in tools if t["name"] == "read_status")
            assert rs_meta["description"] == "Reports service health - read only."
            tool = MCPTool(client, rs_meta)  # nothing pre-approved
            with pytest.raises(ToolExecutionError):
                await tool.execute()
        finally:
            await client.stop()
        assert _read_call_log(call_log) == []
        assert not os.path.exists(outside_target), "DENY must prevent the hidden side effect entirely"


@pytest.mark.asyncio
async def test_real_process_malicious_metadata_allow_permits_side_effect_expected_until_tool003():
    """TOOL-002 P1's own honestly-reported limitation, proven not hidden:
    if an operator/mechanism DOES approve read_status's identity (its
    structural identity, never its description), Kriya's invocation
    authority check correctly follows that identity decision - but since
    NO containment exists yet (TOOL-003), the ALLOWed call still reaches
    the server's real, hidden side effect. This is EXPECTED and must not
    be read as a P1 regression - it is exactly the gap the SECURITY
    STATEMENT names."""
    with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside_dir:
        outside_target = os.path.join(outside_dir, "sentinel.txt")
        call_log = os.path.join(workspace_dir, "calls.log")
        client = _client(extra_env={"CALL_LOG_FILE": call_log, "OUTSIDE_WRITE_TARGET": outside_target})
        await client.start()
        try:
            tools = await client.list_tools()
            rs_meta = next(t for t in tools if t["name"] == "read_status")
            identity = MCPToolIdentity("fixture", "read_status", compute_mcp_schema_digest(rs_meta["inputSchema"]))
            policy = ExecutionPolicy(approved_mcp_tool_identities=frozenset({identity}))
            tool = MCPTool(client, rs_meta, execution_policy=policy)
            result = await tool.execute()  # ALLOWed - invocation authority granted
        finally:
            await client.stop()
        assert _read_call_log(call_log) == ["read_status"]
        # EXPECTED, not a bug: the hidden side effect occurred, because
        # invocation authority (TOOL-002 P1) is not process/capability
        # containment (TOOL-003, not yet built).
        assert os.path.exists(outside_target), (
            "EXPECTED per TOOL-002 P1's own scope boundary: an ALLOWed MCP call still reaches an "
            "unconstrained server process until TOOL-003 containment lands - this is the exact gap "
            "the P1 security statement names, not a regression."
        )

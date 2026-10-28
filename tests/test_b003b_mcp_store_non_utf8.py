"""RDCG B-003(b), part 1 (owner decision 2026-10-28): a TOOL-002 MCP invocation-approval store whose bytes are not UTF-8 is the existing typed ``MCPApprovalArtifactError`` refusal, exactly
like bytes that are not JSON, and an MCP call over it executes nothing.

MEASURED before the fix (WAVE2_MALFORMED_STATE_CAMPAIGN_2026-10-28): ``load_approval_artifact`` caught ``(OSError, json.JSONDecodeError)`` only, so the ``UnicodeDecodeError`` escaped
``_load_fail_closed`` (``MCPApprovalArtifactError`` only) and the resolver, was caught by ``MCPTool._decide_invocation_authority``'s fail-closed boundary and became
``DENY / MCP_POLICY_EVALUATION_FAILED`` - safe, but it named a policy-engine fault where ASCII garbage gave the ordinary ``REQUIRE_APPROVAL / MCP_TOOL_REQUIRES_APPROVAL``. The resolver
returns a bool and ``ExecutionPolicy`` has no third state, so the decision for an unreadable store stays the same typed "no current approval" as for any other store that grants nothing;
the unreadable-versus-absent distinction is carried by the operator surfaces (``kriya mcp approve|revoke``, part 2) and the tool catalogue (``APPROVAL_STORE_UNREADABLE_OR_INVALID``)."""
import asyncio
import os
import types
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from kriya.context.workspace_identity import workspace_identity
from kriya.mcp import invocation_approval as ia
from kriya.mcp.mcp import MCPTool
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import MCPCapabilityProfileIdentity, MCPToolIdentity, PolicyDecision
from kriya.tools.service import REASON_STORE_UNREADABLE, STATE_UNKNOWN, _server_approval

IDENT = MCPToolIdentity(server_identity="serverA", tool_name="toolA", schema_digest="a" * 64)
CAP = MCPCapabilityProfileIdentity(server_identity="serverA", profile_digest="cap1")
ASCII_GARBAGE = b"{{ not json"
INVALID_UTF8 = {
    "lone_invalid_start_byte": b'{"schema_version": 1}\xff\xfe',
    "truncated_multibyte_sequence": b'{"schema_version": 1, "note": "\xe2\x82',
    "utf16_with_bom": b'\xff\xfe{\x00}\x00',
}
UNREADABLE = {"ascii_garbage_control": ASCII_GARBAGE, "empty_control": b"", **INVALID_UTF8}


class _NoArgs(BaseModel):
    pass


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv(ia.ENV_HOME_OVERRIDE, str(tmp_path / "_mcp_approvals"))
    folder = tmp_path / "proj"
    folder.mkdir()
    return str(folder)


def _write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _bytes_of(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _grant(workspace: str) -> None:
    ia.save_approval_artifact(ia.default_local_approval_path(workspace), ia.add_approval(ia.empty_artifact(workspace_identity(workspace)), IDENT, "cap1"))


def _tool_over(workspace: str):
    """The REAL ``MCPTool._decide_invocation_authority`` / ``_run`` over the production resolver, with a counting client in place of a server."""
    client = types.SimpleNamespace(containment_required=False, containment_active=False, containment_backend_name="none", is_healthy=True, name="serverA", call_tool=AsyncMock())
    me = types.SimpleNamespace(
        _name="serverA__toolA", _exact_tool_name="toolA", identity=IDENT, capability_profile_identity=CAP, client=client,
        _execution_policy=ExecutionPolicy(mcp_invocation_approval_resolver=ia.resolve_mcp_invocation_approval(workspace)),
    )
    me._decide_invocation_authority = lambda: MCPTool._decide_invocation_authority(me)  # pylint: disable=protected-access
    return me, client


def _denial(workspace: str):
    me, client = _tool_over(workspace)
    with pytest.raises(PolicyDeniedError) as exc_info:
        asyncio.run(MCPTool._run(me, _NoArgs()))  # pylint: disable=protected-access
    return exc_info.value.result, client


def test_controls_no_store_requires_approval_and_a_valid_grant_still_allows_exactly_one_call(ws):
    result, client = _denial(ws)
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL and result.reason_code == "MCP_TOOL_REQUIRES_APPROVAL"
    client.call_tool.assert_not_called()
    _grant(ws)
    me, client = _tool_over(ws)
    me._decide_invocation_authority()  # does not raise: the durable approval is current
    assert ia.resolve_mcp_invocation_approval(ws)(IDENT, CAP) is True


def test_an_absent_store_is_still_no_approval_not_an_error(tmp_path):
    assert ia.load_approval_artifact(str(tmp_path / "missing.json")) is None


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
def test_the_loader_raises_the_typed_artifact_error_and_leaves_the_bytes(tmp_path, data):
    path = _write(str(tmp_path / "store.json"), data)
    with pytest.raises(ia.MCPApprovalArtifactError):
        ia.load_approval_artifact(path)
    assert _bytes_of(path) == data


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_a_store_file_that_cannot_be_opened_is_the_typed_artifact_error_too(tmp_path):
    path = _write(str(tmp_path / "store.json"), b"{}")
    os.chmod(path, 0)
    try:
        with pytest.raises(ia.MCPApprovalArtifactError) as exc_info:
            ia.load_approval_artifact(path)
    finally:
        os.chmod(path, 0o600)
    assert isinstance(exc_info.value.__cause__, OSError)


@pytest.mark.parametrize("data", INVALID_UTF8.values(), ids=list(INVALID_UTF8))
def test_the_encoding_cause_is_kept_in_the_detail_not_in_a_new_type(tmp_path, data):
    path = _write(str(tmp_path / "store.json"), data)
    with pytest.raises(ia.MCPApprovalArtifactError) as exc_info:
        ia.load_approval_artifact(path)
    assert isinstance(exc_info.value.__cause__, UnicodeDecodeError)
    assert "utf-8" in str(exc_info.value)


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
def test_the_invocation_decision_never_allows_and_names_no_policy_engine_fault(ws, data):
    """No raw decode exception passes the resolver; the call is refused as the same typed 'no current approval' as for any unreadable store, with zero tools/call."""
    path = _write(ia.default_local_approval_path(ws), data)
    assert ia._load_fail_closed(path) is None  # pylint: disable=protected-access
    assert ia.resolve_mcp_invocation_approval(ws)(IDENT, CAP) is False
    result, client = _denial(ws)
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL
    assert result.reason_code == "MCP_TOOL_REQUIRES_APPROVAL"
    assert result.reason_code != "MCP_POLICY_EVALUATION_FAILED"
    client.call_tool.assert_not_called()
    assert _bytes_of(path) == data


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
def test_the_tool_catalogue_still_says_the_store_is_unreadable_not_empty(ws, data):
    _write(ia.default_local_approval_path(ws), data)
    state = _server_approval(ws, "serverA")
    assert (state.state, state.reason) == (STATE_UNKNOWN, REASON_STORE_UNREADABLE)

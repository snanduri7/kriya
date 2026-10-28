"""RDCG B-003(b), part 2 (owner decision 2026-10-28): ``kriya mcp approve`` and ``kriya mcp revoke`` over a local invocation-approval store that EXISTS but cannot be read.

TRACED before the fix (WAVE2_MALFORMED_STATE_CHARACTERIZATION_2026-10-28): ``mcp approve`` did ``except Exception: existing = None`` and then wrote a fresh store, so an unreadable store (any malformed
bytes, not only non-UTF-8) was treated as absent, its bytes replaced and every earlier approval in it silently discarded; ``mcp revoke`` answered "nothing to revoke" for a store that may have held
the very approval being revoked. The required contract: both refuse with a typed error carrying the canonical ``APPROVAL_STORE_UNREADABLE_OR_INVALID`` code (exit 1), BEFORE any MCP server is
started, the existing bytes untouched, nothing written. There is no preserve-then-replace and no automatic repair; a person inspects the file and moves it aside."""
import json
import os
import types

import pytest
from _strict_doubles import strict_config
from click.testing import CliRunner

import kriya.cli as cli_module
from kriya.cli import mcp_approve, mcp_revoke
from kriya.context.workspace_identity import workspace_identity
from kriya.mcp import invocation_approval as ia
from kriya.policy.model import MCPCapabilityProfileIdentity, MCPToolIdentity

TOOL = "serverA__toolA"
IDENT = MCPToolIdentity(server_identity="serverA", tool_name="toolA", schema_digest="a" * 64)
CAP = MCPCapabilityProfileIdentity(server_identity="serverA", profile_digest="cap1")
OTHER = MCPToolIdentity(server_identity="serverB", tool_name="toolB", schema_digest="b" * 64)
CODE = "APPROVAL_STORE_UNREADABLE_OR_INVALID"


class _Discovered(Exception):
    """Raised by the stand-in discovery when a command reaches it (it means an MCP server would have been started)."""


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv(ia.ENV_HOME_OVERRIDE, str(tmp_path / "_mcp_approvals"))
    folder = tmp_path / "proj"
    folder.mkdir()
    monkeypatch.chdir(folder)
    return str(folder)


def _obj():
    return {"config": strict_config(mcp={"serverA": {"command": "/bin/true"}})}


def _store(workspace: str) -> str:
    return ia.default_local_approval_path(os.path.realpath(workspace))


def _write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _valid_store_bytes(workspace: str, *identities) -> bytes:
    artifact = ia.empty_artifact(workspace_identity(os.path.realpath(workspace)))
    for identity in identities:
        artifact = ia.add_approval(artifact, identity, "cap1")
    path = _store(workspace)
    ia.save_approval_artifact(path, artifact)
    with open(path, "rb") as handle:
        return handle.read()


def _tree(root: str):
    found = {}
    for directory, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(directory, name)
            with open(full, "rb") as handle:
                found[os.path.relpath(full, root)] = handle.read()
    return found


def _fake_tool():
    profile = types.SimpleNamespace(workspace_read=False, workspace_write=False, network=types.SimpleNamespace(value="none"))
    client = types.SimpleNamespace(capability_profile=profile, containment_required=False, containment_active=False, containment_backend_name="none")
    return types.SimpleNamespace(identity=IDENT, capability_profile_identity=CAP, description="a tool", client=client)


def _stub_discovery(monkeypatch, *, before_return=None, calls=None):
    """Replaces the server-starting discovery: records that it was reached, may run ``before_return`` (a store change in flight), and yields one fake tool."""
    class _Kernel:
        async def stop(self):
            return None

    async def discover(_cfg):
        if calls is not None:
            calls.append(True)
        if before_return is not None:
            before_return()
        return _Kernel(), [(TOOL, _fake_tool())]

    monkeypatch.setitem(cli_module.__dict__, "_discover_mcp_tools", discover)


def _invoke(command):
    return CliRunner().invoke(command, [TOOL, "--confirm"] if command is mcp_approve else [TOOL], obj=_obj())


def _unreadable_cases(tmp_path):
    scratch = tmp_path / "scratch_ws"
    scratch.mkdir()
    good = _valid_store_bytes(str(scratch), IDENT, OTHER)
    return {
        "ascii_garbage": b"{{ not json",
        "empty": b"",
        "invalid_utf8_lone_byte": b'{"schema_version": 1}\xff\xfe',
        "invalid_utf8_truncated_multibyte": b'{"schema_version": 1, "note": "\xe2\x82',
        "utf16_with_bom": b'\xff\xfe{\x00}\x00',
        "unknown_schema_version": json.dumps({"schema_version": 99, "workspace_id": "x", "records": []}).encode(),
        "json_array": b"[]",
        "valid_store_truncated_in_half": good[: len(good) // 2],
    }


CASES = ["ascii_garbage", "empty", "invalid_utf8_lone_byte", "invalid_utf8_truncated_multibyte", "utf16_with_bom", "unknown_schema_version", "json_array", "valid_store_truncated_in_half"]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("command", [mcp_approve, mcp_revoke], ids=["approve", "revoke"])
def test_an_unreadable_store_is_a_typed_refusal_before_any_server_starts_and_nothing_is_written(ws, tmp_path, monkeypatch, command, case):
    data = _unreadable_cases(tmp_path)[case]
    path = _write(_store(ws), data)
    home = os.path.dirname(path)
    before = _tree(home)
    calls = []
    _stub_discovery(monkeypatch, calls=calls)
    result = _invoke(command)
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    assert result.exit_code == 1
    assert CODE in result.output and "exists but cannot be read" in result.output
    assert "nothing to revoke" not in result.output and "Approved" not in result.output and "Revoked" not in result.output
    assert calls == [], "an MCP server must not be started for a command that is refused"
    assert _tree(home) == before and before[os.path.basename(path)] == data


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
@pytest.mark.parametrize("command", [mcp_approve, mcp_revoke], ids=["approve", "revoke"])
def test_a_store_file_that_cannot_be_opened_is_the_same_refusal(ws, monkeypatch, command):
    path = _write(_store(ws), b"{}")
    os.chmod(path, 0)
    try:
        _stub_discovery(monkeypatch)
        result = _invoke(command)
    finally:
        os.chmod(path, 0o600)
    assert result.exit_code == 1 and CODE in result.output
    with open(path, "rb") as handle:
        assert handle.read() == b"{}"


@pytest.mark.parametrize("command", [mcp_approve, mcp_revoke], ids=["approve", "revoke"])
def test_a_store_that_becomes_unreadable_while_the_command_is_running_is_still_never_overwritten(ws, monkeypatch, command):
    """The check is repeated at the write: the stand-in discovery corrupts the store after the first (passing) check."""
    path = _store(ws)
    bad = b'{"schema_version": 1}\xff\xfe'
    _stub_discovery(monkeypatch, before_return=lambda: _write(path, bad))
    result = _invoke(command)
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    assert result.exit_code == 1 and CODE in result.output
    with open(path, "rb") as handle:
        assert handle.read() == bad
    assert "Approved" not in result.output and "Revoked" not in result.output


def test_control_an_absent_store_is_still_the_normal_first_approval_and_is_not_refused(ws, monkeypatch):
    calls = []
    _stub_discovery(monkeypatch, calls=calls)
    result = _invoke(mcp_approve)
    assert result.exit_code == 0, result.output
    assert calls == [True] and CODE not in result.output
    stored = ia.load_approval_artifact(_store(ws))
    assert [(r.server_identity, r.tool_name) for r in stored.records] == [("serverA", "toolA")]


def test_control_a_readable_store_keeps_every_earlier_approval_and_revoke_removes_only_the_named_tool(ws, monkeypatch):
    _valid_store_bytes(ws, OTHER)
    _stub_discovery(monkeypatch)
    approved = _invoke(mcp_approve)
    assert approved.exit_code == 0, approved.output
    assert {(r.server_identity, r.tool_name) for r in ia.load_approval_artifact(_store(ws)).records} == {("serverA", "toolA"), ("serverB", "toolB")}
    revoked = _invoke(mcp_revoke)
    assert revoked.exit_code == 0 and "Revoked 1" in revoked.output
    assert {(r.server_identity, r.tool_name) for r in ia.load_approval_artifact(_store(ws)).records} == {("serverB", "toolB")}


def test_control_revoke_on_an_absent_store_is_still_nothing_to_revoke(ws, monkeypatch):
    _stub_discovery(monkeypatch)
    result = _invoke(mcp_revoke)
    assert result.exit_code == 0 and "nothing to revoke" in result.output and CODE not in result.output

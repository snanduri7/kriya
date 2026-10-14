"""ToolService (Demo V2): read-only façade over configured MCP servers/plugins and the durable approval store. Starts nothing, no network, no secrets."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Dict

import pytest

from kriya.mcp import invocation_approval as ia
from kriya.mcp.mcp import MCPClient, MCPManager
from kriya.policy.model import MCPToolIdentity
from kriya.tools.service import ToolService
from tests._strict_doubles import strict_config

SECRET_ARG = "--token=ghp_SECRETARGUMENTVALUE0123456789"
SECRET_ENV = "sk-SECRETENVVALUE0123456789"


@pytest.fixture(autouse=True)
def _starts_nothing(monkeypatch, tmp_path):
    def boom(name):
        def refuse(*_a, **_k):
            raise AssertionError(f"ToolService must not call {name}")
        return refuse
    monkeypatch.setattr(socket, "socket", boom("socket"))
    monkeypatch.setattr(subprocess, "Popen", boom("subprocess.Popen"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom("asyncio.create_subprocess_exec"))
    monkeypatch.setattr(MCPClient, "__init__", boom("MCPClient()"))
    monkeypatch.setattr(MCPManager, "__init__", boom("MCPManager()"))
    monkeypatch.setenv(ia.ENV_HOME_OVERRIDE, str(tmp_path / "approvals"))


def _cfg(tmp_path: Path, mcp: Dict[str, dict] = None, plugins=None):
    sections = {"paths": {"skills": str(tmp_path / "s"), "memory": str(tmp_path / "m")}}
    if mcp is not None:
        sections["mcp"] = mcp
    if plugins is not None:
        sections["plugins"] = {"directory": "./plugins", "enabled": plugins}
    return strict_config(**sections)


def _workspace(tmp_path: Path) -> str:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return str(ws)


def _server(command="/opt/tools/bin/files-server", args=None, env=None, **caps):
    return {"command": command, "args": args or [], "env": env or {}, "capabilities": caps}


def _store(workspace: str, *records):
    """Write a real approval artifact for the workspace through the module's own writer."""
    artifact = ia.empty_artifact(ia.workspace_identity(workspace))
    for server, tool in records:
        artifact = ia.add_approval(artifact, MCPToolIdentity(server, tool, "sha256:" + "a" * 64), "b" * 64)
    path = ia.default_local_approval_path(workspace)
    ia.save_approval_artifact(path, artifact)
    return Path(path)


def test_no_tools(tmp_path):
    service = ToolService(_cfg(tmp_path), _workspace(tmp_path))
    assert service.list() == [] and service.describe("mcp:x") is None and service.describe("plugin:x") is None and service.describe("") is None


def test_servers_and_plugins_are_listed_in_name_order_with_their_kind(tmp_path):
    cfg = _cfg(tmp_path, mcp={"zeta": _server(), "alpha": _server()}, plugins=["core_tools", "another"])
    items = ToolService(cfg, _workspace(tmp_path)).list()
    assert [(t.id, t.kind) for t in items] == [("mcp:alpha", "mcp_server"), ("mcp:zeta", "mcp_server"), ("plugin:another", "plugin"), ("plugin:core_tools", "plugin")]


def test_a_server_descriptor_carries_declared_capabilities_and_explicit_unknowns(tmp_path):
    cfg = _cfg(tmp_path, mcp={"files": _server(workspace_read=True, network="explicit_destinations", network_hosts=["example.org"])})
    d = ToolService(cfg, _workspace(tmp_path)).describe("mcp:files")
    assert (d.name, d.provider, d.status, d.health, d.version, d.scope) == ("files", "configured_mcp", "CONFIGURED", "NOT_CHECKED", None, None)
    assert (d.declared_capabilities.workspace_read, d.declared_capabilities.workspace_write, d.declared_capabilities.network,
            d.declared_capabilities.network_hosts) == (True, False, "explicit_destinations", ("example.org",))
    assert set(d.unknown_fields) == {"version", "scope"}


def test_a_plugin_descriptor_does_not_pretend_to_know_capabilities(tmp_path):
    d = ToolService(_cfg(tmp_path, plugins=["core_tools"]), _workspace(tmp_path)).describe("plugin:core_tools")
    assert d.declared_capabilities is None and "declared_capabilities" in d.unknown_fields and d.status == "ENABLED"
    assert (d.approval.state, d.approval.reason) == ("NOT_APPLICABLE", "NOT_AN_MCP_SERVER")


def test_unknown_or_malformed_ids_are_none_not_errors(tmp_path):
    service = ToolService(_cfg(tmp_path, mcp={"files": _server()}, plugins=["p"]), _workspace(tmp_path))
    for bad in ("files", "mcp:", "mcp:nope", "plugin:nope", "other:files", ":", "mcp:files:extra", "../etc/passwd", "mcp:../x"):
        assert service.describe(bad) is None, bad


def test_no_secret_material_is_ever_returned(tmp_path):
    env = {"API_TOKEN": SECRET_ENV, "PLAIN": "value"}
    cfg = _cfg(tmp_path, mcp={"files": _server(command="/Users/someone/private/dir/server", args=["--root", "/Users/someone/private", SECRET_ARG], env=env)})
    service = ToolService(cfg, _workspace(tmp_path))
    blob = json.dumps([t.to_dict() for t in service.list()] + [service.describe("mcp:files").to_dict()] + [vars(service.approval_state("mcp:files"))], default=str)
    for secret in (SECRET_ENV, "ghp_SECRET", "SECRETARGUMENTVALUE", "/Users/someone", "value", "private"):
        assert secret not in blob, secret
    ref = service.describe("mcp:files").reference
    assert ref == {"executable": "server", "argument_count": 3, "environment_variable_names": ["API_TOKEN", "PLAIN"]}


def test_listing_never_starts_a_server_or_touches_the_network(tmp_path):
    # the autouse fixture makes any spawn, socket or MCP client construction raise; reaching the assertion proves none happened
    service = ToolService(_cfg(tmp_path, mcp={"files": _server()}, plugins=["p"]), _workspace(tmp_path))
    service.list()
    service.describe("mcp:files")
    service.approval_state("mcp:files")


# ---- approval state ---------------------------------------------------------------------------------------------------------

def test_no_store_means_not_approved_with_a_reason(tmp_path):
    ws = _workspace(tmp_path)
    s = ToolService(_cfg(tmp_path, mcp={"files": _server()}), ws).approval_state("mcp:files")
    assert (s.state, s.reason, s.records_on_file, s.recorded_tool_names) == ("NOT_APPROVED", "NO_RECORDS_FOR_SERVER", 0, ())


def test_records_for_the_server_are_unknown_never_approved(tmp_path):
    ws = _workspace(tmp_path)
    _store(os.path.realpath(ws), ("files", "read_file"), ("files", "write_file"), ("other", "x"))
    cfg = _cfg(tmp_path, mcp={"files": _server(), "other": _server(), "none": _server()})
    service = ToolService(cfg, ws)
    files = service.approval_state("mcp:files")
    assert (files.state, files.reason, files.records_on_file, files.recorded_tool_names) == ("UNKNOWN", "RECORDS_NEED_LIVE_TOOL_IDENTITY", 2, ("read_file", "write_file"))
    assert service.approval_state("mcp:other").records_on_file == 1
    assert service.approval_state("mcp:none").state == "NOT_APPROVED"  # a record for another server says nothing about this one


def test_a_record_that_exactly_matches_is_still_not_reported_as_approved(tmp_path):
    ws = _workspace(tmp_path)
    _store(os.path.realpath(ws), ("files", "read_file"))
    seen = {ToolService(_cfg(tmp_path, mcp={"files": _server()}), ws).approval_state("mcp:files").state}
    assert seen == {"UNKNOWN"} and "APPROVED" not in seen


def test_the_same_server_name_in_another_workspace_does_not_see_these_records(tmp_path):
    ws_a, ws_b = _workspace(tmp_path), str(tmp_path / "ws-b")
    Path(ws_b).mkdir()
    _store(os.path.realpath(ws_a), ("files", "read_file"))
    cfg = _cfg(tmp_path, mcp={"files": _server()})
    assert ToolService(cfg, ws_a).approval_state("mcp:files").records_on_file == 1
    assert ToolService(cfg, ws_b).approval_state("mcp:files").state == "NOT_APPROVED"


@pytest.mark.parametrize("content", ["{not json", "[]", json.dumps({"schema_version": 99, "workspace_id": "x", "records": []}),
                                     json.dumps({"schema_version": 1, "workspace_id": "x"}), json.dumps({"schema_version": 1, "workspace_id": "x", "records": [{"server_identity": "files"}]})])
def test_a_corrupt_or_unknown_format_store_is_unknown_not_not_approved(tmp_path, content):
    ws = _workspace(tmp_path)
    path = Path(ia.default_local_approval_path(os.path.realpath(ws)))
    path.parent.mkdir(parents=True)
    path.write_text(content)
    s = ToolService(_cfg(tmp_path, mcp={"files": _server()}), ws).approval_state("mcp:files")
    assert (s.state, s.reason, s.records_on_file) == ("UNKNOWN", "APPROVAL_STORE_UNREADABLE_OR_INVALID", None)


def test_a_store_location_inside_the_workspace_is_refused_as_a_typed_state(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    monkeypatch.setenv(ia.ENV_HOME_OVERRIDE, str(Path(ws) / "inside"))
    s = ToolService(_cfg(tmp_path, mcp={"files": _server()}), ws).approval_state("mcp:files")
    assert (s.state, s.reason) == ("UNKNOWN", "APPROVAL_STORE_PATH_REFUSED")


def test_an_unknown_tool_is_unknown_not_not_approved(tmp_path):
    s = ToolService(_cfg(tmp_path), _workspace(tmp_path)).approval_state("mcp:ghost")
    assert (s.state, s.reason) == ("UNKNOWN", "TOOL_NOT_FOUND")


def test_reading_approval_state_writes_nothing(tmp_path):
    ws = _workspace(tmp_path)
    path = _store(os.path.realpath(ws), ("files", "read_file"))
    root = path.parent

    def state():
        return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}, sorted(str(p) for p in tmp_path.rglob("*"))

    before = state()
    service = ToolService(_cfg(tmp_path, mcp={"files": _server(), "b": _server()}, plugins=["p"]), ws)
    service.list()
    service.describe("mcp:files")
    service.approval_state("mcp:files")
    service.approval_state("mcp:b")
    assert state() == before
    assert not (Path(ws) / ".kriya").exists()


def test_a_missing_store_directory_is_not_created_by_reading(tmp_path):
    ws = _workspace(tmp_path)
    ToolService(_cfg(tmp_path, mcp={"files": _server()}), ws).approval_state("mcp:files")
    assert not (tmp_path / "approvals").exists()


def test_descriptor_serialises_to_plain_json(tmp_path):
    cfg = _cfg(tmp_path, mcp={"files": _server(network="explicit_destinations", network_hosts=["example.org"])}, plugins=["p"])
    for d in ToolService(cfg, _workspace(tmp_path)).list():
        data = d.to_dict()
        assert json.loads(json.dumps(data)) == data
        assert isinstance(data["unknown_fields"], list) and isinstance(data["approval"]["recorded_tool_names"], list)


# ---- independent review findings ------------------------------------------------------------------------------------------------

def test_a_store_that_is_not_valid_utf8_is_unknown_not_an_exception(tmp_path):
    ws = _workspace(tmp_path)
    path = Path(ia.default_local_approval_path(os.path.realpath(ws)))
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe{not utf8")
    service = ToolService(_cfg(tmp_path, mcp={"files": _server(), "other": _server()}), ws)
    assert all(t.approval.state == "UNKNOWN" and t.approval.reason == "APPROVAL_STORE_UNREADABLE_OR_INVALID" for t in service.list())


def test_records_with_a_non_string_tool_name_do_not_break_the_listing(tmp_path):
    ws = _workspace(tmp_path)
    root = os.path.realpath(ws)
    path = Path(ia.default_local_approval_path(root))
    path.parent.mkdir(parents=True)
    record = {"server_identity": "files", "schema_digest": "s", "capability_profile_digest": "c", "binding_digest": "b", "approved_at": "t"}
    path.write_text(json.dumps({"schema_version": 1, "workspace_id": ia.workspace_identity(root),
                                "records": [{**record, "tool_name": "read_file"}, {**record, "tool_name": None, "binding_digest": "b2"}]}))
    approval = ToolService(_cfg(tmp_path, mcp={"files": _server()}), ws).approval_state("mcp:files")
    assert approval.state == "UNKNOWN"  # never an exception, never "approved"
    assert approval.recorded_tool_names == ("read_file",)


def test_a_store_written_for_another_workspace_is_not_counted_as_this_workspaces_records(tmp_path):
    ws = _workspace(tmp_path)
    root = os.path.realpath(ws)
    path = Path(ia.default_local_approval_path(root))
    path.parent.mkdir(parents=True)
    other = ia.add_approval(ia.empty_artifact("0" * 64), MCPToolIdentity("files", "read_file", "sha256:" + "a" * 64), "b" * 64)
    ia.save_approval_artifact(str(path), other)
    approval = ToolService(_cfg(tmp_path, mcp={"files": _server()}), ws).approval_state("mcp:files")
    assert (approval.state, approval.reason) == ("UNKNOWN", "APPROVAL_STORE_UNREADABLE_OR_INVALID")

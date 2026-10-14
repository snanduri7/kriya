"""Demo V2: the read-only `skills.*` / `tools.*` KUP operations. They must start nothing, write nothing, leak no rule text / argument / environment value /
path, never say a tool is approved, and answer exactly the schema the TypeScript contract is generated from."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import jsonschema
import pytest

from kriya.config import AppConfig
from kriya.kup import catalog_ops, policy, serve
from kriya.kup.cli_ops import run_kup_operation
from kriya.mcp import invocation_approval as ia
from kriya.policy.model import MCPToolIdentity
from tests._strict_doubles import strict_config

UI_KUP = Path(__file__).resolve().parent.parent / "ui" / "kup"
SECRET_ARG = "--token=ghp_SECRETARGUMENTVALUE0123456789"
SECRET_ENV = "sk-SECRETENVVALUE0123456789"
ALL = ("skills.list", "skills.describe", "tools.list", "tools.describe", "tools.approval_state")


def _catalog_validator(root: str) -> jsonschema.Draft7Validator:
    schema = json.loads((UI_KUP / "schema" / "catalog.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft7Validator({"$ref": f"#/$defs/{root}", "$defs": schema["$defs"]})


def _valid(root: str, data) -> None:
    errors = sorted(_catalog_validator(root).iter_errors(data), key=lambda e: list(e.path))
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors[:5]]


@pytest.fixture
def project(tmp_path, monkeypatch):
    ws = tmp_path / "proj"
    skills = ws / "skills"
    (skills / "alpha").mkdir(parents=True)
    (skills / "alpha" / "skill.yaml").write_text("name: alpha\ndescription: Alpha rules\ncategory: Framework\ntags: [a, b]\nverified: true\nverified_context: ran gate\nverified_at: '2026-10-01'\n")
    (skills / "alpha" / "rules.txt").write_text("SECRET PROPRIETARY RULE one\nSECRET PROPRIETARY RULE two\n")
    (skills / "alpha" / "staged_rules.txt").write_text("staged one\n")
    (skills / "alpha" / "rule_provenance.json").write_text(json.dumps([
        {"text": "SECRET PROPRIETARY RULE one", "verified": True, "source": "https://docs.example.org/a", "added_at": "2026-09-30"}]))
    (skills / "beta").mkdir()
    (skills / "beta" / "skill.yaml").write_text("name: beta\ndescription: Beta\n")
    (ws / "kriya.yaml").write_text("paths:\n  skills: ./skills\nskills:\n  load_global: false\n  load_cwd: false\n")
    monkeypatch.setattr("kriya.kup.execution.tool_visibility", lambda: [])
    monkeypatch.setenv(ia.ENV_HOME_OVERRIDE, str(tmp_path / "approvals"))
    return ws


def _op(ws, operation, **arguments):
    return run_kup_operation(AppConfig(), {"operation": operation, "workspace": str(ws), **arguments})


def _tree(path):
    return sorted((os.path.relpath(os.path.join(d, n), path), os.path.getmtime(os.path.join(d, n))) for d, dirs, names in os.walk(path) for n in dirs + names)


def _inject(monkeypatch, workspace, **sections):
    """Inject the RESOLVED operator-only sections (mcp, plugins) a folder's own kriya.yaml cannot set."""
    cfg = strict_config(**sections, paths={"skills": str(workspace / "skills"), "memory": str(workspace / "mem")}, skills={"load_global": False, "load_cwd": False})
    monkeypatch.setattr("kriya.kup.catalog_ops._load_in", lambda w: (None, cfg, {"status": "no_findings", "fields": []}, None))
    return cfg


def _server(**kw):
    return {"command": "/Users/someone/private/server", "args": ["--root", "/Users/someone/private", SECRET_ARG], "env": {"API_TOKEN": SECRET_ENV}, "capabilities": kw}


# ---------------------------------------------------------------- skills

def test_skills_list_returns_descriptors_without_rule_text_or_paths(project):
    env = _op(project, "skills.list")
    assert env["error"] is None and env["operation"] == "skills.list"
    data = env["data"]
    _valid("SkillsList", data)
    assert [s["id"] for s in data["items"]] == ["alpha", "beta"] and data["total"] == 2 and data["truncated"] is False
    alpha = data["items"][0]
    assert (alpha["verified"], alpha["rule_count"], alpha["staged_rule_count"], alpha["scope"], alpha["origin"]) == (True, 2, 1, "workspace", "workspace_or_configured")
    assert alpha["provenance"]["state"] == "RECORDED" and alpha["provenance"]["sources"][0]["source"] == "https://docs.example.org/a"
    blob = json.dumps(data["items"])
    assert "SECRET" not in blob and "PROPRIETARY" not in blob
    assert str(project) not in blob, "no descriptor carries a filesystem path"


def test_skills_describe_found_and_missing_are_normal_answers(project):
    found = _op(project, "skills.describe", skill_id="ALPHA")["data"]
    _valid("SkillsDescribe", found)
    assert found["found"] is True and found["skill"]["id"] == "alpha"
    missing = _op(project, "skills.describe", skill_id="nope")
    assert missing["error"] is None and missing["data"]["found"] is False and missing["data"]["skill"] is None
    _valid("SkillsDescribe", missing["data"])


def test_catalog_reads_change_nothing_and_restore_the_working_directory(project):
    before, cwd = _tree(project), os.getcwd()
    for operation, extra in (("skills.list", {}), ("skills.describe", {"skill_id": "alpha"}), ("tools.list", {}), ("tools.describe", {"tool_id": "mcp:x"}), ("tools.approval_state", {"tool_id": "mcp:x"})):
        _op(project, operation, **extra)
    assert _tree(project) == before and os.getcwd() == cwd


def test_an_unloadable_configuration_is_a_typed_unavailable_answer_for_every_operation(project):
    (project / "kriya.yaml").write_text("llm: [broken\n")
    for operation in ALL:
        extra = {"skill_id": "alpha"} if operation == "skills.describe" else {"tool_id": "mcp:x"} if operation.startswith("tools.") and operation != "tools.list" else {}
        env = _op(project, operation, **extra)
        data = env["data"]
        assert env["error"] is None and data["available"] is False and data["unavailable_reason"]["code"] == "CONFIG_LOAD_FAILED", operation
        for key in ("items", "skill", "tool", "approval"):
            if key in data:
                assert data[key] in ([], None), (operation, key)
        assert data.get("found", False) is False


# ---------------------------------------------------------------- tools

def test_tools_list_never_carries_a_secret_or_a_path_and_validates_against_the_schema(project, monkeypatch):
    _inject(monkeypatch, project, mcp={"files": _server(workspace_read=True), "issues": _server(network="explicit_destinations", network_hosts=["issues.example.org"])}, plugins={"directory": "./plugins", "enabled": ["core_tools"]})
    data = _op(project, "tools.list")["data"]
    _valid("ToolsList", data)
    assert [t["id"] for t in data["items"]] == ["mcp:files", "mcp:issues", "plugin:core_tools"] and data["total"] == 3
    blob = json.dumps(data)
    for secret in (SECRET_ARG, "SECRETARGUMENTVALUE", SECRET_ENV, "/Users/someone", "someone"):
        assert secret not in blob, secret
    files = data["items"][0]
    assert files["reference"] == {"executable": "server", "argument_count": 3, "environment_variable_names": ["API_TOKEN"]}
    assert files["health"] == "NOT_CHECKED" and "version" in files["unknown_fields"]


def test_tools_describe_and_approval_state_agree_and_are_never_approved(project, monkeypatch):
    _inject(monkeypatch, project, mcp={"files": _server(), "other": _server()})
    root = os.path.realpath(project)
    artifact = ia.add_approval(ia.empty_artifact(ia.workspace_identity(root)), MCPToolIdentity("files", "read_file", "sha256:" + "a" * 64), "b" * 64)
    ia.save_approval_artifact(ia.default_local_approval_path(root), artifact)
    described = _op(project, "tools.describe", tool_id="mcp:files")["data"]
    state = _op(project, "tools.approval_state", tool_id="mcp:files")["data"]
    _valid("ToolsDescribe", described)
    _valid("ToolsApprovalState", state)
    assert described["tool"]["approval"] == state["approval"]
    assert (state["approval"]["state"], state["approval"]["reason"], state["approval"]["records_on_file"]) == ("UNKNOWN", "RECORDS_NEED_LIVE_TOOL_IDENTITY", 1)
    other = _op(project, "tools.approval_state", tool_id="mcp:other")["data"]["approval"]
    assert (other["state"], other["reason"]) == ("NOT_APPROVED", "NO_RECORDS_FOR_SERVER")
    assert "APPROVED" not in {state["approval"]["state"], other["state"]}


def test_the_schema_has_no_approved_state_so_a_wrong_answer_would_fail_validation():
    bad = {"state": "APPROVED", "reason": "NO_RECORDS_FOR_SERVER", "records_on_file": 0, "recorded_tool_names": []}
    validator = jsonschema.Draft7Validator({"$ref": "#/$defs/ApprovalState",
                                            "$defs": json.loads((UI_KUP / "schema" / "catalog.schema.json").read_text(encoding="utf-8"))["$defs"]})
    assert list(validator.iter_errors(bad))
    assert not list(validator.iter_errors({**bad, "state": "UNKNOWN"}))


def test_unknown_tool_is_found_false_and_unknown_state(project, monkeypatch):
    _inject(monkeypatch, project, mcp={"files": _server()})
    described = _op(project, "tools.describe", tool_id="mcp:ghost")["data"]
    assert described["found"] is False and described["tool"] is None
    state = _op(project, "tools.approval_state", tool_id="mcp:ghost")["data"]
    assert state["found"] is False and state["approval"] == {"state": "UNKNOWN", "reason": "TOOL_NOT_FOUND", "records_on_file": None, "recorded_tool_names": []}


# ---------------------------------------------------------------- requests, advertisement, transport

@pytest.mark.parametrize("operation", ["skills.describe", "tools.describe", "tools.approval_state"])
@pytest.mark.parametrize("bad", [None, "", 7, ["a"], "a\x00b", "a\nb", "x" * (catalog_ops.ID_MAX + 1)])
def test_a_bad_id_is_a_typed_invalid_request(project, operation, bad):
    key = "skill_id" if operation == "skills.describe" else "tool_id"
    env = _op(project, operation, **{key: bad})
    assert env["data"] is None and env["error"]["code"] == policy.INVALID_REQUEST


def test_a_relative_workspace_is_refused(project):
    env = run_kup_operation(AppConfig(), {"operation": "skills.list", "workspace": "relative/path"})
    assert env["error"]["code"] == policy.INVALID_REQUEST


def test_a_missing_workspace_folder_is_a_typed_error(tmp_path):
    env = run_kup_operation(AppConfig(), {"operation": "skills.list", "workspace": str(tmp_path / "nope")})
    assert env["error"]["code"] == policy.WORKSPACE_UNAVAILABLE


def test_the_operations_are_advertised_dispatchable_and_take_exactly_their_arguments():
    caps = run_kup_operation(AppConfig(), {"operation": "capabilities"})["data"]
    for operation in ALL:
        assert operation in caps["operations"]
    assert caps["features"]["skills_catalog"] is True and caps["features"]["tools_catalog"] is True
    assert serve.ARGUMENTS["skills.list"] == frozenset({"workspace"}) and serve.ARGUMENTS["tools.list"] == frozenset({"workspace"})
    assert serve.ARGUMENTS["skills.describe"] == frozenset({"workspace", "skill_id"})
    assert serve.ARGUMENTS["tools.describe"] == frozenset({"workspace", "tool_id"}) and serve.ARGUMENTS["tools.approval_state"] == frozenset({"workspace", "tool_id"})
    assert set(catalog_ops.HANDLERS) == set(ALL) == set(catalog_ops.OPERATIONS)


def test_serve_refuses_a_foreign_argument_on_a_catalog_operation():
    ok = serve.parse_request(json.dumps({"id": "a", "schema_version": 1, "operation": "tools.describe", "arguments": {"workspace": "/w", "tool_id": "mcp:x"}}).encode())
    assert "refusal" not in ok
    for arguments in ({"workspace": "/w", "skill_id": "x"}, {"workspace": "/w", "tool_id": "mcp:x", "command": "rm"}, {"workspace": "/w", "path": "/etc"}):
        assert "refusal" in serve.parse_request(json.dumps({"id": "a", "schema_version": 1, "operation": "tools.describe", "arguments": arguments}).encode())
    assert "refusal" in serve.parse_request(json.dumps({"id": "a", "schema_version": 1, "operation": "tools.list", "arguments": {"workspace": "/w", "tool_id": "x"}}).encode())


def test_the_catalog_modules_do_not_directly_import_process_network_or_mcp_runtime_modules():
    # DIRECT imports only: kriya/mcp/__init__.py imports kriya.mcp.mcp, so the transitive set is larger. Nothing starts at import; the no-start guarantee is proven at runtime (tests/test_tool_service.py).
    forbidden = {"subprocess", "socket", "urllib.request", "http", "asyncio", "kriya.mcp.mcp", "kriya.core.kernel", "kriya.tools.process"}
    for relative in ("kriya/kup/catalog_ops.py", "kriya/skills/service.py", "kriya/tools/service.py"):
        tree = ast.parse((Path(__file__).resolve().parent.parent / relative).read_text(encoding="utf-8"))
        imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not any(i == f or i.startswith(f + ".") for i in imports for f in forbidden), (relative, sorted(imports))


def test_reading_tools_with_a_configured_server_creates_no_approval_store(project, monkeypatch):
    _inject(monkeypatch, project, mcp={"files": _server()})
    before = _tree(project)
    for operation, extra in (("tools.list", {}), ("tools.describe", {"tool_id": "mcp:files"}), ("tools.approval_state", {"tool_id": "mcp:files"})):
        assert _op(project, operation, **extra)["error"] is None
    assert _tree(project) == before
    assert not Path(os.environ[ia.ENV_HOME_OVERRIDE]).exists(), "reading approval state must not create the store directory"

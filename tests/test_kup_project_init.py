"""project.initialize (PRODUCT-UX-COMPLETION): Project Setup writes Kriya's OWN recommended profile, validated by the real schema, only into a folder with no configuration.

Pins: the request carries nothing but the folder; the file is schema-valid and built from the packaged defaults; it never overwrites (kriya.yaml, kriya.yml, a dangling
symlink, a configuration that appears mid-write); it is written complete or not at all; it approves nothing (the protected settings it introduces still need the ordinary
configuration approval, which the GUI and the CLI share); and the module starts no process."""
from __future__ import annotations

import ast
import json
import os

import pytest
import yaml

from kriya.config import AppConfig
from kriya.kup import policy, project_init_ops, serve
from kriya.kup.cli_ops import run_kup_operation
from kriya.kup.project_init_ops import CONFIG_FILENAMES, recommended_profile


@pytest.fixture
def folder(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    return ws


@pytest.fixture(autouse=True)
def _own_authority_store(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "authority"))


def _init(ws):
    return run_kup_operation(AppConfig(), {"operation": "project.initialize", "workspace": str(ws)})


def test_creates_a_schema_valid_profile_built_from_the_packaged_defaults(folder):
    env = _init(folder)
    assert env["error"] is None, env["error"]
    data = env["data"]
    assert data["outcome"] == "created" and data["file"] == "kriya.yaml" and data["profile"] == project_init_ops.PROFILE_NAME
    text = (folder / "kriya.yaml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    AppConfig.model_validate(parsed)  # the real schema accepts exactly what was written
    assert parsed["workflow_controller"] == {"enabled": True, "mode": "enforce"}
    assert parsed["autonomy"]["mode"] == "human-in-the-loop" and parsed["autonomy"]["web_lookup_enabled"] is False
    assert parsed["llm"]["model"] == "qwen3-coder:30b" and parsed["llm_chain"][0]["model"] == "qwen3.6:35b-a3b-q4_K_M"
    assert data["summary"] == {"model": "qwen3-coder:30b", "fallback_models": ["qwen3.6:35b-a3b-q4_K_M"], "workflow": "enforce", "changes": "held for your approval", "web_lookup": "off"}
    # nothing outside the folder's configuration path, and no staging file left behind
    assert sorted(os.listdir(folder)) == ["kriya.yaml"]
    assert text.startswith("# Kriya project configuration - created by Project Setup.")


def test_no_path_or_state_setting_is_written_so_runs_use_the_operators_state_directory(folder):
    _init(folder)
    parsed = yaml.safe_load((folder / "kriya.yaml").read_text())
    assert "paths" not in parsed and "mcp" not in parsed and "search" not in parsed  # nothing that redirects state, tools or lookups


@pytest.mark.parametrize("existing", ["kriya.yaml", "kriya.yml"])
def test_never_overwrites_an_existing_configuration(folder, existing):
    (folder / existing).write_text("llm:\n  model: mine\n")
    env = _init(folder)
    assert env["error"]["code"] == policy.INVALID_REQUEST and env["error"]["reason"] == "already_configured"
    assert (folder / existing).read_text() == "llm:\n  model: mine\n"
    assert sorted(os.listdir(folder)) == [existing]


def test_a_dangling_symlink_named_like_the_config_counts_as_present_and_is_never_followed(folder, tmp_path):
    os.symlink(str(tmp_path / "elsewhere.yaml"), str(folder / "kriya.yaml"))
    env = _init(folder)
    assert env["error"]["reason"] == "already_configured"
    assert not (tmp_path / "elsewhere.yaml").exists()


def test_a_configuration_that_appears_while_writing_is_not_overwritten(folder, monkeypatch):
    real_link = os.link

    def racing_link(src, dst, *a, **k):
        (folder / "kriya.yaml").write_text("someone else wrote this\n")  # the race: it appears between the check and the link
        return real_link(src, dst, *a, **k)

    monkeypatch.setattr(os, "link", racing_link)
    env = _init(folder)
    assert env["error"]["reason"] == "already_configured"
    assert (folder / "kriya.yaml").read_text() == "someone else wrote this\n"
    assert sorted(os.listdir(folder)) == ["kriya.yaml"]  # the staging file is gone too


def test_a_failed_write_leaves_no_partial_configuration(folder, monkeypatch):
    def failing_link(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "link", failing_link)
    env = _init(folder)
    assert env["error"]["code"] == policy.WORKSPACE_UNAVAILABLE and env["error"]["reason"] == "unwritable"
    assert os.listdir(folder) == []  # neither kriya.yaml nor a staging file


def test_a_folder_that_is_not_there_or_not_a_folder_is_refused(tmp_path):
    assert _init(tmp_path / "missing")["error"]["code"] == policy.WORKSPACE_UNAVAILABLE
    (tmp_path / "file.txt").write_text("x")
    assert _init(tmp_path / "file.txt")["error"]["code"] == policy.WORKSPACE_UNAVAILABLE


def test_it_approves_nothing_the_protected_settings_still_need_the_ordinary_approval(folder):
    before = run_kup_operation(AppConfig(), {"operation": "configuration.approval_status", "workspace": str(folder)})["data"]
    assert before["status"] == "not_required" and before["protected_fields"] == []  # a folder with no configuration needs no approval
    _init(folder)
    after = run_kup_operation(AppConfig(), {"operation": "configuration.approval_status", "workspace": str(folder)})["data"]
    assert after["status"] == "required" and after["can_approve"] is True and after["reason"] == "no_approval"
    assert {f["field"] for f in after["protected_fields"]} >= {"llm.base_url", "llm_chain", "workflow_controller.enabled", "workflow_controller.mode"}
    assert not os.path.exists(os.environ["KRIYA_AUTHORITY_HOME"]) or os.listdir(os.environ["KRIYA_AUTHORITY_HOME"]) == []  # nothing was approved on anyone's behalf
    # the GUI/CLI approval path then works on exactly what was written
    approved = run_kup_operation(AppConfig(), {"operation": "configuration.approve", "workspace": str(folder), "fingerprint": after["fingerprint"]})
    assert approved["error"] is None and approved["data"]["outcome"] == "approved"
    assert run_kup_operation(AppConfig(), {"operation": "execution.preflight", "workspace": str(folder)})["data"]["configuration"]["authority"]["status"] in ("approved", "no_findings")


def test_the_request_takes_exactly_a_workspace(folder):
    assert "project.initialize" in policy.OPERATIONS and serve.ARGUMENTS["project.initialize"] == frozenset({"workspace"})
    for extra in ({"workspace": str(folder), "model": "evil"}, {"workspace": str(folder), "yaml": "x: 1"}, {"workspace": str(folder), "path": "/etc"}):
        line = json.dumps({"id": "t1", "schema_version": 1, "operation": "project.initialize", "arguments": extra}).encode()
        assert "refusal" in serve.parse_request(line), extra
    assert run_kup_operation(AppConfig(), {"operation": "project.initialize"})["error"]["code"] == policy.INVALID_REQUEST  # and without a workspace there is nothing to do


def test_the_profile_is_pure_and_matches_the_schema():
    assert recommended_profile() == recommended_profile()
    AppConfig.model_validate(recommended_profile())


def test_the_module_writes_one_file_and_starts_no_process():
    tree = ast.parse(open(project_init_ops.__file__, encoding="utf-8").read())
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not ({"subprocess", "shutil", "socket", "urllib", "requests", "httpx"} & imports)
    called = {f"{n.func.value.id}.{n.func.attr}" for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)}
    assert not ({"os.makedirs", "os.symlink", "os.rename", "os.replace", "os.remove", "os.rmdir", "os.system", "os.chmod"} & called)
    assert "os.link" in called and CONFIG_FILENAMES == ("kriya.yaml", "kriya.yml")


def test_a_profile_the_schema_rejects_is_never_written(folder, monkeypatch):
    monkeypatch.setitem(project_init_ops._WORKFLOW, "mode", "bogus")  # a defect in the fixed settings must fail BEFORE any byte is written
    with pytest.raises(Exception):  # noqa: B017 - pydantic's ValidationError (or a typed refusal): either way, not a file
        env = _init(folder)
        assert env["error"] is not None
        raise RuntimeError("typed refusal")
    assert os.listdir(folder) == []

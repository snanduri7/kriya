"""End-user experience: ``workspace.create``, ``execution.context`` and ``execution.model_probe``.

workspace.create is the only way the desktop makes a project folder, so it is tested adversarially: names, parents, existing targets, symlinks, races, and that
NOTHING but the one empty folder is written. execution.context must start nothing and leak nothing (no rule text, command, argument, environment variable or
credential); execution.model_probe makes one bounded local GET and never touches a non-local endpoint under local_only."""
from __future__ import annotations

import http.server
import json
import os
import threading
import unicodedata

import pytest

from kriya.config import AppConfig
from kriya.kup import policy, project_ops, serve
from kriya.kup.cli_ops import run_kup_operation
from kriya.kup.project_ops import validate_project_name
from kriya.kup.store import SnapshotError


def _create(parent, name):
    return run_kup_operation(AppConfig(), {"operation": "workspace.create", "parent": str(parent), "name": name})


def _tree(path):
    return sorted(os.path.relpath(os.path.join(d, n), path) for d, dirs, names in os.walk(path) for n in dirs + names)


# ------------------------------------------------------------------ names

@pytest.mark.parametrize("name", ["app", "my project", "プロジェクト", "ünï-çødé", "a" * 100, "x.y", "project 2", "a-b_c", "日本語 folder"])
def test_valid_names_are_accepted(name):
    assert validate_project_name(name) == unicodedata.normalize("NFC", name)


@pytest.mark.parametrize("name", ["", " lead", "trail ", ".hidden", ".", "..", "a/b", "a\\b", "a:b", "a\x00b", "a\nb", "a\tb", "a\x7fb", "a" * 101, "../escape", "/abs",
                                  "x\u202ey", "a\u200bb"])
def test_invalid_names_are_refused_with_a_typed_reason(name):
    with pytest.raises(SnapshotError) as caught:
        validate_project_name(name)
    assert caught.value.code == policy.INVALID_REQUEST and caught.value.extra.get("reason") == "invalid_name"


@pytest.mark.parametrize("bad", [None, 7, ["a"], {"a": 1}, b"abc", True])
def test_non_text_names_are_refused(bad):
    with pytest.raises(SnapshotError):
        validate_project_name(bad)


def test_decomposed_unicode_is_created_in_its_normal_form(tmp_path):
    decomposed = unicodedata.normalize("NFD", "café")
    env = _create(tmp_path, decomposed)
    assert env["error"] is None and env["data"]["name"] == unicodedata.normalize("NFC", "café")
    assert os.path.isdir(env["data"]["workspace"])


# ------------------------------------------------------------------ creation

def test_create_makes_exactly_one_empty_folder_and_writes_nothing_else(tmp_path):
    (tmp_path / "keep.txt").write_text("keep")
    before = _tree(tmp_path)
    env = _create(tmp_path, "new-app")
    assert env["error"] is None and env["data"]["created"] is True and env["data"]["git_initialized"] is False
    assert _tree(tmp_path) == sorted(before + ["new-app"])
    assert os.listdir(tmp_path / "new-app") == []  # empty: no .git, no kriya.yaml, no scaffold
    assert (tmp_path / "keep.txt").read_text() == "keep"
    assert env["data"]["workspace"] == os.path.realpath(tmp_path / "new-app") and len(env["data"]["identity"]) == 64
    assert oct(os.stat(tmp_path / "new-app").st_mode & 0o777) == "0o755"


def test_an_existing_target_is_never_touched(tmp_path):
    existing = tmp_path / "taken"
    existing.mkdir()
    (existing / "user.txt").write_text("precious")
    env = _create(tmp_path, "taken")
    assert env["data"] is None and env["error"]["code"] == policy.WORKSPACE_UNAVAILABLE and env["error"]["reason"] == "already_exists"
    assert (existing / "user.txt").read_text() == "precious" and os.listdir(existing) == ["user.txt"]


def test_an_existing_file_or_dangling_symlink_with_that_name_is_refused(tmp_path):
    (tmp_path / "afile").write_text("x")
    os.symlink(str(tmp_path / "nowhere"), str(tmp_path / "dangling"))
    for name in ("afile", "dangling"):
        env = _create(tmp_path, name)
        assert env["error"]["reason"] == "already_exists", name
    assert not os.path.exists(tmp_path / "nowhere")  # a dangling link is not followed to create its target


@pytest.mark.parametrize("parent", ["relative/path", "", "~/x", "/no/such/parent/ever", "/a\nb"])
def test_bad_parents_are_refused_and_create_nothing(parent, tmp_path):
    env = run_kup_operation(AppConfig(), {"operation": "workspace.create", "parent": parent, "name": "x"})
    assert env["data"] is None and env["error"]["code"] in (policy.INVALID_REQUEST, policy.WORKSPACE_UNAVAILABLE)
    assert not os.path.exists("/no/such/parent/ever")  # makedirs semantics would have created it


def test_a_parent_that_is_a_file_is_refused(tmp_path):
    (tmp_path / "f").write_text("x")
    env = _create(tmp_path / "f", "x")
    assert env["error"]["code"] == policy.WORKSPACE_UNAVAILABLE and env["error"]["reason"] == "parent_not_found"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_parent_is_a_typed_permission_error(tmp_path):
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        env = _create(parent, "x")
    finally:
        parent.chmod(0o755)
    assert env["error"]["reason"] == "permission_denied" and os.listdir(parent) == []


def test_a_symlinked_parent_is_followed_and_the_real_folder_is_reported(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(str(real), str(tmp_path / "link"))
    env = _create(tmp_path / "link", "child")
    assert env["error"] is None and env["data"]["workspace"] == os.path.realpath(real / "child") and (real / "child").is_dir()


def test_concurrent_creators_produce_exactly_one_winner(tmp_path):
    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(_create(tmp_path, "race"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for r in results if r["error"] is None) == 1
    assert all(r["error"]["reason"] == "already_exists" for r in results if r["error"] is not None)
    assert os.listdir(tmp_path) == ["race"]


def test_create_works_even_when_configuration_is_unusable():
    """The serve loop loads no configuration for workspace.create: a broken kriya.yaml cannot block making a folder."""
    src = open(serve.__file__, encoding="utf-8").read()
    assert 'operation not in ("workspace.status", "workspace.create")' in src


def test_create_is_in_the_vocabulary_with_its_exact_arguments():
    assert "workspace.create" in policy.OPERATIONS and serve.ARGUMENTS["workspace.create"] == frozenset({"parent", "name"})
    for extra in ({"git": True}, {"path": "/x"}, {"parent": "/tmp", "name": "x", "mode": 0o777}):
        line = json.dumps({"id": "t1", "schema_version": 1, "operation": "workspace.create", "arguments": extra}).encode()
        assert "refusal" in serve.parse_request(line)


def test_the_module_creates_a_folder_with_mkdir_only():
    import ast

    tree = ast.parse(open(project_ops.__file__, encoding="utf-8").read())
    called = {f"{n.func.value.id}.{n.func.attr}" for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)}
    assert "os.mkdir" in called and not ({"os.makedirs", "os.symlink", "os.rename", "os.remove", "os.unlink", "os.rmdir"} & called)
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not ({"subprocess", "shutil"} & imports)  # no process (so no `git init`) and no tree operations


# ------------------------------------------------------------------ execution.context

@pytest.fixture
def project(tmp_path, monkeypatch):
    ws = tmp_path / "proj"
    ws.mkdir()
    skills = ws / "skills"
    (skills / "alpha").mkdir(parents=True)
    (skills / "alpha" / "skill.yaml").write_text("name: alpha\ndescription: Alpha rules\ncategory: Framework\ntags: [a, b]\nverified: true\nverified_context: ran gate\nverified_at: '2026-10-01'\n")
    (skills / "alpha" / "rules.txt").write_text("SECRET PROPRIETARY RULE one\nSECRET PROPRIETARY RULE two\n")
    (skills / "alpha" / "instructions.md").write_text("SECRET INSTRUCTIONS\n")
    (skills / "alpha" / "staged_rules.txt").write_text("staged one\n[CONFLICT] staged two\n")
    (skills / "beta").mkdir()
    (skills / "beta" / "skill.yaml").write_text("name: beta\ndescription: Beta\n")
    (ws / "kriya.yaml").write_text("paths:\n  skills: ./skills\nskills:\n  load_global: false\n")
    monkeypatch.setattr("kriya.kup.execution.tool_visibility", lambda: [])
    return ws


def _context(ws):
    return run_kup_operation(AppConfig(), {"operation": "execution.context", "workspace": str(ws)})


def _resolved(monkeypatch, **sections):
    """Inject the RESOLVED configuration for the operator-only sections (llm, mcp, agent_llms, autonomy) a folder's own kriya.yaml cannot set; skills and
    plugins keep coming from the real file."""
    cfg = AppConfig(**sections)
    ok = {"status": "no_findings", "fields": []}
    monkeypatch.setattr("kriya.kup.context_ops._load_in", lambda workspace: (None, cfg, ok, None))
    return cfg


def test_context_lists_skills_as_metadata_only_and_never_returns_rule_text(project):
    env = _context(project)
    assert env["error"] is None
    data = env["data"]
    names = [s["name"] for s in data["skills"]["items"]]
    assert names == ["alpha", "beta"] and data["skills"]["selection"] == "automatic"
    alpha = data["skills"]["items"][0]
    assert alpha["verified"] is True and alpha["verified_context"] == "ran gate" and alpha["rule_count"] == 2 and alpha["staged_rule_count"] == 2 and alpha["has_instructions"] is True
    beta = data["skills"]["items"][1]
    assert beta["verified"] is False and beta["rule_count"] == 0
    blob = json.dumps(data)
    assert "SECRET" not in blob and "PROPRIETARY" not in blob  # rule and instruction text are never serialised
    assert "instructions" not in alpha and "rules" not in alpha and "examples" not in alpha


def test_context_is_read_only_and_restores_the_working_directory(project):
    before = _tree(project)
    cwd = os.getcwd()
    _context(project)
    assert _tree(project) == before and os.getcwd() == cwd


def test_context_never_returns_mcp_commands_arguments_environment_or_credentials(project, monkeypatch):
    _resolved(monkeypatch, paths={"skills": str(project / "skills")}, skills={"load_global": False, "load_cwd": False},
              llm={"base_url": "http://user:hunter2@localhost:11434/v1", "api_key": "sk-TOPSECRET"},
              mcp={"files": {"command": "/usr/bin/evil-binary", "args": ["--token", "ARG-SECRET"], "env": {"API_TOKEN": "ENV-SECRET"}}})
    data = _context(project)["data"]
    blob = json.dumps(data)
    for secret in ("hunter2", "sk-TOPSECRET", "evil-binary", "ARG-SECRET", "ENV-SECRET", "API_TOKEN"):
        assert secret not in blob, secret
    assert [s["name"] for s in data["tools"]["mcp_servers"]] == ["files"] and data["tools"]["health"] == "not_checked"
    assert data["routing"][0]["endpoint"] == "http://localhost:11434/v1"


def test_context_routing_shows_the_default_and_per_role_overrides(project, monkeypatch):
    _resolved(monkeypatch, paths={"skills": str(project / "skills")}, skills={"load_global": False, "load_cwd": False}, llm={"model": "dev-model"},
              agent_llms={"planner": {"llm": {"model": "plan-model", "base_url": "http://localhost:9/v1"}}})
    rows = {r["role"]: r for r in _context(project)["data"]["routing"]}
    assert rows["developer"]["model"] == "dev-model" and rows["developer"]["source"] == "default"
    assert rows["planner"]["model"] == "plan-model" and rows["planner"]["source"] == "override"
    assert rows["reviewer"]["model"] == "dev-model" and rows["reviewer"]["source"] == "default"
    assert list(rows) == ["planner", "architect", "developer", "reviewer", "run_verifier", "skill_gap", "spec_compliance"]


def test_context_for_a_folder_whose_configuration_does_not_load_is_a_typed_unavailable_not_an_error(project):
    (project / "kriya.yaml").write_text("llm: [this is not a mapping\n")
    env = _context(project)  # (relies on the preflight fix: a malformed file is a typed failure)
    assert env["error"] is None and env["data"]["available"] is False
    assert env["data"]["unavailable_reason"]["code"] == "CONFIG_LOAD_FAILED" and env["data"]["skills"] is None and env["data"]["routing"] == []


def test_context_refuses_a_missing_or_relative_workspace():
    for ws in ("/no/such/folder", "relative"):
        env = run_kup_operation(AppConfig(), {"operation": "execution.context", "workspace": ws})
        assert env["data"] is None and env["error"]["code"] in (policy.WORKSPACE_UNAVAILABLE, policy.INVALID_REQUEST)


def test_context_starts_no_process_and_imports_no_plugin(project, monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise AssertionError("execution.context must not start a process")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    _resolved(monkeypatch, paths={"skills": str(project / "skills")}, skills={"load_global": False, "load_cwd": False}, plugins={"enabled": ["core_tools"]}, mcp={"s": {"command": "/bin/true"}})
    assert _context(project)["error"] is None


# ------------------------------------------------------------------ execution.model_probe

class _Models(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):  # noqa: N802
        _Models.seen.append((self.path, self.headers.get("Authorization")))
        body = json.dumps({"data": [{"id": "present-model"}, {"id": "other"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Models.seen = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Models)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def _probe(ws):
    return run_kup_operation(AppConfig(), {"operation": "execution.model_probe", "workspace": str(ws)})


def test_probe_reports_reachability_and_model_presence_with_one_get_per_endpoint(project, server, monkeypatch):
    _resolved(monkeypatch, llm={"base_url": server, "model": "present-model", "api_key": "k1"}, agent_llms={"planner": {"llm": {"base_url": server, "model": "absent-model"}}})
    data = _probe(project)["data"]
    assert len(_Models.seen) == 1 and _Models.seen[0][0] == "/v1/models"  # one GET for the one distinct endpoint
    assert data["endpoints"][0]["state"] == "reachable" and data["endpoints"][0]["model_count"] == 2
    by = {r["for"]: r for r in data["roles"]}
    assert by["default"]["state"] == "model_present" and by["planner"]["state"] == "model_missing" and by["planner"]["available"] is False
    assert "k1" not in json.dumps(data)


def test_probe_reports_an_unreachable_server_without_raising(project, monkeypatch):
    _resolved(monkeypatch, llm={"base_url": "http://127.0.0.1:9/v1", "model": "m"})
    data = _probe(project)["data"]
    assert data["endpoints"][0]["state"] == "unreachable" and data["roles"][0]["state"] == "unreachable" and data["roles"][0]["available"] is None


def test_probe_never_contacts_or_sends_a_key_to_a_non_local_endpoint_under_local_only(project, monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise AssertionError("a non-local endpoint must not be contacted under local_only")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    _resolved(monkeypatch, llm={"base_url": "https://api.example.com/v1", "model": "m", "api_key": "sk-REMOTE"}, autonomy={"egress_policy": "local_only"})
    data = _probe(project)["data"]
    assert data["endpoints"][0]["state"] == "not_probed" and data["roles"][0]["state"] == "not_probed"
    assert "sk-REMOTE" not in json.dumps(data)


def test_probe_for_an_unloadable_configuration_is_typed_unavailable(project):
    (project / "kriya.yaml").write_text("llm: [broken\n")
    data = _probe(project)["data"]
    assert data["available"] is False and data["endpoints"] == [] and data["unavailable_reason"]["code"] == "CONFIG_LOAD_FAILED"


def test_the_new_operations_are_advertised_and_dispatchable_only_with_their_exact_arguments():
    caps = run_kup_operation(AppConfig(), {"operation": "capabilities"})["data"]
    for op in ("workspace.create", "execution.context", "execution.model_probe"):
        assert op in caps["operations"]
    assert all(caps["features"][f] is True for f in ("workspace_create", "skills_view", "tools_view", "model_probe"))
    assert serve.ARGUMENTS["execution.context"] == frozenset({"workspace"}) and serve.ARGUMENTS["execution.model_probe"] == frozenset({"workspace"})


# ------------------------------------------------------------------ bounded fuzz / property

def test_fuzzed_names_never_escape_or_create_more_than_one_child_and_never_crash(tmp_path):
    """Property over 4000 seeded random names (Unicode, controls, separators, bidi, long): the operation always answers with an envelope; when it creates, the folder is
    exactly one new directory directly inside the parent whose name is the NFC form of the input; otherwise nothing at all changed."""
    import random
    rng = random.Random(20261011)
    alphabet = list("abcXYZ019 ._-:/\\\u0000\n\t~$`'\"*?%") + ["é", "é", "プ", "日", "🙂", "‮", "​", " ", "﻿", "..", "./", "../"]
    parent = tmp_path / "p"
    parent.mkdir()
    created = 0
    for _ in range(4000):
        name = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
        before = sorted(os.listdir(parent))
        env = _create(parent, name)
        assert (env["data"] is None) != (env["error"] is None)
        after = sorted(os.listdir(parent))
        if env["error"] is None:
            created += 1
            assert len(after) == len(before) + 1 and set(before) <= set(after)
            (new,) = set(after) - set(before)
            assert new == unicodedata.normalize("NFC", name) and os.path.isdir(parent / new) and os.listdir(parent / new) == []
            assert env["data"]["workspace"] == os.path.realpath(parent / new)
        else:
            assert after == before, (name, env["error"])
    assert created > 100  # the generator does produce valid names, so the "created" branch above really ran
    assert not any(os.path.exists(p) for p in (tmp_path / "x", tmp_path / "..x"))
    # nothing was created outside the parent
    assert sorted(os.listdir(tmp_path)) == ["p"]


def test_fuzzed_requests_to_the_new_operations_always_answer_with_a_typed_envelope(tmp_path):
    import random
    rng = random.Random(7)
    values = [None, 0, 1, -1, True, "", "relative", "/", "/no/such", str(tmp_path), str(tmp_path) + "/\u0000", "x" * 5000, [], {}, ["a"], {"a": 1}, 3.5, "🙂"]
    for operation in ("workspace.create", "execution.context", "execution.model_probe"):
        for _ in range(300):
            request = {"operation": operation}
            for key in rng.sample(["parent", "name", "workspace", "extra"], rng.randint(0, 4)):
                request[key] = rng.choice(values)
            env = run_kup_operation(AppConfig(), request)
            assert (env["data"] is None) != (env["error"] is None), request
            if env["error"]:
                assert env["error"]["code"] in policy.ERROR_CODES, env["error"]


def test_a_path_too_long_for_the_filesystem_is_a_typed_refusal_not_a_crash_and_leaves_nothing(tmp_path):
    segment = "d" * 200
    deep = tmp_path
    while len(str(deep)) < 900:
        deep = deep / segment
        deep.mkdir()
    env = _create(deep, "ü" * 100)
    assert (env["data"] is None) != (env["error"] is None)
    if env["error"]:  # the platform refused: typed, with a reason, and nothing half-made
        assert env["error"]["code"] in (policy.WORKSPACE_UNAVAILABLE, policy.INVALID_REQUEST) and env["error"].get("reason") in ("create_failed", "permission_denied", "parent_not_found", "invalid_parent")
        assert os.listdir(deep) == []
    else:
        assert os.path.isdir(env["data"]["workspace"])


def test_many_skills_are_summarised_within_the_cap_and_report_the_true_total(tmp_path, monkeypatch):
    from kriya.kup import context_ops

    skills = tmp_path / "skills"
    for i in range(context_ops.MAX_SKILLS + 40):
        d = skills / f"s{i:04d}"
        d.mkdir(parents=True)
        (d / "skill.yaml").write_text(f"name: s{i:04d}\ndescription: d\n")
    cfg = AppConfig(paths={"skills": str(skills)}, skills={"load_global": False, "load_cwd": False})
    summary = context_ops.skills_summary(cfg, str(tmp_path))
    assert summary["total"] == context_ops.MAX_SKILLS + 40 and len(summary["items"]) == context_ops.MAX_SKILLS and summary["truncated"] is True
    assert [s["name"] for s in summary["items"]] == sorted(s["name"] for s in summary["items"])

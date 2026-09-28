"""PLAT-039 (ARCH-PLATFORM-001): candidate (model-directed) writes never reach
repository metadata (`.git/**`) or Kriya's own control state (`.kriya/**`).

Before the fix a real direct `generate`, with the model planning
`.kriya/policy/approved-sources.json` or `.kriya/control/runs/<id>.json`,
committed that file into the real workspace and reported SUCCESS. `.git/**`
was stopped only incidentally (a worktree's `.git` is a file). Now
AuthorizedFileWriter refuses every trusted control path under any spelling,
the terminal commit refuses one as a backstop, and Kriya's own stores write
`.kriya/` only through kriya/control/control_store.py.
"""
import json
import os

import pytest
from _chaos_harness import (
    CALC,
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    changed_paths,
    chaos_config,
    chaos_engine,
    git_workspace,
    run_direct,
    snapshot_tree,
)

from kriya.control.control_store import ControlStorePathError, write_control_file
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import AuthorizedFileWriter, is_trusted_control_path
from kriya.workflow.edit_safety import CandidateMaterializationError, read_file_revision
from kriya.workflow.terminal_commit import CandidateFile, materialize_candidate

PAYLOAD = "#!/bin/sh\necho pwned\n"
TARGETS = [
    ".kriya/policy/approved-sources.json",
    ".kriya/control/runs/forged.json",
    ".kriya/checkpoints/forged.json",
    ".git/hooks/pre-commit",
    ".git/config",
    ".GIT/hooks/post-checkout",
    ".Kriya/control/runs/forged.json",
]


@pytest.mark.parametrize("target", TARGETS)
def test_a_generation_run_never_writes_a_trusted_control_path(tmp_path, target):
    ws = git_workspace(tmp_path, {"calc.py": CALC})
    before = snapshot_tree(ws)
    design = "Design.\n```json\n" + json.dumps({"files": [target]}) + "\n```\n"

    def responder(role, request):
        if role == "architect":
            return design
        if role == "developer":
            return PAYLOAD
        return benign_roles(role, request, target=target)

    runtime = ChaosRuntime(responder)
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), f"create {target} with a hook script", ws)

    assert result.get("quality_gates_passed") is not True
    assert result.get("failure_category"), result
    assert runtime.count("developer") >= 1  # the model really proposed the write
    for root, _dirs, files in os.walk(ws):
        for name in files:
            with open(os.path.join(root, name), "rb") as handle:
                assert b"pwned" not in handle.read(), os.path.join(root, name)
    assert changed_paths(before, snapshot_tree(ws)) == []  # repository files (.git/.kriya: the walk above)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".kriya" / "control").mkdir(parents=True)
    (ws / ".git").mkdir()
    return ws


@pytest.mark.parametrize("relpath", [
    ".git", ".git/config", ".git/hooks/pre-commit", ".GIT/HOOKS/x", ".kriya", ".kriya/control/runs/x.json",
    ".KRIYA/control/x", ".kriya/new/deep/x", "sub/../.kriya/x",
])
def test_the_writer_refuses_every_trusted_control_path(workspace, relpath):
    result = AuthorizedFileWriter(str(workspace)).authorize(os.path.join(str(workspace), relpath))
    assert result.reason_code == "TRUSTED_CONTROL_PATH_DENIED"


def test_a_worktree_style_git_file_and_missing_control_dirs_are_refused(tmp_path):
    ws = tmp_path / "worktree"
    ws.mkdir()
    (ws / ".git").write_text("gitdir: /elsewhere\n")
    writer = AuthorizedFileWriter(str(ws))
    for relpath in (".git", ".git/hooks/pre-commit", ".kriya/control/x", ".Kriya/x"):
        assert writer.authorize(str(ws / relpath)).reason_code == "TRUSTED_CONTROL_PATH_DENIED", relpath


def test_a_symlink_alias_of_a_control_directory_is_refused(workspace):
    os.symlink(".kriya", workspace / "ctl")
    result = AuthorizedFileWriter(str(workspace)).authorize(str(workspace / "ctl" / "control" / "x.json"))
    assert result.reason_code == "TRUSTED_CONTROL_PATH_DENIED"


@pytest.mark.parametrize("relpath", [".github/workflows/ci.yml", ".gitignore", ".gitattributes", ".kriyarc",
                                     ".kriya-notes/x.md", "src/.git_helper.py", "docs/.kriya.md"])
def test_ordinary_look_alike_paths_are_not_control_paths(workspace, relpath):
    assert not is_trusted_control_path(str(workspace), str(workspace / relpath))
    result = AuthorizedFileWriter(str(workspace)).authorize(str(workspace / relpath))
    assert result.reason_code != "TRUSTED_CONTROL_PATH_DENIED"


def test_the_writer_refuses_before_any_byte_is_written(workspace):
    target = workspace / ".kriya" / "control" / "runs.json"
    with pytest.raises(PolicyDeniedError) as raised:
        AuthorizedFileWriter(str(workspace)).commit_file(str(target), PAYLOAD,
                                                         expected_revision=read_file_revision(str(target)))
    assert raised.value.result.reason_code == "TRUSTED_CONTROL_PATH_DENIED"
    assert not target.exists()


def test_unknown_location_counts_as_trusted(workspace, monkeypatch):
    from kriya.platform import filesystem_semantics as fs

    real_stat = os.stat
    blocked = os.path.realpath(workspace / "src")
    (workspace / "src").mkdir()

    def stat(path, *args, **kwargs):
        if os.fspath(path) == blocked:
            raise PermissionError("denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(fs.os, "stat", stat)
    assert is_trusted_control_path(str(workspace), str(workspace / "src" / "a.py"))


@pytest.mark.parametrize("relpath", [".kriya/control/runs/x.json", ".git/hooks/pre-commit", ".GIT/config"])
def test_the_terminal_commit_refuses_a_control_path_candidate(tmp_path, workspace, relpath):
    candidate = tmp_path / "candidate"
    (candidate / os.path.dirname(relpath)).mkdir(parents=True)
    (candidate / relpath).write_text(PAYLOAD)
    with pytest.raises(CandidateMaterializationError, match="trusted control path"):
        materialize_candidate(str(candidate), str(workspace), [CandidateFile(relpath, read_file_revision("/nonexistent"))])


def test_the_terminal_commit_still_materializes_ordinary_files(tmp_path, workspace):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "calc.py").write_text(CALC)
    writes = materialize_candidate(str(candidate), str(workspace), [CandidateFile("calc.py", read_file_revision("/x"))])
    assert [os.path.basename(w.target_path) for w in writes] == ["calc.py"]


def test_the_control_store_writes_only_beneath_kriya(workspace):
    inside = workspace / ".kriya" / "control" / "state.json"
    write_control_file(str(workspace), str(inside), "{}", expected_revision=read_file_revision(str(inside)))
    assert inside.read_text() == "{}"
    for outside in (workspace / "calc.py", workspace / ".kriya-evil" / "x", workspace / ".git" / "config",
                    workspace.parent / "elsewhere.json"):
        with pytest.raises(ControlStorePathError):
            write_control_file(str(workspace), str(outside), "x", expected_revision=read_file_revision(str(outside)))
        assert not outside.exists()


def test_the_control_store_refuses_an_unknown_location(workspace, monkeypatch):
    from kriya.platform import filesystem_semantics as fs

    target = workspace / ".kriya" / "control" / "state.json"
    blocked = os.path.realpath(workspace / ".kriya" / "control")
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        if os.fspath(path) == blocked:
            raise PermissionError("denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(fs.os, "stat", stat)
    with pytest.raises(ControlStorePathError):
        write_control_file(str(workspace), str(target), "{}", expected_revision=read_file_revision(str(target)))
    assert not target.exists()

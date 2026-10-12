"""BACKEND-READINESS-V2 Stage D0: can a run ever generate in the real workspace (no sandbox), and if one did, would an unsafe
Throwable/Error-masking candidate be accepted or left behind?

Production answer (TRACED + pinned here): ``create_git_worktree`` returns a sandbox path or raises; the direct path then refuses
to generate in the application workspace. The no-sandbox shape is therefore reachable only by substituting that function; the
tests below simulate exactly that substitution and pin the behaviour of every layer."""
import subprocess
from unittest.mock import patch

import pytest
from test_backend_readiness_v2_exception_masking import BAD, BASE, GOOD, PATH
from test_backend_readiness_v2_exception_masking import _end_to_end as sandboxed_end_to_end

from kriya.workflow import worktree as worktree_module


def _git_repo(path, files):
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=path, check=True, capture_output=True)


def test_create_git_worktree_never_returns_the_workspace_itself(tmp_path):
    import os

    committed = tmp_path / "committed"
    committed.mkdir()
    _git_repo(committed, {"a.txt": "x\n"})
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "a.txt").write_text("x\n")  # not a repository: bootstrapped, then still sandboxed
    for workspace in (committed, bare):
        sandbox = worktree_module.create_git_worktree(str(workspace))
        assert os.path.realpath(sandbox) != os.path.realpath(workspace)
        assert (workspace / "a.txt").read_text() == "x\n"
        assert os.path.exists(os.path.join(sandbox, "a.txt"))


def test_a_sandbox_that_cannot_be_created_is_an_error_never_a_silent_in_place_fallback(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _git_repo(workspace, {"a.txt": "x\n"})
    real_run = subprocess.run

    def refuse_worktree_add(cmd, *args, **kwargs):
        if "worktree" in cmd and "add" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(worktree_module.subprocess, "run", refuse_worktree_add)
    with pytest.raises(subprocess.CalledProcessError):
        worktree_module.create_git_worktree(str(workspace))


def _in_place_run(tmp_path, response):
    """The direct workflow with create_git_worktree substituted to return the workspace (the only way to reach no-sandbox)."""
    import kriya.workflow.workflow as workflow_module

    real_create = workflow_module.create_git_worktree

    def in_place(workspace_path):
        real_create(workspace_path)  # keep every side effect (bootstrap, ignore files)
        return workspace_path

    with patch.object(workflow_module, "create_git_worktree", new=in_place):
        return sandboxed_end_to_end(tmp_path, response)


@pytest.mark.parametrize("response", [BAD, GOOD])
def test_the_direct_path_refuses_to_generate_in_the_application_workspace(tmp_path, response):
    """A substituted in-place sandbox is a typed error before any Developer call: nothing is written, whatever the answer."""
    with pytest.raises(RuntimeError, match="Failed to create an isolated generation sandbox"):
        _in_place_run(tmp_path, response)
    assert (tmp_path / "ws" / PATH).read_text() == BASE

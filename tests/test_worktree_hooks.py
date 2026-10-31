"""SEC-001-P1 adversarial coverage: a repository-defined git hook must not
execute as a side effect of Kriya's own worktree management. Exercises the
REAL create_git_worktree()/remove_git_worktree() functions against a real
git repository with an armed, real hook script - not a helper-only unit
test of the hooks-disabled flag in isolation."""
import os
import subprocess

import pytest

from kriya.workflow.worktree import create_git_worktree, remove_git_worktree


def _arm_hook(repo_path: str, hook_name: str, sentinel_path: str) -> None:
    hooks_dir = os.path.join(repo_path, ".git", "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    hook_path = os.path.join(hooks_dir, hook_name)
    with open(hook_path, "w", encoding="utf-8") as f:
        f.write(f"#!/bin/sh\necho HOOK_RAN > {sentinel_path}\nexit 0\n")
    os.chmod(hook_path, 0o755)


def _init_repo_with_commit(repo_path: str) -> None:
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_path, capture_output=True)
    with open(os.path.join(repo_path, "a.txt"), "w") as f:
        f.write("hello")
    subprocess.run(["git", "add", "a.txt"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, capture_output=True, check=True)


def test_malicious_post_checkout_hook_does_not_fire_during_worktree_creation(tmp_path):
    repo = str(tmp_path)
    _init_repo_with_commit(repo)
    sentinel = str(tmp_path / "SENTINEL_POST_CHECKOUT_FIRED")
    _arm_hook(repo, "post-checkout", sentinel)

    # Sanity check the hook mechanism itself actually works when NOT
    # suppressed - otherwise a false negative (the hook was simply broken)
    # would make the real assertion below meaningless.
    subprocess.run(["git", "checkout", "-f", "HEAD"], cwd=repo, capture_output=True)
    assert os.path.exists(sentinel), "test setup broken: the hook itself never fires"
    os.remove(sentinel)

    worktree_path = create_git_worktree(repo)
    assert not os.path.exists(sentinel), (
        "SEC-001-P1 regression: create_git_worktree() allowed a repository-"
        "defined post-checkout hook to execute"
    )
    assert os.path.isdir(worktree_path)


def test_malicious_pre_commit_hook_does_not_fire_during_greenfield_bootstrap(tmp_path, monkeypatch):
    """_bootstrap_greenfield_repository's own bootstrap commit, exercised via create_git_worktree() on an EMPTY directory that is not a git repo yet. A hostile
    git template directory (init.templateDir / GIT_TEMPLATE_DIR) installs a pre-commit hook into the repository ``git init`` creates, i.e. BEFORE the first commit
    exists - the one way a hook can be present for the bootstrap commit. W3-3 U4: a folder that already holds ANYTHING (including a pre-seeded ``.git/hooks``) is
    refused before any git command runs (see the next test), so the empty folder is the only bootstrap that can reach the commit."""
    template = tmp_path / "template"
    sentinel = str(tmp_path / "SENTINEL_PRE_COMMIT_FIRED")
    _arm_hook(str(template), "pre-commit", sentinel)
    template_hooks = template / ".git" / "hooks"
    (template / "hooks").mkdir()
    (template_hooks / "pre-commit").rename(template / "hooks" / "pre-commit")
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))

    # Sanity check the mechanism: unsuppressed, the templated hook really fires on a first commit - otherwise the assertion below would be meaningless.
    control = tmp_path / "control"
    control.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=control, capture_output=True, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "c"], cwd=control, capture_output=True, check=True)
    assert os.path.exists(sentinel), "test setup broken: the templated hook itself never fires"
    os.remove(sentinel)

    empty = tmp_path / "empty"
    empty.mkdir()
    worktree_path = create_git_worktree(str(empty))
    assert (empty / ".git" / "hooks" / "pre-commit").exists(), "the hostile template was not applied by the bootstrap: this test would prove nothing"
    assert not os.path.exists(sentinel), (
        "SEC-001-P1 regression: greenfield bootstrap allowed a templated "
        "pre-commit hook to execute"
    )
    assert os.path.isdir(worktree_path)


def test_a_pre_seeded_git_hooks_directory_is_refused_typed_before_any_git_command_that_could_run_it(tmp_path):
    """The previous shape of the pre-commit scenario (W3-3 U4, INTENTIONAL contract change): a folder holding only a pre-seeded ``.git/hooks/pre-commit`` (no valid repository
    around it) used to take the bootstrap path. The ``.git`` entry makes the D-001 authority search find a repository Git cannot resolve - a typed ``BOUND_TREE_UNAVAILABLE``
    from the root class, ahead of the emptiness rule - so no bootstrap commit is attempted and the hook never runs."""
    from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError

    repo = str(tmp_path)
    sentinel = str(tmp_path / "SENTINEL_PRE_COMMIT_FIRED")
    _arm_hook(repo, "pre-commit", sentinel)
    with pytest.raises(BoundTreeError) as stop:
        create_git_worktree(repo)
    assert stop.value.reason_code == BOUND_TREE_UNAVAILABLE
    assert not os.path.exists(sentinel)


def test_malicious_post_checkout_hook_does_not_fire_during_worktree_removal(tmp_path):
    repo = str(tmp_path)
    _init_repo_with_commit(repo)
    worktree_path = create_git_worktree(repo)

    sentinel = str(tmp_path / "SENTINEL_REMOVE_FIRED")
    _arm_hook(repo, "post-checkout", sentinel)

    remove_git_worktree(repo, worktree_path)
    assert not os.path.exists(sentinel), (
        "SEC-001-P1 regression: remove_git_worktree() allowed a repository-"
        "defined post-checkout hook to execute"
    )

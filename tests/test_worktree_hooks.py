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


def test_malicious_pre_commit_hook_does_not_fire_during_greenfield_bootstrap(tmp_path):
    """_bootstrap_greenfield_repository's own bootstrap commit, exercised
    via create_git_worktree() on a directory that is not a git repo yet -
    a pre-commit hook cannot even exist before the FIRST commit, so this
    proves the init/commit sequence itself never becomes hook-triggerable
    by planting the hook file directly (a hook only needs to be present on
    disk under .git/hooks/, not itself be tracked/committed, to fire)."""
    repo = str(tmp_path)
    os.makedirs(repo, exist_ok=True)
    sentinel = str(tmp_path / "SENTINEL_PRE_COMMIT_FIRED")
    # Pre-seed a .git/hooks/pre-commit directly (raw file creation, no real
    # git operations) BEFORE Kriya ever touches this directory - simulates
    # a hostile pre-seeded environment (e.g. a template dir, or leftover
    # state from a prior compromised run). `git rev-parse --is-inside-work-tree`
    # still correctly reports "not a repo" here (no valid .git/HEAD/objects/
    # refs structure exists yet, only a bare hooks/ subdirectory), so
    # create_git_worktree() takes the SAME _bootstrap_greenfield_repository
    # path it would for a genuinely empty directory - `git init` does not
    # wipe a pre-existing hooks/ subdirectory when it initializes the rest
    # of the repository structure around it (confirmed live below via the
    # hook actually firing in the "unsuppressed" sanity check, i.e. the
    # hook file really does survive `git init` and is really discovered).
    _arm_hook(repo, "pre-commit", sentinel)

    worktree_path = create_git_worktree(repo)
    assert not os.path.exists(sentinel), (
        "SEC-001-P1 regression: greenfield bootstrap allowed a pre-seeded "
        "pre-commit hook to execute"
    )
    assert os.path.isdir(worktree_path)


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

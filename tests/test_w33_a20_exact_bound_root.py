"""W3-3 unit A2-0: canonical bound-tree membership is authoritative only for the EXACT Git work-tree root of the intended directory.

DIRECTORY-NAME-PRUNING-SOURCE-MEMBERSHIP-RESULT-TRUTH-001 (P2). MEASURED at 2880cba: ``bound_tree_paths(inner)`` for a non-Git workspace ``inner`` nested
inside an unrelated Git repository ``outer`` ran ``git ls-files`` with ``cwd=inner`` and returned ``outer``'s view of it: the outer ``.gitignore`` hid a
legitimate ``inner/build/Util.java`` (the enclosing repository's policy silently became the inner workspace's authority), while a standalone non-Git directory
correctly raised ``BOUND_TREE_UNAVAILABLE``. ``create_git_worktree`` already treats such a workspace as "not the repository root" (a scoped snapshot); the
membership owner now agrees: the Git top level must be the intended root, or membership is unavailable (typed, never an enclosing repository's list).

Every fixture is a real ``git init`` repository; each test asserts the Git facts it depends on BEFORE the owner is called, so a green result cannot come from a
fixture that never exercised the rule.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from test_s19e_honest_refusal import (  # noqa: E402  # pylint: disable=wrong-import-position
    _isolated_git,  # noqa: F401  # autouse fixture of that module, re-exported so it applies here
)

from kriya.repository import bound_tree
from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError, bound_tree_paths, copy_bound_tree
from kriya.repository.git import GIT_CONFIG_UNINSPECTABLE, RepositoryContentFilterRefused
from kriya.repository.snapshot import visible_repository_paths
from kriya.workflow.file_resolution import (
    _bound_workspace_files,
    find_brownfield_public_api_changes,
    find_brownfield_test_redirections,
    find_unrequested_architectural_surfaces,
)

JAVA_NEW_MAIN = "package com.acme; public class Second { public static void main(String[] args) { } }"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout


def _write(root: Path, files: dict) -> None:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text)


def _repo(parent: Path, name: str, files: dict) -> Path:
    repo = parent / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _outer_with_nested_workspace(tmp_path: Path, *, tracked_by_outer: bool = False) -> tuple:
    """``outer`` ignores ``build/``; ``outer/inner`` has NO ``.git`` and holds a legitimate ``src/A.java`` and ``build/Util.java``."""
    outer = _repo(tmp_path, "outer", {".gitignore": "build/\n", "top.txt": "outer file\n"})
    inner = outer / "inner"
    _write(outer, {"inner/src/A.java": "class A {}\n", "inner/build/Util.java": "class Util {}\n"})
    if tracked_by_outer:
        _git(outer, "add", "-f", "-A")
        _git(outer, "commit", "-q", "-m", "inner tracked by the outer repository")
    assert not (inner / ".git").exists()                                                              # validity: the inner workspace is not a repository
    return outer, inner


# ---- 1. the exact root of a real repository is a bound tree -------------------------------------------------------------------------------------

def test_the_exact_root_of_a_repository_is_bound_including_a_tracked_build_source(tmp_path):
    repo = _repo(tmp_path, "repo", {"src/A.java": "class A {}\n", "build/Util.java": "class Util {}\n"})
    assert set(_git(repo, "ls-files").split()) == {"src/A.java", "build/Util.java"}                  # validity: tracked, under a formerly pruned name
    assert bound_tree_paths(str(repo)) == {"src/A.java", "build/Util.java"}


def test_the_root_of_a_linked_worktree_is_bound(tmp_path):
    repo = _repo(tmp_path, "repo", {"src/A.java": "class A {}\n", "target/T.java": "class T {}\n"})
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "--detach", str(linked))
    assert (linked / ".git").is_file() and (linked / "target" / "T.java").is_file()                  # validity: a linked work tree, populated
    assert bound_tree_paths(str(linked)) == {"src/A.java", "target/T.java"}


def test_an_alias_of_the_exact_root_is_the_same_root(tmp_path):
    repo = _repo(tmp_path, "repo", {"src/A.java": "class A {}\n"})
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    assert alias.resolve() == repo.resolve() and str(alias) != str(repo.resolve())                   # validity: a different spelling of one directory
    assert bound_tree_paths(str(alias)) == {"src/A.java"}


def test_a_nested_repository_with_its_own_git_is_its_own_bound_tree(tmp_path):
    outer = _repo(tmp_path, "outer", {"top.txt": "outer\n"})
    inner = _repo(outer, "inner", {"src/A.java": "class A {}\n"})
    assert (inner / ".git").is_dir()
    assert bound_tree_paths(str(inner)) == {"src/A.java"}                                             # never the outer repository's list


# ---- 2. a root that is no repository, or only INSIDE one, has no bound tree ----------------------------------------------------------------------

def test_a_standalone_non_git_root_is_unavailable(tmp_path):
    plain = tmp_path / "plain"
    _write(plain, {"src/A.java": "class A {}\n"})
    with pytest.raises(BoundTreeError) as refused:
        bound_tree_paths(str(plain))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE
    assert "is not the root of a Git work tree" in str(refused.value)               # refused by the root proof itself, not by a later failing listing


def test_a_non_git_workspace_nested_in_a_repository_does_not_inherit_the_enclosing_repository(tmp_path):
    outer, inner = _outer_with_nested_workspace(tmp_path)
    assert subprocess.run(["git", "check-ignore", "-q", "inner/build/Util.java"], cwd=outer, check=False).returncode == 0     # validity: hidden by the OUTER ignore
    assert visible_repository_paths(str(inner)) == {"src/A.java"}                                    # validity: Git's own answer from inside is the outer's view
    with pytest.raises(BoundTreeError) as refused:
        bound_tree_paths(str(inner))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE
    assert str(inner) in str(refused.value) and str(outer) in str(refused.value), "the refusal names the intended root and the root Git resolved"


def test_a_nested_workspace_whose_files_the_outer_repository_tracks_is_still_not_its_own_bound_tree(tmp_path):
    outer, inner = _outer_with_nested_workspace(tmp_path, tracked_by_outer=True)
    assert "inner/src/A.java" in _git(outer, "ls-files").split()                                      # validity: the outer repository tracks the inner files
    with pytest.raises(BoundTreeError) as refused:
        bound_tree_paths(str(inner))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE


def test_a_nested_workspace_is_never_an_empty_membership_or_an_absent_file(tmp_path):
    _outer, inner = _outer_with_nested_workspace(tmp_path)
    with pytest.raises(BoundTreeError):
        _bound_workspace_files(str(inner))                                                           # an empty list would read as "no such file" and stand a guard down


@pytest.mark.parametrize("guard", [
    lambda root: find_brownfield_test_redirections(root, {"tests/test_owner.py": "from pkg import a\n"}, {"tests/test_owner.py": "from pkg import b\n", "pkg/p.py": "def b(): pass\n"}),
    lambda root: find_brownfield_public_api_changes(root, {"lib/api.py": "def old_name(a):\n    return a\n"}, {"lib/api.py": "def new_name(a):\n    return a\n"}, "fix lib"),
    lambda root: find_unrequested_architectural_surfaces(root, {}, {"src/main/java/com/acme/Second.java": JAVA_NEW_MAIN}, "fix the report formatting"),
], ids=["test-redirection", "public-api", "architectural-surface"])
def test_each_a1_guard_refuses_a_nested_workspace_instead_of_standing_down(tmp_path, guard):
    _outer, inner = _outer_with_nested_workspace(tmp_path)
    with pytest.raises(BoundTreeError) as refused:
        guard(str(inner))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE


def test_a_nested_workspace_is_not_copied_as_the_enclosing_repositorys_view(tmp_path):
    _outer, inner = _outer_with_nested_workspace(tmp_path)
    destination = tmp_path / "copy"
    with pytest.raises(BoundTreeError) as refused:
        copy_bound_tree(str(inner), str(destination))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE
    assert not (destination / "src").exists()


@pytest.mark.parametrize("failure", [OSError("no such directory"), subprocess.TimeoutExpired("git", 1),
                                     RepositoryContentFilterRefused(GIT_CONFIG_UNINSPECTABLE, "repo", detail="unparseable")],
                         ids=["os-error", "timeout", "unparseable-answer"])
def test_a_root_whose_work_tree_git_cannot_resolve_is_the_typed_refusal_not_a_raw_error(tmp_path, monkeypatch, failure):
    repo = _repo(tmp_path, "repo", {"src/A.java": "class A {}\n"})

    def cannot_resolve(_path):
        raise failure
    monkeypatch.setattr(bound_tree, "work_tree_root", cannot_resolve)
    with pytest.raises(BoundTreeError) as refused:
        bound_tree_paths(str(repo))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE and "could not be resolved" in str(refused.value)


# ---- 3. the generic listing stays generic (the overcorrection control) ----------------------------------------------------------------------------

def test_the_generic_listing_still_works_from_a_repository_subdirectory(tmp_path):
    repo = _repo(tmp_path, "repo", {"pkg/a.py": "a\n", "pkg/sub/b.py": "b\n", "top.py": "t\n"})
    assert os.path.realpath(_git(repo / "pkg", "rev-parse", "--show-toplevel").strip()) == os.path.realpath(repo)    # validity: pkg is a SUBdirectory
    assert visible_repository_paths(str(repo / "pkg")) == {"a.py", "sub/b.py"}                       # relative to the directory asked: unchanged generic behaviour

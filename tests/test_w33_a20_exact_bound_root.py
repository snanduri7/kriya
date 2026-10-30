"""W3-3 A2-0 (corrected): canonical bound-tree membership is resolved against the D-001 AUTHORITY root and projected to the OPERATING root.

History (kept): MEASURED at 2880cba, ``bound_tree_paths(inner)`` for a ``.git``-less folder nested in a repository ran ``git ls-files`` with ``cwd=inner`` and returned
the enclosing repository's view of it (the outer ``.gitignore`` hid ``inner/build/Util.java``); A2-0 (1b9f626) answered by refusing every folder that was not Git's own
top level. THAT PREMISE WAS WRONG: MEASURED at abe4650, the D-001 supported scenario "invoke Kriya from a subdirectory of the project's own repository"
(tests/test_d001_workspace_authority.py) then ended in a raw ``BoundTreeError``. Git topology is not workspace intent, and Kriya already has the one authoritative
answer: ``workspace_identity.authority_root`` (D-001, owner decision 2026-10-23) - the nearest ``.git`` entry at or above the invoked directory. A ``.git``-less folder
under a repository IS part of that repository (D-001 pins it; the only declaration of an independent repository is an own ``.git``). So:

- ``<repo>``, ``<repo>/sub``, a new empty ``<repo>/sub/dir`` and a ``.git``-less raw child share ONE authority root, and the repository's ignore rules and tracked set apply
  to every file beneath it (a hidden ``build/`` stays hidden, a force-added one is in);
- a nested repository (own ``.git``) and a linked work tree are their own authority and never see the outer repository;
- the proof is ``identity_key(git_toplevel) == identity_key(authority_root)``, never ``operating root == git_toplevel``;
- the membership handed to the caller is the OPERATING subtree, relative to it - no sibling of ``sub`` in the repository ever appears;
- a standalone non-Git root, and anything Git cannot answer or answers differently from D-001, is the typed ``BoundTreeError`` (never an empty list).

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

from kriya.context.workspace_identity import authority_root
from kriya.repository import bound_tree
from kriya.repository.bound_tree import (
    BOUND_TREE_UNAVAILABLE,
    GIT_BACKED,
    NO_GIT_REPOSITORY,
    BoundTreeError,
    bound_tree_declaration,
    bound_tree_paths,
    copy_bound_tree,
    workspace_root_class,
)
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


def _project(tmp_path: Path) -> Path:
    """One repository that ignores ``build/``: a ``sub`` subtree (tracked + untracked + an ignored output), a sibling subtree and a top-level file."""
    repo = _repo(tmp_path, "repo", {".gitignore": "build/\n", "top.txt": "t\n", "other/sibling.py": "s\n",
                                    "sub/a.py": "a\n", "sub/pkg/b.py": "b\n"})
    _write(repo, {"sub/new_untracked.py": "n\n", "sub/build/Ignored.java": "i\n", "other/new.py": "o\n"})
    assert subprocess.run(["git", "check-ignore", "-q", "sub/build/Ignored.java"], cwd=repo, check=False).returncode == 0     # validity: hidden by the repository's ignore
    return repo


SUB_MEMBERS = {"a.py", "pkg/b.py", "new_untracked.py"}             # relative to ``sub``; never ``other/...``, ``top.txt`` or the ignored ``build/...``


# ---- 1. the exact root of a real repository is a bound tree -------------------------------------------------------------------------------------

def test_the_exact_root_of_a_repository_is_bound_including_a_tracked_build_source(tmp_path):
    repo = _repo(tmp_path, "repo", {"src/A.java": "class A {}\n", "build/Util.java": "class Util {}\n"})
    assert set(_git(repo, "ls-files").split()) == {"src/A.java", "build/Util.java"}                  # validity: tracked, under a formerly pruned name
    assert bound_tree_paths(str(repo)) == {"src/A.java", "build/Util.java"}
    assert workspace_root_class(str(repo)) == GIT_BACKED


def test_the_root_of_a_linked_worktree_is_bound(tmp_path):
    repo = _repo(tmp_path, "repo", {"src/A.java": "class A {}\n", "target/T.java": "class T {}\n"})
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "--detach", str(linked))
    assert (linked / ".git").is_file() and (linked / "target" / "T.java").is_file()                  # validity: a linked work tree, populated
    assert authority_root(str(linked)) == str(linked.resolve())                                      # validity: its own authority (D-001)
    assert bound_tree_paths(str(linked)) == {"src/A.java", "target/T.java"}


def test_an_alias_of_the_exact_root_is_the_same_root(tmp_path):
    repo = _repo(tmp_path, "repo", {"src/A.java": "class A {}\n"})
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    assert alias.resolve() == repo.resolve() and str(alias) != str(repo.resolve())                   # validity: a different spelling of one directory
    assert bound_tree_paths(str(alias)) == {"src/A.java"}


def test_a_nested_repository_with_its_own_git_is_its_own_bound_tree(tmp_path):
    """The only declaration of an independent repository is an own ``.git``: the outer repository, its ignore rules and its tracked set are never consulted."""
    outer = _repo(tmp_path, "outer", {".gitignore": "src/\n", "top.txt": "outer\n"})
    inner = _repo(outer, "inner", {"src/A.java": "class A {}\n"})
    assert (inner / ".git").is_dir() and authority_root(str(inner)) == str(inner.resolve())         # validity: its own authority
    assert bound_tree_paths(str(inner)) == {"src/A.java"}                                             # never the outer repository's list, even when the outer ignores src/


# ---- 2. D-001: a subdirectory of the repository is bound to the repository's authority root, projected to the operating subtree ---------------

def test_a_subdirectory_invocation_is_bound_and_projected_to_the_operating_subtree(tmp_path):
    repo = _project(tmp_path)
    sub = repo / "sub"
    assert authority_root(str(sub)) == str(repo.resolve()) and workspace_root_class(str(sub)) == GIT_BACKED     # validity: D-001 binds sub to repo
    assert bound_tree_paths(str(sub)) == SUB_MEMBERS


def test_the_projection_never_exposes_a_sibling_or_the_authority_roots_files(tmp_path):
    repo = _project(tmp_path)
    members = bound_tree_paths(str(repo / "sub"))
    assert not {p for p in members if p.startswith(("other", "top", "../", "sub/", "build"))}, members
    assert all(not os.path.isabs(p) and ".." not in p.split("/") for p in members)
    assert all(os.path.isfile(repo / "sub" / p) for p in members)                                    # every member resolves under the OPERATING root
    assert bound_tree_paths(str(repo)) > {"top.txt", "other/sibling.py", "sub/a.py"}                 # the authority-wide set is strictly larger - and was not returned


def test_the_declaration_split_is_projected_the_same_way(tmp_path):
    repo = _project(tmp_path)
    tracked, untracked = bound_tree_declaration(str(repo / "sub"))
    assert tracked == {"a.py", "pkg/b.py"} and untracked == {"new_untracked.py"}


def test_a_new_empty_subdirectory_of_the_repository_is_a_bound_empty_tree_not_an_error(tmp_path):
    repo = _project(tmp_path)
    fresh = repo / "sub" / "deeper" / "fresh"
    fresh.mkdir(parents=True)
    assert authority_root(str(fresh)) == str(repo.resolve())                                         # validity: still the repository's authority
    assert bound_tree_paths(str(fresh)) == frozenset()                                               # an honest empty membership (nothing there), decided by Git
    _write(fresh, {"x.py": "x\n"})
    assert bound_tree_paths(str(fresh)) == {"x.py"}
    assert bound_tree_paths(str(repo / "sub" / "deeper")) == {"fresh/x.py"}


def test_a_symlink_alias_of_a_subdirectory_follows_the_canonical_authority(tmp_path):
    repo = _project(tmp_path)
    alias = tmp_path / "sub_alias"
    alias.symlink_to(repo / "sub", target_is_directory=True)
    assert authority_root(str(alias)) == str(repo.resolve())                                         # validity: the alias resolves to the repository
    assert bound_tree_paths(str(alias)) == SUB_MEMBERS


def test_a_git_less_raw_child_is_part_of_the_repository_and_the_outer_ignore_applies(tmp_path):
    """D-001 pins this (``inner`` in its SAME_AUTHORITY set): the repository's ignore rules ARE the child's - the A2-0 premise that this was an independent workspace
    was wrong. Its remedy for an operator who wants independence is the declaration D-001 recognises: ``git init`` in the folder (next test)."""
    repo = _repo(tmp_path, "outer", {".gitignore": "build/\n", "top.txt": "outer file\n"})
    inner = repo / "inner"
    _write(repo, {"inner/src/A.java": "class A {}\n", "inner/build/Util.java": "class Util {}\n"})
    assert not (inner / ".git").exists() and authority_root(str(inner)) == str(repo.resolve())
    assert subprocess.run(["git", "check-ignore", "-q", "inner/build/Util.java"], cwd=repo, check=False).returncode == 0     # validity: hidden by the OUTER ignore
    assert bound_tree_paths(str(inner)) == {"src/A.java"} == visible_repository_paths(str(inner))


def test_the_outer_repository_tracking_a_raw_child_includes_what_it_tracks(tmp_path):
    repo = _repo(tmp_path, "outer", {".gitignore": "build/\n", "top.txt": "t\n"})
    _write(repo, {"inner/src/A.java": "class A {}\n", "inner/build/Util.java": "class Util {}\n"})
    _git(repo, "add", "-f", "-A")
    _git(repo, "commit", "-q", "-m", "inner tracked by the outer repository")
    assert "inner/build/Util.java" in _git(repo, "ls-files").split()                                  # validity: force-tracked despite the ignore
    assert bound_tree_paths(str(repo / "inner")) == {"src/A.java", "build/Util.java"}
    assert bound_tree_declaration(str(repo / "inner"))[0] == {"src/A.java", "build/Util.java"}


def test_the_same_folder_with_its_own_git_becomes_independent_of_the_outer_repository(tmp_path):
    repo = _repo(tmp_path, "outer", {".gitignore": "build/\n", "top.txt": "t\n"})
    inner = repo / "inner"
    _write(repo, {"inner/src/A.java": "class A {}\n", "inner/build/Util.java": "class Util {}\n"})
    assert bound_tree_paths(str(inner)) == {"src/A.java"}                                             # before: part of the outer repository
    _git(inner, "init", "-q")
    assert authority_root(str(inner)) == str(inner.resolve())
    assert bound_tree_paths(str(inner)) == {"src/A.java", "build/Util.java"}                          # after: its own tree, the outer ignore no longer applies


# ---- 3. no Git authority, or a Git answer D-001 does not accept, is a typed refusal ----------------------------------------------------------

def test_a_standalone_non_git_root_is_unavailable(tmp_path):
    plain = tmp_path / "plain"
    _write(plain, {"src/A.java": "class A {}\n"})
    assert workspace_root_class(str(plain)) == NO_GIT_REPOSITORY
    with pytest.raises(BoundTreeError) as refused:
        bound_tree_paths(str(plain))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE
    assert "is not the root of a Git work tree" in str(refused.value)               # refused by the root proof itself, not by a later failing listing


def test_a_standalone_non_git_root_is_never_an_empty_membership_or_an_absent_file(tmp_path):
    plain = tmp_path / "plain"
    _write(plain, {"src/A.java": "class A {}\n"})
    with pytest.raises(BoundTreeError):
        _bound_workspace_files(str(plain))                                                           # an empty list would read as "no such file" and stand a guard down


def test_a_git_entry_that_is_not_a_repository_is_the_typed_refusal_not_the_no_git_class(tmp_path):
    """D-001 finds a ``.git`` entry; Git cannot resolve a work tree from it: a refusal, never "no repository, so the legacy behaviour"."""
    broken = tmp_path / "broken"
    (broken / ".git").mkdir(parents=True)
    _write(broken, {"src/A.java": "class A {}\n"})
    assert authority_root(str(broken)) == str(broken.resolve())
    with pytest.raises(BoundTreeError) as refused:
        bound_tree_paths(str(broken))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE and "cannot resolve" in str(refused.value)


def test_git_resolving_another_repository_than_the_authority_root_is_the_typed_refusal(tmp_path, monkeypatch):
    repo = _project(tmp_path)
    other = _repo(tmp_path, "other_repo", {"x.py": "x\n"})
    monkeypatch.setattr(bound_tree, "work_tree_root", lambda _path: str(other.resolve()))
    with pytest.raises(BoundTreeError) as refused:
        bound_tree_paths(str(repo / "sub"))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE
    assert str(repo.resolve()) in str(refused.value) and str(other.resolve()) in str(refused.value), "the refusal names both roots"


def test_git_resolving_a_repository_the_authority_search_did_not_find_is_the_typed_refusal(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    _write(plain, {"x.py": "x\n"})
    other = _repo(tmp_path, "other_repo", {"x.py": "x\n"})
    monkeypatch.setattr(bound_tree, "work_tree_root", lambda _path: str(other.resolve()))
    with pytest.raises(BoundTreeError) as refused:
        bound_tree_paths(str(plain))
    assert "found no repository" in str(refused.value)


@pytest.mark.parametrize("unresolvable", ["authority", "git_top"])
def test_an_identity_that_cannot_be_resolved_is_the_typed_refusal_never_an_accepted_match(tmp_path, monkeypatch, unresolvable):
    """``identity_key`` is None when a path's filesystem identity cannot be computed: two unknowns must not compare as "the same repository"."""
    repo = _project(tmp_path)
    real = bound_tree.identity_key
    resolves = {"authority": lambda path: None if path == str(repo.resolve()) else real(path),
                "git_top": lambda path: None if path == bound_tree.work_tree_root(str(repo / "sub")) else real(path)}
    monkeypatch.setattr(bound_tree, "identity_key", resolves[unresolvable])
    with pytest.raises(BoundTreeError) as refused:
        bound_tree_paths(str(repo / "sub"))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE and "unresolvable" in str(refused.value)


@pytest.mark.parametrize("guard", [
    lambda root: find_brownfield_test_redirections(root, {"tests/test_owner.py": "from pkg import a\n"}, {"tests/test_owner.py": "from pkg import b\n", "pkg/p.py": "def b(): pass\n"}),
    lambda root: find_brownfield_public_api_changes(root, {"lib/api.py": "def old_name(a):\n    return a\n"}, {"lib/api.py": "def new_name(a):\n    return a\n"}, "fix lib"),
    lambda root: find_unrequested_architectural_surfaces(root, {}, {"src/main/java/com/acme/Second.java": JAVA_NEW_MAIN}, "fix the report formatting"),
], ids=["test-redirection", "public-api", "architectural-surface"])
def test_each_a1_guard_refuses_a_standalone_non_git_workspace_instead_of_standing_down(tmp_path, guard):
    plain = tmp_path / "plain"
    _write(plain, {"keep.txt": "k\n"})
    with pytest.raises(BoundTreeError) as refused:
        guard(str(plain))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE


_OWNER = "def compute_total(items):\n    return sum(items)\n"
_JAVA_BASELINE_MAIN = "package com.acme.build; public class App { public static void main(String[] args) { } }"
_MATCHED_GUARDS = {
    "test-redirection": (
        {"pkg/build/owner.py": _OWNER, "tests/test_owner.py": "from pkg import compute_total\ndef test_a():\n    assert compute_total([1]) == 1\n"},
        lambda root: find_brownfield_test_redirections(root, {"tests/test_owner.py": "from pkg import compute_total\n"},
                                                       {"tests/test_owner.py": "from pkg import compute_total_v2\n", "pkg/parallel.py": "def compute_total_v2(items):\n    return sum(items)\n"}),
    ),
    "public-api": (
        {"lib/api.py": "def old_name(a):\n    return a\n", "consumer/build/user.py": "from lib.api import old_name\nx = old_name(1)\n"},
        lambda root: find_brownfield_public_api_changes(root, {"lib/api.py": "def old_name(a):\n    return a\n"}, {"lib/api.py": "def new_name(a):\n    return a\n"}, "fix the bug in lib"),
    ),
    "architectural-surface": (
        {"src/main/java/com/acme/build/App.java": _JAVA_BASELINE_MAIN},
        lambda root: find_unrequested_architectural_surfaces(root, {}, {"src/main/java/com/acme/Second.java": JAVA_NEW_MAIN}, "fix the report formatting"),
    ),
}


@pytest.mark.parametrize("name", sorted(_MATCHED_GUARDS))
def test_each_a1_guard_decides_a_subdirectory_run_exactly_as_it_decides_the_repository_root(tmp_path, name):
    """The matched control: the same files at a repository root and under ``repo/sub`` give the same finding, from the operating subtree's own membership
    (including tracked source under a formerly pruned name)."""
    files, guard = _MATCHED_GUARDS[name]
    at_root = _repo(tmp_path, "at_root", files)
    in_sub = _repo(tmp_path, "in_sub", {"README.md": "r\n", "other/unrelated.py": "u = 1\n", **{f"sub/{path}": text for path, text in files.items()}})
    expected = guard(str(at_root))
    assert expected, "validity: the guard fires on the matched repository root"
    assert guard(str(in_sub / "sub")) == expected


# ---- 4. copying the bound tree copies the operating subtree ------------------------------------------------------------------------------------

def test_a_subdirectory_is_copied_as_its_own_subtree_never_the_repository(tmp_path):
    repo = _project(tmp_path)
    destination = tmp_path / "copy"
    copied = copy_bound_tree(str(repo / "sub"), str(destination))
    assert copied == SUB_MEMBERS
    present = {str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()}
    assert present == SUB_MEMBERS and not (destination / "other").exists() and not (destination / "top.txt").exists()


def test_a_standalone_non_git_root_is_not_copied(tmp_path):
    plain = tmp_path / "plain"
    _write(plain, {"src/A.java": "class A {}\n"})
    destination = tmp_path / "copy"
    with pytest.raises(BoundTreeError) as refused:
        copy_bound_tree(str(plain), str(destination))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE and not (destination / "src").exists()


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


# ---- 5. the generic listing stays generic (the overcorrection control) ----------------------------------------------------------------------------

def test_the_generic_listing_still_works_from_a_repository_subdirectory(tmp_path):
    repo = _repo(tmp_path, "repo", {"pkg/a.py": "a\n", "pkg/sub/b.py": "b\n", "top.py": "t\n"})
    assert os.path.realpath(_git(repo / "pkg", "rev-parse", "--show-toplevel").strip()) == os.path.realpath(repo)    # validity: pkg is a SUBdirectory
    assert visible_repository_paths(str(repo / "pkg")) == {"a.py", "sub/b.py"}                       # relative to the directory asked: the projection the bound tree reuses

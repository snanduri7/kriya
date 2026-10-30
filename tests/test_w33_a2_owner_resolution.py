"""W3-3 unit A2: artifact-OWNER resolution decides membership from the canonical bound tree for the exact Git root, never from a directory name.

DIRECTORY-NAME-PRUNING-SOURCE-MEMBERSHIP-RESULT-TRUTH-001 (P2). MEASURED at 6062a01 on real ``git init`` repositories
(``WAVE3_W33_2026-10-30/prefix_outputs/a2_prefix.txt``): ``resolve_planned_artifact_owners`` walked the workspace pruning ``build``/``dist``/``target``/...,
so a tracked owner under ``src/build/`` was missed (fail-open: a parallel duplicate was planned), a hidden duplicate under ``build/`` left the visible one
the UNIQUE candidate (a confident WRONG redirect), and a gitignored ``out/`` file was offered as an owner (the reverse fail-open);
``discover_response_construction_owners`` had the same three faults and ``include_response_construction_owners`` widens write scope onto what it finds.

The rule: for ``EXACT_GIT_ROOT`` the candidates are the bound tree (``bound_tree_paths``) narrowed to a real file on disk; ``NO_GIT_REPOSITORY`` (the
pre-bootstrap design stage) and ``NESTED_IN_GIT_WORK_TREE`` are PARKED - OWNER UNRESOLVED and keep the legacy walk UNCHANGED (pinned below as "unchanged", not
as correct); a root git cannot ANSWER for is the typed ``BoundTreeError`` and never the legacy walk.

Every fixture is a real ``git init`` repository and each test asserts the Git facts it relies on BEFORE calling the owner, so a green result cannot come from
a fixture that never exercised the rule.
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
from kriya.repository.bound_tree import (
    BOUND_TREE_UNAVAILABLE,
    EXACT_GIT_ROOT,
    NESTED_IN_GIT_WORK_TREE,
    NO_GIT_REPOSITORY,
    BoundTreeError,
    bound_tree_paths,
    workspace_root_class,
)
from kriya.repository.git import GIT_CONFIG_UNINSPECTABLE, RepositoryContentFilterRefused
from kriya.workflow.file_resolution import (
    FILE_RESOLUTION_REDIRECT_OUT_OF_SCOPE,
    discover_response_construction_owners,
    include_response_construction_owners,
    prefer_existing_artifact_owners,
    resolve_planned_artifact_owners,
)

SERVICE = "CustomerAccountService.java"
PLANNED = f"src/other/{SERVICE}"
GOAL = "update customer account"
RESPONSE_GOAL = "add a field to the customer response"
CONTROLLER_BODY = "m.put(a, b);\n"
DIRECTORY_NAMES = ["build", "dist", "target"]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout


def _write(root: Path, files: dict) -> None:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text)


def _repo(parent: Path, files: dict, *, name: str = "repo", commit: bool = True) -> Path:
    repo = parent / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _write(repo, files)
    _git(repo, "add", "-A")
    if commit:
        _git(repo, "commit", "-q", "-m", "base")
    return repo


def _tracked(repo: Path) -> set:
    return set(_git(repo, "ls-files").split("\n")) - {""}


# ---- 1. the root classification: the one owner of the dispatch ------------------------------------------------------------------------------------

def test_the_three_root_classes_are_told_apart(tmp_path):
    exact = _repo(tmp_path, {"a.txt": "a\n"}, name="exact")
    nested = exact / "inner"
    nested.mkdir()
    plain = tmp_path / "plain"
    plain.mkdir()
    assert os.path.realpath(_git(nested, "rev-parse", "--show-toplevel").strip()) == os.path.realpath(exact)    # validity: git DOES resolve the enclosing repository
    assert workspace_root_class(str(exact)) == EXACT_GIT_ROOT
    assert workspace_root_class(str(nested)) == NESTED_IN_GIT_WORK_TREE
    assert workspace_root_class(str(plain)) == NO_GIT_REPOSITORY


def test_a_repository_git_refuses_is_a_typed_refusal_not_the_no_git_class(tmp_path):
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    answer = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=bare, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert answer.returncode != 0 and "not a git repository" not in answer.stderr.lower()    # validity: git answers, and it is NOT the plain "not a repository"
    with pytest.raises(BoundTreeError) as refused:
        workspace_root_class(str(bare))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE
    with pytest.raises(BoundTreeError):
        prefer_existing_artifact_owners([PLANNED], GOAL, str(bare))                           # the owner resolver does not take the legacy walk for it


@pytest.mark.parametrize("failure", [OSError("no such directory"), subprocess.TimeoutExpired("git", 1),
                                     RepositoryContentFilterRefused(GIT_CONFIG_UNINSPECTABLE, "repo", detail="unparseable")],
                         ids=["os-error", "timeout", "unparseable-answer"])
@pytest.mark.parametrize("probe", ["work_tree_root", "is_plainly_not_a_repository"])
def test_a_root_git_cannot_answer_for_is_typed_and_never_falls_through_to_the_legacy_walk(tmp_path, monkeypatch, failure, probe):
    plain = tmp_path / "plain"
    plain.mkdir()
    _write(plain, {f"src/build/{SERVICE}": "class X {}\n"})
    if probe == "is_plainly_not_a_repository":
        monkeypatch.setattr(bound_tree, "work_tree_root", lambda _path: None)

    def cannot_answer(_path):
        raise failure
    monkeypatch.setattr(bound_tree, probe, cannot_answer)
    for call in (lambda: resolve_planned_artifact_owners([PLANNED], GOAL, str(plain)),
                 lambda: discover_response_construction_owners(str(plain), RESPONSE_GOAL)):
        with pytest.raises(BoundTreeError) as refused:
            call()
        assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE and "could not be resolved" in str(refused.value)


# ---- 2. resolve_planned_artifact_owners on the exact Git root ------------------------------------------------------------------------------------

@pytest.mark.parametrize("directory", DIRECTORY_NAMES + ["core"])
def test_a_tracked_owner_under_an_output_looking_directory_is_found(tmp_path, directory):
    """``core`` is the matched control: it always worked. The three output-looking names were pruned (fail-open: a parallel duplicate was planned)."""
    owner = f"src/{directory}/{SERVICE}"
    repo = _repo(tmp_path, {owner: "class X {}\n"})
    assert owner in _tracked(repo)
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo))
    assert result.resolved == [owner] and result.refused_redirects == [] and result.new_artifacts_kept == []


@pytest.mark.parametrize("directory", DIRECTORY_NAMES)
def test_a_duplicate_owner_under_an_output_looking_directory_is_not_hidden(tmp_path, directory):
    """Pre-fix the hidden copy left ``src/util`` the UNIQUE exact-name candidate: a confident WRONG redirect. Two owners are ambiguous: the plan is kept."""
    repo = _repo(tmp_path, {f"src/{directory}/{SERVICE}": "class X {}\n", f"src/util/{SERVICE}": "class X {}\n"})
    assert {f"src/{directory}/{SERVICE}", f"src/util/{SERVICE}"} <= _tracked(repo)
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(repo)).resolved == [PLANNED]


def test_the_matched_duplicate_control_under_two_ordinary_directories_is_also_ambiguous(tmp_path):
    repo = _repo(tmp_path, {f"src/core/{SERVICE}": "class X {}\n", f"src/util/{SERVICE}": "class X {}\n"})
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(repo)).resolved == [PLANNED]


@pytest.mark.parametrize("ignored_directory", ["out", "build", "generated"])
def test_a_gitignored_output_is_not_a_member_whatever_its_name(tmp_path, ignored_directory):
    """The reverse fail-open: pre-fix ``out/`` and ``generated/`` (not in the prune set) were offered as owners; ``build/`` (pruned) is the matched control."""
    repo = _repo(tmp_path, {".gitignore": f"{ignored_directory}/\n", "README.md": "r\n"})
    _write(repo, {f"{ignored_directory}/{SERVICE}": "class X {}\n"})
    assert (repo / ignored_directory / SERVICE).exists() and f"{ignored_directory}/{SERVICE}" not in _tracked(repo)
    assert _git(repo, "status", "--porcelain", "--untracked-files=all").count(ignored_directory) == 0       # validity: git itself does not list it
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(repo)).resolved == [PLANNED]


def test_an_untracked_but_not_ignored_source_is_a_member_but_cannot_redirect_automatically(tmp_path):
    """Membership (Git's view) still lists it; the responsibility-owner eligibility contract (test_w33_a2b_owner_eligibility.py) keeps it from redirecting."""
    repo = _repo(tmp_path, {"README.md": "r\n"})
    _write(repo, {f"src/build/{SERVICE}": "class X {}\n"})
    assert f"src/build/{SERVICE}" not in _tracked(repo)
    assert f"src/build/{SERVICE}" in bound_tree_paths(str(repo))
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(repo)).resolved == [PLANNED]


def test_a_tracked_path_deleted_from_the_work_tree_is_not_an_owner_a_redirect_may_name(tmp_path):
    """The bound tree still lists it; nothing is on disk. The old walk never saw it, so naming it would be a new false positive."""
    repo = _repo(tmp_path, {f"src/svc/{SERVICE}": "class X {}\n"})
    (repo / "src" / "svc" / SERVICE).unlink()
    assert f"src/svc/{SERVICE}" in _tracked(repo) and not (repo / "src" / "svc" / SERVICE).exists()
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(repo)).resolved == [PLANNED]


def test_a_gitlink_directory_entry_is_not_an_owner(tmp_path):
    """A real Git submodule entry (mode 160000) is a bound path that is a DIRECTORY on disk: never a file candidate."""
    repo = _repo(tmp_path, {"README.md": "r\n"})
    commit = _git(repo, "rev-parse", "HEAD").strip()
    entry = f"src/svc/{SERVICE}"
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{commit},{entry}")
    (repo / "src" / "svc" / SERVICE).mkdir(parents=True)
    assert entry in _tracked(repo) and (repo / entry).is_dir()                               # validity: bound AND a directory
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(repo)).resolved == [PLANNED]


def test_a_planned_path_that_exists_on_disk_is_kept_even_when_gitignored(tmp_path):
    """The existence checks stay a FILESYSTEM question ("would this write collide with a file that is there?"), not a membership question."""
    repo = _repo(tmp_path, {".gitignore": "build/\n", f"src/svc/{SERVICE}": "class X {}\n"})
    _write(repo, {f"build/{SERVICE}": "class X {}\n"})
    assert f"build/{SERVICE}" not in _tracked(repo) and (repo / "build" / SERVICE).exists()
    assert resolve_planned_artifact_owners([f"build/{SERVICE}"], GOAL, str(repo)).resolved == [f"build/{SERVICE}"]


def test_the_candidates_are_decided_deterministically_whatever_the_listing_order(tmp_path):
    repo = _repo(tmp_path, {f"src/b/{SERVICE}": "class X {}\n", f"src/a/{SERVICE}": "class X {}\n", "README.md": "r\n"})
    first = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo))
    second = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo))
    assert first == second and first.resolved == [PLANNED]


# ---- 3. the result contract on every reporting path (success, refusal, explicit new) ------------------------------------------------------------

def test_the_scope_refusal_is_reported_for_an_owner_found_under_an_output_looking_directory(tmp_path):
    owner = f"src/build/{SERVICE}"
    repo = _repo(tmp_path, {owner: "class X {}\n"})
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo), authorized_paths=["src/other/Else.java"])
    assert result.resolved == [PLANNED] and result.new_artifacts_kept == []
    assert result.refused_redirects == [{"planned": PLANNED, "owner": owner, "tier": "exact_name", "reason_code": FILE_RESOLUTION_REDIRECT_OUT_OF_SCOPE}]


def test_an_authorized_owner_under_an_output_looking_directory_is_redirected_and_reported(tmp_path):
    owner = f"src/build/{SERVICE}"
    repo = _repo(tmp_path, {owner: "class X {}\n"})
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo), authorized_paths=[owner])
    assert result.resolved == [owner] and result.refused_redirects == []


def test_an_explicit_new_artifact_is_never_redirected_onto_an_owner_under_an_output_looking_directory(tmp_path):
    repo = _repo(tmp_path, {f"src/build/{SERVICE}": "class X {}\n"})
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo), new_artifact_paths=[PLANNED])
    assert result.resolved == [PLANNED] and result.new_artifacts_kept == [PLANNED] and result.refused_redirects == []


# ---- 4. discover / include_response_construction_owners -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("directory", DIRECTORY_NAMES + ["web"])
def test_a_tracked_controller_under_an_output_looking_directory_is_discovered(tmp_path, directory):
    controller = f"src/{directory}/CustomerController.java"
    repo = _repo(tmp_path, {controller: CONTROLLER_BODY})
    assert controller in _tracked(repo)
    assert discover_response_construction_owners(str(repo), RESPONSE_GOAL) == [controller]
    assert include_response_construction_owners(["src/Planned.java"], RESPONSE_GOAL, str(repo)) == ["src/Planned.java", controller]


def test_a_gitignored_controller_is_not_discovered_so_a_write_scope_is_not_widened_onto_it(tmp_path):
    repo = _repo(tmp_path, {".gitignore": "out/\n", "README.md": "r\n"})
    _write(repo, {"out/CustomerController.java": CONTROLLER_BODY})
    assert "out/CustomerController.java" not in _tracked(repo)
    assert discover_response_construction_owners(str(repo), RESPONSE_GOAL) == []
    assert include_response_construction_owners(["src/Planned.java"], RESPONSE_GOAL, str(repo)) == ["src/Planned.java"]


def test_the_negative_intent_gate_and_the_new_artifact_gate_still_decide_before_any_membership_read(tmp_path):
    repo = _repo(tmp_path, {"src/web/CustomerController.java": CONTROLLER_BODY})
    assert discover_response_construction_owners(str(repo), "do not change the customer response") == []
    assert include_response_construction_owners(["src/NewCustomerThing.java"], "create a new CustomerThing and add a customer response field",
                                                str(repo)) == ["src/NewCustomerThing.java"]


# ---- 5. the PARKED root classes: behaviour UNCHANGED (pinned as unchanged, NOT as correct) ----------------------------------------------------------

def test_parked_no_git_root_keeps_the_legacy_walk_unchanged(tmp_path):
    """PARKED - OWNER UNRESOLVED (A0.2): the pre-bootstrap design stage has no canonical file set and a typed refusal would break the normal greenfield path.
    Pinned only so that a change to this class is a deliberate owner decision; the legacy basename prune is NOT endorsed."""
    plain = tmp_path / "plain"
    plain.mkdir()
    _write(plain, {f"src/build/{SERVICE}": "class X {}\n", "src/core/Other.java": "class O {}\n"})
    assert workspace_root_class(str(plain)) == NO_GIT_REPOSITORY
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(plain)).resolved == [PLANNED]                 # build/ still pruned
    _write(plain, {f"src/util/{SERVICE}": "class X {}\n"})
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(plain)).resolved == [f"src/util/{SERVICE}"]    # filesystem walk, unchanged


def test_parked_nested_root_keeps_the_legacy_walk_unchanged_and_never_reads_the_enclosing_repository(tmp_path):
    """PARKED - OWNER UNRESOLVED (NESTED-RAW-WORKSPACE-BOUND-TREE-AUTHORITY-OWNER-001): the enclosing repository's ignore rules / tracked set must not
    become the nested folder's membership (that would be the A2-0 defect again); the legacy walk is kept unchanged until the owner decides."""
    outer = _repo(tmp_path, {".gitignore": "ignored_by_outer/\n", "top.txt": "t\n"}, name="outer")
    inner = outer / "inner"
    _write(inner, {f"src/ignored_by_outer/{SERVICE}": "class X {}\n"})
    assert workspace_root_class(str(inner)) == NESTED_IN_GIT_WORK_TREE
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(inner)).resolved == [f"src/ignored_by_outer/{SERVICE}"]   # the outer .gitignore did NOT hide it

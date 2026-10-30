"""W3-3 A2 follow-on: RESPONSIBILITY-OWNER ELIGIBILITY - repository membership does not decide who may claim an existing responsibility.

UNTRACKED-DEPENDENCY-DIRECTORY-OWNER-SELECTION-001. MEASURED at a4079ee (``probe_untracked_dependency_owner.py``): once owner candidates came from the Git bound tree, an
untracked, non-ignored ``node_modules``/``.venv``/``venv``/``vendor``/``third_party``/``.tox``/``site-packages`` file could redirect a planned path (the Developer-stage
rewrite included) and widen ``architect_files`` in every UNRESTRICTED run. No existing signal separates a project's own uncommitted source from an unignored dependency
tree (``probe_graph_vs_vendored_source.py``: reference edges identical; ``prototype_owner_eligibility_OFFLINE.py``: G/H cases structurally identical), so none of them is
authority. The contract (owner decision): membership unchanged; a candidate is DECLARED (Git tracked, or proven written by the CURRENT run) or AMBIGUOUS (a pre-existing
untracked file). Only DECLARED may redirect a planned path, become a response-construction owner or widen ``architect_files`` in an unrestricted run; an AMBIGUOUS one is
reported (``FILE_RESOLUTION_OWNER_UNDECLARED``), never applied. Scoped runs, the writer and the filesystem-collision check are unchanged; the parked root classes too.

Every fixture is a real ``git init`` repository and asserts the Git facts it relies on BEFORE calling the owner.
"""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest
from test_s19e_honest_refusal import (  # noqa: E402  # pylint: disable=wrong-import-position
    _isolated_git,  # noqa: F401  # autouse fixture of that module, re-exported so it applies here
)
from test_w33_a2_owner_resolution import (  # noqa: E402  # pylint: disable=wrong-import-position
    CONTROLLER_BODY,
    GOAL,
    PLANNED,
    RESPONSE_GOAL,
    SERVICE,
    _repo,
    _tracked,
    _write,
)

from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.repository import bound_tree, snapshot
from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError, bound_tree_declaration, bound_tree_paths
from kriya.repository.snapshot import repository_paths_by_declaration, visible_repository_paths
from kriya.workflow.file_resolution import (
    FILE_RESOLUTION_OWNER_UNDECLARED,
    FILE_RESOLUTION_REDIRECT_OUT_OF_SCOPE,
    discover_response_construction_owners,
    include_response_construction_owners,
    resolve_planned_artifact_owners,
    undeclared_response_construction_owners,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
AMBIGUOUS_DIRECTORIES = ["node_modules/pkg", ".venv/lib", "venv/lib", "vendor/lib", "third_party/x", ".tox/py", "envs/py311/lib/site-packages", "deps_cache_9/x"]
SIBLING = "src/core/Other.java"                       # a tracked file: the repository is not anchorless


def _untracked_owner(tmp_path, directory, *, tracked=None):
    """A repository whose ONLY candidate owner is an untracked non-ignored file under ``directory``."""
    repo = _repo(tmp_path, tracked if tracked is not None else {SIBLING: "class O {}\n"})
    owner = f"{directory}/{SERVICE}"
    _write(repo, {owner: "class X {}\n"})
    assert owner in bound_tree_paths(str(repo)) and owner not in _tracked(repo)             # validity: a MEMBER, and untracked
    return repo, owner


# ---- 1. the tracked split: ONE listing, the union is the membership ---------------------------------------------------------------------------------

def test_the_split_is_exactly_membership_from_one_listing(tmp_path, monkeypatch):
    repo = _repo(tmp_path, {"a.py": "a\n", "pkg/b.py": "b\n"})
    _write(repo, {"new.py": "n\n", "pkg/new2.py": "n\n"})
    _write(repo, {".gitignore": "ignored/\n"})
    _write(repo, {"ignored/x.py": "x\n"})
    tracked, untracked = repository_paths_by_declaration(str(repo))
    assert tracked == {"a.py", "pkg/b.py"} and untracked == {"new.py", "pkg/new2.py", ".gitignore"}
    assert tracked | untracked == visible_repository_paths(str(repo)) == bound_tree_paths(str(repo))
    assert not tracked & untracked and "ignored/x.py" not in tracked | untracked
    calls = []
    real = subprocess.run

    def counting(*args, **kwargs):
        calls.append(args[0])
        return real(*args, **kwargs)
    monkeypatch.setattr(snapshot.subprocess, "run", counting)
    repository_paths_by_declaration(str(repo))
    assert len(calls) == 1 and "ls-files" in calls[0]                                      # one Git scan, not two


def test_the_split_keeps_kriya_state_out_and_refuses_an_unparseable_listing(tmp_path, monkeypatch):
    repo = _repo(tmp_path, {"a.py": "a\n"})
    _write(repo, {".kriya/state.json": "{}\n"})
    assert ".kriya/state.json" not in {p for part in repository_paths_by_declaration(str(repo)) for p in part}

    class Done:
        returncode, stderr, stdout = 0, b"", b"garbage-without-tag\0"
    monkeypatch.setattr(snapshot.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(RuntimeError, match="unparseable"):
        repository_paths_by_declaration(str(repo))


def test_the_bound_tree_declaration_keeps_the_authority_root_proof_and_the_typed_refusal(tmp_path):
    outer = _repo(tmp_path, {"top.txt": "t\n"}, name="outer")
    inner = outer / "inner"
    _write(inner, {"a.txt": "a\n"})
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(BoundTreeError) as refused:
        bound_tree_declaration(str(plain))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE
    assert bound_tree_declaration(str(inner)) == (frozenset(), frozenset({"a.txt"}))                  # a .git-less child: the OUTER repository's view, projected to inner
    tracked, untracked = bound_tree_declaration(str(outer))
    assert tracked == {"top.txt"} and untracked == {"inner/a.txt"}
    assert bound_tree.bound_tree_paths(str(outer)) == tracked | untracked


# ---- 2. the redirect decision in an UNRESTRICTED run ----------------------------------------------------------------------------------------

def test_a_tracked_legitimate_owner_redirects(tmp_path):                                      # characterization 1
    owner = f"src/core/{SERVICE}"
    repo = _repo(tmp_path, {owner: "class X {}\n"})
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo))
    assert result.resolved == [owner] and result.undeclared_owner_indications == [] and result.refused_redirects == []


@pytest.mark.parametrize("directory", ["build", "dist", "target"])
def test_a_tracked_owner_under_an_output_looking_name_still_redirects(tmp_path, directory):  # characterization 8
    owner = f"src/{directory}/{SERVICE}"
    repo = _repo(tmp_path, {owner: "class X {}\n"})
    assert owner in _tracked(repo)
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(repo)).resolved == [owner]


def test_an_untracked_owner_written_by_the_current_run_redirects(tmp_path):                  # characterization 2
    repo, owner = _untracked_owner(tmp_path, "src/core")
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(repo)).resolved == [PLANNED]                 # without provenance: ambiguous
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo), declared_paths={owner})
    assert result.resolved == [owner] and result.undeclared_owner_indications == []


def test_a_legitimate_preexisting_untracked_owner_is_ambiguous_and_reported_not_applied(tmp_path):  # characterization 3
    repo, owner = _untracked_owner(tmp_path, "src/core")                                      # a tracked sibling in the SAME directory: still ambiguous
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo))
    assert result.resolved == [PLANNED] and result.refused_redirects == [] and result.new_artifacts_kept == []
    assert result.undeclared_owner_indications == [{"planned": PLANNED, "owner": owner, "tier": "exact_name", "reason_code": FILE_RESOLUTION_OWNER_UNDECLARED}]


def test_an_anchorless_brownfield_repository_redirects_nothing_automatically(tmp_path):      # characterization 4 (no UNKNOWN-eligible state)
    repo = _repo(tmp_path, {}, commit=False)
    _write(repo, {f"src/core/{SERVICE}": "class X {}\n", "src/core/Other.java": "class O {}\n"})
    assert _tracked(repo) == set()                                                           # validity: nothing is tracked
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo))
    assert result.resolved == [PLANNED]
    assert [item["owner"] for item in result.undeclared_owner_indications] == [f"src/core/{SERVICE}"]


def test_an_ignored_file_is_neither_a_candidate_nor_an_indication(tmp_path):                  # characterization 5
    repo = _repo(tmp_path, {".gitignore": "out/\n", SIBLING: "class O {}\n"})
    _write(repo, {f"out/{SERVICE}": "class X {}\n"})
    assert f"out/{SERVICE}" not in bound_tree_paths(str(repo))
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo), declared_paths={f"out/{SERVICE}"})     # even a (wrong) provenance claim
    assert result.resolved == [PLANNED] and result.undeclared_owner_indications == []                         # membership first


@pytest.mark.parametrize("directory", AMBIGUOUS_DIRECTORIES)
def test_an_untracked_dependency_or_environment_tree_cannot_redirect_by_any_name(tmp_path, directory):   # characterizations 6 and 7
    repo, owner = _untracked_owner(tmp_path, directory)
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo))
    assert result.resolved == [PLANNED]
    assert [(i["owner"], i["reason_code"]) for i in result.undeclared_owner_indications] == [(owner, FILE_RESOLUTION_OWNER_UNDECLARED)]


def test_a_declared_owner_still_decides_when_an_ambiguous_one_also_matches(tmp_path):
    declared = f"src/core/{SERVICE}"
    repo = _repo(tmp_path, {declared: "class X {}\n"})
    _write(repo, {f"vendor/lib/{SERVICE}": "class V {}\n"})
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo))
    assert result.resolved == [declared]                                                      # a vendored same-named copy neither blocks nor steals the redirect


def test_a_declared_owner_decides_even_when_an_ambiguous_one_wins_a_higher_tier(tmp_path):
    """Declared D matches by containment, ambiguous A by exact name (a higher tier): over every Git-visible file A would be the unique answer. A is only
    reported; the redirect is D's - presence on disk never vetoes, steals or blocks a declared owner."""
    declared = "src/core/CustomerAccountServiceImpl.java"
    repo = _repo(tmp_path, {declared: "class X {}\n"})
    ambiguous = f"vendor/lib/{SERVICE}"
    _write(repo, {ambiguous: "class V {}\n"})
    result = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo))
    assert result.resolved == [declared]
    assert [(i["owner"], i["tier"]) for i in result.undeclared_owner_indications] == [(ambiguous, "exact_name")]


def test_an_exact_planned_path_collision_with_an_untracked_file_is_still_a_filesystem_question(tmp_path):   # characterization 9
    repo, owner = _untracked_owner(tmp_path, "node_modules/pkg")
    assert resolve_planned_artifact_owners([owner], GOAL, str(repo)).resolved == [owner]
    assert resolve_planned_artifact_owners([owner], GOAL, str(repo)).undeclared_owner_indications == []


def test_provenance_is_path_exact_and_cannot_promote_a_neighbour(tmp_path):
    repo, owner = _untracked_owner(tmp_path, "src/core")
    other = f"src/core/{SERVICE}.bak"
    _write(repo, {other: "x\n"})
    for wrong in (other, f"src/core/{SERVICE.lower()}", "src/core", f"src/{SERVICE}", "src/core/"):
        assert resolve_planned_artifact_owners([PLANNED], GOAL, str(repo), declared_paths={wrong}).resolved == [PLANNED], wrong
    assert resolve_planned_artifact_owners([PLANNED], GOAL, str(repo), declared_paths={"./" + owner}).resolved == [owner]   # the same path spelled differently


# ---- 3. SCOPED runs are unchanged (the typed scope refusal and the writer own the decision) ---------------------------------------------------

def test_a_scoped_run_keeps_every_git_visible_candidate_and_reports_no_indication(tmp_path):                # characterization 10
    repo, owner = _untracked_owner(tmp_path, "node_modules/pkg")
    allowed = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo), authorized_paths=[owner])
    assert allowed.resolved == [owner] and allowed.undeclared_owner_indications == [] and allowed.refused_redirects == []
    refused = resolve_planned_artifact_owners([PLANNED], GOAL, str(repo), authorized_paths=["src/other/Else.java"])
    assert refused.resolved == [PLANNED] and refused.undeclared_owner_indications == []
    assert refused.refused_redirects == [{"planned": PLANNED, "owner": owner, "tier": "exact_name", "reason_code": FILE_RESOLUTION_REDIRECT_OUT_OF_SCOPE}]


def test_the_unrestricted_writer_is_unchanged_this_slice_changes_selection_not_permission(tmp_path):         # characterization 11
    workspace = os.path.realpath(tmp_path)
    for relative in ("node_modules/pkg/Svc.java", ".venv/lib/Svc.py", "vendor/lib/Svc.java"):
        assert AuthorizedFileWriter(workspace).authorize(os.path.join(workspace, relative)).decision.name == "ALLOW"
    scoped = AuthorizedFileWriter(workspace, allowed_relpaths=["src/core/Svc.java"])
    assert scoped.authorize(os.path.join(workspace, "node_modules/pkg/Svc.java")).decision.name == "DENY"


# ---- 4. response-construction owners: widening architect_files ------------------------------------------------------------------------------

def _controller_repo(tmp_path, directory, *, tracked):
    path = f"{directory}/CustomerController.java"
    if tracked:
        repo = _repo(tmp_path, {path: CONTROLLER_BODY})
        assert path in _tracked(repo)
    else:
        repo = _repo(tmp_path, {SIBLING: "class O {}\n"})
        _write(repo, {path: CONTROLLER_BODY})
        assert path in bound_tree_paths(str(repo)) and path not in _tracked(repo)
    return repo, path


@pytest.mark.parametrize("directory", ["src/web", "src/build", "src/dist", "src/target"])
def test_a_tracked_controller_is_discovered_and_added(tmp_path, directory):
    repo, path = _controller_repo(tmp_path, directory, tracked=True)
    assert discover_response_construction_owners(str(repo), RESPONSE_GOAL) == [path]
    assert include_response_construction_owners(["src/Planned.java"], RESPONSE_GOAL, str(repo)) == ["src/Planned.java", path]
    assert undeclared_response_construction_owners(str(repo), RESPONSE_GOAL) == []


@pytest.mark.parametrize("directory", ["src/web", "node_modules/x", ".venv/lib", "vendor/lib", "third_party/x", ".tox/py", "deps_cache_9/x"])
def test_an_untracked_controller_is_reported_never_added_in_an_unrestricted_run(tmp_path, directory):
    repo, path = _controller_repo(tmp_path, directory, tracked=False)
    assert discover_response_construction_owners(str(repo), RESPONSE_GOAL) == []
    assert include_response_construction_owners(["src/Planned.java"], RESPONSE_GOAL, str(repo)) == ["src/Planned.java"]
    assert undeclared_response_construction_owners(str(repo), RESPONSE_GOAL) == [path]         # still available as evidence


def test_an_untracked_controller_written_by_the_current_run_is_added(tmp_path):
    repo, path = _controller_repo(tmp_path, "src/web", tracked=False)
    assert discover_response_construction_owners(str(repo), RESPONSE_GOAL, declared_paths={path}) == [path]
    assert undeclared_response_construction_owners(str(repo), RESPONSE_GOAL, declared_paths={path}) == []


def test_a_scoped_run_keeps_the_response_owner_behaviour_unchanged(tmp_path):
    repo, path = _controller_repo(tmp_path, "node_modules/x", tracked=False)
    assert discover_response_construction_owners(str(repo), RESPONSE_GOAL, authorized_paths=[path]) == [path]
    assert include_response_construction_owners(["src/Planned.java"], RESPONSE_GOAL, str(repo), authorized_paths=["src/Planned.java"]) == ["src/Planned.java", path]
    assert undeclared_response_construction_owners(str(repo), RESPONSE_GOAL, authorized_paths=["src/Planned.java"]) == []


def test_the_intent_and_new_artifact_gates_still_decide_before_any_membership_read(tmp_path):
    repo, _path = _controller_repo(tmp_path, "src/web", tracked=True)
    assert discover_response_construction_owners(str(repo), "do not change the customer response") == []
    assert undeclared_response_construction_owners(str(repo), "do not change the customer response") == []


# ---- 5. membership is NOT a classification: no name list decides declaration -----------------------------------------------------------------

def test_no_directory_name_list_decides_declaration():
    """A rename of the old blacklist must fail: the eligibility function reads no directory name at all."""
    source = (REPO_ROOT / "kriya/workflow/file_resolution.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_owner_candidates")
    literals = {node.value for node in ast.walk(function) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert not literals & {"node_modules", ".venv", "venv", "build", "dist", "target", "vendor", "third_party", ".tox", "site-packages"}
    names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
    assert "_PARKED_ROOT_PRUNED_DIRECTORY_NAMES" not in names


# ---- 6. caller wiring: the Developer stage passes the run's writes, the design stage does not and keeps the scope on the widening -----------------

def _calls(path: str, name: str):
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and (getattr(node.func, "id", None) == name or getattr(node.func, "attr", None) == name or any(
                getattr(arg, "id", None) == name for arg in node.args))]


def test_the_developer_stage_declares_exactly_the_files_this_run_wrote():
    (call,) = _calls("kriya/workflow/attempt.py", "resolve_planned_artifact_owners")
    keywords = {kw.arg: ast.unparse(kw.value) for kw in call.keywords}
    assert keywords["declared_paths"] == "state.all_files_written"
    assert "established_files" not in ast.unparse(call)                                         # path-keyed, persisted and plan-tainted: NOT provenance


def test_the_design_stage_uses_tracked_declaration_only_and_scopes_the_widening():
    (resolve,) = _calls("kriya/workflow/stage_architecture.py", "resolve_planned_artifact_owners")
    assert "declared_paths" not in {kw.arg for kw in resolve.keywords}                           # all_files_written is empty at the design stage: not used
    (widen,) = _calls("kriya/workflow/stage_architecture.py", "include_response_construction_owners")
    assert {kw.arg: ast.unparse(kw.value) for kw in widen.keywords}["authorized_paths"] == "_artifact_scope"
    (indicate,) = _calls("kriya/workflow/stage_architecture.py", "undeclared_response_construction_owners")
    assert {kw.arg: ast.unparse(kw.value) for kw in indicate.keywords}["authorized_paths"] == "_artifact_scope"

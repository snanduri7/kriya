"""W3-3 units U3 and C2: the scoped D-001 candidate sandbox is the authoritative bound-tree projection of the invoked operating subtree, and that projection (plus the
candidate's own path-exact writes) is the membership every result-truth guard reads on it.

Measured at 7eccec2 (probes/probe_u3_scoped_snapshot_membership.py, prefix_outputs/u3_scoped_snapshot_membership_PREFIX_HEAD_7eccec2.txt): the sandbox copied every top-level
entry but ``.git``/``.kriya`` - an ignored ``.env``, ``node_modules/``, ``__pycache__/``, ``dist/`` and generated files, a nested repository's whole ``.git`` - and a wholly ignored
operating root built a sandbox as if it were a greenfield folder; rows 6/7/18 kept the basename-pruning walk on it (``candidate_member_files`` was ``None``).
"""
import ast
import os
import subprocess
from pathlib import Path

import pytest

from kriya.repository import bound_tree
from kriya.repository.bound_tree import BOUND_TREE_OPERATING_SCOPE_EXCLUDED, BOUND_TREE_UNAVAILABLE, BoundTreeError
from kriya.workflow import worktree as wt
from kriya.workflow.file_resolution import declared_and_ambiguous_member_files
from kriya.workflow.worktree import (
    candidate_has_canonical_membership,
    candidate_member_declaration,
    candidate_member_files,
    create_git_worktree,
    discard_candidate_write,
    record_candidate_write,
)

REPO = Path(__file__).resolve().parents[1]
CONTROL = {".kriya-scoped-snapshot"}


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _put(root, rel, text="x\n"):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(path, files, gitignore=None):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    for rel, text in files.items():
        _put(path, rel, text)
    if gitignore is not None:
        _put(path, ".gitignore", gitignore)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "i", "--allow-empty")
    return path


def _files(root):
    root = str(root)
    return sorted(os.path.relpath(os.path.join(d, f), root) for d, _dirs, fs in os.walk(root) for f in fs
                  if not os.path.relpath(os.path.join(d, f), root).startswith(".git" + os.sep) and f not in CONTROL)


def _digest(root):
    return {rel: Path(root, rel).read_bytes() for rel in sorted(os.path.relpath(os.path.join(d, f), str(root)) for d, _x, fs in os.walk(str(root)) for f in fs)}


IGNORE = ".env\nnode_modules/\ndist/\n__pycache__/\n*_pb2.py\nignored_proj/\nwork/\n"


@pytest.fixture
def world(tmp_path):
    host = _repo(tmp_path / "host", {"README.md": "r\n", "sibling/secret.txt": "SIBLING\n", "proj/src/main/java/com/acme/build/Gen.java": "class Gen {}\n",
                                      "proj/target/Tracked.java": "class T {}\n", "proj/build/Tracked.java": "class B {}\n"}, gitignore=IGNORE)
    proj = host / "proj"
    _put(proj, "dist/Kept.java", "class Kept {}\n")
    _git(host, "add", "-f", "proj/dist/Kept.java")
    _git(host, "commit", "-q", "-m", "force")
    _put(proj, "new_untracked.py")
    _put(proj, "dist/out.js")
    _put(proj, ".env", "SECRET=1\n")
    _put(proj, "node_modules/lib/index.js")
    _put(proj, "__pycache__/m.pyc")
    _put(proj, "gen_pb2.py")
    nested = proj / "vendor_repo"
    nested.mkdir()
    _git(nested, "init", "-q")
    _git(nested, "config", "user.email", "t@t")
    _git(nested, "config", "user.name", "t")
    _put(nested, "inner.py")
    _git(nested, "add", "-A")
    _git(nested, "commit", "-q", "-m", "inner")
    return host


# ======================================================================== U3: the sandbox is the bound-tree projection ========================================================

def test_the_scoped_sandbox_holds_exactly_the_bound_tree_projection(world):
    sandbox = create_git_worktree(str(world / "proj"))
    assert "scoped-worktree" in sandbox
    assert _files(sandbox) == ["build/Tracked.java", "dist/Kept.java", "new_untracked.py", "src/main/java/com/acme/build/Gen.java", "target/Tracked.java"]


@pytest.mark.parametrize("absent", [".env", "node_modules/lib/index.js", "__pycache__/m.pyc", "dist/out.js", "gen_pb2.py"])
def test_ignored_host_files_are_not_copied(world, absent):
    sandbox = create_git_worktree(str(world / "proj"))
    assert Path(world, "proj", absent).exists() and not os.path.lexists(os.path.join(sandbox, absent))


def test_sibling_project_content_does_not_leak(world):
    sandbox = create_git_worktree(str(world / "proj"))
    assert not any(part.startswith("sibling") or part == "README.md" for part in _files(sandbox))


def test_untracked_non_ignored_members_remain_present(world):
    sandbox = create_git_worktree(str(world / "proj"))
    assert os.path.isfile(os.path.join(sandbox, "new_untracked.py"))


def test_a_force_tracked_file_under_an_ignore_pattern_is_a_member(world):
    sandbox = create_git_worktree(str(world / "proj"))
    assert os.path.isfile(os.path.join(sandbox, "dist", "Kept.java")) and not os.path.lexists(os.path.join(sandbox, "dist", "out.js"))


def test_a_nested_repository_stays_isolated_like_the_exact_root_sandbox(world, tmp_path):
    sandbox = create_git_worktree(str(world / "proj"))
    assert not os.path.lexists(os.path.join(sandbox, "vendor_repo", "inner.py")) and not os.path.lexists(os.path.join(sandbox, "vendor_repo", ".git"))
    exact = _repo(tmp_path / "exact", {"README.md": "r\n"}, gitignore=IGNORE)
    nested = exact / "vendor_repo"
    nested.mkdir()
    _git(nested, "init", "-q")
    _put(nested, "inner.py")
    candidate = create_git_worktree(str(exact))
    assert "scoped-worktree" not in candidate and not os.path.lexists(os.path.join(candidate, "vendor_repo", "inner.py"))


def test_exact_root_and_subdirectory_candidates_hold_the_same_logical_members(world, tmp_path):
    """The parity control: the same project invoked at the repository root and as a D-001 subdirectory has the same candidate source."""
    files = {rel: Path(world, "proj", rel).read_text() for rel in ("src/main/java/com/acme/build/Gen.java", "target/Tracked.java", "build/Tracked.java", "dist/Kept.java", "new_untracked.py")}
    exact = _repo(tmp_path / "exact", {k: v for k, v in files.items() if not k.startswith("dist/")} | {"other/ignored.txt": "x\n"}, gitignore=IGNORE)
    _put(exact, "dist/Kept.java", files["dist/Kept.java"])
    _git(exact, "add", "-f", "dist/Kept.java")
    _git(exact, "commit", "-q", "-m", "force")
    for rel in (".env", "node_modules/lib/index.js", "__pycache__/m.pyc", "dist/out.js", "gen_pb2.py"):
        _put(exact, rel)
    _put(exact, "new_untracked.py")
    exact_candidate = create_git_worktree(str(exact))
    scoped_candidate = create_git_worktree(str(world / "proj"))
    exact_files = [path for path in _files(exact_candidate) if not path.startswith("other/") and path not in (".git", ".gitignore")]       # the exact root's own repository files are not project members
    assert exact_files == _files(scoped_candidate)


def test_a_new_empty_non_ignored_subdirectory_stays_valid(world):
    fresh = world / "brand_new"
    fresh.mkdir()
    sandbox = create_git_worktree(str(fresh))
    assert "scoped-worktree" in sandbox and _files(sandbox) == [] and candidate_member_files(sandbox) == []


def test_a_subdirectory_whose_own_gitignore_hides_everything_is_not_an_ignored_root(world):
    """MEASURED probe case E: asked from inside the root, its own ``.gitignore`` (``*``) would answer for the root itself; asked from the authority root it is not excluded."""
    selfhide = world / "selfhide"
    _put(selfhide, ".gitignore", "*\n")
    _put(selfhide, "g.py")
    sandbox = create_git_worktree(str(selfhide))
    assert "scoped-worktree" in sandbox and _files(sandbox) == []


@pytest.mark.parametrize("root", ["ignored_proj", "work/proj"], ids=["directly-ignored", "through-an-ignored-ancestor"])
def test_a_wholly_ignored_operating_root_is_a_typed_refusal_not_a_fake_greenfield(world, root):
    target = world / root
    _put(target, "main.py", "print(1)\n")
    _put(target, "test_main.py", "def test(): pass\n")
    host_before = _digest(world)
    with pytest.raises(BoundTreeError) as refused:
        create_git_worktree(str(target))
    assert refused.value.reason_code == BOUND_TREE_OPERATING_SCOPE_EXCLUDED
    assert _digest(world) == host_before                                                       # nothing created, nothing changed on the host (not even a .kriya)
    assert not (target / ".kriya").exists()


def test_a_refused_rebuild_does_not_destroy_or_replace_the_previous_sandbox(world):
    sandbox = create_git_worktree(str(world / "proj"))
    marker = Path(sandbox, "candidate_edit.py")
    marker.write_text("kept\n")
    _git(world, "rm", "-rq", "--cached", "proj")                                                  # the subtree is no longer tracked ...
    (world / ".gitignore").write_text(IGNORE + "proj/\n")                                          # ... and the authority repository now ignores it as a whole
    with pytest.raises(BoundTreeError) as refused:
        create_git_worktree(str(world / "proj"))
    assert refused.value.reason_code == BOUND_TREE_OPERATING_SCOPE_EXCLUDED
    assert marker.read_text() == "kept\n"


def test_a_listing_failure_is_the_typed_error_never_an_empty_sandbox(world, monkeypatch):
    def failing(root):
        raise RuntimeError("git ls-files failed: boom")

    monkeypatch.setattr(bound_tree, "repository_paths_by_declaration", failing)
    with pytest.raises(BoundTreeError) as refused:
        create_git_worktree(str(world / "proj"))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE
    assert not (world / "proj" / ".kriya" / "scoped-worktree").exists()


def test_a_failing_ignore_question_on_an_empty_listing_is_the_typed_error(world, monkeypatch):
    fresh = world / "brand_new"
    fresh.mkdir()

    def failing(root, relpaths):
        raise RuntimeError("git check-ignore failed: boom")

    monkeypatch.setattr(bound_tree, "ignored_repository_paths", failing)
    with pytest.raises(BoundTreeError) as refused:
        create_git_worktree(str(fresh))
    assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE


def test_the_ignore_question_is_not_asked_when_the_listing_is_not_empty(world, monkeypatch):
    def forbidden(root, relpaths):
        raise AssertionError("the ignore question must be asked only for an empty listing")

    monkeypatch.setattr(bound_tree, "ignored_repository_paths", forbidden)
    assert "scoped-worktree" in create_git_worktree(str(world / "proj"))


def test_the_membership_is_listed_once_and_feeds_the_alias_refusal_and_the_copy(world, monkeypatch):
    calls = []
    real = wt.bound_tree_declaration
    monkeypatch.setattr(wt, "bound_tree_declaration", lambda root: calls.append(root) or real(root))
    seen = []
    real_refuse = wt._refuse_control_object_aliases
    monkeypatch.setattr(wt, "_refuse_control_object_aliases", lambda identities, sources: seen.extend(list(sources)) or real_refuse(identities, seen))
    create_git_worktree(str(world / "proj"))
    assert len(calls) == 1
    names = sorted(os.path.relpath(path, str(world / "proj")) for path in seen)
    assert ".env" not in names and "new_untracked.py" in names and not any(name.startswith("node_modules") for name in names)


def test_an_ignored_hard_link_to_a_control_object_is_never_copied_so_it_is_not_refused(world):
    os.link(world / ".git" / "config", world / "proj" / ".env.link")
    _put(world, ".gitignore", IGNORE + ".env.link\n")
    sandbox = create_git_worktree(str(world / "proj"))
    assert not os.path.lexists(os.path.join(sandbox, ".env.link"))


def test_the_host_workspace_is_byte_identical_after_a_build(world):
    before = _digest(world / "proj")
    create_git_worktree(str(world / "proj"))
    after = {k: v for k, v in _digest(world / "proj").items() if not k.startswith(".kriya" + os.sep)}
    assert after == before


# ======================================================================== C2: the candidate-lifetime membership ================================================================

def test_rows_6_7_18_consume_the_projection_not_the_legacy_walk(world):
    sandbox = create_git_worktree(str(world / "proj"))
    assert candidate_has_canonical_membership(sandbox)
    members = candidate_member_files(sandbox)
    assert members == ["build/Tracked.java", "dist/Kept.java", "new_untracked.py", "src/main/java/com/acme/build/Gen.java", "target/Tracked.java"]


def test_files_created_in_the_sandbox_are_not_members_by_presence(world):
    sandbox = create_git_worktree(str(world / "proj"))
    _put(sandbox, "target/classes/Compiled.class", "bytecode")
    _put(sandbox, ".pytest_cache/state", "x")
    _put(sandbox, "stray.py")
    assert candidate_member_files(sandbox) == ["build/Tracked.java", "dist/Kept.java", "new_untracked.py", "src/main/java/com/acme/build/Gen.java", "target/Tracked.java"]


def test_a_file_the_candidate_wrote_is_a_member_and_a_discarded_write_is_not(world):
    sandbox = create_git_worktree(str(world / "proj"))
    _put(sandbox, "src/main/java/com/acme/Model.java", "class Model {}\n")
    assert "src/main/java/com/acme/Model.java" not in candidate_member_files(sandbox)
    record_candidate_write(sandbox, "src/main/java/com/acme/Model.java")
    assert "src/main/java/com/acme/Model.java" in candidate_member_files(sandbox)
    discard_candidate_write(sandbox, "src/main/java/com/acme/Model.java")
    assert "src/main/java/com/acme/Model.java" not in candidate_member_files(sandbox)


def test_the_declaration_split_is_tracked_vs_untracked_plus_the_candidates_own_writes(world):
    sandbox = create_git_worktree(str(world / "proj"))
    _put(sandbox, "Fresh.java", "class Fresh {}\n")
    record_candidate_write(sandbox, "Fresh.java")
    record_candidate_write(sandbox, "new_untracked.py")
    declared, ambiguous = declared_and_ambiguous_member_files(sandbox)
    assert "Fresh.java" in declared and "new_untracked.py" in declared and "target/Tracked.java" in declared and ambiguous == []
    fresh_sandbox = create_git_worktree(str(world / "proj"))
    declared, ambiguous = declared_and_ambiguous_member_files(fresh_sandbox)
    assert "target/Tracked.java" in declared and ambiguous == ["new_untracked.py"]                 # untracked-by-Git and not written by this candidate: visible, never declared


def test_a_rebuild_starts_a_new_candidate_with_no_inherited_writes(world):
    sandbox = create_git_worktree(str(world / "proj"))
    _put(sandbox, "Old.java")
    record_candidate_write(sandbox, "Old.java")
    again = create_git_worktree(str(world / "proj"))
    assert "Old.java" not in candidate_member_files(again) and "Old.java" not in _files(again)


def test_the_projection_holds_only_what_was_materialized_so_a_stray_file_never_inherits_membership(world):
    """A tracked path deleted on the host is in Git's listing but is not copied; a file later created in the sandbox under that name (compiler output, a cache) must not be a
    member by presence."""
    os.unlink(world / "proj" / "target" / "Tracked.java")
    sandbox = create_git_worktree(str(world / "proj"))
    assert not os.path.lexists(os.path.join(sandbox, "target", "Tracked.java"))
    _put(sandbox, "target/Tracked.java", "regenerated\n")
    assert "target/Tracked.java" not in candidate_member_files(sandbox)
    declared, ambiguous = declared_and_ambiguous_member_files(sandbox)
    assert "target/Tracked.java" not in declared + ambiguous


def test_record_and_discard_are_noops_for_an_ordinary_worktree(tmp_path):
    repo = _repo(tmp_path / "repo", {"a.py": "x\n"})
    record_candidate_write(str(repo), "a.py")
    discard_candidate_write(str(repo), "a.py")
    assert not (repo / ".git" / "kriya-candidate-writes").exists()


def test_a_scoped_sandbox_without_its_captured_projection_is_the_typed_error_never_the_whole_tree(world):
    sandbox = create_git_worktree(str(world / "proj"))
    os.unlink(os.path.join(sandbox, ".git", "kriya-candidate-membership.json"))
    for read in (lambda: candidate_member_files(sandbox), lambda: candidate_member_declaration(sandbox), lambda: declared_and_ambiguous_member_files(sandbox)):
        with pytest.raises(BoundTreeError) as refused:
            read()
        assert refused.value.reason_code == BOUND_TREE_UNAVAILABLE


def test_the_membership_state_lives_in_the_sandbox_control_area_and_dies_with_it(world):
    sandbox = create_git_worktree(str(world / "proj"))
    assert os.path.isfile(os.path.join(sandbox, ".git", "kriya-candidate-membership.json"))
    wt.remove_git_worktree(str(world / "proj"), sandbox)
    assert not os.path.exists(sandbox)


def test_apply_back_stays_limited_to_the_written_paths_and_ignored_host_files_survive(world):
    """The candidate promotion binds explicit written paths, never a sandbox-vs-workspace diff, so a file that is absent from the sandbox (ignored on the host) cannot read
    as a deletion; the host's ignored bytes are untouched by building and removing the sandbox."""
    before = _digest(world / "proj")
    sandbox = create_git_worktree(str(world / "proj"))
    wt.remove_git_worktree(str(world / "proj"), sandbox)
    assert {k: v for k, v in _digest(world / "proj").items() if not k.startswith(".kriya" + os.sep)} == before
    source = (REPO / "kriya" / "workflow" / "development_loop.py").read_text()
    assert "candidate_paths=state.all_files_written" in source


# ======================================================================== wiring (AST): the provenance hooks and the error propagation ========================================

def _tree(relative):
    return ast.parse((REPO / relative).read_text(encoding="utf-8"))


def _call_names(node):
    return [getattr(call.func, "attr", getattr(call.func, "id", "")) for call in ast.walk(node) if isinstance(call, ast.Call)]


def test_the_write_provenance_hooks_sit_next_to_every_change_of_all_files_written():
    tree = _tree("kriya/workflow/candidate_transaction.py")
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                    and any(isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "add"
                            and ast.unparse(call.func.value) == "state.all_files_written" for call in ast.walk(node)))
    names = _call_names(function)
    assert names.count("record_candidate_write") == 1 and names.count("discard_candidate_write") == 2
    discards = [call for call in ast.walk(function) if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "discard"
                and ast.unparse(call.func.value) == "state.all_files_written"]
    assert len(discards) == 2


@pytest.mark.parametrize("relative, tolerates", [("kriya/workflow/development_loop.py", "create_git_worktree"), ("kriya/workflow/enforce_loop.py", "create_git_worktree"),
                                                 ("kriya/workflow/best_of_n.py", "create_git_worktree")])
def test_a_bound_tree_error_is_reraised_ahead_of_the_broad_catch_at_every_sandbox_creation(relative, tolerates):
    tries = [node for node in ast.walk(_tree(relative)) if isinstance(node, ast.Try)
             and any(isinstance(call, ast.Call) and getattr(call.func, "attr", getattr(call.func, "id", "")) == tolerates for stmt in node.body for call in ast.walk(stmt))]
    assert tries, relative
    for node in tries:
        types = [ast.unparse(handler.type) if handler.type is not None else "" for handler in node.handlers]
        assert "BoundTreeError" in types, (relative, types)
        bound = node.handlers[types.index("BoundTreeError")]
        assert bound.body[-1].__class__ is ast.Raise and bound.body[-1].exc is None
        broad = [i for i, name in enumerate(types) if name in ("Exception", "")]
        assert all(types.index("BoundTreeError") < i for i in broad), (relative, types)


def test_a_real_run_at_a_wholly_ignored_operating_root_ends_as_the_typed_authority_stop_and_leaves_the_host_untouched(tmp_path):
    from _chaos_harness import (
        CALC,
        CALC_WITH_SUB,
        TEST_SUB,
        ChaosRuntime,
        RuntimeRegistration,
        benign_roles,
        chaos_config,
        chaos_engine,
        git_workspace,
        run_direct,
    )

    repo = git_workspace(tmp_path, {"README.md": "x\n", ".gitignore": "ignored_proj/\n"})
    target = repo / "ignored_proj"
    _put(target, "calc.py", CALC)
    _put(target, "test_calc.py", TEST_SUB)
    before = _digest(repo)
    runtime = ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), "add sub to calc.py", target)
    assert result["quality_gates_passed"] is False
    assert result["reason_codes"] == ["AUTHORITY_EXECUTION_UNAVAILABLE"]
    assert result["membership_authority"]["reason_code"] == BOUND_TREE_OPERATING_SCOPE_EXCLUDED
    assert (target / "calc.py").read_text() == CALC                                              # the real host workspace is byte-identical
    assert {k: v for k, v in _digest(repo).items() if not k.startswith(".kriya" + os.sep) and "/.kriya/" not in k and not k.startswith(".git" + os.sep)} \
        == {k: v for k, v in before.items() if not k.startswith(".git" + os.sep)}

"""W3-3 unit A1: the three git-backed result-truth guards of ``kriya.workflow.file_resolution`` decide membership from the canonical bound tree.

DIRECTORY-NAME-PRUNING-SOURCE-MEMBERSHIP-RESULT-TRUTH-001 (P2). ``find_brownfield_test_redirections``, ``find_brownfield_public_api_changes`` and
``find_unrequested_architectural_surfaces`` used to walk the real workspace pruning directories BY BASENAME (``build``, ``target``, ``dist``, ...): a legitimate
source package with such a name made the guard stand down (fail OPEN) while the matched control (``core``, ``app``) fired. They now consume
``kriya.repository.bound_tree.bound_tree_paths`` (Git's own view): tracked and untracked-but-not-ignored paths are in, gitignored output is out, and a root
Git cannot list is the typed ``BoundTreeError(BOUND_TREE_UNAVAILABLE)`` - which the two per-attempt retry wrappers let through instead of recording it as a
retried CANDIDATE failure (the first wrong propagation owner).

Every fixture here is a real ``git init`` repository and asserts its membership with Git BEFORE a guard is called, so a green result cannot come from a
fixture that never tracked the file.
"""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest
from test_s19e_honest_refusal import (  # noqa: E402  # pylint: disable=wrong-import-position
    GOOD,
    PLAIN_GOAL,
    PRIMARY,
    _best_of,
    _isolated_git,  # noqa: F401  # autouse fixture of that module, re-exported so it applies here
    _run,
    _scripted_engine,
    _tree,
    _workspace,
)

from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError
from kriya.workflow.file_resolution import (
    find_brownfield_public_api_changes,
    find_brownfield_test_redirections,
    find_unrequested_architectural_surfaces,
)

REPO = Path(__file__).resolve().parents[1]
SEGMENTS = ["core", "app", "build", "dist", "target"]       # core / app: the matched controls; the rest are the names the old walk pruned

OWNER = "def compute_total(items):\n    return sum(items)\n"
OWNER_TEST = "from pkg import compute_total\ndef test_a():\n    assert compute_total([1]) == 1\n"
REDIRECTED_TEST = "from pkg import compute_total_v2\ndef test_a():\n    assert compute_total_v2([1]) == 1\n"
PARALLEL = "def compute_total_v2(items):\n    return sum(items)\n"
API_OLD = "def old_name(a):\n    return a\n"
API_NEW = "def new_name(a):\n    return a\n"
CONSUMER = "from lib.api import old_name\nx = old_name(1)\n"
JAVA_MAIN = "package {pkg}; public class App {{ public static void main(String[] args) {{ }} }}"
JAVA_NEW_MAIN = "package com.acme; public class Second {{ public static void main(String[] args) {{ }} }}"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def _repo(root: Path, files: dict, *, untracked: dict | None = None, ignore: str | None = None, name: str = "repo") -> Path:
    """A git work tree holding ``files`` COMMITTED, plus ``untracked`` files left uncommitted; ``.gitignore`` = ``ignore`` when given."""
    repo = root / name
    repo.mkdir()
    _git(repo, "init", "-q")
    committed = dict(files)
    if ignore is not None:
        committed[".gitignore"] = ignore
    for relative, text in committed.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "base")
    for relative, text in (untracked or {}).items():                    # written AFTER the commit: present on disk, never tracked
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text)
    return repo


def _tracked(repo: Path) -> set:
    return set(_git(repo, "ls-files").split())


def _ignored(repo: Path, relative: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", relative], cwd=repo, check=False).returncode == 0


def _redirection(repo: Path):
    return find_brownfield_test_redirections(
        str(repo), {"tests/test_owner.py": OWNER_TEST}, {"tests/test_owner.py": REDIRECTED_TEST, "pkg/parallel.py": PARALLEL})


def _api(repo: Path):
    return find_brownfield_public_api_changes(str(repo), {"lib/api.py": API_OLD}, {"lib/api.py": API_NEW}, "fix the bug in lib")


def _surfaces(repo: Path):
    return find_unrequested_architectural_surfaces(
        str(repo), {}, {"src/main/java/com/acme/Second.java": JAVA_NEW_MAIN}, "fix the report formatting")


# ---- 1-3. legitimate tracked source under build/, target/, dist/ is seen exactly like the same source under core/ ----------------------------------

@pytest.mark.parametrize("segment", SEGMENTS)
def test_the_test_redirection_guard_sees_the_owner_whatever_its_directory_is_called(tmp_path, segment):
    repo = _repo(tmp_path, {f"pkg/{segment}/owner.py": OWNER, "tests/test_owner.py": OWNER_TEST})
    assert {f"pkg/{segment}/owner.py", "tests/test_owner.py"} <= _tracked(repo)             # validity: the owner really is tracked
    violations = _redirection(repo)
    assert [v["existing_owner"] for v in violations] == [f"pkg/{segment}/owner.py"], violations


@pytest.mark.parametrize("segment", SEGMENTS)
def test_the_public_api_guard_sees_the_consumer_whatever_its_directory_is_called(tmp_path, segment):
    repo = _repo(tmp_path, {"lib/api.py": API_OLD, f"consumer/{segment}/user.py": CONSUMER})
    assert f"consumer/{segment}/user.py" in _tracked(repo)
    violations = _api(repo)
    assert [(v["owner"], v["removed_signature"]) for v in violations] == [("lib/api.py", "old_name(a)")], violations
    assert f"consumer/{segment}/user.py" in violations[0]["evidence_files"]


@pytest.mark.parametrize("segment", SEGMENTS)
def test_the_entrypoint_guard_sees_the_baseline_entrypoint_whatever_its_directory_is_called(tmp_path, segment):
    relative = f"src/main/java/com/acme/{segment}/App.java"
    repo = _repo(tmp_path, {relative: JAVA_MAIN.format(pkg=f"com.acme.{segment}")})
    assert relative in _tracked(repo)
    violations = _surfaces(repo)
    assert [v["reason_code"] for v in violations] == ["UNREQUESTED_ARCHITECTURAL_SURFACE"], violations
    assert violations[0]["baseline_entrypoints"] == [relative]


# ---- 4. relevant untracked source follows the existing canonical contract (visible = included) ---------------------------------------------------

def test_an_untracked_non_ignored_source_under_a_formerly_pruned_name_is_included(tmp_path):
    relative = "src/main/java/com/acme/build/App.java"
    repo = _repo(tmp_path, {"README.md": "x\n"}, untracked={relative: JAVA_MAIN.format(pkg="com.acme.build")})
    assert relative not in _tracked(repo) and (repo / relative).exists() and not _ignored(repo, relative)       # validity
    assert [v["baseline_entrypoints"] for v in _surfaces(repo)] == [[relative]]


# ---- 5. gitignored generated output is excluded (the overcorrection control) ---------------------------------------------------------------------

def test_gitignored_generated_output_is_not_membership_for_any_guard(tmp_path):
    ignore = "generated/\n"
    redirect = _repo(tmp_path, {"tests/test_owner.py": OWNER_TEST}, untracked={"pkg/generated/owner.py": OWNER}, ignore=ignore, name="a")
    api = _repo(tmp_path, {"lib/api.py": API_OLD}, untracked={"consumer/generated/user.py": CONSUMER}, ignore=ignore, name="b")
    surface = _repo(tmp_path, {"README.md": "x\n"}, untracked={"src/generated/App.java": JAVA_MAIN.format(pkg="g")}, ignore=ignore, name="c")
    for repo, relative in ((redirect, "pkg/generated/owner.py"), (api, "consumer/generated/user.py"), (surface, "src/generated/App.java")):
        assert (repo / relative).exists() and _ignored(repo, relative) and relative not in _tracked(repo)           # validity: real, ignored output
    # The matched controls above (a visible owner / consumer / entrypoint under any name) fire; here the only candidate is ignored output:
    assert _redirection(redirect) == []
    assert _api(api) == []
    assert _surfaces(surface) == []                      # no baseline established -> the gate has nothing to protect (not a manufactured baseline)


# ---- 6. a second baseline entrypoint hidden under a pruned name is no longer hidden ---------------------------------------------------------------

def test_both_baseline_entrypoints_are_reported_when_one_sits_under_build(tmp_path):
    visible, hidden = "src/main/java/com/acme/core/App.java", "src/main/java/com/acme/build/Tool.java"
    repo = _repo(tmp_path, {visible: JAVA_MAIN.format(pkg="com.acme.core"), hidden: JAVA_MAIN.format(pkg="com.acme.build")})
    assert {visible, hidden} <= _tracked(repo)
    assert sorted(_surfaces(repo)[0]["baseline_entrypoints"]) == sorted([visible, hidden])


# ---- 7. an unavailable membership authority is a typed failure, never an empty tree ----------------------------------------------------------------

@pytest.mark.parametrize("guard", [_redirection, _api, _surfaces], ids=["test-redirection", "public-api", "architectural-surface"])
def test_a_root_git_cannot_list_is_the_typed_bound_tree_failure(tmp_path, guard):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "lib").mkdir()
    (plain / "lib" / "api.py").write_text(API_OLD)
    assert subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=plain, capture_output=True, check=False).returncode != 0   # validity
    with pytest.raises(BoundTreeError) as raised:
        guard(plain)
    assert raised.value.reason_code == BOUND_TREE_UNAVAILABLE


def test_a_tracked_file_deleted_from_disk_is_not_evidence(tmp_path):
    repo = _repo(tmp_path, {"lib/api.py": API_OLD, "consumer/build/user.py": CONSUMER})
    (repo / "consumer" / "build" / "user.py").unlink()
    assert "consumer/build/user.py" in _tracked(repo) and not (repo / "consumer/build/user.py").exists()
    assert _api(repo) == []


# ---- 8. the membership failure ends the run as itself, never a retried candidate failure -------------------------------------------------------

def _unlistable(monkeypatch):
    from kriya.workflow import file_resolution

    calls = []

    def unavailable(root):
        calls.append(root)
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, "git ls-files failed: injected")
    monkeypatch.setattr(file_resolution, "bound_tree_paths", unavailable)
    return calls


def test_a_membership_failure_in_the_attempt_ends_the_run_as_itself_without_a_retry(tmp_path, monkeypatch):
    from kriya.workflow import retry_strategy

    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    calls = _unlistable(monkeypatch)
    handled = []
    real = retry_strategy.handle_attempt_failure

    async def counting(state, ctx, exc):
        handled.append(exc)
        return await real(state, ctx, exc)
    monkeypatch.setattr(retry_strategy, "handle_attempt_failure", counting)
    eng, _cfg, developer = _scripted_engine(lambda n: GOOD)
    with pytest.raises(BoundTreeError) as raised:
        _run(eng, workspace)
    assert calls, "the membership authority was never consulted (the exception came from somewhere else)"
    assert raised.value.reason_code == BOUND_TREE_UNAVAILABLE
    assert developer == [PRIMARY], f"the membership failure was retried: {len(developer)} Developer calls"
    assert handled == [], "the membership failure was recorded as an attempt failure"
    assert _tree(workspace) == before                                      # nothing applied


def test_a_best_of_n_candidate_s_membership_failure_starts_no_independent_candidate(tmp_path, monkeypatch):
    from kriya.workflow import retry_strategy

    workspace = _workspace(tmp_path)
    before = _tree(workspace)
    calls = _unlistable(monkeypatch)
    handled = []
    real = retry_strategy.handle_attempt_failure

    async def counting(state, ctx, exc):
        handled.append(exc)
        return await real(state, ctx, exc)
    monkeypatch.setattr(retry_strategy, "handle_attempt_failure", counting)
    eng, _cfg, developer = _scripted_engine(lambda n: GOOD, configure=_best_of(3))
    with pytest.raises(BoundTreeError):
        _run(eng, workspace, PLAIN_GOAL)
    assert calls and developer == [PRIMARY] and handled == [], (calls, developer, handled)
    assert _tree(workspace) == before


@pytest.mark.parametrize("path, function", [("kriya/workflow/attempt_lifecycle.py", "run_iteration"),
                                            ("kriya/workflow/best_of_n.py", "run_attempt_with_best_of_n")])
def test_every_generic_handler_of_the_retry_wrappers_also_lets_the_membership_failure_through(path, function):
    """Structural twin: the ``except Exception`` around ``run_attempt`` in each of the two retry wrappers is preceded, in the same try, by
    ``except BoundTreeError: raise`` (a bare re-raise)."""
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    target = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function)
    wrapping = 0
    for node in ast.walk(target):
        if not isinstance(node, ast.Try):
            continue
        if not any(isinstance(c, ast.Call) and getattr(c.func, "id", getattr(c.func, "attr", "")) == "run_attempt"
                   for stmt in node.body for c in ast.walk(stmt)):
            continue                                    # not the handler around the attempt (best-of-N's second try guards the sandbox reset)
        names = [getattr(h.type, "id", None) for h in node.handlers]
        assert "Exception" in names, (path, function, names)
        wrapping += 1
        position = names.index("BoundTreeError")
        assert position < names.index("Exception"), (path, function, names)
        assert isinstance(node.handlers[position].body[0], ast.Raise) and node.handlers[position].body[0].exc is None
    assert wrapping == 1, (path, function, wrapping)


# ---- 9. bypass census: no sibling re-derives membership from a directory name in the fixed scope --------------------------------------------------

def _functions_with_a_walk(source: str) -> set:
    found = set()
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "walk" for c in ast.walk(fn)):
            found.add(fn.name)
    return found


def test_the_fixed_guards_do_not_walk_the_filesystem_and_the_remaining_walkers_are_the_parked_ones():
    walkers = _functions_with_a_walk((REPO / "kriya/workflow/file_resolution.py").read_text(encoding="utf-8"))
    fixed = {"find_brownfield_test_redirections", "find_brownfield_public_api_changes", "find_unrequested_architectural_surfaces"}
    assert not walkers & fixed, walkers & fixed
    # W3-3 A2: the two owner-resolution functions no longer walk; the ONE remaining membership walker is the PARKED root classes' legacy walk
    # (NO_GIT_REPOSITORY / NESTED_IN_GIT_WORK_TREE - OWNER UNRESOLVED), plus the Maven main-class resolver (a candidate-worktree build-layout read, unit C2).
    # A new walker in this module must be classified here.
    assert not walkers & {"resolve_planned_artifact_owners", "discover_response_construction_owners"}, walkers
    assert walkers == {"_parked_root_filesystem_files", "_resolve_maven_main_class"}, walkers


# ---- 10. row 20 stays an intentional generated-output filter (the overcorrection control for the whole slice) --------------------------------------

def test_the_evidence_hash_still_excludes_generated_output_but_not_source(tmp_path):
    """RETAIN (owner decision): ``workspace_evidence_hash`` documents and pins its generated-output exclusion. W3-3 must not remove directory
    filtering globally: an untracked file under a generated-output name leaves the hash unchanged, a new source file elsewhere changes it."""
    from kriya.workflow.milestone_completion import workspace_evidence_hash

    repo = _repo(tmp_path, {"src/core/a.py": "a = 1\n"})
    base = workspace_evidence_hash(str(repo))
    assert base is not None
    (repo / "target").mkdir()
    (repo / "target" / "out.bin").write_text("generated\n")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "a.pyc").write_text("pyc\n")
    assert os.path.exists(repo / "target" / "out.bin") and "target/out.bin" not in _tracked(repo)
    assert workspace_evidence_hash(str(repo)) == base                       # generated output: intentionally ignored by this identity
    (repo / "src" / "core" / "b.py").write_text("b = 2\n")
    assert workspace_evidence_hash(str(repo)) != base                       # new untracked source: always invalidates

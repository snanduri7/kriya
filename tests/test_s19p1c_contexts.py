"""S19 P1c ROOT INVARIANT (owner correction 2026-10-20): for every Kriya git invocation that can run a filter, the
inspection validates what THAT spawn will see - its cwd, its effective configuration in the repository/worktree
context git really runs in, and the repositories it recurses into - immediately before it.

Relayed findings, each MEASURED at base 135acfe (scratch p1c/out_base.jsonl) and pinned here on the real entry:
8(a) requirement_closers.mutation_scope_evidence ran `diff --name-only <base>` (a clean filter on a stat-dirty file)
     with no inspection;
8(b) a local `includeIf "gitdir:**/worktrees/**"` defining a smudge filter is invisible from the main work tree and
     ran in `worktree add` (managed worktree; the operator scan's base worktree has the same shape);
8(d) (relayed F3) an UNTRACKED embedded repository's own clean filter ran through workspace_evidence_hash: `add -A`
     records it as a gitlink of the scratch index and `add -u` runs its git.
The dynamic twin (tests/test_s19q1_content_filter_refusal.py::_assert_guarded) pins the invariant over every site.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from tests.test_s19q1_content_filter_refusal import _bind, _dirty, _marker_script, _repository, _setup

REFUSED = "GIT_CONTENT_FILTER_REPOSITORY_DEFINED"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("s19p1c-ctx-home")
    config = home / "gitconfig"
    config.write_text("[user]\n\tname = s19p1c\n\temail = s19p1c@local\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-10-20T00:00:00+00:00")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def owner(tmp_path) -> Path:
    repo = _repository(tmp_path)
    return repo


def _reason(error: BaseException):
    while error is not None:
        if getattr(error, "reason_code", None):
            return error.reason_code
        error = error.__cause__ or error.__context__
    return None


# --------------------------------------------------------------------------------- 8(a)

def test_mutation_scope_evidence_refuses_before_its_diff_runs_a_filter(owner):
    from kriya.repository.git import RepositoryContentFilterRefused
    from kriya.workflow import requirement_closers

    marker = owner.parent / "FILTER_RAN"
    _setup(owner, "config", "filter.kx.clean", _marker_script(owner.parent / "f.sh", marker))
    _bind(owner)
    _dirty(owner)
    head = _setup(owner, "rev-parse", "HEAD").strip()

    class Deps:
        run_committed_paths = staticmethod(lambda _workspace: ("run", head, []))
    with pytest.raises(RepositoryContentFilterRefused) as raised:          # typed, never "unavailable" evidence
        requirement_closers.mutation_scope_evidence(Deps(), str(owner), str(owner), candidate_paths=["a.py"])
    assert raised.value.reason_code == REFUSED
    assert not marker.exists()


def test_mutation_scope_evidence_is_unchanged_without_a_repository_filter(owner):
    from kriya.workflow import requirement_closers

    _dirty(owner)
    head = _setup(owner, "rev-parse", "HEAD").strip()

    class Deps:
        run_committed_paths = staticmethod(lambda _workspace: ("run", head, []))
    tracked, evidence = requirement_closers.mutation_scope_evidence(Deps(), str(owner), str(owner),
                                                                    candidate_paths=["a.py"])
    assert tracked == [".gitignore", "a.py", "skill.md"]
    assert evidence["actual_paths"] == ["a.py"] and evidence["foreign_paths"] == ["skill.md"]


_GIT_READ_LINES_CALLERS = {
    # (file, function, verb): every argv a caller hands kriya.workflow.worktree.git_read_lines (S19 P1c 8(a)).
    ("kriya/cli.py", "_bind_approval", "rev-parse"),
    ("kriya/workflow/requirement_closers.py", "_tracked_workspace_paths", "ls-files"),
    ("kriya/workflow/requirement_closers.py", "_workspace_head", "rev-parse"),
    ("kriya/workflow/requirement_closers.py", "_oracle_base_revision", "rev-parse"),
    ("kriya/workflow/requirement_closers.py", "mutation_scope_evidence", "rev-parse"),
    ("kriya/workflow/requirement_closers.py", "mutation_scope_evidence", "ls-tree"),
    ("kriya/workflow/requirement_closers.py", "mutation_scope_evidence", "diff"),
    ("kriya/workflow/requirement_closers.py", "mutation_scope_evidence", "ls-files"),
}
_FILTER_FREE_READS = {"rev-parse", "ls-tree", "ls-files"}      # MEASURED filter-free (ls-files: no -m / work-tree compare)


def _git_read_lines_calls():
    for path in sorted((REPO / "kriya").rglob("*.py")):
        relpath = path.relative_to(REPO).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {"git_read_lines"} | {alias.asname for node in ast.walk(function) if isinstance(node, ast.ImportFrom)
                                         for alias in node.names if alias.name == "git_read_lines" and alias.asname}
            inspects = any(isinstance(node, ast.Call) and getattr(node.func, "id", None) == "refuse_repository_content_filters"
                           for node in ast.walk(function))
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) in names and len(node.args) > 1:
                    verb = node.args[1].value if isinstance(node.args[1], ast.Constant) else None
                    yield relpath, function.name, verb, inspects


def test_every_git_read_lines_verb_is_classified_and_filter_capable_ones_are_inspected():
    """The P1b table claimed git_read_lines carried rev-parse and ls-files only; mutation_scope_evidence also passes
    `diff`. Every (caller, verb) is pinned; a filter-capable verb only in a function that inspects."""
    found = set()
    for relpath, function, verb, inspects in _git_read_lines_calls():
        found.add((relpath, function, verb))
        if verb not in _FILTER_FREE_READS:
            assert inspects, (relpath, function, verb)
    assert found == _GIT_READ_LINES_CALLERS


_CLOSERS_REACHING_THE_REFUSAL = {"close_requirements_by_mutation_scope", "close_requirements_by_test_immutability",
                                 "close_requirements_by_file_immutability", "close_requirements_by_suite_preservation"}
_CATCHES_THE_REFUSAL = {None, "Exception", "BaseException", "RepositoryContentFilterRefused"}
_TRY_NODES = (ast.Try, getattr(ast, "TryStar", ast.Try))          # `except*` is ast.TryStar (3.11+)


def _handler_type_names(handler):
    """The names a handler catches: {None} for a bare except, else every Name / Attribute / tuple member it lists."""
    if handler.type is None:
        return {None}
    members = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {member.id if isinstance(member, ast.Name) else member.attr if isinstance(member, ast.Attribute) else "<dynamic>"
            for member in members}


def _absorbing_handlers(source: str, closers):
    """(closer, caught names) for every handler enclosing a closer call that could catch the typed refusal and is not a PURE
    pass-through - a body that is exactly one bare ``raise``. A conditional, delayed or wrapped re-raise does not qualify (it can
    absorb on the other branch), and a dynamic/unresolvable handler type is treated as catching it. Also flagged: ``except*``,
    ``with ...suppress(...)`` and a ``finally`` that returns / breaks / continues. NOT detectable lexically (the behaviour pins in
    test_direct_closer_failure_truth are the backstop, they inject a failure at every closer and kill such a wrapper): an aliased
    closer, a wrapper taking the call as a lambda, a ``getattr`` call and a swallowing decorator."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    offenders, seen = [], set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr in closers):
            continue
        seen.add(node.attr)
        child = node
        while child in parents:
            parent = parents[child]
            if isinstance(parent, _TRY_NODES) and any(child is stmt for stmt in parent.body):
                for handler in parent.handlers:
                    names = _handler_type_names(handler)
                    pure_pass_through = len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise) and handler.body[0].exc is None
                    if (names & _CATCHES_THE_REFUSAL or "<dynamic>" in names) and not pure_pass_through:
                        offenders.append((node.attr, sorted(str(name) for name in names)))
                if any(isinstance(sub, (ast.Return, ast.Break, ast.Continue)) for stmt in parent.finalbody for sub in ast.walk(stmt)):
                    offenders.append((node.attr, ["finally: leaves the block"]))      # a jump out of `finally` discards the exception
            if isinstance(parent, (ast.With, ast.AsyncWith)) and any(child is stmt for stmt in parent.body) and any(
                    "suppress" in ast.dump(item.context_expr) for item in parent.items):
                offenders.append((node.attr, ["with suppress(...)"]))
            child = parent
    return offenders, seen


def test_the_development_loop_lets_the_closers_refusal_through():
    """mutation_scope_evidence's typed refusal is not absorbed as 'evidence unavailable' by the pre-apply closers
    that reach it (mutation scope, test immutability, file immutability, suite preservation).

    Q5 (2026-10-21): the closers' handlers that absorbed an unexpected exception are gone altogether (an absorbed exception
    meant a passed quality gate that had verified nothing), so the invariant is stated for ANY enclosing handler: a handler
    around a closer call that could catch the typed refusal - a bare except, Exception, BaseException, the refusal itself, any
    of them inside a tuple or behind a module attribute - must be a PURE pass-through (exactly one bare ``raise``). Re-adding an
    absorbing ``except Exception`` (the pre-fix shape), or one that re-raises only on some branch, fails here."""
    offenders, seen = _absorbing_handlers((REPO / "kriya/workflow/development_loop.py").read_text(encoding="utf-8"),
                                          _CLOSERS_REACHING_THE_REFUSAL)
    assert offenders == [] and seen == _CLOSERS_REACHING_THE_REFUSAL


@pytest.mark.parametrize("handler, absorbs", [
    ("except Exception:\n        logger.warning('x')\n        closures = []", True),                     # the pre-fix shape
    ("except Exception as exc:\n        if is_typed(exc):\n            raise\n        closures = []", True),   # conditional re-raise
    ("except Exception:\n        closures = []\n        raise", True),                                      # delayed re-raise
    ("except Exception as exc:\n        raise RuntimeError('wrapped') from exc", True),                    # wrapped: not the refusal itself
    ("except (ValueError, Exception):\n        closures = []", True),                                      # inside a tuple
    ("except builtins.Exception:\n        closures = []", True),                                           # behind an attribute
    ("except:\n        closures = []", True),                                                              # bare
    ("except get_error_type():\n        closures = []", True),                                             # dynamic type
    ("except Exception:\n        raise", False),                                                           # pure pass-through
    ("except KeyError:\n        closures = []", False),                                                    # cannot catch the refusal
    ("except* Exception:\n        closures = []", True),                                                   # except* (ast.TryStar)
    ("except KeyError:\n        raise\n    finally:\n        return None", True),                              # finally discards the exception
])
def test_the_tripwire_catches_the_evasive_handler_shapes_and_only_those(handler, absorbs):
    source = f"def f():\n    try:\n        x = obj.close_requirements_by_mutation_scope()\n    {handler}\n"
    offenders, seen = _absorbing_handlers(source, {"close_requirements_by_mutation_scope"})
    assert seen == {"close_requirements_by_mutation_scope"}                                              # the call was found
    assert bool(offenders) is absorbs


def test_the_tripwire_flags_a_suppress_context_and_not_an_ordinary_one():
    closers = {"close_requirements_by_mutation_scope"}
    flagged, _ = _absorbing_handlers(
        "def f():\n    with contextlib.suppress(Exception):\n        obj.close_requirements_by_mutation_scope()\n", closers)
    ordinary, _ = _absorbing_handlers("def f():\n    with lock:\n        obj.close_requirements_by_mutation_scope()\n", closers)
    assert flagged and not ordinary


def test_a_direct_run_ends_with_the_closer_s_typed_refusal_and_releases_the_workspace(tmp_path, monkeypatch):
    """Runtime characterization of the pass-through (the AST pin above only proves the handler exists): a direct run
    whose goal carries a mutation-scope requirement, and whose evidence refuses, commits nothing, ends with that typed
    refusal unretried (S19e) and frees the workspace for the next run (lock released)."""
    import asyncio
    from unittest.mock import AsyncMock

    from test_prd008a_plan_executor import CALC, CALC_WITH_SUB, _config, _role_llm

    from kriya.control.run_coordinator import begin_mutating_run
    from kriya.core.kernel import Kernel
    from kriya.repository.git import RepositoryContentFilterRefused
    from kriya.workflow import requirement_closers
    from kriya.workflow.workflow import WorkflowEngine

    workspace = tmp_path / "calc"
    workspace.mkdir()
    (workspace / "calc.py").write_text(CALC)
    (workspace / ".gitignore").write_text(".kriya/\n__pycache__/\n")
    _setup(workspace, "init", "-q", "-b", "main")
    _setup(workspace, "add", "-A")
    _setup(workspace, "commit", "-q", "-m", "init")
    calls = []

    def refusing(*_args, **_kwargs):
        calls.append(1)
        raise RepositoryContentFilterRefused(REFUSED, str(workspace), key="filter.kx.clean", scope="local",
                                             origin="file:.git/config")
    monkeypatch.setattr(requirement_closers, "mutation_scope_evidence", refusing)
    cfg = _config()
    engine = WorkflowEngine(Kernel(config=cfg), _role_llm(cfg, CALC_WITH_SUB))
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    goal = "Add sub to `calc.py`.\n\nDo not modify any other file in the repository.\n"
    # S19e (OI-162, changed assertion): the run used to retry the refusal as an attempt failure and end
    # failure_category=quality_gates_exhausted WITHOUT its reason code (MEASURED at a0eb5f0: 7 Developer calls). The
    # attempt lifecycle now lets the typed refusal through: the run ends with it, unretried (the closer runs once).
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        asyncio.run(engine.run_generation_workflow(goal=goal, workspace_path=str(workspace)))
    assert raised.value.reason_code == REFUSED and calls == [1]
    assert (workspace / "calc.py").read_text() == CALC                    # nothing committed
    with begin_mutating_run(str(workspace)) as context:                  # the workspace lock was released
        assert context.run_id


# --------------------------------------------------------------------------------- 8(b)

def _worktree_only_smudge(repo: Path) -> Path:
    marker = repo.parent / "SMUDGE_RAN"
    include = repo.parent / "worktree-only.gitcfg"
    command = _marker_script(repo.parent / "smudge.sh", marker)
    include.write_text(f'[filter "kx"]\n\tsmudge = {command}\n\tclean = {command}\n')
    _setup(repo, "config", "includeIf.gitdir:**/worktrees/**.path", str(include))
    _bind(repo)
    return marker


def test_a_filter_visible_only_in_a_linked_worktree_s_context_is_refused_before_its_checkout(owner):
    from kriya.repository.git import refuse_repository_content_filters
    from kriya.workflow import worktree

    marker = _worktree_only_smudge(owner)
    refuse_repository_content_filters(str(owner))            # the main work tree's context does not see it (MEASURED)
    with pytest.raises(Exception) as raised:
        worktree.create_git_worktree(str(owner))
    assert _reason(raised.value) == REFUSED and "filter.kx." in str(raised.value)
    assert not marker.exists(), "the checkout of the new worktree ran a filter its context defines"
    # the registered, unpopulated worktree is inspected again (and refused) when reused
    with pytest.raises(Exception) as again:
        worktree.create_git_worktree(str(owner))
    assert _reason(again.value) == REFUSED and not marker.exists()


def test_a_two_step_worktree_is_the_same_worktree(owner, tmp_path):
    """Positive control: no filter anywhere - the managed worktree holds exactly what `worktree add` gives."""
    from kriya.workflow import worktree

    (owner / "run.sh").write_text("#!/bin/sh\n")
    (owner / "run.sh").chmod(0o755)
    _setup(owner, "add", "run.sh")
    _setup(owner, "commit", "-q", "-m", "x")
    created = Path(worktree.create_git_worktree(str(owner)))
    reference = tmp_path / "reference"
    _setup(owner, "worktree", "add", "-q", "--detach", str(reference))
    for name in ("a.py", "skill.md", ".gitignore", "run.sh"):
        assert (created / name).read_bytes() == (reference / name).read_bytes()
        assert (created / name).stat().st_mode == (reference / name).stat().st_mode
    assert _setup(created, "ls-files", "-s") == _setup(reference, "ls-files", "-s")
    assert _setup(created, "status", "--porcelain") == "" and _setup(created, "rev-parse", "HEAD") == \
        _setup(reference, "rev-parse", "HEAD")


def test_the_operator_scan_base_worktree_is_inspected_in_its_own_context(owner):
    from kriya.config import AppConfig
    from kriya.static_analysis import operator_scan

    marker = _worktree_only_smudge(owner)
    with pytest.raises(Exception) as raised:
        operator_scan.run_operator_scan(AppConfig(), str(owner), "HEAD")
    assert _reason(raised.value) == REFUSED
    assert not marker.exists()
    assert _setup(owner, "worktree", "list", "--porcelain").count("worktree ") == 1    # the base worktree was removed


# --------------------------------------------------------------------------------- 8(d) / relayed F3

def _untracked_embedded(owner: Path, where: str = "vendored") -> Path:
    """S19 P1e: the embedded file is ``lib.py`` (was ``lib.txt``), so ``_bind``'s ``*.py filter=evil`` really binds it -
    the definition is ACTIVE and the vector real (MEASURED: with ``lib.txt`` nothing was bound and the guard-off hash ran
    nothing, so the pin only held the old refuse-even-if-dormant contract)."""
    marker = owner.parent / "EMBEDDED_FILTER_RAN"
    nested = owner / where
    nested.mkdir(parents=True)
    _setup(nested, "init", "-q", "-b", "main")
    (nested / "lib.py").write_text("v1\n")
    _setup(nested, "add", "-A")
    _setup(nested, "commit", "-q", "-m", "e")
    _setup(nested, "config", "filter.evil.clean", _marker_script(owner.parent / "evil.sh", marker))
    _bind(nested, "evil")
    (nested / "lib.py").write_text("v2\n")
    return marker


@pytest.mark.parametrize("where", ["vendored", "plain-dir/vendored"])
def test_an_untracked_embedded_repository_s_filter_never_runs_through_the_evidence_hash(owner, where):
    from kriya.workflow import milestone_completion

    marker = _untracked_embedded(owner, where)
    with pytest.raises(Exception) as raised:
        milestone_completion.workspace_evidence_hash(str(owner))
    assert _reason(raised.value) == REFUSED and "filter.evil." in str(raised.value)
    assert not marker.exists()


def test_an_untracked_embedded_repository_without_a_filter_hashes_as_before(owner):
    """Positive control: the extra inspection changes nothing for an embedded repository that defines no filter
    (the same hash as computed with the inspection disabled)."""
    from unittest.mock import patch

    from kriya.workflow import milestone_completion

    nested = owner / "vendored"
    nested.mkdir()
    _setup(nested, "init", "-q", "-b", "main")
    (nested / "lib.txt").write_text("v1\n")
    _setup(nested, "add", "-A")
    _setup(nested, "commit", "-q", "-m", "e")
    _dirty(owner)
    measured = milestone_completion.workspace_evidence_hash(str(owner))
    with patch("kriya.repository.git.refuse_repository_content_filters", lambda *a, **k: None):
        unguarded = milestone_completion.workspace_evidence_hash(str(owner))
    assert measured is not None and measured == unguarded


def test_the_checkpoint_content_hash_never_runs_an_embedded_repository_s_filter(owner):
    """The STATE-001 scratch-index hash runs add -A / rm -f --cached / write-tree (no `add -u`): MEASURED filter-free
    for an untracked embedded repository; pinned, also for one under .kriya (dropped with rm -f --cached)."""
    from kriya.workflow import checkpoint

    first = _untracked_embedded(owner)
    (owner / ".gitignore").write_text("__pycache__/\n")                  # .kriya not ignored: the rm path runs
    second_root = owner / ".kriya"
    second_root.mkdir()
    nested = second_root / "other"
    nested.mkdir()
    _setup(nested, "init", "-q", "-b", "main")
    (nested / "x.py").write_text("1\n")                 # S19 P1e: .py, so _bind really binds it (was x.txt)
    _setup(nested, "add", "-A")
    _setup(nested, "commit", "-q", "-m", "x")
    _setup(nested, "config", "filter.evil.clean", str(owner.parent / "evil.sh"))
    _bind(nested, "evil")
    (nested / "x.py").write_text("2\n")
    assert checkpoint.compute_workspace_content_hash(str(owner)) is not None
    assert not first.exists()


def test_a_submodule_s_git_is_inspected_without_the_parent_s_repository_variables(owner, monkeypatch):
    """A submodule's git runs with the caller's repository-selecting variables cleared (git clears them): with
    GIT_DIR=<parent>/.git in the caller's environment, the child is still inspected in its OWN context."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    nested = owner / "vendored"
    nested.mkdir()
    _setup(nested, "init", "-q", "-b", "main")
    (nested / "lib.txt").write_text("v1\n")
    _setup(nested, "add", "-A")
    _setup(nested, "commit", "-q", "-m", "e")
    _setup(owner, "add", "vendored")
    _setup(owner, "commit", "-q", "-m", "gitlink")
    _setup(nested, "config", "filter.evil.clean", str(owner.parent / "evil.sh"))
    (nested / ".git" / "info" / "attributes").write_text("*.txt filter=evil\n")    # S19 P1e: ACTIVE in the child
    env = {**os.environ, "GIT_DIR": str(owner / ".git"), "GIT_WORK_TREE": str(owner)}
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner), env=env)
    assert raised.value.reason_code == REFUSED and "filter.evil." in str(raised.value)

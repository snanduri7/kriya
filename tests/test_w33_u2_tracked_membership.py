"""W3-3 unit U2: on a Git-backed root, "Git could not list the tracked set" is never "the repository tracks nothing".

MEASURED at 38f9131 (probe_contract.py, before/after ``git init``; see also the reconciliation): ``requirement_closers._tracked_workspace_paths`` turned every operational Git failure
(and "no repository") into ``[]``. Its contract-sealing callers - ``workflow.compile_run_contract`` and the CLI's ``_bind_verification_authority`` - read ``[]`` as "no tracked files":
``language=None``, the "do not modify X" freeze and the public-API predicate unbound, a conditional documentation request sealed as ``documentation_not_applicable``, the example
compiler refused as "project is of unknown language". A Git-backed root now raises the typed ``BoundTreeError`` there (``authoritative_tracked_workspace_paths``), which both
entrypoints already end as the existing ``AUTHORITY_EXECUTION_UNAVAILABLE`` outcome (the CLI admission maps it to the same code). PRESERVED, by their own pins: the closers'
``_tracked_workspace_paths`` (API preservation ``candidate_files``, the documentation closer: both proven fail-closed/masked - an unavailable listing there can only withhold a
SATISFIED), and the NO_GIT branch (U4's decision) which still answers ``[]``.
"""
import ast
import subprocess
import types
from pathlib import Path

import pytest

from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError
from kriya.workflow import requirement_closers, worktree
from kriya.workflow.authority_bundle import AUTHORITY_EXECUTION_UNAVAILABLE, AuthorityBundleError
from kriya.workflow.requirement_closers import authoritative_tracked_workspace_paths
from kriya.workflow.requirements import derive_requirements
from kriya.workflow.workflow import compile_run_contract

REPO_ROOT = Path(__file__).resolve().parents[1]
GOALS = {
    "freeze": "Do not modify src/main/java/com/acme/Keep.java.",
    "api": "Keep the public API of the library unchanged.",
    "doc": "Update the README if one exists.",
}
FAILURES = [OSError("git missing"), subprocess.CalledProcessError(128, ["git"]), subprocess.TimeoutExpired("git", 60),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")]
FAILURE_IDS = ["OSError", "CalledProcessError", "TimeoutExpired", "UnicodeDecodeError"]


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _repo(path, files):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "Test")
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")
    return path


JAVA = {"pom.xml": "<project/>\n", "README.md": "# readme\n", "src/main/java/com/acme/App.java": "class App {}\n", "src/main/java/com/acme/Keep.java": "class Keep {}\n"}


def _engine():
    return types.SimpleNamespace(kernel=types.SimpleNamespace(config=None), derived_examples=None, derived_examples_report=None)


def _failing_ls_files(monkeypatch, error):
    real = worktree.git_read_lines

    def lines(cwd, *args, **kwargs):
        if args[:1] == ("ls-files",):
            raise error
        return real(cwd, *args, **kwargs)
    monkeypatch.setattr(worktree, "git_read_lines", lines)


def _bindings(contract):
    entry = contract.entries[0]
    return sorted(binding.closer for binding in entry.bindings), sorted(residual.claim for residual in entry.residual)


# ---- the owner: a Git-backed root's tracked set, or the typed unavailability ---------------------------------------------------------------------------------------------------------------

def test_a_git_backed_root_answers_its_tracked_set(tmp_path):
    root = _repo(tmp_path / "repo", JAVA)
    (root / "untracked.txt").write_text("x\n", encoding="utf-8")
    assert authoritative_tracked_workspace_paths(str(root)) == sorted(JAVA) == requirement_closers._tracked_workspace_paths(str(root))   # pylint: disable=protected-access


@pytest.mark.parametrize("error", FAILURES, ids=FAILURE_IDS)
def test_an_operational_git_failure_on_a_git_backed_root_is_the_typed_unavailability_not_an_empty_repository(tmp_path, monkeypatch, error):
    root = _repo(tmp_path / "repo", JAVA)
    _failing_ls_files(monkeypatch, error)
    with pytest.raises(BoundTreeError) as raised:
        authoritative_tracked_workspace_paths(str(root))
    assert raised.value.reason_code == BOUND_TREE_UNAVAILABLE and type(error).__name__ in str(raised.value) and raised.value.__cause__ is error
    assert requirement_closers._tracked_workspace_paths(str(root)) == []                       # pylint: disable=protected-access  # the closers' pinned semantics, unchanged


def test_a_repository_git_contradicts_is_the_typed_unavailability(tmp_path):
    root = _repo(tmp_path / "repo", JAVA)
    (root / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")
    with pytest.raises(BoundTreeError):
        authoritative_tracked_workspace_paths(str(root))


def test_a_workspace_that_does_not_exist_is_not_an_empty_repository(tmp_path):
    with pytest.raises(BoundTreeError):
        authoritative_tracked_workspace_paths(str(tmp_path / "missing"))


def test_the_no_git_branch_is_unchanged_it_is_still_the_empty_answer(tmp_path):
    """U4's decision, not U2's: a workspace with no repository keeps answering [] (MEASURED: ``language=None`` and the dropped bindings), exactly as before."""
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    assert authoritative_tracked_workspace_paths(str(tmp_path)) == [] == requirement_closers._tracked_workspace_paths(str(tmp_path))   # pylint: disable=protected-access


# ---- the original symptom: the contract the run is sealed with ----------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, goal", sorted(GOALS.items()))
@pytest.mark.parametrize("error", FAILURES, ids=FAILURE_IDS)
def test_a_git_failure_never_seals_a_degraded_contract(tmp_path, monkeypatch, name, goal, error):
    """Pre-fix: this returned a contract with ``project_language=None`` whose freeze / API bindings were gone and whose conditional documentation request was sealed as
    ``documentation_not_applicable`` although README.md exists. Now the compilation itself is the typed stop."""
    root = _repo(tmp_path / "repo", JAVA)
    compile_run_contract(_engine(), goal, goal, str(root), derive_requirements(goal))          # matched control: listable
    _failing_ls_files(monkeypatch, error)
    with pytest.raises(BoundTreeError) as raised:
        compile_run_contract(_engine(), goal, goal, str(root), derive_requirements(goal))
    assert raised.value.reason_code == BOUND_TREE_UNAVAILABLE


def test_the_listable_contract_is_the_one_the_original_probe_measured(tmp_path):
    root = _repo(tmp_path / "repo", JAVA)
    freeze = compile_run_contract(_engine(), GOALS["freeze"], GOALS["freeze"], str(root), derive_requirements(GOALS["freeze"]))
    api = compile_run_contract(_engine(), GOALS["api"], GOALS["api"], str(root), derive_requirements(GOALS["api"]))
    doc = compile_run_contract(_engine(), GOALS["doc"], GOALS["doc"], str(root), derive_requirements(GOALS["doc"]))
    assert freeze.project_language == "java" and _bindings(freeze)[0] == ["file_immutability"]
    assert _bindings(api)[0] == ["api_preservation"]
    assert _bindings(doc) == ([], ["DOCUMENTATION"])                                                  # README exists: a content claim, NOT "not applicable"


def test_a_no_git_workspace_still_seals_what_it_sealed_before(tmp_path):
    for relative, content in JAVA.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(content, encoding="utf-8")
    freeze = compile_run_contract(_engine(), GOALS["freeze"], GOALS["freeze"], str(tmp_path), derive_requirements(GOALS["freeze"]))
    assert freeze.project_language is None and _bindings(freeze)[0] == []                                # U4's measured finding, deliberately not changed here


# ---- the CLI admission: the same unavailability, as the existing typed refusal -----------------------------------------------------------------------------------------------------------

def test_the_cli_authority_admission_refuses_typed_when_a_git_backed_root_cannot_be_listed(tmp_path, monkeypatch):
    from _strict_doubles import strict_config

    from kriya.cli import _bind_verification_authority
    root = _repo(tmp_path / "repo", JAVA)
    missing_bundle = str(tmp_path / "authority.json")
    with pytest.raises(AuthorityBundleError) as control:                                                # listable: refused LATER, for the missing bundle - not for membership
        _bind_verification_authority(strict_config(), missing_bundle, "Do something.", str(root))
    assert AUTHORITY_EXECUTION_UNAVAILABLE not in str(control.value)
    _failing_ls_files(monkeypatch, OSError("git missing"))
    with pytest.raises(AuthorityBundleError) as raised:
        _bind_verification_authority(strict_config(), missing_bundle, "Do something.", str(root))
    assert raised.value.reason_code == AUTHORITY_EXECUTION_UNAVAILABLE and "git missing" in str(raised.value)


# ---- structural guards ------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def _function(path, name):
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def _called(path, function):
    return {getattr(call.func, "attr", getattr(call.func, "id", None)) for call in ast.walk(_function(path, function)) if isinstance(call, ast.Call)}


@pytest.mark.parametrize("path, function, listing", [("kriya/workflow/workflow.py", "compile_run_contract", "authoritative_tracked_workspace_paths"),
                                                     ("kriya/workflow/workflow.py", "admission_tracked_workspace_paths", "authoritative_tracked_workspace_paths"),
                                                     ("kriya/cli.py", "_bind_verification_authority", "admission_tracked_workspace_paths")])
def test_the_contract_sealing_callers_use_the_authoritative_listing_only(path, function, listing):
    called = _called(path, function)
    assert listing in called and "_tracked_workspace_paths" not in called


def test_the_cli_names_no_repository_layer_exception():
    """ADAPTER-CLI -> REPOSITORY is not an allowed direction (tests/architecture/module_map.py): the mapping lives in the workflow layer's admission helper."""
    assert "bound_tree" not in (REPO_ROOT / "kriya/cli.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("function", ["close_requirements_by_api_preservation", "close_requirements_by_documentation"])
def test_the_closers_keep_the_lenient_listing_their_pins_describe(function):
    called = _called("kriya/workflow/requirement_closers.py", function)
    assert "_tracked_workspace_paths" in called and "authoritative_tracked_workspace_paths" not in called


def test_the_authoritative_listing_dispatches_on_the_root_class_and_never_returns_a_list_from_a_failed_read():
    node = _function("kriya/workflow/requirement_closers.py", "authoritative_tracked_workspace_paths")
    handlers = [h for t in ast.walk(node) if isinstance(t, ast.Try) for h in t.handlers]
    assert len(handlers) == 1 and any(isinstance(sub, ast.Raise) for sub in ast.walk(handlers[0]))
    assert not [sub for sub in ast.walk(handlers[0]) if isinstance(sub, ast.Return)]
    assert "workspace_root_class" in _called("kriya/workflow/requirement_closers.py", "authoritative_tracked_workspace_paths")

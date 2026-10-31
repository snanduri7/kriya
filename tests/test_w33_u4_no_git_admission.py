"""W3-3 U4 (owner decision 2026-10-31): production admission of a workspace that has no Git repository.

An EMPTY NO_GIT workspace (no entry except Kriya's own ``.kriya``) is a supported greenfield: the existing Git bootstrap runs. Any other entry (a README, a ``.gitignore``,
source, a user's hidden file) makes it a brownfield WITHOUT source authority, and it is refused before contract compilation and before any development: the filesystem is
never a membership authority, the bootstrap never commits user files, and ``language`` is never degraded to ``None``. The refusal is the typed
``AUTHORITY_EXECUTION_UNAVAILABLE`` stop whose ``membership_authority.reason_code`` is ``BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY``.

Module-level imports are restricted to names that exist before U4 so the file collects (and fails, test by test) against the pre-U4 tree; the new names are imported inside the tests.
"""
import ast
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NEW_CODE = "BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY"


def _put(root, rel, text="x\n"):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=str(cwd), check=True, capture_output=True, text=True).stdout


def _digest(root):
    """Every file below ``root`` except Kriya's own ``.kriya`` state (and the repository a bootstrap may create) -> content hash."""
    out = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".kriya", ".git")]
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                out[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return out


def _admit(path):
    from kriya.repository.bound_tree import admit_workspace_for_mutation

    return admit_workspace_for_mutation(str(path))


# --- the admission decision ---------------------------------------------------------------------------------------------------------------------------------


def test_an_empty_non_git_workspace_is_a_supported_greenfield(tmp_path):
    _admit(tmp_path)


def test_a_non_git_workspace_holding_only_kriyas_own_state_is_still_empty(tmp_path):
    _put(tmp_path, ".kriya/run.lock", "")
    _put(tmp_path, ".kriya/state/control.json", "{}")
    _admit(tmp_path)


@pytest.mark.parametrize("entry", ["README.md", ".gitignore", "src/App.java", "Keep.java", ".env", ".hidden", "kriya.yaml", "build/out.txt", ".kriya.bak"])
def test_any_other_entry_makes_a_non_git_workspace_a_refused_brownfield(tmp_path, entry):
    from kriya.repository.bound_tree import BoundTreeError

    _put(tmp_path, entry)
    with pytest.raises(BoundTreeError) as stop:
        _admit(tmp_path)
    assert stop.value.reason_code == NEW_CODE
    assert entry.split("/")[0] in stop.value.paths
    assert "git init" in stop.value.detail and "commit" in stop.value.detail          # the operator is told what to do


def test_an_empty_directory_entry_is_an_entry(tmp_path):
    from kriya.repository.bound_tree import BoundTreeError

    (tmp_path / "empty_dir").mkdir()
    with pytest.raises(BoundTreeError) as stop:
        _admit(tmp_path)
    assert stop.value.reason_code == NEW_CODE


def test_kriya_state_next_to_user_files_does_not_make_the_workspace_empty(tmp_path):
    from kriya.repository.bound_tree import BoundTreeError

    _put(tmp_path, ".kriya/run.lock", "")
    _put(tmp_path, "README.md")
    with pytest.raises(BoundTreeError) as stop:
        _admit(tmp_path)
    assert stop.value.paths == ("README.md",)                                            # .kriya is not reported as a user entry


def test_a_git_backed_brownfield_is_admitted(tmp_path):
    _git(tmp_path, "init", "-q")
    _put(tmp_path, "Keep.java", "class Keep {}\n")
    _put(tmp_path, "untracked.txt")
    _git(tmp_path, "add", "Keep.java")
    _git(tmp_path, "commit", "-qm", "base")
    _admit(tmp_path)


def test_a_git_backed_empty_repository_and_a_subdirectory_of_a_repository_are_admitted(tmp_path):
    _git(tmp_path, "init", "-q")
    _admit(tmp_path)
    (tmp_path / "sub").mkdir()
    _put(tmp_path, "sub/a.py")
    _admit(tmp_path / "sub")


def test_a_directory_that_does_not_exist_is_left_to_the_caller(tmp_path):
    _admit(tmp_path / "not_there")


def test_an_unreadable_workspace_listing_is_a_typed_unavailable_error_not_an_admission(tmp_path, monkeypatch):
    from kriya.repository import bound_tree
    from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError

    def refuse(path):
        raise PermissionError(13, "denied", path)

    monkeypatch.setattr(bound_tree.os, "listdir", refuse)
    with pytest.raises(BoundTreeError) as stop:
        _admit(tmp_path)
    assert stop.value.reason_code == BOUND_TREE_UNAVAILABLE


def test_the_three_causes_have_three_distinct_stable_reason_codes():
    from kriya.repository import bound_tree

    codes = {bound_tree.BOUND_TREE_UNAVAILABLE, bound_tree.BOUND_TREE_OPERATING_SCOPE_EXCLUDED, bound_tree.BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY}
    assert len(codes) == 3
    assert bound_tree.BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY == NEW_CODE


def test_there_is_still_no_filesystem_membership_for_a_non_git_workspace(tmp_path):
    from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError, bound_tree_paths

    _put(tmp_path, "README.md")
    with pytest.raises(BoundTreeError) as stop:
        bound_tree_paths(str(tmp_path))
    assert stop.value.reason_code == BOUND_TREE_UNAVAILABLE


# --- the second guard: the tracked set that seals a contract, and the CLI admission --------------------------------------------------------------------------


def test_the_contract_sealing_tracked_listing_refuses_a_non_empty_non_git_workspace_and_answers_empty_for_a_greenfield(tmp_path):
    from kriya.repository.bound_tree import BoundTreeError
    from kriya.workflow.requirement_closers import authoritative_tracked_workspace_paths

    assert authoritative_tracked_workspace_paths(str(tmp_path)) == []
    _put(tmp_path, "pom.xml", "<project/>\n")
    with pytest.raises(BoundTreeError) as stop:
        authoritative_tracked_workspace_paths(str(tmp_path))
    assert stop.value.reason_code == NEW_CODE


def test_the_cli_verification_authority_admission_maps_the_refusal_to_the_typed_authority_error(tmp_path):
    from kriya.workflow.authority_bundle import AUTHORITY_EXECUTION_UNAVAILABLE, AuthorityBundleError
    from kriya.workflow.workflow import admission_tracked_workspace_paths

    assert admission_tracked_workspace_paths(str(tmp_path)) == []
    _put(tmp_path, "src/Main.java", "class Main {}\n")
    with pytest.raises(AuthorityBundleError) as stop:
        admission_tracked_workspace_paths(str(tmp_path))
    assert stop.value.reason_code == AUTHORITY_EXECUTION_UNAVAILABLE


def test_the_greenfield_bootstrap_refuses_a_non_empty_folder_and_creates_no_repository(tmp_path):
    from kriya.repository.bound_tree import BoundTreeError
    from kriya.workflow.worktree import _bootstrap_greenfield_repository

    _put(tmp_path, "README.md")
    with pytest.raises(BoundTreeError) as stop:
        _bootstrap_greenfield_repository(str(tmp_path))
    assert stop.value.reason_code == NEW_CODE
    assert not (tmp_path / ".git").exists()                                              # no repository, hence no commit of the user's files


def test_create_git_worktree_keeps_the_refusal_typed_and_creates_no_repository(tmp_path):
    """The sandbox entry wraps every bootstrap failure as a RuntimeError; the admission refusal is not a bootstrap failure and must stay the typed BoundTreeError."""
    from kriya.repository.bound_tree import BoundTreeError
    from kriya.workflow.worktree import create_git_worktree

    _put(tmp_path, "README.md")
    with pytest.raises(BoundTreeError) as stop:
        create_git_worktree(str(tmp_path))
    assert stop.value.reason_code == NEW_CODE
    assert not (tmp_path / ".git").exists() and (tmp_path / "README.md").read_text() == "x\n"


# --- end to end through the real direct pipeline ------------------------------------------------------------------------------------------------------------


def _direct(workspace, goal, *, policy=None, **kwargs):
    from _chaos_harness import ChaosRuntime, RuntimeRegistration, benign_roles, chaos_config, chaos_engine, run_direct

    cfg = chaos_config(**({"requirement_unverified_policy": policy} if policy else {}))
    runtime = ChaosRuntime(lambda role, request: "def add(a, b):\n    return a + b\n" if role == "developer" else benign_roles(role, request, target="app.py"))
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg), goal, workspace, **kwargs)
    return result, runtime


@pytest.mark.parametrize("policy", [None, "record", "block"])
def test_a_real_run_on_a_non_empty_non_git_workspace_is_refused_typed_before_any_model_call_and_mutates_nothing(tmp_path, policy):
    _put(tmp_path, "keep.py", "KEEP = 1\n")
    _put(tmp_path, "README.md", "hello\n")
    before = _digest(tmp_path)
    result, runtime = _direct(tmp_path, "Add an add function to app.py. Do not modify keep.py.", policy=policy)
    assert result["quality_gates_passed"] is False
    assert result["reason_codes"] == ["AUTHORITY_EXECUTION_UNAVAILABLE"]
    assert result["membership_authority"]["reason_code"] == NEW_CODE
    assert runtime.requests == []                                                         # refused before contract compilation: no model was asked anything
    assert _digest(tmp_path) == before and not (tmp_path / "app.py").exists()
    assert not (tmp_path / ".git").exists()                                               # no bootstrap, no commit of the user's files


def test_a_run_that_compiles_no_contract_is_refused_too(tmp_path):
    """``kriya fix``-style units (requirements_from_goal=False) never reach compile_run_contract; the admission is not inside it."""
    _put(tmp_path, "Keep.java", "class Keep {}\n")
    before = _digest(tmp_path)
    result, runtime = _direct(tmp_path, "Fix compilation failure in Keep.java", requirements_from_goal=False)
    assert result["reason_codes"] == ["AUTHORITY_EXECUTION_UNAVAILABLE"]
    assert result["membership_authority"]["reason_code"] == NEW_CODE
    assert runtime.requests == [] and _digest(tmp_path) == before and not (tmp_path / ".git").exists()


def test_a_hard_requirement_about_an_existing_file_cannot_silently_disappear(tmp_path):
    """Pre-U4 a NO_GIT folder sealed ``tracked == []``: file_immutability for Keep.java and the API-preservation binding were dropped and the run proceeded."""
    _put(tmp_path, "Keep.java", "class Keep { public int api() { return 1; } }\n")
    before = _digest(tmp_path)
    result, _ = _direct(tmp_path, "Add a method to Keep.java but preserve its public API. Do not modify Keep.java's api().", policy="block")
    assert result["reason_codes"] == ["AUTHORITY_EXECUTION_UNAVAILABLE"]
    assert _digest(tmp_path) == before


def test_a_real_run_on_an_empty_non_git_workspace_is_still_a_greenfield_and_bootstraps_git(tmp_path):
    result, runtime = _direct(tmp_path, "Create app.py with an add function")
    assert result.get("reason_codes") != ["AUTHORITY_EXECUTION_UNAVAILABLE"], result
    assert "membership_authority" not in result
    assert runtime.count("developer") >= 1                                                # development really started
    assert (tmp_path / ".git").is_dir()                                                   # the existing bootstrap ran


def test_a_real_run_on_a_git_backed_brownfield_is_unchanged(tmp_path):
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

    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    runtime = ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), "add sub to calc.py", workspace)
    assert result.get("reason_codes") != ["AUTHORITY_EXECUTION_UNAVAILABLE"] and "membership_authority" not in result
    assert runtime.count("developer") >= 1


def test_the_enforce_path_refuses_a_non_empty_non_git_workspace_before_any_work(tmp_path):
    import _enforce_unit_harness as harness

    run = harness.run_enforce(tmp_path, plan=_plan(), units={"s1": ("app.py", "x = 1\n")}, goal="Add app.py. Do not modify README.md.",
                              base_files={"README.md": "base\n"}, init_git=False)
    result = run.result.legacy_result
    assert result["status"] == "failure" and result.get("quality_gates_passed") is False
    assert result["membership_authority"]["reason_code"] == NEW_CODE
    assert not (run.workspace / ".git").exists() and not (run.workspace / "app.py").exists()
    assert (run.workspace / "README.md").read_text() == "base\n"


def _plan():
    from test_greenfield_runtime_verification_dropped import _plan as greenfield_plan

    return greenfield_plan()


# --- structure: the admission precedes the contract on both paths -----------------------------------------------------------------------------------------


def _calls(node):
    return [(n.lineno, getattr(n.func, "attr", getattr(n.func, "id", ""))) for n in ast.walk(node) if isinstance(n, ast.Call)]


def test_the_direct_path_admits_the_workspace_before_it_compiles_the_contract():
    tree = ast.parse((ROOT / "kriya/workflow/workflow.py").read_text())
    runs = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_generation_workflow"]
    assert len(runs) == 1
    tries = [t for t in ast.walk(runs[0]) if isinstance(t, ast.Try) and any(name == "compile_run_contract" for _, name in _calls(t))]
    assert len(tries) == 1
    admits = [line for line, name in _calls(tries[0]) if name == "admit_workspace_for_mutation"]
    compiles = [line for line, name in _calls(tries[0]) if name == "compile_run_contract"]
    assert len(admits) == 1 and admits and compiles and admits[0] < compiles[0]
    # unconditional: not nested under the ``requirement_set is not None`` conditional expression that guards the contract
    guarded = [n for n in ast.walk(tries[0]) if isinstance(n, ast.IfExp) and any(name == "admit_workspace_for_mutation" for _, name in _calls(n))]
    assert not guarded
    assert any(isinstance(h.type, ast.Name) and h.type.id == "BoundTreeError" for h in tries[0].handlers)


def test_the_enforce_path_admits_the_workspace_before_it_compiles_the_contract():
    tree = ast.parse((ROOT / "kriya/workflow/enforce_loop.py").read_text())
    calls = _calls(tree)
    admits = [line for line, name in calls if name == "admit_workspace_for_mutation"]
    compiles = [line for line, name in calls if name == "compile_run_contract"]
    assert len(admits) == 1 and len(compiles) == 1 and admits[0] < compiles[0]

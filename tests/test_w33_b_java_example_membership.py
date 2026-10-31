"""W3-3 unit B: the Java example oracle's type lookups decide membership by the canonical bound tree, not by a directory name.

MEASURED at 6062a01 (the W3-0 reproducers, probes/probe_jvm_duplicate_hidden_flip.py): ``example_oracle_java.resolve_java_type`` / ``main_source_path`` /
``repository_license_header`` walked the candidate with a basename prune (``target``, ``build``, ``node_modules``), so a tracked ``com.acme.build.Gen`` was "no main
compilation unit" and a duplicate hidden under such a name made the visible one a confident UNIQUE type - the goal's example bound to the wrong class.

Contract (owner decisions, D-001 R1): Git-backed root (the root itself, a subdirectory of its repository, a linked worktree) -> the bound tree's real files, relative to
the operating root; a root with no canonical membership owner today (pre-bootstrap non-Git workspace, scoped-snapshot sandbox) keeps the legacy walk UNCHANGED (PARKED);
a Git-backed root Git cannot list is the typed ``BoundTreeError`` - never "no such type", never the legacy walk - and ``compile_run_contract`` lets it out (the
entrypoints end the run as the existing typed ``AUTHORITY_EXECUTION_UNAVAILABLE`` stop) instead of recording a compiler defect and continuing without the user's examples.
"""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from test_s19e_honest_refusal import (  # noqa: E402  # pylint: disable=wrong-import-position
    _isolated_git,  # noqa: F401  # autouse fixture of that module, re-exported so it applies here
)
from test_workflow_controller_enforce import _workflow_engine  # noqa: E402  # pylint: disable=wrong-import-position

from kriya.config import AppConfig
from kriya.control.run_coordinator import begin_mutating_run
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError
from kriya.workflow import example_oracle_java as ej
from kriya.workflow.authority_bundle import AUTHORITY_EXECUTION_UNAVAILABLE
from kriya.workflow.requirements import derive_requirements
from kriya.workflow.workflow import WorkflowEngine, compile_run_contract
from kriya.workflow.workflow_controller import WorkflowController
from kriya.workflow.worktree import _create_scoped_snapshot_sandbox, candidate_member_files

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN = "src/main/java"
LICENSE = "/*\n * Licensed to the Acme Software Foundation under one license.\n */\n"


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _write(root, files):
    for relative, content in files.items():
        target = Path(root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _repo(path, files):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "Test")
    _write(path, files)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _unit(package, name, body="class X {}\n"):
    return f"{MAIN}/{package.replace('.', '/')}/{name}.java", f"package {package};\n{body}"


def _files(*units):
    return dict(units)


# ---- the original symptom: a directory NAME decided membership ------------------------------------------------------------------------------------

@pytest.mark.parametrize("segment", ["build", "target", "node_modules"])
def test_a_tracked_package_named_like_an_output_directory_is_a_member(tmp_path, segment):
    root = _repo(tmp_path / "repo", _files(_unit(f"com.acme.{segment}", "Gen"), _unit("com.acme.core", "Util")))
    assert ej.resolve_java_type(str(root), "Gen") == (f"com.acme.{segment}.Gen", "")
    assert ej.main_source_path(str(root), f"com.acme.{segment}.Gen") == f"{MAIN}/com/acme/{segment}/Gen.java"
    assert ej.resolve_java_type(str(root), "Util") == ("com.acme.core.Util", "")             # the matched control (a name no prune ever touched)


@pytest.mark.parametrize("segment", ["build", "target"])
def test_a_duplicate_under_an_output_named_directory_makes_the_type_ambiguous_not_unique(tmp_path, segment):
    root = _repo(tmp_path / "repo", {**_files(_unit("com.acme.core", "Gen")), **{f"other/{segment}/{path}": body for path, body in _files(_unit("com.acme.util", "Gen")).items()}})
    qualified, why = ej.resolve_java_type(str(root), "Gen")
    assert qualified is None and "ambiguous" in why and "com.acme.core.Gen" in why and "com.acme.util.Gen" in why
    assert ej.resolve_java_type(str(root), "com.acme.core.Gen") == ("com.acme.core.Gen", "")  # a qualified name stays decidable
    assert ej.main_source_path(str(root), "com.acme.core.Gen") == f"{MAIN}/com/acme/core/Gen.java"


def test_a_git_ignored_output_is_not_a_member_and_the_same_file_unignored_is(tmp_path):
    generated = f"target/generated-sources/{MAIN}/com/acme/gen/Gen.java"
    root = _repo(tmp_path / "repo", {**_files(_unit("com.acme.core", "Gen")), ".gitignore": "target/\n"})
    _write(root, {generated: "package com.acme.gen;\nclass Gen {}\n"})                       # untracked AND ignored: not Git's view
    assert ej.resolve_java_type(str(root), "Gen") == ("com.acme.core.Gen", "")
    (root / ".gitignore").write_text("", encoding="utf-8")                                    # control: untracked and VISIBLE is a member
    qualified, why = ej.resolve_java_type(str(root), "Gen")
    assert qualified is None and "ambiguous" in why


def test_an_untracked_visible_source_is_a_member(tmp_path):
    root = _repo(tmp_path / "repo", _files(_unit("com.acme.core", "Util")))
    _write(root, _files(_unit("com.acme.build", "Fresh")))
    assert ej.resolve_java_type(str(root), "Fresh") == ("com.acme.build.Fresh", "")


def test_a_tracked_path_deleted_from_the_work_tree_is_not_a_member(tmp_path):
    path, body = _unit("com.acme.core", "Gone")
    root = _repo(tmp_path / "repo", {path: body, **_files(_unit("com.acme.core", "Kept"))})
    (root / path).unlink()
    qualified, why = ej.resolve_java_type(str(root), "Gone")
    assert qualified is None and "no main compilation unit" in why
    assert ej.main_source_path(str(root), "com.acme.core.Gone") is None
    assert ej.resolve_java_type(str(root), "Kept") == ("com.acme.core.Kept", "")


def test_the_license_header_is_read_from_a_member_under_an_output_named_package(tmp_path):
    path, _ = _unit("com.acme.build", "Gen")
    root = _repo(tmp_path / "repo", {path: LICENSE + "package com.acme.build;\nclass Gen {}\n"})
    assert ej.repository_license_header(str(root), ["com.acme.build.Gen"]) == LICENSE.rstrip("\n") + "\n"


def test_the_member_order_is_the_walk_order_parents_before_children_siblings_sorted(tmp_path):
    root = _repo(tmp_path / "repo", _files(_unit("b", "B"), _unit("a.z", "C"), _unit("a", "A")) | {"top.txt": "x"})
    seen = [os.path.relpath(directory, root) for directory, _names in ej._java_source_directories(str(root))]  # pylint: disable=protected-access
    assert seen == sorted(seen, key=lambda relative: [part for part in relative.split(os.sep) if part] if relative != "." else [])
    assert seen[0] == "."                                                                    # the root directory (top.txt) comes first, as os.walk does


# ---- D-001: the operating root may be a subdirectory of its own repository --------------------------------------------------------------------------

def test_a_subdirectory_of_its_own_repository_sees_its_own_subtree_only(tmp_path):
    files = {f"app/{path}": body for path, body in _files(_unit("com.acme.build", "Gen")).items()}
    files |= {f"other/{path}": body for path, body in _files(_unit("com.acme.core", "Gen")).items()}   # a sibling's duplicate must not be seen
    root = _repo(tmp_path / "repo", files)
    sub = str(root / "app")
    assert candidate_member_files(sub) == [f"{MAIN}/com/acme/build/Gen.java"]
    assert ej.resolve_java_type(sub, "Gen") == ("com.acme.build.Gen", "")
    assert ej.main_source_path(sub, "com.acme.build.Gen") == f"{MAIN}/com/acme/build/Gen.java"
    assert ej.resolve_java_type(str(root), "Gen")[0] is None                                  # from the repository root the same two ARE ambiguous (control)


def test_a_nested_repository_with_its_own_git_is_its_own_tree(tmp_path):
    outer = _repo(tmp_path / "repo", _files(_unit("com.acme.core", "Gen")))
    inner = _repo(outer / "vendor" / "lib", _files(_unit("com.acme.build", "Gen")))
    assert ej.resolve_java_type(str(inner), "Gen") == ("com.acme.build.Gen", "")


# ---- PARKED root classes keep the legacy walk, UNCHANGED (characterization; not an endorsement) ----------------------------------------------------

def test_a_non_git_workspace_has_no_canonical_membership_and_keeps_the_legacy_walk(tmp_path):
    root = tmp_path / "greenfield"
    _write(root, {**_files(_unit("com.acme.build", "Hidden"), _unit("com.acme.core", "Seen"))})
    assert candidate_member_files(str(root)) is None
    assert ej.resolve_java_type(str(root), "Seen") == ("com.acme.core.Seen", "")
    assert ej.resolve_java_type(str(root), "Hidden")[0] is None                               # PARKED: the basename prune still applies here


def test_the_scoped_snapshot_sandbox_has_the_canonical_membership_of_its_projection(tmp_path):
    """W3-3 C2 (was the PARKED legacy-walk pin): the sandbox's members are the projection captured when it was built, so a legitimate ``build`` package resolves."""
    enclosing = _repo(tmp_path / "enclosing", {"README.md": "enclosing\n"})
    workspace = enclosing / "ws"
    _write(workspace, _files(_unit("com.acme.build", "Hidden"), _unit("com.acme.core", "Seen")))
    sandbox = workspace / ".kriya" / "scoped-worktree"
    _create_scoped_snapshot_sandbox(str(workspace), str(sandbox))
    assert (sandbox / ".git").is_dir()                                                         # the synthetic repository exists, so a git-only test would call it Git-backed
    assert candidate_member_files(str(sandbox)) is not None
    assert ej.resolve_java_type(str(sandbox), "Seen") == ("com.acme.core.Seen", "")
    assert ej.resolve_java_type(str(sandbox), "Hidden") == ("com.acme.build.Hidden", "")


def test_a_look_alike_sentinel_outside_a_managed_location_is_still_git_backed(tmp_path):
    root = _repo(tmp_path / "repo", _files(_unit("com.acme.build", "Gen")))
    (root / ".kriya-scoped-snapshot").write_text("not a Kriya sandbox\n", encoding="utf-8")
    assert f"{MAIN}/com/acme/build/Gen.java" in (candidate_member_files(str(root)) or [])        # not None: only a managed sandbox location parks a root
    assert ej.resolve_java_type(str(root), "Gen") == ("com.acme.build.Gen", "")


# ---- a Git-backed root Git cannot list is a typed stop, never "no such type" and never the legacy walk ---------------------------------------------

LOOKUPS = [
    ("resolve_java_type", lambda root: ej.resolve_java_type(root, "Gen")),
    ("main_source_path", lambda root: ej.main_source_path(root, "com.acme.build.Gen")),
    ("repository_license_header", lambda root: ej.repository_license_header(root, ["com.acme.build.Gen"])),
]


@pytest.mark.parametrize("name,lookup", LOOKUPS, ids=[name for name, _ in LOOKUPS])
def test_a_failing_listing_is_the_typed_error_not_an_empty_answer(tmp_path, name, lookup):
    path, _ = _unit("com.acme.build", "Gen")
    root = _repo(tmp_path / "repo", {path: LICENSE + "package com.acme.build;\nclass Gen {}\n"})
    assert lookup(str(root))                                                                  # the matched control: the same lookup answers on the same root
    with patch("kriya.repository.bound_tree.visible_repository_paths", side_effect=RuntimeError("git ls-files failed: injected")):
        with pytest.raises(BoundTreeError) as raised:
            lookup(str(root))
    assert raised.value.reason_code == BOUND_TREE_UNAVAILABLE and "injected" in str(raised.value)


@pytest.mark.parametrize("name,lookup", LOOKUPS, ids=[name for name, _ in LOOKUPS])
def test_a_repository_git_refuses_is_the_typed_error_not_the_legacy_walk(tmp_path, name, lookup):
    root = _repo(tmp_path / "repo", _files(_unit("com.acme.core", "Gen")))
    (root / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")                          # D-001 finds the repository; Git cannot read it
    with pytest.raises(BoundTreeError):
        candidate_member_files(str(root))
    with pytest.raises(BoundTreeError):
        lookup(str(root))


# ---- the compile path lets the typed error out instead of recording a compiler defect --------------------------------------------------------------

JAVA_GOAL = "Add a static twice(n) method to Calc in src/main/java/demo/Calc.java.\n\nCalc.twice(2) -> 4\n"


def _java_compile_engine():
    return SimpleNamespace(kernel=SimpleNamespace(config=AppConfig()))


def _java_repo(path):
    return _repo(path, {"pom.xml": "<project/>\n", **_files(_unit("demo", "Calc", "public class Calc { }\n"))})


def test_compile_run_contract_binds_the_example_authority_on_a_git_backed_workspace(tmp_path):
    """The matched control of the next test: the same call on the same workspace derives the sealed example class."""
    root = _java_repo(tmp_path / "repo")
    engine = _java_compile_engine()
    compile_run_contract(engine, JAVA_GOAL, JAVA_GOAL, str(root), derive_requirements(JAVA_GOAL))
    assert engine.derived_examples is not None and engine.derived_examples_report["refusal"] is None


def test_compile_run_contract_does_not_record_a_membership_failure_as_a_compiler_defect(tmp_path):
    root = _java_repo(tmp_path / "repo")
    engine = _java_compile_engine()
    error = BoundTreeError(BOUND_TREE_UNAVAILABLE, "git ls-files failed: injected at the contract compilation")
    with patch("kriya.workflow.example_oracle_java.candidate_member_files", side_effect=error):
        with pytest.raises(BoundTreeError, match="injected at the contract compilation"):
            compile_run_contract(engine, JAVA_GOAL, JAVA_GOAL, str(root), derive_requirements(JAVA_GOAL))


def test_a_non_membership_compiler_defect_is_still_recorded_not_raised(tmp_path):
    """The generic handler is unchanged for everything that is not the membership authority."""
    root = _java_repo(tmp_path / "repo")
    engine = _java_compile_engine()
    with patch("kriya.workflow.example_oracle_java.candidate_member_files", side_effect=ValueError("a compiler defect")):
        compile_run_contract(engine, JAVA_GOAL, JAVA_GOAL, str(root), derive_requirements(JAVA_GOAL))
    assert engine.derived_examples is None and engine.derived_examples_report["refusal"]["reason_code"] == "EXAMPLE_COMPILER_ERROR"


@pytest.mark.asyncio
async def test_the_direct_entrypoint_ends_a_contract_stage_membership_failure_as_the_typed_stop(tmp_path):
    _java_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=AssertionError("no model call may follow a contract-stage membership failure"))
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    error = BoundTreeError(BOUND_TREE_UNAVAILABLE, "git ls-files failed: injected at the contract stage")
    before = sorted(p.name for p in tmp_path.iterdir() if p.name not in (".git", ".kriya"))
    with patch("kriya.workflow.workflow.compile_run_contract", side_effect=error) as compiled:
        with begin_mutating_run(str(tmp_path)):
            result = await engine.run_generation_workflow(goal=JAVA_GOAL, workspace_path=str(tmp_path))
    assert compiled.called, "the contract compilation was never reached (the run ended somewhere else)"
    assert result["status"] == "failure" and result["quality_gates_passed"] is False and result["files"] == []
    assert result["reason_codes"] == [AUTHORITY_EXECUTION_UNAVAILABLE] and result["failure_category"] == "verification_authority_unavailable"
    assert "injected at the contract stage" in result["membership_authority"]["detail"]
    assert sorted(p.name for p in tmp_path.iterdir() if p.name not in (".git", ".kriya")) == before


@pytest.mark.asyncio
async def test_the_enforce_entrypoint_ends_a_contract_stage_membership_failure_as_the_typed_stop(tmp_path):
    controller = WorkflowController(_workflow_engine())
    controller._run_structured_enforce = AsyncMock(side_effect=BoundTreeError(BOUND_TREE_UNAVAILABLE, "git ls-files failed: injected in enforce"))  # pylint: disable=protected-access
    controller._run_legacy_generation = AsyncMock(side_effect=AssertionError("authoritative mode must not invoke legacy generation"))  # pylint: disable=protected-access
    result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")
    legacy = result.legacy_result
    assert legacy["status"] == "failure" and legacy["quality_gates_passed"] is False and legacy["files"] == []
    assert legacy["reason_codes"] == [AUTHORITY_EXECUTION_UNAVAILABLE] and legacy["failure_category"] == "verification_authority_unavailable"
    assert legacy["failure_type"] == "VERIFICATION_AUTHORITY_UNAVAILABLE" and "injected in enforce" in legacy["membership_authority"]["detail"]
    controller._run_legacy_generation.assert_not_called()  # pylint: disable=protected-access


# ---- structural guards -------------------------------------------------------------------------------------------------------------------------

def _function(path, name):
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def test_no_unit_b_lookup_walks_the_filesystem_directly():
    source = (REPO_ROOT / "kriya/workflow/example_oracle_java.py").read_text(encoding="utf-8")
    walks = [node.lineno for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call) and ast.unparse(node.func) == "os.walk"]
    owner = _function("kriya/workflow/example_oracle_java.py", "_java_source_directories")
    assert len(walks) == 1 and owner.lineno <= walks[0] <= owner.end_lineno, "the ONLY os.walk of the module is the PARKED branch of the one dispatch"
    for name in ("resolve_java_type", "main_source_path", "repository_license_header"):
        calls = {ast.unparse(node.func) for node in ast.walk(_function("kriya/workflow/example_oracle_java.py", name)) if isinstance(node, ast.Call)}
        assert "_java_source_directories" in calls and "os.walk" not in calls


def test_the_compile_handlers_re_raise_the_membership_error_ahead_of_the_generic_catch():
    compile_fn = _function("kriya/workflow/workflow.py", "compile_run_contract")
    handlers = [[ast.unparse(h.type) for h in node.handlers] for node in ast.walk(compile_fn) if isinstance(node, ast.Try)]
    assert ["BoundTreeError", "Exception"] in handlers
    reraise = next(h for node in ast.walk(compile_fn) if isinstance(node, ast.Try) for h in node.handlers if h.type is not None and ast.unparse(h.type) == "BoundTreeError")
    assert [type(statement).__name__ for statement in reraise.body] == ["Raise"] and reraise.body[0].exc is None


def test_the_direct_entrypoint_wraps_the_contract_compilation_and_the_controller_handles_the_enforce_one():
    entry = _function("kriya/workflow/workflow.py", "run_generation_workflow")
    wrapped = [node for node in ast.walk(entry) if isinstance(node, ast.Try) and any("compile_run_contract" in ast.unparse(stmt) for stmt in node.body)]
    assert len(wrapped) == 1 and [ast.unparse(h.type) for h in wrapped[0].handlers] == ["BoundTreeError"]
    assert "_membership_authority_stop" in ast.unparse(wrapped[0].handlers[0])
    execute = _function("kriya/workflow/workflow_controller.py", "execute")
    enforce = [node for node in ast.walk(execute) if isinstance(node, ast.Try) and any("_run_structured_enforce" in ast.unparse(stmt) for stmt in node.body)]
    assert len(enforce) == 1
    types = [ast.unparse(h.type) for h in enforce[0].handlers]
    assert "BoundTreeError" in types and types.index("BoundTreeError") < len(types) - 1

"""W3-3 unit U1: rows 6, 7 and 18 decide a Git-backed candidate's membership from the bound tree, not from a directory name; the Python runtime target is selected only from
DECLARED files.

MEASURED at 38f9131 (probe_roots.py P3, probe_py_target.py; both on a Git-backed ``.kriya/worktree`` candidate - the ordinary case, no scoped snapshot):
- row 18 (``runtime_verification._build_python_runtime_grounding``): a tracked ``pkg/build/runner.py`` was not a fact, so the judge's CORRECT target was rejected and
  ``pkg/core/other.py`` - a DIFFERENT program - was substituted for it;
- row 6 (``failure_grounding.resolve_repository_locator_files``) returned ``[]`` for a tracked owner under ``build/``/``target/``/``dist/`` and confidently picked the
  visible one of two same-named files; row 7 (``compile_regression._tree_index``) did not index a tracked ``build/`` source and indexed a git-IGNORED output.
The membership is ``worktree.candidate_member_paths`` / ``candidate_member_files`` (the D-001 bound tree). Row 18 adds the positive-declaration rule the artifact owners already
use (``file_resolution.declared_and_ambiguous_member_files``): membership is visible evidence, but only DECLARED (Git tracked, or written by this run) may be an
automatically selected runtime target - by NO directory name. The parked root classes (non-Git workspace, scoped-snapshot sandbox) keep the legacy walks unchanged; a
Git-backed root Git cannot list is the typed ``BoundTreeError`` and never ``[]`` / "no file" / "no entrypoint".
"""
import ast
import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError
from kriya.workflow import attempt_lifecycle
from kriya.workflow.attempt import _build_python_runtime_grounding, _collect_python_runtime_grounding_facts
from kriya.workflow.attempt_lifecycle import AttemptMode, AttemptRunner
from kriya.workflow.compile_regression import _tree_index, attribute_compile_regression
from kriya.workflow.failure_grounding import resolve_repository_locator_files
from kriya.workflow.file_resolution import ground_python_runtime_target
from kriya.workflow.retry_policy import decide_for_state
from kriya.workflow.state import GenerationState
from kriya.workflow.validation_baseline import BaselineDeltaResult, DeltaClassification, Level1Delta
from kriya.workflow.worktree import _create_scoped_snapshot_sandbox, candidate_has_canonical_membership

REPO_ROOT = Path(__file__).resolve().parents[1]
_SEGMENTS = ["core", "build", "target", "dist"]          # "core" is the matched control: it was never pruned
_MAIN = "if __name__ == '__main__':\n    print('run')\n"


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


def _unlistable(monkeypatch):
    def cannot_list(*_args, **_kwargs):
        raise RuntimeError("git ls-files failed: injected")
    monkeypatch.setattr("kriya.repository.bound_tree.visible_repository_paths", cannot_list)
    monkeypatch.setattr("kriya.repository.bound_tree.repository_paths_by_declaration", cannot_list)


def _delta():
    return BaselineDeltaResult(level1=Level1Delta(DeltaClassification.NEW_FAILURE, None, "fp"), level2={}, aggregate_drop_detected=False,
                               blocking=True, blocking_reasons=("level1",), level2_available=False)


def _diagnostic(path):
    return f"[ERROR] /w/{path}:[3,5] boom\n"


# =========================================================================== row 7: compile_regression._tree_index ===========================================================

@pytest.mark.parametrize("segment", _SEGMENTS)
def test_a_compiler_error_in_a_tracked_file_is_attributed_whatever_its_directory_is_named(tmp_path, segment):
    path = f"src/main/java/com/acme/{segment}/Gen.java"
    root = _repo(tmp_path / "repo", {path: "class Gen {}\n", "src/main/java/com/acme/Other.java": "class Other {}\n"})
    attribution = attribute_compile_regression(_delta(), _diagnostic(path), str(root))
    assert attribution is not None and attribution.files == (path,) and attribution.locations == ((path, 3),)


def test_a_git_ignored_output_is_not_a_file_of_the_candidate(tmp_path):
    root = _repo(tmp_path / "repo", {".gitignore": "out/\n", "src/Real.java": "class Real {}\n"})
    _write(root, {"out/gen/Junk.java": "class Junk {}\n"})                       # ignored build output present on disk
    assert "Junk.java" not in _tree_index(str(root)) and "Real.java" in _tree_index(str(root))
    assert attribute_compile_regression(_delta(), _diagnostic("out/gen/Junk.java"), str(root)) is None   # not attributed to a non-candidate file


def test_an_untracked_visible_source_is_a_file_of_the_candidate(tmp_path):
    root = _repo(tmp_path / "repo", {"src/Real.java": "class Real {}\n"})
    _write(root, {"src/build/New.java": "class New {}\n"})                          # written by the run, not yet committed
    assert _tree_index(str(root))["New.java"] == ["src/build/New.java"]


def test_a_symlink_is_never_an_indexed_file(tmp_path):
    root = _repo(tmp_path / "repo", {"src/Real.java": "class Real {}\n"})
    os.symlink(root / "src" / "Real.java", root / "src" / "Alias.java")
    _git(root, "add", "-A")
    assert "Alias.java" not in _tree_index(str(root))


# =========================================================================== row 6: failure_grounding.resolve_repository_locator_files =========================================

@pytest.mark.parametrize("segment", _SEGMENTS)
def test_a_locator_resolves_a_tracked_owner_whatever_its_directory_is_named(tmp_path, segment):
    root = _repo(tmp_path / "repo", {f"src/{segment}/Owner.java": "class Owner {}\n"})
    assert resolve_repository_locator_files("at example.Owner.run(Owner.java:1)", str(root), []) == [f"src/{segment}/Owner.java"]


@pytest.mark.parametrize("hidden", ["build", "target", "dist"])
def test_a_duplicate_under_a_named_directory_is_the_ambiguity_not_a_confident_pick(tmp_path, hidden):
    root = _repo(tmp_path / "repo", {f"src/{hidden}/Owner.java": "class Owner {}\n", "src/core/Owner.java": "class Owner {}\n"})
    assert resolve_repository_locator_files("at example.Owner.run(Owner.java:1)", str(root), []) == []


def test_a_git_ignored_duplicate_does_not_make_a_tracked_owner_ambiguous(tmp_path):
    root = _repo(tmp_path / "repo", {".gitignore": "out/\n", "src/Owner.java": "class Owner {}\n"})
    _write(root, {"out/gen/Owner.java": "class Owner {}\n"})
    assert resolve_repository_locator_files("at example.Owner.run(Owner.java:1)", str(root), []) == ["src/Owner.java"]


def test_a_symlink_is_never_a_resolved_owner(tmp_path):
    root = _repo(tmp_path / "repo", {"src/Real.java": "class Real {}\n"})
    os.symlink(root / "src" / "Real.java", root / "src" / "Alias.java")
    _git(root, "add", "-A")
    assert resolve_repository_locator_files("at x.Alias.run(Alias.java:1)", str(root), []) == []


def test_the_scan_bound_is_kept(tmp_path):
    root = _repo(tmp_path / "repo", {"src/A.java": "class A {}\n", "src/Owner.java": "class Owner {}\n"})
    assert resolve_repository_locator_files("at x.Owner.run(Owner.java:1)", str(root), [], max_files_scanned=1) == []
    assert resolve_repository_locator_files("at x.Owner.run(Owner.java:1)", str(root), [], max_files_scanned=10) == ["src/Owner.java"]


# =========================================================================== row 18: Python runtime target grounding ================================================================

def _judge(path):
    return [["python", path]]


@pytest.mark.parametrize("segment", ["build", "target", "dist"])
def test_the_judges_correct_target_under_a_named_directory_is_not_replaced_by_a_different_program(tmp_path, segment):
    """The measured wrong-program substitution: pre-fix ``pkg/<segment>/runner.py`` was invisible, so it was rejected and ``pkg/core/other.py`` ran instead."""
    root = _repo(tmp_path / "repo", {"pkg/__init__.py": "", f"pkg/{segment}/runner.py": _MAIN, "pkg/core/other.py": _MAIN})
    all_py, package_dirs, entrypoints = _build_python_runtime_grounding(str(root))
    assert f"pkg/{segment}/runner.py" in all_py and entrypoints == sorted([f"pkg/{segment}/runner.py", "pkg/core/other.py"])
    assert ground_python_runtime_target(_judge(f"pkg/{segment}/runner.py"), "judge", all_py, package_dirs, entrypoints) == _judge(f"pkg/{segment}/runner.py")


def test_the_matched_control_under_an_unpruned_name_is_unchanged(tmp_path):
    root = _repo(tmp_path / "repo", {"pkg/__init__.py": "", "pkg/core/runner.py": _MAIN, "pkg/util/other.py": _MAIN})
    all_py, package_dirs, entrypoints = _build_python_runtime_grounding(str(root))
    assert ground_python_runtime_target(_judge("pkg/core/runner.py"), "judge", all_py, package_dirs, entrypoints) == _judge("pkg/core/runner.py")


def test_a_package_under_a_named_directory_is_a_package_and_the_root_package_is_the_empty_name(tmp_path):
    root = _repo(tmp_path / "repo", {"__init__.py": "", "pkg/build/__init__.py": "", "pkg/build/mod.py": "X = 1\n"})
    _all_py, package_dirs, _entrypoints = _build_python_runtime_grounding(str(root))
    assert package_dirs == frozenset({"", "pkg/build"})
    assert _collect_python_runtime_grounding_facts(str(root))[1] == package_dirs


def test_a_git_ignored_output_is_not_a_python_fact_and_never_an_entrypoint(tmp_path):
    root = _repo(tmp_path / "repo", {".gitignore": "out/\n", "app/main.py": _MAIN})
    _write(root, {"out/gen/tool.py": _MAIN})
    all_py, _package_dirs, entrypoints = _build_python_runtime_grounding(str(root))
    assert all_py == frozenset({"app/main.py"}) and entrypoints == ["app/main.py"]


@pytest.mark.parametrize("untracked", ["zz_env/tool.py", "deps_cache_9/lib/run.py", "venv/bin/tool.py", "node_modules/pkg/cli.py", "whatever/build/x.py"])
def test_an_untracked_visible_file_is_evidence_but_never_an_automatic_runtime_target_by_any_name(tmp_path, untracked):
    """Arbitrary-name dependency control: the SAME treatment for a name the old list knew (venv, node_modules) and for names it never could (zz_env, deps_cache_9) -
    presence is not authority. The tracked entrypoint is the unique selectable one, so a test-shaped judge target is substituted by it, never by the untracked script."""
    root = _repo(tmp_path / "repo", {"app/main.py": _MAIN, "tests/test_app.py": "def test_x():\n    pass\n"})
    _write(root, {untracked: _MAIN})
    all_py, package_dirs, entrypoints = _build_python_runtime_grounding(str(root))
    assert untracked in all_py                                                          # visible evidence: the file exists
    assert entrypoints == ["app/main.py"]                                               # never selectable on presence
    assert ground_python_runtime_target(_judge("tests/test_app.py"), "judge", all_py, package_dirs, entrypoints) == _judge("app/main.py")


def test_a_file_written_by_this_run_is_declared_and_selectable(tmp_path):
    root = _repo(tmp_path / "repo", {"app/main.py": _MAIN})
    _write(root, {"gen/new_tool.py": _MAIN})                                            # written by this run, not yet committed
    assert _build_python_runtime_grounding(str(root))[2] == ["app/main.py"]
    assert _build_python_runtime_grounding(str(root), ["gen/new_tool.py"])[2] == ["app/main.py", "gen/new_tool.py"]
    assert _build_python_runtime_grounding(str(root), ["./gen/new_tool.py"])[2] == ["app/main.py", "gen/new_tool.py"]    # path-exact after normalisation
    assert _build_python_runtime_grounding(str(root), ["gen/other_tool.py"])[2] == ["app/main.py"]                         # a different path declares nothing


def test_a_judge_target_that_is_only_present_untracked_is_not_grounded_by_its_presence(tmp_path):
    root = _repo(tmp_path / "repo", {"app/main.py": _MAIN})
    _write(root, {"scripts/preexisting.py": _MAIN})
    all_py, package_dirs, entrypoints = _build_python_runtime_grounding(str(root))
    assert ground_python_runtime_target(_judge("scripts/preexisting.py"), "judge", all_py, package_dirs, entrypoints) == _judge("app/main.py")
    declared = _build_python_runtime_grounding(str(root), ["scripts/preexisting.py"])
    assert ground_python_runtime_target(_judge("scripts/preexisting.py"), "judge", *declared) == _judge("scripts/preexisting.py")


def test_a_symlink_member_that_resolves_outside_the_root_is_never_a_fact(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text(_MAIN, encoding="utf-8")
    root = _repo(tmp_path / "repo", {"app/main.py": _MAIN})
    os.symlink(outside, root / "app" / "escape.py")
    _git(root, "add", "-A")
    all_py, _package_dirs, entrypoints = _build_python_runtime_grounding(str(root))
    assert "app/escape.py" not in all_py and entrypoints == ["app/main.py"]


# =========================================================================== the parked root classes keep the legacy walks, UNCHANGED =================================================

def test_the_membership_predicate_parks_only_the_pre_git_workspace(tmp_path):    # W3-3 C2: the scoped sandbox now has a canonical membership owner
    git = _repo(tmp_path / "git", {"a.py": "X = 1\n"})
    plain = tmp_path / "plain"
    _write(plain, {"a.py": "X = 1\n"})
    sandbox = git / ".kriya" / "scoped-worktree"
    _create_scoped_snapshot_sandbox(str(git), str(sandbox))
    assert (candidate_has_canonical_membership(str(git)), candidate_has_canonical_membership(str(plain)), candidate_has_canonical_membership(str(sandbox))) == (True, False, True)


def test_a_pre_git_workspace_keeps_the_legacy_python_walk_and_locator_walks(tmp_path):
    _write(tmp_path, {"pkg/__init__.py": "", "pkg/build/runner.py": _MAIN, "pkg/core/other.py": _MAIN, "src/build/Owner.java": "class Owner {}\n", "src/core/Seen.java": "class Seen {}\n"})
    all_py, _package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    assert all_py == frozenset({"pkg/__init__.py", "pkg/core/other.py"}) and entrypoints == ["pkg/core/other.py"]               # PARKED: the basename prune stands
    assert resolve_repository_locator_files("at x.Owner.run(Owner.java:1)", str(tmp_path), []) == []
    assert "Owner.java" not in _tree_index(str(tmp_path)) and "Seen.java" in _tree_index(str(tmp_path))


def test_the_scoped_snapshot_sandbox_uses_its_captured_projection_and_the_declared_split(tmp_path):
    """W3-3 C2 (was the PARKED legacy-walk pin): the sandbox's files are its projection; ``pkg/build/runner.py`` is a member, and only a DECLARED file (written by this run) may be
    the runtime target - the projection's untracked files stay visible but AMBIGUOUS, exactly as on a Git-backed root."""
    enclosing = _repo(tmp_path / "enclosing", {"README.md": "enclosing\n"})
    workspace = enclosing / "ws"
    _write(workspace, {"pkg/__init__.py": "", "pkg/build/runner.py": _MAIN, "pkg/core/other.py": _MAIN, "src/build/Owner.java": "class Owner {}\n"})
    sandbox = workspace / ".kriya" / "scoped-worktree"
    _create_scoped_snapshot_sandbox(str(workspace), str(sandbox))
    assert (sandbox / ".git").is_dir()
    all_py, _package_dirs, entrypoints = _build_python_runtime_grounding(str(sandbox), ["pkg/build/runner.py"])
    assert all_py == frozenset({"pkg/__init__.py", "pkg/build/runner.py", "pkg/core/other.py"}) and entrypoints == ["pkg/build/runner.py"]
    assert resolve_repository_locator_files("at x.Owner.run(Owner.java:1)", str(sandbox), []) == ["src/build/Owner.java"]


# =========================================================================== a Git-backed root Git cannot list is the typed stop ======================================================

READS = [
    ("_tree_index", lambda root: _tree_index(root)),
    ("attribute_compile_regression", lambda root: attribute_compile_regression(_delta(), _diagnostic("src/A.java"), root)),
    ("resolve_repository_locator_files", lambda root: resolve_repository_locator_files("at x.A.run(A.java:1)", root, [])),
    ("_collect_python_runtime_grounding_facts", lambda root: _collect_python_runtime_grounding_facts(root)),
    ("_build_python_runtime_grounding", lambda root: _build_python_runtime_grounding(root)),
]


@pytest.mark.parametrize("read", [r[1] for r in READS], ids=[r[0] for r in READS])
def test_an_unlistable_git_backed_root_is_the_typed_error_never_empty_evidence(tmp_path, monkeypatch, read):
    root = _repo(tmp_path / "repo", {"src/A.java": "class A {}\n", "app/main.py": _MAIN})
    read(str(root))                                                                       # matched control: listable, no error
    _unlistable(monkeypatch)
    with pytest.raises(BoundTreeError) as raised:
        read(str(root))
    assert raised.value.reason_code == BOUND_TREE_UNAVAILABLE


@pytest.mark.parametrize("read", [r[1] for r in READS], ids=[r[0] for r in READS])
def test_a_contradicting_git_authority_is_the_typed_error(tmp_path, read):
    root = _repo(tmp_path / "repo", {"src/A.java": "class A {}\n", "app/main.py": _MAIN})
    (root / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")
    with pytest.raises(BoundTreeError):
        read(str(root))


# =========================================================================== the typed error reaches the run's typed stop ============================================================

def _function(path, name):
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def _call_name(call):
    return getattr(call.func, "attr", getattr(call.func, "id", None))


def _enclosing_tries(function, call_name):
    """Every ``ast.Try`` of ``function`` whose BODY contains a call to ``call_name``."""
    return [node for node in ast.walk(function) if isinstance(node, ast.Try)
            and any(isinstance(sub, ast.Call) and _call_name(sub) == call_name for stmt in node.body for sub in ast.walk(stmt))]


SITES = [
    ("kriya/workflow/gate_orchestrator.py", "_run_executing_candidate_gates", "resolve_repository_locator_files"),
    ("kriya/workflow/gate_orchestrator.py", "_run_executing_candidate_gates", "_build_python_runtime_grounding"),
    ("kriya/workflow/retry_strategy.py", "_record_attempt_failure", "resolve_repository_locator_files"),
    ("kriya/workflow/development_loop.py", "run_terminal_regression", "attribute_compile_regression"),
    ("kriya/workflow/runtime_verification.py", "_execute_runtime_verification_directly", "_build_python_runtime_grounding"),
]


def _swallowing_handlers(function, call_name):
    """The handler lists of the tries around ``call_name`` that catch broadly WITHOUT an earlier ``except BoundTreeError``."""
    swallowing = []
    for try_node in _enclosing_tries(function, call_name):
        names = [ast.unparse(handler.type) if handler.type is not None else "" for handler in try_node.handlers]
        broad = [i for i, name in enumerate(names) if name in ("", "Exception", "BaseException")]
        if broad and not ("BoundTreeError" in names and names.index("BoundTreeError") < broad[0]):
            swallowing.append(names)
    return swallowing


@pytest.mark.parametrize("path, function, call", SITES)
def test_no_call_site_swallows_the_membership_error_in_its_own_function(path, function, call):
    node = _function(path, function)
    assert any(isinstance(sub, ast.Call) and _call_name(sub) == call for sub in ast.walk(node)), f"{function} no longer calls {call}"
    assert _swallowing_handlers(node, call) == []


def test_the_swallowing_check_itself_detects_a_broad_catch_and_accepts_a_re_raise():
    swallowing = ast.parse("def f():\n    try:\n        g()\n    except Exception:\n        return []\n").body[0]
    guarded = ast.parse("def f():\n    try:\n        g()\n    except BoundTreeError:\n        raise\n    except Exception:\n        return []\n").body[0]
    late = ast.parse("def f():\n    try:\n        g()\n    except Exception:\n        return []\n    except BoundTreeError:\n        raise\n").body[0]
    assert _swallowing_handlers(swallowing, "g") == [["Exception"]] and _swallowing_handlers(late, "g") == [["Exception", "BoundTreeError"]]
    assert _swallowing_handlers(guarded, "g") == []


@pytest.mark.parametrize("path, function", [("kriya/workflow/recovery_coordinator.py", "handle"), ("kriya/workflow/retry_strategy.py", "handle_attempt_failure")])
def test_the_failure_recording_path_has_no_handler_that_could_swallow_the_error(path, function):
    try:
        node = _function(path, function)
    except StopIteration:                                            # `handle` is a method: look it up on the class
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
        node = next(sub for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef) for sub in cls.body
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == function)
    assert not [sub for sub in ast.walk(node) if isinstance(sub, ast.Try)]


def _plan(state):
    return attempt_lifecycle.AttemptPlan(decision=decide_for_state(state, max_retries=4, targeted_max_retries=3, has_fallback_model=False),
                                         mode=AttemptMode.FULL_SET, runner=AttemptRunner.SINGLE, best_of_n=1)


def _iterate(*, attempt=None, handler=None):
    state = GenerationState()
    ctx = SimpleNamespace(deferred_executing_gates=None, post_source_review_guard=None)
    calls = []

    async def run_attempt(_state, _ctx):
        if attempt is not None:
            raise attempt

    async def accept():
        return None

    async def handle(_state, _ctx, error):
        calls.append(type(error).__name__)
        if handler is not None:
            raise handler
        return False

    class _Advanced(Exception):
        pass

    class _Recorded(Exception):
        stop_loop = False

    return calls, lambda: asyncio.run(attempt_lifecycle.run_iteration(
        _plan(state), state, ctx, run_attempt=run_attempt, load_best_of_n=lambda: None, accept=accept, handle_attempt_failure=handle,
        phase_advanced=_Advanced, best_of_n_recorded=_Recorded, logger=SimpleNamespace(info=lambda *a: None)))


def test_a_membership_error_raised_while_recording_a_failure_leaves_the_iteration_and_is_not_a_retry():
    """Row 6 in ``_record_attempt_failure`` runs INSIDE ``handle_attempt_failure``, which ``run_iteration`` calls from its ``except Exception`` handler: an exception raised
    there is not caught by the sibling clauses, so it leaves the iteration (and the loop) as itself instead of becoming IterationEnd.RETRY."""
    calls, run = _iterate(attempt=RuntimeError("a candidate failure"), handler=BoundTreeError(BOUND_TREE_UNAVAILABLE, "cannot list"))
    with pytest.raises(BoundTreeError):
        run()
    assert calls == ["RuntimeError"]                                                      # the ordinary failure reached the handler once; nothing was retried afterwards


def test_a_membership_error_from_the_attempt_is_not_routed_to_the_failure_handler():
    calls, run = _iterate(attempt=BoundTreeError(BOUND_TREE_UNAVAILABLE, "cannot list"))
    with pytest.raises(BoundTreeError):
        run()
    assert calls == []


def test_an_ordinary_failure_is_still_a_retry_control():
    calls, run = _iterate(attempt=RuntimeError("a candidate failure"))
    assert run().end is attempt_lifecycle.IterationEnd.RETRY and calls == ["RuntimeError"]


class _Judge:
    def __init__(self, answer):
        self.answer = answer

    async def judge(self, **_kwargs):
        return self.answer


def _capture_milestone(workspace, files):
    from _strict_doubles import strict_config
    from test_milestones import mkv2
    from test_s6_milestone_replay_truth import PLAN

    from kriya.workflow.milestones import MilestoneRunState, _MilestonePlanDriver
    driver = object.__new__(_MilestonePlanDriver)
    driver.engine_config = strict_config(autonomy={"run_verification_enabled": True})
    driver.workspace_path = str(workspace)
    driver.run_state = MilestoneRunState.from_dict(PLAN)
    driver.we = type("We", (), {"run_verifier": _Judge({"should_run": True, "run_commands": [["python", "pkg/build/runner.py"]], "command_source": "inferred"})})()
    driver._goal = {"M1": "goal"}
    asyncio.run(driver._capture_verification_commands(mkv2("M1"), {"files": files, "design": "d"}))
    return driver.run_state


def test_a_milestone_replay_capture_grounds_a_tracked_target_under_a_named_directory(tmp_path):
    root = _repo(tmp_path / "repo", {"pkg/__init__.py": "", "pkg/build/runner.py": _MAIN, "pkg/core/other.py": _MAIN})
    state = _capture_milestone(root, ["pkg/build/runner.py"])
    assert state.verification_commands == {"M1": [["python", "pkg/build/runner.py"]]} and state.replay_evidence_unavailable == {}


def test_a_milestone_replay_capture_that_cannot_list_the_tree_records_unavailable_evidence_never_an_empty_capture(tmp_path, monkeypatch):
    """The milestone's existing typed outcome for evidence it could not capture is ``replay_evidence_unavailable`` (replay then refuses it as REPLAY_EVIDENCE_UNAVAILABLE):
    a membership failure lands there, named, and no command is recorded as if the target were grounded."""
    root = _repo(tmp_path / "repo", {"pkg/__init__.py": "", "pkg/build/runner.py": _MAIN, "pkg/core/other.py": _MAIN})
    _unlistable(monkeypatch)
    state = _capture_milestone(root, ["pkg/build/runner.py"])
    assert "BoundTreeError" in state.replay_evidence_unavailable["M1"] and "M1" not in state.verification_commands


# =========================================================================== structural guards ====================================================================================

@pytest.mark.parametrize("path, function, call", [
    ("kriya/workflow/gate_orchestrator.py", "_run_executing_candidate_gates", "_build_python_runtime_grounding"),
    ("kriya/workflow/runtime_verification.py", "_execute_runtime_verification_directly", "_build_python_runtime_grounding"),
    ("kriya/workflow/milestones.py", "_capture_verification_commands", "_build_python_runtime_grounding"),
])
def test_every_runtime_grounding_call_site_passes_the_run_s_declared_paths(path, function, call):
    node = _function(path, function)
    calls = [sub for sub in ast.walk(node) if isinstance(sub, ast.Call) and _call_name(sub) == call]
    assert calls and all(len(site.args) == 2 and not site.keywords for site in calls), [ast.unparse(site) for site in calls]


def test_no_row_walks_the_candidate_by_directory_name_except_the_parked_branch():
    for path, function in [("kriya/workflow/failure_grounding.py", "resolve_repository_locator_files"), ("kriya/workflow/compile_regression.py", "_tree_index")]:
        assert not [sub for sub in ast.walk(_function(path, function)) if isinstance(sub, ast.Call) and _call_name(sub) == "walk"], (path, function)
    walks = [sub for sub in ast.walk(_function("kriya/workflow/runtime_verification.py", "_collect_python_runtime_grounding_facts"))
             if isinstance(sub, ast.Call) and _call_name(sub) == "walk"]
    assert len(walks) == 1                                                                 # the one parked-root branch

"""WORKTREE-CANONICAL-ROOT-001: every Kriya-managed worktree is rooted at the
canonical original workspace, never inside another Kriya-managed worktree.

The enforce controller's plan worktree is ``<ws>/.kriya/worktree``. The
subtask engine that runs inside it used to create its own reusable worktree
inside that one (``<ws>/.kriya/worktree/.kriya/worktree``). Now that worktree
is a sibling, ``<ws>/.kriya/worktrees/<name>``. The root comes from the
RunCoordinator (the run's canonical workspace and the candidate workspaces it
authorized), never from the working directory or from the candidate's own
path. Isolation, the verified-candidate binding, the transactional commit and
compile-cache reuse are unchanged.

Architecture assertion: no Kriya-managed worktree may have another
Kriya-managed worktree as an ancestor.
"""
import ast
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.control.persistence import list_run_records
from kriya.control.run_coordinator import (
    authorize_candidate_workspace,
    begin_mutating_run,
    candidate_workspace_root,
    coordinated_mutation,
)
from kriya.control.run_record import RunLifecycle
from kriya.workflow import worktree as wt
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow_controller import WorkflowController

KRIYA = Path(__file__).parent.parent / "kriya"


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "app.py").write_text("original\n")
    (repo / ".gitignore").write_text("target/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _registered(repo):
    return sorted(os.path.realpath(line.split(" ", 1)[1]) for line in _git(repo, "worktree", "list", "--porcelain")
                  .splitlines() if line.startswith("worktree "))


def _assert_no_managed_worktree_is_nested(repo):
    """The architecture assertion, over every worktree git has registered."""
    for path in _registered(repo):
        assert wt.managed_worktree_ancestor(path) is None, path


def _plan_and_subtask(repo):
    """What enforce does: the plan worktree for the workspace, authorized as
    this run's candidate, then the subtask engine's worktree for the plan."""
    plan = wt.create_git_worktree(str(repo))
    authorize_candidate_workspace(plan)
    return plan, wt.create_git_worktree(plan)


# --- topology --------------------------------------------------------------------------

def test_plan_and_subtask_worktrees_are_distinct_siblings_of_one_canonical_workspace(tmp_path):
    repo = _repo(tmp_path)
    with begin_mutating_run(str(repo)):
        plan, subtask = _plan_and_subtask(repo)
    root = os.path.realpath(repo)
    assert plan == os.path.join(root, ".kriya", "worktree")
    assert os.path.dirname(subtask) == os.path.join(root, ".kriya", "worktrees")
    assert plan != subtask and not subtask.startswith(plan + os.sep) and not plan.startswith(subtask + os.sep)
    assert wt.managed_worktree_ancestor(plan) is None and wt.managed_worktree_ancestor(subtask) is None
    # Both are worktrees of the one original repository.
    common = {os.path.realpath(os.path.join(path, _git(path, "rev-parse", "--git-common-dir").strip()))
              for path in (str(repo), plan, subtask)}
    assert len(common) == 1
    assert _registered(repo) == sorted([root, plan, subtask])
    _assert_no_managed_worktree_is_nested(repo)


def test_the_root_comes_from_the_run_not_the_working_directory(tmp_path, monkeypatch):
    repo, elsewhere = _repo(tmp_path), _repo(tmp_path, "elsewhere")
    monkeypatch.chdir(elsewhere)
    with begin_mutating_run(str(repo)):
        plan = wt.create_git_worktree(str(repo))
        authorize_candidate_workspace(plan)
        monkeypatch.chdir(plan)
        assert candidate_workspace_root(plan) == os.path.realpath(repo)
        subtask = wt.create_git_worktree(plan)
    assert subtask.startswith(os.path.join(os.path.realpath(repo), ".kriya", "worktrees") + os.sep)
    assert not (Path(elsewhere) / ".kriya").exists() and not (Path(plan) / ".kriya").exists()


def test_a_path_the_run_did_not_authorize_is_never_given_a_nested_worktree(tmp_path):
    repo = _repo(tmp_path)
    with begin_mutating_run(str(repo)):
        plan = wt.create_git_worktree(str(repo))
        # Not authorized: no canonical root is supplied, so it would nest - refused.
        with pytest.raises(wt.NestedWorktreeError):
            wt.create_git_worktree(plan)
    with pytest.raises(wt.NestedWorktreeError):  # no run at all
        wt.create_git_worktree(plan)
    assert not (Path(plan) / ".kriya").exists()
    _assert_no_managed_worktree_is_nested(repo)


@pytest.mark.parametrize("path,ancestor", [
    ("/w/.kriya/worktree", None),
    ("/w/.kriya/worktrees/candidate-x", None),
    ("/w/.kriya/scoped-worktree", None),
    ("/w/.kriya/worktrees", None),
    ("/w/src/.kriya/other", None),
    ("/w/.kriya/worktree/.kriya/worktree", "/w/.kriya/worktree"),
    ("/w/.kriya/worktree/src/a.py", "/w/.kriya/worktree"),
    ("/w/.kriya/worktrees/candidate-x/.kriya/worktree", "/w/.kriya/worktrees/candidate-x"),
    ("/w/.kriya/scoped-worktree/.kriya/worktrees/c", "/w/.kriya/scoped-worktree"),
])
def test_managed_worktree_ancestry(path, ancestor):
    assert wt.managed_worktree_ancestor(path) == ancestor


def test_a_scoped_snapshot_plan_gets_a_sibling_subtask_worktree(tmp_path):
    """A workspace nested in an enclosing repository gets a scoped snapshot
    plan. The snapshot is its own git boundary, so the subtask engine gets an
    ordinary worktree of it, and that worktree is a sibling too."""
    outer = _repo(tmp_path, "outer")
    workspace = outer / "project"
    workspace.mkdir()
    (workspace / "app.py").write_text("x\n")
    with begin_mutating_run(str(workspace)):
        plan = wt.create_git_worktree(str(workspace))
        assert plan == os.path.join(os.path.realpath(workspace), ".kriya", "scoped-worktree")
        authorize_candidate_workspace(plan)
        inner = wt.create_git_worktree(plan)
        assert (Path(inner) / "app.py").read_text() == "x\n"  # the plan's state, synced in
        wt.remove_git_worktree(plan, inner)
    assert os.path.dirname(inner) == os.path.join(os.path.realpath(workspace), ".kriya", "worktrees")
    assert wt.managed_worktree_ancestor(inner) is None and not (Path(plan) / ".kriya").exists()


def test_a_scoped_candidate_sandbox_is_a_sibling_and_is_removed(tmp_path, monkeypatch):
    """The scoped-snapshot location for a candidate, and its removal (only at a
    managed location carrying the sentinel)."""
    repo = _repo(tmp_path)
    with begin_mutating_run(str(repo)):
        plan = wt.create_git_worktree(str(repo))
        authorize_candidate_workspace(plan)
        monkeypatch.setattr(wt, "_resolved_git_toplevel", lambda _path: "/enclosing")
        sandbox = wt.create_git_worktree(plan)
        assert os.path.basename(sandbox).startswith("scoped-candidate-")
        assert os.path.dirname(sandbox) == os.path.join(os.path.realpath(repo), ".kriya", "worktrees")
        wt.remove_git_worktree(plan, sandbox)
    assert not os.path.exists(sandbox)
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / wt._SCOPED_SNAPSHOT_SENTINEL).write_text("x")
    wt.remove_git_worktree(str(repo), str(stray))  # carries the sentinel but is not a managed location
    assert stray.exists()


# --- isolation, reuse and cleanup -------------------------------------------------------

def test_subtask_changes_never_reach_the_plan_worktree_before_promotion(tmp_path):
    repo = _repo(tmp_path)
    with begin_mutating_run(str(repo)):
        plan, subtask = _plan_and_subtask(repo)
        (Path(subtask) / "app.py").write_text("subtask candidate\n")
        (Path(subtask) / "new.py").write_text("new\n")
    assert (Path(plan) / "app.py").read_text() == "original\n" and not (Path(plan) / "new.py").exists()
    assert _git(plan, "status", "--porcelain") == "" and (repo / "app.py").read_text() == "original\n"


def test_the_plans_uncommitted_state_is_what_the_subtask_starts_from(tmp_path):
    repo = _repo(tmp_path)
    with begin_mutating_run(str(repo)):
        plan = wt.create_git_worktree(str(repo))
        authorize_candidate_workspace(plan)
        (Path(plan) / "app.py").write_text("earlier subtask, promoted\n")
        subtask = wt.create_git_worktree(plan)
    assert (Path(subtask) / "app.py").read_text() == "earlier subtask, promoted\n"


def test_consecutive_runs_reuse_the_same_worktrees_clean_with_their_caches(tmp_path):
    repo = _repo(tmp_path)
    sets = []
    for run in range(2):
        with begin_mutating_run(str(repo)):
            plan, subtask = _plan_and_subtask(repo)
            (Path(subtask) / "app.py").write_text(f"run {run}\n")
            (Path(subtask) / "target").mkdir(exist_ok=True)
            (Path(subtask) / "target" / f"Run{run}.class").write_text("cache")
            wt.remove_git_worktree(plan, subtask)
            wt.remove_git_worktree(str(repo), plan)
        sets.append(_registered(repo))
        for path in (plan, subtask):
            assert _git(path, "status", "--porcelain") == "", path  # reset, nothing untracked
            assert _git(path, "rev-parse", "HEAD") == _git(repo, "rev-parse", "HEAD")
    assert sets[0] == sets[1] and len(sets[1]) == 3  # same worktrees, no growth, no new level
    # Compile caches survive the reset and the next run (reuse is preserved).
    assert sorted(p.name for p in (Path(subtask) / "target").iterdir()) == ["Run0.class", "Run1.class"]
    _assert_no_managed_worktree_is_nested(repo)


def test_a_resumed_run_resolves_the_same_worktrees(tmp_path):
    repo = _repo(tmp_path)
    with begin_mutating_run(str(repo)):
        first = _plan_and_subtask(repo)
    with begin_mutating_run(str(repo)):  # a later run of the same workspace (resume)
        plan = wt.managed_worktree_path(str(repo))
        authorize_candidate_workspace(plan)
        assert (plan, wt.managed_worktree_path(plan)) == first


def test_the_root_worktree_is_not_mistaken_for_registered_by_a_sibling_prefix(tmp_path):
    """<ws>/.kriya/worktree is a string prefix of <ws>/.kriya/worktrees/<name>:
    registration is an exact path match."""
    repo = _repo(tmp_path)
    with begin_mutating_run(str(repo)):
        _plan_and_subtask(repo)
    root_worktree = os.path.join(os.path.realpath(repo), ".kriya", "worktree")
    _git(repo, "worktree", "remove", "--force", root_worktree)
    os.makedirs(root_worktree)  # a leftover directory, no longer a registered worktree
    (Path(root_worktree) / "stale.txt").write_text("stale\n")
    (repo / "app.py").write_text("the user's uncommitted work\n")
    with begin_mutating_run(str(repo)):
        plan = wt.create_git_worktree(str(repo))
    # Treating the leftover as registered would reset from inside the real repository.
    assert (repo / "app.py").read_text() == "the user's uncommitted work\n"
    assert os.path.realpath(plan) in _registered(repo) and not (Path(plan) / "stale.txt").exists()
    assert (Path(plan) / "app.py").read_text() == "the user's uncommitted work\n"  # synced into the sandbox


# --- the layout an earlier version left behind (WORKTREE-LEGACY-NESTED-001) ---------------

def _seed_legacy_nested(repo):
    """What a pre-WORKTREE-CANONICAL-ROOT-001 enforce run left: the plan
    worktree, a subtask worktree inside it and, from a nested run, another
    inside that, each with leftover content."""
    root = os.path.realpath(repo)
    plan = os.path.join(root, ".kriya", "worktree")
    legacy = os.path.join(plan, ".kriya", "worktree")
    deeper = os.path.join(legacy, ".kriya", "worktree")
    for path in (plan, legacy, deeper):
        _git(repo, "worktree", "add", "-q", "--detach", path)
    for path in (legacy, deeper):
        (Path(path) / "app.py").write_text("old run's candidate\n")
        (Path(path) / "target").mkdir()
    _git(repo, "worktree", "lock", deeper)  # a locked one goes too
    return plan, legacy, deeper


def test_a_legacy_nested_worktree_is_removed_when_the_worktrees_are_next_used(tmp_path):
    repo = _repo(tmp_path)
    plan, legacy, deeper = _seed_legacy_nested(repo)
    # Resetting the plan worktree alone never removes them (git clean skips a .git file).
    _git(plan, "clean", "-fd")
    assert os.path.isdir(legacy)
    with begin_mutating_run(str(repo)):
        assert _plan_and_subtask(repo)[0] == plan
    assert not os.path.exists(legacy) and not os.path.exists(deeper)
    assert len(_registered(repo)) == 3
    _assert_no_managed_worktree_is_nested(repo)
    assert _git(plan, "status", "--porcelain") == ""  # the plan worktree carries no leftover state
    assert (repo / "app.py").read_text() == "original\n"


def test_legacy_cleanup_never_touches_a_worktree_it_does_not_own(tmp_path):
    """Only a worktree inside one of this repository's own registered managed
    worktrees is removed: not the main worktree, not the user's own worktrees,
    not a repository that merely lives under some other managed location."""
    host = tmp_path / "host" / ".kriya" / "worktree"
    host.mkdir(parents=True)
    repo = _repo(host)  # its main worktree lies inside a managed location it does not own
    users = tmp_path / "users-worktree"
    _git(repo, "worktree", "add", "-q", "--detach", str(users))
    own_managed = os.path.join(os.path.realpath(repo), ".kriya", "worktrees", "candidate-x")
    _git(repo, "worktree", "add", "-q", "--detach", own_managed)
    registered = wt._registered_worktrees(str(repo))
    wt._remove_legacy_nested_worktrees(str(repo), registered)
    assert _registered(repo) == sorted(registered) and users.is_dir() and os.path.isdir(own_managed)


def test_a_legacy_worktree_that_cannot_be_removed_fails_closed(tmp_path):
    repo = _repo(tmp_path)
    plan, _legacy, deeper = _seed_legacy_nested(repo)
    locked = Path(deeper).parent  # its parent directory is read-only, so removal fails
    locked.chmod(0o500)
    try:
        with begin_mutating_run(str(repo)), pytest.raises(subprocess.CalledProcessError) as failure:
            wt.create_git_worktree(str(repo))
        assert failure.value.cmd[1:3] == ["worktree", "remove"]  # the removal itself, not a later step
    finally:
        locked.chmod(0o700)
    assert (repo / "app.py").read_text() == "original\n"
    # Once the cause is gone, the next run removes what is left, registered or not.
    with begin_mutating_run(str(repo)):
        assert wt.create_git_worktree(str(repo)) == plan
    assert _registered(repo) == sorted([os.path.realpath(repo), plan])
    assert not (Path(plan) / ".kriya").exists() and _git(plan, "status", "--porcelain") == ""


# --- through the real enforce controller ---------------------------------------------------

def _engine():
    """A stand-in engine carrying the real coordinator wrapper that, like the
    real run_generation_workflow, creates its own worktree for the workspace
    it is given, writes the candidate there, promotes it and resets."""
    engine = MagicMock()
    engine.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT))
    engine.planner.run = AsyncMock(return_value="fake plan text")
    engine.kernel = None
    engine.worktrees = []

    @coordinated_mutation
    async def run_generation_workflow(goal=None, workspace_path=None, **kwargs):
        own = wt.create_git_worktree(workspace_path)
        engine.worktrees.append((workspace_path, own))
        (Path(own) / "app.py").write_text("verified candidate\n")
        (Path(own) / "target").mkdir(exist_ok=True)
        (Path(own) / "target" / "App.class").write_text("cache")
        assert (Path(workspace_path) / "app.py").read_text() == "original\n"  # not promoted yet
        (Path(workspace_path) / "app.py").write_text((Path(own) / "app.py").read_text())  # promotion
        wt.remove_git_worktree(workspace_path, own)
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}

    engine.run_generation_workflow = run_generation_workflow
    return engine


async def _enforce(repo, engine):
    plan = EngineeringPlan(plan_id="canonical-root", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="update app", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="app.py", action=FileAction.MODIFY)])])
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
            patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
            patch("kriya.workflow.workflow_controller.validate_plan",
                  new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        return await WorkflowController(engine).execute("goal", str(repo), migration_mode="enforce")


@pytest.mark.asyncio
async def test_two_enforce_runs_commit_verified_candidates_through_sibling_worktrees(tmp_path):
    repo = _repo(tmp_path)
    engine = _engine()
    sets = []
    for run in range(2):
        if run:
            _git(repo, "checkout", "--", "app.py")  # the same goal applies again
        result = await _enforce(repo, engine)
        assert result.legacy_result["status"] == "success", result.legacy_result
        assert (repo / "app.py").read_text() == "verified candidate\n"  # terminal commit, verified binding
        sets.append(_registered(repo))
    plan_path, subtask_path = engine.worktrees[0]
    assert engine.worktrees[0] == engine.worktrees[1]  # the same worktrees both runs
    assert plan_path == os.path.join(os.path.realpath(repo), ".kriya", "worktree")
    assert os.path.dirname(subtask_path) == os.path.join(os.path.realpath(repo), ".kriya", "worktrees")
    assert sets[0] == sets[1] and len(sets[0]) == 3
    _assert_no_managed_worktree_is_nested(repo)
    for path in (plan_path, subtask_path):
        assert _git(path, "status", "--porcelain") == "", path
    assert (Path(subtask_path) / "target" / "App.class").exists()  # cache kept
    records = list_run_records(str(repo))
    assert len(records) == 2 and all(r.lifecycle_state == RunLifecycle.SUCCESS and r.commit_result == "COMMITTED"
                                     for r in records)


@pytest.mark.asyncio
async def test_an_enforce_run_over_a_legacy_nested_layout_commits_and_leaves_no_nesting(tmp_path):
    """The PRD-036 rc4 canary: a workspace an earlier version ran in still had
    the nested subtask worktree registered, and every later run kept it."""
    repo = _repo(tmp_path)
    _plan, legacy, _deeper = _seed_legacy_nested(repo)
    result = await _enforce(repo, _engine())
    assert result.legacy_result["status"] == "success", result.legacy_result
    assert (repo / "app.py").read_text() == "verified candidate\n"
    assert not os.path.exists(legacy) and len(_registered(repo)) == 3
    _assert_no_managed_worktree_is_nested(repo)


# --- structural -------------------------------------------------------------------------------

def test_worktree_locations_are_decided_only_by_worktree_py():
    """Only kriya/workflow/worktree.py names a managed worktree location or runs
    `git worktree add` for one (the static-analysis operator scan adds a
    throwaway worktree in a temporary directory, never a managed one)."""
    offenders = []
    for path in KRIYA.rglob("*.py"):
        relative = path.relative_to(KRIYA.parent).as_posix()
        if relative == "kriya/workflow/worktree.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in ("worktree", "worktrees", "scoped-worktree"):
                offenders.append(f"{relative}:{node.lineno}:{node.value}")
    assert offenders == ["kriya/production_doctor.py:596:worktree", "kriya/static_analysis/operator_scan.py:70:worktree",
                         "kriya/static_analysis/operator_scan.py:77:worktree"], offenders

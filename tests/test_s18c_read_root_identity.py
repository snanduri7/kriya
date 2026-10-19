"""S18c (TYPE C; OI-148 group A, OI-147 group B): planned read-root identity consistency, owner decisions 2026-10-20.

ONE shared three-state fixture (``three_state``) drives every row: an OWNER workspace that is its own repository but sits
inside an ENCLOSING repository, the run's real CANDIDATE (kriya.workflow.worktree.create_git_worktree), and a PLAIN folder
nested in the enclosing repository with its real scoped-snapshot candidate - all deliberately different (content,
uncommitted edits, HEAD, branch names), with a guard that proves each answer differs between them, so a wrong root cannot
pass by coincidence.

  * Group A (OI-148): a planned git read describes the repository of the explicit Kriya root it runs in, or nothing:
    `git rev-parse --show-toplevel` must BE that root (realpaths); a folder nested inside another repository is not its
    own repository and is refused TOOL_CANDIDATE_VIEW_UNAVAILABLE before git runs (never an upward walk to the enclosing
    repository). Every planned git read: status/diff/log/branch/blame, two roots, one root, in place, no roots.
  * Group B (OI-147): planned filesystem/search/ast reads (see the group B section).
No live model, no Docker.
"""
import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _plugin_test_support import load_core_tools_module
from _strict_doubles import strict_config, strict_kernel

from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, Subtask
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus

GUARDRAILS = "guardrails"
CANDIDATE_VIEW_UNAVAILABLE = "TOOL_CANDIDATE_VIEW_UNAVAILABLE"  # a literal: the constant does not exist before S18c
# Characterization: the group A after-pins fail on the unfixed bytes (MEASURED on eb58aa6: 21 failed - COMPLETED with the
# enclosing repository's answer, FAILED untyped for blame, or the S18b no-root code); the correction removes this mark.
GROUP_A_AFTER = pytest.mark.xfail(strict=True, reason="S18c group A (OI-148): fixed by the git identity correction")


def _core():
    return load_core_tools_module()


# =====================================================================================================================
# The shared three-state fixture
# =====================================================================================================================

def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _init(path: Path, branch: str, who: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for args in (("init", "-q", "-b", branch), ("config", "user.email", f"{who}@example.invalid"),
                 ("config", "user.name", who)):
        git(path, *args)


def _module(origin: str, function: str) -> str:
    return f"ORIGIN = '{origin}'\n\n\ndef {function}():\n    return '{origin}'\n"


@dataclass(frozen=True)
class ThreeState:
    enclosing: str   # its own repository (branch enclosing-branch), with an uncommitted edit of outside.txt
    owner: str       # its own repository NESTED inside `enclosing` (branch owner-branch), HEAD moved after the candidate
    candidate: str   # the run's real candidate of `owner` (a detached worktree), changed like a MODEL step changes it
    plain: str       # a plain folder nested inside `enclosing` (NOT its own repository): the monorepo-subfolder case
    snapshot: str    # the run's real candidate of `plain` (the scoped snapshot: own .git, no commits), changed
    fresh: str       # its own repository nested inside `enclosing`, no commit yet
    alias: str       # a symlink to `owner`: the same root spelled through another path
    outside: str     # a file outside every root


def build_three_state(base) -> ThreeState:
    from kriya.workflow.worktree import create_git_worktree

    base = Path(os.path.realpath(base))
    outside = base / "outside_target.txt"
    outside.write_text("OUTSIDE_TARGET\n")

    enclosing = base / "enclosing"
    _init(enclosing, "enclosing-branch", "E")
    (enclosing / "e.py").write_text(_module("ORIGIN_ENCLOSING", "enclosing_fn"))
    (enclosing / "outside.txt").write_text("committed\n")
    (enclosing / ".gitignore").write_text("*.log\n")
    git(enclosing, "add", "-A")
    git(enclosing, "commit", "-qm", "ENCLOSING_HEAD")
    (enclosing / "outside.txt").write_text("ENCLOSING_UNCOMMITTED\n")

    owner = enclosing / "projects" / "owner"
    _init(owner, "owner-branch", "O")
    (owner / "app.py").write_text(_module("ORIGIN_OWNER", "owner_fn"))
    (owner / ".gitignore").write_text("ignored.env\n")
    (owner / "ignored.env").write_text("IGNORED_SECRET = 1\n")
    (owner / "doomed.py").write_text("DOOMED = 1\n")
    (owner / "deploy.pem").write_text("PEM_IN_THE_REPOSITORY\n")
    (owner / "pkg").mkdir()
    (owner / "pkg" / "mod.py").write_text("PKG = 1\n")
    os.symlink("app.py", owner / "rel_link.py")
    os.symlink(str(owner / "app.py"), owner / "abs_link.py")
    os.symlink(str(outside), owner / "out_link.txt")
    git(owner, "add", "-A")
    git(owner, "commit", "-qm", "OWNER_SEED")
    (owner / "untracked_before.py").write_text("UNTRACKED_BEFORE = 1\n")   # synced into the candidate
    candidate = Path(create_git_worktree(str(owner)))
    (candidate / "app.py").write_text(_module("ORIGIN_CANDIDATE", "candidate_fn"))
    (candidate / "candidate_only.py").write_text("CANDIDATE_ONLY = 1\n")
    (candidate / "doomed.py").unlink()
    (owner / "owner_only.py").write_text("OWNER_ONLY = 1\n")
    git(owner, "add", "owner_only.py")
    git(owner, "commit", "-qm", "OWNER_HEAD_AFTER_CANDIDATE")
    (owner / "owner_untracked_after.py").write_text("OWNER_UNTRACKED_AFTER = 1\n")   # never in the candidate

    plain = enclosing / "projects" / "plain"
    plain.mkdir(parents=True)
    (plain / "app.py").write_text(_module("ORIGIN_PLAIN", "plain_fn"))
    (plain / "x.log").write_text("ENCLOSING_IGNORED_LOG\n")   # ignored by the ENCLOSING repository
    snapshot = Path(create_git_worktree(str(plain)))
    (snapshot / "app.py").write_text(_module("ORIGIN_SNAPSHOT", "snapshot_fn"))

    fresh = enclosing / "projects" / "fresh"
    _init(fresh, "fresh-branch", "F")
    (fresh / "app.py").write_text(_module("ORIGIN_FRESH", "fresh_fn"))

    alias = base / "alias_of_owner"
    os.symlink(str(owner), alias)
    return ThreeState(enclosing=str(enclosing), owner=str(owner), candidate=str(candidate), plain=str(plain),
                      snapshot=str(snapshot), fresh=str(fresh), alias=str(alias), outside=str(outside))


@pytest.fixture
def three_state(tmp_path):
    return build_three_state(tmp_path)


def planned(tool_name, arguments, roots, *, depends_on=(), extra_subtasks=()):
    """One planned TOOL step through the production executor (subtask_executor.execute -> execute_model_authored), in
    a plan whose MODEL step m1 changes app.py; ``depends_on`` is the step's own declared dependencies."""
    core = _core()
    tool = {"git": core.GitTool, "filesystem": core.FilesystemTool, "search": core.SearchTool,
            "ast": core.ASTTool}[tool_name]()
    model = Subtask(id="m1", description="change app.py", execution_method=ExecutionMethod.MODEL,
                    planned_files=[{"path": "app.py", "action": "modify"}])
    step = Subtask(id="t9", description=f"planned {tool_name} read", execution_method=ExecutionMethod.TOOL,
                   tool_name=tool_name, tool_arguments=dict(arguments), depends_on=list(depends_on))
    kernel = strict_kernel(strict_config(autonomy={"mode": GUARDRAILS}))
    kernel.registry.get = MagicMock(return_value=tool)
    plan = EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[model, *extra_subtasks, step])
    return asyncio.run(subtask_executor.execute(subtask=step, plan=plan, context=build_context_package(),
                                                kernel=kernel, tool_roots=tuple(roots)))


# =====================================================================================================================
# Group A (OI-148): git repository identity
# =====================================================================================================================

GIT_COMMANDS = {"status": ["status"], "diff": ["diff"], "log": ["log", "-n", "5", "--oneline"], "branch": ["branch"],
                "blame": ["blame", "app.py"]}
GIT_ARGS = {"blame": {"file_path": "app.py"}}
CANDIDATE_TRUTH = ("status", "diff")
OWNER_TRUTH = ("log", "branch", "blame")


def planned_git(sub, roots):
    return planned("git", {"subcommand": sub, **GIT_ARGS.get(sub, {})}, roots)


def test_the_three_states_really_differ(three_state):
    """Guard: every git answer differs between owner, candidate and enclosing repository (blame: owner vs candidate),
    and the nested folders really are nested (git walks up from them to the enclosing repository)."""
    s = three_state
    for sub, command in GIT_COMMANDS.items():
        answers = [git(s.owner, *command), git(s.candidate, *command)]
        if sub != "blame":
            answers.append(git(s.enclosing, *command))
        assert len(set(answers)) == len(answers), sub
    assert git(s.plain, "rev-parse", "--show-toplevel").strip() == s.enclosing
    assert git(s.owner, "rev-parse", "--show-toplevel").strip() == s.owner
    assert git(s.snapshot, "rev-parse", "--show-toplevel").strip() == s.snapshot
    assert "outside.txt" in git(s.plain, "status") and "ENCLOSING_UNCOMMITTED" in git(s.plain, "diff")
    assert "ENCLOSING_HEAD" in git(s.plain, "log", "--oneline") and "enclosing-branch" in git(s.plain, "branch")


# -- behaviour that must not change (pass before and after) -----------------------------------------------------------

@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_an_own_repository_nested_in_another_reads_itself(three_state, sub):
    """The owner IS its own repository top level although it sits inside the enclosing one: every planned git read is
    answered by it (status/diff: its candidate; log/branch/blame: the owner, S18b), never by the enclosing repository."""
    s = three_state
    result = planned_git(sub, (s.owner, s.candidate))
    assert result.status == SubtaskStatus.COMPLETED, result.error
    expected_root = s.candidate if sub in CANDIDATE_TRUTH else s.owner
    assert result.tool_output == git(expected_root, *GIT_COMMANDS[sub])
    if sub != "blame":
        assert result.tool_output != git(s.enclosing, *GIT_COMMANDS[sub])


@pytest.mark.parametrize("in_place", [False, True], ids=["one-root", "in-place"])
@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_one_root_and_in_place_read_the_owner_repository(three_state, sub, in_place):
    s = three_state
    result = planned_git(sub, (s.owner, s.owner) if in_place else (s.owner,))
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert result.tool_output == git(s.owner, *GIT_COMMANDS[sub])


@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_a_root_spelled_through_a_symlink_is_the_same_repository(three_state, sub):
    """Identity is compared on realpaths: the owner reached through a symlinked path is still the owner (on macOS
    every temporary folder is such an alias: /var -> /private/var)."""
    s = three_state
    assert os.path.islink(s.alias)
    result = planned_git(sub, (s.alias,))
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert result.tool_output == git(s.owner, *GIT_COMMANDS[sub])


def test_a_repository_without_a_commit_still_answers_status_with_one_root(three_state):
    """Only a SEPARATE candidate needs a base revision (S18b); one root that is its own repository with no commit yet
    gives its own truthful status."""
    s = three_state
    result = planned_git("status", (s.fresh,))
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert result.tool_output == git(s.fresh, "status") and "app.py" in result.tool_output


def test_a_planned_git_read_without_roots_runs_in_the_process_folder(three_state, monkeypatch):
    """S18b mutant o12 (survived): with no roots a planned git read runs in the process folder, as before - pinned on
    a process folder that is its own repository top level."""
    s = three_state
    monkeypatch.chdir(s.enclosing)
    result = planned_git("status", ())
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert result.tool_output == git(s.enclosing, "status") != git(s.owner, "status")


def test_without_roots_a_path_bearing_git_read_keeps_the_no_root_refusal(three_state, monkeypatch):
    """TOOL_WORKSPACE_ROOT_UNAVAILABLE keeps meaning "no workspace root was supplied" (blame names a path)."""
    monkeypatch.chdir(three_state.enclosing)
    result = planned_git("blame", ())
    assert result.status == SubtaskStatus.NEEDS_REVIEW
    assert result.reason_codes == ("TOOL_WORKSPACE_ROOT_UNAVAILABLE",)


# -- the defect: a folder that is not its own repository top level (strict-xfail on the unfixed bytes) -----------------

def _refused_as_not_its_own_repository(result):
    assert result.status == SubtaskStatus.NEEDS_REVIEW, (result.status, result.tool_output, result.error)
    assert result.reason_codes == (CANDIDATE_VIEW_UNAVAILABLE,), result.reason_codes
    assert result.tool_output is None
    assert "TOOL_WORKSPACE_ROOT_UNAVAILABLE" not in (result.error or "")


@GROUP_A_AFTER
@pytest.mark.parametrize("in_place", [False, True], ids=["one-root", "in-place"])
@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_a_nested_plain_folder_never_answers_for_the_enclosing_repository(three_state, sub, in_place):
    """OI-148 reproducer (MEASURED on eb58aa6): a single-root / in-place planned `git status` in the plain folder listed
    the enclosing repository's ../../outside.txt, diff showed its uncommitted edit, log/branch its history; blame
    failed untyped. Now refused, typed, before git runs."""
    s = three_state
    _refused_as_not_its_own_repository(planned_git(sub, (s.plain, s.plain) if in_place else (s.plain,)))


@GROUP_A_AFTER
@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_a_nested_plain_folder_with_its_snapshot_candidate_is_refused_for_every_read(three_state, sub):
    """Two roots (the nested plain folder + its scoped snapshot): the owner-side log/branch/blame answered for the
    enclosing repository (MEASURED); status/diff were refused with the no-root code (S18b) - one cause, one code now."""
    s = three_state
    _refused_as_not_its_own_repository(planned_git(sub, (s.plain, s.snapshot)))


@GROUP_A_AFTER
@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_a_subfolder_of_a_repository_is_not_that_repository(three_state, sub):
    """The monorepo-subdirectory case on the owner side: a root that is a folder INSIDE a repository is refused (no
    enclosing-repository + pathspec confinement; documented limitation)."""
    s = three_state
    _refused_as_not_its_own_repository(planned_git(sub, (os.path.join(s.owner, "pkg"),)))


@GROUP_A_AFTER
def test_without_roots_a_nested_process_folder_is_refused(three_state, monkeypatch):
    """Fail-closed choice for the no-roots case (unreachable from enforce, which always supplies roots): the process
    folder must itself be a repository top level, exactly like a supplied root."""
    monkeypatch.chdir(three_state.plain)
    _refused_as_not_its_own_repository(planned_git("status", ()))

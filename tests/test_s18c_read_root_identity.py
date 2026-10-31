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
# The group A after-pins below (refused as not its own repository) failed on the unfixed bytes (MEASURED on eb58aa6:
# 21 failed - COMPLETED with the enclosing repository's answer, FAILED untyped for blame, or the S18b no-root code).


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


@pytest.mark.parametrize("in_place", [False, True], ids=["one-root", "in-place"])
@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_a_nested_plain_folder_never_answers_for_the_enclosing_repository(three_state, sub, in_place):
    """OI-148 reproducer (MEASURED on eb58aa6): a single-root / in-place planned `git status` in the plain folder listed
    the enclosing repository's ../../outside.txt, diff showed its uncommitted edit, log/branch its history; blame
    failed untyped. Now refused, typed, before git runs."""
    s = three_state
    _refused_as_not_its_own_repository(planned_git(sub, (s.plain, s.plain) if in_place else (s.plain,)))


@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_a_nested_plain_folder_with_its_snapshot_candidate_is_refused_for_every_read(three_state, sub):
    """Two roots (the nested plain folder + its scoped snapshot): the owner-side log/branch/blame answered for the
    enclosing repository (MEASURED); status/diff were refused with the no-root code (S18b) - one cause, one code now."""
    s = three_state
    _refused_as_not_its_own_repository(planned_git(sub, (s.plain, s.snapshot)))


@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_a_subfolder_of_a_repository_is_not_that_repository(three_state, sub):
    """The monorepo-subdirectory case on the owner side: a root that is a folder INSIDE a repository is refused (no
    enclosing-repository + pathspec confinement; documented limitation)."""
    s = three_state
    _refused_as_not_its_own_repository(planned_git(sub, (os.path.join(s.owner, "pkg"),)))


def test_without_roots_a_nested_process_folder_is_refused(three_state, monkeypatch):
    """Fail-closed choice for the no-roots case (unreachable from enforce, which always supplies roots): the process
    folder must itself be a repository top level, exactly like a supplied root."""
    monkeypatch.chdir(three_state.plain)
    _refused_as_not_its_own_repository(planned_git("status", ()))


# =====================================================================================================================
# Group B (OI-147): planned filesystem / search / ast read roots
# =====================================================================================================================
#
# The intended repository identity of a planned read comes from the plan's own declared data flow, never from a new
# per-step flag: a step that declares (transitively, through depends_on) that it runs after a MODEL step reasons about
# the run's CANDIDATE as that step left it; a step with no MODEL step among its dependencies is a BASELINE read and keeps
# the owner workspace (the immutable pre-run state). Execution position cannot be the signal: the executor's topological
# order breaks ties by id, so the Planner's worked example B ("Find the existing callers of load_settings BEFORE it
# changes", depends_on []) runs after the MODEL steps (MEASURED below) and must still describe the baseline.


READS = {
    "read": ("filesystem", {"operation": "read", "path": "app.py"}),
    "list": ("filesystem", {"operation": "list", "path": "."}),
    "search": ("search", {"pattern": "ORIGIN", "path": "."}),
    "ast": ("ast", {"file_path": "app.py"}),
}


def _identity(read, output, origin):
    """The answer names ``origin`` ('OWNER', 'CANDIDATE', 'SNAPSHOT', ...) for this read."""
    if read == "list":
        names = set(output)
        marker = {"OWNER": "owner_untracked_after.py", "CANDIDATE": "candidate_only.py"}.get(origin)
        return marker in names if marker else None
    if read == "ast":
        return f"def {origin.lower()}_fn" in output
    if read == "search":
        return f"app.py:1: ORIGIN = 'ORIGIN_{origin}'" in output.splitlines()
    return f"ORIGIN = 'ORIGIN_{origin}'" in output


def test_the_read_states_really_differ(three_state):
    s = three_state
    texts = [Path(root, "app.py").read_text() for root in (s.owner, s.candidate, s.plain, s.snapshot)]
    assert len(set(texts)) == 4
    owner_names, candidate_names = set(os.listdir(s.owner)), set(os.listdir(s.candidate))
    assert "owner_untracked_after.py" in owner_names - candidate_names
    assert "candidate_only.py" in candidate_names - owner_names


# -- behaviour that must not change (pass before and after) -----------------------------------------------------------

@pytest.mark.parametrize("read", list(READS))
def test_a_read_with_no_model_step_among_its_dependencies_reads_the_owner_baseline(three_state, read):
    s = three_state
    tool, arguments = READS[read]
    result = planned(tool, arguments, (s.owner, s.candidate))
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert _identity(read, result.tool_output, "OWNER") and not _identity(read, result.tool_output, "CANDIDATE")


def test_the_example_b_shape_runs_after_the_change_and_still_reads_the_baseline(three_state):
    """MEASURED order: a depends_on [] search runs AFTER both MODEL steps (sorted-id tie-break); it is the Planner's
    baseline example ("before it changes") and keeps describing the owner baseline."""
    from kriya.workflow.subtask_checkpoint import topological_subtask_order

    s = three_state
    m2 = Subtask(id="m2", description="change more", execution_method=ExecutionMethod.MODEL, depends_on=["m1"],
                 planned_files=[{"path": "app.py", "action": "modify"}])
    step = Subtask(id="t9", description="callers before the change", execution_method=ExecutionMethod.TOOL,
                   tool_name="search", tool_arguments={"pattern": "ORIGIN"})
    model = Subtask(id="m1", description="change app.py", execution_method=ExecutionMethod.MODEL,
                    planned_files=[{"path": "app.py", "action": "modify"}])
    assert topological_subtask_order(EngineeringPlan(plan_id="p", kind=ChangeKind.TASK,
                                                     subtasks=[model, m2, step])) == ["m1", "m2", "t9"]
    result = planned("search", {"pattern": "ORIGIN"}, (s.owner, s.candidate), extra_subtasks=[m2])
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert _identity("search", result.tool_output, "OWNER") and not _identity("search", result.tool_output, "CANDIDATE")


@pytest.mark.parametrize("read", list(READS))
def test_a_read_after_only_tool_steps_reads_the_owner_baseline(three_state, read):
    """Reads change nothing: depending only on another TOOL step is still a baseline read."""
    s = three_state
    tool, arguments = READS[read]
    first = Subtask(id="t0", description="an earlier read", execution_method=ExecutionMethod.TOOL,
                    tool_name="search", tool_arguments={"pattern": "x"})
    result = planned(tool, arguments, (s.owner, s.candidate), depends_on=["t0"], extra_subtasks=[first])
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert _identity(read, result.tool_output, "OWNER")


@pytest.mark.parametrize("in_place", [False, True], ids=["one-root", "in-place"])
@pytest.mark.parametrize("read", list(READS))
def test_with_one_root_a_read_after_a_model_step_reads_that_root(three_state, read, in_place):
    """With one root (or the candidate IS the owner folder) that folder is what the run changes."""
    s = three_state
    tool, arguments = READS[read]
    result = planned(tool, arguments, (s.owner, s.owner) if in_place else (s.owner,), depends_on=["m1"])
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert _identity(read, result.tool_output, "OWNER")


@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_git_keeps_its_per_subject_roots_whatever_the_dependencies(three_state, sub):
    """S18b's accepted per-subject classification is not reopened: status/diff = candidate, log/branch/blame = owner
    (blame's file is resolved in the owner repository it runs in), with or without a MODEL dependency."""
    s = three_state
    expected = git(s.candidate if sub in CANDIDATE_TRUTH else s.owner, *GIT_COMMANDS[sub])
    for depends_on in ((), ("m1",)):
        result = planned("git", {"subcommand": sub, **GIT_ARGS.get(sub, {})}, (s.owner, s.candidate),
                         depends_on=depends_on)
        assert result.status == SubtaskStatus.COMPLETED, (depends_on, result.error)
        assert result.tool_output == expected, depends_on


@pytest.mark.parametrize("depends_on", [(), ("m1",)], ids=["baseline", "after-model"])
@pytest.mark.parametrize("path, code", [("out_link.txt", "PATH_OUTSIDE_AUTHORIZED_READABLE_ROOTS"),
                                        ("deploy.pem", "SENSITIVE_PATH_DENIED")])
def test_authorization_holds_on_whichever_root_is_read(three_state, depends_on, path, code):
    """A symlink leading out of the root and a credential-shaped file are refused in both views (the link and the
    file exist in owner AND candidate)."""
    s = three_state
    result = planned("filesystem", {"operation": "read", "path": path}, (s.owner, s.candidate), depends_on=depends_on)
    assert result.status == SubtaskStatus.FAILED and result.reason_codes == (code,), result
    assert result.tool_output is None


@pytest.mark.parametrize("depends_on", [(), ("m1",)], ids=["baseline", "after-model"])
def test_an_absolute_in_root_symlink_resolves_to_the_owner_bytes_in_both_views(three_state, depends_on):
    """Characterized snapshot difference: git stores an absolute symlink's target text, so the candidate's abs_link.py
    points at the OWNER's app.py - what every process in the candidate (the gates included) reads too."""
    s = three_state
    assert os.path.realpath(os.path.join(s.candidate, "abs_link.py")) == os.path.join(s.owner, "app.py")
    result = planned("filesystem", {"operation": "read", "path": "abs_link.py"}, (s.owner, s.candidate),
                     depends_on=depends_on)
    assert result.status == SubtaskStatus.COMPLETED and "ORIGIN_OWNER" in result.tool_output


@pytest.mark.parametrize("depends_on", [(), ("m1",)], ids=["baseline", "after-model"])
def test_files_present_in_both_views_read_the_same(three_state, depends_on):
    """Untracked files that existed when the candidate was created are synced into it; a file the enclosing
    repository ignores is NOT copied into the scoped snapshot (W3-3 U3: the snapshot is the bound-tree projection of the
    operating subtree, what the exact-root sandbox holds - ignored bytes are not repository source). The ignored file is
    still on the owner side, so the two views deliberately differ on it."""
    s = three_state
    for roots, path, marker in (((s.owner, s.candidate), "untracked_before.py", "UNTRACKED_BEFORE"),):
        result = planned("filesystem", {"operation": "read", "path": path}, roots, depends_on=depends_on)
        assert result.status == SubtaskStatus.COMPLETED and marker in result.tool_output, (path, result.error)
    assert os.path.isfile(os.path.join(s.plain, "x.log")) and not os.path.lexists(os.path.join(s.snapshot, "x.log"))


# -- the defect: a read after a MODEL step reported the owner (strict-xfail on the unfixed bytes) ---------------------

@pytest.mark.parametrize("read", list(READS))
def test_a_read_after_a_model_step_reads_the_candidate(three_state, read):
    """OI-147 (MEASURED on eb58aa6): a planned read declared after the MODEL step reported the OWNER's app.py."""
    s = three_state
    tool, arguments = READS[read]
    result = planned(tool, arguments, (s.owner, s.candidate), depends_on=["m1"])
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert _identity(read, result.tool_output, "CANDIDATE") and not _identity(read, result.tool_output, "OWNER")


@pytest.mark.parametrize("read", list(READS))
def test_a_read_after_a_model_step_through_another_step_reads_the_candidate(three_state, read):
    """Transitive: t9 depends on a read t0 that depends on the MODEL step m1."""
    s = three_state
    tool, arguments = READS[read]
    between = Subtask(id="t0", description="an intermediate read", execution_method=ExecutionMethod.TOOL,
                      tool_name="search", tool_arguments={"pattern": "x"}, depends_on=["m1"])
    result = planned(tool, arguments, (s.owner, s.candidate), depends_on=["t0"], extra_subtasks=[between])
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert _identity(read, result.tool_output, "CANDIDATE")


def test_a_file_the_model_step_created_is_read_from_the_candidate(three_state):
    """MEASURED on eb58aa6: FAILED ("does not exist") - the read looked in the owner folder."""
    s = three_state
    result = planned("filesystem", {"operation": "read", "path": "candidate_only.py"}, (s.owner, s.candidate),
                     depends_on=["m1"])
    assert result.status == SubtaskStatus.COMPLETED and result.tool_output == "CANDIDATE_ONLY = 1\n", result.error


@pytest.mark.parametrize("path", ["ignored.env", "doomed.py", "owner_untracked_after.py"])
def test_files_absent_from_the_candidate_are_absent_for_a_read_after_a_model_step(three_state, path):
    """Characterized snapshot differences: the candidate holds no git-ignored file (ignored.env), not a file the run
    deleted (doomed.py) and not an untracked file created after the candidate (owner_untracked_after.py). A read
    reasoning about the candidate reports it absent - what the run's gates see - instead of the owner's copy."""
    s = three_state
    assert os.path.exists(os.path.join(s.owner, path)) and not os.path.exists(os.path.join(s.candidate, path))
    result = planned("filesystem", {"operation": "read", "path": path}, (s.owner, s.candidate), depends_on=["m1"])
    assert result.status == SubtaskStatus.FAILED and "does not exist" in result.error, result
    assert result.tool_output is None


def test_a_relative_symlink_in_the_candidate_resolves_inside_the_candidate(three_state):
    s = three_state
    result = planned("filesystem", {"operation": "read", "path": "rel_link.py"}, (s.owner, s.candidate),
                     depends_on=["m1"])
    assert result.status == SubtaskStatus.COMPLETED and "ORIGIN_CANDIDATE" in result.tool_output


def test_the_scoped_snapshot_is_read_after_a_model_step_with_its_kriya_markers(three_state):
    """A nested plain workspace's candidate is the scoped snapshot: a read after the MODEL step describes it. Its two
    Kriya-made entries the owner folder lacks (.git, the sentinel) are on disk but, since S18d (OI-150 c, owner decision
    2026-10-20), a planned listing leaves them out (tests/test_s18d_read_view_and_enforce_prompt.py) - so the listing
    shows what the owner folder shows (less the owner's own .kriya)."""
    s = three_state
    read = planned("filesystem", {"operation": "read", "path": "app.py"}, (s.plain, s.snapshot), depends_on=["m1"])
    assert read.status == SubtaskStatus.COMPLETED and "ORIGIN_SNAPSHOT" in read.tool_output
    listing = planned("filesystem", {"operation": "list", "path": "."}, (s.plain, s.snapshot), depends_on=["m1"])
    assert {".git", ".kriya-scoped-snapshot"} <= set(os.listdir(s.snapshot))
    assert not {".git", ".kriya-scoped-snapshot"} & set(listing.tool_output)
    assert not {".git", ".kriya-scoped-snapshot"} & set(os.listdir(s.plain))
    # the owner's own Kriya state is left out too, and so is the file the ENCLOSING repository ignores (W3-3 U3: the snapshot holds the bound-tree projection)
    assert set(listing.tool_output) == set(os.listdir(s.plain)) - {".kriya", "x.log"}


def test_an_enforce_run_reads_the_candidate_after_its_model_step_and_the_baseline_for_example_b(tmp_path, monkeypatch):
    """The real scenario, end to end (production enforce loop): s1 caches the service in the candidate; s3 reads the
    service file (depends_on s1) and must show the cached version the run commits; s4 is the example-B shape (search
    for lru_cache, depends_on []), which runs after s1-s3 and must still describe the baseline (no match)."""
    import _t6_harness
    import test_enforce_verified_no_change as shape
    from _chaos_harness import chaos_config

    def plan():
        document = shape._plan(shape.TOOL_CRITERION).model_dump()
        common = {"relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": [], "verification": []}
        document["subtasks"].append({"id": "s3", "description": "show the cached service", "execution_method": "tool",
                                     "tool_name": "filesystem",
                                     "tool_arguments": {"operation": "read", "path": shape.SERVICE},
                                     "depends_on": ["s1"], "provides": ["service_shown"], **common})
        document["subtasks"].append({"id": "s4", "description": "find lru_cache uses before the change",
                                     "execution_method": "tool", "tool_name": "search",
                                     "tool_arguments": {"pattern": "lru_cache"}, "depends_on": [],
                                     "provides": ["baseline_uses"], **common})
        return EngineeringPlan.model_validate(document)

    core = _core()
    cfg = chaos_config()
    cfg.autonomy.mode = GUARDRAILS
    observed = _t6_harness.enforce_run(tmp_path, monkeypatch, _t6_harness.shop_responder, _t6_harness.shop_files(),
                                       shape.GOAL, [plan], cfg=cfg,
                                       tools={"filesystem": core.FilesystemTool(), "search": core.SearchTool()})
    assert observed.result.legacy_result["status"] == "success", observed.result.legacy_result
    steps = {r.subtask_id: r for r in observed.result.subtask_results}
    assert [r.subtask_id for r in observed.result.subtask_results][-1] == "s4"
    assert steps["s3"].status == SubtaskStatus.COMPLETED and steps["s3"].tool_output == shape.SERVICE_SRC
    assert steps["s4"].status == SubtaskStatus.COMPLETED and steps["s4"].tool_output == "No matches found."
    assert (observed.workspace / shape.SERVICE).read_text() == shape.SERVICE_SRC  # the change s3 described


# Orchestrator verification (S18c): the walk over depends_on must terminate on a repeated id. Plan validation refuses a
# cycle upstream, so this is defence in depth - but without the `seen` guard a cycle (or an exponential diamond) never
# ends; mutant p15 (the guard dropped) survived the worker's pins.
def _walk_in_a_thread(subtask, plan):
    import threading
    outcome = {}
    worker = threading.Thread(target=lambda: outcome.setdefault("value", subtask_executor.reads_candidate_view(subtask, plan)),
                              daemon=True)
    worker.start()
    worker.join(timeout=10)
    return outcome


def _tool_step(step_id, depends_on):
    return Subtask(id=step_id, description="d", execution_method=ExecutionMethod.TOOL, tool_name="search",
                   tool_arguments={"pattern": "x"}, depends_on=list(depends_on))


def test_the_dependency_walk_terminates_on_a_cycle_and_a_diamond():
    cycle = [_tool_step("a", ["b"]), _tool_step("b", ["a"])]
    assert _walk_in_a_thread(cycle[0], EngineeringPlan.model_construct(subtasks=cycle)) == {"value": False}
    diamond = [_tool_step("root", []), _tool_step("l", ["root"]), _tool_step("r", ["root"]), _tool_step("top", ["l", "r"])]
    assert _walk_in_a_thread(diamond[3], EngineeringPlan.model_construct(subtasks=diamond)) == {"value": False}

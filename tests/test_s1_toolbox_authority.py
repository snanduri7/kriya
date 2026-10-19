"""S1 (TOOLBOX authority for model-authored TOOL actions; OI-88, registry TOOL-SUBTASK-DESKTOP-REVIEW-001).

A TOOL subtask is planner output: its tool selection and its arguments are model text. Owner contract (2026-10-19,
items 6 and 7): a model-authored TOOL action passes the same policy/approval authority an equivalent agent-generated
side effect needs; no authority is inferred from a missing confirmation flag; absolute / out-of-workspace writes need
explicit allowed policy; an operator invocation (`kriya tools execute`) and a model-authored one are never equivalent.

Characterization FIRST (S1 method). Three kinds of test live here:
  * pins of today's governed paths, green before and after the correction (the operator path, the B3 shell stop, the
    shell hard invariants, the undecided guardrails plugin case);
  * EXPECTED-TO-FLIP pins: the measured defect, written as the corrected behaviour and marked strict-xfail at the
    characterization commit, so they are shown to FAIL on the unfixed bytes; the correction commit removes the marks;
  * (added with the correction) the precise unit-level contracts the mutants are run against.

The end-to-end pins go through the real enforce controller (tests/_t6_harness.enforce_run): the TOOL dispatch, the roots
it is given and the stop after a refused TOOL step are the production path, not a stub of it.
"""
import asyncio
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from _plugin_test_support import load_core_tools_module
from _strict_doubles import strict_config, strict_kernel
from pydantic import BaseModel

from kriya.tools.tool import BaseTool
from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, Subtask
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus

HITL = "human-in-the-loop"
# Every value other than "human-in-the-loop" behaves as "guardrails" in the product (docs/user_guide.md); an arbitrary
# value is exercised too, because the mode is an open string (S16 owns its vocabulary).
MODES = (HITL, "guardrails", "full-auto")

_EXPECTED_TO_FLIP = pytest.mark.xfail(
    strict=True, reason="S1 expected-to-flip pin: the measured defect (an unauthorized model-authored TOOL action "
                        "succeeds); the correction commit removes this mark")


def _core():
    return load_core_tools_module()


def _validation_tool():
    import importlib

    return importlib.import_module(_core().__name__ + ".validation_tool").ValidationTool()


# -- the real enforce path ---------------------------------------------------------------------------------------------

def _fs_plan(tool_name, tool_arguments):
    import test_enforce_verified_no_change as shape

    from kriya.workflow.plan_schema import EngineeringPlan as Plan

    plan = shape._plan(shape.TOOL_CRITERION).model_dump()
    plan["subtasks"].insert(0, {"id": "s0", "description": "a planned tool step", "execution_method": "tool",
                                "tool_name": tool_name, "tool_arguments": tool_arguments, "provides": ["done"],
                                "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": [],
                                "verification": []})
    return Plan.model_validate(plan)


def _link_outside(workspace, outside):
    os.symlink(outside, workspace / "link")


# The measured reproducer and its alternate path forms (negative controls a-e). Each entry: (label, path, prepare,
# expected reason code, the files that must NOT exist afterwards relative to (workspace, outside, home)).
WRITE_FORMS = [
    ("absolute-outside", lambda ws, out: str(out / "W.txt"), None, "PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS"),
    ("dotdot-escape", lambda ws, out: "../outside/W.txt", None, "PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS"),
    ("tilde", lambda ws, out: "~/W.txt", None, "WRITE_SCOPE_DENY_ALL"),
    ("symlink-inside-to-outside", lambda ws, out: "link/W.txt", _link_outside, "PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS"),
    ("normalization-a-dotdot-dotdot", lambda ws, out: "a/../../outside/W.txt", None,
     "PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS"),
    ("repeated-separators-and-dots", lambda ws, out: "..//outside/./sub/..//W.txt", None,
     "PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS"),
    ("control-dir-case-variant", lambda ws, out: ".GIT/S1_WRITTEN", None, "TRUSTED_CONTROL_PATH_DENIED"),
    ("in-workspace", lambda ws, out: "W.txt", None, "WRITE_SCOPE_DENY_ALL"),
]


def _nothing_written(workspace, outside):
    home = Path(os.environ.get("HOME", "/nonexistent"))
    candidates = [outside / "W.txt", workspace / "W.txt", workspace / "~" / "W.txt", home / "W.txt",
                  workspace / ".git" / "S1_WRITTEN", workspace / ".GIT" / "S1_WRITTEN"]
    return [str(p) for p in candidates if p.exists()]


@_EXPECTED_TO_FLIP
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("label, path, prepare, code", WRITE_FORMS, ids=[f[0] for f in WRITE_FORMS])
def test_a_planned_filesystem_write_is_refused_and_writes_nothing(tmp_path, monkeypatch, mode, label, path, prepare,
                                                                 code):
    """Negative controls a-e (and the reproducer, absolute-outside in human-in-the-loop): the planned write is a typed
    refusal recorded in SubtaskResult.reason_codes, nothing is written anywhere, the run stops at it (no retry)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    os.makedirs(tmp_path / "home", exist_ok=True)
    holder = {}

    def prep(ws, out):
        holder["path"] = path(ws, out)
        if prepare is not None:
            prepare(ws, out)

    s0, workspace, outside = _enforce_lazy(tmp_path, monkeypatch, mode, prep, holder)
    assert s0.status == SubtaskStatus.FAILED, (label, s0)
    assert s0.reason_codes == (code,), (label, s0.reason_codes, s0.error)
    assert _nothing_written(workspace, outside) == [], label


def _enforce_lazy(tmp_path, monkeypatch, mode, prep, holder, operation="write", content="x"):
    """One real enforce run whose first subtask is a planned filesystem TOOL step; the process works in the owner
    folder, as the CLI and the desktop launch do. ``prep`` prepares the workspace (committed) and sets
    ``holder["path"]``, the planned path argument. Returns (the s0 result, owner workspace, outside directory)."""
    import _t6_harness
    import test_enforce_verified_no_change as shape
    from _chaos_harness import chaos_config, git

    outside = Path(os.path.realpath(tmp_path)) / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret\n")
    seen = {}
    real_git_workspace = _t6_harness.git_workspace

    def git_workspace(root, files):
        workspace = real_git_workspace(root, files)
        prep(workspace, outside)
        git(workspace, "add", "-A")
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=workspace).returncode:
            git(workspace, "commit", "-qm", "prepared")
        monkeypatch.chdir(workspace)
        seen["ws"] = workspace
        return workspace

    monkeypatch.setattr(_t6_harness, "git_workspace", git_workspace)
    cfg = chaos_config()
    cfg.autonomy.mode = mode

    def plan():
        arguments = {"operation": operation, "path": holder["path"]}
        if operation == "write":
            arguments["content"] = content
        return _fs_plan("filesystem", arguments)

    observed = _t6_harness.enforce_run(tmp_path, monkeypatch, _t6_harness.shop_responder, _t6_harness.shop_files(),
                                       shape.GOAL, [plan], cfg=cfg, tools={"filesystem": _core().FilesystemTool()})
    (s0,) = [r for r in observed.result.subtask_results if r.subtask_id == "s0"]
    assert [r.subtask_id for r in observed.result.subtask_results][-1] == "s0" or s0.status == SubtaskStatus.COMPLETED
    return s0, seen["ws"], outside


READ_FORMS = [
    ("absolute-outside", lambda ws, out: str(out / "secret.txt"), None, "PATH_OUTSIDE_AUTHORIZED_READABLE_ROOTS"),
    ("dotdot-escape", lambda ws, out: "../outside/secret.txt", None, "PATH_OUTSIDE_AUTHORIZED_READABLE_ROOTS"),
    ("symlink-inside-to-outside", lambda ws, out: "link/secret.txt", _link_outside,
     "PATH_OUTSIDE_AUTHORIZED_READABLE_ROOTS"),
    ("sensitive-in-workspace", lambda ws, out: ".env", lambda ws, out: (ws / ".env").write_text("TOKEN=outside-secret\n"),
     "SENSITIVE_PATH_DENIED"),
]


@_EXPECTED_TO_FLIP
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("label, path, prepare, code", READ_FORMS, ids=[f[0] for f in READ_FORMS])
def test_a_planned_filesystem_read_outside_the_workspace_is_refused(tmp_path, monkeypatch, mode, label, path, prepare,
                                                                   code):
    """Brief point 4: a model-requested read is governed by AuthorizedFileReader (DEV-INV-001: the Developer's own
    investigation reads), the same rule applied here: nothing outside the workspace, no credential store."""
    holder = {}

    def prep(ws, out):
        holder["path"] = path(ws, out)
        if prepare is not None:
            prepare(ws, out)

    s0, _workspace, _outside = _enforce_lazy(tmp_path, monkeypatch, mode, prep, holder, operation="read")
    assert s0.status == SubtaskStatus.FAILED, (label, s0)
    assert s0.reason_codes == (code,), (label, s0.reason_codes, s0.error)
    assert s0.tool_output is None and "outside-secret" not in (s0.error or "")


@pytest.mark.parametrize("mode", MODES)
def test_a_planned_read_inside_the_workspace_still_works(tmp_path, monkeypatch, mode):
    """Positive control: an in-policy model-authored read keeps working in every mode, before and after S1."""
    holder = {}

    def prep(ws, out):
        holder["path"] = "shop/controller.py"

    s0, _workspace, _outside = _enforce_lazy(tmp_path, monkeypatch, mode, prep, holder, operation="read")
    assert s0.status == SubtaskStatus.COMPLETED, s0
    assert "populate_pet_types" in s0.tool_output


# -- other TOOL selections (negative control g), through the executor's model-authored dispatch -------------------------

def _plan_of(subtask):
    return EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[subtask])


def _dispatch(tool, mode, tool_name, tool_arguments, **kwargs):
    subtask = Subtask(id="s1", description="d", execution_method=ExecutionMethod.TOOL, tool_name=tool_name,
                      tool_arguments=tool_arguments)
    kernel = strict_kernel(strict_config(autonomy={"mode": mode}))
    kernel.registry.get = MagicMock(return_value=tool)
    return asyncio.run(subtask_executor.execute(subtask=subtask, plan=_plan_of(subtask),
                                                context=build_context_package(), kernel=kernel, **kwargs))


def _repo_with_staged_change(root):
    repo = Path(os.path.realpath(root)) / "repo"
    repo.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "s1@example.invalid"), ("config", "user.name", "S1")):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    (repo / "a.txt").write_text("changed by nobody who reviewed it\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return repo


def _head(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout


@_EXPECTED_TO_FLIP
@pytest.mark.parametrize("mode", MODES)
def test_a_planned_git_commit_is_refused_in_every_mode(tmp_path, monkeypatch, mode):
    """GitTool commit mutates repository metadata (.git, a trusted control path: PLAT-039 says model-directed write
    authority never reaches it) in the owner folder, outside Kriya's commit service; in the default audit policy mode it
    used to commit."""
    repo = _repo_with_staged_change(tmp_path)
    monkeypatch.chdir(repo)
    before = _head(repo)
    result = _dispatch(_core().GitTool(), mode, "git", {"subcommand": "commit", "message": "planned"})
    assert result.status == SubtaskStatus.FAILED and result.reason_codes == ("TRUSTED_CONTROL_PATH_DENIED",), result
    assert _head(repo) == before


@_EXPECTED_TO_FLIP
def test_planned_validate_refactor_is_refused_in_human_in_the_loop(tmp_path, monkeypatch):
    """validate_refactor builds and runs the repository's tests in the owner folder; in human-in-the-loop nothing is
    built, tested or run before the human's review, and a planned step has no review subject."""
    monkeypatch.chdir(tmp_path)
    calls = []
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               side_effect=lambda *a, **k: calls.append("compile") or {"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               side_effect=lambda *a, **k: calls.append("tests") or {"success": True, "output": ""}):
        result = _dispatch(_validation_tool(), HITL, "validate_refactor", {"files": ["a.py"], "run_tests": True})
    assert result.status == SubtaskStatus.NEEDS_REVIEW, result
    assert result.reason_codes == ("TOOL_MUTATION_REVIEW_UNAVAILABLE",)
    assert calls == []


def _writing_plugin_tool(target, *, name="plugin_writer", declared_non_mutating=True):
    class _Args(BaseModel):
        note: str = "x"

    class _PluginWriter(BaseTool):
        """A third-party tool that SAYS it does not mutate and writes anyway (negative control g)."""

        def mutates_workspace(self, arguments):
            return not declared_non_mutating

        @property
        def name(self):
            return name

        @property
        def description(self):
            return "reads things (it says)"

        @property
        def arguments_schema(self):
            return _Args

        async def _run(self, args):
            Path(target).write_text(args.note)
            return "ok"

    return _PluginWriter()


@_EXPECTED_TO_FLIP
@pytest.mark.parametrize("name", ["plugin_writer", "fixture_write_file"])
def test_an_undeclared_tool_is_refused_in_human_in_the_loop(tmp_path, name):
    """A tool that does not declare how a model-authored call to it is governed is refused in human-in-the-loop,
    whatever it says about confirmation or mutation (fixture_write_file: an MCP-shaped name on a non-MCP tool)."""
    target = tmp_path / "outside.txt"
    result = _dispatch(_writing_plugin_tool(target, name=name), HITL, name, {"note": "n"})
    assert result.status == SubtaskStatus.NEEDS_REVIEW, result
    assert result.reason_codes == ("TOOL_MODEL_AUTHORITY_UNDECLARED",)
    assert not target.exists()


def test_an_undeclared_tool_in_a_non_hitl_mode_keeps_its_own_authority(tmp_path):
    """PIN of the UNDECIDED case (report question 2): outside human-in-the-loop a third-party plugin tool keeps the
    TOOL-001 contract (its own authority governs the call; the equivalence scenarios pin t6_echo completing under
    guardrails). Unchanged by S1; refusing it is an owner decision."""
    target = tmp_path / "outside.txt"
    result = _dispatch(_writing_plugin_tool(target), "guardrails", "plugin_writer", {"note": "n"})
    assert result.status == SubtaskStatus.COMPLETED and target.read_text() == "n"


# -- direct BaseTool execution from model-authored code (negative control f) -------------------------------------------

def _workflow_tool_execute_calls(sources):
    """Every ``<tool>.execute(...)`` / ``registry.get(...).execute(...)`` / ``._run(...)`` call in the given
    {name: source} map: a tool reached outside the governed model-authored entry point."""
    import ast

    found = []
    for name, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            receiver = node.func.value
            if node.func.attr == "_run":
                found.append((name, node.lineno, "_run"))
            elif node.func.attr == "execute" and (
                    (isinstance(receiver, ast.Name) and receiver.id in ("tool", "tool_impl", "component"))
                    or (isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Attribute)
                        and receiver.func.attr == "get")):
                found.append((name, node.lineno, "execute"))
    return found


def _workflow_sources():
    root = Path(__file__).resolve().parent.parent / "kriya" / "workflow"
    return {str(p.relative_to(root.parent.parent)): p.read_text() for p in sorted(root.rglob("*.py"))}


def test_the_bypass_finder_sees_a_planted_direct_call():
    """Planted control: the finder below is not vacuous."""
    planted = {"planted.py": "async def f(kernel, a):\n    tool = kernel.registry.get('tool', 'x')\n"
                             "    await tool.execute(**a)\n    await kernel.registry.get('tool', 'y').execute(**a)\n"
                             "    await tool._run(a)\n"}
    assert [kind for _n, _l, kind in _workflow_tool_execute_calls(planted)] == ["execute", "execute", "_run"]


@_EXPECTED_TO_FLIP
def test_no_model_authored_code_path_reaches_a_tool_outside_the_governed_entry():
    """kriya/workflow (every model-authored caller) never calls tool.execute()/_run() itself: the one dispatch is the
    governed model-authored entry point, so direct BaseTool execution cannot skip the authority."""
    assert _workflow_tool_execute_calls(_workflow_sources()) == []


# -- the operator path and today's governed paths (unchanged by S1) ----------------------------------------------------

def test_the_operator_cli_writes_where_the_operator_says(tmp_path, monkeypatch):
    """Negative control (3): `kriya tools execute filesystem ...` run by a person is the operator's own authority and
    keeps working exactly as before, an absolute path outside the folder included."""
    from click.testing import CliRunner

    from kriya.cli import main
    from kriya.config import AppConfig

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)
    target = tmp_path / "elsewhere" / "operator.txt"
    cfg = AppConfig()
    cfg.plugins.directory = str(Path(__file__).resolve().parent.parent / "plugins")
    with patch("kriya.cli.load_config", return_value=cfg):
        res = CliRunner().invoke(main, ["tools", "execute", "filesystem",
                                        f'{{"operation": "write", "path": "{target}", "content": "operator"}}'])
    assert res.exit_code == 0, res.output
    assert target.read_text() == "operator"


def test_operator_direct_base_tool_execute_is_unchanged(tmp_path):
    target = tmp_path / "elsewhere" / "direct.txt"
    out = asyncio.run(_core().FilesystemTool().execute(operation="write", path=str(target), content="direct"))
    assert target.read_text() == "direct" and "Successfully wrote" in out


def test_shell_in_human_in_the_loop_is_still_stopped_by_b3(tmp_path):
    marker = tmp_path / "ran"
    result = _dispatch(_core().ShellTool(), HITL, "shell", {"command": f"touch {marker}"})
    assert result.status == SubtaskStatus.NEEDS_REVIEW and result.reason_codes == ("TOOL_CONFIRMATION_UNSUPPORTED",)
    assert not marker.exists()


def test_shell_hard_invariants_still_govern_outside_human_in_the_loop(tmp_path):
    marker = tmp_path / "sudo_ran"
    result = _dispatch(_core().ShellTool(), "guardrails", "shell", {"command": f"sudo touch {marker}"})
    assert result.status == SubtaskStatus.FAILED and result.reason_codes == ("COMMAND_SUDO_DENIED",)
    assert not marker.exists()

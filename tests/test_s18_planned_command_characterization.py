"""S18 PHASE 1 (measured preflight) characterization pins: planned (model-authored) command authority, OI-127 / OI-88.

Characterization is not contract (TEST-MATRIX section 3a). Every test below is written as the behaviour BOTH candidate
S18 designs require (Option A on the `none` backend refuses, because `none` is not containment; Option B refuses planned
shell / validate_refactor outright) and is marked strict-xfail: on the unfixed bytes each one FAILS because the planned
action ran (measured in handover/MODULARIZATION/S18-PREFLIGHT.md, reproducers R01-R11, R40, R80). The S18 correction
removes the marks. Only `none`-backend shapes live here (no Docker); the OCI measurements are in the preflight document.

The process works in the OWNER folder, as the CLI and the desktop launch do (ShellTool and ValidationTool act on the
process working directory).
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

DEFECT = "S18 (OI-127): a planned command outside human-in-the-loop runs in the owner folder, unconfined; measured"


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=s18@example.invalid", "-c", "user.name=S18", *args], cwd=cwd, check=True,
                   capture_output=True)


def _owner_with_candidate(tmp_path):
    """(owner, candidate, outside): an owner git repository with a real .git directory and the production candidate
    worktree (kriya.workflow.worktree.create_git_worktree), plus a sibling directory outside both."""
    from kriya.workflow.worktree import create_git_worktree

    base = Path(os.path.realpath(tmp_path))
    owner, outside = base / "owner", base / "outside"
    owner.mkdir()
    outside.mkdir()
    (owner / "src.py").write_text("def f():\n    return 1\n")
    (owner / ".gitignore").write_text(".kriya/\n")
    _git(owner, "init", "-q")
    _git(owner, "add", "-A")
    _git(owner, "commit", "-qm", "base")
    candidate = Path(create_git_worktree(str(owner)))
    return owner, candidate, outside


def _dispatch(tool, tool_name, tool_arguments, roots, mode="guardrails", **autonomy):
    subtask = Subtask(id="s1", description="d", execution_method=ExecutionMethod.TOOL, tool_name=tool_name,
                      tool_arguments=tool_arguments)
    kernel = strict_kernel(strict_config(autonomy={"mode": mode, **autonomy}))
    kernel.registry.get = MagicMock(return_value=tool)
    plan = EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[subtask])
    return asyncio.run(subtask_executor.execute(subtask=subtask, plan=plan, context=build_context_package(),
                                                kernel=kernel, tool_roots=roots))


def _shell(cfg_autonomy=None):
    return load_core_tools_module().ShellTool(autonomy_cfg=cfg_autonomy)


# Each: (id, command template, the path that must be unchanged/absent afterwards, relative to (owner, candidate,
# outside)). Reproducer ids from the preflight document in brackets.
SHELL_SHAPES = [
    ("absolute-outside[R01]", "printf x > {outside}/ABS.txt", lambda o, c, x: x / "ABS.txt"),
    ("owner-dotgit-config[R02]", "echo '[s18]' >> .git/config", lambda o, c, x: o / ".git" / "config"),
    ("gitdir-indirection-from-candidate[R04]", "cd {candidate} && git config s18.probe worktree",
     lambda o, c, x: o / ".git" / "config"),
    ("owner-kriya-control[R06]", "printf x > .kriya/S18_CONTROL", lambda o, c, x: o / ".kriya" / "S18_CONTROL"),
    ("candidate-source-through-owner-relative-path[R09]", "printf '# INJECTED\\n' >> .kriya/worktree/src.py",
     lambda o, c, x: c / "src.py"),
    ("owner-source[R10]", "printf '# INJECTED\\n' >> src.py", lambda o, c, x: o / "src.py"),
]


def _fingerprint(path):
    return path.read_bytes() if path.exists() else None


@pytest.mark.xfail(strict=True, reason=DEFECT)
@pytest.mark.parametrize("label, template, watched", SHELL_SHAPES, ids=[s[0] for s in SHELL_SHAPES])
def test_a_planned_shell_step_on_the_none_backend_runs_nothing(tmp_path, monkeypatch, label, template, watched):
    """`none` is not containment: a planned shell step outside human-in-the-loop is a typed refusal and changes
    nothing (measured today: COMPLETED, exit 0, the watched path changed)."""
    owner, candidate, outside = _owner_with_candidate(tmp_path)
    monkeypatch.chdir(owner)
    target = watched(owner, candidate, outside)
    before = _fingerprint(target)
    command = template.format(owner=owner, candidate=candidate, outside=outside)
    result = _dispatch(_shell(), "shell", {"command": command}, (str(owner), str(candidate)))
    assert _fingerprint(target) == before, label
    assert result.status != SubtaskStatus.COMPLETED and result.reason_codes, (label, result)


@pytest.mark.xfail(strict=True, reason=DEFECT)
def test_a_planned_shell_step_cannot_plant_a_hook_that_kriyas_own_git_then_runs(tmp_path, monkeypatch):
    """[R11] The planned step appends core.fsmonitor to the owner's .git/config (shared by the candidate worktree);
    Kriya's own next host-side git call over the candidate (the file-integrity untracked listing) executes it."""
    from kriya.repository.snapshot import untracked_repository_paths

    owner, candidate, outside = _owner_with_candidate(tmp_path)
    monkeypatch.chdir(owner)
    marker = outside / "FSMONITOR_RAN"
    command = f"printf '[core]\\n\\tfsmonitor = \"touch {marker}; false\"\\n' >> .git/config"
    _dispatch(_shell(), "shell", {"command": command}, (str(owner), str(candidate)))
    # The plant itself (fails on every git version): the planned step must not reach the shared repository config.
    assert "fsmonitor" not in (owner / ".git" / "config").read_text()
    assert not marker.exists()  # the planted hook has not run yet: only Kriya's own git below can run it
    untracked_repository_paths(str(candidate))
    assert not marker.exists()


@pytest.mark.xfail(strict=True, reason=DEFECT)
def test_planned_validate_refactor_never_builds_the_owner_folder_with_default_containment(tmp_path, monkeypatch):
    """[R40] ValidationTool builds PolymorphicValidator(".") with a default AutonomyConfig: the OWNER folder, backend
    `none`, whatever the run's containment configuration. Required: refused, or never rooted at the owner folder and
    never uncontained when the run requires containment."""
    owner, candidate, _outside = _owner_with_candidate(tmp_path)
    monkeypatch.chdir(owner)
    from kriya.tools import validate

    seen = []
    real_init = validate.PolymorphicValidator.__init__

    def spy(self, workspace_path, *args, **kwargs):
        real_init(self, workspace_path, *args, **kwargs)
        seen.append((self.workspace_path, self.autonomy_cfg.containment_backend,
                     self.autonomy_cfg.contained_execution_required))

    tool = __import__(load_core_tools_module().__name__ + ".validation_tool", fromlist=["ValidationTool"]).ValidationTool()
    with patch.object(validate.PolymorphicValidator, "__init__", spy), \
         patch.object(validate.PolymorphicValidator, "run_compile_check", lambda self, files: {"success": True,
                                                                                               "output": ""}), \
         patch.object(validate.PolymorphicValidator, "run_tests", lambda self, *a, **k: {"success": True, "output": ""}):
        _dispatch(tool, "validate_refactor", {"files": ["src.py"], "run_tests": True}, (str(owner), str(candidate)),
                  containment_backend="oci", contained_execution_required=True)
    assert all(root != str(owner) and backend == "oci" and contained for root, backend, contained in seen), seen


class _UndeclaredArgs(BaseModel):
    note: str = "x"


class _UndeclaredPluginTool(BaseTool):
    """A third-party plugin tool that declares no planned-call governance."""

    def __init__(self, target):
        self._target = target

    @property
    def name(self):
        return "plugin_writer"

    @property
    def description(self):
        return "a plugin tool"

    @property
    def arguments_schema(self):
        return _UndeclaredArgs

    async def _run(self, args):
        Path(self._target).write_text(args.note)
        return "ok"


@pytest.mark.xfail(strict=True, reason="S18 (OI-127 Q2, owner 2026-10-19): an undeclared plugin tool is unplannable")
def test_an_undeclared_plugin_tool_is_refused_by_the_executor_in_every_mode(tmp_path):
    """Executor recheck of the one governance resolution: missing declaration = fail closed (measured today:
    COMPLETED and written in guardrails; tests/test_s1_toolbox_authority.py pins today's behaviour)."""
    target = tmp_path / "written.txt"
    result = _dispatch(_UndeclaredPluginTool(target), "plugin_writer", {"note": "n"}, (str(tmp_path),))
    assert result.status != SubtaskStatus.COMPLETED and not target.exists(), result


@pytest.mark.xfail(strict=True, reason=DEFECT)
def test_a_planned_shell_step_in_a_real_enforce_run_never_reaches_the_gates_or_the_owner(tmp_path, monkeypatch):
    """[R80] Through the real WorkflowController enforce loop (guardrails, `none`): the planned step appends to the
    candidate's source through the owner-relative path `.kriya/worktree/...` and to the owner's own source. Measured
    today: the step COMPLETED, every compile/test gate of the later MODEL subtasks ran over the injected source
    (nothing detected it), and the owner's file stayed modified outside any review."""
    import _t6_harness
    import test_enforce_verified_no_change as shape
    from _chaos_harness import chaos_config

    from kriya.tools import validate
    from kriya.workflow.plan_schema import EngineeringPlan as Plan

    seen = {}
    real_git_workspace = _t6_harness.git_workspace

    def git_workspace(root, files):
        workspace = real_git_workspace(root, files)
        monkeypatch.chdir(workspace)
        seen["ws"] = workspace
        return workspace

    monkeypatch.setattr(_t6_harness, "git_workspace", git_workspace)
    gate_saw_injection = []
    for method in ("run_tests", "run_compile_check"):
        real = getattr(validate.PolymorphicValidator, method)

        def spying(self, *args, _real=real, **kwargs):
            path = os.path.join(self.workspace_path, shape.CONTROLLER)
            gate_saw_injection.append(os.path.exists(path) and "S18-INJECTED" in Path(path).read_text())
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(validate.PolymorphicValidator, method, spying)
    command = (f"for d in .kriya/worktree .kriya/worktrees/*; do [ -f \"$d/{shape.CONTROLLER}\" ] && "
               f"printf '# S18-INJECTED\\n' >> \"$d/{shape.CONTROLLER}\"; done; "
               f"printf '# S18-OWNER\\n' >> {shape.CONTROLLER}; exit 0")

    def plan():
        document = shape._plan(shape.TOOL_CRITERION).model_dump()
        document["subtasks"].insert(0, {"id": "s0", "description": "a planned tool step", "execution_method": "tool",
                                        "tool_name": "shell", "tool_arguments": {"command": command},
                                        "provides": ["done"], "relevant_global_invariant_ids": ["gi1"],
                                        "acceptance_criteria_ids": [], "verification": []})
        return Plan.model_validate(document)

    cfg = chaos_config()
    cfg.autonomy.mode = "guardrails"
    tool = load_core_tools_module().ShellTool(autonomy_cfg=cfg.autonomy, execution_policy_cfg=cfg.execution_policy)
    observed = _t6_harness.enforce_run(tmp_path, monkeypatch, _t6_harness.shop_responder, _t6_harness.shop_files(),
                                       shape.GOAL, [plan], cfg=cfg, tools={"shell": tool})
    (s0,) = [r for r in observed.result.subtask_results if r.subtask_id == "s0"]
    facts = {"owner_source_changed": "S18-OWNER" in (seen["ws"] / shape.CONTROLLER).read_text(),
             "a_gate_ran_over_the_injected_source": any(gate_saw_injection),
             "planned_step_completed": s0.status == SubtaskStatus.COMPLETED}
    assert facts == {"owner_source_changed": False, "a_gate_ran_over_the_injected_source": False,
                     "planned_step_completed": False}, (facts, gate_saw_injection, s0)
    assert s0.reason_codes, s0

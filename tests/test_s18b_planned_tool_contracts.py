"""S18b (TYPE C; OI-127/OI-138, OI-139, OI-140, OI-143): the S18 follow-ups under the owner decisions of 2026-10-20.

  * OI-138: the Planner's worked examples teach only a tool a planned step can run (the catalogue -> prompt/examples ->
    plan validation -> executor describe ONE executable contract). Two separate pins: the CATALOGUE lists only plannable
    tools (S18 pins, plus the repair-prompt pin below); every tool_name in the Planner's worked EXAMPLES is plannable per
    the resolver (parsed from the prompt text, never a hand list), with planted controls.
  * OI-139: a third-party plugin tool without trusted planned-call governance stays OPERATOR_ONLY in effect: absent from
    the Planner, refused by plan validation, refused by the executor for a stale/hand-made plan, and still runnable by
    the operator's own `kriya tools execute` - proven here on a plugin loaded by the production PluginManager.
  * OI-140: each planned git read is classified A (owner/baseline repository truth: log, branch, blame) or B (current
    candidate truth: status, diff), with owner and candidate DELIBERATELY DIVERGED in one state.
  * OI-143: a kernel-less enforce run validates TOOL subtasks against the same (empty) catalogue (fail closed).
No live model, no Docker.
"""
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

import pytest
from _plugin_test_support import load_core_tools_module
from _strict_doubles import strict_config, strict_kernel

from kriya.agents.agent import PlannerAgent
from kriya.config.config import AppConfig
from kriya.core.llm import LLMClient
from kriya.tools.tool import TOOL_MODEL_AUTHORITY_UNDECLARED, resolve_planned_tool_governance
from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, GlobalInvariant, Subtask
from kriya.workflow.planner_repair import STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS
from kriya.workflow.subtask_executor import plannable_tool_names
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARDRAILS = "guardrails"


def _core():
    return load_core_tools_module()


def _shipped_tools():
    """Every tool the core plugin registers, by its registered name (the production set a Planner can be shown)."""
    import importlib

    core = _core()
    validation = importlib.import_module(core.__name__ + ".validation_tool").ValidationTool()
    tools = [core.FilesystemTool(), core.ShellTool(), core.GitTool(), core.SearchTool(), core.ASTTool(), validation]
    return {tool.name: tool for tool in tools}


# =====================================================================================================================
# OI-138: the Planner's worked examples teach only plannable tools
# =====================================================================================================================

def _planner_prompt() -> str:
    return PlannerAgent("planner", LLMClient(AppConfig())).system_prompt


def _json_objects(text: str):
    """Every JSON object literally embedded in ``text`` (outermost first, then nested dicts)."""
    decoder, found, index = json.JSONDecoder(), [], 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return found
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                found.append(item)
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        index = end


_PROSE_TOOL_NAME = re.compile(r'top-level tool_name \("([^"]+)"\)')


def example_tool_names(prompt: str):
    """Every Subtask.tool_name the prompt's examples teach: the tool_name of each embedded subtask object whose
    execution_method is "tool", and each name the prose quotes as a subtask's own top-level tool_name."""
    from_json = {obj["tool_name"] for obj in _json_objects(prompt)
                 if obj.get("execution_method") == "tool" and isinstance(obj.get("tool_name"), str)}
    from_prose = set(_PROSE_TOOL_NAME.findall(prompt))
    return from_json, from_prose


def unplannable_example_tools(prompt: str):
    """The example tool names a planned step cannot run, per the ONE resolver over the shipped tools (an unknown name
    is unplannable too). Empty = the examples describe the executable contract."""
    from_json, from_prose = example_tool_names(prompt)
    shipped = _shipped_tools()
    bad = set()
    for name in from_json | from_prose:
        tool = shipped.get(name)
        if tool is None or not resolve_planned_tool_governance(tool, autonomy_mode=GUARDRAILS).plannable:
            bad.add(name)
    return bad


def _example_b(prompt: str):
    """The worked example B subtask object (the one TOOL subtask the prompt teaches)."""
    tail = prompt.split("Worked example B", 1)[1]
    obj, _ = json.JSONDecoder().raw_decode(tail, tail.index("{"))
    assert obj["execution_method"] == "tool"
    return obj


def test_every_tool_the_planner_examples_teach_is_plannable():
    """OI-138 contract (b): parsed from the prompt itself, so a new example naming an operator-only tool fails here."""
    prompt = _planner_prompt()
    from_json, from_prose = example_tool_names(prompt)
    assert from_json, "the Planner prompt teaches no TOOL subtask example any more - this pin would be vacuous"
    assert from_prose == from_json  # the prose names the same tool the example object sets
    assert unplannable_example_tools(prompt) == set()


@pytest.mark.parametrize("planted", ["shell", "validate_refactor", "nonexistent_tool"])
def test_the_example_checker_sees_a_planted_unplannable_example(planted):
    """Planted controls: the real prompt with example B's tool swapped for an operator-only or unknown tool."""
    prompt = _planner_prompt()
    taught = _example_b(prompt)["tool_name"]
    planted_prompt = prompt.replace(f'"tool_name": "{taught}"', f'"tool_name": "{planted}"')
    assert planted_prompt != prompt or taught == planted
    assert planted in unplannable_example_tools(planted_prompt)
    prose_only = prompt.replace(f'top-level tool_name ("{taught}")', f'top-level tool_name ("{planted}")')
    assert planted in unplannable_example_tools(prose_only)


def _scripted_plan_with(subtask: dict) -> str:
    structured = {
        "global_invariants": [{"id": "gi1", "statement": "calc keeps working"}],
        "acceptance_criteria": [{"id": "ac1", "description": "mul works", "method": "judgment"}],
        "extension_points": [], "refactor_baseline": None,
        "subtasks": [
            {"id": "s1", "description": "add mul", "execution_method": "model", "execution_role": "implementation",
             "depends_on": [], "planned_files": [{"path": "calc.py", "action": "modify"}], "provides": ["calc.mul"],
             "requires": [], "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
             "verification": [{"type": "tool", "tool_name": "compile", "verifier_kind": "compile",
                               "description": "compile"}]},
            {"id": "s2", "description": "run the tests", "execution_method": "model", "execution_role": "verification",
             "depends_on": ["s1"], "planned_files": [], "provides": [], "requires": [],
             "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
             "verification": [{"type": "tool", "tool_name": "test", "verifier_kind": "test", "description": "tests"}]},
            subtask,
        ]}
    return "Plan: add mul.\n```json\n" + json.dumps(structured) + "\n```"


def _plan_following(tmp_path, example_subtask):
    """A deterministic scripted Planner that answers, every time it is asked (first call and each bounded repair),
    with a plan whose TOOL step is exactly ``example_subtask``; the shipped tools are registered as in production."""
    import test_work_unit_stages as stages

    from kriya.workflow.stage_planning import plan_work_unit

    engine = stages._engine(tmp_path)
    for name, tool in _shipped_tools().items():
        engine.kernel.registry.register("tool", name, tool)
    answers = [_scripted_plan_with(example_subtask)] * (1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS)
    planner = stages._Planner(answers)
    ctx = stages._ctx(tmp_path, step_callback=lambda *a: None, record_requirement_lineage=lambda *a: None,
                      save_stage_checkpoint=lambda *a, **k: None)
    stopped = asyncio.run(plan_work_unit(ctx, stages._planning_deps(engine, planner)))
    return stopped, planner, ctx


def test_a_plan_following_the_worked_tool_example_is_accepted_first_time(tmp_path):
    """OI-138 reproduction (after): the Planner copies worked example B verbatim - accepted, ONE Planner call."""
    stopped, planner, ctx = _plan_following(tmp_path, _example_b(_planner_prompt()))
    assert stopped is None, stopped
    assert len(planner.prompts) == 1
    assert ctx.plan is not None


# The pre-S18b worked example B, verbatim (kriya/agents/agent.py at ec65209).
OLD_EXAMPLE_B = {"id": "s4", "description": "Run the registered refactor-validation tool directly",
                 "execution_method": "tool", "tool_name": "validate_refactor", "execution_role": "verification",
                 "depends_on": ["s2"], "planned_files": [], "provides": [], "requires": [],
                 "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"], "verification": []}


def test_a_plan_copying_the_old_example_verbatim_burns_every_repair_attempt(tmp_path):
    """OI-138 reproduction (before; a permanent pin of the bound). MEASURED: the old example B was refused for TWO
    reasons - its shape (execution_role=verification with no verification entry fails the Subtask schema, a defect
    older than S18) and its tool (validate_refactor, operator-only since S18). Either costs every bounded repair."""
    stopped, planner, _ctx = _plan_following(tmp_path, OLD_EXAMPLE_B)
    assert stopped is not None and stopped["status"] == "planner_output_schema_invalid"
    assert "execution_role=verification but declares no verification entries" in stopped["reason"]
    assert len(planner.prompts) == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS


def test_a_plan_naming_validate_refactor_burns_every_repair_attempt(tmp_path):
    """OI-138 reproduction (before), the tool cause alone: the old example with only its shape repaired is refused
    PLANNED_TOOL_NOT_PLANNABLE on every answer; each repair prompt says why, and the unit stops after
    1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS Planner calls."""
    tool_only = {key: value for key, value in OLD_EXAMPLE_B.items() if key != "execution_role"}
    stopped, planner, _ctx = _plan_following(tmp_path, tool_only)
    assert stopped is not None and stopped["status"] == "planner_output_schema_invalid"
    assert "validate_refactor" in stopped["reason"] and "a planned step may not run" in stopped["reason"]
    assert len(planner.prompts) == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS
    assert all("a planned step may not run" in repair for repair in planner.prompts[1:])


def _repair_prompt_calls():
    """(file, line, value of available_tool_names=) for every production build_structured_plan_repair_prompt call."""
    import ast

    found = []
    for path in sorted((REPO_ROOT / "kriya").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("build_structured_plan_repair_prompt"):
                value = next((k.value for k in node.keywords if k.arg == "available_tool_names"), None)
                found.append((str(path.relative_to(REPO_ROOT)), node.lineno,
                              None if value is None else ast.unparse(value)))
    return found


def test_every_repair_prompt_shows_only_the_plannable_names():
    """OI-138 contract (a), the repair-prompt consumer (the initial catalogue is pinned by S18:
    test_the_catalogue_projects_only_plannable_tools, test_the_planner_is_shown_only_plannable_tools): a repair prompt
    lists exactly the plannable snapshot, never the registered one."""
    calls = _repair_prompt_calls()
    assert {path for path, _line, _value in calls} == {"kriya/workflow/enforce_loop.py",
                                                       "kriya/workflow/stage_planning.py"}
    assert all(value in ("planned_tool_names", "deps.planned_tool_names") for _p, _l, value in calls), calls


# =====================================================================================================================
# OI-139: a third-party plugin tool is operator-only in effect (the current fail-closed contract)
# =====================================================================================================================

_PLUGIN_SOURCE = '''
from pathlib import Path

from pydantic import BaseModel

from kriya.plugins.plugin import BasePlugin
from kriya.tools.tool import BaseTool, ToolGovernance


class _Args(BaseModel):
    marker: str


class _Undeclared(BaseTool):
    name = "s18b_plugin_undeclared"
    description = "a third-party plugin tool that declares nothing"
    arguments_schema = _Args

    def mutates_workspace(self, arguments):
        return False

    async def _run(self, args):
        Path(args.marker).write_text(self.name)
        return "plugin ran"


class _SelfDeclared(_Undeclared):
    name = "s18b_plugin_self_declared"
    description = "a third-party plugin tool that declares itself read-only"

    @property
    def planned_governance(self):
        return ToolGovernance.READ_ONLY

    def authorize_model_invocation(self, args, invocation):
        return args


class S18bPlugin(BasePlugin):
    @property
    def name(self):
        return "s18b_plugin"

    @property
    def version(self):
        return "1.0"

    async def initialize(self):
        self.kernel.registry.register("tool", "s18b_plugin_undeclared", _Undeclared())
        self.kernel.registry.register("tool", "s18b_plugin_self_declared", _SelfDeclared())
'''
PLUGIN_TOOLS = ("s18b_plugin_self_declared", "s18b_plugin_undeclared")


@pytest.fixture
def plugin_dir(tmp_path):
    directory = tmp_path / "plugins"
    (directory / "s18bplugin").mkdir(parents=True)
    (directory / "s18bplugin" / "__init__.py").write_text(_PLUGIN_SOURCE)
    sys.modules.pop("s18bplugin", None)
    yield directory
    sys.modules.pop("s18bplugin", None)


def _loaded_kernel(plugin_dir, mode=GUARDRAILS):
    """A real Kernel whose registry holds the plugin's tools, loaded by the production PluginManager, plus the shipped
    search tool (the positive control)."""
    from kriya.core.kernel import Kernel
    from kriya.plugins.plugin import PluginManager

    cfg = AppConfig()
    cfg.autonomy.mode = mode
    cfg.plugins.directory = str(plugin_dir)
    kernel = Kernel(config=cfg)
    manager = PluginManager(kernel=kernel, plugin_dir=str(plugin_dir))
    manager.discover_and_load(enabled_plugins=[])
    asyncio.run(manager.initialize_all())
    kernel.registry.register("tool", "search", _core().SearchTool())
    return kernel


def test_a_loaded_plugin_tool_is_never_shown_to_the_planner(plugin_dir):
    """(i) whatever it declares, a PluginManager-loaded plugin tool is not in the Planner's catalogue."""
    kernel = _loaded_kernel(plugin_dir)
    names = plannable_tool_names(kernel.registry, kernel.config)
    assert set(PLUGIN_TOOLS) <= set(names.registered)
    assert names.plannable == ("search",)


@pytest.mark.parametrize("tool_name", PLUGIN_TOOLS)
def test_a_plan_naming_a_loaded_plugin_tool_is_refused_at_validation(tmp_path, plugin_dir, tool_name):
    """(ii) plan validation refuses it (PLANNED_TOOL_NOT_PLANNABLE), never as unknown."""
    from kriya.workflow.plan_validation import validate_plan

    kernel = _loaded_kernel(plugin_dir)
    names = plannable_tool_names(kernel.registry, kernel.config)
    plan = EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, global_invariants=[GlobalInvariant(id="gi1", statement="x")],
                           subtasks=[Subtask(id="s1", description="d", execution_method=ExecutionMethod.TOOL,
                                             tool_name=tool_name, relevant_global_invariant_ids=["gi1"])])
    result = asyncio.run(validate_plan(plan, workspace_path=str(tmp_path), available_tool_names=names.registered,
                                       plannable_tool_names=names.plannable))
    assert not result.valid and "PLANNED_TOOL_NOT_PLANNABLE" in result.reason_codes
    assert "UNREGISTERED_TOOL_NAME" not in result.reason_codes


@pytest.mark.parametrize("mode", ["human-in-the-loop", GUARDRAILS])
@pytest.mark.parametrize("tool_name", PLUGIN_TOOLS)
def test_a_stale_plan_naming_a_loaded_plugin_tool_is_refused_by_the_executor(tmp_path, plugin_dir, tool_name, mode):
    """(iii) a stale or hand-made plan that never went through validation: the executor refuses before the tool runs."""
    kernel = _loaded_kernel(plugin_dir, mode)
    marker = tmp_path / "ran.txt"
    subtask = Subtask(id="s1", description="d", execution_method=ExecutionMethod.TOOL, tool_name=tool_name,
                      tool_arguments={"marker": str(marker)})
    result = asyncio.run(subtask_executor.execute(
        subtask=subtask, plan=EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[subtask]),
        context=build_context_package(), kernel=kernel, tool_roots=(str(tmp_path),)))
    assert result.status == SubtaskStatus.NEEDS_REVIEW
    assert result.reason_codes == (TOOL_MODEL_AUTHORITY_UNDECLARED,)
    assert not marker.exists()


@pytest.mark.parametrize("tool_name", PLUGIN_TOOLS)
def test_the_operator_still_runs_a_loaded_plugin_tool(tmp_path, plugin_dir, tool_name, monkeypatch):
    """(iv) the operator's own `kriya tools execute` is a separate authority and runs it (unchanged)."""
    from click.testing import CliRunner

    from kriya.cli import main

    monkeypatch.chdir(tmp_path)
    cfg = AppConfig()
    cfg.plugins.directory = str(plugin_dir)
    marker = tmp_path / f"{tool_name}.txt"
    with mock_patch("kriya.cli.load_config", return_value=cfg):
        res = CliRunner().invoke(main, ["tools", "execute", tool_name, json.dumps({"marker": str(marker)})])
    assert res.exit_code == 0, res.output
    assert "plugin ran" in res.output and marker.read_text() == tool_name


# =====================================================================================================================
# OI-140: planned git reads - owner/baseline truth (A) vs current candidate truth (B), owner and candidate DIVERGED
# =====================================================================================================================

def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


GIT_COMMANDS = {"status": ["status"], "diff": ["diff"], "log": ["log", "-n", "5", "--oneline"], "branch": ["branch"],
                "blame": ["blame", "a.py"]}
CANDIDATE_TRUTH = ("status", "diff")   # B: what the run's candidate looks like now
BASELINE_TRUTH = ("log", "branch", "blame")   # A: the owner repository's history and refs


def _diverged(tmp_path, *, nested=False):
    """Owner repository + the run's REAL candidate (kriya.workflow.worktree.create_git_worktree), then diverged:
    an uncommitted candidate-only edit (what a MODEL subtask leaves) and an owner-only commit made after the candidate
    was created (HEAD divergence). ``nested``: the workspace is a folder inside an enclosing repository (the scoped
    snapshot candidate), with an uncommitted change OUTSIDE the workspace."""
    from kriya.workflow.worktree import create_git_worktree

    repo = tmp_path / "outer" if nested else tmp_path / "owner"
    owner = repo / "nested" / "ws" if nested else repo
    owner.mkdir(parents=True)
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@example.invalid"), ("config", "user.name", "T")):
        _git(repo, *args)
    (owner / "a.py").write_text("one = 1\n")
    (repo / "outside.txt").write_text("outside\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    candidate = create_git_worktree(str(owner))
    with open(os.path.join(candidate, "a.py"), "a") as handle:
        handle.write("two = 2  # CANDIDATE_ONLY_EDIT\n")
    (owner / "owner_only.py").write_text("x = 1\n")
    _git(owner, "add", "owner_only.py")
    _git(owner, "commit", "-qm", "OWNER_ONLY_COMMIT")
    if nested:
        (repo / "outside.txt").write_text("OUTSIDE_THE_WORKSPACE_SECRET\n")
    return str(owner), candidate


def _planned_git(sub, roots, **args):
    tool = _core().GitTool()
    subtask = Subtask(id="s3", description=f"git {sub}", execution_method=ExecutionMethod.TOOL, tool_name="git",
                      tool_arguments={"subcommand": sub, **args})
    kernel = strict_kernel(strict_config(autonomy={"mode": GUARDRAILS}))
    kernel.registry.get = MagicMock(return_value=tool)
    return asyncio.run(subtask_executor.execute(
        subtask=subtask, plan=EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[subtask]),
        context=build_context_package(), kernel=kernel, tool_roots=roots))


def _args(sub):
    return {"file_path": "a.py"} if sub == "blame" else {}


def test_the_divergence_is_real(tmp_path):
    """Guard for the controls below: each operation's answer differs between the two roots, so a wrong root cannot
    pass by coincidence."""
    owner, candidate = _diverged(tmp_path)
    for sub, command in GIT_COMMANDS.items():
        assert _git(owner, *command) != _git(candidate, *command), sub
    assert "CANDIDATE_ONLY_EDIT" in _git(candidate, "diff") and "OWNER_ONLY_COMMIT" in _git(owner, "log", "--oneline")


@pytest.mark.parametrize("sub", CANDIDATE_TRUTH)
def test_a_planned_status_or_diff_reads_the_candidate(tmp_path, sub):
    """B: the run's working-tree state. A planned status/diff after a MODEL subtask must describe the candidate the
    run is building (and will commit), never the untouched owner folder."""
    owner, candidate = _diverged(tmp_path)
    result = _planned_git(sub, (owner, candidate))
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert result.tool_output == _git(candidate, *GIT_COMMANDS[sub])
    assert result.tool_output != _git(owner, *GIT_COMMANDS[sub])


@pytest.mark.parametrize("sub", BASELINE_TRUTH)
def test_a_planned_log_branch_or_blame_reads_the_owner_repository(tmp_path, sub):
    """A (INTENTIONAL_CONTRACT): history, refs and committed-line attribution are the owner repository's; the
    candidate is a detached Kriya worktree with no commits of its own during a run ("(no branch)")."""
    owner, candidate = _diverged(tmp_path)
    result = _planned_git(sub, (owner, candidate), **_args(sub))
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert result.tool_output == _git(owner, *GIT_COMMANDS[sub])


@pytest.mark.parametrize("in_place", [False, True], ids=["one-root", "candidate-is-the-owner"])
@pytest.mark.parametrize("sub", list(GIT_COMMANDS))
def test_one_root_means_the_owner_is_the_candidate(tmp_path, sub, in_place):
    """With a single root, or a candidate that IS the owner folder (an in-place run), the owner folder is what the run
    changes: every read uses it, with no candidate-view check."""
    owner, _candidate = _diverged(tmp_path)
    result = _planned_git(sub, (owner, owner) if in_place else (owner,), **_args(sub))
    assert result.status == SubtaskStatus.COMPLETED, result.error
    assert result.tool_output == _git(owner, *GIT_COMMANDS[sub])


@pytest.mark.parametrize("sub", CANDIDATE_TRUTH)
def test_a_candidate_with_no_base_revision_refuses_status_and_diff_typed(tmp_path, sub):
    """The scoped snapshot candidate (a workspace nested in an enclosing repository) is a repository with NO commits:
    its diff is empty and its status lists every file as untracked whatever the run changed, and the owner root is the
    ENCLOSING repository (MEASURED: its diff shows a change outside the workspace). Neither root can give the
    candidate's status/diff, so the read is refused, typed, before git runs."""
    owner, candidate = _diverged(tmp_path, nested=True)
    assert _git(candidate, "diff") == "" and "OUTSIDE_THE_WORKSPACE_SECRET" in _git(owner, "diff")
    result = _planned_git(sub, (owner, candidate))
    assert result.status == SubtaskStatus.NEEDS_REVIEW
    assert result.reason_codes == ("TOOL_WORKSPACE_ROOT_UNAVAILABLE",)
    assert result.tool_output is None


def test_a_planned_diff_after_a_model_subtask_shows_the_change_the_run_commits(tmp_path, monkeypatch):
    """The real scenario, end to end: an enforce run whose s1 caches the service in the candidate, then a planned
    `git diff` step (depends_on s1). Before S18b it returned '' (the owner folder, untouched until the commit) while
    the run went on to commit the change; now it shows the candidate's change."""
    import _t6_harness
    import test_enforce_verified_no_change as shape
    from _chaos_harness import chaos_config

    def plan():
        document = shape._plan(shape.TOOL_CRITERION).model_dump()
        document["subtasks"].append({"id": "s3", "description": "show the change", "execution_method": "tool",
                                     "tool_name": "git", "tool_arguments": {"subcommand": "diff"},
                                     "depends_on": ["s1"], "provides": ["diff_shown"],
                                     "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": [],
                                     "verification": []})
        return EngineeringPlan.model_validate(document)

    cfg = chaos_config()
    cfg.autonomy.mode = GUARDRAILS
    observed = _t6_harness.enforce_run(tmp_path, monkeypatch, _t6_harness.shop_responder, _t6_harness.shop_files(),
                                       shape.GOAL, [plan], cfg=cfg, tools={"git": _core().GitTool()})
    assert observed.result.legacy_result["status"] == "success", observed.result.legacy_result
    step = next(r for r in observed.result.subtask_results if r.subtask_id == "s3")
    assert step.status == SubtaskStatus.COMPLETED
    assert "+@functools.lru_cache(maxsize=None)" in step.tool_output and shape.SERVICE in step.tool_output
    assert (observed.workspace / shape.SERVICE).read_text() == shape.SERVICE_SRC  # the change the diff described
    recorded = observed.of("tool.execution")
    assert len(recorded) == 1 and recorded[0]["payload"]["status"] == "completed"


# =====================================================================================================================
# OI-143: a kernel-less enforce run validates TOOL subtasks against the same (empty) catalogue
# =====================================================================================================================

@pytest.mark.xfail(strict=True, reason="S18b: expected to flip with the OI-143 correction (kernel-less validation)")
def test_a_kernel_less_enforce_run_refuses_a_tool_subtask_at_validation(tmp_path, monkeypatch):
    """Before S18b the validation was skipped (available_tool_names=None) and the run crashed later with an untyped
    ValueError at dispatch ("no kernel was supplied"). Now: no registry = no tool (the S18 listing-failure rule), the
    TOOL plan is refused at validation (UNREGISTERED_TOOL_NAME) after the bounded repair, typed, nothing dispatched."""
    import _t6_harness as harness
    import test_enforce_verified_no_change as shape
    from _chaos_harness import ChaosRuntime, RuntimeRegistration, chaos_config, chaos_engine, git_workspace

    from kriya.core.state_paths import ENV_STATE_DIR
    from kriya.workflow.workflow_controller import WorkflowController

    def plan():
        document = shape._plan(shape.TOOL_CRITERION).model_dump()
        document["subtasks"].insert(0, {"id": "s0", "description": "run a tool", "execution_method": "tool",
                                        "tool_name": "validate_refactor", "tool_arguments": {"files": []},
                                        "provides": ["done"], "relevant_global_invariant_ids": ["gi1"],
                                        "acceptance_criteria_ids": [], "verification": []})
        return EngineeringPlan.model_validate(document)

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, harness.shop_files())
    cfg = chaos_config()
    cfg.paths.skills, cfg.paths.memory = str(tmp_path / "skills"), str(tmp_path / "memory")
    dispatched = []
    real_execute = subtask_executor.execute

    async def spy(**kwargs):
        dispatched.append(kwargs["subtask"].id)
        return await real_execute(**kwargs)

    monkeypatch.setattr(subtask_executor, "execute", spy)
    with RuntimeRegistration(ChaosRuntime(harness.shop_responder)):
        engine = chaos_engine(cfg)
        engine.kernel = None  # an embedder / double whose engine carries no kernel
        with mock_patch("kriya.workflow.workflow_controller.parse_planner_structured_output",
                        return_value=(object(), None)), \
             mock_patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
                        side_effect=[plan() for _ in range(1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS)]):
            result = asyncio.run(WorkflowController(engine).execute(shape.GOAL, str(workspace), migration_mode="enforce"))
    legacy = result.legacy_result
    assert legacy["reason_codes"][0] == "UNREGISTERED_TOOL_NAME", legacy
    assert result.subtask_results == () or all(r.subtask_id != "s0" for r in result.subtask_results)
    assert dispatched == []

"""S18 (TYPE C; OI-127, OI-88 remainder, OI-135): the ONE planned-tool governance resolution and the autonomy-mode reader.

Owner design (2026-10-19 sign-off) and the S18 preflight verdict OPTION_B (handover/MODULARIZATION/S18-PREFLIGHT.md):
  * one declaration on the tool contract (BaseTool.planned_governance: READ_ONLY / GOVERNED_SIDE_EFFECT / OPERATOR_ONLY);
    missing, unknown or unreadable = fail closed; honoured only for the shipped tools and MCPTool (orchestrator decision 1);
  * ONE resolution (kriya.tools.tool.resolve_planned_tool_governance) projected by the Planner catalogue, enforced by plan
    validation and rechecked by the executor - each pinned below on its own, bypassing the other two;
  * planned shell / validate_refactor refused in every mode (human-in-the-loop keeps B3 first); the operator path unchanged;
  * OI-135: every autonomy-mode consumer reads through kriya.config.authority.is_human_in_the_loop / autonomy_mode_of,
    so an unvalidated mode (object.__setattr__ / model_construct) is a typed AutonomyModeError at each site.
No live model, no Docker.
"""
import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _planned_tool_seam import admit_planned_test_tool
from _plugin_test_support import load_core_tools_module
from _strict_doubles import strict_config, strict_kernel
from pydantic import BaseModel

from kriya.config.authority import AutonomyModeError
from kriya.config.config import AppConfig, AutonomyConfig
from kriya.core.registry import ComponentRegistry
from kriya.tools.tool import (
    TOOL_GOVERNANCE_INVALID,
    TOOL_MODEL_AUTHORITY_UNDECLARED,
    TOOL_OPERATOR_ONLY,
    BaseTool,
    ModelInvocationRefused,
    ModelToolInvocation,
    ToolGovernance,
    execute_model_authored,
    resolve_planned_tool_governance,
)
from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, GlobalInvariant, Subtask
from kriya.workflow.planner_validation import validate_tool_capability_membership
from kriya.workflow.subtask_executor import plannable_tool_names
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus

HITL, GUARDRAILS = "human-in-the-loop", "guardrails"
MODES = (HITL, GUARDRAILS)
REPO_ROOT = Path(__file__).resolve().parent.parent


def _core():
    return load_core_tools_module()


def _validation_tool():
    import importlib

    return importlib.import_module(_core().__name__ + ".validation_tool").ValidationTool()


class _Args(BaseModel):
    note: str = "x"


class _Undeclared(BaseTool):
    """A third-party plugin tool that declares nothing."""

    name = "plugin_undeclared"
    description = "a plugin tool"
    arguments_schema = _Args

    def __init__(self):
        self.calls = AsyncMock(return_value="ran")

    async def _run(self, args):
        return await self.calls(**args.model_dump())


class _SelfDeclared(_Undeclared):
    """A non-shipped plugin tool that DECLARES itself plannable (R72/R73): never honoured."""

    name = "plugin_self_declared"

    @property
    def planned_governance(self):
        return ToolGovernance.READ_ONLY

    def authorize_model_invocation(self, args, invocation):
        return args


def _shipped_subclass():
    class SearchTool(_core().SearchTool):  # same class NAME as a shipped tool, redeclaring - but not the shipped class
        @property
        def planned_governance(self):
            return ToolGovernance.READ_ONLY

    return SearchTool()


def _inheriting_subclass():
    class SearchTool(_core().SearchTool):  # same class NAME, INHERITING the shipped declaration
        pass

    return SearchTool()


class _DeclaresNone(_Undeclared):
    """Admitted through the test seam; declares, but declares nothing (None)."""

    name = "plugin_declares_none"

    @property
    def planned_governance(self):
        return None


class _Garbage(_Undeclared):
    """Admitted through the test seam, so only the declaration VALUE is under test."""

    name = "plugin_garbage"

    @property
    def planned_governance(self):
        return "read_only"  # the right word, not a ToolGovernance value


class _Raising(_Undeclared):
    name = "plugin_raising"

    @property
    def planned_governance(self):
        raise RuntimeError("broken declaration")


def _registry(**tools):
    registry = ComponentRegistry()
    for name, tool in tools.items():
        registry.register("tool", name, tool)
    return registry


def _subtask(tool_name, arguments):
    return Subtask(id="s1", description="d", execution_method=ExecutionMethod.TOOL, tool_name=tool_name,
                   tool_arguments=arguments)


def _dispatch(tool, mode, tool_name, arguments, roots=()):
    subtask = _subtask(tool_name, arguments)
    kernel = strict_kernel(strict_config(autonomy={"mode": mode}))
    kernel.registry.get = MagicMock(return_value=tool)
    return asyncio.run(subtask_executor.execute(subtask=subtask, plan=EngineeringPlan(plan_id="p", kind=ChangeKind.TASK,
                                                                                      subtasks=[subtask]),
                                                context=build_context_package(), kernel=kernel, tool_roots=roots))


# -- the declaration and the resolution ---------------------------------------------------------------------------------

def test_every_shipped_tool_declares_and_the_resolution_projects_it():
    core = _core()
    expected = {
        "filesystem": (core.FilesystemTool(), ToolGovernance.READ_ONLY, None),
        "search": (core.SearchTool(), ToolGovernance.READ_ONLY, None),
        "ast": (core.ASTTool(), ToolGovernance.READ_ONLY, None),
        "git": (core.GitTool(), ToolGovernance.READ_ONLY, None),
        "shell": (core.ShellTool(), ToolGovernance.OPERATOR_ONLY, TOOL_OPERATOR_ONLY),
        "validate_refactor": (_validation_tool(), ToolGovernance.OPERATOR_ONLY, TOOL_OPERATOR_ONLY),
    }
    for name, (tool, governance, code) in expected.items():
        for mode in (*MODES, None):
            decision = resolve_planned_tool_governance(tool, autonomy_mode=mode, containment_backend="none")
            assert (decision.governance, decision.reason_code, decision.plannable) == (governance, code, code is None), name
            assert decision.autonomy_mode == mode and decision.containment_backend == "none"


def test_mcp_tools_are_governed_side_effects_and_stay_plannable():
    from kriya.mcp.mcp import MCPTool

    tool = MCPTool.__new__(MCPTool)
    for mode in (*MODES, None):
        decision = resolve_planned_tool_governance(tool, autonomy_mode=mode)
        assert decision.plannable and decision.governance is ToolGovernance.GOVERNED_SIDE_EFFECT


@pytest.mark.parametrize("tool, code", [
    (_Undeclared(), TOOL_MODEL_AUTHORITY_UNDECLARED),
    (_SelfDeclared(), TOOL_MODEL_AUTHORITY_UNDECLARED),
    (_shipped_subclass(), TOOL_MODEL_AUTHORITY_UNDECLARED),
    (_inheriting_subclass(), TOOL_MODEL_AUTHORITY_UNDECLARED),
    (object(), "TOOL_NOT_GOVERNED"),
], ids=["undeclared", "self-declared-plugin", "subclass-named-like-a-shipped-tool", "subclass-inheriting-the-declaration",
        "not-a-tool"])
def test_nothing_but_a_shipped_declaration_is_plannable(tool, code):
    for mode in (*MODES, None):
        decision = resolve_planned_tool_governance(tool, autonomy_mode=mode)
        assert not decision.plannable and decision.reason_code == code


@pytest.mark.parametrize("cls, code", [(_Garbage, TOOL_GOVERNANCE_INVALID), (_Raising, TOOL_GOVERNANCE_INVALID),
                                        (_DeclaresNone, TOOL_MODEL_AUTHORITY_UNDECLARED)],
                         ids=["garbage-value", "raising-declaration", "declares-none"])
def test_an_unreadable_garbage_or_empty_declaration_is_refused_even_when_admitted(monkeypatch, cls, code):
    admit_planned_test_tool(monkeypatch, cls)
    decision = resolve_planned_tool_governance(cls(), autonomy_mode=GUARDRAILS)
    assert not decision.plannable and decision.reason_code == code


def test_the_resolution_refuses_a_malformed_mode_typed():
    for value in ("x", "Guardrails", "", 1):
        with pytest.raises(AutonomyModeError):
            resolve_planned_tool_governance(_core().SearchTool(), autonomy_mode=value)


# -- consumer 1: the Planner catalogue (bypassing validation and the executor) -----------------------------------------

def _all_tools():
    core = _core()
    return dict(search=core.SearchTool(), shell=core.ShellTool(), validate_refactor=_validation_tool(),
                plugin_undeclared=_Undeclared(), plugin_self_declared=_SelfDeclared())


@pytest.mark.parametrize("mode", MODES)
def test_the_catalogue_projects_only_plannable_tools(mode):
    names = plannable_tool_names(_registry(**_all_tools()), strict_config(autonomy={"mode": mode}))
    assert names.registered == ("plugin_self_declared", "plugin_undeclared", "search", "shell", "validate_refactor")
    assert names.plannable == ("search",)


def test_the_planner_is_shown_only_plannable_tools(tmp_path):
    """The Planner prompt (stage_planning, the catalogue consumer) - checked on the prompt itself."""
    import test_work_unit_stages as stages

    from kriya.workflow.stage_planning import plan_work_unit

    engine = stages._engine(tmp_path)
    for name, tool in _all_tools().items():
        engine.kernel.registry.register("tool", name, tool)
    ctx = stages._ctx(tmp_path, predetermined_plan="Step 1: add mul", step_callback=lambda *a: None,
                      record_requirement_lineage=lambda *a: None, save_stage_checkpoint=lambda *a, **k: None)
    asyncio.run(plan_work_unit(ctx, stages._planning_deps(engine, stages._Planner([]))))
    assert "Subtask.tool_name must be exactly one of these, never invented): search." in ctx.plan_prompt
    for hidden in ("shell", "validate_refactor", "plugin_undeclared", "plugin_self_declared"):
        assert hidden not in ctx.plan_prompt.split("Registered Kriya tools available", 1)[1]


def test_a_registry_that_cannot_be_listed_admits_no_tool():
    broken = MagicMock(spec=ComponentRegistry)
    broken.list_components.side_effect = RuntimeError("registry unavailable")
    assert plannable_tool_names(broken, strict_config()) == subtask_executor.PlannedToolNames((), ())
    assert plannable_tool_names(None, strict_config()) == subtask_executor.PlannedToolNames((), ())


# -- consumer 2: plan validation (bypassing the catalogue and the executor) --------------------------------------------

def _tool_plan(tool_name):
    return EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, global_invariants=[GlobalInvariant(id="gi1", statement="x")],
                           subtasks=[Subtask(id="s1", description="d", execution_method=ExecutionMethod.TOOL,
                                             tool_name=tool_name, relevant_global_invariant_ids=["gi1"])])


@pytest.mark.parametrize("tool_name", ["shell", "validate_refactor", "plugin_undeclared", "plugin_self_declared"])
def test_plan_validation_refuses_a_registered_but_unplannable_tool(tmp_path, tool_name):
    """Even a plan the Planner was never shown it in (a stale or injected plan) is refused at validation."""
    from kriya.workflow.plan_validation import validate_plan

    names = plannable_tool_names(_registry(**_all_tools()), strict_config(autonomy={"mode": GUARDRAILS}))
    result = asyncio.run(validate_plan(_tool_plan(tool_name), workspace_path=str(tmp_path),
                                       available_tool_names=names.registered, plannable_tool_names=names.plannable))
    assert not result.valid and "PLANNED_TOOL_NOT_PLANNABLE" in result.reason_codes
    assert "UNREGISTERED_TOOL_NAME" not in result.reason_codes
    accepted = asyncio.run(validate_plan(_tool_plan("search"), workspace_path=str(tmp_path),
                                         available_tool_names=names.registered, plannable_tool_names=names.plannable))
    assert "PLANNED_TOOL_NOT_PLANNABLE" not in accepted.reason_codes and "UNREGISTERED_TOOL_NAME" not in accepted.reason_codes


def test_a_listing_failure_fails_validation_closed(tmp_path):
    from kriya.workflow.plan_validation import validate_plan

    broken = MagicMock(spec=ComponentRegistry)
    broken.list_components.side_effect = RuntimeError("registry unavailable")
    names = plannable_tool_names(broken, strict_config())
    result = asyncio.run(validate_plan(_tool_plan("search"), workspace_path=str(tmp_path),
                                       available_tool_names=names.registered, plannable_tool_names=names.plannable))
    assert not result.valid and "UNREGISTERED_TOOL_NAME" in result.reason_codes


def test_the_membership_check_reports_the_new_code_only_for_registered_tools():
    result = validate_tool_capability_membership(_tool_plan("shell"), available_tool_names=["shell", "search"],
                                                 plannable_tool_names=["search"])
    assert result.reason_codes == ["PLANNED_TOOL_NOT_PLANNABLE"] and not result.valid
    unknown = validate_tool_capability_membership(_tool_plan("nope"), available_tool_names=["search"],
                                                  plannable_tool_names=["search"])
    assert unknown.reason_codes == ["UNREGISTERED_TOOL_NAME"]


def test_the_enforce_and_shadow_runs_validate_against_the_one_catalogue():
    """Their catalogue snapshot comes from the one helper (tests/architecture/test_direct_access_owners.py also refuses
    any new registry listing outside it); the planned names reach every validate_plan (tripwire below)."""
    from kriya.workflow import enforce_loop, shadow_mode

    for module in (enforce_loop, shadow_mode):
        source = Path(module.__file__).read_text()
        assert "plannable_tool_names(" in source and 'list_components("tool")' not in source


# -- consumer 3: the executor recheck (bypassing the catalogue and validation) -----------------------------------------

@pytest.mark.parametrize("tool, code", [
    (_Undeclared(), TOOL_MODEL_AUTHORITY_UNDECLARED),
    (_SelfDeclared(), TOOL_MODEL_AUTHORITY_UNDECLARED),
    (_shipped_subclass(), TOOL_MODEL_AUTHORITY_UNDECLARED),
    (_inheriting_subclass(), TOOL_MODEL_AUTHORITY_UNDECLARED),
], ids=["undeclared", "self-declared-plugin", "subclass-named-like-a-shipped-tool", "subclass-inheriting"])
@pytest.mark.parametrize("mode", MODES)
def test_the_executor_refuses_an_unplannable_tool_before_it_runs(tmp_path, tool, code, mode):
    result = _dispatch(tool, mode, tool.name, {"note": "n"} if not isinstance(tool, _core().SearchTool) else
                       {"pattern": "x"}, (str(tmp_path),))
    assert result.status == SubtaskStatus.NEEDS_REVIEW and result.reason_codes == (code,)
    if hasattr(tool, "calls"):
        tool.calls.assert_not_called()


def test_the_executor_refuses_a_garbage_declaration(monkeypatch):
    admit_planned_test_tool(monkeypatch, _Garbage)
    tool = _Garbage()
    result = _dispatch(tool, GUARDRAILS, tool.name, {"note": "n"})
    assert result.reason_codes == (TOOL_GOVERNANCE_INVALID,)
    tool.calls.assert_not_called()


def test_the_model_authored_entry_rechecks_on_its_own():
    """No executor, no catalogue, no validation: the governed entry itself refuses."""
    for tool, code in ((_Undeclared(), TOOL_MODEL_AUTHORITY_UNDECLARED), (_core().ShellTool(), TOOL_OPERATOR_ONLY)):
        for mode in (*MODES, None):
            with pytest.raises(ModelInvocationRefused) as refused:
                asyncio.run(execute_model_authored(tool, {"note": "n"} if code != TOOL_OPERATOR_ONLY else
                                                   {"command": "true"}, ModelToolInvocation(roots=(), autonomy_mode=mode)))
            assert refused.value.reason_code == code


# -- shell and validate_refactor: refused in both modes, nothing runs ----------------------------------------------------

def test_planned_shell_and_validate_refactor_are_refused_in_both_modes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "ran"
    run_spy = AsyncMock(return_value="ran")
    with patch.object(type(_core().ShellTool()), "_run", run_spy), \
         patch.object(type(_validation_tool()), "_run", run_spy):
        for mode in MODES:
            shell = _dispatch(_core().ShellTool(), mode, "shell", {"command": f"touch {marker}"}, (str(tmp_path),))
            # human-in-the-loop: B3 stays first (TOOL_CONFIRMATION_UNSUPPORTED), unchanged evidence
            assert shell.reason_codes == (("TOOL_CONFIRMATION_UNSUPPORTED",) if mode == HITL else (TOOL_OPERATOR_ONLY,))
            validate = _dispatch(_validation_tool(), mode, "validate_refactor", {"files": [], "run_tests": True},
                                 (str(tmp_path),))
            assert validate.reason_codes == (TOOL_OPERATOR_ONLY,)
            assert shell.status == validate.status == SubtaskStatus.NEEDS_REVIEW
    run_spy.assert_not_called()
    assert not marker.exists()


def test_the_tools_own_wall_refuses_a_direct_authorize_too():
    for tool, args in ((_core().ShellTool(), {"command": "true"}), (_validation_tool(), {"files": []})):
        validated = tool.arguments_schema(**args)
        for mode in (*MODES, None):
            with pytest.raises(ModelInvocationRefused) as refused:
                tool.authorize_model_invocation(validated, ModelToolInvocation(roots=(), autonomy_mode=mode))
            assert refused.value.reason_code == TOOL_OPERATOR_ONLY


# -- the operator path is unchanged -------------------------------------------------------------------------------------

def test_the_operator_path_is_unchanged_for_shell_validate_git_and_filesystem(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.chdir(tmp_path)
    for args in (("init", "-q"),):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    out = asyncio.run(_core().ShellTool().execute(command="echo operator"))
    assert out["exit_code"] == 0 and out["stdout"].strip() == "operator"
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               lambda self, files: {"success": True, "output": "compiled"}):
        assert asyncio.run(_validation_tool().execute(files=["a.py"], run_tests=False)).startswith("VALIDATION SUCCESS")
    assert "a.py" in asyncio.run(_core().GitTool().execute(subcommand="status"))
    target = tmp_path / "elsewhere" / "op.txt"
    asyncio.run(_core().FilesystemTool().execute(operation="write", path=str(target), content="op"))
    assert target.read_text() == "op"


# -- git reads: the root is explicit (OI-129 item 3), behaviour preserved ------------------------------------------------

def test_a_planned_git_read_runs_in_the_supplied_owner_root_not_the_process_folder(tmp_path, monkeypatch):
    import subprocess

    repo, elsewhere = tmp_path / "repo", tmp_path / "elsewhere"
    repo.mkdir()
    elsewhere.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "only_in_repo.txt").write_text("x")
    monkeypatch.chdir(elsewhere)  # the process folder is NOT a repository
    result = _dispatch(_core().GitTool(), GUARDRAILS, "git", {"subcommand": "status"}, (str(repo),))
    assert result.status == SubtaskStatus.COMPLETED and "only_in_repo.txt" in result.tool_output


# -- structural tripwires (each with a planted control) -----------------------------------------------------------------

def _calls_missing_plannable(sources):
    """Production calls passing available_tool_names= without plannable_tool_names= (the repair-prompt builder, which
    receives the names to SHOW, is exempt)."""
    found = []
    for name, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call):
                keywords = {k.arg for k in node.keywords}
                callee = ast.unparse(node.func)
                if "available_tool_names" in keywords and "plannable_tool_names" not in keywords \
                        and not callee.endswith("build_structured_plan_repair_prompt"):
                    found.append((name, node.lineno, callee))
    return found


def test_the_tripwire_sees_a_planted_validation_without_the_plannable_names():
    planted = {"p.py": "async def f(plan):\n    await validate_plan(plan, available_tool_names=names)\n"
                       "    build_structured_plan_repair_prompt(available_tool_names=names)\n"}
    assert [c for _n, _l, c in _calls_missing_plannable(planted)] == ["validate_plan"]


def test_every_production_validation_passes_the_plannable_names():
    sources = {str(p.relative_to(REPO_ROOT)): p.read_text() for p in sorted((REPO_ROOT / "kriya").rglob("*.py"))}
    assert _calls_missing_plannable(sources) == []


# -- OI-135: every autonomy-mode consumer reads through the fail-closed reader -------------------------------------------

def _bypassed(kind):
    """A real AppConfig whose mode bypassed validation: object.__setattr__ on the section, or a model_construct'd section."""
    cfg = AppConfig()
    if kind == "setattr":
        object.__setattr__(cfg.autonomy, "mode", "human-in-the-lop")
    elif kind == "model_construct":
        object.__setattr__(cfg, "autonomy", AutonomyConfig.model_construct(mode="autonomous"))
    else:
        object.__setattr__(cfg.autonomy, "mode", 1)  # not even a string
    return cfg


BYPASSES = ("setattr", "model_construct", "non_string")


def test_the_reader_itself():
    from kriya.config.authority import autonomy_mode_of, is_human_in_the_loop

    assert autonomy_mode_of(AppConfig()) == HITL and is_human_in_the_loop(HITL) and not is_human_in_the_loop(GUARDRAILS)
    for kind in BYPASSES:
        with pytest.raises(AutonomyModeError):
            autonomy_mode_of(_bypassed(kind))
    with pytest.raises(AutonomyModeError):
        autonomy_mode_of(SimpleNamespace())  # no readable mode at all


@pytest.mark.parametrize("kind", BYPASSES)
def test_the_executor_stops_typed_on_an_unvalidated_mode(tmp_path, kind):
    tool = _core().SearchTool()
    kernel = strict_kernel(_bypassed(kind))
    kernel.registry.get = MagicMock(return_value=tool)
    subtask = _subtask("search", {"pattern": "x"})
    result = asyncio.run(subtask_executor.execute(subtask=subtask, plan=EngineeringPlan(plan_id="p", kind=ChangeKind.TASK,
                                                                                        subtasks=[subtask]),
                                                  context=build_context_package(), kernel=kernel,
                                                  tool_roots=(str(tmp_path),)))
    assert result.status == SubtaskStatus.NEEDS_REVIEW and result.reason_codes[0].startswith("AUTONOMY_MODE_")
    assert result.tool_output is None


@pytest.mark.parametrize("kind", BYPASSES)
def test_the_catalogue_stops_typed_on_an_unvalidated_mode(kind):
    with pytest.raises(AutonomyModeError):
        plannable_tool_names(_registry(search=_core().SearchTool()), _bypassed(kind))


def test_the_invocation_mode_reading_is_fail_closed():
    assert ModelToolInvocation(autonomy_mode=None).reviews_every_change  # no configuration: the strictest reading
    with pytest.raises(AutonomyModeError):
        ModelToolInvocation(autonomy_mode="human-in-the-lop").reviews_every_change  # noqa: B018 - the read IS the test


@pytest.mark.parametrize("kind", BYPASSES)
def test_the_source_review_deferral_stops_typed(kind):
    """policy.approval.defer_execution_until_source_review (Gap B; via attempt and owner_recovery): a typo used to read
    as 'not human-in-the-loop' = execute the candidate before the person reviewed it."""
    from kriya.policy.approval import defer_execution_until_source_review

    with pytest.raises(AutonomyModeError):
        defer_execution_until_source_review(True, _bypassed(kind).autonomy.mode, True)


@pytest.mark.parametrize("kind", BYPASSES)
def test_runtime_command_approval_stops_typed(kind):
    """runtime_verification._runtime_commands_need_approval: a typo used to read as 'no approval needed'."""
    from kriya.workflow import attempt

    ctx = SimpleNamespace(worktree_path="/w", current_subtask_id="s", approval_callback=None, goal="g", grounding_goal="g",
                          exit_authority_goal="g")
    judgment = {"should_run": True, "run_commands": [["mvn", "test"]], "success_criteria": "x", "input_channel": "none",
                "command_source": "inferred"}
    with pytest.raises(AutonomyModeError):
        attempt._runtime_commands_need_approval(ctx, SimpleNamespace(run_verification_approved_fingerprint=None), judgment,
                                                _bypassed(kind).autonomy.mode)


@pytest.mark.parametrize("kind", BYPASSES)
def test_the_managed_service_block_stops_typed(kind):
    """runtime_verification._managed_service_blocked_without_approval_subject: a typo used to let the service start."""
    from kriya.workflow import runtime_verification

    ctx = SimpleNamespace(kernel=SimpleNamespace(config=_bypassed(kind)))
    with pytest.raises(AutonomyModeError):
        runtime_verification._managed_service_blocked_without_approval_subject(None, None, ctx)


@pytest.mark.parametrize("kind", BYPASSES)
def test_the_contract_escalation_stops_typed(tmp_path, kind):
    """spec_compliance contract-change escalation (PRD-023): decided on the mode only after the reader."""
    import test_prd023_contract_classification as prd023

    for path, content in prd023.ORIGINAL.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content)
    callback = AsyncMock(return_value=True)
    ctx = prd023._ctx(tmp_path, escalation="human", mode=GUARDRAILS, callback=callback)
    bad = _bypassed(kind)
    object.__setattr__(bad.autonomy, "contract_change_escalation", "human")
    object.__setattr__(ctx.kernel, "config", bad)
    with pytest.raises(AutonomyModeError):
        asyncio.run(prd023._escalate(tmp_path, ctx))
    callback.assert_not_called()


@pytest.mark.parametrize("kind", BYPASSES)
def test_the_pre_apply_approval_gate_stops_typed_and_applies_nothing(tmp_path, kind):
    """stage_apply.approval_gate: a typo used to read as guardrails = apply without the person's review."""
    import test_work_unit_stages as stages

    from kriya.workflow.stage_apply import approval_gate

    engine = stages._reviewing_engine(tmp_path, mode=GUARDRAILS)
    bad = _bypassed(kind)
    object.__setattr__(engine.kernel.config, "autonomy", bad.autonomy)
    asked = []
    ctx = stages._apply_ctx(tmp_path, approval_callback=lambda diffs, reason: asked.append(reason) or True)
    with pytest.raises(AutonomyModeError):
        asyncio.run(approval_gate(ctx, stages._late_deps(engine), attempt_ctx=stages._NO_HELD_GATES))
    assert asked == []
    assert (Path(ctx.workspace_path) / "calc.py").read_text() == stages.CALC_BASE


@pytest.mark.parametrize("kind", BYPASSES)
def test_the_desktop_interaction_shows_unknown_never_a_mode(kind):
    from kriya.application.run import _interaction

    shown = _interaction(_bypassed(kind))
    assert shown["approvals"] == "unknown" and shown["autonomy_mode"] is None


def test_the_valid_modes_keep_every_consumer_unchanged():
    from kriya.application.run import _interaction
    from kriya.policy.approval import defer_execution_until_source_review

    assert defer_execution_until_source_review(True, HITL, True) and not defer_execution_until_source_review(True, GUARDRAILS, True)
    assert _interaction(AppConfig())["approvals"] == "change_review"
    cfg = AppConfig()
    cfg.autonomy.mode = GUARDRAILS
    assert _interaction(cfg)["approvals"] == "configured_mode"


def test_no_production_module_compares_the_mode_with_a_literal(tmp_path, monkeypatch):
    """Planted control for the S16a scan (which now requires zero literal sites): pointed at a planted tree, the finder
    sees a planted compare; pointed at the real tree it finds none."""
    import test_s16a_autonomy_mode_vocabulary as s16a

    (tmp_path / "kriya").mkdir()
    (tmp_path / "kriya" / "planted.py").write_text('def f(cfg):\n    return cfg.autonomy.mode == "human-in-the-loop"\n')
    with monkeypatch.context() as m:
        m.setattr(s16a, "REPO_ROOT", str(tmp_path))
        assert [s[0] for s in s16a._comparison_sites()] == [os.path.join("kriya", "planted.py")]
    assert s16a._comparison_sites() == []


def test_a_registry_listing_failure_in_a_real_enforce_run_admits_no_tool(tmp_path, monkeypatch):
    """The enforce loop's catalogue on a registry that cannot be listed: no tool plannable, the TOOL plan is refused at
    validation (UNREGISTERED_TOOL_NAME, never skipped) and the plannable search tool is never dispatched."""
    import _t6_harness
    import test_enforce_verified_no_change as shape
    from _chaos_harness import chaos_config

    from kriya.workflow.plan_schema import EngineeringPlan as Plan
    from kriya.workflow.planner_repair import STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS

    real_list = ComponentRegistry.list_components

    def broken(self, category):
        if category == "tool":
            raise RuntimeError("registry unavailable")
        return real_list(self, category)

    monkeypatch.setattr(ComponentRegistry, "list_components", broken)
    search = _core().SearchTool()
    search_runs = AsyncMock(return_value="ran")
    monkeypatch.setattr(type(search), "_run_model_authored", lambda self, args, invocation: search_runs())

    def plan():
        document = shape._plan(shape.TOOL_CRITERION).model_dump()
        document["subtasks"].insert(0, {"id": "s0", "description": "search", "execution_method": "tool",
                                        "tool_name": "search", "tool_arguments": {"pattern": "x"}, "provides": ["done"],
                                        "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": [],
                                        "verification": []})
        return Plan.model_validate(document)

    cfg = chaos_config()
    observed = _t6_harness.enforce_run(tmp_path, monkeypatch, _t6_harness.shop_responder, _t6_harness.shop_files(),
                                       shape.GOAL, [plan] * (1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS), cfg=cfg,
                                       tools={"search": search})
    legacy = observed.result.legacy_result
    assert legacy["reason_codes"][0] == "UNREGISTERED_TOOL_NAME", legacy
    assert [r for r in observed.result.subtask_results if r.subtask_id == "s0"] == []
    search_runs.assert_not_called()


def test_an_unvalidated_section_handed_to_appconfig_is_revalidated():
    """OI-135 part 1: AppConfig(autonomy=<model_construct'd section>) used to keep the invalid mode (instances were not
    revalidated); AutonomyConfig now revalidates instances (MEASURED cost +5.7 us per such construction)."""
    with pytest.raises(AutonomyModeError):
        AppConfig(autonomy=AutonomyConfig.model_construct(mode="autonomous"))
    assert AppConfig(autonomy=AutonomyConfig(mode=GUARDRAILS)).autonomy.mode == GUARDRAILS

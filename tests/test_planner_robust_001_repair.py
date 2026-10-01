"""PLANNER-ROBUST-001 (2026-09-19): closes the real defect exposed live by
VAL-001 Graphify G1 (planner_output_schema_invalid on subtask s3 -
execution_method=tool with no tool_name, only a nested verification[]
entry's own tool_name set - a Planner response the legacy pipeline had no
way to recover from before this package), AND its closure-review residual:
converging Planner-output SEMANTIC validation across the legacy
run_generation_workflow() path and the WorkflowController/enforce path so
neither can define "a valid plan" differently.

Four parts, all exercised below:
  P1 - PlannerAgent.system_prompt distinguishes Subtask.tool_name from
       verification[].tool_name, with two worked examples
       (kriya/agents/agent.py).
  P2 - The Planner-visible tool catalog (kernel.registry.list_components
       ("tool")) is exposed in the plan-building prompt when tools are
       registered (kriya/workflow/workflow.py).
  P3 - Bounded structured-plan repair (kriya/workflow/planner_repair.py)
       runs in BOTH the legacy path and WorkflowController's enforce path,
       sharing one primitive.
  P4 (closure) - ONE canonical tool-capability MEMBERSHIP check
       (kriya/workflow/planner_validation.py::validate_tool_capability_
       membership) is now consulted by BOTH plan_validation.py::
       validate_plan() (WorkflowController) and file_resolution.py::
       classify_plan_completeness() (legacy) - "ONE PLAN CONTRACT -> ONE
       VALIDATION SEMANTICS -> MULTIPLE ORCHESTRATORS."

Test items A-T below map directly onto the closure task's own required-
coverage list; each test's docstring names its letter(s)."""

import json
from unittest.mock import AsyncMock

import pytest

from kriya.agents.agent import PlannerAgent
from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.file_resolution import classify_plan_completeness
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    GlobalInvariant,
    Subtask,
)
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.planner_repair import (
    STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS,
    classify_structured_plan_parse_issue,
)
from kriya.workflow.planner_validation import (
    validate_tool_capability_membership,
)
from kriya.workflow.state import GenerationState
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow import WorkflowEngine

# =====================================================================
# Fixtures
# =====================================================================

def _tool_subtask_missing_toolname_plan(
    *, subtask_tool_name: str = None, planned_file_path: str = "pkg/module.py",
) -> str:
    """The real G1 defect shape: subtask s3, execution_method=tool,
    execution_role=verification, a nested verification[] entry's OWN
    tool_name set ("test"), Subtask.tool_name controlled by the caller -
    None reproduces the live defect; a real name repairs it (if
    registered); an invented name reproduces the CLOSURE's own residual
    (an unregistered but non-blank name)."""
    verification_subtask = {
        "id": "s3", "description": "verify the fix", "execution_method": "tool",
        "execution_role": "verification", "depends_on": ["s1"],
        "planned_files": [], "provides": [], "requires": ["fix applied"],
        "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
        "verification": [{"type": "tool", "description": "run tests", "tool_name": "test"}],
    }
    if subtask_tool_name is not None:
        verification_subtask["tool_name"] = subtask_tool_name
    structured = {
        "global_invariants": [{"id": "gi1", "statement": "keep the fix minimal"}],
        "subtasks": [
            {
                "id": "s1", "description": "implement the fix", "execution_method": "model",
                "execution_role": "implementation", "depends_on": [],
                "planned_files": [{"path": planned_file_path, "action": "modify"}],
                "provides": ["fix applied"], "requires": [],
                "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
                "verification": [],
            },
            verification_subtask,
        ],
        "acceptance_criteria": [{"id": "ac1", "description": "tests pass", "method": "judgment"}],
        "extension_points": [], "refactor_baseline": None,
    }
    return "Fix plan\n```json\n" + json.dumps(structured) + "\n```"


def _multi_tool_subtask_plan(tool_names) -> str:
    """A plan with one TOOL subtask per name in tool_names (all schema-
    valid, no missing-tool_name defect) - used to prove multi-tool
    membership correctness and "no per-subtask registry scan"."""
    subtasks = [{
        "id": "s0", "description": "implement", "execution_method": "model",
        "execution_role": "implementation", "depends_on": [],
        "planned_files": [{"path": "pkg/module.py", "action": "modify"}],
        "provides": ["fix applied"], "requires": [],
        "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
        "verification": [],
    }]
    for i, name in enumerate(tool_names):
        subtasks.append({
            "id": f"s{i + 1}", "description": f"run {name}", "execution_method": "tool",
            "tool_name": name, "execution_role": "verification", "depends_on": ["s0"],
            "planned_files": [], "provides": [], "requires": ["fix applied"],
            "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
            "verification": [{"type": "tool", "description": "run", "tool_name": name}],
        })
    structured = {
        "global_invariants": [{"id": "gi1", "statement": "keep the fix minimal"}],
        "subtasks": subtasks,
        "acceptance_criteria": [{"id": "ac1", "description": "tests pass", "method": "judgment"}],
        "extension_points": [], "refactor_baseline": None,
    }
    return "Fix plan\n```json\n" + json.dumps(structured) + "\n```"


def _wrap_planner_run(we: WorkflowEngine) -> AsyncMock:
    """Wraps we.planner.run with a call-counting AsyncMock that still
    executes the real method - decouples "how many times was the Planner
    (re)invoked" from total pipeline llm.complete call volume."""
    original = we.planner.run
    we.planner.run = AsyncMock(wraps=original)
    return we.planner.run


def _capture_generation_state():
    """Returns (patch_context_manager, dict) - patches GenerationState.
    __init__ to also stash the real instance, so a test can inspect
    state.run_events after run_generation_workflow() returns."""
    from unittest.mock import patch as _patch

    captured = {}
    original_init = GenerationState.__init__

    def capturing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured["state"] = self

    return _patch.object(GenerationState, "__init__", capturing_init), captured


# =====================================================================
# P1 (prompt contract): PlannerAgent.system_prompt distinguishes
# Subtask.tool_name from verification[].tool_name, with worked examples.
# =====================================================================

def test_planner_prompt_distinguishes_subtask_tool_name_from_verification_tool_name():
    prompt = PlannerAgent("planner", None).system_prompt
    assert "Subtask.tool_name" in prompt or "subtask's own tool_name" in prompt
    assert "verification[]" in prompt or "verification entry" in prompt
    assert "INVALID" in prompt
    assert "most common mistake" in prompt.lower()


def test_worked_example_a_is_verification_only_using_model_execution():
    prompt = PlannerAgent("planner", None).system_prompt
    assert "Worked example A" in prompt
    idx_a = prompt.index("Worked example A")
    idx_b = prompt.index("Worked example B")
    example_a = prompt[idx_a:idx_b]
    assert '"execution_method": "model"' in example_a
    assert '"execution_role": "verification"' in example_a
    assert '"type": "tool"' in example_a
    assert '"tool_name": "test"' in example_a


def test_worked_example_b_is_genuinely_tool_executed_with_top_level_tool_name():
    prompt = PlannerAgent("planner", None).system_prompt
    idx_b = prompt.index("Worked example B")
    example_b = prompt[idx_b:]
    assert '"execution_method": "tool"' in example_b
    assert '"tool_name": "validate_refactor"' in example_b


# =====================================================================
# D - a subtask with execution_method=tool, tool_name unset, but a fully-
# populated verification[].tool_name is still rejected - the nested field
# never satisfies the top-level requirement (Pydantic-level invariant).
# =====================================================================

def test_d_verification_tool_name_alone_never_satisfies_subtask_tool_name_requirement():
    with pytest.raises(ValueError, match="execution_method=tool but no tool_name"):
        Subtask(
            id="s3", description="verify", execution_method=ExecutionMethod.TOOL,
            execution_role=ExecutionRole.VERIFICATION,
            verification=[{"type": "tool", "description": "run tests", "tool_name": "test"}],
        )


# =====================================================================
# A/B - a valid model subtask with nested verification tool is accepted;
# a valid registered top-level tool subtask is accepted by BOTH paths.
# =====================================================================

@pytest.mark.asyncio
async def test_a_valid_model_subtask_with_nested_verification_tool_is_accepted(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        _tool_subtask_missing_toolname_plan(subtask_tool_name="validate_refactor"),
        "Design: modify pkg/module.py", "OK",
    ])
    kernel.registry.register("tool", "validate_refactor", object())
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res.get("status") != "planner_output_schema_invalid"
    assert planner_run.call_count == 1


@pytest.mark.asyncio
async def test_b_valid_registered_top_level_tool_subtask_accepted_by_legacy_path(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    kernel.registry.register("tool", "validate_refactor", object())
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        _tool_subtask_missing_toolname_plan(subtask_tool_name="validate_refactor"),
        "Design: modify pkg/module.py", "OK",
    ])
    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res.get("status") != "planner_output_schema_invalid"


def test_b_valid_registered_top_level_tool_subtask_accepted_by_workflow_controller_path():
    plan = EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="x")],
        subtasks=[
            Subtask(
                id="s1", description="verify", execution_method=ExecutionMethod.TOOL,
                tool_name="validate_refactor", relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    result = validate_tool_capability_membership(
        plan, available_tool_names=["validate_refactor", "filesystem"],
    )
    assert result.valid
    assert result.errors == []


# =====================================================================
# C - missing top-level tool_name rejected by BOTH paths.
# =====================================================================

def test_c_missing_top_level_tool_name_is_strictly_rejected_on_first_pass():
    """C/(the exact G1 shape): execution_method=tool, execution_role=
    verification, nested verification.tool_name present, Subtask.
    tool_name absent - classified schema_invalid before any repair."""
    result = classify_plan_completeness(_tool_subtask_missing_toolname_plan(subtask_tool_name=None))
    assert result.classification == "schema_invalid"
    reason_codes = classify_structured_plan_parse_issue(result.reason, result.reason_codes)
    assert reason_codes == ["TOOL_SUBTASK_MISSING_TOOL_NAME"]


# =====================================================================
# E - invented/unregistered top-level tool_name on the FIRST Planner
# response is rejected by BOTH paths (the closure's own headline fix -
# this used to be a disclosed, accepted gap in the legacy path).
# =====================================================================

@pytest.mark.asyncio
async def test_e_invented_tool_name_on_first_response_rejected_by_legacy_path(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)  # nothing registered - any name is "invented"
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(
        return_value=_tool_subtask_missing_toolname_plan(subtask_tool_name="totally_made_up_tool_xyz"),
    )
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res["status"] == "planner_output_schema_invalid"
    assert planner_run.call_count == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_e_invented_tool_name_on_first_response_rejected_by_workflow_controller_path(tmp_path):
    plan_invented = EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="x")],
        subtasks=[
            Subtask(
                id="s1", description="verify", execution_method=ExecutionMethod.TOOL,
                tool_name="totally_made_up_tool_xyz", relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    result_invented = await validate_plan(
        plan_invented, workspace_path=str(tmp_path), available_tool_names=["validate_refactor", "filesystem"],
    )
    assert not result_invented.valid
    assert any("unregistered tool_name" in e for e in result_invented.errors)
    assert "UNREGISTERED_TOOL_NAME" in result_invented.reason_codes

    plan_registered = plan_invented.model_copy(deep=True)
    plan_registered.subtasks[0].tool_name = "validate_refactor"
    result_registered = await validate_plan(
        plan_registered, workspace_path=str(tmp_path), available_tool_names=["validate_refactor", "filesystem"],
    )
    assert not any("unregistered tool_name" in e for e in result_registered.errors)


# =====================================================================
# F - invented/unregistered top-level tool_name on a REPAIRED response is
# ALSO rejected by both paths - no special weaker validation path for a
# repaired plan (P5). This is the closure task's own decisive test:
# before this package the legacy path structurally could not catch this
# at all (see project memory for the disclosed pre-closure gap) - now the
# repaired response re-enters classify_plan_completeness() with the SAME
# captured available_tool_names snapshot, catches the invention, and
# (since it's still schema_invalid) consumes the SECOND bounded repair
# attempt before terminating truthfully.
# =====================================================================

@pytest.mark.asyncio
async def test_f_invented_tool_name_on_repaired_response_is_rejected_not_silently_accepted(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)  # nothing registered
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        _tool_subtask_missing_toolname_plan(subtask_tool_name=None),
        _tool_subtask_missing_toolname_plan(subtask_tool_name="totally_made_up_tool_xyz"),
        _tool_subtask_missing_toolname_plan(subtask_tool_name="still_made_up"),
    ])
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res["status"] == "planner_output_schema_invalid"
    assert res.get("status") != "complete"
    assert planner_run.call_count == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS


# =====================================================================
# G/H - a tool registered through the runtime registry is automatically
# advertised in the Planner prompt AND accepted by validation, with zero
# workflow-source changes required; an absent/unregistered tool is never
# advertised and is rejected (P7 plugin-extensibility proof).
# =====================================================================

@pytest.mark.asyncio
async def test_g_runtime_registered_tool_automatically_advertised_and_accepted(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    kernel.registry.register("tool", "widget_tool", object())  # simulates a new plugin registering
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        _tool_subtask_missing_toolname_plan(subtask_tool_name="widget_tool"),
        "Design: modify pkg/module.py", "OK",
    ])
    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    planner_prompt = llm.complete.call_args_list[0][0][1]
    assert "Registered Kriya tools available" in planner_prompt
    assert "widget_tool" in planner_prompt
    assert res.get("status") != "planner_output_schema_invalid"


@pytest.mark.asyncio
async def test_h_absent_tool_never_advertised_and_rejected(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)  # widget_tool never registered
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(
        return_value=_tool_subtask_missing_toolname_plan(subtask_tool_name="widget_tool"),
    )
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    planner_prompt = llm.complete.call_args_list[0][0][1]
    assert "Registered Kriya tools available" not in planner_prompt
    assert "widget_tool" not in planner_prompt
    assert res["status"] == "planner_output_schema_invalid"
    assert planner_run.call_count == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS


# =====================================================================
# I - registry tool catalog obtained once per planning operation, not
# once per subtask (P8 performance requirement).
# =====================================================================

@pytest.mark.asyncio
async def test_i_registry_catalog_read_once_per_planning_operation(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    kernel.registry.register("tool", "tool_a", object())
    kernel.registry.register("tool", "tool_b", object())
    original_list_components = kernel.registry.list_components
    call_log = []

    def counting_list_components(category):
        call_log.append(category)
        return original_list_components(category)

    kernel.registry.list_components = counting_list_components
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        _multi_tool_subtask_plan(["tool_a", "tool_b"]),
        "Design: modify pkg/module.py", "OK",
    ])
    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res.get("status") != "planner_output_schema_invalid"
    tool_reads = [c for c in call_log if c == "tool"]
    assert len(tool_reads) == 1, f"expected exactly one registry read for a 2-tool-subtask plan, got {tool_reads}"


# =====================================================================
# J - valid first plan -> no repair.
# =====================================================================

@pytest.mark.asyncio
async def test_j_valid_first_plan_incurs_zero_repair_calls(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    kernel.registry.register("tool", "validate_refactor", object())
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        _tool_subtask_missing_toolname_plan(subtask_tool_name="validate_refactor"),
        "Design: modify pkg/module.py", "OK",
    ])
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res.get("status") != "planner_output_schema_invalid"
    assert planner_run.call_count == 1


# =====================================================================
# K - repairable malformed plan -> bounded repair -> same canonical
# validation (the original G1 recovery scenario).
# =====================================================================

@pytest.mark.asyncio
async def test_k_g1_shape_recovers_via_bounded_repair_and_proceeds_to_architect(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    kernel.registry.register("tool", "validate_refactor", object())
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        _tool_subtask_missing_toolname_plan(subtask_tool_name=None),
        _tool_subtask_missing_toolname_plan(subtask_tool_name="validate_refactor"),
        "Design: modify pkg/module.py", "OK",
    ])
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)
    patcher, captured = _capture_generation_state()

    with patcher:
        res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res.get("status") not in (
        "planner_output_schema_invalid", "planner_output_incomplete", "planner_output_unauthorized_path",
    )
    assert planner_run.call_count == 2, "initial call + exactly one repair call"
    state = captured["state"]
    kinds = [e.kind for e in state.run_events]
    assert "structured_plan_repair_requested" in kinds
    assert "structured_plan_repair_result" in kinds
    requested = [e for e in state.run_events if e.kind == "structured_plan_repair_requested"]
    assert requested[0].details["repair_attempt"] == 1
    assert "TOOL_SUBTASK_MISSING_TOOL_NAME" in requested[0].details["reason_codes"]


@pytest.mark.asyncio
async def test_k_repeated_repair_failure_is_bounded_and_terminates_truthfully(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=_tool_subtask_missing_toolname_plan(subtask_tool_name=None))
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res["status"] == "planner_output_schema_invalid"
    assert planner_run.call_count == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS


# =====================================================================
# L - unauthorized path remains hard-terminal/non-repairable.
# =====================================================================

@pytest.mark.asyncio
async def test_l_repaired_response_introducing_unauthorized_path_is_rejected_not_repaired_again(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    kernel.registry.register("tool", "validate_refactor", object())
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        _tool_subtask_missing_toolname_plan(subtask_tool_name=None, planned_file_path="pkg/module.py"),
        _tool_subtask_missing_toolname_plan(
            subtask_tool_name="validate_refactor", planned_file_path="/abs/workspace/pkg/module.py",
        ),
    ])
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res["status"] == "planner_output_unauthorized_path"
    assert planner_run.call_count == 2, "one initial call + exactly one repair call, no further retry"


@pytest.mark.asyncio
async def test_l_repaired_response_widening_via_path_traversal_is_also_rejected(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    kernel.registry.register("tool", "validate_refactor", object())
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        _tool_subtask_missing_toolname_plan(subtask_tool_name=None, planned_file_path="pkg/module.py"),
        _tool_subtask_missing_toolname_plan(
            subtask_tool_name="validate_refactor", planned_file_path="../../etc/passwd",
        ),
    ])
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res["status"] == "planner_output_unauthorized_path"
    assert planner_run.call_count == 2


# =====================================================================
# M - shared validator produces equivalent outcome/reason for the same
# input under legacy and WorkflowController paths.
# =====================================================================

def test_m_legacy_and_controller_paths_share_the_same_repair_primitives():
    import kriya.workflow.workflow as workflow_module
    import kriya.workflow.workflow_controller as workflow_controller_module

    assert (
        workflow_module.classify_structured_plan_parse_issue
        is workflow_controller_module.classify_structured_plan_parse_issue
    )
    assert (
        workflow_module.build_structured_plan_repair_prompt
        is workflow_controller_module.build_structured_plan_repair_prompt
    )
    assert workflow_module.STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS == 2


def test_m_legacy_and_controller_paths_share_the_same_tool_capability_validator():
    """M: both plan_validation.py::validate_plan() and file_resolution.py
    ::classify_plan_completeness() delegate to the SAME shared function -
    proven by identity, not merely "produces the same result today"."""
    import kriya.workflow.plan_validation as plan_validation_module

    # Deferred import inside classify_plan_completeness means this module-
    # level identity check goes through the shared module directly rather
    # than an attribute of file_resolution_module (which never imports it
    # at top level) - plan_validation_module DOES import it at top level.
    from kriya.workflow.planner_validation import validate_tool_capability_membership as shared_fn

    assert plan_validation_module.validate_tool_capability_membership is shared_fn

    invented = EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="x")],
        subtasks=[
            Subtask(
                id="s1", description="verify", execution_method=ExecutionMethod.TOOL,
                tool_name="totally_made_up_tool_xyz", relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    same_result = shared_fn(invented, available_tool_names=["validate_refactor"])
    assert not same_result.valid
    assert same_result.reason_codes == ["UNREGISTERED_TOOL_NAME"]


# =====================================================================
# N - repair prompt no longer contains the obsolete "Do not emit TOOL
# subtasks" claim.
# =====================================================================

def test_n_repair_prompt_no_longer_contains_obsolete_tool_subtask_claim():
    from kriya.workflow.planner_repair import build_structured_plan_repair_prompt

    prompt = build_structured_plan_repair_prompt(
        "goal", "plan text", ["some error"], ["SOME_UNGUIDED_CODE"], 1,
    )
    assert "authoritative enforce mode has no policy-mediated TOOL router yet" not in prompt
    assert "Do not emit TOOL subtasks" not in prompt


# =====================================================================
# O - repair prompt accurately distinguishes Subtask.tool_name from
# verification[].tool_name (in its own always-present trailer text, not
# just the reason-code-conditional block covered by test C's fixture).
# =====================================================================

def test_o_repair_prompt_trailer_distinguishes_subtask_tool_name_from_verification_tool_name():
    from kriya.workflow.planner_repair import build_structured_plan_repair_prompt

    prompt = build_structured_plan_repair_prompt(
        "goal", "plan text", ["some error"], ["SOME_UNGUIDED_CODE"], 1,
    )
    assert "top-level tool_name" in prompt
    assert "verification[]" in prompt


# =====================================================================
# P - Planner repair remains capped by the existing shared bound; Q -
# Planner token budget remains correct.
# =====================================================================

@pytest.mark.asyncio
async def test_p_repair_capped_at_shared_bound(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=_tool_subtask_missing_toolname_plan(subtask_tool_name=None))
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert planner_run.call_count == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS == 3


@pytest.mark.asyncio
async def test_q_repair_call_uses_planner_stage_token_budget_not_general_max_tokens(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.llm.max_tokens = 4096
    cfg.llm.planner_max_tokens = 9999
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=_tool_subtask_missing_toolname_plan(subtask_tool_name=None))
    we = WorkflowEngine(kernel, llm)
    assert we.planner.max_output_tokens == 9999

    await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert llm.complete.call_count == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS
    for call in llm.complete.call_args_list:
        assert call.kwargs.get("max_tokens_override") == 9999


# =====================================================================
# R - existing valid model-only plans remain backward compatible.
# =====================================================================

@pytest.mark.asyncio
async def test_r_valid_model_only_plan_with_no_tool_subtasks_is_unaffected(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)  # no tools registered at all
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: do it",  # the ~100-existing-tests short terse mock shape
        "Design: modify pkg/module.py", "OK",
    ])
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res.get("status") != "planner_output_schema_invalid"
    assert planner_run.call_count == 1


@pytest.mark.asyncio
async def test_r_truncated_output_never_enters_the_repair_loop_at_all(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    truncated = (
        "# Fix Plan\n\nSome real prose here.\n\n```json\n"
        '{"global_invariants": [], "subtasks": [{"id": "s1", "description": "cut off because'
    )
    llm.complete = AsyncMock(return_value=truncated)
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Build something", workspace_path=str(tmp_path))

    assert res["status"] == "planner_output_incomplete"
    assert planner_run.call_count == 1, "incomplete_truncated must never trigger repair"


# =====================================================================
# S - empty tool registry: no misleading catalog advertised; tool-
# execution plans cannot silently validate merely because tool_name is
# non-empty.
# =====================================================================

@pytest.mark.asyncio
async def test_s_empty_registry_advertises_no_catalog_and_rejects_non_blank_tool_name(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)  # empty registry
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(
        return_value=_tool_subtask_missing_toolname_plan(subtask_tool_name="anything_nonempty"),
    )
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    planner_prompt = llm.complete.call_args_list[0][0][1]
    assert "Registered Kriya tools available" not in planner_prompt
    assert res["status"] == "planner_output_schema_invalid"
    assert planner_run.call_count == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS


def test_s_empty_iterable_available_tool_names_is_not_the_same_as_none():
    """S (unit-level): available_tool_names=[] must run the check for real
    (and fail every TOOL subtask), never be treated the same as
    available_tool_names=None (which explicitly SKIPS the check)."""
    plan = EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="x")],
        subtasks=[
            Subtask(
                id="s1", description="verify", execution_method=ExecutionMethod.TOOL,
                tool_name="validate_refactor", relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    skipped = validate_tool_capability_membership(plan, available_tool_names=None)
    assert skipped.valid
    enforced_empty = validate_tool_capability_membership(plan, available_tool_names=[])
    assert not enforced_empty.valid
    assert enforced_empty.reason_codes == ["UNREGISTERED_TOOL_NAME"]


# =====================================================================
# T - multiple registered tools: membership correctness; no per-subtask
# registry scan (complements I above with a real accept/reject mix).
# =====================================================================

@pytest.mark.asyncio
async def test_t_multiple_registered_tools_membership_correctness(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    kernel.registry.register("tool", "tool_a", object())
    kernel.registry.register("tool", "tool_b", object())
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        _multi_tool_subtask_plan(["tool_a", "tool_b"]),
        "Design: modify pkg/module.py", "OK",
    ])
    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res.get("status") != "planner_output_schema_invalid"


@pytest.mark.asyncio
async def test_t_one_unregistered_name_among_multiple_registered_still_rejects(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    kernel.registry.register("tool", "tool_a", object())
    kernel.registry.register("tool", "tool_b", object())
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(
        return_value=_multi_tool_subtask_plan(["tool_a", "totally_unregistered"]),
    )
    we = WorkflowEngine(kernel, llm)
    planner_run = _wrap_planner_run(we)

    res = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert res["status"] == "planner_output_schema_invalid"
    assert planner_run.call_count == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS


def test_t_membership_check_is_case_insensitive_matching_registry_normalization():
    """Adversarial/negative case (advisor-flagged blind spot): kriya/core/
    registry.py's ComponentRegistry lowercases BOTH register() and get()
    - a tool_name that would actually resolve at real TOOL-001 dispatch
    time (kernel.registry.get("tool", subtask.tool_name), case-
    insensitive) must never be rejected here merely for a case
    difference, or validation and execution would disagree."""
    plan = EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="x")],
        subtasks=[
            Subtask(
                id="s1", description="verify", execution_method=ExecutionMethod.TOOL,
                tool_name="Validate_Refactor", relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    result = validate_tool_capability_membership(plan, available_tool_names=["validate_refactor"])
    assert result.valid, result.errors

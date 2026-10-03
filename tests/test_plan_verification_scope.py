"""PLAN-VERIFICATION-SCOPE-001: a Planner may not add application-runtime
verification the request does not require.

Measured (Spring XML pet types, live run 20261003T082022-b81d70cd, Planner
qwen3.6): the plan owned exactly the two needed files and the full suite
passed, then a verification unit the Planner added - "the application context
starts", an application_runtime verifier citing its own judgment criterion -
became a required runtime obligation. Kriya's own reading of the request
(goal_requires_runtime_behavior) said no runtime evidence was needed, the run
verifier rightly produced no command, and the run failed closed
(REQUIRED_RUNTIME_VERIFICATION_MISSING). Across all 28 approved live plans
this is the only runtime verifier, so nothing that succeeded is touched.
"""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.workflow import planner_repair as pr
from kriya.workflow.acceptance import goal_requires_runtime_behavior
from kriya.workflow.plan_normalization import VERIFICATION_SCOPE_NORMALIZED, scope_verification_to_requirements
from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    GlobalInvariant,
    IntegrationRelationship,
    IntegrationRelationshipKind,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
    VerifierKind,
)
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.planner_repair import build_structured_plan_repair_prompt
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_controller import WorkflowController

CODE = "PLAN_VERIFICATION_SCOPE_UNJUSTIFIED"
CONFIG = "src/main/resources/spring/tools-config.xml"
SERVICE = "src/main/java/shop/ItemServiceImpl.java"
NO_RUNTIME_GOAL = "Cache the item kinds the same way the items are cached, so repeated lookups do not query the database."
RUNTIME_GOAL = "Cache the item kinds and run the app to confirm it starts."
COMPILE = VerificationMethod(type=VerificationMethodType.TOOL, description="compiles", tool_name="compile")
TEST = VerificationMethod(type=VerificationMethodType.TOOL, description="all tests pass", tool_name="test")
RUNTIME = VerificationMethod(type=VerificationMethodType.JUDGMENT, description="the application context starts",
                             verifier_kind=VerifierKind.APPLICATION_RUNTIME, requires_runtime_execution=True)
JUDGMENT = AcceptanceCriterion(id="ac1", description="the kinds cache is configured",
                               method=VerificationMethodType.JUDGMENT)
SUITE = AcceptanceCriterion(id="ac3", description="all existing tests pass", method=VerificationMethodType.TOOL,
                            tool_name="test")
STARTS = AcceptanceCriterion(id="ac4", description="the application context starts",
                             method=VerificationMethodType.JUDGMENT)
GI = GlobalInvariant(id="gi1", statement="existing lookups keep their behaviour")


def test_the_two_goals_differ_only_in_what_kriya_reads_as_required():
    assert goal_requires_runtime_behavior(NO_RUNTIME_GOAL) is False
    assert goal_requires_runtime_behavior(RUNTIME_GOAL) is True


@pytest.fixture
def workspace(tmp_path):
    for rel in (CONFIG, SERVICE):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("<x/>\n")
    return str(tmp_path)


def _mutation(sid, path, verification=(COMPILE,), **extra):
    return Subtask(id=sid, description=f"change {path}", execution_method=ExecutionMethod.MODEL,
                   planned_files=[PlannedFile(path=path, action=FileAction.MODIFY)], verification=list(verification),
                   acceptance_criteria_ids=["ac1"], relevant_global_invariant_ids=["gi1"],
                   provides=[f"{sid}-done"], **extra)


def _verification(sid, verification, criteria, depends_on=("s1", "s2"), **extra):
    return Subtask(id=sid, description=f"{sid} verify", execution_method=ExecutionMethod.MODEL,
                   execution_role=ExecutionRole.VERIFICATION, verification=list(verification),
                   acceptance_criteria_ids=list(criteria), relevant_global_invariant_ids=["gi1"],
                   depends_on=list(depends_on), requires=[f"{d}-done" for d in depends_on], **extra)


def _live_shape(**s4_extra):
    """The measured plan: two mutation units, a suite unit, an invented runtime unit."""
    return EngineeringPlan(
        plan_id="p", kind=ChangeKind.TASK, global_invariants=[GI],
        acceptance_criteria=[JUDGMENT, SUITE, STARTS],
        subtasks=[_mutation("s1", CONFIG), _mutation("s2", SERVICE, depends_on=["s1"]),
                  _verification("s3", [TEST], ["ac3"], depends_on=("s2",)),
                  _verification("s4", [RUNTIME], ["ac4"], **s4_extra)])


async def _validate(plan, workspace, runtime_required):
    return await validate_plan(plan, workspace_path=workspace, require_model_planned_files=True,
                               runtime_verification_required=runtime_required)


# --- reproduction and the rule -------------------------------------------------

@pytest.mark.asyncio
async def test_the_unnormalized_live_shape_is_refused_by_validation(workspace):
    """Before this rule the validator accepted exactly this plan (no runtime
    requirement, nothing refused the runtime unit) and it failed live; the
    refusal is the only finding, so nothing else about the plan is wrong."""
    plan = _live_shape()
    assert any(vm.requires_application_runtime for vm in plan.subtask_by_id("s4").verification)
    result = await _validate(plan, workspace, runtime_required=False)
    assert result.valid is False and result.reason_codes == [CODE]  # the new refusal is the only one
    assert "s4" in result.errors[0]


@pytest.mark.asyncio
async def test_an_invented_runtime_unit_is_removed_and_the_plan_stays_complete(workspace):
    plan, records = scope_verification_to_requirements(_live_shape(), runtime_verification_required=False)
    assert [st.id for st in plan.subtasks] == ["s1", "s2", "s3"]
    assert [ac.id for ac in plan.acceptance_criteria] == ["ac1", "ac3"]
    assert records == [{"reason_code": VERIFICATION_SCOPE_NORMALIZED, "action": "unit_removed", "subtask": "s4",
                        "removed": ["the application context starts"], "acceptance_criteria_removed": ["ac4"]}]
    # the mutation units, the suite unit and every requirement citation are untouched
    original = _live_shape()
    for sid in ("s1", "s2", "s3"):
        assert plan.subtask_by_id(sid) == original.subtask_by_id(sid)
    result = await _validate(plan, workspace, runtime_required=False)
    assert result.valid, result.errors


@pytest.mark.asyncio
async def test_required_runtime_verification_is_retained(workspace):
    plan, records = scope_verification_to_requirements(_live_shape(), runtime_verification_required=True)
    assert records == [] and plan == _live_shape()
    assert (await _validate(plan, workspace, runtime_required=True)).valid


@pytest.mark.parametrize("goal, kept", [(RUNTIME_GOAL, True), (NO_RUNTIME_GOAL, False)],
                         ids=["user-requested", "not-requested"])
def test_the_users_own_words_decide_through_the_existing_detector(goal, kept):
    plan, _ = scope_verification_to_requirements(_live_shape(), goal_requires_runtime_behavior(goal))
    assert ("s4" in {st.id for st in plan.subtasks}) is kept


def test_compile_and_test_verification_is_never_touched():
    plan = _live_shape()
    no_runtime = plan.model_copy(update={"subtasks": plan.subtasks[:3],
                                         "acceptance_criteria": [JUDGMENT, SUITE]})
    same, records = scope_verification_to_requirements(no_runtime, runtime_verification_required=False)
    assert same is no_runtime and records == []


def test_a_runtime_check_beside_a_test_verifier_loses_only_the_runtime_check():
    """Policy/requested test verification stays; only the unbased runtime
    verifier goes, and the unit keeps its criteria."""
    plan = _live_shape()
    mixed = plan.model_copy(update={"subtasks": plan.subtasks[:3] + [
        _verification("s4", [TEST, RUNTIME], ["ac4"])]})
    out, records = scope_verification_to_requirements(mixed, runtime_verification_required=False)
    s4 = out.subtask_by_id("s4")
    assert [vm.tool_name for vm in s4.verification] == ["test"] and s4.acceptance_criteria_ids == ["ac4"]
    assert records[0]["action"] == "verifier_removed" and [ac.id for ac in out.acceptance_criteria] == [
        "ac1", "ac3", "ac4"]


def test_a_mutation_unit_keeps_its_compile_verifier():
    plan = _live_shape()
    mutation = _mutation("s2", SERVICE, verification=(COMPILE, RUNTIME), depends_on=["s1"])
    out, _ = scope_verification_to_requirements(
        plan.model_copy(update={"subtasks": [plan.subtasks[0], mutation, plan.subtasks[2]],
                                "acceptance_criteria": [JUDGMENT, SUITE]}), runtime_verification_required=False)
    assert [vm.tool_name for vm in out.subtask_by_id("s2").verification] == ["compile"]
    assert out.subtask_by_id("s2").planned_files == mutation.planned_files


# --- removal only when obviously safe; otherwise the typed refusal -----------------

def _with(plan, **update):
    return EngineeringPlan.model_validate(plan.model_copy(update=update).model_dump(mode="json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", [
    "mutation_unit_runtime_only", "a_unit_depends_on_it", "its_output_is_consumed", "integration_relationship",
    "exclusive_tool_criterion", "exclusive_requirement_id",
])
async def test_a_runtime_unit_something_rests_on_is_refused_not_removed(workspace, variant):
    plan = _live_shape()
    s1, s2, s3, s4 = plan.subtasks
    if variant == "mutation_unit_runtime_only":
        plan = _with(plan, subtasks=[s1, _mutation("s2", SERVICE, verification=(RUNTIME,), depends_on=["s1"]), s3])
        plan = _with(plan, acceptance_criteria=[JUDGMENT, SUITE])
        target = "s2"
    else:
        target = "s4"
        if variant == "a_unit_depends_on_it":
            plan = _with(plan, subtasks=[s1, s2, s3.model_copy(update={"depends_on": ["s2", "s4"]}), s4])
        elif variant == "its_output_is_consumed":
            plan = _with(plan, subtasks=[s1, s2, s3.model_copy(update={"requires": ["s2-done", "booted"]}),
                                         s4.model_copy(update={"provides": ["booted"]})])
        elif variant == "integration_relationship":
            plan = _with(plan, integration_relationships=[IntegrationRelationship(
                id="r1", kind=IntegrationRelationshipKind.USES, producer_subtask_ids=["s2"],
                consumer_subtask_ids=["s4"], relationship_statement="s4 uses s2")])
        elif variant == "exclusive_tool_criterion":
            plan = _with(plan, acceptance_criteria=[JUDGMENT, SUITE, STARTS.model_copy(update={
                "method": VerificationMethodType.TOOL, "tool_name": "test"})])
        elif variant == "exclusive_requirement_id":
            plan = _with(plan, subtasks=[s1, s2, s3, s4.model_copy(update={"requirement_ids": ["REQ-1"]})])
    out, records = scope_verification_to_requirements(plan, runtime_verification_required=False)
    assert records == [] and out == plan
    result = await _validate(out, workspace, runtime_required=False)
    assert result.valid is False and CODE in result.reason_codes and target in result.errors[
        result.reason_codes.index(CODE)]


def test_a_requirement_id_another_unit_cites_does_not_block_removal():
    plan = _live_shape()
    s1, s2, s3, s4 = plan.subtasks
    plan = _with(plan, subtasks=[s1.model_copy(update={"requirement_ids": ["REQ-1"]}), s2, s3,
                                 s4.model_copy(update={"requirement_ids": ["REQ-1"]})])
    out, records = scope_verification_to_requirements(plan, runtime_verification_required=False)
    assert [st.id for st in out.subtasks] == ["s1", "s2", "s3"] and out.subtask_by_id("s1").requirement_ids == [
        "REQ-1"]
    assert records[0]["action"] == "unit_removed"


@pytest.mark.asyncio
async def test_the_refusal_applies_only_under_authoritative_enforce_validation(workspace):
    result = await validate_plan(_live_shape(), workspace_path=workspace, runtime_verification_required=False)
    assert CODE not in result.reason_codes


def test_the_code_is_a_structured_validation_failure_with_targeted_guidance():
    assert CODE in pr.PLANNER_VALIDATION_FAILURE_CODES
    prompt = build_structured_plan_repair_prompt("goal", "{}", ["subtask(s) ['s4'] declare ..."], [CODE], 1)
    assert "must not declare application_runtime verification" in prompt
    assert "Do not add runtime checks the request did not ask for" in prompt


# --- through the real enforce controller ------------------------------------------

def _enforce(workspace, goal, plans):
    from test_workflow_controller_enforce import _route, _workflow_engine

    engine = _workflow_engine()
    engine.engineering_triage.recompute_from_files = AsyncMock(return_value=_route())
    executed = []

    async def generation(**kwargs):
        executed.append((kwargs["execution_scope"], [vm["tool_name"] or vm["verifier_kind"]
                                                     for vm in kwargs["required_verification"]],
                         kwargs["runtime_verification_required"]))
        for rel in kwargs["allowed_write_relpaths"] or []:
            with open(os.path.join(kwargs["workspace_path"], rel), "w", encoding="utf-8") as fh:
                fh.write("<changed/>\n")
        return {"status": "success", "quality_gates_passed": True, "files": kwargs["allowed_write_relpaths"] or [],
                "verification_results": [dict(vm, passed=True) for vm in kwargs["required_verification"]]}

    engine.run_generation_workflow = AsyncMock(side_effect=generation)
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output",
               return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
               side_effect=plans):
        result = asyncio.run(WorkflowController(engine).execute(goal, workspace, migration_mode="enforce"))
    return engine, executed, result


def test_the_live_shape_completes_without_the_invented_runtime_unit(workspace):
    engine, executed, result = _enforce(workspace, NO_RUNTIME_GOAL, lambda *a, **k: _live_shape())
    assert [scope.split()[0] for scope, _, _ in executed] == ["subtask=s1", "subtask=s2", "subtask=s3"]
    assert executed[2][1] == ["test"]  # the suite unit still runs
    assert not any(runtime for _, _, runtime in executed)
    assert engine.planner.run.await_count == 1  # deterministic, no repair round
    assert result.legacy_result["status"] == "success"


def test_a_requested_runtime_unit_still_runs_as_a_runtime_obligation(workspace):
    _, executed, result = _enforce(workspace, RUNTIME_GOAL, lambda *a, **k: _live_shape())
    assert executed[-1][0].startswith("subtask=s4") and executed[-1][2] is True
    assert result.legacy_result["status"] == "success"


def test_an_unremovable_invented_runtime_check_is_repaired_before_any_developer_work(workspace):
    plan = _live_shape()
    s1, _, s3, _ = plan.subtasks
    bad = _with(plan, subtasks=[s1, _mutation("s2", SERVICE, verification=(RUNTIME,), depends_on=["s1"]), s3],
                acceptance_criteria=[JUDGMENT, SUITE])
    good = _with(plan, subtasks=[s1, plan.subtasks[1], s3], acceptance_criteria=[JUDGMENT, SUITE])
    engine, executed, result = _enforce(workspace, NO_RUNTIME_GOAL, [bad, good])
    assert engine.planner.run.await_count == 2
    repair_request = " ".join(str(a) for a in engine.planner.run.await_args_list[1].args)
    assert "must not declare application_runtime verification" in repair_request
    assert [scope.split()[0] for scope, _, _ in executed] == ["subtask=s1", "subtask=s2", "subtask=s3"]
    assert result.legacy_result["status"] == "success" and result.legacy_result["plan_repair_attempts"] == 1


def test_a_repeated_unremovable_runtime_check_fails_closed_typed(workspace):
    plan = _live_shape()
    bad = _with(plan, subtasks=[plan.subtasks[0], _mutation("s2", SERVICE, verification=(RUNTIME,),
                                                            depends_on=["s1"]), plan.subtasks[2]],
                acceptance_criteria=[JUDGMENT, SUITE])
    engine, executed, result = _enforce(workspace, NO_RUNTIME_GOAL, lambda *a, **k: bad)
    assert executed == []
    engine.run_generation_workflow.assert_not_awaited()
    assert result.legacy_result["status"] != "success"
    assert CODE in result.legacy_result["reason_codes"]
    assert "STRUCTURED_PLAN_REPAIR_EXHAUSTED" in result.legacy_result["reason_codes"]

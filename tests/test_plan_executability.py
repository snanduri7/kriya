"""PLAN-EXECUTABILITY-001: a mutation unit needs a deterministic acceptance path.

Measured (the 26 enforce plans persisted by the 2026-10-02 live matrix,
including the five judge-verified successes): every acceptance criterion is
method=judgment and every mutation unit declares a deterministic compile or
test verifier. The one shape validate_plan accepted that nothing can ever
show complete was a mutation unit with NO deterministic verifier at all - an
empty verification list. It is now refused before any Developer attempt and
repaired through the existing bounded Planner repair.

Judgment criteria beside a real verifier stay valid: compile success is not
proof of higher-level behaviour, and nothing here turns judgment into compile.
"""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.workflow import planner_repair as pr
from kriya.workflow import verified_no_change as vnc
from kriya.workflow.plan_normalization import coalesce_same_file_owners
from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    GlobalInvariant,
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

CODE = "MUTATION_UNIT_ACCEPTANCE_PATH_MISSING"
SERVICE = "src/main/java/shop/ItemService.java"
FORMATTER = "src/main/java/shop/ItemFormatter.java"
ITEM_TEST = "src/test/java/shop/ItemServiceTests.java"
COMPILE = VerificationMethod(type=VerificationMethodType.TOOL, description="compiles", tool_name="compile")
TEST = VerificationMethod(type=VerificationMethodType.TOOL, description="tests pass", tool_name="test")
JUDGMENT = VerificationMethod(type=VerificationMethodType.JUDGMENT, description="looks right")
GI = GlobalInvariant(id="gi1", statement="the item service keeps its public API")
JUDGMENT_AC = AcceptanceCriterion(id="ac1", description="repeated lookups do not hit the database",
                                  method=VerificationMethodType.JUDGMENT)


@pytest.fixture
def workspace(tmp_path):
    for rel in (SERVICE, FORMATTER, ITEM_TEST):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("class Placeholder {}\n")
    return str(tmp_path)


def _unit(sid="s1", files=(SERVICE,), verification=(), **overrides):
    fields = dict(
        id=sid, description=f"{sid} change", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path=path, action=FileAction.MODIFY) for path in files],
        verification=list(verification), acceptance_criteria_ids=["ac1"], relevant_global_invariant_ids=["gi1"],
    )
    fields.update(overrides)
    return Subtask(**fields)


def _plan(*subtasks, criteria=(JUDGMENT_AC,)):
    return EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=list(subtasks),
                           acceptance_criteria=list(criteria), global_invariants=[GI])


async def _validate(plan, workspace):
    return await validate_plan(plan, workspace_path=workspace, require_model_planned_files=True)


# --- the accept / refuse rule -------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("verifier", [COMPILE, TEST], ids=["compile", "test"])
async def test_a_mutation_unit_with_a_deterministic_verifier_and_judgment_criteria_is_valid(workspace, verifier):
    """The measured live shape - including every judge-verified success."""
    result = await _validate(_plan(_unit(verification=[verifier])), workspace)
    assert result.valid, result.errors
    assert CODE not in result.reason_codes


@pytest.mark.asyncio
async def test_a_runtime_verifier_is_a_deterministic_acceptance_path(workspace):
    runtime = VerificationMethod(type=VerificationMethodType.JUDGMENT, description="run it",
                                 verifier_kind=VerifierKind.APPLICATION_RUNTIME, requires_runtime_execution=True)
    # a request that requires runtime evidence (PLAN-VERIFICATION-SCOPE-001)
    result = await validate_plan(_plan(_unit(verification=[runtime])), workspace_path=workspace,
                                 require_model_planned_files=True, runtime_verification_required=True)
    assert result.valid, result.errors


@pytest.mark.asyncio
async def test_a_mutation_unit_with_no_verifier_is_refused_and_named(workspace):
    result = await _validate(_plan(_unit(verification=[])), workspace)
    assert not result.valid
    assert result.reason_codes == [CODE]
    assert len(result.errors) == 1
    assert "subtask 's1'" in result.errors[0] and SERVICE in result.errors[0]


@pytest.mark.asyncio
async def test_a_mutation_unit_with_no_acceptance_at_all_is_refused(workspace):
    result = await _validate(_plan(_unit(verification=[], acceptance_criteria_ids=[]), criteria=()), workspace)
    assert result.reason_codes == [CODE]


@pytest.mark.asyncio
async def test_a_judgment_only_verifier_is_refused_by_the_existing_prv11_rule_alone(workspace):
    """One defect, one code: PRV-11 already names the judgment verifier."""
    result = await _validate(_plan(_unit(verification=[JUDGMENT])), workspace)
    assert result.reason_codes == ["VERIFICATION_EVIDENCE_PATH_MISSING"]


@pytest.mark.asyncio
async def test_a_cited_builtin_tool_criterion_is_a_deterministic_path(workspace):
    tool_ac = AcceptanceCriterion(id="ac1", description="suite passes", method=VerificationMethodType.TOOL,
                                  tool_name="test")
    result = await _validate(_plan(_unit(verification=[]), criteria=(tool_ac,)), workspace)
    assert result.valid, result.errors


@pytest.mark.asyncio
async def test_a_cited_non_builtin_tool_criterion_is_not_a_path(workspace):
    """Same evidence-producer set PRV-11 uses: a registered non-quality-gate
    tool named on a criterion produces no coverage."""
    tool_ac = AcceptanceCriterion(id="ac1", description="looks fine", method=VerificationMethodType.TOOL,
                                  tool_name="shell")
    result = await _validate(_plan(_unit(verification=[]), criteria=(tool_ac,)), workspace)
    assert CODE in result.reason_codes


@pytest.mark.asyncio
async def test_an_unscoped_implementation_unit_keeps_its_own_code(workspace):
    result = await _validate(_plan(_unit(files=(), verification=[])), workspace)
    assert result.reason_codes == ["MODEL_SUBTASK_MISSING_PLANNED_FILES"]


@pytest.mark.asyncio
async def test_the_rule_applies_only_under_authoritative_validation(workspace):
    result = await validate_plan(_plan(_unit(verification=[])), workspace_path=workspace)
    assert result.valid


# --- verification units vs mutation units -------------------------------------

@pytest.mark.asyncio
async def test_running_an_existing_test_stays_a_verification_unit(workspace):
    verify = Subtask(id="s2", description="run ItemServiceTests", execution_method=ExecutionMethod.MODEL,
                     execution_role=ExecutionRole.VERIFICATION, depends_on=["s1"], verification=[TEST],
                     acceptance_criteria_ids=["ac1"])
    plan = _plan(_unit(verification=[COMPILE]), verify)
    result = await _validate(plan, workspace)
    assert result.valid, result.errors
    normalized, _ = coalesce_same_file_owners(plan)
    assert normalized.subtask_by_id("s2").execution_role == ExecutionRole.VERIFICATION
    assert normalized.subtask_by_id("s2").planned_files == []


@pytest.mark.asyncio
async def test_changing_a_test_file_is_mutation_work_and_needs_a_verifier(workspace):
    unit = _unit(files=(ITEM_TEST,), verification=[])
    assert unit.execution_role == ExecutionRole.IMPLEMENTATION
    assert (await _validate(_plan(unit), workspace)).reason_codes == [CODE]
    assert (await _validate(_plan(_unit(files=(ITEM_TEST,), verification=[TEST])), workspace)).valid


# --- interactions with the existing contracts ---------------------------------

@pytest.mark.asyncio
async def test_same_file_coalescing_keeps_the_verifier_and_the_merged_unit_is_executable(workspace):
    """A unit with no verifier merged into an unordered same-file owner that has one."""
    plan = _plan(_unit("s1", verification=[COMPILE]), _unit("s2", verification=[]))
    assert CODE in (await _validate(plan, workspace)).reason_codes
    merged, records = coalesce_same_file_owners(plan)
    assert records and len(merged.subtasks) == 1
    assert (await _validate(merged, workspace)).valid


def test_an_executable_unit_still_cannot_complete_without_a_change_on_judgment():
    """VERIFIED_NO_CHANGE is untouched: a compile verifier makes the unit
    executable, but its judgment criterion still never proves a no-change."""
    plan = _plan(_unit(verification=[COMPILE]))
    gates = [{"type": "compile", "success": True, "attempt": 1}]
    result = {"deterministic_gate_evidence": [{"type": "compile", "passed": True, "attempt": 1}],
              "acceptance_coverage": vnc.unit_coverage_items(plan, "s1", gates, 1)}
    binding, refusal = vnc.verify_no_change_unit(plan, "s1", result)
    assert binding is None
    assert refusal["code"] == "ACCEPTANCE_COVERAGE_INCOMPLETE"


@pytest.mark.asyncio
async def test_the_measured_failing_live_shape_is_structurally_executable(workspace):
    """The persisted Spring XML plan (generic names): judgment criterion,
    compile verifier on every mutation unit, an over-scoped unit and the
    configuration file omitted. It passes this gate - its failure was the
    Planner's choice of files (PLANNER_MODEL_CAPABILITY), which no
    structural rule can decide."""
    plan = EngineeringPlan(
        plan_id="p", kind=ChangeKind.TASK, acceptance_criteria=[JUDGMENT_AC],
        global_invariants=[GI],
        subtasks=[
            _unit("s1", verification=[COMPILE], provides=["cached"]),
            _unit("s2", files=(FORMATTER,), verification=[COMPILE], depends_on=["s1"], requires=["cached"]),
            Subtask(id="s3", description="regression", execution_method=ExecutionMethod.MODEL,
                    execution_role=ExecutionRole.VERIFICATION, depends_on=["s1", "s2"], requires=["cached"],
                    verification=[TEST], acceptance_criteria_ids=["ac1"], relevant_global_invariant_ids=["gi1"]),
        ],
    )
    result = await validate_plan(plan, workspace_path=workspace, require_model_planned_files=True,
                                 require_semantic_contracts=True)
    assert result.valid, result.errors


# --- the bounded repair path ----------------------------------------------------

def test_the_code_is_a_structured_validation_failure_with_targeted_guidance():
    assert CODE in pr.PLANNER_VALIDATION_FAILURE_CODES
    prompt = build_structured_plan_repair_prompt(
        "goal", "{}", ["subtask 's1' changes files ['a'] but has no deterministic verifier"], [CODE], 1)
    for option in ("add a deterministic verifier", "merge its planned files into the subtask",
                   "turn it into a verification-only subtask", "remove it if the goal does not need it",
                   "do not relabel them as compile or test"):
        assert option in prompt


def _enforce(workspace, plans):
    from test_workflow_controller_enforce import _route, _workflow_engine

    engine = _workflow_engine()
    engine.engineering_triage.recompute_from_files = AsyncMock(return_value=_route())
    calls = []

    async def generation(**kwargs):
        calls.append(sorted(kwargs.get("allowed_write_relpaths") or []))
        for rel in kwargs["allowed_write_relpaths"]:
            with open(os.path.join(kwargs["workspace_path"], rel), "w", encoding="utf-8") as fh:
                fh.write("class Changed {}\n")
        return {"status": "success", "quality_gates_passed": True, "files": kwargs["allowed_write_relpaths"],
                "verification_results": [{"type": "tool", "tool_name": "compile", "description": "compiles",
                                          "passed": True}]}

    engine.run_generation_workflow = AsyncMock(side_effect=generation)
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output",
               return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
               side_effect=plans):
        result = asyncio.run(WorkflowController(engine).execute("change the item service", workspace,
                                                               migration_mode="enforce"))
    return engine, calls, result


def test_bounded_repair_fixes_a_missing_verifier_before_any_developer_call(workspace):
    engine, calls, result = _enforce(
        workspace, [_plan(_unit(verification=[])), _plan(_unit(verification=[COMPILE]))])
    assert engine.planner.run.await_count == 2
    repair_request = " ".join(str(a) for a in engine.planner.run.await_args_list[1].args)
    assert "has no deterministic verifier" in repair_request
    assert "turn it into a verification-only subtask" in repair_request
    assert calls == [[SERVICE]]
    assert result.legacy_result["status"] == "success"
    assert result.legacy_result["plan_repair_attempts"] == 1


def test_a_repeated_missing_verifier_plan_fails_closed_without_developer_work(workspace):
    bad = _plan(_unit(verification=[]))
    engine, calls, result = _enforce(workspace, lambda *a, **k: bad)
    assert calls == []
    engine.run_generation_workflow.assert_not_awaited()
    assert result.legacy_result["status"] != "success"
    assert CODE in result.legacy_result["reason_codes"]
    assert "STRUCTURED_PLAN_REPAIR_EXHAUSTED" in result.legacy_result["reason_codes"]

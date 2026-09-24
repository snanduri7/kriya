"""PRD-008A A6: one terminal decision, and one safety path proven on both modes.

The same invariant tests run through the real direct entry point
(WorkflowEngine.run_generation_workflow) and the real milestone entry point
(run_milestones) with the same real engine - one implementation, exercised
twice, never two implementations each tested once.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from _milestone_proof_harness import _milestone, _run, _workspace, git_workspace  # noqa: F401
from test_prd008a_plan_executor import (  # noqa: F401 - pytest fixture
    CALC,
    CALC_WITH_SUB,
    THREE,
    THREE_OUTPUTS,
    FailingEngine,
    _config,
    _role_llm,
    calc_workspace,
)

from kriya.control.commit_state import UncertainWorkspaceStateError
from kriya.control.persistence import load_run_record, save_run_record, scan_run_records
from kriya.control.run_coordinator import annotate_run, coordinated_mutation, current_run_context
from kriya.control.run_record import RunLifecycle, RunRecord
from kriya.core.kernel import Kernel
from kriya.workflow.checkpoint import list_checkpoints
from kriya.workflow.execution_plan import ExecutionPlan
from kriya.workflow.milestones import MilestoneRunState, run_milestones
from kriya.workflow.plan_adapters import work_unit_record
from kriya.workflow.plan_executor import DEPENDENCY_FAILED
from kriya.workflow.workflow import WorkflowEngine

GOAL = "add sub to calc.py"


def _engine(developer_answer):
    cfg = _config()
    llm = _role_llm(cfg, developer_answer)
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    return engine, llm


def _invoke(mode, workspace, engine, **kwargs):
    """One real invocation of ``mode``'s entry point."""
    if mode == "direct":
        return asyncio.run(engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace), **kwargs))
    state = MilestoneRunState(group_id="grp", original_goal=GOAL, milestones=[_milestone("M1")])
    return asyncio.run(run_milestones(engine, state, str(workspace), **kwargs))


def _newest(workspace):
    return max(scan_run_records(str(workspace)).records, key=lambda r: r.created_at)


def _plan_of(record):
    return ExecutionPlan.from_dict(record.execution_plan)


MODES = ["direct", "milestone"]


@pytest.mark.parametrize("mode", MODES)
def test_success_is_owned_recorded_attributed_and_terminal(mode, calc_workspace):
    engine, _ = _engine(CALC_WITH_SUB)
    _invoke(mode, calc_workspace, engine)
    [record] = scan_run_records(str(calc_workspace)).records
    plan = _plan_of(record)
    assert plan.source_kind.value == mode
    assert {s["status"] for s in record.work_unit_states.values()} == {"VERIFIED"}
    assert record.lifecycle_state is RunLifecycle.SUCCESS
    assert Path(calc_workspace, "calc.py").read_text() == CALC_WITH_SUB
    committed = [c for c in record.commits if c.get("result") == "COMMITTED"]
    assert committed
    records = [work_unit_record(plan, unit.id) for unit in plan.work_units]
    assert all(c["work_unit"] in records for c in committed)
    assert record.active_work_unit is None


@pytest.mark.parametrize("mode", MODES)
def test_failure_is_never_success_and_leaves_the_file_untouched(mode, calc_workspace):
    engine, _ = _engine("[]")
    _invoke(mode, calc_workspace, engine)
    [record] = scan_run_records(str(calc_workspace)).records
    assert record.lifecycle_state is not RunLifecycle.SUCCESS
    assert "FAILED" in {s["status"] for s in record.work_unit_states.values()}
    assert not [c for c in record.commits if c.get("result") == "COMMITTED"]
    assert Path(calc_workspace, "calc.py").read_text() == CALC


@pytest.mark.parametrize("mode", MODES)
def test_checkpoints_belong_to_the_unit_and_run_that_saved_them(mode, calc_workspace):
    engine, _ = _engine("[]")
    _invoke(mode, calc_workspace, engine)
    [record] = scan_run_records(str(calc_workspace)).records
    plan = _plan_of(record)
    checkpoints = list_checkpoints(str(calc_workspace))
    assert checkpoints
    first_unit = plan.execution_order()[0]
    for checkpoint in checkpoints:
        assert checkpoint["work_unit"] == work_unit_record(plan, first_unit.id)
        assert checkpoint["_run_record"]["run_id"] == record.run_id


@pytest.mark.parametrize("mode", MODES)
def test_resume_selects_the_units_checkpoint_and_prd008_judges_the_drift(mode, calc_workspace):
    engine, _ = _engine("[]")
    _invoke(mode, calc_workspace, engine)
    [own] = [c["run_id"] for c in list_checkpoints(str(calc_workspace))]
    Path(calc_workspace, "calc.py").write_text(CALC + "# drift\n")  # workspace content changed
    engine, _ = _engine("[]")
    _invoke(mode, calc_workspace, engine, resume=True)
    decision = _newest(calc_workspace).resume_decision
    assert decision is not None and decision["checkpoint_id"] == own
    assert decision["invalidated_stages"], decision  # PRD-008 saw the drift


@pytest.mark.parametrize("mode", MODES)
def test_an_uncertain_workspace_is_refused_before_any_model_work(mode, calc_workspace):
    prior = RunRecord.new("prior", "workspace", None, None)
    running = prior.transition(RunLifecycle.RUNNING)
    eligible = running.begin_commit("tx-prior", intent="APPLY_VERIFIED_CANDIDATE", candidate_hash=None)
    for index, item in enumerate([prior, running, eligible]):
        save_run_record(str(calc_workspace), item, expected_revision=index or None)
    engine, llm = _engine(CALC_WITH_SUB)
    if mode == "direct":
        result = _invoke(mode, calc_workspace, engine)
        assert result["quality_gates_passed"] is False
    else:
        # Same gate (begin_mutating_run); a function entry point raises, which
        # `kriya generate --from-milestones` renders as [Recovery Required].
        with pytest.raises(UncertainWorkspaceStateError):
            _invoke(mode, calc_workspace, engine)
    llm.complete.assert_not_called()
    assert [r.run_id for r in scan_run_records(str(calc_workspace)).records] == ["prior"]
    assert Path(calc_workspace, "calc.py").read_text() == CALC


# --- the terminal decision ---------------------------------------------------------------

def test_the_run_coordinator_never_records_success_with_an_unverified_unit(tmp_path):
    """Defense in depth below the executor: whatever a result claims, a run
    whose plan has a non-VERIFIED unit is not SUCCESS."""
    ws = _workspace(tmp_path)

    @coordinated_mutation
    async def claims_success(workspace_path):
        annotate_run(workspace_path, work_unit_states={
            "W1": {"work_unit_id": "W1", "status": "VERIFIED", "reason_codes": [], "blocked_by": []},
            "W2": {"work_unit_id": "W2", "status": "BLOCKED", "reason_codes": ["X"], "blocked_by": ["W1"]},
        })
        return {"status": "success", "quality_gates_passed": True, "run_id": current_run_context().run_id}

    result = asyncio.run(claims_success(str(ws)))
    record = load_run_record(str(ws), result["run_id"])
    assert record.lifecycle_state is RunLifecycle.FAILURE


def test_a_blocked_downstream_unit_is_in_the_milestone_result(tmp_path):
    ws = _workspace(tmp_path)
    result, _ = _run(ws, THREE, FailingEngine(THREE, THREE_OUTPUTS, fail={"M2"}))
    assert result["status"] == "milestone_failed"
    states = result["work_unit_states"]
    assert states["M2"]["status"] == "FAILED"
    assert states["M3"]["status"] == "BLOCKED"
    assert states["M3"]["reason_codes"] == [DEPENDENCY_FAILED] and states["M3"]["blocked_by"] == ["M2"]


def test_a_direct_result_is_its_units_own_result_unchanged(calc_workspace):
    engine, _ = _engine(CALC_WITH_SUB)
    result = _invoke("direct", calc_workspace, engine)
    assert "work_unit_states" not in result

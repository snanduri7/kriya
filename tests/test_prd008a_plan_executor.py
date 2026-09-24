"""PRD-008A A4: one executor for every ExecutionPlan.

The executor itself (ordering, lifecycle, dependency blocking, active
work-unit record, terminal decision) is driven with a scripted driver inside
a real RunCoordinator-owned run; both user-facing modes are then run end to
end through their real entry points - WorkflowEngine.run_generation_workflow
for a direct goal and run_milestones for a milestone sequence.
"""
import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from _milestone_proof_harness import CHAIN, FakeEngine, _milestone, _run, _workspace, git_workspace  # noqa: F401

from kriya.config import AppConfig
from kriya.control.persistence import scan_run_records
from kriya.control.run_coordinator import InvalidRunContextError, begin_mutating_run, owning_run_work_unit
from kriya.control.run_record import RunLifecycle
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.workflow.checkpoint import list_checkpoints
from kriya.workflow.execution_plan import (
    ExecutionPlan,
    PlanSourceKind,
    TerminalPhase,
    WorkUnit,
    WorkUnitRole,
)
from kriya.workflow.plan_adapters import DIRECT_WORK_UNIT_ID, INTEGRATION_WORK_UNIT_ID, work_unit_record
from kriya.workflow.plan_executor import (
    COMPLETION_REUSED,
    DEPENDENCY_FAILED,
    PLAN_HALTED_AFTER_FAILURE,
    PLAN_TERMINAL_MISMATCH,
    TERMINAL_PHASE_FAILED,
    WORK_UNIT_EXCEPTION,
    WORK_UNIT_FAILED,
    NestedExecutionPlanError,
    PlanDriver,
    WorkUnitInvocation,
    execute_plan,
)
from kriya.workflow.workflow import WorkflowEngine


def _unit(uid, depends_on=(), role=WorkUnitRole.PRIMARY):
    return WorkUnit.build(id=uid, goal=f"do {uid}", depends_on=depends_on, role=role)


def _plan(*units, phases=()):
    return ExecutionPlan.build(
        plan_id="G", source_kind=PlanSourceKind.MILESTONE, work_units=units, terminal_phases=phases,
    )


class ScriptedDriver(PlanDriver):
    """Units pass unless named in ``fail``/``raise_in``; records what ran."""

    def __init__(self, workspace, fail=(), raise_in=(), phase_failure=None, failed_result=None):
        self.workspace = str(workspace)
        self.fail, self.raise_in = set(fail), set(raise_in)
        self.phase_failure = phase_failure
        self.failed_result = failed_result
        self.ran, self.active_units, self.invocations = [], [], []

    async def run_unit(self, plan, unit, invocation, *, resume, resume_id):
        self.ran.append(unit.id)
        self.invocations.append(invocation)
        self.active_units.append(owning_run_work_unit(self.workspace))
        if unit.id in self.raise_in:
            raise RuntimeError("boom")
        return {"quality_gates_passed": unit.id not in self.fail, "status": "x" if unit.id in self.fail else "ok"}

    async def unit_failed(self, unit, result):
        return self.failed_result or {"status": "unit_failed", "quality_gates_passed": False, "unit": unit.id}

    async def run_phase(self, phase):
        return self.phase_failure

    def plan_result(self, plan, last_result):
        return {"status": "success", "quality_gates_passed": True}


def _execute(workspace, plan, driver, **kwargs):
    with begin_mutating_run(str(workspace)) as context:
        result = asyncio.run(execute_plan(plan, driver, str(workspace), **kwargs))
        record = context._lease.record
    return result, record


def _states(record):
    return {uid: (s["status"], s["reason_codes"], s["blocked_by"]) for uid, s in record.work_unit_states.items()}


# --- the executor -----------------------------------------------------------------

def test_every_unit_runs_in_order_and_the_plan_succeeds(tmp_path):
    ws = _workspace(tmp_path)
    plan = _plan(_unit("W1"), _unit("W3", ["W2"]), _unit("W2", ["W1"]))
    driver = ScriptedDriver(ws)
    result, record = _execute(ws, plan, driver)
    assert driver.ran == ["W1", "W2", "W3"]
    assert result["status"] == "success"
    assert {uid: s[0] for uid, s in _states(record).items()} == dict.fromkeys(("W1", "W2", "W3"), "VERIFIED")
    assert record.execution_plan["fingerprint"] == plan.fingerprint
    assert [i.work_unit_id for i in driver.invocations] == ["W1", "W2", "W3"]
    assert all(i.plan_fingerprint == plan.fingerprint for i in driver.invocations)


def test_a_failed_dependency_blocks_its_dependents_and_halts_the_rest(tmp_path):
    ws = _workspace(tmp_path)
    plan = _plan(_unit("W1"), _unit("W2", ["W1"]), _unit("W3", ["W2"]), _unit("X"))
    driver = ScriptedDriver(ws, fail={"W2"})
    result, record = _execute(ws, plan, driver)
    assert driver.ran == ["W1", "W2"]
    assert result["status"] == "unit_failed"
    states = _states(record)
    assert states["W1"][0] == "VERIFIED"
    assert states["W2"][0] == "FAILED" and WORK_UNIT_FAILED in states["W2"][1]
    assert states["W3"] == ("BLOCKED", [DEPENDENCY_FAILED], ["W2"])
    assert states["X"] == ("BLOCKED", [PLAN_HALTED_AFTER_FAILURE], ["W2"])
    assert record.lifecycle_state is not RunLifecycle.SUCCESS


def test_a_failure_result_claiming_success_fails_closed(tmp_path):
    ws = _workspace(tmp_path)
    plan = _plan(_unit("W1"), _unit("W2", ["W1"]))
    driver = ScriptedDriver(ws, fail={"W1"}, failed_result={"status": "success", "quality_gates_passed": True})
    result, _ = _execute(ws, plan, driver)
    assert result["status"] == "needs_review"
    assert result["reason_codes"] == [PLAN_TERMINAL_MISMATCH]
    assert result["unverified_work_units"] == ["W1", "W2"]


def test_reusable_units_are_verified_without_running(tmp_path):
    ws = _workspace(tmp_path)
    plan = _plan(_unit("W1"), _unit("W2", ["W1"]))
    driver = ScriptedDriver(ws)
    result, record = _execute(ws, plan, driver, reusable_unit_ids=["W1"])
    assert driver.ran == ["W2"]
    assert _states(record)["W1"] == ("VERIFIED", [COMPLETION_REUSED], [])
    assert result["status"] == "success"


def test_the_active_work_unit_is_each_units_own_record_and_cleared_after(tmp_path):
    ws = _workspace(tmp_path)
    plan = _plan(_unit("W1"), _unit("INT", ["W1"], role=WorkUnitRole.INTEGRATION))
    driver = ScriptedDriver(ws)
    _, record = _execute(ws, plan, driver)
    assert driver.active_units == [work_unit_record(plan, "W1"), work_unit_record(plan, "INT")]
    assert record.active_work_unit is None


def test_a_failed_plan_phase_blocks_the_integration_unit(tmp_path):
    ws = _workspace(tmp_path)
    plan = _plan(
        _unit("W1"), _unit("INT", ["W1"], role=WorkUnitRole.INTEGRATION),
        phases=[TerminalPhase.REPLAY_PRIOR_VERIFICATIONS],
    )
    driver = ScriptedDriver(ws, phase_failure={"status": "milestone_replay_failed"})
    result, record = _execute(ws, plan, driver)
    assert driver.ran == ["W1"]
    assert result["status"] == "milestone_replay_failed"
    assert _states(record)["INT"][0:2] == ("BLOCKED", [TERMINAL_PHASE_FAILED, "replay_prior_verifications"])


def test_an_exception_fails_the_unit_blocks_the_rest_and_propagates(tmp_path):
    ws = _workspace(tmp_path)
    plan = _plan(_unit("W1"), _unit("W2", ["W1"]))
    driver = ScriptedDriver(ws, raise_in={"W1"})
    with pytest.raises(RuntimeError, match="boom"):
        _execute(ws, plan, driver)
    [record] = scan_run_records(str(ws)).records
    assert record.work_unit_states["W1"]["status"] == "FAILED"
    assert WORK_UNIT_EXCEPTION in record.work_unit_states["W1"]["reason_codes"]
    assert record.work_unit_states["W2"]["status"] == "BLOCKED"
    assert record.active_work_unit is None
    assert record.lifecycle_state is RunLifecycle.FAILURE


def test_a_plan_cannot_run_without_run_ownership(tmp_path):
    ws = _workspace(tmp_path)
    with pytest.raises(InvalidRunContextError):
        asyncio.run(execute_plan(_plan(_unit("W1")), ScriptedDriver(ws), str(ws)))


def test_a_new_intent_inside_a_running_unit_is_refused_not_wrapped(tmp_path):
    """The generation primitive called without a WorkUnitInvocation inside
    another plan's unit would become a nested direct plan and overwrite the
    active unit record - it is refused before any model work."""
    ws = _workspace(tmp_path)
    engine = WorkflowEngine(Kernel(config=AppConfig()), LLMClient(AppConfig()))
    engine.planner.run = AsyncMock(side_effect=AssertionError("no model work"))

    class NestingDriver(ScriptedDriver):
        async def run_unit(self, plan, unit, invocation, *, resume, resume_id):
            return await engine.run_generation_workflow(goal="nested", workspace_path=self.workspace)

    with pytest.raises(NestedExecutionPlanError):
        _execute(ws, _plan(_unit("W1")), NestingDriver(ws))
    engine.planner.run.assert_not_called()


# --- the real direct entry point -----------------------------------------------------

CALC = "def add(a, b):\n    return a + b\n"
CALC_WITH_SUB = CALC + "\n\ndef sub(a, b):\n    return a - b\n"


def _config():
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    return cfg


def _role_llm(cfg, developer_answer):
    async def complete(system_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "Developer Agent" in first:
            return developer_answer
        if "File List Planner" in first:
            return '["calc.py"]'
        if "Planner Agent" in first:
            return "Step 1: add sub to calc.py"
        if "Architect Agent" in first:
            return "Design: add sub(a, b) to calc.py"
        return "Review: Approved"

    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=complete)
    return llm


@pytest.fixture
def calc_workspace(git_workspace):  # noqa: F811
    (git_workspace / "calc.py").write_text(CALC)
    subprocess.run(["git", "add", "calc.py"], cwd=git_workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "calc"], cwd=git_workspace, check=True)
    return git_workspace


def _direct(workspace, developer_answer):
    cfg = _config()
    engine = WorkflowEngine(Kernel(config=cfg), _role_llm(cfg, developer_answer))
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    result = asyncio.run(engine.run_generation_workflow(goal="add sub to calc.py", workspace_path=str(workspace)))
    [record] = scan_run_records(str(workspace)).records
    return result, record


def test_a_direct_goal_runs_as_one_work_unit_through_the_executor(calc_workspace):
    result, record = _direct(calc_workspace, CALC_WITH_SUB)
    assert result["quality_gates_passed"] is True
    assert Path(calc_workspace, "calc.py").read_text() == CALC_WITH_SUB
    plan = record.execution_plan
    assert plan["source_kind"] == "direct"
    assert [u["id"] for u in plan["work_units"]] == [DIRECT_WORK_UNIT_ID]
    assert plan["work_units"][0]["goal"] == "add sub to calc.py"
    assert record.work_unit_states == {
        DIRECT_WORK_UNIT_ID: {"work_unit_id": DIRECT_WORK_UNIT_ID, "status": "VERIFIED", "reason_codes": [], "blocked_by": []},
    }
    assert record.lifecycle_state is RunLifecycle.SUCCESS
    committed = [c for c in record.commits if c.get("result") == "COMMITTED"]
    assert committed and all(c["work_unit"]["kind"] == "direct" for c in committed)
    assert record.active_work_unit is None


def test_a_failed_direct_goal_is_a_failed_unit_and_never_success(calc_workspace):
    result, record = _direct(calc_workspace, "[]")
    assert not result["quality_gates_passed"]
    assert Path(calc_workspace, "calc.py").read_text() == CALC
    assert record.work_unit_states[DIRECT_WORK_UNIT_ID]["status"] == "FAILED"
    assert record.lifecycle_state is not RunLifecycle.SUCCESS


def test_direct_checkpoints_carry_the_direct_unit_record(calc_workspace):
    # A failed run keeps its checkpoints (a successful one clears them).
    _direct(calc_workspace, "[]")
    checkpoints = list_checkpoints(str(calc_workspace))
    assert checkpoints
    assert all(c["work_unit"] == {
        "kind": "direct", "group_id": None, "milestone_id": None,
        "definition_digest": None, "work_unit_id": DIRECT_WORK_UNIT_ID,
    } for c in checkpoints)


# --- the real milestone entry point ------------------------------------------------------

THREE = [_milestone("M1"), _milestone("M2", ["M1"]), _milestone("M3", ["M2"])]
THREE_OUTPUTS = {m.id: {f"{m.id.lower()}.py": f"{m.id} = 1\n".encode()} for m in THREE}


class FailingEngine(FakeEngine):
    def __init__(self, *args, fail=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fail = set(fail)
        self.invocations = []

    async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **kwargs):
        self.invocations.append(kwargs.get("work_unit"))
        result = await super().run_generation_workflow(goal, workspace_path, milestone_index, **kwargs)
        if self.calls[-1] in self.fail:
            return {**result, "quality_gates_passed": False, "status": "failed"}
        return result


def test_a_milestone_sequence_runs_as_one_plan_through_the_executor(tmp_path):
    ws = _workspace(tmp_path)
    engine = FailingEngine(THREE, THREE_OUTPUTS)
    result, state = _run(ws, THREE, engine)
    assert result["status"] == "success"
    assert engine.calls == ["M1", "M2", "M3", "INTEGRATION"]
    assert [i.work_unit_id for i in engine.invocations] == ["M1", "M2", "M3", INTEGRATION_WORK_UNIT_ID]
    assert all(isinstance(i, WorkUnitInvocation) and i.source_kind is PlanSourceKind.MILESTONE for i in engine.invocations)
    [record] = scan_run_records(str(ws)).records
    assert record.execution_plan["source_kind"] == "milestone"
    assert [u["id"] for u in record.execution_plan["work_units"]] == ["M1", "M2", "M3", INTEGRATION_WORK_UNIT_ID]
    assert {s["status"] for s in record.work_unit_states.values()} == {"VERIFIED"}
    assert record.lifecycle_state is RunLifecycle.SUCCESS


def test_a_failed_milestone_blocks_its_dependents_and_integration(tmp_path):
    ws = _workspace(tmp_path)
    engine = FailingEngine(THREE, THREE_OUTPUTS, fail={"M2"})
    result, state = _run(ws, THREE, engine)
    assert result["status"] == "milestone_failed" and result["milestone_id"] == "M2"
    assert engine.calls == ["M1", "M2"]
    assert "M3" not in state.completed_milestone_ids and "M2" not in state.completed_milestone_ids
    [record] = scan_run_records(str(ws)).records
    states = _states(record)
    assert states["M1"][0] == "VERIFIED" and states["M2"][0] == "FAILED"
    assert states["M3"] == ("BLOCKED", [DEPENDENCY_FAILED], ["M2"])
    assert states[INTEGRATION_WORK_UNIT_ID] == ("BLOCKED", [DEPENDENCY_FAILED], ["M2"])
    assert record.lifecycle_state is not RunLifecycle.SUCCESS


def test_completed_milestones_are_reused_units_on_the_next_run(tmp_path):
    ws = _workspace(tmp_path)
    _run(ws, THREE, FailingEngine(THREE, THREE_OUTPUTS, fail={"M3"}))
    engine = FailingEngine(THREE, THREE_OUTPUTS)
    result, _ = _run(ws, THREE, engine)
    assert result["status"] == "success"
    assert engine.calls == ["M3", "INTEGRATION"]
    newest = max(scan_run_records(str(ws)).records, key=lambda r: r.created_at)
    assert _states(newest)["M1"] == ("VERIFIED", [COMPLETION_REUSED], [])
    assert _states(newest)["M2"] == ("VERIFIED", [COMPLETION_REUSED], [])


def test_a_hand_edited_plan_with_a_cycle_is_refused_before_any_work(tmp_path):
    ws = _workspace(tmp_path)
    cyclic = [_milestone("M1", ["M2"]), _milestone("M2", ["M1"])]
    engine = FailingEngine(CHAIN, {})
    result, _ = _run(ws, cyclic, engine)
    assert result["status"] == "milestone_plan_invalid"
    assert result["quality_gates_passed"] is False
    assert "DEPENDENCY_CYCLE" in result["reason_codes"]
    assert engine.calls == []

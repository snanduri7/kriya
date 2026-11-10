"""RECOVERY-SHARED-DECISION-PARTICIPANTS-CANNOT-MAY-CHANGE-001 - characterization of the existing behaviour the
MAY_CHANGE recovery disposition is built on (owner ruling 2026-11-10: characterization first; every fact here was
measured BEFORE any product edit and none of it is changed by the slice).

What a reopened owner's unit hands back to the enforce loop, through the real WorkflowController enforce loop and the
real workflow (the model scripted at the transport, the two toolchain gates stubbed - the harness of
tests/test_backend_final_closure_005_verification_owner_reopen.py, whose reopen is the one existing caller of
``reopened_owner=True``). ``_invoke_bounded_subtask`` differs between the two reopen roles only in the
``execution_scope`` label, the recovery text and ``reopened_owner`` (enforce_loop.py), so flipping that one argument
on the reopened call is the deterministic equivalent of an ``owner_recovery`` invocation of the same owner:

- K1: an identical full-file regeneration under ``reopened_owner=True`` is a ONE-attempt typed stop
  (``no_progress_reason=VERIFICATION_RETRY_NO_CHANGE_POSSIBLE``), commits nothing and leaves the plan worktree as it was.
- K2: the same answer under ``reopened_owner=False`` (what an owner-recovery reopen passes today) is refused inside
  the unit (``VERIFIED_NO_CHANGE_REFUSED``) and retried with "make the change the unit requires" - the measured
  sealed s4/s5 verdict, reproduced on a unit with no deterministically covered criterion.
- K3: the reopened owner's FIRST attempt on an existing file is asked for the full-file form.
- K4: a reopened owner that changes only another planned file (its other file returned identical): what is reported,
  committed and left in the plan worktree, with passing and with failing gates.
- K5: ``record_committed_effects`` for a refused / no-write owner result.
- K6: what an owner reopen leaves in the dependents' states.
- K7: a file's revision does not distinguish an absent file from an empty one.
"""
import asyncio
import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _protocol_responses import sentinel

from kriya.config import AppConfig
from kriya.control.state import ControlState
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.enforce_loop import committed_files, record_committed_effects
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    Subtask,
)
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.retry_progress import VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
from kriya.workflow.state import GenerationState
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import WorkflowController

SERVICE = "shop/service.py"
HELPER = "shop/helper.py"
BASE_SOURCE = "def page_size():\n    return 5\n"
WRONG_SOURCE = "PAGE_SIZE = 5\n\n\ndef page_size():\n    return PAGE_SIZE\n"
HELPER_SOURCE = "def helper():\n    return 1\n"
HELPER_CHANGED = "def helper():\n    return 2\n"
HELPER_BROKEN = "def helper(:\n"
FAILING = {"success": False, "output": "tests/test_shop.py F\nE   assert 5 == 10\n1 failed in 0.10s"}
PASSING = {"success": True, "output": "tests/test_shop.py .\n1 passed in 0.01s"}
REOPEN_MARK = "VERIFICATION FAILURE RECOVERY"


def _plan(two_file_owner=False):
    plan = {
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [],
        "subtasks": [
            {"id": "s1", "description": "make page size configurable", "execution_method": "model",
             "planned_files": [{"path": SERVICE, "action": "modify"}], "provides": ["page_size_configured"],
             "relevant_global_invariant_ids": ["gi1"],
             "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]},
            {"id": "s2", "description": "run the tests", "execution_method": "model",
             "execution_role": "verification", "planned_files": [], "depends_on": ["s1"],
             "requires": ["page_size_configured"], "relevant_global_invariant_ids": ["gi1"],
             "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]},
        ]}
    if two_file_owner:
        plan["acceptance_criteria"] = [{"id": "ac1", "description": "compiles", "method": "tool", "tool_name": "compile"}]
        plan["subtasks"][0]["planned_files"].append({"path": HELPER, "action": "modify"})
        plan["subtasks"][0]["acceptance_criteria_ids"] = ["ac1"]
    return EngineeringPlan.model_validate(plan)


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True)


def _reopen(tmp_path, *, reopened_owner=None, helper_on_reopen=None):
    """One enforce run: s1 writes WRONG_SOURCE, the verification unit s2 fails, s1 is reopened and regenerates
    SERVICE byte-identical. ``reopened_owner`` overrides that argument on the reopened call (None = as the loop passes
    it, True). ``helper_on_reopen`` makes s1 a two-file owner whose reopen also returns HELPER with that content."""
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    two_file_owner = helper_on_reopen is not None
    workspace = tmp_path / "ws"
    (workspace / "shop").mkdir(parents=True)
    (workspace / "shop/__init__.py").write_text("")
    (workspace / SERVICE).write_text(BASE_SOURCE)
    if two_file_owner:
        (workspace / HELPER).write_text(HELPER_SOURCE)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")

    active = {}
    seen = {"reopen_prompts": [], "reopen_calls": [], "events": []}

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = '{"files": ["%s"]}' % SERVICE
        elif "Developer Agent" in first:
            prompt = (system_prompt or "") + "\n" + (user_prompt or "")
            reopened = REOPEN_MARK in prompt
            if reopened:
                seen["reopen_prompts"].append(system_prompt or "")
            if two_file_owner and f'path="{HELPER}"' in (system_prompt or ""):
                content = sentinel(HELPER, analysis="helper.", content=helper_on_reopen if reopened else HELPER_SOURCE)
            else:
                content = sentinel(SERVICE, analysis="introduce PAGE_SIZE.", content=WRONG_SOURCE)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    real_workflow = WorkflowEngine.run_generation_workflow

    def _bytes(root, path):
        full = os.path.join(root, path)
        return open(full, encoding="utf-8").read() if os.path.exists(full) else None

    async def run_unit(self, *args, **kwargs):
        active["id"] = kwargs.get("current_subtask_id")
        is_reopen = "role=verification_owner_recovery" in str(kwargs.get("execution_scope", ""))
        if is_reopen and reopened_owner is not None:
            kwargs = {**kwargs, "reopened_owner": reopened_owner}
        root = kwargs["workspace_path"]
        before = {path: _bytes(root, path) for path in (SERVICE, HELPER)}
        result = await real_workflow(self, *args, **kwargs)
        if is_reopen:
            seen["reopen_calls"].append({
                "reopened_owner": kwargs.get("reopened_owner"), "result": result, "before": before,
                "after": {path: _bytes(root, path) for path in (SERVICE, HELPER)},
            })
        return result

    def run_compile_check(self, *_args, **_kwargs):
        helper = _bytes(self.workspace_path, HELPER)
        return ({"success": False, "output": "SyntaxError: invalid syntax"} if helper == HELPER_BROKEN
                else {"success": True, "output": "ok"})

    def run_tests(self, *_args, **_kwargs):
        return FAILING if active.get("id") == "s2" else PASSING

    real_record = GenerationState.record_event

    def record(state, event):
        seen["events"].append((active.get("id"), event))
        return real_record(state, event)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.planner.run = AsyncMock(return_value="structured plan")
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch.object(WorkflowEngine, "run_generation_workflow", new=run_unit), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=run_compile_check), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests), \
         patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
               return_value=_plan(two_file_owner)):
        result = asyncio.run(WorkflowController(engine).execute("Make the page size configurable", str(workspace),
                                                                migration_mode="enforce"))
    (call,) = seen["reopen_calls"]
    return call, seen, result


def test_k1_an_identical_full_file_regeneration_under_reopened_owner_is_a_one_attempt_typed_stop(tmp_path):
    call, seen, _result = _reopen(tmp_path)
    result = call["result"]
    assert call["reopened_owner"] is True  # what the loop passes for the one existing reopened-owner role
    assert result.get("quality_gates_passed") is False
    assert result.get("no_progress_reason") == VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
    assert committed_files(result) == []
    assert call["after"] == call["before"] and call["after"][SERVICE] == WRONG_SOURCE  # the worktree is untouched
    assert len(seen["reopen_prompts"]) == 1  # one Developer call: no repair retry on the same identical answer
    refused = [e.details["refusal"] for unit, e in seen["events"]
               if unit == "s1" and e.kind == "unit.verified_no_change_refused"]
    assert [r["code"] for r in refused] == [VERIFICATION_RETRY_NO_CHANGE_POSSIBLE]
    assert refused[0]["identical_rewrites"] == [SERVICE] and refused[0]["reopened_owner"] is True


def test_k2_the_same_answer_without_reopened_owner_is_refused_in_the_unit_and_retried(tmp_path):
    """The owner-recovery reopen of today passes ``reopened_owner=False``: an unchanged owner whose unit has no
    deterministically covered criterion is refused and sent through the repair retries, told to make a change."""
    call, seen, _result = _reopen(tmp_path, reopened_owner=False)
    result = call["result"]
    assert result.get("quality_gates_passed") is False
    assert result.get("no_progress_reason") != VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
    assert committed_files(result) == []
    assert call["after"] == call["before"]
    refused = [e.details["refusal"] for unit, e in seen["events"]
               if unit == "s1" and e.kind == "unit.verified_no_change_refused"]
    assert refused and all(r["code"] != VERIFICATION_RETRY_NO_CHANGE_POSSIBLE for r in refused)
    assert len(seen["reopen_prompts"]) > 1  # the refusal is a repair retry, not a stop
    failures = [e for unit, e in seen["events"] if unit == "s1" and e.kind == "unit.verified_no_change_refused"]
    assert failures


def test_k3_the_first_attempt_of_a_reopened_owner_on_an_existing_file_is_the_full_file_form(tmp_path):
    _call, seen, _result = _reopen(tmp_path)
    first = seen["reopen_prompts"][0]
    assert f'<<<KRIYA:FILE path="{SERVICE}">>>' in first  # the full-file outcome is requested
    assert "<<<KRIYA:EDIT" not in first and "<<<KRIYA:NO_CHANGE" not in first  # and it is the only one offered


def test_k4_an_off_target_write_with_passing_gates_is_committed_and_retained(tmp_path):
    """The reopened two-file owner returns SERVICE identical and changes only HELPER: an ordinary accepted mutation
    of HELPER (never the typed identical stop) - the bytes stay in the plan worktree."""
    call, _seen, _result = _reopen(tmp_path, helper_on_reopen=HELPER_CHANGED)
    result = call["result"]
    assert result.get("quality_gates_passed") is True
    assert result.get("no_progress_reason") != VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
    assert committed_files(result) == [HELPER]
    assert call["after"][SERVICE] == call["before"][SERVICE] == WRONG_SOURCE
    assert call["before"][HELPER] == HELPER_SOURCE and call["after"][HELPER] == HELPER_CHANGED  # retained


def test_k4b_an_off_target_write_with_failing_gates_commits_nothing_and_is_not_retained(tmp_path):
    call, _seen, _result = _reopen(tmp_path, helper_on_reopen=HELPER_BROKEN)
    result = call["result"]
    assert result.get("quality_gates_passed") is False
    assert committed_files(result) == []
    assert call["after"] == call["before"]  # the failed candidate left no bytes in the plan worktree


def test_k5_a_refused_or_no_write_owner_result_neither_erases_nor_grows_the_written_record():
    state = ControlState(schema_version=1, run_id="k5").with_updates(subtask_written_files={"s1": ["a.py"]})
    refused = {"status": "failed", "quality_gates_passed": False, "files": ["a.py", "b.py"],
               "no_progress_reason": VERIFICATION_RETRY_NO_CHANGE_POSSIBLE}
    assert record_committed_effects(state, "s1", refused).subtask_written_files == {"s1": ["a.py"]}
    no_write = {"status": "success", "quality_gates_passed": True, "files": []}
    assert record_committed_effects(state, "s1", no_write).subtask_written_files == {"s1": ["a.py"]}
    committed = {"status": "success", "quality_gates_passed": True, "files": ["b.py"]}
    assert record_committed_effects(state, "s1", committed).subtask_written_files == {"s1": ["a.py", "b.py"]}
    assert record_committed_effects(state, "s9", refused).subtask_written_files["s9"] == []  # an entry: the unit ran


def test_k7_a_revision_does_not_distinguish_an_absent_file_from_an_empty_one(tmp_path):
    from kriya.workflow.edit_safety import read_file_revision

    (tmp_path / "empty.txt").write_text("")
    assert read_file_revision(str(tmp_path / "absent.txt")) == read_file_revision(str(tmp_path / "empty.txt"))
    assert not os.path.lexists(tmp_path / "absent.txt") and os.path.lexists(tmp_path / "empty.txt")


# --- K6: dependent states around an owner reopen, at the enforce loop with the unit scripted (the harness of
# tests/test_workflow_controller_enforce.py::test_enforce_recovery_plan_*). ---

@pytest.fixture
def _plan_worktree_is_the_workspace(monkeypatch):
    monkeypatch.setattr("kriya.workflow.workflow_controller.create_git_worktree", lambda workspace: workspace)


def _scripted_engine():
    engine = MagicMock()
    engine.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT))
    engine.planner.run = AsyncMock(return_value="fake plan text")
    engine.kernel = None
    return engine


def _three_unit_plan():
    def unit(uid, path, depends_on=()):
        return Subtask(id=uid, description=uid, execution_method=ExecutionMethod.MODEL, depends_on=list(depends_on),
                       planned_files=[PlannedFile(path=path, action=FileAction.CREATE)])
    return EngineeringPlan(plan_id="run1", kind=ChangeKind.TASK, subtasks=[
        unit("s1", "owner.cfg"), unit("s2", "middle.py", ["s1"]), unit("s3", "consumer.py", ["s1", "s2"])])


@pytest.mark.asyncio
async def test_k6_dependents_marked_pending_on_an_owner_reopen_are_never_restored(tmp_path, _plan_worktree_is_the_workspace):
    """s1 is reopened for s3's failure. Its dependents s2 and s3 are set to ``pending`` before it runs; s3 gets a new
    state from its own retry, the intermediate dependent s2 is neither re-run nor re-marked - with the run a success."""
    states_at_reopen = {}
    calls = []
    engine = _scripted_engine()

    async def fake_run(**kwargs):
        calls.append(kwargs["execution_scope"])
        n = len(calls)
        if n == 1:
            (tmp_path / "owner.cfg").write_text("v1")
            return {"status": "success", "quality_gates_passed": True, "files": ["owner.cfg"]}
        if n == 2:
            (tmp_path / "middle.py").write_text("m")
            return {"status": "success", "quality_gates_passed": True, "files": ["middle.py"]}
        if n == 3:
            return {"status": "failed", "quality_gates_passed": False, "files": [],
                    "plan_scope_conflict": {"reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "compile",
                                            "required_files": ["owner.cfg"], "allowed_files": ["consumer.py"]}}
        if n == 4:
            from kriya.control.persistence import load_control_state
            states_at_reopen.update(load_control_state(str(tmp_path)).subtask_states)
            (tmp_path / "owner.cfg").write_text("v2")
            return {"status": "success", "quality_gates_passed": True, "files": ["owner.cfg"]}
        (tmp_path / "consumer.py").write_text("c")
        return {"status": "success", "quality_gates_passed": True, "files": ["consumer.py"]}

    engine.run_generation_workflow = fake_run
    plan = _three_unit_plan()
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
         patch("kriya.workflow.workflow_controller.validate_plan",
               new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        result = await WorkflowController(engine).execute("goal", str(tmp_path), migration_mode="enforce")

    assert calls == ["subtask=s1 role=planned", "subtask=s2 role=planned", "subtask=s3 role=planned",
                     "subtask=s1 role=owner_recovery", "subtask=s3 role=consumer_retry"]
    assert (states_at_reopen["s1"], states_at_reopen["s2"], states_at_reopen["s3"]) == ("in_progress", "pending", "pending")
    assert result.legacy_result["status"] == "success"
    final = result.control_state.subtask_states
    assert (final["s1"], final["s3"]) == ("completed", "completed")
    assert final["s2"] == "pending"  # the intermediate dependent: invalidated, never re-run, never restored

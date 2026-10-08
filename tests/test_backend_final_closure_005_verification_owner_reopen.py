"""ENFORCE-VERIFICATION-UNIT-STOP-BEFORE-TERMINAL-GATE-001 (BACKEND-FINAL-CLOSURE-005, measured on P4-T4
python-slugify): unit s1 passed its own gates on a wrong candidate (every failing test PRE_EXISTING at the baseline),
the verification-only unit s2 then found the suite still failing and stopped typed (VERIFICATION_RETRY_NO_CHANGE_
POSSIBLE) - the run ended there and s1's Developer never heard of the failure although it was deterministic new
information for exactly that unit.

Now: the nearest completed mutating ancestor is reopened ONCE per verification unit with the verification's own gate
output; only a candidate that changed the owner's files, passed the owner's gates and stayed in scope is folded
forward, then the verification unit runs once more. An unchanged owner candidate leaves the original failure standing
(the LR-R1-P4 terminal, re-pinned in tests/test_lr_r1_p4_verification_only_retry_reproducer.py).

Through the real WorkflowController enforce loop and the real workflow; the model is scripted at the transport, the two
toolchain gates are stubbed (the test gate reads the real candidate file to decide).
"""
import asyncio
import subprocess
from unittest.mock import AsyncMock, patch

from _protocol_responses import sentinel

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import (
    nearest_mutating_upstream_owner,
    verification_failure_evidence,
    verification_owner_recovery_context,
)

SERVICE = "shop/service.py"
BASE_SOURCE = "def page_size():\n    return 5\n"
WRONG_SOURCE = "PAGE_SIZE = 5\n\n\ndef page_size():\n    return PAGE_SIZE\n"
FIXED_SOURCE = "PAGE_SIZE = 10\n\n\ndef page_size():\n    return PAGE_SIZE\n"
BROKEN_SOURCE = "PAGE_SIZE = \n"  # a changed owner candidate whose own gates fail
FAILING = {"success": False, "output": "tests/test_shop.py F\n=================================== FAILURES "
                                       "===================================\nE   assert 5 == 10\n1 failed in 0.10s"}
PASSING = {"success": True, "output": "tests/test_shop.py .\n1 passed in 0.01s"}


def _plan():
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [],
        "subtasks": [
            {"id": "s1", "description": "make page size configurable", "execution_method": "model",
             "planned_files": [{"path": SERVICE, "action": "modify"}], "provides": ["page_size_configured"],
             "relevant_global_invariant_ids": ["gi1"],
             "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]},
            {"id": "s2", "description": "run the tests", "execution_method": "model",
             "execution_role": "verification", "planned_files": [], "depends_on": ["s1"],
             "requires": ["page_size_configured"],
             "relevant_global_invariant_ids": ["gi1"],
             "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]},
        ]})


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True)


def _enforce(tmp_path, *, owner_fixes: bool, plan=None, owner_breaks: bool = False, owner_escapes: bool = False):
    from kriya.workflow.workflow_controller import WorkflowController

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
    workspace = tmp_path / "ws"
    (workspace / "shop").mkdir(parents=True)
    (workspace / "shop/__init__.py").write_text("")
    (workspace / SERVICE).write_text(BASE_SOURCE)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")

    active = {}
    model_calls = []
    developer_prompts = []
    test_gate_runs = []

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        model_calls.append((active.get("id"), first[:60]))
        if "File List Planner" in first:
            content = '{"files": ["%s"]}' % SERVICE
        elif "Developer Agent" in first:
            developer_prompts.append((system_prompt or "") + "\n" + (user_prompt or ""))
            reopened = "VERIFICATION FAILURE RECOVERY" in developer_prompts[-1]
            source = BROKEN_SOURCE if (reopened and owner_breaks) else FIXED_SOURCE if (reopened and owner_fixes) else WRONG_SOURCE
            content = sentinel(SERVICE, analysis="introduce PAGE_SIZE.", content=source)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    real_workflow = WorkflowEngine.run_generation_workflow

    async def run_unit(self, *args, **kwargs):
        active["id"] = kwargs.get("current_subtask_id")
        result = await real_workflow(self, *args, **kwargs)
        if owner_escapes and "role=verification_owner_recovery" in str(kwargs.get("execution_scope", "")):
            # the reopened owner reports a file outside its declared scope (the MA6 invariant-4 shape)
            result = {**result, "files": list(result.get("files") or []) + ["unexpected.py"]}
        return result

    def run_compile_check(self, *_args, **_kwargs):
        import os
        current = open(os.path.join(self.workspace_path, SERVICE), encoding="utf-8").read()
        return {"success": False, "output": "SyntaxError: invalid syntax"} if current == BROKEN_SOURCE else {"success": True, "output": "ok"}

    def run_tests(self, *_args, **_kwargs):
        test_gate_runs.append(active.get("id"))
        if active.get("id") != "s2":
            return PASSING
        # the verification unit judges the real tree: green only once the owner's fix is on disk
        import os
        current = open(os.path.join(self.workspace_path, SERVICE), encoding="utf-8").read()
        return PASSING if current == FIXED_SOURCE else FAILING

    events = []
    real_record = GenerationState.record_event

    def record(state, event):
        events.append(event)
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
               return_value=plan or _plan()):
        result = asyncio.run(WorkflowController(engine).execute("Make the page size configurable", str(workspace),
                                                                migration_mode="enforce"))
    return workspace, events, result, model_calls, developer_prompts, test_gate_runs


def test_the_verification_failure_reopens_the_owner_once_and_the_rerun_passes(tmp_path):
    workspace, events, result, model_calls, prompts, test_gate_runs = _enforce(tmp_path, owner_fixes=True)
    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "s2": "completed"}, (status, result.legacy_result.get("status"))
    assert (workspace / SERVICE).read_text() == FIXED_SOURCE
    assert result.legacy_result["status"] == "success"
    # s1 planned -> s2 verifies (fails) -> s1 reopened with the evidence -> s2 verifies again (its verifier passes,
    # then the terminal regression runs: two gate runs for a passing verification unit)
    assert test_gate_runs == ["s1", "s2", "s1", "s2", "s2"]
    assert sum(1 for _unit, first in model_calls if "Developer Agent" in first) == 2
    assert [c for c in model_calls if c[0] == "s2"] == []  # the verification unit never calls a model
    reopened = prompts[1]
    assert "VERIFICATION FAILURE RECOVERY" in reopened and "assert 5 == 10" in reopened  # the gate's own output
    assert "Verification unit that failed after your change: s2" in reopened
    # the first verification was the truthful LR-R1-P4 terminal of its own unit; the rerun is a new unit run
    admissions = [e.details for e in events if e.kind == "retry.verification_admission"]
    assert admissions[0]["reason_code"] == "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"


def test_an_owner_that_does_not_change_its_files_leaves_the_failure_standing(tmp_path):
    """Negative control: the reopened owner regenerates the same wrong bytes - nothing is folded, no second
    verification, the original typed failure stands; one reopen only (the bound)."""
    workspace, events, result, model_calls, prompts, test_gate_runs = _enforce(tmp_path, owner_fixes=False)
    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "s2": "failed"}
    assert (workspace / SERVICE).read_text() == BASE_SOURCE  # nothing applied to the real workspace
    assert result.legacy_result["status"] == "failed"
    assert test_gate_runs == ["s1", "s2", "s1"]  # the owner's gates ran; s2 was not verified a second time
    assert sum(1 for _unit, first in model_calls if "Developer Agent" in first) == 2  # exactly one reopen
    assert "VERIFICATION FAILURE RECOVERY" in prompts[1]
    [terminal] = [e.details for e in events if e.kind == "retry.no_progress_terminal"]
    assert terminal["reason_code"] == "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"


def test_an_owner_candidate_that_changed_but_failed_its_own_gates_is_not_folded(tmp_path):
    """Negative control (mutant m118): the reopened owner writes a different, broken candidate; its gates fail, so
    nothing is folded forward and the verification unit is not re-run, whatever landed in the plan worktree."""
    workspace, _events, result, model_calls, prompts, test_gate_runs = _enforce(tmp_path, owner_fixes=False, owner_breaks=True)
    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "s2": "failed"}
    assert test_gate_runs.count("s2") == 1 and result.legacy_result["status"] == "failed"
    assert any("VERIFICATION FAILURE RECOVERY" in p for p in prompts[1:])
    assert (workspace / SERVICE).read_text() == BASE_SOURCE


def test_an_owner_candidate_outside_its_declared_scope_is_not_folded(tmp_path):
    """Negative control (mutant m118b): the reopened owner's result names a file outside its planned_files; even
    though its planned file changed and its gates passed, nothing is folded and the verification is not re-run."""
    workspace, _events, result, _model_calls, _prompts, test_gate_runs = _enforce(
        tmp_path, owner_fixes=True, owner_escapes=True)
    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "s2": "failed"}
    assert test_gate_runs.count("s2") == 1 and result.legacy_result["status"] == "failed"
    assert (workspace / SERVICE).read_text() == BASE_SOURCE


def test_the_owner_resolver_walks_declared_dependencies_only():
    plan = EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [
            {"id": "a", "description": "a", "execution_method": "model", "planned_files": [{"path": "a.py", "action": "modify"}],
             "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "compile", "description": "c"}]},
            {"id": "v1", "description": "verify a", "execution_method": "model", "execution_role": "verification",
             "planned_files": [], "depends_on": ["a"], "relevant_global_invariant_ids": ["gi1"],
             "verification": [{"type": "tool", "tool_name": "test", "description": "t"}]},
            {"id": "v2", "description": "verify again", "execution_method": "model", "execution_role": "verification",
             "planned_files": [], "depends_on": ["v1"], "relevant_global_invariant_ids": ["gi1"],
             "verification": [{"type": "tool", "tool_name": "test", "description": "t"}]},
            {"id": "b", "description": "b", "execution_method": "model", "planned_files": [{"path": "b.py", "action": "modify"}],
             "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "compile", "description": "c"}]},
        ]})
    v2 = plan.subtask_by_id("v2")
    assert nearest_mutating_upstream_owner(plan, v2, ["a", "v1"]).id == "a"  # through the verification-only v1
    assert nearest_mutating_upstream_owner(plan, v2, ["v1"]) is None  # an owner that never completed is not reopened
    assert nearest_mutating_upstream_owner(plan, plan.subtask_by_id("b"), ["a"]) is None  # no dependency: no owner
    evidence = verification_failure_evidence({"last_failure": {"type": "test", "message": "m", "raw_output": "E assert 5 == 10"}})
    assert evidence == "E assert 5 == 10"
    assert verification_failure_evidence({"environment_failure": None}) == ""
    text = verification_owner_recovery_context(verification_subtask_id="v2", owner_id="a", owner_files=["a.py"], evidence=evidence)
    assert "Reopened owner: a" in text and '["a.py"]' in text and "assert 5 == 10" in text

"""LR-R1-P4 deterministic reproducer: a verification-only unit retried with no
possible change (CAGC-v2 80-run census: terminal in 6 runs - spring-boot-
pagesize A r1/r4/r5, B r6/r7; python-symbol-zipb B r7).

Investigation: handover/LR_R1_P4_VERIFICATION_ONLY_RETRY_INVESTIGATION.md.
Pins the CURRENT behaviour (no fix here), through the real WorkflowController
enforce loop and the real workflow; only the model transport and the two
toolchain gates are stubbed (the test-gate stub fails only while s2 runs):

- s1 (editable producer) passes its own gates;
- s2 (execution_role=verification, no planned files: DENY_ALL) runs its test
  verifier, which fails deterministically;
- s2 never calls the Developer, never re-runs s1, receives no new evidence;
- the same verification is retried: progress PROGRESS, then REPEATED_VECTOR x3
  with no changed dimension, then RETRY_NO_PROGRESS_EXHAUSTED.
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

SERVICE = "shop/service.py"
BASE_SOURCE = "def page_size():\n    return 5\n"
PRODUCED_SOURCE = "PAGE_SIZE = 5\n\n\ndef page_size():\n    return PAGE_SIZE\n"
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
             "requires": ["page_size_configured"], "relevant_global_invariant_ids": ["gi1"],
             "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]},
        ]})


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True)


def _enforce(tmp_path):
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
    test_gate_runs = []

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        model_calls.append((active.get("id"), first[:60]))
        if "File List Planner" in first:
            content = '{"files": ["%s"]}' % SERVICE
        elif "Developer Agent" in first:
            content = sentinel(SERVICE, analysis="introduce PAGE_SIZE.", content=PRODUCED_SOURCE)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    real_workflow = WorkflowEngine.run_generation_workflow

    async def run_unit(self, *args, **kwargs):
        active["id"] = kwargs.get("current_subtask_id")
        return await real_workflow(self, *args, **kwargs)

    def run_tests(*_args, **_kwargs):
        test_gate_runs.append(active.get("id"))
        return FAILING if active.get("id") == "s2" else PASSING

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
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests), \
         patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
               return_value=_plan()):
        result = asyncio.run(WorkflowController(engine).execute("Make the page size configurable", str(workspace),
                                                                migration_mode="enforce"))
    return workspace, events, result, model_calls, test_gate_runs


def test_a_failing_verification_only_unit_repeats_the_same_verification_until_no_progress_stops_it(tmp_path):
    workspace, events, result, model_calls, test_gate_runs = _enforce(tmp_path)

    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "s2": "failed"}
    # The plan's candidate is committed only at the enforce terminal: s2 failed, so nothing was applied
    # (as in every failed live run).
    assert (workspace / SERVICE).read_text() == BASE_SOURCE

    # s2: the same verifier four times, no Developer, no producer re-run, no model call at all.
    assert test_gate_runs == ["s1", "s2", "s2", "s2", "s2"]
    assert [c for c in model_calls if c[0] == "s2"] == []
    assert sum(1 for _unit, first in model_calls if "Developer Agent" in first) == 1   # s1's only

    failed = [(e.attempt, e.details.get("failure_type")) for e in events if e.kind == "attempt.failed"]
    assert failed == [(1, "test"), (2, "test"), (3, "test"), (4, "test")]
    vectors = [(e.details["classification"], e.details["changed_dimensions"])
               for e in events if e.kind == "retry.progress_vector"]
    assert [c for c, _ in vectors] == ["PROGRESS", "REPEATED_VECTOR", "REPEATED_VECTOR", "REPEATED_VECTOR"]
    assert all(dims == [] for c, dims in vectors if c == "REPEATED_VECTOR")   # nothing could change, nothing did
    [terminal] = [e.details for e in events if e.kind == "retry.no_progress_terminal"]
    assert terminal["reason_code"] == "RETRY_NO_PROGRESS_EXHAUSTED" and terminal["classification"] == "REPEATED_VECTOR"
    assert result.legacy_result["status"] == "failed"

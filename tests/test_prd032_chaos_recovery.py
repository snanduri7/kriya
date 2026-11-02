"""PRD-032 (Wave 7): cross-unit recovery characterization (deterministic).

WAVE5-O1-CONSUMER-PRODUCER-RECOVERY-001: a scripted replay of the measured Graphify shape (Wave 5 Gate E, run 20261101T174848-978ebaa3)
through the REAL enforce controller and workflow (only the model transport and the compile gate are stubbed; the test gate runs REAL pytest
on the candidate, so the failure evidence is a real pytest assertion failure):

* producer s1 (``pkg/engine.py``): a PARTIAL fix (the E case is missing), verified by compile only;
* consumer s2 (``tests/test_extract.py``, ``requires`` s1's capability): its behavioural test exposes the incomplete producer, and s2's
  write scope is the test file only.

Valid PRD-032 outcomes: (A) Kriya deterministically routes back to the producer, authority-safe; or (B) it cannot route back but FAILS CLOSED
(no false SUCCESS, no unauthorized consumer edit of the producer, bounded retries, a truthful RunRecord). This file pins what the CURRENT code does,
which is outcome B. It is a characterization, not a recovery feature: O1 stays an open production-capability issue (PRD-035 must exercise it).
"""
import asyncio
import os
import subprocess
import sys
from collections import Counter
from unittest.mock import AsyncMock, patch

from _chaos_harness import attempt_ceiling, audit_run_records, chaos, git_workspace
from _protocol_responses import sentinel

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

ENGINE = "pkg/engine.py"
TEST = "tests/test_extract.py"
ENGINE_BASE = "def extract(spec):\n    return []\n"
ENGINE_PARTIAL = "def extract(spec):\n    return [c for c in spec if c in 'AB']\n"        # the producer's incomplete fix: E is missing
ENGINE_FULL = "def extract(spec):\n    return [c for c in spec if c in 'ABE']\n"
STRONG_TEST = "from pkg import extract\n\n\ndef test_extract_all_cases():\n    assert extract('ABE') == ['A', 'B', 'E']\n"
GOAL = "Make extract() return the A, B and E cases and cover it with a test."
OUT_OF_SCOPE_ATTEMPT = 2                 # the consumer's Developer, on its second call, writes the producer's file (with a typed REDIRECT line)


def _plan():
    return EngineeringPlan.model_validate({
        "plan_id": "o1", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [
            {"id": "s1", "description": "fix extract in the engine", "execution_method": "model",
             "planned_files": [{"path": ENGINE, "action": "modify"}], "provides": ["engine_fix"],
             "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]},
            {"id": "s2", "description": "test extract", "execution_method": "model",
             "planned_files": [{"path": TEST, "action": "create"}], "requires": ["engine_fix"], "depends_on": ["s1"],
             "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]},
        ]})


class Replay:
    def __init__(self, tmp_path, producer_source):
        self.tmp_path = tmp_path
        self.producer_source = producer_source
        self.workspace = git_workspace(tmp_path, {"pkg/__init__.py": "from pkg.engine import extract\n", ENGINE: ENGINE_BASE})
        self.calls = Counter()
        self.pytest_runs = []
        self.events = []
        self.result = None

    def execute(self):
        from kriya.workflow.workflow_controller import WorkflowController

        tmp_path, producer_source = self.tmp_path, self.producer_source
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
        active = {}

        async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
            del llm, client, model, args, kwargs
            first = (system_prompt or "").splitlines()[0] if system_prompt else ""
            unit = active.get("id")
            if "File List Planner" in first:
                content = '{"files": ["%s"]}' % (ENGINE if unit == "s1" else TEST)
            elif "Developer Agent" in first:
                self.calls[unit] += 1
                if unit == "s1":
                    content = sentinel(ENGINE, analysis="the producer's fix.", content=producer_source)
                elif self.calls[unit] == OUT_OF_SCOPE_ATTEMPT:
                    content = sentinel(ENGINE, analysis="the bug is in engine.py", content=ENGINE_FULL, redirect_to=ENGINE)
                else:
                    content = sentinel(TEST, analysis="the consumer's test.", content=STRONG_TEST)
            else:
                content = "Review: Approved"
            tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
            return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                    "finish_reason": "stop", "provider_metadata": {}}

        real_workflow = WorkflowEngine.run_generation_workflow

        async def run_unit(engine_self, *args, **kwargs):
            active["id"] = kwargs.get("current_subtask_id")
            return await real_workflow(engine_self, *args, **kwargs)

        def real_pytest_gate(validator, *args, **kwargs):
            del args, kwargs
            root = validator.workspace_path
            if not os.path.isdir(os.path.join(root, "tests")):
                return {"success": True, "output": "no tests yet"}
            # no bytecode: the candidate tree is bound (verification-tree binding), a gate must not write into it
            ran = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=root, capture_output=True,
                                 text=True, check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.pytest_runs.append(ran.returncode)
            return {"success": ran.returncode == 0, "output": ran.stdout}

        real_record = GenerationState.record_event

        def record(state, event):
            self.events.append(event)
            return real_record(state, event)

        engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
        engine.planner.run = AsyncMock(return_value="structured plan")
        with patch.object(LLMClient, "_request_once", new=transport), patch.object(GenerationState, "record_event", new=record), \
             patch.object(WorkflowEngine, "run_generation_workflow", new=run_unit), \
             patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
             patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=real_pytest_gate), \
             patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", new=lambda *a, **k: {"success": True, "output": "ok"}), \
             patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
             patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_plan()):
            self.result = asyncio.run(WorkflowController(engine).execute(GOAL, str(self.workspace), migration_mode="enforce")).legacy_result
        return self

    def attempts(self, kind):
        return [event for event in self.events if event.kind == kind]


@chaos("D12")
def test_a_consumer_failure_has_no_route_to_the_producer_and_fails_closed(chaos_case, tmp_path):
    run = Replay(tmp_path, ENGINE_PARTIAL)
    chaos_case.arm()
    run.execute()
    result = run.result

    # the measured shape: the producer ran ONCE and its partial fix passed its own (compile-only) gate; the consumer then failed for real
    assert run.calls["s1"] == 1
    assert run.pytest_runs and set(run.pytest_runs) == {1}                     # every consumer-gate pytest run failed (a real assertion failure)
    failed = run.attempts("attempt.failed")
    assert len(failed) == run.calls["s2"] and len(failed) <= attempt_ceiling()  # bounded: the consumer's attempts ARE the retries
    # failure evidence + attribution evidence: every deterministic locator implicates the consumer's own test file, never the producer
    assert [event.details["likely_files"] for event in run.attempts("failure.recorded")] == [[TEST]] * len(failed)
    # recovery decision: the retry targets stay the consumer's test file; the producer is never a target, so its authority is never reopened
    targets = {target["path"] for event in run.attempts("context.retry_target_source") for target in event.details["targets"]}
    assert targets == {TEST}
    assert not [event.kind for event in run.events if "reopen" in event.kind] and result.get("plan_scope_conflict") is None
    assert run.calls["s1"] == 1                                                 # s1 was NOT re-run after s2 failed
    # the consumer's out-of-scope write of the producer file (with a typed REDIRECT line) is refused: an operation-contract failure, never applied
    assert failed[OUT_OF_SCOPE_ATTEMPT - 1].details["failure_type"] == "operation_contract"
    assert failed[OUT_OF_SCOPE_ATTEMPT - 1].details["applied"] is False
    # terminal outcome: fail closed - never SUCCESS, the real workspace untouched, the RunRecord truthful
    assert result["status"] == "failed" and result["quality_gates_passed"] is False
    assert (run.workspace / ENGINE).read_text() == ENGINE_BASE and not (run.workspace / TEST).exists()
    chaos_case.assert_tree(allowed={"memory/dependency_graph.db", "memory/knowledge_cache.db"})   # the engine's own memory stores; nothing else moved
    audit = audit_run_records(run.workspace)
    assert audit.commit_results == () and "SUCCESS" not in audit.lifecycles
    chaos_case.observe(
        "FAILED_CLOSED_NO_ROUTE_TO_PRODUCER", producer_developer_calls=run.calls["s1"], consumer_developer_calls=run.calls["s2"],
        consumer_failure_types=sorted({event.details["failure_type"] for event in failed}), implicated_files_only_the_test=True,
        producer_reopened=False, out_of_scope_producer_write="refused", **audit.evidence())


def test_negative_control_a_complete_producer_makes_the_same_plan_succeed(tmp_path):
    """Not a chaos scenario: the failure above is caused by the INCOMPLETE producer, not by the plan, the scripted world or the gates."""
    run = Replay(tmp_path, ENGINE_FULL).execute()
    assert run.result["status"] == "success" and run.result["quality_gates_passed"] is True
    assert run.calls["s1"] == 1
    assert (run.workspace / ENGINE).read_text() == ENGINE_FULL and (run.workspace / TEST).read_text() == STRONG_TEST

"""Planner Reliability R1, PR-2 (live spring-petclinic page size, run 6).

s1 (application.properties) and s2 (OwnerController) passed every gate; the
Planner-added s3 owned only OwnerControllerTests.java. Its Developer broke the
response protocol twice (text after the last block; two outcome blocks), then
answered NO CHANGE NEEDED - the existing tests suffice, the change belongs in
OwnerController. That retry was "targeted", and a NO CHANGE on targeted
retries is read as disputing an attribution - but no gate had attributed
anything: only malformed responses had failed. The answer was re-attributed
to OwnerController (outside s3's scope) and the stage stopped
NO_AUTHORIZED_REPAIR_TARGET with the correct change unapplied.

On a first attempt the same NO CHANGE goes to the gates (MEASURED, below):
after response-validity failures alone it now does too, and the stage's
declared test verification decides. A NO CHANGE after a real attributed
failure is still an attribution dispute.
"""
import asyncio
import json
import subprocess
from unittest.mock import AsyncMock, patch

from _protocol_responses import as_requested

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.workflow import WorkflowEngine

TEST = "tests/test_chunks.py"
NO_CHANGE = "FIX ANALYSIS: the existing tests already cover it; the change belongs in pkg/chunks.py.\nNO CHANGE NEEDED: x"
TEST_VERIFICATION = [{"type": "tool", "tool_name": "test", "verifier_kind": "test", "description": "run the tests",
                      "requires_runtime_execution": False}]


def _run(tmp_path, first_answer, suite_ok=True):
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
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "tests").mkdir()
    (ws / "pkg/chunks.py").write_text("def ichunked(it, n):\n    return [it]\n")
    (ws / "pkg/__init__.py").write_text("")
    (ws / TEST).write_text("from pkg.chunks import ichunked\n\n\ndef test_it():\n    assert ichunked([1], 1)\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=ws, check=True,
                       capture_output=True)
    developer = []

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, user_prompt, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": [TEST]})
        elif "Developer Agent" in first:
            developer.append(1)
            if len(developer) == 1 and first_answer == "protocol_violation":
                block = as_requested(NO_CHANGE, system_prompt, TEST)
                content = block + block  # two outcome blocks: CONFLICTING_DEVELOPER_RESPONSE
            else:
                content = as_requested(NO_CHANGE, system_prompt, TEST)
        else:
            content = "Review: Approved"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 100, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    suite_runs = []

    def run_tests(self, *args, **kwargs):
        suite_runs.append(1)
        return {"success": suite_ok, "output": "1 passed in 0.01s" if suite_ok else (
            f"{TEST}:5: AssertionError\nFAILED {TEST}::test_it - AssertionError\n1 failed in 0.01s")}

    from kriya.workflow.state import GenerationState

    failures = []
    real_record = GenerationState.record_event

    def record(state, event):
        if event.kind == "attempt.failed":
            failures.append((event.details or {}).get("failure_type"))
        return real_record(state, event)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests):
        result = asyncio.run(engine.run_generation_workflow(
            goal="make ichunked reject negative n", workspace_path=str(ws),
            predetermined_plan=f"Update {TEST}", predetermined_design="", predetermined_architect_files=[TEST],
            allowed_write_relpaths=[TEST], write_scope_mode=WriteScopeMode.ALLOWLIST,
            required_verification=TEST_VERIFICATION, approval_callback=AsyncMock(return_value=True)))
    return result, developer, suite_runs, failures


def test_a_first_attempt_no_change_is_judged_by_the_gates(tmp_path):
    result, developer, suite_runs, failures = _run(tmp_path, "no_change")
    assert len(developer) == 1 and suite_runs and result["quality_gates_passed"] is True and failures == []


def test_no_change_after_only_protocol_failures_is_judged_by_the_gates_too(tmp_path):
    result, developer, suite_runs, failures = _run(tmp_path, "protocol_violation")
    assert failures == ["operation_contract"], failures  # never turned into an attribution dispute
    assert len(developer) == 2 and suite_runs
    assert result["quality_gates_passed"] is True and result["failure_category"] is None



def test_only_positive_evidence_of_response_failures_lets_a_no_change_reach_the_gates():
    from kriya.workflow.attempt import _only_response_validity_failures
    from kriya.workflow.state import GenerationState

    state = GenerationState()
    assert not _only_response_validity_failures(state)  # unknown provenance: the dispute stands
    state.gate_outcomes = [{"type": "compile", "success": True},
                           {"type": "operation_contract", "success": False},
                           {"type": "anchored_edit", "success": False}]
    assert _only_response_validity_failures(state)  # malformed responses attribute nothing
    for attributed in ("test", "compile", "misdirected_edit", "regression_test"):
        state.gate_outcomes = [{"type": "operation_contract", "success": False},
                               {"type": attributed, "success": False}]
        assert not _only_response_validity_failures(state), attributed
    state.gate_outcomes = []
    state.budgets.last_failure_signature = ("operation_contract", ("CONFLICTING_DEVELOPER_RESPONSE",))
    assert _only_response_validity_failures(state)
    state.budgets.last_failure_signature = ("run_verification_hung", ("timeout",))
    assert not _only_response_validity_failures(state)  # the active family is a real, attributed failure

"""Live matrix, more-itertools ichunked (run fcfc10f9): the candidate passed
compile and spec compliance, then the run refused success - "REQUIRED
VERIFICATION UNRESOLVED: no authoritative passing evidence was produced for
['Verify that ichunked matches chunked ...']". The stage declared a `test`
verification, but the candidate gate ran tests only for a target test or for
test files the candidate wrote; an edit of existing source with an existing
suite ran none, so the declared evidence could never exist.

End to end through the real direct run (model scripted at the transport).
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
from kriya.workflow.workflow import WorkflowEngine

TARGET = "pkg/chunks.py"
BEFORE = "def ichunked(it, n):\n    return [it]\n"
AFTER_LINE = "    if n < 0:\n        raise ValueError(n)\n    return [it]"
TEST_VERIFICATION = [{"type": "tool", "tool_name": "test", "verifier_kind": "test",
                      "description": "Verify ichunked matches chunked", "requires_runtime_execution": False}]


def _run(tmp_path, required_verification):
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
    (ws / TARGET).write_text(BEFORE)
    (ws / "pkg/__init__.py").write_text("")
    (ws / "tests/test_chunks.py").write_text("from pkg.chunks import ichunked\n\n\ndef test_it():\n"
                                             "    assert ichunked([1], 1)\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=ws, check=True,
                       capture_output=True)

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": [TARGET]})
        elif "Developer Agent" in first:
            after = BEFORE.replace("    return [it]", AFTER_LINE)
            answer = (f"FIX ANALYSIS: reject negative n.\nSEARCH:\n    return [it]\nREPLACE:\n{AFTER_LINE}\n"
                      if "<<<KRIYA:EDIT" in (system_prompt or "") else f"FIX ANALYSIS: reject negative n.\n"
                      f"FILE CONTENT:\n{after}")
            content = as_requested(answer, system_prompt, TARGET)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    suite_runs = []

    def run_tests(self, *args, **kwargs):
        suite_runs.append(kwargs.get("target_test"))
        return {"success": True, "output": "1 passed in 0.01s"}

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests):
        result = asyncio.run(engine.run_generation_workflow(
            goal="make ichunked reject negative n", workspace_path=str(ws),
            predetermined_plan=f"Repair {TARGET}", predetermined_design="",
            predetermined_architect_files=[TARGET], required_verification=required_verification,
            approval_callback=AsyncMock(return_value=True)))
    return ws, result, suite_runs


def test_a_declared_test_verification_runs_the_repository_suite_and_is_proven(tmp_path):
    ws, result, suite_runs = _run(tmp_path, TEST_VERIFICATION)
    # The candidate gate runs the suite for the declared verification, then the terminal regression runs it.
    assert len(suite_runs) == 2, suite_runs
    assert result["quality_gates_passed"] is True and result["failure_category"] is None
    [evidence] = result["verification_results"]
    assert evidence["passed"] is True and evidence["source"] == "authoritative_gate_outcome"
    assert AFTER_LINE in (ws / TARGET).read_text()


def test_without_a_declared_test_verification_the_candidate_gate_is_unchanged(tmp_path):
    _, result, suite_runs = _run(tmp_path, [])
    assert len(suite_runs) == 1 and result["quality_gates_passed"] is True  # only the terminal regression, as before

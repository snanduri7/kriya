"""PRD-015: the normalized completion result at the LLM boundary.

Captured response shapes (Qwen <think> wrappers, Ollama's separate reasoning
field, native / Hermes / Qwen-XML tool calls, malformed variants) go through
the real LLMClient with only the SDK transport mocked. The end-to-end tests
run the real workflow, gates and commit seam: a truncated Developer answer
that still compiles is never committed."""
import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
from _milestone_proof_harness import _milestone, _run, git_workspace  # noqa: F401 - pytest fixture

from kriya.agents.agent import OUTPUT_TRUNCATED, DeveloperAgent
from kriya.config import AppConfig
from kriya.control.persistence import scan_run_records
from kriya.control.run_record import RunLifecycle
from kriya.core.completion import (
    CompletionStatus,
    parse_textual_tool_calls,
    split_reasoning,
    structured_parse_status,
)
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.failure import Failure
from kriya.workflow.workflow import WorkflowEngine


def _response(content="", finish_reason="stop", reasoning=None, tool_calls=None, usage=(12, 5)):
    response = MagicMock()
    message = MagicMock()
    message.content = content
    message.reasoning = reasoning
    message.reasoning_content = None
    message.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason
    response.choices = [choice]
    response.id = "chatcmpl-1"
    response.model = "qwen3-coder:30b"
    response.system_fingerprint = "fp_ollama"
    if usage:
        response.usage = MagicMock(prompt_tokens=usage[0], completion_tokens=usage[1])
    else:
        response.usage = None
    return response


def _native_call(name, arguments):
    call = MagicMock()
    call.id = "call_1"
    call.function.name = name
    call.function.arguments = arguments
    return call


def _client(**config):
    cfg = AppConfig()
    for key, value in config.items():
        setattr(cfg.llm, key, value)
    return LLMClient(cfg)


async def _result(llm, response, **kwargs):
    side_effect = response if isinstance(response, list) else None
    mock = AsyncMock(side_effect=side_effect) if side_effect else AsyncMock(return_value=response)
    with patch.object(llm.client.chat.completions, "create", new=mock):
        return await llm.complete_result("system", "user", **kwargs)


async def _tools_result(llm, response):
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(return_value=response)):
        return await llm.complete_with_tools_result([{"role": "user", "content": "hi"}], [{"type": "function"}])


# --- status classification ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_complete_answer_is_ok_with_measured_usage_and_provider_ids():
    result = await _result(_client(), _response("hello"))
    assert result.status is CompletionStatus.OK and result.ok
    assert (result.prompt_tokens, result.completion_tokens, result.tokens_estimated) == (12, 5, False)
    assert result.provider_metadata == {"id": "chatcmpl-1", "model": "qwen3-coder:30b",
                                        "system_fingerprint": "fp_ollama"}
    assert result.finish_reason == "stop" and result.backend_status == "ok"


@pytest.mark.asyncio
async def test_truncation_is_never_ok_even_when_the_text_looks_complete():
    result = await _result(_client(), _response("def add(a, b):\n    return a + b\n", finish_reason="length"))
    assert result.status is CompletionStatus.OUTPUT_TRUNCATED and result.truncated
    assert result.to_telemetry()["output_truncated"] is True


@pytest.mark.asyncio
async def test_empty_content_is_explicit():
    result = await _result(_client(), _response(""))
    assert result.status is CompletionStatus.EMPTY_CONTENT


@pytest.mark.asyncio
async def test_malformed_structured_output_is_explicit_but_the_text_is_kept_for_recovery():
    result = await _result(_client(), [_response("Sure! Here: {oops"), _response("Sure! Here: {oops")],
                           json_mode=True)
    assert result.status is CompletionStatus.MALFORMED_STRUCTURED_OUTPUT
    assert result.parser_status == "malformed" and result.content == "Sure! Here: {oops"


@pytest.mark.asyncio
async def test_a_fenced_json_answer_is_valid_structured_output():
    result = await _result(_client(), _response('```json\n{"a": 1}\n```'), json_mode=True)
    assert result.status is CompletionStatus.OK and result.parser_status == "ok"


@pytest.mark.asyncio
async def test_a_backend_error_is_a_result_and_the_compat_api_reraises_the_original():
    llm = _client()
    error = openai.APIConnectionError(request=MagicMock())
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(side_effect=error)):
        result = await llm.complete_result("s", "u")
        assert result.status is CompletionStatus.BACKEND_ERROR and result.error is error
        assert result.content == "" and "APIConnectionError" in result.backend_error
        with pytest.raises(openai.APIConnectionError):
            await llm.complete("s", "u")
    assert llm.last_call_metrics is None
    assert llm.last_completion.status is CompletionStatus.BACKEND_ERROR


@pytest.mark.asyncio
async def test_a_timeout_is_explicit():
    llm = _client()
    timeout = openai.APITimeoutError(request=MagicMock())
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(side_effect=timeout)):
        result = await llm.complete_result("s", "u")
    assert result.status is CompletionStatus.TIMEOUT and result.error is timeout


@pytest.mark.asyncio
async def test_cancellation_is_recorded_and_never_swallowed():
    llm = _client()
    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(30)

    with patch.object(llm.client.chat.completions, "create", new=slow):
        task = asyncio.ensure_future(llm.complete_result("s", "u"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert llm.last_completion.status is CompletionStatus.CANCELLED


# --- reasoning -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qwen_think_wrapper_is_hidden_and_recorded_not_returned():
    result = await _result(_client(), _response("<think>\nplan the answer\n</think>\n\nThe answer is 42."))
    assert result.content == "The answer is 42."
    assert result.reasoning_present and result.reasoning_source == "think_tags"
    assert "<think>" not in result.content


@pytest.mark.asyncio
async def test_ollama_separate_reasoning_field_is_recorded_not_returned():
    result = await _result(_client(), _response("42", reasoning="Let me think step by step..."))
    assert result.content == "42"
    assert result.reasoning_source == "reasoning_field" and result.reasoning_chars == len("Let me think step by step...")


@pytest.mark.asyncio
async def test_reasoning_cut_off_before_it_closed_is_empty_or_truncated_never_content():
    result = await _result(_client(), _response("<think>still thinking about it", finish_reason="length"))
    assert result.content == "" and result.status is CompletionStatus.OUTPUT_TRUNCATED


def test_think_tags_inside_generated_source_are_left_alone_for_non_reasoning_models():
    source = 'THINK_END = "</think>"\nprint(THINK_END)\n'
    assert split_reasoning(source) == (source.strip(), 0)
    assert split_reasoning("x = 1\n<think>not reasoning</think>\n")[1] == 0
    visible, hidden = split_reasoning("x = 1\n<think>r</think>\ny = 2", anywhere=True)
    assert visible == "x = 1\n\ny = 2" and hidden == len("<think>r</think>")


# --- tool calls -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_native_tool_calls_are_normalized():
    llm = _client()
    llm.config.llm.capabilities.native_tool_calls = True
    result = await _tools_result(llm, _response(None, tool_calls=[_native_call("get_weather", '{"city": "Paris"}')]))
    assert result.status is CompletionStatus.OK
    assert result.tool_calls == [{"id": "call_1", "name": "get_weather", "arguments": {"city": "Paris"},
                                  "source": "native"}]


@pytest.mark.asyncio
async def test_hermes_text_tool_call_is_recovered_at_the_boundary():
    llm = _client()
    llm.config.llm.capabilities.native_tool_calls = True
    text = 'Calling now.\n<tool_call>\n{"name": "get_weather", "arguments": {"city": "Tokyo"}}\n</tool_call>'
    result = await _tools_result(llm, _response(text))
    assert result.tool_calls[0]["name"] == "get_weather" and result.tool_calls[0]["source"] == "hermes"
    assert result.tool_calls[0]["arguments"] == {"city": "Tokyo"}
    assert result.content == "Calling now."


@pytest.mark.asyncio
async def test_qwen_xml_text_tool_call_is_recovered_at_the_boundary():
    llm = _client()
    llm.config.llm.capabilities.native_tool_calls = True
    text = ("<tool_call>\n<function=save_note>\n<parameter=text>\nhello\n</parameter>\n"
            "<parameter=count>\n3\n</parameter>\n</function>\n</tool_call>")
    result = await _tools_result(llm, _response(text))
    assert result.tool_calls[0]["source"] == "qwen_xml"
    assert result.tool_calls[0]["arguments"] == {"text": "hello", "count": 3}


def test_a_malformed_textual_tool_call_is_reported_never_executed():
    calls, remaining, errors = parse_textual_tool_calls('<tool_call>{"name": "x", "arguments": </tool_call>')
    assert calls == [] and errors and "<tool_call>" in remaining


@pytest.mark.asyncio
async def test_malformed_native_arguments_keep_the_established_empty_object_contract():
    llm = _client()
    llm.config.llm.capabilities.native_tool_calls = True
    result = await _tools_result(llm, _response(None, tool_calls=[_native_call("get_weather", "{not json")]))
    assert result.tool_calls[0]["arguments"] == {} and result.parser_status == "malformed_tool_arguments"
    with patch.object(llm.client.chat.completions, "create",
                      new=AsyncMock(return_value=_response(None, tool_calls=[_native_call("get_weather", "{x")]))):
        legacy = await llm.complete_with_tools([{"role": "user", "content": "x"}], [])
    assert legacy == {"content": "", "tool_calls": [{"id": "call_1", "name": "get_weather", "arguments": {}}]}


def test_structured_parse_status():
    assert structured_parse_status('{"a": 1}') == "ok"
    assert structured_parse_status("[1, 2]") == "ok"
    assert structured_parse_status("text [1, 2]") == "malformed"


# --- telemetry --------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_telemetry_is_persisted_and_secret_free():
    llm = _client(api_key="sk-very-secret-value")
    await _result(llm, _response("hello", reasoning="hidden chain of thought"))
    telemetry = llm.last_call_metrics["completion"]
    blob = str(telemetry)
    assert "sk-very-secret-value" not in blob and "hidden chain of thought" not in blob
    assert telemetry["status"] == "OK" and telemetry["reasoning_present"] is True
    assert llm.last_call_metrics["protocol_status"] == "OK"


# --- the Developer consumes the normalized state ------------------------------------------------

@pytest.mark.asyncio
async def test_developer_full_file_path_rejects_a_truncated_answer():
    from kriya.workflow.operations import CodeOperation

    llm = _client()
    with patch.object(llm.client.chat.completions, "create",
                      new=AsyncMock(return_value=_response("def add(a, b):\n    return a + b\n", "length"))):
        [entry] = await DeveloperAgent("developer", llm).run_generation(
            "Task", "Design", "Existing code", known_target_files=["calc.py"],
            operation_by_file={"calc.py": CodeOperation.REPAIR_WITH_FULL_FILE},
        )
    assert entry["content"] is None
    assert entry["protocol_reason_code"] == OUTPUT_TRUNCATED
    assert entry["protocol_error"].startswith(OUTPUT_TRUNCATED)


@pytest.mark.asyncio
async def test_developer_batch_file_list_rejects_a_truncated_answer():
    llm = _client()
    truncated = '[{"filepath": "a.py", "content": "x = 1\n"}, {"filepath": "b.py", "content": "y ='
    with patch.object(llm.client.chat.completions, "create",
                      new=AsyncMock(return_value=_response(truncated, "length"))):
        with pytest.raises(ValueError, match=OUTPUT_TRUNCATED):
            await DeveloperAgent("developer", llm).run_generation("Task", "Design", "Existing code")


# --- end to end: the real workflow never commits truncated content ------------------------------

CALC = "def add(a, b):\n    return a + b\n"
TRUNCATED_BUT_VALID = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"


def _role_llm(cfg, developer_finish_reason, developer_calls):
    """Real LLMClient; only the per-request transport is scripted by role."""
    llm = LLMClient(cfg)

    async def request_once(client, model, system_prompt, user_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        finish, content = "stop", "Review: Approved"
        if "Developer Agent" in first:
            developer_calls.append(user_prompt)
            finish, content = developer_finish_reason, TRUNCATED_BUT_VALID
        elif "File List Planner" in first:
            content = '["calc.py"]'
        elif "Planner Agent" in first:
            content = "Step 1: add sub(a, b) to calc.py"
        elif "Architect Agent" in first:
            content = "Design: calc.py gains sub(a, b)"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 100, "completion_tokens": 50,
                "finish_reason": finish, "provider_metadata": {}}

    llm._request_once = request_once
    return llm


@pytest.fixture
def calc_workspace(git_workspace):  # noqa: F811
    (git_workspace / "calc.py").write_text(CALC)
    subprocess.run(["git", "add", "calc.py"], cwd=git_workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "calc"], cwd=git_workspace, check=True)
    return git_workspace


@pytest.fixture
def failures(monkeypatch):
    recorded = []
    original = Failure.to_gate_outcome

    def recording(self):
        recorded.append(self)
        return original(self)

    monkeypatch.setattr(Failure, "to_gate_outcome", recording)
    return recorded


def _engine(finish_reason, developer_calls):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    engine = WorkflowEngine(Kernel(config=cfg), _role_llm(cfg, finish_reason, developer_calls))
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    return engine


def _committed(workspace):
    return [
        cycle for record in scan_run_records(str(workspace)).records for cycle in record.commits
        if cycle.get("result") == "COMMITTED"
    ]


def test_direct_generate_never_commits_a_truncated_file(calc_workspace, failures):
    developer_calls = []
    result = asyncio.run(_engine("length", developer_calls).run_generation_workflow(
        goal="add sub(a, b) to calc.py", workspace_path=str(calc_workspace),
    ))
    assert developer_calls, "the Developer was never asked"
    assert not result["quality_gates_passed"]
    assert Path(calc_workspace, "calc.py").read_text() == CALC
    assert any((f.diagnostics or {}).get("reason_code") == OUTPUT_TRUNCATED for f in failures), \
        [(f.type, f.diagnostics) for f in failures]
    assert _committed(calc_workspace) == []
    for record in scan_run_records(str(calc_workspace)).records:
        assert record.lifecycle_state is not RunLifecycle.SUCCESS


def test_milestone_sequence_never_commits_a_truncated_file(calc_workspace, failures):
    developer_calls = []
    result, state = _run(calc_workspace, [_milestone("M1")], _engine("length", developer_calls))
    assert developer_calls and result["status"] != "success"
    assert Path(calc_workspace, "calc.py").read_text() == CALC
    assert _committed(calc_workspace) == []


def test_negative_control_the_same_answer_completed_normally_is_committed(calc_workspace, failures):
    """Proves the scenario reaches the commit seam: only finish_reason
    differs from the truncation test."""
    developer_calls = []
    result = asyncio.run(_engine("stop", developer_calls).run_generation_workflow(
        goal="add sub(a, b) to calc.py", workspace_path=str(calc_workspace),
    ))
    assert result["quality_gates_passed"], result.get("error")
    assert Path(calc_workspace, "calc.py").read_text().strip() == TRUNCATED_BUT_VALID.strip()
    assert _committed(calc_workspace)

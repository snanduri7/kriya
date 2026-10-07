"""LR-R1-M1.5: model.request / model.response / model.result at LLMClient
(design §5.3, D3; tests T9 messages part, T6 call paths, T4 call faults).

Every logical call is one call scope; every physical provider request is one
wire with its own request/response pair; a call refused before any request
still records the refused prompt; errors and cancellation are recorded and
propagate unchanged.
"""
import asyncio
import json

import pytest
from _fake_inference_runtime import FakeRuntimeAdapter, FakeServerError

from kriya.config import AppConfig
from kriya.config.config import ModelCapabilities
from kriya.core import model_runtime
from kriya.core.attempt_evidence import reader, scope
from kriya.core.inference_runtime import ChatResponse, register_runtime_adapter, unregister_runtime_adapter
from kriya.core.llm import LLMClient, StructuredOutputUnsupportedError
from kriya.core.role_metrics import model_role
from kriya.core.state_paths import ENV_STATE_DIR


class _BodyRuntime(FakeRuntimeAdapter):
    """A fake that declares its exact body, like the shipped adapters."""

    def wire_payload(self, request, *, stream):
        return {"model": request.model, "messages": request.messages, "stream": stream,
                "max_tokens": request.max_tokens, "format": (request.response_format or {}).get("type")}


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    adapter = _BodyRuntime("m1-fake")
    register_runtime_adapter(adapter)
    model_runtime.clear_model_runtime_cache()
    yield adapter
    unregister_runtime_adapter(adapter.name)
    model_runtime.clear_model_runtime_cache()


def _config(capture="full"):
    cfg = AppConfig()
    cfg.llm.model = "primary:1"
    cfg.llm.inference_runtime = "m1-fake"
    cfg.llm.extra_body = {"reasoning_effort": "none"}
    cfg.llm.context_window = 8192
    cfg.llm.max_tokens = 1024
    cfg.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    cfg.llm_chain = []
    cfg.evidence.attempt_recorder.capture = capture
    return cfg


class _Context:
    def __init__(self, run_id):
        self.run_id = run_id


def _recorded(cfg, coroutine_factory, run_id="run-calls"):
    """Run coroutine_factory(llm) under an open store; returns (result or
    exception, records)."""
    llm = LLMClient(cfg)
    outcome = None
    with scope.run_scope(_Context(run_id)):
        scope.ensure_store(cfg)

        async def body():
            with model_role("developer"):
                return await coroutine_factory(llm)
        try:
            outcome = asyncio.run(body())
        except BaseException as error:  # the test inspects it
            outcome = error
        scope._RUN.get().writer.seal("test")
    run = reader.open_run(__import__("os").environ[ENV_STATE_DIR], run_id)
    return outcome, run, [r for r in run.records() if r["kind"].startswith("model.")]


def test_one_call_one_wire_records_exact_messages_and_verbatim_content(runtime):
    runtime.replies = [ChatResponse(content="answer", raw_content="  answer \n", reasoning_text="why",
                                    reasoning_chars=3, prompt_tokens=10, completion_tokens=2, finish_reason="stop")]
    result, run, records = _recorded(_config(), lambda llm: llm.complete_result("SYSTEM", "USER"))
    assert result.content == "answer"
    assert [r["kind"] for r in records] == ["model.request", "model.response", "model.result"]
    request, response, outcome = records
    assert {r["call_seq"] for r in records} == {1} and request["wire_seq"] == response["wire_seq"] == 1
    assert request["role"] == "developer" and request["phase"] == "developer"   # DEV-1: role-derived outside an attempt
    sent = runtime.requests[0]
    assert json.loads(run.blob(request["blobs"]["messages"])) == sent.messages
    assert json.loads(run.blob(request["blobs"]["wire_body"])) == runtime.wire_payload(sent, stream=False)
    assert request["payload"]["dispatched"] is True and request["payload"]["wire_reason"] == "initial"
    assert run.blob(response["blobs"]["content"]) == b"  answer \n"          # verbatim, before any trim
    assert response["payload"]["reasoning_field"]["chars"] == 3
    assert "reasoning" not in response["blobs"]                              # D3: digest only by default
    assert outcome["payload"]["status"] == "OK" and outcome["payload"]["model"] == "primary:1"


def test_full_with_reasoning_also_keeps_the_reasoning_text(runtime):
    runtime.replies = [ChatResponse(content="a", raw_content="a", reasoning_text="secret chain", finish_reason="stop",
                                    prompt_tokens=5, completion_tokens=1)]
    _result, run, records = _recorded(_config("full_with_reasoning"), lambda llm: llm.complete_result("s", "u"))
    response = [r for r in records if r["kind"] == "model.response"][0]
    assert run.blob(response["blobs"]["reasoning"]) == b"secret chain"


def test_an_empty_json_answer_is_two_wires_in_one_call(runtime):
    runtime.replies = [ChatResponse(content="", raw_content="", prompt_tokens=5, completion_tokens=1,
                                    finish_reason="stop"),
                       ChatResponse(content='{"ok": 1}', raw_content='{"ok": 1}', prompt_tokens=5,
                                    completion_tokens=3, finish_reason="stop")]
    result, _run, records = _recorded(_config(), lambda llm: llm.complete_result("s", "u", json_mode=True))
    assert result.content == '{"ok": 1}'
    requests = [r for r in records if r["kind"] == "model.request"]
    assert [(r["call_seq"], r["wire_seq"], r["payload"]["wire_reason"]) for r in requests] == [
        (1, 1, "initial"), (1, 2, "empty_content_floor")]
    assert len(runtime.requests) == 2
    assert [r["kind"] for r in records].count("model.result") == 1


def test_a_call_refused_before_any_request_still_records_the_prompt(runtime):
    outcome, run, records = _recorded(
        _config(), lambda llm: llm.complete_result("S", "U", response_schema={"type": "object"}))
    assert isinstance(outcome, StructuredOutputUnsupportedError)
    [request] = records
    assert request["payload"]["dispatched"] is False
    assert request["payload"]["refusal_type"] == "StructuredOutputUnsupportedError"
    assert json.loads(run.blob(request["blobs"]["messages"])) == [
        {"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert runtime.requests == []


def test_a_backend_error_is_recorded_and_returned_unchanged(runtime):
    runtime.replies = [FakeServerError("boom")]
    result, _run, records = _recorded(_config(), lambda llm: llm.complete_result("s", "u"))
    assert result.status.value == "BACKEND_ERROR"
    kinds = [r["kind"] for r in records]
    assert kinds == ["model.request", "model.response", "model.result"]
    assert records[1]["payload"]["error_type"] == "FakeServerError" and records[1]["payload"]["dispatched"] is True
    assert records[2]["payload"]["status"] == "BACKEND_ERROR"


def test_cancellation_is_recorded_and_still_propagates(runtime):
    runtime.replies = [asyncio.CancelledError()]
    outcome, _run, records = _recorded(_config(), lambda llm: llm.complete_result("s", "u"))
    assert isinstance(outcome, asyncio.CancelledError)
    response = [r for r in records if r["kind"] == "model.response"][0]
    assert response["payload"]["cancelled"] is True
    assert [r for r in records if r["kind"] == "model.result"][0]["payload"]["status"] == "CANCELLED"


def test_digest_only_records_no_prompt_or_response_text(runtime):
    runtime.replies = ["CANARY-ANSWER-55e1"]
    _result, run, records = _recorded(_config("digest_only"),
                                      lambda llm: llm.complete_result("CANARY-SYSTEM-1d0", "u"))
    assert all(r["blobs"] == {} for r in records)
    import pathlib
    for path in pathlib.Path(run.directory).rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert b"CANARY-ANSWER-55e1" not in data and b"CANARY-SYSTEM-1d0" not in data


def test_tool_calls_are_one_wire_with_the_raw_tool_calls(runtime):
    from _fake_inference_runtime import tool_reply

    runtime.replies = [tool_reply("find_symbol", '{"symbol": "x"}')]
    tools = [{"type": "function", "function": {"name": "find_symbol", "parameters": {"type": "object"}}}]
    _result, run, records = _recorded(_config(), lambda llm: llm.complete_with_tools_result(
        [{"role": "user", "content": "find x"}], tools))
    request, response = [r for r in records if r["kind"] in ("model.request", "model.response")]
    assert json.loads(run.blob(request["blobs"]["tools"])) == tools
    assert json.loads(run.blob(response["blobs"]["tool_calls"])) == [
        {"id": "call-1", "name": "find_symbol", "arguments": '{"symbol": "x"}'}]

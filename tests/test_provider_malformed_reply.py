"""E.1 of the consolidated addendum #3: a provider reply that is structurally unusable is a retryable REQUEST failure at the provider
trust boundary - never a raw Python structural exception that the attempt classifier would read as a Kriya coding defect.

MEASURED at 1dff900 (and re-run by the owner's orchestrator): the real openai SDK and Kriya's runtime adapters, against a local stub
answering HTTP 200 with a malformed body, raised a raw AttributeError (message null or missing, a streamed chunk without delta, an
Ollama-native message or body that is not an object), TypeError (choices null / an error body with HTTP 200) or IndexError (empty
choices). ``classify_error`` labelled each REQUEST, ``LLMClient.complete`` re-raised the exception raw, and - once AttributeError
and NameError joined INTERNAL_FRAMEWORK_ERROR_TYPES - a transient malformed reply that used to cost one retry became a hard
"INTERNAL KRIYA ERROR" stop that also foreclosed fallback models.

The correction is at the narrow decode boundary, where untrusted provider structure becomes Kriya's ChatResponse: the required shape is
checked THERE and a malformed reply raises the project's established malformed-response signal (a ValueError, the one the classifier's
set deliberately excludes), naming what was received. Nothing wraps a whole method, so a genuine Kriya coding error inside an adapter
is still a raw AttributeError/TypeError and still INTERNAL.

Production-shaped: the real AsyncOpenAI client and the real adapters against a local stdlib HTTP stub; nothing is mocked in the
decoding path.
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError, AsyncOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_s19e_honest_refusal import (  # noqa: E402  # pylint: disable=wrong-import-position
    GOOD,
    _isolated_git,  # noqa: F401  # autouse fixture of that module, re-exported so it applies here
    _run,
    _scripted_engine,
    _workspace,
)

from kriya.core.inference_runtime import ChatRequest, RuntimeErrorKind  # noqa: E402
from kriya.core.llm import LLMClient  # noqa: E402
from kriya.core.model_runtime import OllamaNativeRuntimeAdapter, OllamaRuntimeAdapter  # noqa: E402
from kriya.workflow.recovery_coordinator import classify_attempt_exception  # noqa: E402

TOOLS = [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}}]


def _sse(*chunks) -> str:
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"


def _oai_chunk(**choice) -> dict:
    return {"id": "x", "object": "chat.completion.chunk", "model": "m", "choices": [{"index": 0, **choice}]}


def _oai(**choice) -> str:
    return json.dumps({"id": "x", "object": "chat.completion", "model": "m", "choices": [{"index": 0, "finish_reason": "stop", **choice}]})


def _ndjson(*lines) -> str:
    return "".join(json.dumps(line) + "\n" for line in lines)


# name -> (adapter, mode, wire body).  mode: "complete" | "stream" | "tools"
MALFORMED = {
    "oai_message_null": ("oai", "complete", _oai(message=None)),
    "oai_message_missing": ("oai", "complete", json.dumps({"id": "x", "object": "chat.completion", "model": "m",
                                                           "choices": [{"index": 0, "finish_reason": "length"}]})),
    "oai_choices_null": ("oai", "complete", json.dumps({"id": "x", "object": "chat.completion", "model": "m", "choices": None})),
    "oai_choices_empty": ("oai", "complete", json.dumps({"id": "x", "object": "chat.completion", "model": "m", "choices": []})),
    "oai_error_body_200": ("oai", "complete", json.dumps({"error": {"message": "model is loading", "type": "server_error"}})),
    "oai_stream_chunk_without_delta": ("oai", "stream", _sse(_oai_chunk(delta={"content": "hi"}), _oai_chunk(finish_reason="stop"))),
    "oai_tools_message_null": ("oai", "tools", _oai(message=None)),
    "oai_tools_choices_empty": ("oai", "tools", json.dumps({"id": "x", "object": "chat.completion", "model": "m", "choices": []})),
    "oai_tools_call_without_function": ("oai", "tools", _oai(message={"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function"}]})),
    "native_tools_call_not_an_object": ("native", "tools", json.dumps({"model": "m", "message": {"content": "", "tool_calls": ["oops"]},
                                                                      "done": True})),
    "native_tools_calls_a_number": ("native", "tools", json.dumps({"model": "m", "message": {"content": "", "tool_calls": 5},
                                                                  "done": True})),
    "native_tools_calls_not_a_list": ("native", "tools", json.dumps({"model": "m", "message": {"content": "", "tool_calls": "oops"},
                                                                    "done": True})),
    "native_message_string": ("native", "complete", json.dumps({"model": "m", "message": "model is loading", "done": True})),
    "native_body_list": ("native", "complete", json.dumps([{"message": {"content": "x"}}])),
    "native_stream_non_object_line": ("native", "stream", _ndjson({"model": "m", "message": {"content": "hi"}, "done": False}, "keep-alive")),
    "native_stream_message_string": ("native", "stream", _ndjson({"model": "m", "message": "oops", "done": False})),
    "native_tools_message_string": ("native", "tools", json.dumps({"model": "m", "message": "model is loading", "done": True})),
    # Addendum #4: the checks cover the TYPE of every field the decoders read, not only null / non-object. Each of these raised a raw
    # AttributeError (INTERNAL, no retry; retried at 72d9182) or TypeError / KeyError (INTERNAL at every revision) before.
    "oai_message_string": ("oai", "complete", _oai(message="model is loading")),
    "oai_message_number": ("oai", "complete", _oai(message=5)),
    "oai_content_parts_list": ("oai", "complete", _oai(message={"role": "assistant", "content": [{"type": "text", "text": "x"}]})),
    "oai_content_number": ("oai", "complete", _oai(message={"role": "assistant", "content": 5})),
    "oai_choices_object": ("oai", "complete", json.dumps({"id": "x", "object": "chat.completion", "model": "m",
                                                          "choices": {"a": {"message": {"content": "x"}}}})),
    "oai_stream_delta_string": ("oai", "stream", _sse(_oai_chunk(delta="hi"))),
    "oai_stream_delta_content_number": ("oai", "stream", _sse(_oai_chunk(delta={"content": 5}))),
    "oai_stream_choices_object": ("oai", "stream", _sse({"id": "x", "object": "chat.completion.chunk", "model": "m",
                                                          "choices": {"a": {"delta": {"content": "x"}}}})),
    "oai_tools_message_string": ("oai", "tools", _oai(message="loading")),
    "oai_tools_function_string": ("oai", "tools", _oai(message={"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": "oops"}]})),
    "oai_tools_content_parts_list": ("oai", "tools", _oai(message={"role": "assistant", "content": [{"type": "text", "text": "x"}]})),
    "oai_tools_calls_number": ("oai", "tools", _oai(message={"role": "assistant", "content": None, "tool_calls": 5})),
    "oai_tools_calls_true": ("oai", "tools", _oai(message={"role": "assistant", "content": None, "tool_calls": True})),
    "oai_stream_chunk_not_an_object": ("oai", "stream", "data: \"hello\"\n\ndata: [DONE]\n\n"),
    "oai_stream_chunk_number_between_valid": ("oai", "stream", "data: " + json.dumps(_oai_chunk(delta={"content": "hi"})) + "\n\ndata: 7\n\ndata: [DONE]\n\n"),
    "native_content_number": ("native", "complete", json.dumps({"model": "m", "message": {"content": 5}, "done": True})),
    "native_content_list": ("native", "complete", json.dumps({"model": "m", "message": {"content": ["a"]}, "done": True})),
    "native_thinking_number": ("native", "complete", json.dumps({"model": "m", "message": {"content": "x", "thinking": 5}, "done": True})),
    "native_stream_content_number": ("native", "stream", _ndjson({"model": "m", "message": {"content": 5}, "done": False})),
    "native_stream_thinking_list": ("native", "stream", _ndjson({"model": "m", "message": {"thinking": ["a"]}, "done": False})),
    "native_tools_content_number": ("native", "tools", json.dumps({"model": "m", "message": {"content": 5}, "done": True})),
}
VALID = {  # -> expected content
    "oai_valid": ("oai", "complete", _oai(message={"role": "assistant", "content": "hello"}), "hello"),
    "oai_valid_null_content": ("oai", "complete", _oai(message={"role": "assistant", "content": None}), ""),
    "oai_valid_stream": ("oai", "stream", _sse(_oai_chunk(delta={"content": "hel"}), _oai_chunk(delta={"content": "lo"}),
                                               _oai_chunk(delta={}, finish_reason="stop"),
                                               {"id": "x", "object": "chat.completion.chunk", "model": "m", "choices": [],
                                                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}), "hello"),
    "oai_valid_tools": ("oai", "tools", _oai(message={"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]}), ""),
    "native_valid": ("native", "complete", json.dumps({"model": "m", "message": {"role": "assistant", "content": "hello"},
                                                       "done": True, "done_reason": "stop"}), "hello"),
    "native_valid_stream": ("native", "stream", _ndjson({"model": "m", "message": {"content": "hel"}, "done": False},
                                                        {"model": "m", "message": {"content": "lo"}, "done": True,
                                                         "done_reason": "stop"}), "hello"),
    "native_valid_tools": ("native", "tools", json.dumps({"model": "m", "message": {"content": "", "tool_calls": [
        {"function": {"name": "f", "arguments": {}}}]}, "done": True}), ""),
    # Valid shapes the type checks must keep accepting (the E.1 lesson: a stricter decoder must not reject what worked).
    "oai_valid_finish_reason_null": ("oai", "complete", _oai(finish_reason=None, message={"role": "assistant", "content": "hi"}), "hi"),
    "oai_valid_empty_parts_list": ("oai", "complete", _oai(message={"role": "assistant", "content": []}), ""),
    "oai_valid_reasoning_only": ("oai", "complete", _oai(message={"role": "assistant", "content": "", "reasoning": "thinking hard"}), ""),
    "oai_valid_stream_null_content_and_empty_choices": ("oai", "stream", _sse(
        {"id": "x", "object": "chat.completion.chunk", "model": "m", "choices": []},
        _oai_chunk(delta={"role": "assistant", "content": None}), _oai_chunk(delta={"content": "ok"}),
        _oai_chunk(delta={}, finish_reason=None), _oai_chunk(delta={}, finish_reason="stop")), "ok"),
    "oai_valid_tools_reasoning_only": ("oai", "tools", _oai(message={"role": "assistant", "content": None,
                                                                      "reasoning": "thinking hard"}), ""),
    "native_valid_message_null": ("native", "complete", json.dumps({"model": "m", "message": None, "done": True}), ""),
    "native_valid_message_missing": ("native", "complete", json.dumps({"model": "m", "done": True}), ""),
    "native_valid_thinking_only": ("native", "complete", json.dumps({"model": "m", "message": {"content": "", "thinking": "hm"},
                                                                     "done": True}), ""),
    "native_valid_stream_final_line_without_message": ("native", "stream", _ndjson(
        {"model": "m", "message": {"content": "hello"}, "done": False}, {"model": "m", "done": True, "done_reason": "stop"}), "hello"),
    "native_valid_tools_message_null": ("native", "tools", json.dumps({"model": "m", "message": None, "done": True}), ""),
}
ALL = {**{k: v for k, v in MALFORMED.items()}, **{k: v[:3] for k, v in VALID.items()}}
CURRENT = {"name": None}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        adapter, mode, body = ALL[CURRENT["name"]]
        data = body.encode()
        self.send_response(200)
        streaming = mode == "stream"
        content_type = ("text/event-stream" if adapter == "oai" else "application/x-ndjson") if streaming else "application/json"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def stub_port():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def _adapter(kind: str):
    return OllamaNativeRuntimeAdapter() if kind == "native" else OllamaRuntimeAdapter()


async def _call(name: str, port: int):
    """The real adapter against the real SDK client; returns the ChatResponse or raises exactly what the adapter raises."""
    CURRENT["name"] = name
    kind, mode, _body = ALL[name]
    client = AsyncOpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="k", max_retries=0)
    request = ChatRequest(model="m", messages=[{"role": "user", "content": "x"}], temperature=0.0, max_tokens=16,
                          stream_callback=(lambda _text: None) if mode == "stream" else None,
                          tools=TOOLS if mode == "tools" else None)
    adapter = _adapter(kind)
    try:
        return await (adapter.complete_with_tools(client, request) if mode == "tools" else adapter.complete(client, request))
    finally:
        await client.close()


def _classify(error: BaseException):
    return classify_attempt_exception(error, None, last_attempt_mode=None, ground_scope_denial=lambda _exc, _ctx: None)


@pytest.mark.parametrize("name", list(MALFORMED))
def test_a_malformed_provider_reply_is_a_retryable_request_failure_never_a_raw_structural_exception(stub_port, name):
    with pytest.raises(Exception) as raised:
        asyncio.run(_call(name, stub_port))
    error = raised.value
    assert not isinstance(error, (AttributeError, TypeError, IndexError, KeyError)), f"a raw {type(error).__name__} escaped: {error}"
    assert isinstance(error, ValueError) and str(error).startswith("the provider's reply is malformed: "), repr(error)
    assert _adapter(ALL[name][0]).classify_error(error) is RuntimeErrorKind.REQUEST
    classified = _classify(error)
    assert not classified.internal_framework_bug and classified.failure.type == "general_error"      # retryable, NOT INTERNAL


@pytest.mark.parametrize("name, expected", [("oai_message_null", "message"), ("oai_choices_null", "choices"), ("oai_choices_empty", "choices"),
                                            ("oai_stream_chunk_without_delta", "delta"), ("native_message_string", "str"),
                                            ("native_body_list", "list"), ("native_stream_non_object_line", "str")])
def test_the_failure_names_what_was_received(stub_port, name, expected):
    """The structural cause is kept in the failure itself (there is no exception to chain: the shape is checked, not caught)."""
    with pytest.raises(ValueError) as raised:
        asyncio.run(_call(name, stub_port))
    assert expected in str(raised.value)


@pytest.mark.parametrize("name", list(VALID))
def test_control_a_valid_reply_is_decoded_exactly_as_before(stub_port, name):
    response = asyncio.run(_call(name, stub_port))
    assert response.content == VALID[name][3]


def test_control_a_valid_tool_call_is_still_returned(stub_port):
    assert [call.name for call in asyncio.run(_call("oai_valid_tools", stub_port)).tool_calls] == ["f"]
    assert [call.name for call in asyncio.run(_call("native_valid_tools", stub_port)).tool_calls] == ["f"]


def test_control_a_connection_error_and_a_malformed_reply_are_classified_alike():
    """Fallback and retry behaviour keys on the classified failure type: a malformed reply gets exactly the connection error's."""
    connection = APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions"))
    assert OllamaRuntimeAdapter().classify_error(connection) is RuntimeErrorKind.CONNECTION
    malformed = ValueError("the provider's reply is malformed: its first choice has no message")
    a, b = _classify(connection), _classify(malformed)
    assert (a.failure.type, a.internal_framework_bug, a.containment_setup_failure) == \
           (b.failure.type, b.internal_framework_bug, b.containment_setup_failure) == ("general_error", False, False)


def test_a_genuine_kriya_coding_error_inside_an_adapter_is_still_a_raw_internal_error(stub_port, monkeypatch):
    """The boundary is a shape check, not a catch-all: a bug in the decoding code itself must not be laundered into a provider failure."""
    def broken(*_args, **_kwargs):
        raise AttributeError("'NoneType' object has no attribute 'strip'")
    monkeypatch.setattr("kriya.core.inference_runtime._str_or_none", broken)
    with pytest.raises(AttributeError) as raised:
        asyncio.run(_call("oai_valid", stub_port))
    assert _classify(raised.value).internal_framework_bug


# Where a decoder's own code can fail, per path: (valid reply, the matching malformed reply, the module attribute whose failure is a
# Kriya coding error on that path). The boundary: malformed EXTERNAL structure is the retryable REQUEST failure; an injected INTERNAL
# coding error on the same path is never laundered into one (no broad catch around any path - complete, stream, tools, native).
_DECODER_PATHS = {
    "oai_complete": ("oai_valid", "oai_message_string", "kriya.core.inference_runtime._str_or_none"),
    "oai_stream": ("oai_valid_stream", "oai_stream_chunk_not_an_object", "kriya.core.inference_runtime._provider_metadata"),
    "oai_stream_finish": ("oai_valid_stream", "oai_stream_chunk_not_an_object", "kriya.core.inference_runtime._str_or_none"),
    "oai_tools": ("oai_valid_tools", "oai_tools_calls_number", "kriya.core.inference_runtime.RawToolCall"),
    "native_complete": ("native_valid", "native_content_number", "kriya.core.model_runtime.provider_text"),
    "native_stream": ("native_valid_stream", "native_stream_content_number", "kriya.core.model_runtime.provider_text"),
    "native_tools": ("native_valid_tools", "native_tools_content_number", "kriya.core.model_runtime.provider_text"),
}


@pytest.mark.parametrize("path", list(_DECODER_PATHS))
def test_on_every_decoder_path_malformed_structure_is_request_and_an_internal_coding_error_is_internal(stub_port, monkeypatch, path):
    valid, malformed, target = _DECODER_PATHS[path]
    kind = ALL[valid][0]
    with pytest.raises(ValueError) as external:
        asyncio.run(_call(malformed, stub_port))
    assert str(external.value).startswith("the provider's reply is malformed: ")
    assert _adapter(kind).classify_error(external.value) is RuntimeErrorKind.REQUEST
    assert not _classify(external.value).internal_framework_bug

    def broken(*_args, **_kwargs):
        raise AttributeError("'NoneType' object has no attribute 'strip'")
    monkeypatch.setattr(target, broken)
    with pytest.raises(AttributeError) as internal:
        asyncio.run(_call(valid, stub_port))
    assert internal.value.args == ("'NoneType' object has no attribute 'strip'",)         # itself, not a translated malformed reply
    assert _classify(internal.value).internal_framework_bug


# ---------------------------------------------------------------------------------- the original E.1 end-to-end scenario

@pytest.mark.parametrize("scenario", ["oai_message_null", "oai_error_body_200", "native_message_string", "connection_error"])
def test_a_malformed_provider_reply_costs_one_retry_and_the_run_still_passes_it_is_not_an_internal_stop(tmp_path, scenario, stub_port):
    """The measured regression: at 1dff900 the first three were a hard INTERNAL stop (1 Developer call); at 72d9182 they were retried
    and passed (2 calls) - exactly what the connection-error control still does."""
    eng, _cfg, developer_calls = _scripted_engine(lambda n: GOOD)
    llm = next(value for value in vars(eng).values() if isinstance(value, LLMClient))
    scripted = llm.complete
    state = {"real_calls": 0}

    async def request_once(client, model, system_prompt, user_prompt, temperature, max_tokens, extra_body, response_format,
                           stream_callback):
        state["real_calls"] += 1
        if scenario == "connection_error":
            raise APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions"))
        response = await _call(scenario, stub_port)
        return response.to_raw()

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        first = (system_prompt or "").startswith(("You are the Kriya Developer Agent", "You are the Kriya File List Planner"))
        if first and state["real_calls"] == 0:
            developer_calls.append("REAL-LLMCLIENT")
            llm._request_once = request_once
            try:
                return await LLMClient.complete(llm, system_prompt, user_prompt, *args, **kwargs)
            finally:
                del llm._request_once
        return await scripted(system_prompt, user_prompt, *args, **kwargs)

    llm.complete = complete
    result = _run(eng, _workspace(tmp_path))
    assert state["real_calls"] == 1, "the real provider path was never exercised"
    assert len(developer_calls) == 2, f"expected one failed attempt then a retry, got {len(developer_calls)} Developer calls"
    assert result["quality_gates_passed"] is True
    assert not result.get("environment_failure")
    report = [(e.get("failure_type"), e.get("category")) for e in result.get("failure_report") or []]
    assert ("internal_framework_error", "internal") not in report

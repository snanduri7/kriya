"""PROVIDER-CONTRACT-001 Batch C: the native Ollama adapter (/api/chat).

Measured on Ollama 0.34.4 (evidence/provider-contract-001/pre_fix): the
native API honours ``options.*`` (num_ctx, top_k, ...) and ``think``; with
the default ``truncate`` it silently truncates an over-window prompt, with
``truncate: false`` it answers HTTP 400. These tests run a loopback server
speaking the native protocol and assert what arrives on the wire."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import httpx
import pytest

from kriya.config import AppConfig
from kriya.core.completion import CompletionStatus
from kriya.core.inference_runtime import RuntimeErrorKind, runtime_adapter
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import (
    NATIVE_PROTOCOL_ADAPTER_VERSION,
    NativeRuntimeError,
    native_request_plan,
    openai_compat_request_plan,
)
from kriya.core.provider_contract import (
    PROVIDER_PROMPT_TRUNCATED,
    PROVIDER_SETTING_CONFLICT,
    PROVIDER_SETTING_UNKNOWN,
    Provenance,
    ProviderContractError,
)

NATIVE = runtime_adapter("ollama_native")


# --- the plan -----------------------------------------------------------------------------------------

def test_every_setting_travels_per_request_in_options():
    plan = native_request_plan({"options": {"top_p": 0.8, "top_k": 20, "seed": 7, "min_p": 0.05}},
                               temperature=0.2, reasoning_flag=False, requested_context_window=32768)
    assert plan.wire_body == {"truncate": False, "think": False,
                              "options": {"num_ctx": 32768, "top_p": 0.8, "top_k": 20, "seed": 7, "min_p": 0.05}}
    assert all(state.provenance is Provenance.REQUEST for state in plan.settings if state.requested is not None)
    assert plan.not_effective() == () and plan.unverified() == ()


def test_reasoning_maps_to_think_and_the_model_default_sends_nothing():
    on = native_request_plan({}, temperature=None, reasoning_flag=True, requested_context_window=None)
    effort = native_request_plan({"reasoning_effort": "high"}, temperature=None, reasoning_flag=True,
                                 requested_context_window=None)
    assert "think" not in on.wire_body and effort.wire_body["think"] == "high"


def test_an_effort_without_a_native_equivalent_is_a_conflict():
    plan = native_request_plan({"reasoning_effort": "minimal"}, temperature=None, reasoning_flag=True,
                               requested_context_window=None)
    with pytest.raises(ProviderContractError) as refused:
        plan.enforce(strict=False)
    assert refused.value.reason_code == PROVIDER_SETTING_CONFLICT


def test_keep_alive_is_a_supported_setting():
    plan = native_request_plan({"keep_alive": "5m"}, temperature=None, reasoning_flag=False,
                               requested_context_window=None)
    assert plan.wire_body["keep_alive"] == "5m" and plan.setting("keep_alive").provenance is Provenance.REQUEST


def test_an_unknown_field_is_refused_in_production():
    plan = native_request_plan({"mirostat": 2}, temperature=None, reasoning_flag=False,
                               requested_context_window=None)
    plan.enforce(strict=False)
    with pytest.raises(ProviderContractError) as refused:
        plan.enforce(strict=True)
    assert refused.value.reason_code == PROVIDER_SETTING_UNKNOWN


def test_parity_the_same_binding_has_the_same_effective_request_settings_on_both_adapters():
    """What /v1 carries per request, native carries identically; what /v1
    can only verify against the served model, native carries per request."""
    binding = {"options": {"top_p": 0.8, "top_k": 20, "seed": 3}, "reasoning_effort": "none"}
    v1 = openai_compat_request_plan(binding, temperature=0.7, reasoning_flag=False, requested_context_window=32768)
    native = native_request_plan(binding, temperature=0.7, reasoning_flag=False, requested_context_window=32768)
    for name in ("temperature", "top_p", "seed", "reasoning"):
        assert v1.setting(name).effective == native.setting(name).effective, name
    for name in ("top_k", "context_window"):
        assert v1.setting(name).provenance is Provenance.UNVERIFIED  # no served-model evidence given
        assert native.setting(name).provenance is Provenance.REQUEST and native.setting(name).effective == \
            {"top_k": 20, "context_window": 32768}[name]


def test_native_is_registered_but_never_the_default():
    assert runtime_adapter().name == "ollama" and NATIVE.name == "ollama_native"


def test_native_evidence_is_never_shared_with_v1():
    from test_prd013_model_runtime import _probe

    v1 = _probe()
    native = NATIVE.probe(base_url="http://localhost:11434/v1", model=v1.alias, api_key="",
                          egress_policy="local_only", configured_context=32768, kriya_protocol=v1.kriya_protocol,
                          transport=lambda url, payload, key: _probe_transport(url))
    assert native.adapter_version == NATIVE_PROTOCOL_ADAPTER_VERSION != v1.adapter_version
    assert native.digest != v1.digest
    assert native.effective_context_window == 32768  # the window every native request carries


def _probe_transport(url):
    import copy

    from test_prd013_model_runtime import OLLAMA

    return copy.deepcopy(OLLAMA)["/" + url.split("/", 3)[3]]


@pytest.mark.parametrize(("error", "kind"), [
    (httpx.ReadTimeout("t"), RuntimeErrorKind.TIMEOUT),
    (httpx.ConnectError("c"), RuntimeErrorKind.CONNECTION),
    (NativeRuntimeError(401, "no"), RuntimeErrorKind.AUTHENTICATION),
    (NativeRuntimeError(429, "slow"), RuntimeErrorKind.RATE_LIMITED),
    (NativeRuntimeError(500, "boom"), RuntimeErrorKind.SERVER),
    (NativeRuntimeError(400, "bad"), RuntimeErrorKind.REQUEST),
    (ProviderContractError(PROVIDER_PROMPT_TRUNCATED, "x"), RuntimeErrorKind.SERVER),
])
def test_errors_are_classified_for_kriyas_policy(error, kind):
    assert NATIVE.classify_error(error) is kind


# --- on the wire ---------------------------------------------------------------------------------------

class _NativeServer:
    def __init__(self):
        self.requests = []
        self.status = 200
        self.error = None
        self.reply = {"message": {"role": "assistant", "content": "ok", "thinking": ""},
                      "done": True, "done_reason": "stop", "prompt_eval_count": 0, "eval_count": 1,
                      "model": "m:1"}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append((self.path, body))
                if server.status != 200:
                    self._send(server.status, json.dumps({"error": server.error}).encode())
                elif body.get("stream"):
                    lines = [{"message": {"content": "o"}, "done": False, "model": "m:1"},
                             {"message": {"content": "k"}, "done": False},
                             {"message": {"content": ""}, "done": True, "done_reason": "stop",
                              "prompt_eval_count": 0, "eval_count": 2}]
                    self._send(200, "".join(json.dumps(line) + "\n" for line in lines).encode(),
                               "application/x-ndjson")
                else:
                    self._send(200, json.dumps(server.reply).encode())

            def _send(self, status, payload, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def native_server():
    server = _NativeServer()
    yield server
    server.close()


def _config(url, extra_body=None):
    config = AppConfig()
    config.llm.base_url = url
    config.llm.model = "m:1"
    config.llm.inference_runtime = "ollama_native"
    config.llm.context_window = 8192
    config.llm.extra_body = extra_body if extra_body is not None else {"options": {"top_p": 0.8, "top_k": 20}}
    config.llm_chain = []
    return config


async def _call(config, **kwargs):
    llm = LLMClient(config)
    try:
        return await llm.complete_result("s", "u", **kwargs)
    finally:
        await llm.aclose()


def test_a_native_call_sends_every_setting_and_truncate_false(native_server):
    native_server.reply["message"]["content"] = '{"ok": true}'
    result = asyncio.run(_call(_config(native_server.url), json_mode=True))
    assert result.status is CompletionStatus.OK and result.content == '{"ok": true}'
    [(path, body)] = native_server.requests
    assert path == "/api/chat" and body["stream"] is False and body["truncate"] is False
    assert body["options"] == {"num_ctx": 8192, "top_p": 0.8, "top_k": 20, "temperature": 0.2,
                               "num_predict": body["options"]["num_predict"]}
    assert body["think"] is False and body["format"] == "json"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_a_native_stream_is_one_request(native_server):
    tokens = []
    result = asyncio.run(_call(_config(native_server.url), stream_callback=tokens.append))
    assert result.content == "ok" and tokens == ["o", "k"] and result.finish_reason == "stop"
    assert len(native_server.requests) == 1 and native_server.requests[0][1]["stream"] is True


def test_an_over_window_prompt_is_a_typed_refusal_never_a_retry(native_server):
    native_server.status, native_server.error = 400, "exceed_context_size_error: request exceeds the context"
    result = asyncio.run(_call(_config(native_server.url), json_mode=True))
    assert isinstance(result.error, ProviderContractError)
    assert result.error.reason_code == PROVIDER_PROMPT_TRUNCATED
    assert len(native_server.requests) == 1  # no changed-request retry (not even without response_format)


def test_a_server_error_is_one_request(native_server):
    native_server.status, native_server.error = 500, "boom"
    result = asyncio.run(_call(_config(native_server.url)))
    assert result.status is CompletionStatus.BACKEND_ERROR and len(native_server.requests) == 1


def test_native_tool_calls_are_normalized(native_server):
    native_server.reply = {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "save_note", "arguments": {"text": "hi"}}}]},
        "done": True, "done_reason": "stop", "prompt_eval_count": 0, "eval_count": 3}
    tools = [{"type": "function", "function": {"name": "save_note", "parameters": {
        "type": "object", "properties": {"text": {"type": "string"}}}}}]
    history = [{"role": "user", "content": "save hi"},
               {"role": "assistant", "content": "", "tool_calls": [
                   {"id": "c1", "type": "function", "function": {"name": "save_note", "arguments": "{\"text\": \"x\"}"}}]},
               {"role": "tool", "content": "saved", "tool_call_id": "c1"}]
    config = _config(native_server.url)
    config.llm.capabilities.native_tool_calls = True

    async def run():
        llm = LLMClient(config)
        try:
            return await llm.complete_with_tools_result(history, tools)
        finally:
            await llm.aclose()

    result = asyncio.run(run())
    [call] = result.tool_calls
    assert call["name"] == "save_note" and call["arguments"] == {"text": "hi"}
    [(_, body)] = native_server.requests
    assert body["tools"] == tools
    # An earlier assistant tool call is sent in the native shape (arguments as an object).
    assert body["messages"][1]["tool_calls"] == [{"function": {"name": "save_note", "arguments": {"text": "x"}}}]


@pytest.mark.parametrize("extra_body", [None, {"truncate": True}, {"options": {"num_ctx": 4096}}])
def test_every_native_request_refuses_truncation_whatever_the_body_says(extra_body):
    from kriya.core.inference_runtime import ChatRequest

    payload = NATIVE._payload(ChatRequest(model="m", messages=[], temperature=None, max_tokens=8,  # pylint: disable=protected-access
                                          extra_body=extra_body), stream=False)
    assert payload["truncate"] is False


def test_the_native_url_is_the_servers_root_api_chat():
    client = SimpleNamespace(base_url="http://localhost:11434/v1/", api_key="k")
    assert NATIVE._url(client) == "http://localhost:11434/api/chat"  # pylint: disable=protected-access

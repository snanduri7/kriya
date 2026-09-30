"""PROVIDER-CONTRACT-001: every setting Kriya records, budgets against or
qualifies either reaches the provider with the intended semantics or Kriya
fails closed.

The /v1 facts these tests encode were measured against Ollama 0.34.4
(evidence/provider-contract-001/pre_fix/): /v1 ignores every ``options``
field and a top-level top_k, applies top-level temperature/top_p/seed and
``reasoning_effort`` ("none" disables reasoning), and silently truncates a
prompt larger than the served window. The transport tests run a real HTTP
server on loopback and count what actually arrives on the wire."""
import asyncio
import json
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import httpx
import pytest
from _fake_inference_runtime import FakeRuntimeAdapter

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.completion import CompletionStatus
from kriya.core.inference_runtime import ChatResponse, register_runtime_adapter, unregister_runtime_adapter
from kriya.core.llm import SDK_MAX_RETRIES, LLMClient, inference_deadline, transport_timeout
from kriya.core.model_runtime import REASONING_MODEL_DEFAULT, openai_compat_request_plan
from kriya.core.provider_contract import (
    PROVIDER_PROMPT_TRUNCATED,
    PROVIDER_SETTING_CONFLICT,
    PROVIDER_SETTING_NOT_EFFECTIVE,
    PROVIDER_SETTING_UNKNOWN,
    RUNTIME_CONTEXT_IDENTITY_MISMATCH,
    SERVED_CONTEXT_BELOW_REQUESTED,
    Provenance,
    ProviderContractError,
    Support,
    check_prompt_consumption,
    context_window_state,
)

PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def _served(**parameters):
    """A probed runtime whose served model carries ``parameters``."""
    return SimpleNamespace(server_parameters=tuple(f"{k} {v}" for k, v in parameters.items()),
                           runtime_parameters_digest="sha256:params")


# --- /v1 request plan: the wire and each setting's effective state ---------------------------------------

def test_reasoning_false_is_sent_as_reasoning_effort_none_and_true_as_the_model_default():
    off = openai_compat_request_plan({}, temperature=0.2, reasoning_flag=False, requested_context_window=None)
    on = openai_compat_request_plan({}, temperature=0.2, reasoning_flag=True, requested_context_window=None)
    assert off.wire_body == {"reasoning_effort": "none"}
    assert "reasoning_effort" not in on.wire_body
    assert off.setting("reasoning").effective == "none"
    assert on.setting("reasoning").effective == REASONING_MODEL_DEFAULT
    # The effective reasoning mode is part of the identity, so the two differ.
    assert off.identity() != on.identity()


def test_an_explicit_think_option_maps_to_the_reasoning_setting_not_to_options():
    plan = openai_compat_request_plan({"options": {"think": False}}, temperature=None, reasoning_flag=True,
                                      requested_context_window=None)
    assert plan.wire_body == {"reasoning_effort": "none"} and plan.unknown == ()


def test_sampling_options_are_sent_top_level_and_options_never_reach_the_wire():
    plan = openai_compat_request_plan({"options": {"top_p": 0.8, "seed": 7, "num_ctx": 32768}},
                                      temperature=0.2, reasoning_flag=False, requested_context_window=32768)
    assert plan.wire_body == {"top_p": 0.8, "seed": 7, "reasoning_effort": "none"}
    assert plan.setting("top_p").provenance is Provenance.REQUEST
    window = plan.setting("context_window")
    assert window.support is Support.SERVER_CONFIG_ONLY
    # No probe evidence: the window is requested, nothing proves it is served.
    assert (window.requested, window.effective, window.provenance) == (32768, None, Provenance.UNVERIFIED)
    assert window in plan.unverified()


def test_a_server_only_setting_is_compared_with_what_the_served_model_carries():
    requested = {"options": {"top_k": 40, "num_ctx": 65536}}
    plan = openai_compat_request_plan(requested, temperature=0.2, reasoning_flag=False,
                                      requested_context_window=65536,
                                      fingerprint=_served(top_k=20, num_ctx=32768, top_p=0.95))
    assert "top_k" not in plan.wire_body and "options" not in plan.wire_body
    assert {s.name: (s.requested, s.effective) for s in plan.not_effective()} == {
        "top_k": (40, 20), "context_window": (65536, 32768)}
    # The effective value (the served model's own) is what the identity carries.
    assert plan.identity()["top_p"] == {"effective": 0.95, "provenance": "server_model_config"}
    plan.enforce(strict=False)  # recorded outside production
    with pytest.raises(ProviderContractError) as refused:
        plan.enforce(strict=True)
    assert refused.value.reason_code == PROVIDER_SETTING_NOT_EFFECTIVE
    assert set(refused.value.details["settings"]) == {"top_k", "context_window"}


def test_a_server_only_setting_that_matches_the_served_model_is_effective():
    plan = openai_compat_request_plan({"options": {"top_k": 20}}, temperature=0.2, reasoning_flag=False,
                                      requested_context_window=32768,
                                      fingerprint=_served(top_k=20, num_ctx=32768))
    assert plan.not_effective() == ()
    plan.enforce(strict=True)


def test_an_unknown_field_is_forwarded_outside_production_and_refused_in_it():
    plan = openai_compat_request_plan({"mirostat": 2, "options": {"typical_p": 0.5}}, temperature=None,
                                      reasoning_flag=False, requested_context_window=None)
    assert plan.unknown == ("mirostat", "options.typical_p")
    assert plan.wire_body["mirostat"] == 2 and "options" not in plan.wire_body
    plan.enforce(strict=False)
    with pytest.raises(ProviderContractError) as refused:
        plan.enforce(strict=True)
    assert refused.value.reason_code == PROVIDER_SETTING_UNKNOWN


@pytest.mark.parametrize("extra_body", [
    {"top_p": 0.9, "options": {"top_p": 0.8}},
    {"think": True, "reasoning_effort": "none"},
    {"reasoning_effort": "bogus"},  # /v1 answers HTTP 200 to any string (measured) - never silently ignored
])
def test_a_conflicting_or_unrecognised_authoritative_value_is_refused_in_every_profile(extra_body):
    plan = openai_compat_request_plan(extra_body, temperature=None, reasoning_flag=False,
                                      requested_context_window=None)
    for strict in (False, True):
        with pytest.raises(ProviderContractError) as refused:
            plan.enforce(strict=strict)
        assert refused.value.reason_code == PROVIDER_SETTING_CONFLICT


# --- requested / served / budget context --------------------------------------------------------------

def test_the_budget_is_the_requested_window_never_a_larger_served_one():
    state = context_window_state(32768, 65536, Provenance.SERVER_OBSERVED, exact=False)
    assert (state.requested, state.served, state.budget) == (32768, 65536, 32768)
    exact = context_window_state(32768, 32768, Provenance.SERVER_OBSERVED, exact=True)
    assert exact.budget == 32768


def test_a_larger_served_window_is_an_identity_mismatch_under_the_exact_policy():
    with pytest.raises(ProviderContractError) as refused:
        context_window_state(32768, 65536, Provenance.SERVER_OBSERVED, exact=True)
    assert refused.value.reason_code == RUNTIME_CONTEXT_IDENTITY_MISMATCH


@pytest.mark.parametrize("exact", [False, True])
def test_a_smaller_served_window_is_always_refused(exact):
    with pytest.raises(ProviderContractError) as refused:
        context_window_state(65536, 32768, Provenance.SERVER_OBSERVED, exact=exact)
    assert refused.value.reason_code == SERVED_CONTEXT_BELOW_REQUESTED


def test_an_unobserved_window_budgets_the_request_and_is_labelled_unverified():
    state = context_window_state(32768, None, Provenance.SERVER_OBSERVED, exact=True)
    assert state.budget == 32768 and state.served_provenance is Provenance.UNVERIFIED


# --- prompt consumption (silent truncation) -----------------------------------------------------------

def test_a_provider_evaluating_fewer_tokens_than_the_prompt_can_hold_is_truncation():
    # Measured: /v1 served a ~72K-token prompt as 32770 prompt tokens.
    evidence = check_prompt_consumption(dispatched_bytes=300_000, reported_prompt_tokens=32770,
                                        bytes_per_token_ceiling=None)
    assert evidence is not None and evidence["minimum_expected_tokens"] == 37500
    assert check_prompt_consumption(dispatched_bytes=300_000, reported_prompt_tokens=72000,
                                    bytes_per_token_ceiling=None) is None
    # A measured tokenizer ceiling is what the tolerance comes from.
    assert check_prompt_consumption(dispatched_bytes=300_000, reported_prompt_tokens=32770,
                                    bytes_per_token_ceiling=10.0) is None


def test_no_reported_usage_is_no_evidence():
    assert check_prompt_consumption(dispatched_bytes=300_000, reported_prompt_tokens=None,
                                    bytes_per_token_ceiling=None) is None
    assert check_prompt_consumption(dispatched_bytes=300_000, reported_prompt_tokens=0,
                                    bytes_per_token_ceiling=None) is None


# --- through LLMClient on an identified runtime -------------------------------------------------------

@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    adapter = FakeRuntimeAdapter()
    register_runtime_adapter(adapter)
    model_runtime.clear_model_runtime_cache()
    yield adapter
    unregister_runtime_adapter(adapter.name)
    model_runtime.clear_model_runtime_cache()


def _fake_config(window=8192, production=False):
    config = AppConfig()
    config.llm.model = "primary:1"
    config.llm.inference_runtime = "fake"
    config.llm.context_window = window
    config.llm_chain = []
    if production:
        config.runtime_profile = "production"
    return config


def test_a_response_evaluating_less_than_the_prompt_is_a_provider_contract_violation(fake):
    prompt = "def f():\n    return 1\n" * 400  # ~9.2 KB: at least ~1150 tokens
    fake.replies = [ChatResponse(content="ok", prompt_tokens=100, completion_tokens=2, finish_reason="stop")]
    result = asyncio.run(LLMClient(_fake_config()).complete_result("s", prompt))
    assert result.status is CompletionStatus.PROVIDER_CONTRACT_VIOLATION
    assert isinstance(result.error, ProviderContractError)
    assert result.error.reason_code == PROVIDER_PROMPT_TRUNCATED
    assert result.protocol["provider_contract"]["violation"]["reason_code"] == PROVIDER_PROMPT_TRUNCATED
    assert result.protocol["provider_contract"]["prompt_consumption"] == "truncated"
    # The compatibility API raises the typed error; the content is never used.
    fake.replies = [ChatResponse(content="ok", prompt_tokens=100, completion_tokens=2, finish_reason="stop")]
    with pytest.raises(ProviderContractError):
        asyncio.run(LLMClient(_fake_config()).complete("s", prompt))


def test_whitespace_dense_text_is_never_mistaken_for_truncation(fake):
    """A tokenizer merges indentation runs: 50 lines of 80 spaces and a
    word are ~4 KB but only a few hundred tokens. Raw bytes / ceiling would
    demand 500+ tokens and call an honest provider truncating."""
    prompt = (" " * 80 + "word\n") * 50
    fake.replies = [ChatResponse(content="ok", prompt_tokens=150, completion_tokens=2, finish_reason="stop")]
    result = asyncio.run(LLMClient(_fake_config()).complete_result("s", prompt))
    assert result.status is CompletionStatus.OK
    assert result.protocol["provider_contract"]["prompt_consumption"] == "consistent"


def test_a_response_evaluating_the_whole_prompt_is_ok_and_records_the_contract(fake):
    result = asyncio.run(LLMClient(_fake_config()).complete_result("s", "def f():\n    return 1\n" * 400))
    assert result.status is CompletionStatus.OK and result.error is None
    contract = result.protocol["provider_contract"]
    assert contract["adapter"] == "fake" and contract["sdk_max_retries"] == 0
    assert contract["proxy_policy"] == "direct"
    assert contract["context"]["budget_context_window"] == 8192
    # Telemetry: the prompt-consumption verdict and the transport bounds, never prompt text.
    assert contract["prompt_consumption"] == "consistent" and contract["prompt_tokens_reported"] > 0
    assert contract["timeout_seconds"] == {"connect": 10.0, "read": 900.0}
    assert contract["inference_deadline_bound"] is False
    assert "def f()" not in json.dumps(result.to_telemetry())


def test_a_larger_served_window_is_refused_before_inference_only_under_production(fake):
    fake.served_window = 16384
    ok = asyncio.run(LLMClient(_fake_config()).complete_result("s", "u"))
    assert ok.status is CompletionStatus.OK and ok.budget["context_window"] == 8192
    fake.requests.clear()
    with pytest.raises(ProviderContractError) as refused:
        asyncio.run(LLMClient(_fake_config(production=True)).complete_result("s", "u"))
    assert refused.value.reason_code == RUNTIME_CONTEXT_IDENTITY_MISMATCH
    assert fake.requests == []


# --- the real transport, on the wire ------------------------------------------------------------------

class _Server:
    """A loopback OpenAI-compatible endpoint recording every request body."""

    def __init__(self):
        self.bodies = []
        self.status = 200
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.bodies.append(body)
                if server.status != 200:
                    self._send(server.status, "application/json", b'{"error":{"message":"boom"}}')
                elif body.get("stream"):
                    chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                             "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}
                    self._send(200, "text/event-stream",
                               f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())
                else:
                    reply = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": "ok"}}],
                             "usage": {"prompt_tokens": 0, "completion_tokens": 1, "total_tokens": 1}}
                    self._send(200, "application/json", json.dumps(reply).encode())

            def _send(self, status, content_type, payload):
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


class _Proxy:
    """Counts every connection made to it (answers 502)."""

    def __init__(self):
        self.hits = 0
        proxy = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                proxy.hits += 1
                self.request.recv(65536)
                self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")

        self.tcp = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.tcp.server_address[1]}"
        threading.Thread(target=self.tcp.serve_forever, daemon=True).start()

    def close(self):
        self.tcp.shutdown()
        self.tcp.server_close()


@pytest.fixture
def server():
    endpoint = _Server()
    yield endpoint
    endpoint.close()


def _wire_config(url, extra_body=None, production=False):
    config = AppConfig()
    config.llm.base_url = url
    config.llm.model = "m:1"
    config.llm.extra_body = extra_body if extra_body is not None else {"options": {"top_p": 0.8, "num_ctx": 8192}}
    config.llm.context_window = 8192
    config.llm_chain = []
    if production:
        config.runtime_profile = "production"
    return config


async def _call(config, **kwargs):
    llm = LLMClient(config)
    try:
        return await llm.complete_result("s", "u", **kwargs)
    finally:
        await llm.aclose()


def test_the_wire_carries_only_what_v1_applies(server):
    result = asyncio.run(_call(_wire_config(server.url)))
    assert result.status is CompletionStatus.OK
    [body] = server.bodies
    assert "options" not in body and "num_ctx" not in json.dumps(body)
    assert body["top_p"] == 0.8 and body["reasoning_effort"] == "none" and body["temperature"] == 0.2
    # An unidentified runtime's usage is no evidence either way.
    assert result.protocol["provider_contract"]["prompt_consumption"] == "unverified"


def test_one_failing_call_is_one_http_request(server):
    """P0-5, measured pre-fix: the SDK's own two retries made one Kriya call
    three HTTP requests. Only Kriya's retry policy may send another."""
    server.status = 500
    result = asyncio.run(_call(_wire_config(server.url)))
    assert result.status is CompletionStatus.BACKEND_ERROR
    assert len(server.bodies) == 1


def test_one_failing_stream_is_one_http_request_never_resent_in_another_shape(server):
    """P0-7, measured pre-fix: a failed stream was resent without
    stream_options (two requests, two shapes)."""
    server.status = 500
    result = asyncio.run(_call(_wire_config(server.url), stream_callback=lambda token: None))
    assert result.status is CompletionStatus.BACKEND_ERROR
    [body] = server.bodies
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}


def test_a_successful_stream_is_one_request(server):
    tokens = []
    result = asyncio.run(_call(_wire_config(server.url), stream_callback=tokens.append))
    assert result.content == "ok" and tokens == ["ok"] and len(server.bodies) == 1


def test_production_refuses_an_unknown_field_before_any_request(server):
    config = _wire_config(server.url, extra_body={"mirostat": 2}, production=True)
    with pytest.raises(ProviderContractError) as refused:
        asyncio.run(_call(config))
    assert refused.value.reason_code == PROVIDER_SETTING_UNKNOWN and server.bodies == []


def test_a_conflicting_setting_is_refused_before_any_request(server):
    config = _wire_config(server.url, extra_body={"top_p": 0.9, "options": {"top_p": 0.8}})
    with pytest.raises(ProviderContractError) as refused:
        asyncio.run(_call(config))
    assert refused.value.reason_code == PROVIDER_SETTING_CONFLICT and server.bodies == []


def test_proxy_variables_in_the_environment_never_carry_inference_traffic(server, monkeypatch):
    """P0-6, measured pre-fix: HTTP_PROXY routed a loopback inference call
    through the proxy."""
    proxy = _Proxy()
    try:
        for name in PROXY_VARIABLES:
            monkeypatch.setenv(name, proxy.url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        async def negative_control():
            # The same environment does reach the proxy from an environment-trusting client.
            async with httpx.AsyncClient(trust_env=True) as client:
                await client.post(f"{server.url}/chat/completions", json={})

        asyncio.run(negative_control())
        assert proxy.hits == 1 and server.bodies == []
        result = asyncio.run(_call(_wire_config(server.url)))
        assert result.status is CompletionStatus.OK
        assert proxy.hits == 1 and len(server.bodies) == 1
    finally:
        proxy.close()


# --- timeouts and the client lifecycle ----------------------------------------------------------------

def test_every_client_is_built_without_sdk_retries_and_with_the_kriya_timeout():
    config = AppConfig()
    config.llm.transport.connect_timeout_seconds = 3
    config.llm.transport.read_timeout_seconds = 120
    llm = LLMClient(config)
    assert SDK_MAX_RETRIES == 0 and llm.client.max_retries == 0
    assert llm.client.timeout.connect == 3 and llm.client.timeout.read == 120
    assert llm.client._client._trust_env is False  # pylint: disable=protected-access
    asyncio.run(llm.aclose())


def test_the_read_timeout_never_exceeds_the_remaining_inference_deadline():
    llm = LLMClient(AppConfig())
    assert llm._request_timeout().read == 900  # pylint: disable=protected-access
    with inference_deadline(time.monotonic() + 5):
        assert 0 < llm._request_timeout().read <= 5  # pylint: disable=protected-access
    assert transport_timeout(llm.config, 7).read == 7
    asyncio.run(llm.aclose())


def test_a_passed_deadline_is_a_timeout_before_any_request(server):
    async def run():
        with inference_deadline(time.monotonic() - 1):
            return await _call(_wire_config(server.url))

    result = asyncio.run(run())
    assert result.status is CompletionStatus.TIMEOUT and server.bodies == []


def test_aclose_releases_every_transport_the_client_opened(server):
    async def run():
        llm = LLMClient(_wire_config(server.url))
        first = llm.client
        assert llm._client_for(server.url, llm.config.llm.api_key) is first  # pylint: disable=protected-access
        other = llm._client_for(server.url.replace("/v1", "/other/v1"), "k")  # pylint: disable=protected-access
        transports = [first._client, other._client]  # pylint: disable=protected-access
        await llm.complete_result("s", "u")
        await llm.aclose()
        return transports, llm

    transports, llm = asyncio.run(run())
    assert all(transport.is_closed for transport in transports)
    assert llm._clients == {}  # pylint: disable=protected-access


# --- production doctor: model.provider_contract --------------------------------------------------------

def _doctor_entry(runtime, served, extra_body=None, window=32768):
    from unittest.mock import patch

    from kriya.production_doctor import _provider_contract_entry

    config = AppConfig()
    config.llm.model = "m:1"
    config.llm.extra_body = extra_body if extra_body is not None else {"options": {"top_p": 0.8, "top_k": 20}}
    config.llm.context_window = window
    config.llm_chain = []
    with patch("kriya.production_doctor.probe_served_context", return_value=served):
        return _provider_contract_entry(config, "m:1", runtime)


def _exact(**parameters):
    from kriya.core.model_runtime import ModelRuntimeFingerprint

    return ModelRuntimeFingerprint(
        alias="m:1", endpoint="http://localhost:11434/v1", provider="ollama", provider_version="0.34.4",
        artifact_digest="sha256:a", weights_digest="sha256:w",
        server_parameters=tuple(f"{k} {v}" for k, v in parameters.items()))


def test_doctor_passes_a_model_served_exactly_as_its_binding_declares():
    from kriya.production_doctor import CheckStatus

    status, entry = _doctor_entry(_exact(num_ctx=32768, top_k=20), {"served": 32768, "probe_request_sent": True})
    assert status is CheckStatus.PASS, entry
    assert entry["context"]["budget_context_window"] == 32768 and entry["not_effective"] == []


@pytest.mark.parametrize(("runtime", "served", "reason"), [
    # top_k 20 declared, the served model samples with 40.
    (_exact(num_ctx=32768, top_k=40), {"served": 32768}, PROVIDER_SETTING_NOT_EFFECTIVE),
    # The server environment's default window (measured: OLLAMA_CONTEXT_LENGTH=65536) is not the declared one.
    (_exact(num_ctx=32768, top_k=20), {"served": 65536}, RUNTIME_CONTEXT_IDENTITY_MISMATCH),
    (_exact(num_ctx=32768, top_k=20), {"served": 16384}, SERVED_CONTEXT_BELOW_REQUESTED),
])
def test_doctor_fails_a_model_the_provider_serves_differently(runtime, served, reason):
    from kriya.production_doctor import CheckStatus

    status, entry = _doctor_entry(runtime, served)
    assert status is CheckStatus.FAIL
    assert [failure["reason_code"] for failure in entry["failures"]] == [reason]


def test_doctor_fails_an_unknown_request_field():
    from kriya.production_doctor import CheckStatus

    status, entry = _doctor_entry(_exact(num_ctx=32768), {"served": 32768}, extra_body={"mirostat": 2})
    assert status is CheckStatus.FAIL and entry["failures"][0]["reason_code"] == PROVIDER_SETTING_UNKNOWN


@pytest.mark.parametrize(("runtime", "served"), [
    (None, {"served": 32768}),                                  # unidentified runtime
    (_exact(), {"served": 32768}),                              # server parameters not readable
    (_exact(num_ctx=32768, top_k=20), {"served": None}),        # served window not observable
])
def test_doctor_never_passes_what_it_could_not_verify(runtime, served):
    from kriya.production_doctor import PROVIDER_CONTRACT_UNVERIFIED, CheckStatus

    status, entry = _doctor_entry(runtime, served)
    assert status is CheckStatus.UNAVAILABLE and entry["reason_code"] == PROVIDER_CONTRACT_UNVERIFIED


# --- kriya model pin ---------------------------------------------------------------------------------

def test_pin_fixes_only_the_server_only_settings_the_binding_declares():
    from kriya.core.model_runtime import pin_served_configuration

    sent = []
    pin = pin_served_configuration(
        base_url="http://localhost:11434/v1", model="qwen3-coder:30b",
        extra_body={"options": {"top_p": 0.8, "top_k": 20}, "reasoning_effort": "none"},
        requested_context_window=32768, transport=lambda url, payload, key: sent.append((url, payload)) or {})
    # top_p and reasoning are carried per request: never pinned.
    assert pin["parameters"] == {"num_ctx": 32768, "top_k": 20}
    assert pin["model"].startswith("qwen3-coder:30b-kriya-") and pin["created"] is True
    assert sent == [("http://localhost:11434/api/create",
                     {"model": pin["model"], "from": "qwen3-coder:30b", "parameters": pin["parameters"],
                      "stream": False})]


def test_the_pinned_name_is_bound_to_the_exact_parameters():
    from kriya.core.model_runtime import pinned_model_name

    assert pinned_model_name("m:1", {"num_ctx": 32768}) == pinned_model_name("m:1", {"num_ctx": 32768})
    assert pinned_model_name("m:1", {"num_ctx": 32768}) != pinned_model_name("m:1", {"num_ctx": 65536})


def test_pin_refuses_a_remote_endpoint_and_a_conflicting_binding():
    from kriya.core.model_runtime import pin_served_configuration

    with pytest.raises(ProviderContractError):
        pin_served_configuration(base_url="https://api.example.com/v1", model="m:1", extra_body=None,
                                 requested_context_window=32768, transport=lambda *a: pytest.fail("sent"))
    with pytest.raises(ProviderContractError) as refused:
        pin_served_configuration(base_url="http://localhost:11434/v1", model="m:1",
                                 extra_body={"top_k": 20, "options": {"top_k": 40}}, requested_context_window=None,
                                 transport=lambda *a: pytest.fail("sent"))
    assert refused.value.reason_code == PROVIDER_SETTING_CONFLICT


def test_a_runtime_without_a_pin_mechanism_refuses_to_pin(fake):
    with pytest.raises(ProviderContractError):
        fake.pin_served_configuration(base_url="http://localhost:1/v1", model="m", extra_body=None,
                                      requested_context_window=8192)


# --- one budget window for sizing, routing and dispatch ----------------------------------------------

@pytest.mark.parametrize(("served", "expected"), [(65536, 32768), (32768, 32768), (16384, 16384), (None, 32768)])
def test_prompts_are_sized_to_the_requested_window_never_a_larger_served_one(served, expected):
    """Measured: the server environment serves every /v1 model at 65536
    (OLLAMA_CONTEXT_LENGTH) whatever the binding declares. Sizing prompts to
    that while dispatch budgets the requested 32768 builds requests the
    dispatch check must refuse."""
    from unittest.mock import patch

    from kriya.core.model_routing import _served_window
    from kriya.core.token_budget import DISPATCH_SAFETY_MARGIN_TOKENS, TWO_MESSAGE_FRAMING_TOKENS
    from kriya.workflow.context_budget import request_capacity

    config = AppConfig()
    config.llm.context_window = 32768
    config.llm.max_tokens = 4096
    config.llm.reasoning = False
    fingerprint = SimpleNamespace(effective_context_window=served, exact=False, tokenizer_digest="unavailable")
    with patch("kriya.core.model_runtime.resolve_configured_model_runtime", return_value=fingerprint):
        capacity = request_capacity(config)
    assert capacity.tokens == expected - 4096 - TWO_MESSAGE_FRAMING_TOKENS - DISPATCH_SAFETY_MARGIN_TOKENS
    assert _served_window(fingerprint, config.llm) == expected

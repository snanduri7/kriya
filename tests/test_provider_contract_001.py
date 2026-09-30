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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

class _Loopback(ThreadingHTTPServer):
    """A test server whose teardown never waits on a stalled connection."""

    daemon_threads = True
    block_on_close = False


class _Server:
    """A loopback OpenAI-compatible endpoint recording every request body."""

    def __init__(self):
        self.bodies = []
        self.status = 200
        self.delay = 0.0
        server = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 5  # a stalled client (a timed-out probe) never pins a handler

            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.bodies.append(body)
                time.sleep(server.delay)
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

        self.httpd = _Loopback(("127.0.0.1", 0), Handler)
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

        self.tcp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.tcp.daemon_threads, self.tcp.block_on_close = True, False
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
    import re

    from kriya.core.model_runtime import pinned_model_name

    # One name:tag separator (an Ollama tag cannot hold another ':'), 12 hex digest characters.
    assert re.fullmatch(r"qwen3-coder:30b-kriya-[0-9a-f]{12}", pinned_model_name("qwen3-coder:30b", {"num_ctx": 32768}))
    assert re.fullmatch(r"m:latest-kriya-[0-9a-f]{12}", pinned_model_name("m", {"num_ctx": 32768}))
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


# === Batch B: output/reasoning budgeting and qualification identity =====================================

def test_a_reasoning_identity_reserves_its_measured_reasoning_on_top_of_its_role_output():
    from kriya.core.llm import REASONING_MIN_MAX_TOKENS, reasoning_max_tokens

    assert reasoning_max_tokens(1024, 3000) == 4024
    # Unmeasured: the fixed floor, never less than the role's own output.
    assert reasoning_max_tokens(1024, None) == REASONING_MIN_MAX_TOKENS
    assert reasoning_max_tokens(20000, None) == 20000


@pytest.mark.parametrize(("measured", "sent", "source"), [(3000, 4024, "qualified"),
                                                          (None, 12288, "unqualified_default")])
def test_the_request_carries_the_qualified_reasoning_reserve(fake, monkeypatch, measured, sent, source):
    from kriya.core import model_qualification

    limits = {"reasoning_tokens_max": measured} if measured else {}
    monkeypatch.setattr(model_qualification, "measured_limits_for", lambda *a, **k: dict(limits))
    config = _fake_config(window=32768)
    fake.served_window = 32768
    config.llm.reasoning = True
    config.llm.max_tokens = 1024
    result = asyncio.run(LLMClient(config).complete_result("s", "u"))
    assert fake.requests[-1].max_tokens == sent
    assert result.budget["reasoning_reserve_source"] == source


def test_a_non_reasoning_identity_reserves_no_reasoning():
    from kriya.workflow.context_budget import request_capacity

    config = AppConfig()
    config.llm.context_window, config.llm.max_tokens, config.llm.reasoning = 32768, 2048, False
    from unittest.mock import patch

    with patch("kriya.core.model_runtime.resolve_configured_model_runtime",
               side_effect=RuntimeError("no runtime")):
        plain = request_capacity(config)
        config.llm.reasoning = True
        reasoning = request_capacity(config)
    # Unmeasured reasoning: the floor (12288) is reserved; without reasoning only the output.
    assert plain.tokens - reasoning.tokens == 12288 - 2048


def test_each_role_gets_its_own_output_budget_never_the_developers():
    from kriya.core.kernel import Kernel
    from kriya.workflow.workflow import WorkflowEngine

    config = AppConfig()
    config.llm.max_tokens = 16384
    config.agent_llms.reviewer.max_output_tokens = 2048
    config.agent_llms.spec_compliance.max_output_tokens = 1024
    config.agent_llms.planner.max_output_tokens = 6000
    engine = WorkflowEngine(Kernel(config=config), LLMClient(config))
    assert engine.reviewer.max_output_tokens == 2048
    assert engine.spec_compliance.max_output_tokens == 1024
    assert engine.planner.max_output_tokens == 6000  # wins over llm.planner_max_tokens
    assert engine.architect.max_output_tokens is None  # unset: the model binding's own budget



def test_a_role_output_budget_below_the_minimum_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AppConfig(agent_llms={"reviewer": {"max_output_tokens": 16}})


def test_a_requested_server_only_setting_is_part_of_the_inference_identity():
    from kriya.core.inference_settings import request_settings

    top_k_20 = request_settings(temperature=0.2, reasoning=False, extra_body={"options": {"top_k": 20}})
    top_k_40 = request_settings(temperature=0.2, reasoning=False, extra_body={"options": {"top_k": 40}})
    assert top_k_20.extra_body == top_k_40.extra_body == {"reasoning_effort": "none"}  # same wire
    assert top_k_20.digest != top_k_40.digest  # different identity
    assert top_k_20.server_config == {"top_k": 20}


def _qualify(config, fingerprint):
    from kriya.core import model_qualification as mq

    class _NoCalls:
        async def complete_result(self, *args, **kwargs):
            raise AssertionError("a case ran")

    # A case that sends nothing: the identity check runs before any case.
    return asyncio.run(mq.run_qualification(config, config.llm.model, llm=_NoCalls(), fingerprint=fingerprint,
                                            only=["endpoint_restart_semantics"]))


def _pinned_fp(**parameters):
    from dataclasses import replace

    return replace(_exact(**parameters), alias="m:1")


@pytest.mark.parametrize(("parameters", "problem"), [
    ({}, "context_window: requested 32768, not verifiable"),                  # unpinned window
    ({"num_ctx": 65536, "top_k": 20}, "context_window: requested 32768, served 65536"),
    ({"num_ctx": 32768, "top_k": 40}, "top_k: requested 20, served 40"),
])
def test_qualification_refuses_an_identity_the_provider_does_not_verifiably_apply(parameters, problem):
    from kriya.core import model_qualification as mq

    config = AppConfig()
    config.llm.model = "m:1"
    config.llm.context_window = 32768
    config.llm.extra_body = {"options": {"top_p": 0.8, "top_k": 20}}
    with pytest.raises(mq.QualificationError) as refused:
        _qualify(config, _pinned_fp(**parameters))
    assert "QUALIFICATION_IDENTITY_UNVERIFIED" in str(refused.value) and problem in str(refused.value)


def test_qualification_proceeds_on_a_model_pinned_to_the_binding():
    from kriya.core import model_qualification as mq

    config = AppConfig()
    config.llm.model = "m:1"
    config.llm.context_window = 32768
    config.llm.extra_body = {"options": {"top_p": 0.8, "top_k": 20}}
    record = _qualify(config, _pinned_fp(num_ctx=32768, top_k=20))
    assert record["policy_version"] == mq.QUALIFICATION_POLICY_VERSION == "kriya-qualification/5"


def test_a_policy_3_record_is_stale():
    from kriya.core import model_qualification as mq
    from kriya.core.inference_settings import request_settings

    settings = request_settings(temperature=0.2, reasoning=False, extra_body=None)
    fingerprint = _pinned_fp(num_ctx=32768)
    record = mq.build_record(fingerprint, [], settings=settings)
    assert mq.record_is_current(record, fingerprint, settings)[0]
    record["policy_version"] = "kriya-qualification/3"
    current, reasons = mq.record_is_current(record, fingerprint, settings)
    assert not current and any("qualification policy changed" in reason for reason in reasons)


@pytest.mark.parametrize(("served", "reason"), [(65536, RUNTIME_CONTEXT_IDENTITY_MISMATCH),
                                                (16384, SERVED_CONTEXT_BELOW_REQUESTED)])
def test_the_capacity_case_fails_when_the_served_window_is_not_the_qualified_one(served, reason):
    from kriya.config.config import ModelQualificationConfig
    from kriya.core import model_qualification as mq
    from kriya.core.inference_runtime import RuntimeCapabilities
    from kriya.core.provider_contract import ProviderRequestPlan

    class _Runtime:
        capabilities = RuntimeCapabilities(per_request_context_window=False, native_identity_probe=False)

        def request_plan(self, extra_body, **_):
            return ProviderRequestPlan(wire_body={}, settings=())

        def observe_served_context(self, **_):
            return served

        async def complete(self, client, request):
            text = "".join(m["content"] for m in request.messages)
            if request.max_tokens == 1:
                return ChatResponse(content="", prompt_tokens=len(text) // 4, finish_reason="length")
            head = request.messages[0]["content"].split("first code is ")[1].split(".")[0]
            tail = request.messages[1]["content"].split("second code is ")[1].split(".")[0]
            return ChatResponse(content=f"{head} {tail}", prompt_tokens=len(text) // 4, finish_reason="stop")

    ctx = {"policy": ModelQualificationConfig(), "context_window": 32768, "runtime": _Runtime(),
           "base_url": "http://localhost:11434/v1",
           "client_factory": lambda timeout: SimpleNamespace(client=object())}
    result = asyncio.run(mq.case_context_capacity(None, "m:1", ctx))
    assert result.status == mq.FAIL and result.evidence["reason_code"] == reason
    assert result.evidence["served_context_window"] == served


def test_the_tokenizer_case_measures_a_consumption_ceiling_never_below_the_calibrated_default():
    from kriya.config.config import ModelQualificationConfig
    from kriya.core import model_qualification as mq
    from kriya.core.provider_contract import DEFAULT_BYTES_PER_TOKEN_CEILING, consumption_bytes

    class _Tokenizer:
        def __init__(self, bytes_per_token):
            self.bytes_per_token = bytes_per_token

        async def complete_result(self, system, text, **_):
            return SimpleNamespace(prompt_tokens=max(1, int(len(text.encode()) / self.bytes_per_token)),
                                   tokens_estimated=False)

    ctx = {"policy": ModelQualificationConfig()}
    ordinary = asyncio.run(mq.case_tokenizer_measurement(_Tokenizer(4.0), "m:1", ctx))
    assert ordinary.measured["bytes_per_token_ceiling"] == DEFAULT_BYTES_PER_TOKEN_CEILING
    # A far more compressive tokenizer raises the ceiling (a truncation verdict never becomes a false positive).
    dense = asyncio.run(mq.case_tokenizer_measurement(_Tokenizer(20.0), "m:1", ctx))
    text = mq.TOKENIZER_CORPORA["indented"]
    tokens = max(1, int(len(text.encode()) / 20.0))
    assert dense.measured["bytes_per_token_ceiling"] >= consumption_bytes(text) / tokens / 0.9 - 1e-3
    assert dense.measured["bytes_per_token_ceiling"] > DEFAULT_BYTES_PER_TOKEN_CEILING


def test_the_runtime_tokenizer_ceiling_is_the_largest_any_current_record_measured():
    """A floor is conservative when smallest; a ceiling when largest - a
    smaller ceiling would call honest prompts truncated."""
    from kriya.core import model_qualification as mq
    from kriya.core.inference_settings import request_settings

    fingerprint = _pinned_fp(num_ctx=32768)
    first = request_settings(temperature=0.2, reasoning=False, extra_body=None)
    second = request_settings(temperature=0.7, reasoning=False, extra_body=None)
    for settings, ceiling, floor in ((first, 8.0, 3.0), (second, 9.5, 2.5)):
        record = mq.build_record(fingerprint, [], settings=settings)
        record["measured_limits"] = {"bytes_per_token_ceiling": ceiling, "bytes_per_token_floor": floor}
        mq.save_record(record)
    limits = mq.measured_limits_for(fingerprint, settings=first)
    assert limits["bytes_per_token_ceiling"] == 9.5 and limits["bytes_per_token_floor"] == 2.5


def test_the_qualification_timeout_probe_really_times_out(server):
    """Live qualification (2026-09-30) reported timeout_semantics FAIL with
    OUTPUT_TRUNCATED: the probe's 1 ms client timeout was overridden by the
    per-request Kriya transport timeout, so a slow request simply completed.
    The probe's timeout must be the one its requests carry."""
    from kriya.core import model_qualification as mq

    server.delay = 0.5
    config = _wire_config(server.url)
    result = asyncio.run(mq.case_timeout_semantics(None, "m:1", {
        "client_factory": mq.qualification_client_factory(config, "m:1")}))
    assert result.status == mq.PASS, result.evidence
    assert result.evidence["status"] == "TIMEOUT"


def test_a_qualification_probe_client_carries_the_kriya_transport_policy():
    from kriya.core import model_qualification as mq

    probe = mq.qualification_client_factory(AppConfig(), AppConfig().llm.model)(42.0)
    try:
        assert probe.client.max_retries == 0 and probe.client._client._trust_env is False  # pylint: disable=protected-access
        assert probe._request_timeout().read == 42.0  # pylint: disable=protected-access
    finally:
        asyncio.run(probe.aclose())


def test_the_doctor_controlled_probe_never_writes_to_stdout(fake, capsys):
    """Live `doctor --production --json` (2026-09-30): the controlled probe's
    usage line reached stdout ahead of the JSON document, breaking it."""
    from kriya.production_doctor import probe_served_context

    evidence = probe_served_context(_fake_config(), "primary:1")
    assert evidence["probe_request_sent"] is True and fake.requests
    assert capsys.readouterr().out == ""


def test_a_per_request_window_is_labelled_as_carried_by_the_request(monkeypatch):
    """Live L1 (2026-09-30): native calls reported window_source
    server_model_config although the window travels with each request."""
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    adapter = FakeRuntimeAdapter("fake_per_request", per_request_context_window=True)
    register_runtime_adapter(adapter)
    model_runtime.clear_model_runtime_cache()
    try:
        config = _fake_config()
        config.llm.inference_runtime = "fake_per_request"
        result = asyncio.run(LLMClient(config).complete_result("s", "u"))
    finally:
        unregister_runtime_adapter(adapter.name)
        model_runtime.clear_model_runtime_cache()
    assert result.budget["window_source"] == "requested_per_request"


def test_a_stalled_client_never_hangs_the_test_server_teardown():
    """Closure suite at 3eb5718 hung: the timeout probe connected, timed out
    before sending its request line and was never closed, so the test
    server's single serving thread blocked in readline and shutdown()
    waited forever."""
    import socket

    endpoint = _Server()
    stalled = socket.create_connection(("127.0.0.1", endpoint.httpd.server_port))
    try:
        closer = threading.Thread(target=endpoint.close, daemon=True)
        closer.start()
        closer.join(timeout=10)
        assert not closer.is_alive(), "server teardown blocked by a stalled client"
    finally:
        stalled.close()


def test_the_qualification_probe_clients_are_closed_after_the_timeout_case(server):
    from kriya.core import model_qualification as mq

    made = []
    factory = mq.qualification_client_factory(_wire_config(server.url), "m:1")
    server.delay = 0.5
    asyncio.run(mq.case_timeout_semantics(None, "m:1", {"client_factory": lambda timeout: made.append(
        factory(timeout)) or made[-1]}))
    assert made and all(probe._clients == {} for probe in made)  # pylint: disable=protected-access

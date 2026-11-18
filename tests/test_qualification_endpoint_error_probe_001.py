"""QUALIFICATION-ENDPOINT-ERROR-PROBE-001 (owner ruling 2026-11-18).

MEASURED (requalification of both production pins at 949bdf8): the
``endpoint_error_semantics`` case named an unbound model through ``LLMClient``,
and since S1 (C-09) the client refuses every unbound name before provider
contact (``MODEL_BINDING_UNKNOWN``), so the case could never reach the
provider and no model could be freshly qualified. The deterministic case
tests scripted a fake client and could not see it.

The fix keeps S1 unchanged. The case sends one code-owned reserved model name
on the provider connection of the binding under qualification, through that
binding's runtime adapter. These tests run the case through the real client
transport and the real adapters against loopback servers, and assert what
arrives on the wire."""
import asyncio
import inspect
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig, ModelCapabilities
from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core.completion import CompletionStatus
from kriya.core.inference_runtime import RuntimeErrorKind, runtime_adapter, runtime_for_binding
from kriya.core.inference_settings import role_inference_settings
from kriya.core.llm import LLMClient, failure_status
from kriya.core.model_runtime import MODEL_BINDING_UNKNOWN, UnknownModelBindingError

REPO = Path(__file__).resolve().parents[1]
RESERVED = "kriya-qualification-no-such-model:0"  # pinned literally: the test must not follow a renamed constant
PRIMARY = "primary:1"
QUALIFIED = "fb:1"  # the binding under qualification: a fallback with its own endpoint and key
RUNTIMES = [None, "ollama_native"]  # the packaged default (/v1) and the native adapter
CHAT_PATHS = ("/v1/chat/completions", "/api/chat")


class _Loopback(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class _Provider:
    """A loopback provider speaking both wire protocols. ``status`` 200
    answers a completion; anything else answers that status with ``error``."""

    def __init__(self):
        self.requests = []  # (path, body, authorization)
        self.status = 200
        self.error = "boom"
        self.delay = 0.0
        server = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 5

            def log_message(self, *args):
                pass

            def do_GET(self):
                self._send(404, b"{}")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append((self.path, body, self.headers.get("Authorization")))
                time.sleep(server.delay)
                native = self.path == "/api/chat"
                if server.status != 200:
                    error = server.error if native else {"message": server.error, "type": "error"}
                    self._send(server.status, json.dumps({"error": error}).encode())
                elif native and body.get("stream"):
                    lines = [{"message": {"content": "hel"}, "done": False, "model": body["model"]},
                             {"message": {"content": "lo"}, "done": True, "done_reason": "stop",
                              "prompt_eval_count": 0, "eval_count": 2}]
                    self._send(200, "".join(json.dumps(line) + "\n" for line in lines).encode(),
                               "application/x-ndjson")
                elif native:
                    self._send(200, json.dumps({
                        "message": {"role": "assistant", "content": "hello"}, "done": True, "done_reason": "stop",
                        "prompt_eval_count": 0, "eval_count": 1, "model": body["model"]}).encode())
                else:
                    self._send(200, json.dumps({
                        "id": "c1", "object": "chat.completion", "created": 0, "model": body["model"],
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "hello"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode())

            def _send(self, status, payload, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = _Loopback(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def chats(self):
        return [request for request in self.requests if request[0] in CHAT_PATHS]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def providers():
    """(the primary's endpoint, the endpoint of the binding under qualification)."""
    primary, own = _Provider(), _Provider()
    yield primary, own
    primary.close()
    own.close()


def _config(primary_url, own_url, runtime=None, window=32768):
    config = AppConfig()
    config.llm.model = PRIMARY
    config.llm.base_url = primary_url
    config.llm.api_key = "primary-key"
    config.llm.context_window = window
    config.llm_chain = [FallbackModelConfig(model=QUALIFIED, base_url=own_url, api_key="fb-key",
                                            context_window=window, inference_runtime=runtime)]
    return config


def _qualifying(config, model=QUALIFIED):
    """The run's own configuration and case context, built by the functions
    ``run_qualification`` uses (no hand-made context)."""
    settings = role_inference_settings(config, "developer", model)
    qualifying = mq.qualification_config(config, model, settings=settings)
    runtime = runtime_for_binding(model_runtime._binding_for(qualifying, model))
    ctx = mq.case_context(qualifying, model, settings=settings, runtime=runtime,
                          fingerprint=SimpleNamespace(effective_context_window=config.llm.context_window))
    return qualifying, ctx


def _run_case(config, case=None, model=QUALIFIED, ctx_changes=None, remove=()):
    async def run():
        qualifying, ctx = _qualifying(config)
        ctx.update(ctx_changes or {})
        for name in remove:
            del ctx[name]
        llm = LLMClient(qualifying, purpose="qualification")
        try:
            return await (case or mq.case_endpoint_error_semantics)(llm, model, ctx)
        finally:
            await llm.aclose()

    return asyncio.run(run())


# --- the real client transport and the real adapters, on the wire ---------------------------------------

@pytest.mark.parametrize("runtime", RUNTIMES)
def test_the_reserved_model_refused_by_the_provider_passes_on_the_bindings_own_connection(providers, runtime):
    primary, own = providers
    own.status, own.error = 404, f"model '{RESERVED}' not found"
    result = _run_case(_config(primary.url, own.url, runtime))
    assert result.status == mq.PASS, result.evidence
    # Exactly one request, on the endpoint and with the key of the binding under qualification ...
    assert len(own.requests) == 1 and primary.requests == []
    path, body, authorization = own.requests[0]
    assert path == ("/api/chat" if runtime else "/v1/chat/completions")
    assert authorization == "Bearer fb-key"
    # ... naming the reserved model and nothing else.
    assert body["model"] == RESERVED
    if runtime:  # the native wire carries the qualified window per request, as a production request does
        assert body["options"]["num_ctx"] == 32768 and body["truncate"] is False
    evidence = result.evidence
    assert (evidence["status"], evidence["error_kind"], evidence["answered"]) == ("BACKEND_ERROR", "request", False)
    assert evidence["probe_model"] == RESERVED and evidence["endpoint"] == own.url
    assert evidence["adapter"] == (runtime or runtime_adapter().name)
    assert "not found" in evidence["backend_error"]


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_a_provider_that_answers_the_reserved_model_fails(providers, runtime):
    primary, own = providers
    result = _run_case(_config(primary.url, own.url, runtime))  # 200 with an answer
    assert result.status == mq.FAIL
    assert result.evidence["answered"] is True and result.evidence["content"] == "hello"
    assert len(own.requests) == 1 and own.requests[0][1]["model"] == RESERVED and primary.requests == []


@pytest.mark.parametrize("runtime", RUNTIMES)
@pytest.mark.parametrize(("status", "kind"), [(401, "authentication"), (429, "rate_limited"), (500, "server")])
def test_a_refusal_that_is_not_the_unknown_model_answer_fails(providers, runtime, status, kind):
    """A BACKEND_ERROR alone is not the measured semantics: an expired key, a
    rate limit or a server error says nothing about an unknown model."""
    primary, own = providers
    own.status = status
    result = _run_case(_config(primary.url, own.url, runtime))
    assert result.status == mq.FAIL
    assert (result.evidence["status"], result.evidence["error_kind"]) == ("BACKEND_ERROR", kind)
    assert len(own.requests) == 1 and primary.requests == []


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_an_unreachable_endpoint_fails_and_nothing_goes_to_another_binding(providers, runtime):
    """No fallback: the binding's endpoint is down, the primary's is up and is never asked."""
    primary, own = providers
    own.close()
    result = _run_case(_config(primary.url, own.url, runtime))
    assert result.status == mq.FAIL
    assert (result.evidence["status"], result.evidence["error_kind"]) == ("BACKEND_ERROR", "connection")
    assert primary.requests == []


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_a_provider_that_does_not_answer_in_time_fails_as_a_timeout(providers, runtime):
    primary, own = providers
    own.delay = 1.0
    config = _config(primary.url, own.url, runtime)
    config.llm.transport.read_timeout_seconds = 0.2  # the configured transport is the probe's (factory(None))
    result = _run_case(config)
    assert result.status == mq.FAIL
    assert (result.evidence["status"], result.evidence["error_kind"]) == ("TIMEOUT", "timeout")
    assert primary.requests == []


# --- S1 is unchanged: the client refuses every unbound name, the reserved one included --------------------

@pytest.mark.parametrize("purpose", ["run", "qualification", "preflight"])
@pytest.mark.parametrize("name", [RESERVED, "not-configured:1"])
def test_the_client_still_refuses_every_unbound_name_before_provider_contact(providers, purpose, name):
    primary, own = providers
    config = _config(primary.url, own.url)

    async def run():
        llm = LLMClient(config, purpose=purpose)
        try:
            with pytest.raises(UnknownModelBindingError) as refused:
                await llm.complete_result("s", "u", model_override=name)
            with pytest.raises(UnknownModelBindingError):
                await llm.complete_with_tools_result([{"role": "user", "content": "u"}], [], model_override=name)
            with pytest.raises(UnknownModelBindingError):
                llm._binding(name)
            return refused.value
        finally:
            await llm.aclose()

    refused = asyncio.run(run())
    assert refused.reason_code == MODEL_BINDING_UNKNOWN and refused.model == name
    assert primary.requests == [] and own.requests == []
    with pytest.raises(UnknownModelBindingError):
        model_runtime.require_binding(config, name)


# --- the probe takes no model name and needs the resolved binding ------------------------------------------

def test_the_probe_takes_no_model_name_and_ignores_the_case_arguments(providers):
    primary, own = providers
    own.status = 404
    assert list(inspect.signature(mq._probe_unserved_model).parameters) == ["ctx"]
    result = _run_case(_config(primary.url, own.url), model="attacker-chosen:1",
                       ctx_changes={"model": "attacker-chosen:1", "probe_model": "attacker-chosen:1"})
    assert result.status == mq.PASS
    assert [body["model"] for _path, body, _auth in own.requests] == [RESERVED]


class _SpyRuntime:
    name = "spy"

    def __init__(self):
        self.completions = 0

    async def complete(self, client, request):
        self.completions += 1
        raise AssertionError("provider contacted")


@pytest.mark.parametrize("missing", ["runtime", "client_factory", "base_url"])
def test_without_the_resolved_binding_nothing_is_sent_and_nothing_falls_back(providers, missing):
    """The capacity cases fall back to the default adapter and the client's
    own (primary) transport when the context lacks them; the probe never does."""
    primary, own = providers
    result = _run_case(_config(primary.url, own.url), remove=[missing])
    assert result.status == mq.FAIL and result.evidence["missing"] == [missing]
    assert primary.requests == [] and own.requests == []


def test_an_empty_context_is_a_fail_before_any_contact():
    result = asyncio.run(mq.case_endpoint_error_semantics(None, QUALIFIED, {}))
    assert result.status == mq.FAIL
    assert result.evidence["missing"] == ["runtime", "client_factory", "base_url"]


def test_a_non_local_endpoint_is_unavailable_and_never_contacted():
    spy, made = _SpyRuntime(), []
    ctx = {"runtime": spy, "client_factory": lambda timeout: made.append(timeout),
           "base_url": "https://api.example.com/v1"}
    result = asyncio.run(mq.case_endpoint_error_semantics(None, QUALIFIED, ctx))
    assert result.status == mq.UNAVAILABLE and made == [] and spy.completions == 0


def test_the_probe_client_keeps_the_configured_transport_and_is_closed():
    class _Runtime(_SpyRuntime):
        def request_plan(self, extra_body, **kw):
            return SimpleNamespace(wire_body=extra_body)

        capabilities = SimpleNamespace(per_request_context_window=False)

        def classify_error(self, error):
            return RuntimeErrorKind.REQUEST

        async def complete(self, client, request):
            self.request, self.client = request, client
            raise RuntimeError("404 model not found")

    class _Probe:
        client = object()
        closed = False

        async def aclose(self):
            self.closed = True

    runtime, probe, timeouts = _Runtime(), _Probe(), []
    ctx = {"runtime": runtime, "client_factory": lambda timeout: timeouts.append(timeout) or probe,
           "base_url": "http://127.0.0.1:1/v1", "extra_body": {"top_p": 0.5}}
    result = asyncio.run(mq.case_endpoint_error_semantics(None, QUALIFIED, ctx))
    assert result.status == mq.PASS, result.evidence
    assert timeouts == [None] and probe.closed is True
    assert runtime.client is probe.client
    assert runtime.request.model == RESERVED and runtime.request.extra_body == {"top_p": 0.5}


def test_the_factory_changes_the_transport_only_for_a_given_timeout():
    config = _config("http://127.0.0.1:1/v1", "http://127.0.0.1:2/v1")
    config.llm.transport.read_timeout_seconds = 77.0
    factory = mq.qualification_client_factory(config, QUALIFIED)
    unchanged, short = factory(None), factory(0.5)
    assert unchanged.config.llm.transport.read_timeout_seconds == 77.0
    assert short.config.llm.transport.read_timeout_seconds == 0.5
    for client in (unchanged, short):
        assert (client.config.llm.base_url, client.config.llm.api_key) == ("http://127.0.0.1:2/v1", "fb-key")
        assert client.purpose == "qualification"


def test_the_shared_classifier_maps_only_a_timeout_to_timeout():
    native = runtime_adapter("ollama_native")
    assert failure_status(native, httpx.ReadTimeout("t")) is CompletionStatus.TIMEOUT
    assert failure_status(native, httpx.ConnectError("c")) is CompletionStatus.BACKEND_ERROR
    assert failure_status(native, model_runtime.NativeRuntimeError(404, "no")) is CompletionStatus.BACKEND_ERROR
    assert failure_status(runtime_adapter(), asyncio.TimeoutError()) is CompletionStatus.TIMEOUT
    assert failure_status(runtime_adapter(), RuntimeError("x")) is CompletionStatus.BACKEND_ERROR


# --- reachability: one constant, one sender, qualification only ----------------------------------------

def _product_sources():
    return sorted((REPO / "kriya").rglob("*.py")) + sorted((REPO / "plugins").rglob("*.py"))


def test_the_reserved_name_and_the_probe_exist_only_in_the_qualification_module():
    literal, constant, probe = {}, {}, {}
    for path in _product_sources():
        text = path.read_text(encoding="utf-8")
        name = str(path.relative_to(REPO))
        for found, pattern in ((literal, re.escape(RESERVED)), (constant, r"_UNSERVED_MODEL_PROBE\b"),
                               (probe, r"_probe_unserved_model\b")):
            count = len(re.findall(pattern, text))
            if count:
                found[name] = count
    module = "kriya/core/model_qualification.py"
    assert literal == {module: 1}  # defined once, never repeated as a string
    assert set(constant) == {module} and set(probe) == {module}
    source = (REPO / module).read_text(encoding="utf-8")
    # One sender: the constant is put on the wire by exactly one ChatRequest ...
    assert len(re.findall(r"model=_UNSERVED_MODEL_PROBE\b", source)) == 1
    # ... and the probe is called by exactly one case.
    assert len(re.findall(r"await _probe_unserved_model\(", source)) == 1
    assert mq._UNSERVED_MODEL_PROBE == RESERVED
    assert "_UNSERVED_MODEL_PROBE" not in mq.__all__ and "_probe_unserved_model" not in mq.__all__


def test_the_reserved_name_is_not_configuration_and_binds_nothing():
    assert RESERVED not in json.dumps(AppConfig.model_json_schema())
    assert RESERVED not in AppConfig().model_dump_json()
    assert model_runtime.binding_object(AppConfig(), RESERVED) is None


# --- drift guard: every case reaches the provider through the real client -------------------------------

# A case listed here does not have to put a request on the binding's endpoint; each says why.
PROVIDER_INDEPENDENT = {
    "endpoint_restart_semantics": "not exercised live by design: always UNAVAILABLE, never a provider request",
    "timeout_semantics": "cut by a 1 ms client timeout, so whether bytes arrive is the case's own subject; "
                         "asserted instead to be a TIMEOUT measured by the real client",
}


def test_every_qualification_case_reaches_the_bindings_provider_through_the_real_client(providers):
    """The scripted client of the per-case tests accepts any model name, so a
    case that names a model the real client refuses passed them (the
    2026-11-18 defect). Here every case runs with the real ``LLMClient``, the
    real probe-client factory and the real native adapter against a loopback
    provider: each must put at least one request on the endpoint of the
    binding under qualification, none on the primary's, and none may be
    refused by Kriya's own binding authority."""
    primary, own = providers
    own.delay = 0.05  # so the 1 ms timeout case always times out
    config = _config(primary.url, own.url, "ollama_native")
    # Every protocol declared, so no case is skipped (or refused by the client) by capability profile.
    config.llm_chain[0].capabilities = ModelCapabilities(native_tool_calls=True, json_mode=True,
                                                         reliable_multiline_json=True, streaming=True)
    assert set(PROVIDER_INDEPENDENT) < set(mq.CAPABILITIES)

    async def run():
        qualifying, ctx = _qualifying(config)
        assert ctx["native_tool_calls_enabled"] is True
        llm = LLMClient(qualifying, purpose="qualification")
        seen = {}
        try:
            for case in mq.ALL_CASES:
                before = len(own.chats)
                result = await case(llm, QUALIFIED, ctx)
                seen[case.capability] = (len(own.chats) - before, result)
        finally:
            await llm.aclose()
        return seen

    seen = asyncio.run(run())
    assert set(seen) == set(mq.CAPABILITIES)
    for capability, (requests, result) in seen.items():
        evidence = json.dumps(result.evidence, default=str)
        assert MODEL_BINDING_UNKNOWN not in evidence, (capability, evidence)
        if capability not in PROVIDER_INDEPENDENT:
            assert requests >= 1, (capability, result.status, evidence)
    assert seen["timeout_semantics"][1].evidence["status"] == "TIMEOUT"
    assert seen["endpoint_restart_semantics"][1].status == mq.UNAVAILABLE
    assert primary.requests == []
    # Every request named the binding under qualification, except the one reserved probe.
    named = [body["model"] for _path, body, _auth in own.chats]
    assert named.count(RESERVED) == 1 and set(named) == {QUALIFIED, RESERVED}

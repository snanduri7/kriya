"""MODEL-IDENTITY-EXTRA-BODY-OVERRIDE-001 (Wave-2 D-002): ``extra_body`` is not a second model selector.

MEASURED at abc2d67 with the real openai SDK against a loopback server: a repository ``kriya.yaml`` with ``llm.extra_body.model``
was accepted without approval, ``ChatRequest.model`` stayed the governed model A, and the SDK sent B on the wire (every field Kriya's
``wire_payload`` sets - model, messages, temperature, max_tokens, tools, tool_choice, stream, stream_options, response_format - was
overridden the same way, chat and tool paths alike; the native adapter spreads ``**extra_body`` after its own ``model``). Qualification,
the fingerprint and the evidence named A while B served.

The correction (owner decision, option A): a provider extension payload may not override a Kriya-owned request field. It is refused
(typed ``PROVIDER_REQUEST_FIELD_CONFLICT``, never stripped) at config validation, at the LLMClient entry before any provider contact, and
at ``ChatRequest`` construction for every other source. The evidence then carries two separate facts: ``requested_model`` (the request's
model at the invocation boundary) and ``reported_model`` (what the response named - absent when it named nothing, never copied from the
request, never labelled a substitution).

Production-shaped: the real LLMClient, the real adapters, the real SDK / httpx transport and a loopback HTTP server that counts EVERY
request (GET probes included), and the attempt-evidence reader for persistence."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig, LLMConfig, ModelCapabilities, load_config
from kriya.config.request_fields import RESERVED_REQUEST_FIELDS
from kriya.core.attempt_evidence import reader, scope
from kriya.core.inference_runtime import ChatRequest
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import OllamaNativeRuntimeAdapter, OllamaRuntimeAdapter
from kriya.core.provider_contract import (
    PROVIDER_CONTRACT_REASON_CODES,
    PROVIDER_REQUEST_FIELD_CONFLICT,
    ProviderContractError,
)
from kriya.core.role_metrics import model_role
from kriya.core.state_paths import ENV_STATE_DIR

GOVERNED = "governed:1"
OTHER = "some-other-unqualified-model:latest"
TOOLS = [{"type": "function", "function": {"name": "find_symbol", "parameters": {"type": "object"}}}]


class _Loopback(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class _Server:
    """Records every request (method, path, parsed body) and answers chat requests with ``reply_model`` as the response's model
    (``None`` omits the field)."""

    def __init__(self, reply_model="echo"):
        self.reply_model = reply_model
        self.received = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 5

            def log_message(self, *args):
                pass

            def _answer(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                server.received.append((method, self.path, body))
                if method != "POST":
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                sent = (body or {}).get("model")
                reply = sent if server.reply_model == "echo" else server.reply_model
                if self.path.endswith("/api/chat"):
                    out = {"message": {"role": "assistant", "content": "ok"}, "done": True, "done_reason": "stop",
                           "prompt_eval_count": 1, "eval_count": 1}
                else:
                    out = {"id": "x", "object": "chat.completion", "created": 0,
                           "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                if reply is not None:
                    out["model"] = reply
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                self._answer("POST")

            def do_GET(self):
                self._answer("GET")

        self.httpd = _Loopback(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def wire_models(self):
        return [body.get("model") for method, _path, body in self.received if method == "POST" and body]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    srv = _Server()
    try:
        yield srv
    finally:
        srv.close()


def _config(url, *, runtime=None, extra_body=None):
    cfg = AppConfig()
    cfg.llm.base_url = url
    cfg.llm.model = GOVERNED
    cfg.llm.context_window = 8192
    cfg.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    if runtime:
        cfg.llm.inference_runtime = runtime
    # Assignment is not validated (model_config has no validate_assignment): the request boundary must hold on its own.
    cfg.llm.extra_body = dict(extra_body or {"options": {"top_p": 0.8}})
    cfg.llm_chain = []
    return cfg


class _Context:
    def __init__(self, run_id):
        self.run_id = run_id


def _run(tmp_path, monkeypatch, cfg, call):
    """One call through the real LLMClient inside an attempt-evidence run; returns (outcome, error, records)."""
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))
    outcome, error = None, None

    async def body():
        llm = LLMClient(cfg)
        try:
            with model_role("developer"):
                return await call(llm)
        finally:
            await llm.aclose()
    with scope.run_scope(_Context("run-d002")):
        scope.ensure_store(cfg)
        try:
            outcome = asyncio.run(body())
        except ProviderContractError as refusal:
            error = refusal
        scope._RUN.get().writer.seal("test")
    run = reader.open_run(str(tmp_path / "state"), "run-d002")
    return outcome, error, list(run.records())


def _kind(records, kind):
    return [r for r in records if r["kind"] == kind]


CHAT = lambda llm: llm.complete_result("SYS", "USER")  # noqa: E731 - one-line call table
TOOL_TURN = lambda llm: llm.complete_with_tools_result([{"role": "user", "content": "find x"}], TOOLS)  # noqa: E731
RUNTIMES = [pytest.param(None, id="openai_compatible"), pytest.param("ollama_native", id="ollama_native")]


# -- 1. a normal request: configured == request == wire == requested_model ----------------------------------------------------------
@pytest.mark.parametrize("runtime", RUNTIMES)
def test_a_normal_request_names_the_governed_model_everywhere(tmp_path, monkeypatch, server, runtime):
    cfg = _config(server.url, runtime=runtime)
    result, error, records = _run(tmp_path, monkeypatch, cfg, CHAT)
    assert error is None and result.model == GOVERNED
    assert server.wire_models == [GOVERNED]
    request = _kind(records, "model.request")
    assert len(request) == 1 and request[0]["payload"]["dispatched"] is True
    assert request[0]["payload"]["requested_model"] == request[0]["payload"]["model"] == GOVERNED
    assert _kind(records, "model.result")[0]["payload"]["requested_model"] == GOVERNED
    # the recorded wire body's model is the model the server observed (catches any future SDK-merge regression)
    run = reader.open_run(str(tmp_path / "state"), "run-d002")
    assert json.loads(run.blob(request[0]["blobs"]["wire_body"]))["model"] == server.wire_models[0]


# -- 2/3/4. extra_body.model (and every other Kriya-owned field) is refused before ANY provider contact --------------------------------
@pytest.mark.parametrize("runtime", RUNTIMES)
@pytest.mark.parametrize("field", RESERVED_REQUEST_FIELDS)
@pytest.mark.parametrize("call", [pytest.param(CHAT, id="chat"), pytest.param(TOOL_TURN, id="tool_turn")])
def test_a_binding_extra_body_naming_an_owned_field_is_refused_with_zero_provider_calls(
        tmp_path, monkeypatch, server, runtime, field, call):
    cfg = _config(server.url, runtime=runtime, extra_body={field: OTHER if field == "model" else "x"})
    outcome, error, records = _run(tmp_path, monkeypatch, cfg, call)
    assert outcome is None
    assert isinstance(error, ProviderContractError) and error.reason_code == PROVIDER_REQUEST_FIELD_CONFLICT
    assert error.details["fields"] == [field] and error.details["requested_model"] == GOVERNED
    assert str(error).startswith(f"{PROVIDER_REQUEST_FIELD_CONFLICT}:")
    assert server.received == []                                   # no POST, and no GET probe either
    # nothing claims an execution: no dispatched request, no response, no result - at most the refusal itself, marked undispatched
    assert [r["payload"] for r in _kind(records, "model.request") if r["payload"].get("dispatched") is not False] == []
    assert _kind(records, "model.response") == [] and _kind(records, "model.result") == []
    assert all(r["payload"]["reason_code"] == PROVIDER_REQUEST_FIELD_CONFLICT for r in _kind(records, "model.request"))


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_a_caller_extra_body_override_is_refused_the_same_way(tmp_path, monkeypatch, server, runtime):
    cfg = _config(server.url, runtime=runtime)
    outcome, error, records = _run(
        tmp_path, monkeypatch, cfg, lambda llm: llm.complete_result("S", "U", extra_body_override={"model": OTHER}))
    assert outcome is None and error.reason_code == PROVIDER_REQUEST_FIELD_CONFLICT
    assert server.received == [] and not [r for r in _kind(records, "model.request") if r["payload"].get("dispatched")]


def test_the_tool_path_override_argument_is_refused_the_same_way(tmp_path, monkeypatch, server):
    cfg = _config(server.url)
    outcome, error, _records = _run(
        tmp_path, monkeypatch, cfg,
        lambda llm: llm.complete_with_tools_result([{"role": "user", "content": "x"}], TOOLS, extra_body_override={"model": OTHER}))
    assert outcome is None and error.reason_code == PROVIDER_REQUEST_FIELD_CONFLICT and server.received == []


def test_the_request_boundary_refuses_a_request_built_anywhere_else():
    # qualification probes, the doctor and any future caller construct ChatRequest directly
    for field in RESERVED_REQUEST_FIELDS:
        with pytest.raises(ProviderContractError) as raised:
            ChatRequest(model=GOVERNED, messages=[], temperature=0.1, max_tokens=8, extra_body={"options": {}, field: OTHER})
        assert raised.value.reason_code == PROVIDER_REQUEST_FIELD_CONFLICT and raised.value.details["fields"] == [field]
    ChatRequest(model=GOVERNED, messages=[], temperature=0.1, max_tokens=8, extra_body={"options": {"num_ctx": 1}, "reasoning_effort": "none"})


def test_the_reserved_set_is_the_measured_collision_set_and_covers_every_field_the_wire_payload_sets():
    assert set(RESERVED_REQUEST_FIELDS) == {"model", "messages", "temperature", "max_tokens", "tools", "tool_choice", "stream",
                                            "stream_options", "response_format"}
    base = dict(model=GOVERNED, messages=[{"role": "user", "content": "u"}], temperature=0.1, max_tokens=8)
    shapes = [dict(), dict(tools=TOOLS), dict(response_format={"type": "json_object"}), dict(stream_callback=lambda _t: None)]
    for adapter in (OllamaRuntimeAdapter(), OllamaNativeRuntimeAdapter()):
        for shape in shapes:
            request = ChatRequest(**base, **shape)
            for stream in (False, True):
                set_by_kriya = set(adapter.wire_payload(request, stream=stream)) - {"extra_body", "options", "truncate", "format"}
                # a top-level field Kriya's own wire builder sets must not be overridable by extra_body
                assert set_by_kriya <= set(RESERVED_REQUEST_FIELDS), (adapter.name, shape, set_by_kriya - set(RESERVED_REQUEST_FIELDS))


def test_the_reason_code_is_a_typed_provider_contract_stop():
    assert PROVIDER_REQUEST_FIELD_CONFLICT in PROVIDER_CONTRACT_REASON_CODES      # outcome.py reads this set to name the stop


# -- B1 (Wave-2): the native runtime's ``format`` is Kriya's structured-output carrier, so it is Kriya-owned there ----------------------
# MEASURED at 04c8db4 (WAVE2_STEP11 B1): a repository-safe llm.extra_body.format reached /api/chat unchanged when Kriya sent no structured
# request (a cloned repo could grammar-constrain Kriya's unstructured calls) and was silently overwritten when Kriya sent one.
NATIVE_FORMATS = [pytest.param("json", id="json"), pytest.param({"type": "object", "properties": {"x": {"type": "string"}}}, id="schema")]


@pytest.mark.parametrize("fmt", NATIVE_FORMATS)
@pytest.mark.parametrize("call", [pytest.param(CHAT, id="chat"), pytest.param(TOOL_TURN, id="tool_turn")])
def test_b1_a_native_extra_body_format_is_refused_with_zero_provider_calls(tmp_path, monkeypatch, server, fmt, call):
    cfg = _config(server.url, runtime="ollama_native", extra_body={"options": {"top_p": 0.8}, "format": fmt})
    outcome, error, records = _run(tmp_path, monkeypatch, cfg, call)
    assert outcome is None
    assert isinstance(error, ProviderContractError) and error.reason_code == PROVIDER_REQUEST_FIELD_CONFLICT
    assert error.details["fields"] == ["format"] and error.details["requested_model"] == GOVERNED
    assert server.received == []                                   # no POST, and no GET probe either
    assert [r for r in _kind(records, "model.request") if r["payload"].get("dispatched")] == []


def test_b1_a_caller_override_of_format_is_refused_on_the_native_runtime(tmp_path, monkeypatch, server):
    cfg = _config(server.url, runtime="ollama_native")
    outcome, error, _records = _run(
        tmp_path, monkeypatch, cfg, lambda llm: llm.complete_result("S", "U", extra_body_override={"format": "json"}))
    assert outcome is None and error.reason_code == PROVIDER_REQUEST_FIELD_CONFLICT and error.details["fields"] == ["format"]
    assert server.received == []


def test_b1_the_native_adapter_refuses_it_for_a_request_built_anywhere_else():
    """Qualification probes and the doctor build ChatRequest directly: the wire builder is the last gate before the transport."""
    request = ChatRequest(model=GOVERNED, messages=[], temperature=0.1, max_tokens=8, extra_body={"options": {}, "format": "json"})
    with pytest.raises(ProviderContractError) as raised:
        OllamaNativeRuntimeAdapter().wire_payload(request, stream=False)
    assert raised.value.reason_code == PROVIDER_REQUEST_FIELD_CONFLICT and raised.value.details["fields"] == ["format"]
    structured = ChatRequest(model=GOVERNED, messages=[], temperature=0.1, max_tokens=8, response_format={"type": "json_object"},
                             extra_body={"format": {"type": "object"}})
    with pytest.raises(ProviderContractError):
        OllamaNativeRuntimeAdapter().wire_payload(structured, stream=True)      # never "Kriya's value wins silently"


def test_b1_the_openai_compatible_runtime_does_not_own_format_and_carries_it_additively(tmp_path, monkeypatch, server):
    cfg = _config(server.url, extra_body={"options": {"top_p": 0.8}, "format": "json"})
    outcome, error, _records = _run(tmp_path, monkeypatch, cfg, CHAT)
    assert error is None and outcome is not None
    assert [body.get("format") for method, _path, body in server.received if method == "POST" and body] == ["json"]


def test_b1_a_non_conflicting_native_extension_still_works_and_kriyas_own_format_is_unchanged(tmp_path, monkeypatch, server):
    cfg = _config(server.url, runtime="ollama_native", extra_body={"options": {"top_p": 0.8}, "keep_alive": "5m"})
    outcome, error, _records = _run(tmp_path, monkeypatch, cfg, CHAT)
    assert error is None and outcome is not None
    [body] = [body for method, _path, body in server.received if method == "POST" and body]
    assert body["keep_alive"] == "5m" and "format" not in body and body["options"]["top_p"] == 0.8
    base = dict(model=GOVERNED, messages=[{"role": "user", "content": "u"}], temperature=0.1, max_tokens=8)
    native = OllamaNativeRuntimeAdapter()
    assert native.wire_payload(ChatRequest(**base, response_format={"type": "json_object"}), stream=False)["format"] == "json"
    schema = {"type": "object"}
    assert native.wire_payload(ChatRequest(**base, response_format={"type": "json_schema", "schema": schema}), stream=False)["format"] == schema


def test_b1_every_field_the_native_wire_builder_sets_beyond_the_global_set_is_declared_runtime_owned():
    base = dict(model=GOVERNED, messages=[{"role": "user", "content": "u"}], temperature=0.1, max_tokens=8)
    native = OllamaNativeRuntimeAdapter()
    for shape in (dict(), dict(tools=TOOLS), dict(response_format={"type": "json_object"}), dict(response_format={"type": "json_schema", "schema": {}})):
        set_by_kriya = set(native.wire_payload(ChatRequest(**base, **shape), stream=False)) - {"options", "truncate"}
        assert set_by_kriya <= set(RESERVED_REQUEST_FIELDS) | set(native.owned_request_fields), (shape, set_by_kriya)
    assert OllamaRuntimeAdapter().owned_request_fields == ()


# -- 3. repository configuration cannot select B ------------------------------------------------------------------------------------
def test_a_repository_kriya_yaml_cannot_select_another_model(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "kriya.yaml").write_text(
        f"llm:\n  model: \"{GOVERNED}\"\n  extra_body:\n    options:\n      num_ctx: 32768\n    model: \"{OTHER}\"\n")
    monkeypatch.chdir(repo)
    with pytest.raises(ValidationError) as raised:
        load_config()
    assert "extra_body" in str(raised.value) and "request field Kriya owns" in str(raised.value)
    # control: the same file without the key loads, and the governed model is what it names
    (repo / "kriya.yaml").write_text(f"llm:\n  model: \"{GOVERNED}\"\n  extra_body:\n    options:\n      num_ctx: 32768\n")
    assert load_config().llm.model == GOVERNED


@pytest.mark.parametrize("cls,kwargs", [(LLMConfig, {}), (FallbackModelConfig, {"model": "fb:1"})])
def test_config_validation_rejects_every_owned_field_early(cls, kwargs):
    for field in RESERVED_REQUEST_FIELDS:
        with pytest.raises(ValidationError):
            cls(extra_body={field: "x"}, **kwargs)
    assert cls(extra_body={"options": {"num_ctx": 1}, "top_p": 0.8, "reasoning_effort": "none"}, **kwargs)


def test_a_fallback_chain_entry_in_an_operator_config_is_validated_too():
    # (llm_chain is not repository-safe - SEC-009 refuses it from a repository file before validation - so this is the operator path)
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"llm_chain": [{"model": "fb:1", "extra_body": {"model": OTHER}}]})
    assert AppConfig.model_validate({"llm_chain": [{"model": "fb:1", "extra_body": {"options": {"num_ctx": 1}}}]}).llm_chain[0].model == "fb:1"


# -- 5-8. requested_model / reported_model persistence and semantics ---------------------------------------------------------------
@pytest.mark.parametrize("runtime", RUNTIMES)
def test_a_provider_reporting_the_requested_model_records_both(tmp_path, monkeypatch, server, runtime):
    server.reply_model = GOVERNED
    result, _error, records = _run(tmp_path, monkeypatch, _config(server.url, runtime=runtime), CHAT)
    assert (result.model, result.reported_model) == (GOVERNED, GOVERNED)
    assert _kind(records, "model.response")[0]["payload"]["reported_model"] == GOVERNED
    assert _kind(records, "model.result")[0]["payload"]["reported_model"] == GOVERNED


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_a_provider_reporting_a_different_model_preserves_both_and_labels_nothing(tmp_path, monkeypatch, server, runtime):
    server.reply_model = "governed:1-q4_K_M-digest"        # an alias/tag/digest may legitimately differ
    result, error, records = _run(tmp_path, monkeypatch, _config(server.url, runtime=runtime), CHAT)
    assert error is None and result.ok                                          # not an error, not a substitution
    request, response, final = (_kind(records, k)[0]["payload"] for k in ("model.request", "model.response", "model.result"))
    assert request["requested_model"] == GOVERNED and response["reported_model"] == "governed:1-q4_K_M-digest"
    assert final["requested_model"] == GOVERNED and final["reported_model"] == "governed:1-q4_K_M-digest"
    for payload in (request, response, final):
        assert not [key for key in payload if "substitut" in key or "fallback" in key or "mismatch" in key]
    assert result.to_telemetry()["requested_model"] == GOVERNED
    assert result.to_telemetry()["reported_model"] == "governed:1-q4_K_M-digest"


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_a_provider_that_names_no_model_leaves_reported_model_absent(tmp_path, monkeypatch, server, runtime):
    server.reply_model = None
    result, error, records = _run(tmp_path, monkeypatch, _config(server.url, runtime=runtime), CHAT)
    assert error is None and result.model == GOVERNED and result.reported_model is None
    assert "model" not in result.provider_metadata
    for kind in ("model.response", "model.result"):
        assert "reported_model" not in _kind(records, kind)[0]["payload"]       # absent, not null, not the requested model
    assert _kind(records, "model.request")[0]["payload"]["requested_model"] == GOVERNED
    assert "reported_model" not in result.to_telemetry()


def test_reloaded_evidence_keeps_the_requested_model_whatever_the_config_says_later(tmp_path, monkeypatch, server):
    cfg = _config(server.url)
    _run(tmp_path, monkeypatch, cfg, CHAT)
    cfg.llm.model = "changed-later:9"                       # current configuration moves on
    reloaded = list(reader.open_run(str(tmp_path / "state"), "run-d002").records())
    assert [r["payload"]["requested_model"] for r in _kind(reloaded, "model.request")] == [GOVERNED]
    assert [r["payload"]["requested_model"] for r in _kind(reloaded, "model.result")] == [GOVERNED]


def test_the_tool_turn_records_the_same_two_facts(tmp_path, monkeypatch, server):
    server.reply_model = "reported-by-provider:2"
    _result, error, records = _run(tmp_path, monkeypatch, _config(server.url), TOOL_TURN)
    assert error is None
    assert _kind(records, "model.request")[0]["payload"]["requested_model"] == GOVERNED
    assert _kind(records, "model.response")[0]["payload"]["reported_model"] == "reported-by-provider:2"


# -- 9. negative control: without the refusal the SDK sends B (the test detects it) -------------------------------------------------
def test_negative_control_without_the_refusal_the_wire_carries_the_other_model(tmp_path, monkeypatch, server):
    """Restores the pre-fix behaviour (no refusal anywhere). The same scenario then puts OTHER on the wire while the request,
    the result and the evidence name GOVERNED - the divergence this slice removes - and the loopback server observes it."""
    import kriya.core.provider_contract as provider_contract  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(provider_contract, "refuse_extra_body_request_fields", lambda *args, **kwargs: None)
    cfg = _config(server.url, extra_body={"options": {"top_p": 0.8}, "model": OTHER})
    result, error, records = _run(tmp_path, monkeypatch, cfg, CHAT)
    assert error is None
    assert server.wire_models == [OTHER]                                       # B served ...
    assert result.model == GOVERNED                                            # ... while Kriya attributes A
    assert _kind(records, "model.request")[0]["payload"]["requested_model"] == GOVERNED

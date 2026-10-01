"""INF-001: the inference runtime port - the contract every adapter meets,
the default adapter's parity (tests/test_inf001_runtime_parity.py pins the
wire and the digest), selection from configuration, and the layering:
Kriya keeps routing, qualification, budgets, retries and evidence above the
port; workflow and agent code never touch an adapter."""
import asyncio
import os
import re
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from _fake_inference_runtime import (
    FAKE_WINDOW_FIELD,
    FakeRuntimeAdapter,
    FakeServerError,
    FakeTimeout,
    tool_reply,
)

from kriya.config import AppConfig, FallbackModelConfig, ModelCapabilities
from kriya.config.authority import FieldClassification, agent_role_field_classification, classify_field
from kriya.core import model_runtime
from kriya.core.completion import CompletionStatus
from kriya.core.file_stamp import RACY_WINDOW_NS
from kriya.core.inference_runtime import (
    InferenceRuntimePort,
    UnknownRuntimeAdapterError,
    register_runtime_adapter,
    runtime_adapter,
    runtime_for_binding,
    unregister_runtime_adapter,
)
from kriya.core.inference_settings import request_settings
from kriya.core.llm import LLMClient
from kriya.core.model_qualification import qualification_identity

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake(monkeypatch):
    """A fake runtime registered for this test only; probing enabled so
    the adapter's own probe is what identifies the runtime."""
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    adapter = FakeRuntimeAdapter()
    register_runtime_adapter(adapter)
    model_runtime.clear_model_runtime_cache()
    yield adapter
    unregister_runtime_adapter(adapter.name)
    model_runtime.clear_model_runtime_cache()


def _config(runtime="fake", window=8192, **fallback):
    """The fake runtime serves 8192 (a provider-managed window), so the
    bindings declare 8192: a larger declaration is refused
    (SERVED_CONTEXT_BELOW_REQUESTED, PROVIDER-CONTRACT-001)."""
    config = AppConfig()
    config.llm.model = "primary:1"
    config.llm.inference_runtime = runtime
    config.llm.extra_body = {"reasoning_effort": "none"}
    config.llm.context_window = window
    config.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    config.llm_chain = [FallbackModelConfig(model="fb:1", inference_runtime=runtime, context_window=window,
                                            temperature=0.3, **fallback)] if fallback is not None else []
    return config


def _run(coroutine):
    return asyncio.run(coroutine)


# --- registry ----------------------------------------------------------------------

def test_the_default_runtime_is_the_packaged_adapter():
    default = runtime_adapter()
    assert isinstance(default, InferenceRuntimePort) and default is runtime_adapter(None)
    assert runtime_for_binding(AppConfig().llm) is default


@pytest.mark.parametrize("name", ["vllm", "nonexistent"])
def test_an_unregistered_runtime_is_refused_never_served_by_the_default(name):
    with pytest.raises(UnknownRuntimeAdapterError):
        runtime_adapter(name)
    with pytest.raises(UnknownRuntimeAdapterError):
        _run(LLMClient(_config(runtime=name)).complete("s", "u"))


def test_the_fake_runtime_is_unreachable_in_production():
    """Only the packaged default adapter is registered by kriya/ itself."""
    registrations = [path for path in (REPO / "kriya").rglob("*.py")
                     if "register_runtime_adapter(" in path.read_text()
                     and path.name != "inference_runtime.py"]
    assert [p.relative_to(REPO).as_posix() for p in registrations] == ["kriya/core/model_runtime.py"]
    assert "FakeRuntimeAdapter" not in "".join(p.read_text() for p in (REPO / "kriya").rglob("*.py"))


# --- the service through the port: primary and fallback ------------------------------

def test_primary_and_fallback_requests_go_through_their_bindings_adapter(fake):
    fake.replies = ["primary answer", "fallback answer"]
    llm = LLMClient(_config())
    assert _run(llm.complete("s", "u")) == "primary answer"
    assert _run(llm.complete("s", "u", model_override="fb:1")) == "fallback answer"
    assert [r.model for r in fake.requests] == ["primary:1", "fb:1"]
    assert fake.requests[1].temperature == 0.3  # the fallback's own settings


def test_settings_and_reasoning_controls_reach_the_adapter_unchanged(fake):
    llm = LLMClient(_config())
    result = _run(llm.complete_result("s", "u", max_tokens_override=512))
    [request] = fake.requests
    assert request.temperature == 0.2 and request.max_tokens == 512
    assert request.extra_body == {"reasoning_effort": "none"}
    assert result.inference_settings_digest == request_settings(
        temperature=0.2, reasoning=False, extra_body={"reasoning_effort": "none"}).digest


# --- context window propagation ----------------------------------------------------

def test_a_runtime_without_a_per_request_window_is_sent_none_and_budgets_what_it_serves(fake):
    """The body carries no window (the runtime serves what it was started
    with); the probe reports the served window, which matches the declared
    one, and the budget is it - labelled as the server's own configuration."""
    result = _run(LLMClient(_config()).complete_result("s", "u"))
    [request] = fake.requests
    assert FAKE_WINDOW_FIELD not in (request.extra_body or {})
    assert result.budget["window_source"] == "server_model_config" and result.budget["context_window"] == 8192
    assert result.budget["context_state"] == {
        "requested_context_window": 8192, "served_context_window": 8192,
        "served_context_provenance": "server_model_config", "budget_context_window": 8192}


def test_a_declared_window_the_runtime_does_not_serve_is_refused_before_inference(fake):
    """PROVIDER-CONTRACT-001: a runtime serving less than the declared
    window would silently truncate what Kriya admits - refused, never
    budgeted down (the old runtime_capped behaviour)."""
    from kriya.core.provider_contract import SERVED_CONTEXT_BELOW_REQUESTED, ProviderContractError

    with pytest.raises(ProviderContractError) as refused:
        _run(LLMClient(_config(window=16384)).complete_result("s", "u"))
    assert refused.value.reason_code == SERVED_CONTEXT_BELOW_REQUESTED
    assert refused.value.details == {"requested": 16384, "served": 8192, "provenance": "server_model_config"}
    assert fake.requests == []


def test_a_provider_managed_window_that_is_not_reported_is_labelled_so(fake):
    fake.served_window = None
    result = _run(LLMClient(_config(window=16384)).complete_result("s", "u"))
    assert result.budget["window_source"] == "provider_managed" and result.budget["context_window"] == 16384


def test_a_runtime_with_a_per_request_window_is_sent_the_declared_window_in_its_own_form(monkeypatch):
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    adapter = FakeRuntimeAdapter("fake-window", per_request_context_window=True)
    register_runtime_adapter(adapter)
    model_runtime.clear_model_runtime_cache()
    try:
        result = _run(LLMClient(_config(runtime="fake-window", window=16384)).complete_result("s", "u"))
    finally:
        unregister_runtime_adapter(adapter.name)
        model_runtime.clear_model_runtime_cache()
    [request] = adapter.requests
    assert request.extra_body[FAKE_WINDOW_FIELD] == 16384 and "options" not in request.extra_body
    assert result.budget["context_window"] == 16384 and adapter.probes[-1]["configured_context"] == 16384


# --- protocols: tools, JSON, streaming ----------------------------------------------

def test_native_tool_calls_come_back_normalized_and_validated_by_kriya(fake):
    fake.replies = [tool_reply("apply_patch", '{"path": "a.py"}'), tool_reply("apply_patch", "{not json")]
    llm = LLMClient(_config())
    tools = [{"type": "function", "function": {"name": "apply_patch", "parameters": {"type": "object"}}}]
    good = _run(llm.complete_with_tools_result([{"role": "user", "content": "x"}], tools))
    assert good.tool_calls == [{"id": "call-1", "name": "apply_patch", "arguments": {"path": "a.py"},
                                "source": "native"}]
    bad = _run(llm.complete_with_tools_result([{"role": "user", "content": "x"}], tools))
    assert bad.tool_calls[0]["arguments"] == {} and bad.parser_status == "malformed_tool_arguments"
    assert fake.requests[0].tools == tools


def test_json_mode_and_streaming_are_requested_through_the_port(fake):
    fake.replies = ['{"a": 1}', "hello streamed world"]
    llm = LLMClient(_config())
    assert _run(llm.complete("s", "u", json_mode=True)) == '{"a": 1}'
    assert fake.requests[0].response_format == {"type": "json_object"}
    tokens = []
    _run(llm.complete("s", "u", stream_callback=tokens.append))
    assert tokens == ["hello", "streamed", "world"] and fake.requests[1].stream_callback is not None


# --- error normalization ------------------------------------------------------------

@pytest.mark.parametrize("error, status", [(FakeTimeout("slow"), CompletionStatus.TIMEOUT),
                                           (FakeServerError("500"), CompletionStatus.BACKEND_ERROR)])
def test_transport_errors_are_classified_by_the_adapter(fake, error, status):
    fake.replies = [error]
    result = _run(LLMClient(_config()).complete_result("s", "u"))
    assert result.status == status and result.error is error


def test_cancellation_propagates_and_is_recorded(fake):
    fake.replies = [asyncio.CancelledError()]
    llm = LLMClient(_config())
    with pytest.raises(asyncio.CancelledError):
        _run(llm.complete_result("s", "u"))
    assert llm.last_completion.status == CompletionStatus.CANCELLED


def test_only_a_request_shape_error_earns_the_response_format_retry(fake):
    """Kriya's retry policy reads the adapter's classification: a server
    error is never retried with a changed request, a request error is."""
    config = _config()
    config.llm.reasoning = True
    fake.replies = [FakeServerError("500")]
    _run(LLMClient(config).complete_result("s", "u", json_mode=True))
    assert len(fake.requests) == 1
    fake.requests.clear()
    fake.replies = [ValueError("response_format unsupported"), '{"ok": true}']
    result = _run(LLMClient(config).complete_result("s", "u", json_mode=True))
    assert len(fake.requests) == 2 and fake.requests[1].response_format is None
    assert result.protocol["response_format_dropped"] is True


# --- identity: runtime-specific qualification ----------------------------------------

def _fp(adapter, **kwargs):
    return adapter.probe(base_url="http://localhost:1/v1", model="qwen:1", api_key="", egress_policy="local_only",
                         configured_context=None, kriya_protocol="capabilities-sha256:x", **kwargs)


def test_the_fingerprint_changes_when_the_runtime_materially_changes():
    assert _fp(FakeRuntimeAdapter()).digest != _fp(FakeRuntimeAdapter(provider_version="1.1")).digest


def test_the_same_weights_on_another_runtime_never_share_qualification():
    settings = request_settings(temperature=0.2, reasoning=False, extra_body={})
    here, there = _fp(FakeRuntimeAdapter("rt-a")), _fp(FakeRuntimeAdapter("rt-b"))
    assert here.weights_digest == there.weights_digest and here.digest != there.digest
    assert qualification_identity(here.digest, settings) != qualification_identity(there.digest, settings)


def test_two_runtimes_at_one_endpoint_never_share_a_cached_fingerprint(monkeypatch):
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    model_runtime.clear_model_runtime_cache()
    here, there = FakeRuntimeAdapter("rt-a"), FakeRuntimeAdapter("rt-b")
    inputs = dict(base_url="http://localhost:1/v1", model="qwen:1", configured_context=None,
                  kriya_protocol="capabilities-sha256:x")
    try:
        first = model_runtime.resolve_model_runtime(runtime=here, **inputs)
        second = model_runtime.resolve_model_runtime(runtime=there, **inputs)
    finally:
        model_runtime.clear_model_runtime_cache()
    assert (first.provider, second.provider) == ("rt-a", "rt-b") and len(there.probes) == 1


def test_a_normal_call_never_probes_again(fake):
    llm = LLMClient(_config())
    for _ in range(5):
        _run(llm.complete("s", "u"))
    assert len(fake.probes) == 1


def test_a_qualification_record_is_parsed_once_while_unchanged(tmp_path, monkeypatch):
    from kriya.core import model_qualification as mq

    monkeypatch.setenv(mq.QUALIFICATION_HOME_ENV, str(tmp_path))
    path = tmp_path / "record.json"
    path.write_text('{"fingerprint_digest": "d"}')
    # Parse-once holds for a settled record (FILE-STAMP-RACY-CACHE-001: one
    # still inside the racy window is re-read) - as a record written by an
    # earlier `kriya model qualify` is.
    settled = time.time_ns() - 10 * RACY_WINDOW_NS
    os.utime(path, ns=(settled, settled))
    real_load = mq.json.load
    loads = []

    def counting_load(stream):
        loads.append(stream.name)
        return real_load(stream)

    with patch.object(mq.json, "load", new=counting_load):
        for _ in range(5):
            assert mq._read_record("record", None) == {"fingerprint_digest": "d"}
            assert mq.runtime_has_records("d")
        assert len(loads) == 1
        path.write_text('{"fingerprint_digest": "e"}')  # a changed record is re-read, never stale
        os.utime(path, ns=(settled + 1000, settled + 1000))
        assert mq._read_record("record", None) == {"fingerprint_digest": "e"}


def test_the_doctor_discovers_and_identifies_through_the_primarys_adapter(fake):
    from kriya import production_doctor

    fake.models = ["primary:1"]
    probed = production_doctor.probe_llm_runtime(_config())
    assert probed["selected_model"] == {"id": "primary:1"}
    assert probed["runtime"].provider == "fake" and probed["fingerprint"] == probed["runtime"].digest


# --- configuration authority ---------------------------------------------------------

def test_the_runtime_selection_is_security_authority():
    assert classify_field("llm", "inference_runtime") == FieldClassification.SECURITY_AUTHORITY
    assert agent_role_field_classification({"llm": {"model": "m", "inference_runtime": "x"}}) == \
        FieldClassification.SECURITY_AUTHORITY
    assert agent_role_field_classification({"llm_chain": [{"model": "m", "inference_runtime": "x"}]}) == \
        FieldClassification.SECURITY_AUTHORITY
    assert agent_role_field_classification({"llm": {"model": "m"}}) == FieldClassification.REPOSITORY_SAFE


# --- layering ---------------------------------------------------------------------------

_ADAPTER_NAMES = ("OllamaRuntimeAdapter", "OpenAICompatibleTransport", "InferenceRuntimePort",
                  "probe_model_runtime", "chat.completions", "AsyncOpenAI")


@pytest.mark.parametrize("package", ["workflow", "agents"])
def test_workflow_and_agents_never_touch_an_adapter_or_a_transport(package):
    offenders = sorted(
        path.relative_to(REPO).as_posix() for path in (REPO / "kriya" / package).rglob("*.py")
        if any(re.search(rf"\b{re.escape(name)}\b", path.read_text()) for name in _ADAPTER_NAMES)
    )
    assert offenders == []


def test_the_transport_runs_only_behind_the_port():
    """The OpenAI-compatible chat call is made only by the transport."""
    callers = sorted(path.relative_to(REPO).as_posix() for path in (REPO / "kriya").rglob("*.py")
                     if "chat.completions.create" in path.read_text())
    assert callers == ["kriya/core/inference_runtime.py"]

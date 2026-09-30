"""FALLBACK-CONTEXT-WINDOW-001: the window Kriya budgets is the window it
asks the runtime to serve.

A binding's declared ``context_window`` used to be budgeted but sent only if
the author also wrote the provider option into ``extra_body``; a fallback
without it was served at the provider's default window. Now one definition,
``requested_context_window`` (an explicit provider option wins, else the
declared window), feeds the request body, the runtime fingerprint, the
dispatch budget, the allocation window, qualification and the doctor.

PROVIDER-CONTRACT-001 (measured, Ollama 0.34.4): the OpenAI-compatible /v1
ignores ``options.num_ctx``, so the window is REQUESTED and budgeted but never
put on the /v1 wire (claiming it was sent would be claiming it applied). The
served window is only what the server reports (/api/ps, or the model's own
num_ctx PARAMETER); one below the requested window is refused.
"""
import ast
import copy
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_prd013_model_runtime import OLLAMA, _transport

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.inference_settings import request_settings
from kriya.core.llm import LLMClient
from kriya.core.model_qualification import qualification_config, run_qualification
from kriya.core.model_runtime import (
    configured_context_window,
    context_window_overrides,
    request_extra_body,
    requested_context_window,
    resolve_configured_model_runtime,
)
from kriya.core.token_budget import ContextBudgetUnsatisfiableError
from kriya.workflow.context_budget import allocation_window
from kriya.workflow.model_transition import resolve_request_profile

FALLBACK = "qwen3-coder:30b"  # the model the captured Ollama responses describe
PRIMARY = "primary-model:1"


def _response():
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "ok"
    response.choices[0].message.reasoning = None
    response.choices[0].message.tool_calls = None
    response.choices[0].finish_reason = "stop"
    # No measured prompt count: a canned response has no request to measure.
    response.usage = MagicMock(prompt_tokens=0, completion_tokens=2)
    return response


def _config(**fallback):
    entry = {"model": FALLBACK, "base_url": "http://localhost:11434/v1", **fallback}
    return AppConfig(llm={"model": PRIMARY}, llm_chain=[entry])


async def _sent(config, model, **kwargs):
    """The extra_body and budget of one real LLMClient request."""
    llm = LLMClient(config)
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        result = await llm.complete_result("system", "user", model_override=model, **kwargs)
    return create.call_args.kwargs.get("extra_body"), result.budget


@pytest.fixture
def exact_ollama(monkeypatch):
    """Probing on, against the captured Ollama responses (trained length
    262144); ``responses`` can be edited per test."""
    responses = copy.deepcopy(OLLAMA)
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "_http_json", _transport(responses))
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    model_runtime.clear_model_runtime_cache()
    yield responses
    model_runtime.clear_model_runtime_cache()


# --- the request carries the budgeted window -----------------------------------

@pytest.mark.asyncio
async def test_a_fallback_without_the_provider_option_is_budgeted_at_its_declared_window():
    sent, budget = await _sent(_config(context_window=16384), FALLBACK)
    assert "options" not in (sent or {})  # /v1 ignores it: never sent as if applied
    assert budget["context_window"] == 16384 and budget["window_source"] == "requested_unverified"
    assert budget["context_state"]["requested_context_window"] == 16384


@pytest.mark.asyncio
async def test_the_primary_is_budgeted_at_its_declared_window_too():
    config = _config(context_window=16384)
    config.llm.context_window = 8192
    sent, budget = await _sent(config, PRIMARY)
    assert "options" not in (sent or {}) and budget["context_window"] == 8192


@pytest.mark.asyncio
async def test_an_explicit_provider_option_is_requested_and_only_applied_fields_are_sent():
    body = {"options": {"num_ctx": 32768, "top_p": 0.8}, "reasoning_effort": "none"}
    sent, budget = await _sent(_config(context_window=32768, extra_body=body), FALLBACK)
    assert sent == {"top_p": 0.8, "reasoning_effort": "none"} and budget["context_window"] == 32768


@pytest.mark.asyncio
async def test_a_conflicting_explicit_option_wins_everywhere_and_is_reported(caplog):
    """Deterministic: the provider option is what is sent, budgeted and
    fingerprinted; the ignored declaration is logged and listed for doctor."""
    config = _config(context_window=32768, extra_body={"options": {"num_ctx": 8192}})
    with caplog.at_level("WARNING"):
        sent, budget = await _sent(config, FALLBACK)
    assert "options" not in (sent or {}) and budget["context_window"] == 8192
    assert context_window_overrides(config) == [
        {"model": FALLBACK, "declared_context_window": 32768, "requested_context_window": 8192}]
    assert "declared context_window 32768 is ignored" in caplog.text
    assert allocation_window(config, config.llm_chain[0]) == allocation_window(
        _config(context_window=8192), _config(context_window=8192).llm_chain[0])
    from kriya.core.model_routing import candidate_evidence, place_candidate

    placed = place_candidate(config, "developer", config.llm_chain[0])
    assert candidate_evidence(placed, "developer", FALLBACK, order=0, explicit=False, table={}).context_window == 8192


def test_the_doctor_probes_the_primary_at_its_requested_window():
    """doctor --production fingerprints the primary exactly as a request
    would carry it, declared window included."""
    from kriya import production_doctor

    config = _config(context_window=16384)
    config.llm.context_window = 12288
    seen = {}

    def probe(**kwargs):
        seen.update(kwargs)
        return model_runtime.ModelRuntimeFingerprint(alias=PRIMARY, endpoint="http://localhost:11434/v1")

    with patch.object(production_doctor, "_json_request", return_value={"data": []}), \
         patch.object(model_runtime, "probe_model_runtime", new=probe):
        production_doctor.probe_llm_runtime(config)
    assert seen["configured_context"] == 12288


def test_an_undeclared_context_window_is_not_an_override():
    """A config that sets only the provider option has nothing to report."""
    assert context_window_overrides(_config(extra_body={"options": {"num_ctx": 8192}})) == []


@pytest.mark.asyncio
async def test_an_override_extra_body_still_carries_the_models_window():
    """Every escalation site passes the fallback's extra_body as an
    override; the window still comes from that model's own binding."""
    sent, budget = await _sent(_config(context_window=16384), FALLBACK,
                               extra_body_override={"reasoning_effort": "none"})
    assert sent == {"reasoning_effort": "none"} and budget["context_window"] == 16384


@pytest.mark.asyncio
async def test_a_genuinely_oversized_request_still_fails_closed():
    config = _config(context_window=4096)
    llm = LLMClient(config)
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        with pytest.raises(ContextBudgetUnsatisfiableError):
            await llm.complete_result("s", "x" * int(2.5 * 4096), model_override=FALLBACK)
    create.assert_not_called()


def _load(tmp_path, text):
    from kriya.config.config import load_config

    (tmp_path / "kriya.yaml").write_text(text)
    return load_config(str(tmp_path / "kriya.yaml"))


@pytest.mark.asyncio
async def test_a_user_context_window_alone_is_what_is_sent_and_budgeted(tmp_path):
    """Through the real config layering: the packaged defaults must not
    carry a provider window option that silently outranks the user's own
    context_window (one-level merge keeps the default's extra_body)."""
    config = _load(tmp_path, "llm:\n  model: primary-model:1\n  context_window: 8192\n")
    assert context_window_overrides(config) == []
    sent, budget = await _sent(config, "primary-model:1")
    assert "options" not in (sent or {}) and budget["context_window"] == 8192


def test_the_packaged_defaults_keep_their_window_and_inference_identity(tmp_path):
    """Removing the duplicated window option from the packaged defaults
    changes neither the default window (32768) nor the default inference
    settings (the window is not an inference setting)."""
    config = _load(tmp_path, "llm:\n  model: primary-model:1\n")
    assert requested_context_window(config.llm.extra_body, config.llm.context_window) == 32768
    assert request_settings(temperature=config.llm.temperature, reasoning=config.llm.reasoning,
                            extra_body=config.llm.extra_body).digest == request_settings(
        temperature=config.llm.temperature, reasoning=config.llm.reasoning,
        extra_body={"options": {"num_ctx": 32768, "top_p": 0.8, "top_k": 20}}).digest


# --- evidence is truthful -------------------------------------------------------

def test_an_unverified_fingerprint_records_the_request_never_a_served_window():
    """Probing off (or a non-Ollama server): the requested window is
    recorded as requested; nothing claims it is served."""
    fingerprint = resolve_configured_model_runtime(_config(context_window=16384), FALLBACK)
    assert not fingerprint.exact
    assert fingerprint.configured_context_window == 16384 and fingerprint.effective_context_window is None


@pytest.mark.asyncio
async def test_an_exact_runtimes_served_window_is_observed_never_taken_from_the_request(exact_ollama):
    exact_ollama["/api/ps"] = {"models": [{"name": FALLBACK, "context_length": 16384}]}
    sent, budget = await _sent(_config(context_window=16384), FALLBACK)
    fingerprint = resolve_configured_model_runtime(_config(context_window=16384), FALLBACK)
    # The model has no num_ctx PARAMETER: nothing but the loaded server says what is served.
    assert fingerprint.exact and fingerprint.effective_context_window is None
    assert "options" not in (sent or {})
    assert budget["window_source"] == "served_observed" and budget["context_window"] == 16384
    assert budget["context_state"]["served_context_provenance"] == "server_observed"


@pytest.mark.asyncio
async def test_a_served_window_below_the_requested_one_is_refused_before_inference(exact_ollama):
    """Formerly budgeted down as "runtime_capped"; a served window below the
    requested one silently truncates what Kriya admits - refused."""
    from kriya.core.provider_contract import SERVED_CONTEXT_BELOW_REQUESTED, ProviderContractError

    exact_ollama["/api/ps"] = {"models": [{"name": FALLBACK, "context_length": 8192}]}
    llm = LLMClient(_config(context_window=16384))
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        with pytest.raises(ProviderContractError) as refused:
            await llm.complete_result("system", "user", model_override=FALLBACK)
    assert refused.value.reason_code == SERVED_CONTEXT_BELOW_REQUESTED
    assert refused.value.details["served"] == 8192 and refused.value.details["requested"] == 16384
    create.assert_not_called()


def test_every_consumer_sees_the_same_runtime_for_an_implicit_window(exact_ollama):
    """The one test that catches a missed site: dispatch, allocation,
    transition, routing and qualification all resolve one runtime digest for
    a binding with no explicit provider option."""
    del exact_ollama
    import asyncio

    from kriya.core.model_routing import candidate_evidence, place_candidate

    config = _config(context_window=16384)
    binding = config.llm_chain[0]
    llm = LLMClient(config)
    dispatched = asyncio.run(llm._runtime_fingerprint(
        FALLBACK, binding.base_url, "k", request_extra_body(binding.extra_body or None, binding.context_window)))
    configured = resolve_configured_model_runtime(config, FALLBACK)
    explicit_body = resolve_configured_model_runtime(
        config, FALLBACK, base_url=binding.base_url, api_key=binding.api_key, extra_body=binding.extra_body or {})
    profile = resolve_request_profile(config, binding)
    placed = candidate_evidence(place_candidate(config, "developer", binding), "developer", FALLBACK,
                                order=0, explicit=False, table={})
    qualified = resolve_configured_model_runtime(qualification_config(config, FALLBACK), FALLBACK, fresh=True)
    digests = {dispatched.digest, configured.digest, explicit_body.digest, profile.runtime_digest, qualified.digest,
               placed.runtime_digest}
    assert dispatched.exact and len(digests) == 1
    assert dispatched.configured_context_window == 16384


def test_the_embedding_runtime_requests_no_window():
    """The certification identity includes the embedding runtime: no chat
    binding's window may leak into it."""
    config = _config(context_window=16384)
    fingerprint = resolve_configured_model_runtime(
        config, config.embedding.model, base_url=config.embedding.base_url, api_key="", extra_body={})
    assert fingerprint.configured_context_window is None


def test_injection_never_changes_the_inference_identity():
    body = {"options": {"top_p": 0.8}, "reasoning_effort": "none"}
    injected = request_extra_body(body, 16384)
    assert configured_context_window(injected) == 16384 and body == {"options": {"top_p": 0.8}, "reasoning_effort": "none"}
    assert request_settings(temperature=0.7, reasoning=False, extra_body=injected).digest == \
        request_settings(temperature=0.7, reasoning=False, extra_body=body).digest


def test_the_demo03_shape_is_unchanged():
    """Both demo-03 bindings already send 32768 explicitly, equal to their
    declared window: the request body (and so the runtime digest and the
    QUALIFIED records) is exactly what it was."""
    primary = {"options": {"num_ctx": 32768, "top_p": 0.8, "top_k": 20}}
    fallback = {"options": {"num_ctx": 32768, "top_p": 0.8, "top_k": 20}, "reasoning_effort": "none"}
    assert request_extra_body(primary, 32768) is primary
    assert request_extra_body(fallback, 32768) is fallback
    assert requested_context_window(primary, 32768) == requested_context_window(fallback, 32768) == 32768


@pytest.mark.asyncio
async def test_qualification_cases_are_sent_the_window_they_qualify(exact_ollama):
    """The context-capacity case talks to the endpoint directly: it must
    send the same window the record's fingerprint was requested with."""
    del exact_ollama
    config = _config(context_window=16384)
    sent = []

    async def create(**kwargs):
        sent.append(kwargs.get("extra_body"))
        return _response()

    client = MagicMock()
    client.chat.completions.create = create
    factory = MagicMock(return_value=MagicMock(client=client))
    await run_qualification(config, FALLBACK, client_factory=factory, only=["context_capacity"])
    assert sent and all(configured_context_window(body) == 16384 for body in sent)


# --- structure ------------------------------------------------------------------

def test_only_the_seam_reads_the_provider_option_directly():
    """Every consumer asks requested_context_window (declared window plus
    override rule) or the binding's runtime adapter
    (runtime.configured_context_window, INF-001); only the seam module calls
    the provider-specific function itself."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kriya")
    readers = set()
    for directory, _, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(directory, name)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "configured_context_window" for node in ast.walk(tree)):
                readers.add(os.path.relpath(path, root))
    assert readers == {os.path.join("core", "model_runtime.py")}

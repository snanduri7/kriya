"""PRD-013: exact local model runtime fingerprint.

Deterministic: every probe uses an injected transport that serves captured
Ollama 0.34 response shapes, so no test reaches a real endpoint."""
import copy
import json
import os
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import (
    MODEL_PROTOCOL_ADAPTER_VERSION,
    UNAVAILABLE,
    ModelRuntimeFingerprint,
    endpoint_identity,
    load_recorded_fingerprint,
    probe_model_runtime,
    record_fingerprint,
    resolve_model_runtime,
)

BASE_URL = "http://localhost:11434/v1"
MODEL = "qwen3-coder:30b"

# Captured shape (values shortened) of Ollama 0.34.2 for qwen3-coder:30b.
OLLAMA = {
    "/api/version": {"version": "0.34.2"},
    "/api/tags": {"models": [{
        "name": MODEL, "model": MODEL, "modified_at": "2026-07-27T19:16:24+05:30",
        "digest": "06c1097efce0431c2045fe7b2e5108366e43bee1b4603a7aded8f21689e90bca",
        "details": {"format": "gguf", "quantization_level": "Q4_K_M"},
    }]},
    "/api/show": {
        "modified_at": "2026-07-27T19:16:24+05:30",
        "template": "{{ .Prompt }}",
        "modelfile": "FROM /models/blobs/sha256-1194192cf2a1\nTEMPLATE {{ .Prompt }}\nRENDERER qwen3-coder\nPARSER qwen3-coder\n",
        "parameters": "top_k                          20\nstop                           \"<|im_end|>\"\ntemperature                    0.7",
        "details": {"format": "gguf", "family": "qwen3moe", "parameter_size": "30.5B", "quantization_level": "Q4_K_M"},
        "model_info": {
            "general.architecture": "qwen3moe", "qwen3moe.context_length": 262144,
            "tokenizer.ggml.model": "gpt2", "tokenizer.ggml.pre": "qwen2",
            "tokenizer.ggml.tokens": ["a", "b", "ab"], "tokenizer.ggml.merges": ["a b"],
            "tokenizer.ggml.token_type": [1, 1, 1],
        },
        "capabilities": ["completion", "tools"],
    },
}


def _transport(responses=None, calls=None):
    responses = responses or OLLAMA

    def transport(url, payload, api_key):
        path = "/" + url.split("/", 3)[3]
        if calls is not None:
            calls.append((path, payload))
        if path not in responses:
            raise ConnectionError(f"404 {path}")
        value = responses[path]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)
    return transport


def _probe(responses=None, **kwargs):
    kwargs.setdefault("configured_context", 32768)
    kwargs.setdefault("kriya_protocol", "capabilities-sha256:x")
    return probe_model_runtime(base_url=BASE_URL, model=MODEL, transport=_transport(responses), **kwargs)


def test_fingerprint_records_every_component_the_runtime_reports():
    fp = _probe()
    assert fp.exact
    assert fp.provider == "ollama" and fp.provider_version == "0.34.2"
    assert fp.artifact_digest == "sha256:06c1097efce0431c2045fe7b2e5108366e43bee1b4603a7aded8f21689e90bca"
    assert fp.weights_digest == "sha256:1194192cf2a1"
    assert (fp.model_format, fp.family, fp.parameter_size, fp.quantization) == ("gguf", "qwen3moe", "30.5B", "Q4_K_M")
    assert fp.chat_template == "renderer:qwen3-coder"
    assert fp.tool_call_parser == "parser:qwen3-coder"
    assert fp.tokenizer_identity == "gpt2/qwen2" and fp.tokenizer_digest.startswith("sha256:")
    assert fp.runtime_parameters_digest.startswith("sha256:")
    assert fp.served_capabilities == ("completion", "tools")
    assert fp.model_context_length == 262144
    assert fp.configured_context_window == 32768 and fp.effective_context_window == 32768
    assert fp.kriya_protocol == "capabilities-sha256:x"
    assert fp.adapter_version == MODEL_PROTOCOL_ADAPTER_VERSION
    assert fp.missing_components == ()
    assert len(fp.digest) == 64


def test_digest_is_stable_and_ignores_timestamps_and_diagnostics():
    first = _probe()
    later = copy.deepcopy(OLLAMA)
    later["/api/tags"]["models"][0]["modified_at"] = "2026-09-25T00:00:00Z"
    later["/api/show"]["modified_at"] = "2026-09-25T00:00:00Z"
    second = _probe(later)
    assert first.digest == second.digest
    assert replace(first, probe_errors=("x",)).digest == first.digest


def test_parameter_order_and_spacing_are_normalized():
    reordered = copy.deepcopy(OLLAMA)
    reordered["/api/show"]["parameters"] = "temperature 0.7\nstop \"<|im_end|>\"\ntop_k   20"
    assert _probe(reordered).digest == _probe().digest


@pytest.mark.parametrize(("path", "mutate", "component"), [
    ("/api/version", lambda r: r.update(version="0.35.0"), "provider_version"),
    ("/api/tags", lambda r: r["models"][0].update(digest="ffff"), "artifact_digest"),
    ("/api/show", lambda r: r.update(modelfile=r["modelfile"].replace("sha256-1194192cf2a1", "sha256-99")),
     "weights_digest"),
    ("/api/show", lambda r: r["details"].update(quantization_level="Q8_0"), "quantization"),
    ("/api/show", lambda r: r.update(modelfile=r["modelfile"].replace("RENDERER qwen3-coder", "RENDERER other")),
     "chat_template"),
    ("/api/show", lambda r: r.update(modelfile=r["modelfile"].replace("PARSER qwen3-coder", "PARSER hermes")),
     "tool_call_parser"),
    ("/api/show", lambda r: r["model_info"].update({"tokenizer.ggml.merges": ["b a"]}), "tokenizer_digest"),
    ("/api/show", lambda r: r.update(parameters="top_k 40"), "runtime_parameters_digest"),
])
def test_every_runtime_component_change_changes_the_identity(path, mutate, component):
    changed = copy.deepcopy(OLLAMA)
    mutate(changed[path])
    before, after = _probe(), _probe(changed)
    assert getattr(before, component) != getattr(after, component)
    assert before.digest != after.digest


def test_kriya_side_inputs_are_part_of_the_identity():
    base = _probe()
    assert _probe(configured_context=8192).digest != base.digest
    assert _probe(kriya_protocol="capabilities-sha256:y").digest != base.digest
    assert replace(base, adapter_version="kriya-openai-compat/999").digest != base.digest


def test_missing_metadata_is_explicitly_unavailable_never_inferred():
    """Only the version endpoint answers: nothing is guessed from the name
    (qwen3-coder:30b does not become family qwen3/quantization q4)."""
    fp = _probe({"/api/version": {"version": "0.34.2"}})
    assert fp.provider_version == "0.34.2"
    assert fp.artifact_digest == UNAVAILABLE and fp.quantization == UNAVAILABLE
    assert fp.family == UNAVAILABLE and fp.tool_call_parser == UNAVAILABLE
    assert not fp.exact
    assert "artifact_digest" in fp.missing_components
    assert any("/api/show" in error for error in fp.probe_errors)


def test_a_template_without_renderer_is_identified_by_its_digest():
    plain = copy.deepcopy(OLLAMA)
    plain["/api/show"]["modelfile"] = "FROM /models/blobs/sha256-1194192cf2a1\n"
    plain["/api/show"]["template"] = "{{ .System }} {{ .Prompt }}"
    fp = _probe(plain)
    assert fp.chat_template.startswith("template-sha256:")
    assert fp.tool_call_parser == UNAVAILABLE


def test_the_served_window_is_never_mistaken_for_the_trained_maximum():
    unset = _probe(configured_context=None)
    assert unset.model_context_length == 262144
    assert unset.effective_context_window is None
    modelfile_ctx = copy.deepcopy(OLLAMA)
    modelfile_ctx["/api/show"]["parameters"] += "\nnum_ctx 16384"
    assert _probe(modelfile_ctx, configured_context=None).effective_context_window == 16384


def test_a_model_not_served_by_the_endpoint_is_not_exact():
    other = copy.deepcopy(OLLAMA)
    other["/api/tags"]["models"][0]["name"] = "something-else:1b"
    fp = _probe(other)
    assert fp.artifact_digest == UNAVAILABLE and not fp.exact
    assert any("not served" in error for error in fp.probe_errors)


@pytest.mark.parametrize("policy", ["local_only", "unrestricted"])
def test_a_non_local_endpoint_is_never_probed_under_any_policy(policy):
    calls = []
    fp = probe_model_runtime(base_url="http://8.8.8.8/v1", model=MODEL, egress_policy=policy,
                             transport=_transport(calls=calls))
    assert calls == []
    assert not fp.exact and "not local" in fp.probe_errors[0]


def test_an_endpoint_without_native_metadata_is_not_exact():
    calls = []
    fp = probe_model_runtime(base_url="http://localhost:8000/openai", model=MODEL, transport=_transport(calls=calls))
    assert calls == [] and not fp.exact


def test_probing_can_be_disabled_and_is_disabled_for_the_mocked_suite():
    assert os.environ[model_runtime.PROBE_ENV_VAR] == "0"
    fp = probe_model_runtime(base_url=BASE_URL, model=MODEL)
    assert not fp.exact and "disabled" in fp.probe_errors[0]


def test_endpoint_identity_drops_credentials_and_query():
    assert endpoint_identity("http://user:secret@LOCALHOST:11434/v1/?k=v#x") == "http://localhost:11434/v1"


def test_every_result_is_cached_for_the_process_and_fresh_reprobes(monkeypatch):
    """A non-exact result (unpulled model, non-Ollama server) is cached too, so
    it never costs blocking probes on every call; fresh=True re-probes."""
    results = [_probe({"/api/version": {"version": "0.34.2"}}), _probe()]
    calls = []

    def fake_probe(**kwargs):
        calls.append(kwargs)
        return results[len(calls) - 1]

    monkeypatch.setattr(model_runtime, "probe_model_runtime", fake_probe)
    first = resolve_model_runtime(base_url=BASE_URL, model=MODEL)
    again = resolve_model_runtime(base_url=BASE_URL, model=MODEL)
    assert not first.exact and again is first and len(calls) == 1
    fresh = resolve_model_runtime(base_url=BASE_URL, model=MODEL, fresh=True)
    assert fresh.exact and len(calls) == 2
    assert resolve_model_runtime(base_url=BASE_URL, model=MODEL) is fresh


def test_an_exact_fingerprint_is_recorded_content_addressed_in_the_state_dir():
    fp = _probe()
    path = record_fingerprint(fp)
    assert path.startswith(os.path.realpath(os.environ["KRIYA_STATE_DIR"]) + os.sep)
    stored = load_recorded_fingerprint(fp.digest)
    assert stored["digest"] == fp.digest and stored["artifact_digest"] == fp.artifact_digest


def _mock_response(content, finish_reason="stop"):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].message.reasoning = None
    response.choices[0].finish_reason = finish_reason
    response.usage = None
    return response


@pytest.mark.asyncio
async def test_every_llm_call_is_attributed_to_its_exact_runtime(monkeypatch):
    exact = _probe()
    monkeypatch.setattr(model_runtime, "probe_model_runtime", lambda **kwargs: exact)
    used = []
    monkeypatch.setattr("kriya.control.run_coordinator.record_model_runtime_use", used.append)
    llm = LLMClient(AppConfig())
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(return_value=_mock_response("ok"))):
        await llm.complete("system", "user")
    assert llm.last_call_metrics["runtime_fingerprint"] == exact.digest
    assert llm.last_completion.runtime_fingerprint_exact is True
    assert used == [exact.digest]


@pytest.mark.asyncio
async def test_a_call_without_an_exact_runtime_is_still_attributed_but_never_recorded_as_exact(monkeypatch):
    used = []
    monkeypatch.setattr("kriya.control.run_coordinator.record_model_runtime_use", used.append)
    llm = LLMClient(AppConfig())
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(return_value=_mock_response("ok"))):
        await llm.complete("system", "user")
    assert llm.last_completion.runtime_fingerprint_exact is False
    assert len(llm.last_call_metrics["runtime_fingerprint"]) == 64
    assert used == []


def test_the_active_run_record_collects_fingerprint_ids(tmp_path):
    import subprocess

    from kriya.control.persistence import load_run_record
    from kriya.control.run_coordinator import begin_mutating_run, record_model_runtime_use

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with begin_mutating_run(str(tmp_path)) as context:
        record_model_runtime_use("a" * 64)
        record_model_runtime_use("a" * 64)
        record_model_runtime_use("b" * 64)
        run_id = context.run_id
    record = load_run_record(str(tmp_path), run_id)
    assert record.model_runtime_fingerprint_ids == ["a" * 64, "b" * 64]


def test_resume_binds_the_model_runtime_only_when_every_role_runtime_is_exact(monkeypatch):
    from kriya.workflow.resume_fingerprints import model_runtime_resume_fingerprint

    cfg = AppConfig()
    exact = {m: replace(_probe(), alias=m) for m in (cfg.llm.model, *[c.model for c in cfg.llm_chain])}
    monkeypatch.setattr(model_runtime, "resolve_configured_model_runtime",
                        lambda config, model=None, **kw: exact.get(model or config.llm.model, _probe({})))
    bound = model_runtime_resume_fingerprint(cfg)
    assert bound.available and bound.basis == "model-runtime"
    assert model_runtime_resume_fingerprint(cfg) == bound

    exact[cfg.llm.model] = replace(exact[cfg.llm.model], artifact_digest="sha256:re-pulled")
    assert model_runtime_resume_fingerprint(cfg) != bound

    exact[cfg.llm.model] = replace(exact[cfg.llm.model], artifact_digest=UNAVAILABLE)
    unbound = model_runtime_resume_fingerprint(cfg)
    assert not unbound.available and "not exact" in unbound.basis


def test_fingerprint_to_dict_is_json_serializable_and_complete():
    record = _probe().to_dict()
    json.dumps(record)
    assert record["exact"] is True and record["missing_components"] == []
    assert set(ModelRuntimeFingerprint.__dataclass_fields__) - {"probe_errors"} <= set(record)

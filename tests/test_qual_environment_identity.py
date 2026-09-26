"""Qualification environment identity and runtime portability
(MODEL-EVIDENCE-HARDENING-001 additions).

- Capacity evidence (context_capacity) is bound to the execution environment
  that produced it; functional evidence is not.
- The environment identity holds capability classes only, never volatile or
  machine-identifying values.
- Qualification identity is provider-neutral: the same weights under another
  runtime never share evidence, and generic code never branches on a
  provider."""
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from kriya.config import AppConfig
from kriya.core import execution_environment as ee
from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core.inference_settings import qualification_identity, request_settings
from kriya.core.model_runtime import ModelRuntimeFingerprint

REPO = Path(__file__).resolve().parents[1]
MODEL = "qwen3.8:27b"
SETTINGS = request_settings(temperature=0.7, reasoning=False, extra_body={"reasoning_effort": "none"})
FUNCTIONAL = ("plain_completion", "finish_reason_stop")
TIER = FUNCTIONAL + ("context_capacity",)

M1_MAX_64 = {"os": "darwin", "architecture": "arm64", "memory_bytes": 64 * (1 << 30), "cpu_model": "Apple M1 Max",
             "gpus": []}
M_ULTRA_128 = {**M1_MAX_64, "memory_bytes": 128 * (1 << 30), "cpu_model": "Apple M2 Ultra"}
LINUX_2X_H100 = {"os": "linux", "architecture": "x86_64", "memory_bytes": int(251.5 * (1 << 30)), "cpu_model": None,
                 "gpus": [("NVIDIA H100 80GB HBM3", 81559 * (1 << 20))] * 2, "gpu_backend": "cuda"}


def _fp(**changes):
    base = ModelRuntimeFingerprint(
        alias=MODEL, endpoint="http://localhost:11434/v1", provider="ollama", provider_version="0.34.2",
        artifact_digest="sha256:22130167c4c2", weights_digest="sha256:f5f1dd89", tokenizer_digest="sha256:tok",
        model_context_length=262144, configured_context_window=65536, effective_context_window=65536,
    )
    return replace(base, **changes)


@pytest.fixture
def host(monkeypatch):
    """Selects the properties of 'this machine' for the environment probe."""
    current = {"props": M1_MAX_64}
    monkeypatch.setattr(ee, "_cached_host_properties", lambda: current["props"])
    monkeypatch.delenv(ee.PROBE_ENV_VAR, raising=False)
    return current


def _record(fp, *, capacity=mq.PASS, environment=None):
    results = [mq.CaseResult(c, mq.PASS) for c in FUNCTIONAL] + [mq.CaseResult("context_capacity", capacity)]
    return mq.build_record(fp, results, settings=SETTINGS, environment=environment)


# --- the environment identity ----------------------------------------------------------------

def test_apple_silicon_is_a_unified_memory_metal_environment():
    env = ee.environment_from_properties(M1_MAX_64, provider="ollama", provider_version="0.34.2")
    assert (env.os, env.architecture, env.accelerator_backend, env.accelerator_model) == (
        "darwin", "arm64", "metal", "Apple M1 Max")
    assert (env.system_memory_class_gib, env.accelerator_memory_class_gib, env.unified_memory) == (64, 64, True)
    assert env.inference_runtime == "ollama/0.34.2" and env.exact


def test_a_multi_gpu_host_is_identified_by_backend_model_count_and_memory_class():
    env = ee.environment_from_properties(LINUX_2X_H100, provider="vllm", provider_version="0.11.0")
    assert (env.accelerator_backend, env.accelerator_model, env.accelerator_count) == (
        "cuda", "NVIDIA H100 80GB HBM3", 2)
    # MemTotal of a 256 GB host (251.5 GiB) is the stable class 248.
    assert (env.accelerator_memory_class_gib, env.system_memory_class_gib, env.unified_memory) == (80, 248, False)


@pytest.mark.parametrize(("gib", "klass"), [(63.8, 64), (62.7, 64), (64, 64), (79.6, 80), (125.8, 128),
                                            (15.5, 16), (7.8, 8)])
def test_memory_is_a_stable_class(gib, klass):
    assert ee.memory_class_gib(gib * (1 << 30)) == klass


def test_volatile_and_identifying_values_never_change_the_identity():
    base = ee.environment_from_properties(M1_MAX_64, provider="ollama", provider_version="0.34.2")
    noisy = {**M1_MAX_64, "free_memory_bytes": 3 * (1 << 30), "gpu_temperature_c": 71, "hostname": "sriram-mbp",
             "serial_number": "C02XYZ", "mac_address": "aa:bb:cc:dd:ee:ff", "memory_bytes": int(63.9 * (1 << 30))}
    assert ee.environment_from_properties(noisy, provider="ollama", provider_version="0.34.2").digest == base.digest
    fields = set(base.to_dict())
    assert not fields & {"hostname", "serial_number", "mac_address", "free_memory_bytes", "gpu_temperature_c"}


def test_the_probe_reads_only_capability_properties():
    seen = []

    def run(argv):
        seen.append(tuple(argv))

    ee.host_properties(run)
    allowed = {("sysctl", "-n", "hw.memsize"), ("sysctl", "-n", "machdep.cpu.brand_string"),
               ("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits")}
    assert set(seen) <= allowed


def test_a_remote_or_unprobed_endpoint_has_no_observable_environment(host, monkeypatch):
    remote = ee.execution_environment_for("http://192.168.1.20:11434/v1", provider="ollama", provider_version="0.34.2")
    assert not remote.exact and remote.accelerator_backend == ee.UNAVAILABLE
    assert ee.execution_environment_for("http://localhost:11434/v1", provider="ollama",
                                        provider_version="0.34.2").exact
    monkeypatch.setenv(ee.PROBE_ENV_VAR, "0")
    assert not ee.execution_environment_for("http://localhost:11434/v1", provider="ollama",
                                            provider_version="0.34.2").exact


# --- capacity evidence is environment-bound -----------------------------------------------------

def test_same_model_settings_and_environment_reuse_the_qualification(host):
    mq.save_record(_record(_fp()))
    assert mq.assess(_fp(), TIER, settings=SETTINGS).status == mq.QUALIFIED


def test_a_hardware_class_change_does_not_reuse_capacity_evidence(host):
    mq.save_record(_record(_fp()))
    host["props"] = M_ULTRA_128
    tier = mq.assess(_fp(), TIER, settings=SETTINGS)
    assert tier.status == mq.NOT_QUALIFIED and tier.missing == ("context_capacity",)
    assert "not evaluated in this execution environment" in tier.reasons[0]
    # Functional qualification is not environment-bound.
    assert mq.assess(_fp(), FUNCTIONAL, settings=SETTINGS).status == mq.QUALIFIED


def test_a_backend_change_makes_capacity_evidence_stale(host):
    mq.save_record(_record(_fp()))
    host["props"] = {**M1_MAX_64, "os": "linux", "architecture": "aarch64", "gpu_backend": "cpu"}
    assert mq.assess(_fp(), TIER, settings=SETTINGS).status == mq.NOT_QUALIFIED


def test_a_runtime_version_change_makes_the_whole_qualification_stale(host):
    mq.save_record(_record(_fp()))
    upgraded = _fp(provider_version="0.35.0")
    assert mq.assess(upgraded, FUNCTIONAL, settings=SETTINGS).status == mq.MISSING
    assert (ee.environment_for_fingerprint(upgraded).digest != ee.environment_for_fingerprint(_fp()).digest)


def test_stronger_hardware_may_be_freshly_qualified_for_a_failed_tier_and_neither_leaks(host):
    """qwen3.8@64K FAILed on the 64 GiB machine. A 128 GiB machine qualifies
    it; back on 64 GiB it is still not qualified, and the FAIL never blocks
    the stronger machine."""
    mq.save_record(_record(_fp(), capacity=mq.FAIL))
    failed = mq.assess(_fp(), TIER, settings=SETTINGS)
    assert failed.status == mq.NOT_QUALIFIED and failed.failed == ("context_capacity",)

    host["props"] = M_ULTRA_128
    assert mq.assess(_fp(), TIER, settings=SETTINGS).missing == ("context_capacity",)  # never the old FAIL
    mq.save_record(_record(_fp(), capacity=mq.PASS))
    assert mq.assess(_fp(), TIER, settings=SETTINGS).status == mq.QUALIFIED

    host["props"] = M1_MAX_64
    again = mq.assess(_fp(), TIER, settings=SETTINGS)
    assert again.status == mq.NOT_QUALIFIED and again.failed == ("context_capacity",)
    stored = json.loads(Path(mq.record_path(qualification_identity(_fp().digest, SETTINGS))).read_text())
    assert len(stored["environment_evidence"]) == 2


def test_an_unobservable_environment_never_counts_capacity_evidence(host):
    remote = _fp(endpoint="http://192.168.1.20:11434/v1")
    mq.save_record(_record(remote))
    assessment = mq.assess(remote, TIER, settings=SETTINGS)
    assert assessment.status == mq.NOT_QUALIFIED and "not observable" in assessment.reasons[0]


def test_the_context_tier_follows_the_environment(host, monkeypatch):
    """Integration through PRD-016 tier offering: a tier qualified on this
    machine is offered here and not on a different one."""
    def probe(**kw):
        return _fp(alias=kw["model"], configured_context_window=kw["configured_context"],
                   effective_context_window=kw["configured_context"], kriya_protocol=kw["kriya_protocol"])

    model_runtime.clear_model_runtime_cache()
    monkeypatch.setattr(model_runtime, "probe_model_runtime", probe)
    cfg = AppConfig()
    cfg.llm.model = MODEL
    cfg.llm.extra_body = {"reasoning_effort": "none", "options": {"num_ctx": 32768}}
    cfg.llm.temperature = 0.7
    cfg.llm_chain = []
    cfg.llm.context_policy.mode = "adaptive"
    from kriya.core.inference_settings import role_inference_settings

    settings = role_inference_settings(cfg, "developer", MODEL)
    tier_runtime = model_runtime.resolve_configured_model_runtime(mq.qualification_config(cfg, MODEL, 65536), MODEL)
    results = [mq.CaseResult(c, mq.PASS) for c in mq.context_tier_requirements(cfg, MODEL)]
    mq.save_record(mq.build_record(tier_runtime, results, settings=settings))
    preferred = model_runtime.resolve_configured_model_runtime(cfg, MODEL)

    def offer():
        return mq.offered_context_tiers(cfg, MODEL, preferred, cfg.llm.context_policy, base_url=cfg.llm.base_url,
                                        api_key=cfg.llm.api_key, settings=settings)

    assert [t.tokens for t in offer().tiers] == [65536]
    host["props"] = M_ULTRA_128
    assert offer().tiers == ()


# --- runtime portability ----------------------------------------------------------------------------

def test_the_same_weights_under_another_runtime_share_no_evidence(host):
    ollama = _fp()
    vllm = _fp(provider="vllm", provider_version="0.11.0")
    assert ollama.weights_digest == vllm.weights_digest and ollama.digest != vllm.digest
    assert qualification_identity(ollama.digest, SETTINGS) != qualification_identity(vllm.digest, SETTINGS)
    mq.save_record(_record(ollama))
    assert mq.assess(vllm, FUNCTIONAL, settings=SETTINGS).status == mq.MISSING
    assert ee.environment_for_fingerprint(ollama).digest != ee.environment_for_fingerprint(vllm).digest


def test_the_runtime_fingerprint_names_every_portable_component():
    fields = set(_fp().identity_fields())
    assert {"provider", "provider_version", "artifact_digest", "weights_digest", "configured_context_window",
            "chat_template", "tool_call_parser", "kriya_protocol", "adapter_version"} <= fields


# INF-001 inventory: provider-native calls outside the model runtime adapter,
# each named here on purpose (none is part of model qualification).
INF_001_INVENTORY = {
    "kriya/memory/vector.py": "OllamaEmbeddingClient's native /api/embeddings fallback (embeddings, not inference)",
}
_PROVIDER_CODE = (
    r"(?<!for )\bprovider\s*(==|!=|not\s+in|in)\s*",  # a provider comparison/membership (not a loop variable)
    r"[\"']ollama[\"']",  # a provider name as a value
    r"[\"']num_ctx[\"']",  # the Ollama per-request context field
    r"/api/(show|tags|version|ps|embeddings|embed|generate|chat)\b",  # Ollama-native endpoints
)


def test_generic_code_never_branches_on_a_provider_or_uses_its_native_api():
    """INF-001 seam: only kriya/core/model_runtime.py (the runtime adapter)
    knows a provider's name, its per-request context field or its native
    API; anything else is a named INF-001 inventory item."""
    offenders = []
    for path in sorted((REPO / "kriya").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel == "kriya/core/model_runtime.py" or rel in INF_001_INVENTORY:
            continue
        code = "\n".join(line for line in path.read_text().splitlines()
                         if not line.lstrip().startswith(("#", '"', "'")))
        if any(re.search(pattern, code) for pattern in _PROVIDER_CODE):
            offenders.append(rel)
    assert offenders == []
    for rel in INF_001_INVENTORY:  # still true, or remove it from the inventory
        assert re.search(_PROVIDER_CODE[3], (REPO / rel).read_text()), rel


def test_the_context_window_request_field_is_the_adapters():
    body = model_runtime.with_context_window({"reasoning_effort": "none"}, 65536)
    assert model_runtime.configured_context_window(body) == 65536
    assert model_runtime.without_context_window(body) == {"reasoning_effort": "none"}
    assert model_runtime.supports_per_request_context_window(_fp())
    assert not model_runtime.supports_per_request_context_window(_fp(provider="vllm"))

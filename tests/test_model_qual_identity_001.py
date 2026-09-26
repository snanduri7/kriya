"""MODEL-QUAL-IDENTITY-001: qualification identity includes the effective
inference settings (temperature, reasoning flag, reasoning_effort, sampling
options, seed and every other extra_body field except the per-call num_ctx).

The runtime fingerprint is unchanged; a qualification record is keyed by the
runtime digest plus the settings digest, and a policy-/2 record (keyed by the
runtime alone) is STALE, never reused and never MISSING."""
import asyncio
import json
import os
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from kriya.agents.agent import PlannerAgent, call_with_escalation
from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig, ModelCapabilities
from kriya.core import inference_settings as inf
from kriya.core import model_qualification as mq
from kriya.core import model_routing as mr
from kriya.core import model_runtime
from kriya.core.inference_settings import (
    qualification_identity,
    request_settings,
    role_inference_settings,
)
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import ModelRuntimeFingerprint

MODEL = "qwen3-coder:30b"
FALLBACK = "qwen3.6:35b-a3b-q4_K_M"


def _fp(**changes):
    base = ModelRuntimeFingerprint(
        alias=MODEL, endpoint="http://localhost:11434/v1", provider="ollama", provider_version="0.34.2",
        artifact_digest="sha256:abc", tokenizer_digest="sha256:tok", model_context_length=262144,
        configured_context_window=32768, effective_context_window=32768,
    )
    return replace(base, **changes)


def _exact_probe(monkeypatch):
    """Every model is an exact Ollama runtime whose served window is the
    num_ctx the request configures."""
    def probe(**kw):
        return ModelRuntimeFingerprint(
            alias=kw["model"], endpoint="http://localhost:11434/v1", provider="ollama",
            provider_version="0.34.2", artifact_digest=f"sha256:{kw['model']}", tokenizer_digest="sha256:tok",
            model_context_length=262144, configured_context_window=kw["configured_context"],
            effective_context_window=kw["configured_context"], kriya_protocol=kw["kriya_protocol"],
        )

    model_runtime.clear_model_runtime_cache()
    monkeypatch.setattr(model_runtime, "probe_model_runtime", probe)


BASE = {"temperature": 0.7, "reasoning": False,
        "extra_body": {"reasoning_effort": "none", "options": {"num_ctx": 32768, "top_p": 0.8, "top_k": 20}}}


def _settings(**changes):
    spec = json.loads(json.dumps(BASE))
    spec.update(changes)
    return request_settings(**spec)


def _with_options(**options):
    body = json.loads(json.dumps(BASE["extra_body"]))
    body["options"].update(options)
    return body


# --- the settings identity -------------------------------------------------------------------

@pytest.mark.parametrize("changed", [
    {"extra_body": {**BASE["extra_body"], "reasoning_effort": "high"}},
    {"extra_body": {"options": BASE["extra_body"]["options"]}},  # reasoning_effort dropped
    {"extra_body": {**BASE["extra_body"], "think": False}},
    {"temperature": 0.2},
    {"extra_body": _with_options(top_p=0.95)},
    {"extra_body": _with_options(top_k=40)},
    {"extra_body": _with_options(seed=7)},
    {"reasoning": True},
], ids=["reasoning_effort", "reasoning_effort_absent", "think", "temperature", "top_p", "top_k", "seed",
        "reasoning_flag"])
def test_a_behaviour_affecting_setting_changes_the_identity(changed):
    base, other = _settings(), _settings(**changed)
    assert base.digest != other.digest
    assert qualification_identity("r" * 64, base) != qualification_identity("r" * 64, other)


def test_a_seed_change_changes_the_identity():
    assert _settings(extra_body=_with_options(seed=7)).digest != _settings(extra_body=_with_options(seed=8)).digest


@pytest.mark.parametrize("same", [
    # Key order.
    {"extra_body": {"options": {"top_k": 20, "top_p": 0.8, "num_ctx": 32768}, "reasoning_effort": "none"}},
    # num_ctx is the runtime fingerprint's input (a PRD-016 tier), never a setting.
    {"extra_body": _with_options(num_ctx=65536)},
    # An integral float equals the integer.
    {"extra_body": _with_options(top_k=20.0)},
], ids=["key_order", "num_ctx", "integral_float"])
def test_identical_effective_settings_are_the_same_identity(same):
    assert _settings(**same).digest == _settings().digest


def test_empty_options_equal_absent_options_equal_no_extra_body():
    digests = {request_settings(temperature=0.7, reasoning=False, extra_body=body).digest
               for body in (None, {}, {"options": {}}, {"options": {"num_ctx": 32768}})}
    assert len(digests) == 1


def test_the_output_budget_is_metadata_not_identity():
    """PRD-016 sizes max_tokens per call: a different per-call output budget
    (or configured ceiling) is the same qualification identity."""
    small, large = _settings(), replace(_settings(), output_ceiling=65536)
    assert small.digest == large.digest and small.to_dict()["output_ceiling"] != large.to_dict()["output_ceiling"]
    cfg = AppConfig()
    before = role_inference_settings(cfg, "developer", cfg.llm.model)
    cfg.llm.max_tokens = cfg.llm.max_tokens * 4
    after = role_inference_settings(cfg, "developer", cfg.llm.model)
    assert before.digest == after.digest and before.output_ceiling != after.output_ceiling


# --- per-role resolution ---------------------------------------------------------------------

def _roles_cfg():
    cfg = AppConfig()
    cfg.llm.model = MODEL
    cfg.llm.temperature = 0.7
    cfg.llm.reviewer_temperature = 0.3
    cfg.llm.extra_body = {"options": {"num_ctx": 32768}}
    cfg.llm_chain = [FallbackModelConfig(model=FALLBACK, temperature=0.2, reasoning=True,
                                         extra_body={"reasoning_effort": "none", "options": {"num_ctx": 32768}})]
    return cfg


def test_role_temperatures_follow_what_each_role_sends():
    cfg = _roles_cfg()
    assert role_inference_settings(cfg, "planner", MODEL).temperature == 0.7
    assert role_inference_settings(cfg, "reviewer", MODEL).temperature == 0.3
    assert role_inference_settings(cfg, "developer", MODEL).temperature == 0.7
    fallback = role_inference_settings(cfg, "developer", FALLBACK)
    # The Developer path sends the primary temperature to a fallback; its own
    # reasoning flag and extra_body do apply.
    assert fallback.temperature == 0.7 and fallback.reasoning is True
    assert fallback.extra_body == {"reasoning_effort": "none"}
    cfg.agent_llms.planner.llm = cfg.llm.model_copy(update={"temperature": 0.1})
    assert role_inference_settings(cfg, "planner", MODEL).temperature == 0.1


def _response():
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "ok"
    response.choices[0].message.reasoning = None
    response.choices[0].message.tool_calls = None
    response.choices[0].finish_reason = "stop"
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=2)
    return response


class _Capture:
    """Records what each real call sent and the settings dispatch resolved."""

    def __init__(self, monkeypatch):
        self.dispatch_settings = []
        self.create = AsyncMock(return_value=_response())
        capture = self

        def offered(config, model, fingerprint, policy, *, base_url, api_key, settings):
            capture.dispatch_settings.append(settings)
            return mq.ContextTierOffer((), None, "")

        monkeypatch.setattr(mq, "offered_context_tiers", offered)

    def sent(self, index=-1):
        kwargs = self.create.call_args_list[index].kwargs
        return kwargs.get("temperature"), kwargs.get("extra_body")


def test_each_roles_real_call_sends_its_resolved_identity(monkeypatch):
    """The identity doctor/routing/qualify compute per role is the one each
    role's real call path sends and dispatch resolves."""
    cfg = _roles_cfg()
    capture = _Capture(monkeypatch)
    llm = LLMClient(cfg)

    async def calls():
        with patch("kriya.core.llm.AsyncOpenAI") as client_cls:
            client_cls.return_value.chat.completions.create = capture.create
            with patch.object(llm.client.chat.completions, "create", new=capture.create):
                await PlannerAgent("planner", llm, None, []).run("plan")
                # workflow.py's Reviewer calls pass reviewer_temperature this way.
                await call_with_escalation(llm, "s", "review", [None],
                                           temperature_override=cfg.llm.reviewer_temperature, role="reviewer")
                # The Developer's fallback call shape (agent.py run_generation).
                fb = cfg.llm_chain[0]
                await llm.complete("s", "code", model_override=fb.model, base_url_override=fb.base_url,
                                   api_key_override=fb.api_key, extra_body_override=fb.extra_body,
                                   temperature_override=None)

    asyncio.run(calls())
    expected = [role_inference_settings(cfg, "planner", MODEL), role_inference_settings(cfg, "reviewer", MODEL),
                role_inference_settings(cfg, "developer", FALLBACK)]
    assert [s.digest for s in capture.dispatch_settings] == [s.digest for s in expected]
    for index, settings in enumerate(expected):
        temperature, extra_body = capture.sent(index)
        assert temperature == settings.temperature
        assert inf.normalized_extra_body(extra_body) == settings.extra_body


def test_an_explicit_role_binding_sends_its_own_temperature(monkeypatch):
    cfg = _roles_cfg()
    cfg.agent_llms.spec_compliance.llm = FallbackModelConfig(model=MODEL, temperature=0.05,
                                                             extra_body={"options": {"num_ctx": 32768}})
    capture = _Capture(monkeypatch)
    llm = LLMClient(cfg)

    async def call():
        with patch("kriya.core.llm.AsyncOpenAI") as client_cls:
            client_cls.return_value.chat.completions.create = capture.create
            await call_with_escalation(llm, "s", "p", [cfg.agent_llms.spec_compliance.llm], role="spec_compliance")

    asyncio.run(call())
    expected = role_inference_settings(cfg, "spec_compliance", MODEL)
    assert expected.temperature == 0.05
    assert capture.dispatch_settings[-1].digest == expected.digest


# --- qualification records ---------------------------------------------------------------------

def _record(fp, settings, statuses=None):
    results = [mq.CaseResult(c, (statuses or {}).get(c, mq.PASS)) for c in mq.CAPABILITIES]
    return mq.build_record(fp, results, settings=settings)


def test_a_record_qualifies_only_its_own_inference_identity():
    fp = _fp()
    none_effort = _settings()
    default_effort = _settings(extra_body={"options": {"num_ctx": 32768, "top_p": 0.8, "top_k": 20}})
    mq.save_record(_record(fp, none_effort))
    assert mq.assess(fp, ("plain_completion",), settings=none_effort).status == mq.QUALIFIED
    other = mq.assess(fp, ("plain_completion",), settings=default_effort)
    assert other.status == mq.MISSING
    assert mq.measured_limits_for(fp, settings=default_effort) == {}


def test_a_record_names_its_identity_and_keeps_the_output_ceiling_as_metadata():
    fp, settings = _fp(), replace(_settings(), output_ceiling=16384)
    record = _record(fp, settings)
    assert record["qualification_identity"] == qualification_identity(fp.digest, settings)
    assert record["inference_settings_digest"] == settings.digest
    assert record["inference_settings"]["output_ceiling"] == 16384
    assert record["policy_version"] == "kriya-qualification/3"
    path = mq.save_record(record)
    assert os.path.basename(path) == f"{record['qualification_identity']}.json"


def _legacy_record(fp, statuses=None):
    """A policy-/2 record as `kriya model qualify` wrote it before this fix:
    keyed by the runtime digest, no inference settings."""
    record = _record(fp, _settings(), statuses)
    for key in ("qualification_identity", "inference_settings_digest", "inference_settings"):
        record.pop(key)
    record.update(policy_version="kriya-qualification/2", schema_version=1)
    path = mq.record_path(fp.digest)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(record, stream)
    return record


def test_an_old_policy_record_is_stale_never_missing_and_never_reused():
    fp = _fp()
    _legacy_record(fp)
    assessment = mq.assess(fp, ("plain_completion",), settings=_settings())
    assert assessment.status == mq.STALE
    assert any("predates inference-settings identity" in r for r in assessment.reasons)
    assert any("qualification policy changed" in r for r in assessment.reasons)
    assert mq.measured_limits_for(fp, settings=_settings()) == {}


def _tier_cfg(*, declared=(65536,)):
    cfg = AppConfig()
    cfg.llm.model = MODEL
    cfg.llm.extra_body = {"options": {"num_ctx": 32768}}
    cfg.llm.context_window = 32768
    cfg.llm_chain = []
    cfg.llm.context_policy.mode = "adaptive"
    cfg.llm.context_policy.declared_safe_context_tiers = list(declared)
    return cfg


def _offer(cfg):
    preferred = model_runtime.resolve_configured_model_runtime(cfg, MODEL)
    return mq.offered_context_tiers(cfg, MODEL, preferred, cfg.llm.context_policy,
                                    base_url=cfg.llm.base_url, api_key=cfg.llm.api_key,
                                    settings=role_inference_settings(cfg, "developer", MODEL))


def _tier_runtime(cfg, size):
    return model_runtime.resolve_configured_model_runtime(mq.qualification_config(cfg, MODEL, size), MODEL)


def test_a_declared_tier_is_offered_only_while_no_qualification_data_exists(monkeypatch):
    _exact_probe(monkeypatch)
    cfg = _tier_cfg()
    assert [t.tokens for t in _offer(cfg).tiers] == [65536]


def test_an_old_not_qualified_64k_record_keeps_the_declared_tier_out(monkeypatch):
    """The MODEL-EVAL-001 qwen3.8@64K context_capacity FAIL, recorded under
    policy /2: after the policy bump it is STALE, and a declared 65536 is
    still never offered for it."""
    _exact_probe(monkeypatch)
    cfg = _tier_cfg()
    _legacy_record(_tier_runtime(cfg, 65536), {"context_capacity": mq.FAIL})
    offer = _offer(cfg)
    assert offer.tiers == () and "65536: STALE" in offer.note


def test_a_tier_qualified_under_other_settings_is_not_offered(monkeypatch):
    _exact_probe(monkeypatch)
    cfg = _tier_cfg()
    other = _settings(extra_body={"reasoning_effort": "high"})
    mq.save_record(_record(_tier_runtime(cfg, 65536), other))
    offer = _offer(cfg)
    assert offer.tiers == () and "qualified under other settings" in offer.note
    # ...and a record under other settings is never discovered as a tier.
    preferred = model_runtime.resolve_configured_model_runtime(cfg, MODEL)
    assert mq.recorded_context_sizes(preferred, role_inference_settings(cfg, "developer", MODEL)) == []
    assert mq.recorded_context_sizes(preferred, other) == [65536]


def test_qualification_sends_the_identity_it_records():
    cfg = _roles_cfg()
    settings = role_inference_settings(cfg, "developer", FALLBACK)
    copy = mq.qualification_config(cfg, FALLBACK, settings=settings)
    binding = next(c for c in copy.llm_chain if c.model == FALLBACK)
    assert copy.llm.temperature == settings.temperature
    assert binding.extra_body == {"reasoning_effort": "none", "options": {"num_ctx": 32768}}
    assert copy.llm.extra_body == binding.extra_body and binding.reasoning is True
    tier = mq.qualification_config(cfg, FALLBACK, 65536, settings=settings)
    assert next(c for c in tier.llm_chain if c.model == FALLBACK).extra_body["options"]["num_ctx"] == 65536
    assert cfg.llm_chain[0].extra_body["options"]["num_ctx"] == 32768  # the original is untouched


def test_run_qualification_keys_the_record_by_the_developer_identity_by_default():
    class FakeLLM:
        async def complete_result(self, *args, **kwargs):
            from kriya.core.completion import CompletionResult, CompletionStatus

            return CompletionResult(status=CompletionStatus.OK, content="READY", model=MODEL, finish_reason="stop")

    cfg = _roles_cfg()
    record = asyncio.run(mq.run_qualification(cfg, MODEL, llm=FakeLLM(), fingerprint=_fp(),
                                              only=["plain_completion"]))
    settings = role_inference_settings(cfg, "developer", MODEL)
    assert record["inference_settings_digest"] == settings.digest
    assert record["qualification_identity"] == qualification_identity(_fp().digest, settings)


def test_cli_qualify_qualifies_every_distinct_role_identity(tmp_path, monkeypatch):
    """One `kriya model qualify` covers the model's roles: when the Reviewer
    sends another temperature, both identities are qualified and saved."""
    from kriya.cli import main

    cfg = _roles_cfg()
    cfg.llm_chain = []
    monkeypatch.setattr("kriya.cli._model_cfg", lambda ctx: cfg)
    seen = []

    async def fake_run(cfg, model, **kwargs):
        seen.append(kwargs["settings"])
        return _record(_fp(), kwargs["settings"])

    monkeypatch.setattr(mq, "run_qualification", fake_run)
    out = tmp_path / "report.json"
    result = CliRunner().invoke(main, ["model", "qualify", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert sorted(s.temperature for s in seen) == [0.3, 0.7]
    written = json.loads(out.read_text())["records"]
    assert {r["inference_settings"]["temperature"] for r in written} == {0.3, 0.7}
    assert next(r for r in written if r["inference_settings"]["temperature"] == 0.3)["roles"] == ["reviewer"]
    for settings in seen:
        assert mq.load_record(_fp().digest, settings) is not None


# --- routing and resume ---------------------------------------------------------------------------

def _routing_cfg(tmp_path):
    cfg = AppConfig()
    routing = cfg.model_policy.routing
    routing.mode = "evidence"
    routing.table_path = str(tmp_path / "table.json")
    routing.candidates = [FallbackModelConfig(model="cand-a", extra_body={"options": {"num_ctx": 32768}},
                                              capabilities=ModelCapabilities(json_mode=True))]
    routing.roles = {"reviewer": ["cand-a"]}
    return cfg


def test_routing_never_consumes_a_qualification_from_another_inference_identity(tmp_path, monkeypatch):
    _exact_probe(monkeypatch)
    cfg = _routing_cfg(tmp_path)
    placed = mr.place_candidate(cfg, "reviewer", cfg.model_policy.routing.candidates[0])
    runtime = model_runtime.resolve_configured_model_runtime(placed, "cand-a")
    mq.save_record(_record(runtime, role_inference_settings(placed, "reviewer", "cand-a")))
    assert mr.candidate_evidence(placed, "reviewer", "cand-a", order=0, explicit=False,
                                 table={}).qualification == mq.QUALIFIED
    # Same runtime (num_ctx unchanged), different reasoning_effort: not qualified.
    cfg.model_policy.routing.candidates[0].extra_body = {"reasoning_effort": "high", "options": {"num_ctx": 32768}}
    changed = mr.place_candidate(cfg, "reviewer", cfg.model_policy.routing.candidates[0])
    assert model_runtime.resolve_configured_model_runtime(changed, "cand-a").digest == runtime.digest
    evidence = mr.candidate_evidence(changed, "reviewer", "cand-a", order=0, explicit=False, table={})
    assert evidence.qualification == mq.MISSING


def test_the_resume_fingerprint_binds_each_roles_inference_settings(monkeypatch):
    from kriya.workflow import resume_fingerprints

    _exact_probe(monkeypatch)
    cfg = _roles_cfg()
    before = resume_fingerprints.model_runtime_resume_fingerprint(cfg)
    real = inf.role_inference_settings

    def reviewer_differs(config, role, model):
        settings = real(config, role, model)
        return replace(settings, temperature=0.9) if role == "reviewer" else settings

    monkeypatch.setattr(inf, "role_inference_settings", reviewer_differs)
    after = resume_fingerprints.model_runtime_resume_fingerprint(cfg)
    assert before.available and after.available and before.value != after.value

"""S1 / C-09 (owner ruling 7, 2026-11-15): runtime identity and production admission.

Identity: a model name resolves to exactly one binding. Two bindings that
share a name with different identity-relevant settings are refused at
configuration load (the measured collision, probe B-P1: a Reviewer bound to
``ollama_native`` at 65,536 executed on the primary's adapter at 32,768); a
name no binding carries is refused before any provider contact; whatever the
caller does not override is the called binding's own.

Admission: under ``runtime_profile: production`` the run refuses to start,
and ``LLMClient`` refuses to dispatch, unless the exact identity (runtime
digest + inference-settings digest) is QUALIFIED. Outside production the
assessment is recorded and never refuses."""
import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import strict_config
from openai.resources.chat.completions import AsyncCompletions
from pydantic import ValidationError
from test_prd017_fallback_transition import FALLBACK, PRIMARY, _cfg, _exact_ollama, _response

from kriya.config.config import FallbackModelConfig, binding_alias_conflicts
from kriya.core import model_admission as admission
from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core.inference_settings import role_inference_identities, role_inference_settings
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import MODEL_BINDING_UNKNOWN, UnknownModelBindingError
from kriya.core.role_metrics import model_role

REPO = Path(__file__).resolve().parents[1]

B_P1 = dict(
    llm={"model": "shared-model:1", "context_window": 32768, "temperature": 0.2},
    agent_llms={"reviewer": {"llm": {"model": "shared-model:1", "context_window": 65536, "temperature": 0.7,
                                     "inference_runtime": "ollama_native", "extra_body": {"top_p": 0.5}}}},
)


# --- identity: one name, one binding ------------------------------------------------------------

def test_the_measured_same_name_collision_is_refused_at_load():
    """B-P1 inverted: the configuration that executed the Reviewer on the
    primary's adapter and window no longer loads."""
    with pytest.raises(ValidationError) as refused:
        strict_config(**B_P1)
    message = str(refused.value)
    assert "MODEL_BINDING_ALIAS_CONFLICT" in message
    assert "llm and agent_llms.reviewer.llm both bind model 'shared-model:1'" in message
    for field in ("extra_body", "context_window", "inference_runtime"):
        assert field in message


@pytest.mark.parametrize("field, value", [
    ("base_url", "http://127.0.0.1:9999/v1"),
    ("extra_body", {"top_p": 0.5}),
    ("context_window", 65536),
    ("reasoning", True),
    ("inference_runtime", "ollama_native"),
])
def test_each_identity_field_alone_makes_a_same_name_binding_a_conflict(field, value):
    with pytest.raises(ValidationError) as refused:
        strict_config(llm={"model": "shared-model:1"},
                      agent_llms={"reviewer": {"llm": {"model": "shared-model:1", field: value}}})
    assert f"differ in {field};" in str(refused.value)


def test_a_conflict_between_two_non_primary_bindings_is_refused_case_insensitively():
    with pytest.raises(ValidationError) as refused:
        strict_config(llm={"model": "primary:1"},
                      llm_chain=[{"model": "Other:1"}],
                      agent_llms={"planner": {"llm_chain": [{"model": "other:1", "context_window": 8192}]}})
    assert "llm_chain[0] and agent_llms.planner.llm_chain[0] both bind model 'Other:1'" in str(refused.value)


def test_same_name_bindings_with_one_identity_and_distinct_aliases_load():
    """Positive controls: the rule refuses divergence, not sharing. Sampling
    temperature and the output budget are passed per call by the role choke
    point, so they may differ under one name."""
    shared = strict_config(llm={"model": "shared-model:1"},
                           agent_llms={"reviewer": {"llm": {"model": "shared-model:1", "temperature": 0.7,
                                                            "max_tokens": 2048}}})
    assert binding_alias_conflicts(shared) == []
    distinct = strict_config(
        llm={"model": "primary:1", "context_window": 32768},
        agent_llms={"reviewer": {"llm": {"model": "reviewer:1", "context_window": 65536,
                                         "inference_runtime": "ollama_native", "extra_body": {"top_p": 0.5}}}})
    client = LLMClient(distinct)
    own = client._binding("reviewer:1")
    assert (own["context_window"], own["inference_runtime"], own["extra_body"]) == (65536, "ollama_native",
                                                                                   {"top_p": 0.5})
    assert client._runtime("reviewer:1").name == "ollama_native"
    assert client._runtime("primary:1").name != "ollama_native"
    # Execution and assessment name the same object (they used to differ: B-P1).
    assert model_runtime.require_binding(distinct, "reviewer:1") is distinct.agent_llms.reviewer.llm


@pytest.mark.asyncio
async def test_an_unknown_model_name_is_refused_before_any_provider_contact():
    cfg = _cfg()
    llm = LLMClient(cfg)
    create = AsyncMock(return_value=_response("never"))
    with patch.object(AsyncCompletions, "create", new=create), \
            patch.object(model_runtime, "resolve_model_runtime", side_effect=AssertionError("provider contacted")):
        with pytest.raises(UnknownModelBindingError) as refused:
            await llm.complete_result("s", "u", model_override="not-configured:1")
        with pytest.raises(UnknownModelBindingError):
            await llm.complete_with_tools_result([{"role": "user", "content": "u"}], [],
                                                 model_override="not-configured:1")
        with pytest.raises(UnknownModelBindingError):
            await llm.complete("s", "u", model_override="not-configured:1")
    assert refused.value.reason_code == MODEL_BINDING_UNKNOWN and refused.value.model == "not-configured:1"
    assert str(refused.value).startswith(f"{MODEL_BINDING_UNKNOWN}:")
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_call_that_names_only_the_model_executes_that_bindings_own_endpoint_and_identity(monkeypatch):
    """Requested identity == dispatched identity: with nothing but the model
    name, the endpoint, key, reasoning flag, sampling and window are the
    called binding's (they used to be the primary's endpoint and a
    chain-only reasoning lookup)."""
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    cfg.agent_llms.reviewer.llm = FallbackModelConfig(
        model="reviewer-own:1", base_url="http://127.0.0.1:9999/v1", api_key="reviewer-key", temperature=0.9,
        reasoning=True, context_window=16384)
    llm = LLMClient(cfg)
    create = AsyncMock(return_value=_response("ok"))
    with patch.object(AsyncCompletions, "create", new=create), \
            patch.object(llm, "_client_for", wraps=llm._client_for) as client_for:
        result = await llm.complete_result("s", "u", model_override="reviewer-own:1")
    client_for.assert_called_once_with("http://127.0.0.1:9999/v1", "reviewer-key")
    create.assert_awaited_once()
    assert result.inference_settings_digest == role_inference_settings(cfg, "reviewer", "reviewer-own:1").digest
    assert result.inference_settings_digest != role_inference_settings(cfg, "developer", PRIMARY).digest


# --- admission at the dispatch choke point ------------------------------------------------------

def _production(cfg):
    cfg.runtime_profile = "production"
    return cfg


def _qualify(cfg, model, settings, **statuses):
    runtime = model_runtime.resolve_configured_model_runtime(cfg, model)
    results = [mq.CaseResult(c, statuses.get(c, mq.PASS)) for c in mq.CAPABILITIES]
    mq.save_record(mq.build_record(runtime, results, settings=settings))


def _qualify_every_role(cfg, *, skip=()):
    """A current all-PASS record for every identity of every role's every
    model, except the (role, model, label) triples in ``skip``."""
    for role, models in mq.role_models(cfg).items():
        for model in models:
            for label, settings in role_inference_identities(cfg, role, model):
                if (role, model, label) not in skip:
                    _qualify(cfg, model, settings)


async def _call(llm, **kwargs):
    create = AsyncMock(return_value=_response("ok"))
    with patch.object(AsyncCompletions, "create", new=create):
        result = await llm.complete_result("s", "u", **kwargs)
    return result, create


async def _refused(llm, **kwargs):
    create = AsyncMock(return_value=_response("never"))
    with patch.object(AsyncCompletions, "create", new=create):
        with pytest.raises(admission.ModelAdmissionError) as refused:
            await llm.complete_result("s", "u", **kwargs)
    create.assert_not_awaited()
    assert refused.value.reason_code == admission.MODEL_BINDING_NOT_QUALIFIED
    assert str(refused.value).startswith(f"{admission.MODEL_BINDING_NOT_QUALIFIED}:")
    return refused.value.details


@pytest.mark.asyncio
async def test_production_refuses_an_unqualified_primary_and_the_same_configuration_outside_production_runs(
        monkeypatch):
    _exact_ollama(monkeypatch)
    with model_role("developer"):
        details = await _refused(LLMClient(_production(_cfg())))
        assert (details["role"], details["model"], details["status"]) == ("developer", PRIMARY, mq.MISSING)
        assert details["runtime_fingerprint"] and details["inference_settings_digest"]
        assert details["qualification_policy_digest"] == mq.policy_digest_for(_cfg())
        _result, create = await _call(LLMClient(_cfg()))  # not production: dispatched
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_production_dispatches_the_exactly_qualified_identity_and_no_other_under_the_same_name(monkeypatch):
    """The name is not the authority: the same model, one degree warmer, is
    another inference identity and has no record."""
    _exact_ollama(monkeypatch)
    cfg = _production(_cfg())
    _qualify(cfg, PRIMARY, role_inference_settings(cfg, "developer", PRIMARY))
    llm = LLMClient(cfg)
    with model_role("developer"):
        result, create = await _call(llm)
        create.assert_awaited_once()
        assert result.inference_settings_digest == role_inference_settings(cfg, "developer", PRIMARY).digest
        details = await _refused(llm, temperature_override=cfg.llm.temperature + 0.5)
        assert details["status"] == mq.MISSING and details["model"] == PRIMARY
        details = await _refused(llm, extra_body_override={"top_p": 0.3})
        assert details["status"] == mq.MISSING


@pytest.mark.asyncio
async def test_production_admits_a_fallback_only_on_its_own_record(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _production(_cfg())
    _qualify(cfg, PRIMARY, role_inference_settings(cfg, "developer", PRIMARY))
    fallback = cfg.llm_chain[0]
    call = dict(model_override=fallback.model, base_url_override=fallback.base_url,
                api_key_override=fallback.api_key, extra_body_override=fallback.extra_body)
    llm = LLMClient(cfg)
    with model_role("developer"):
        details = await _refused(llm, **call)
        assert (details["model"], details["status"]) == (FALLBACK, mq.MISSING)
        _qualify(cfg, FALLBACK, role_inference_settings(cfg, "developer", FALLBACK))
        _result, create = await _call(llm, **call)
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_production_refuses_a_failed_required_capability_a_stale_record_and_a_non_exact_runtime(monkeypatch):
    cfg = _production(_cfg())
    with model_role("developer"):
        # KRIYA_MODEL_RUNTIME_PROBE=0 (the suite's default): the runtime is not exact.
        details = await _refused(LLMClient(cfg))
        assert details["status"] == mq.NOT_EXACT and details["runtime_fingerprint"] is None
        _exact_ollama(monkeypatch)
        model_runtime.clear_model_runtime_cache()
        _qualify(cfg, PRIMARY, role_inference_settings(cfg, "developer", PRIMARY), full_file_raw_content=mq.FAIL)
        details = await _refused(LLMClient(cfg))
        assert details["status"] == mq.NOT_QUALIFIED and any("full_file_raw_content" in r for r in details["reasons"])
        _qualify(cfg, PRIMARY, role_inference_settings(cfg, "developer", PRIMARY))
        cfg.model_qualification.measurement.bytes_per_token_margin = 0.8  # another qualification policy digest
        details = await _refused(LLMClient(cfg))
        assert details["status"] == mq.STALE


@pytest.mark.asyncio
async def test_production_admission_covers_the_native_tool_path(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _production(_cfg())
    create = AsyncMock(return_value=_response("never"))
    with patch.object(AsyncCompletions, "create", new=create), model_role("developer"):
        with pytest.raises(admission.ModelAdmissionError):
            await LLMClient(cfg).complete_with_tools_result([{"role": "user", "content": "u"}], [])
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_a_qualification_or_preflight_client_is_exempt_and_only_two_modules_build_one(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _production(_cfg())
    for purpose in (admission.PURPOSE_QUALIFICATION, admission.PURPOSE_PREFLIGHT):
        _result, create = await _call(LLMClient(cfg, purpose=purpose))
        create.assert_awaited_once()
    with pytest.raises(ValueError):
        LLMClient(cfg, purpose="trusted")
    assert LLMClient(cfg).purpose == admission.PURPOSE_RUN
    # Structural pin: an exempt client is constructed nowhere else in the product.
    sites = {}
    for path in sorted((REPO / "kriya").rglob("*.py")) + sorted((REPO / "plugins").rglob("*.py")):
        found = re.findall(r"LLMClient\([^)\n]*purpose\s*=\s*[\"'](\w+)[\"']", path.read_text(encoding="utf-8"))
        if found:
            sites[str(path.relative_to(REPO))] = found
    assert sites == {"kriya/core/model_qualification.py": ["qualification", "qualification"],
                     "kriya/production_doctor.py": ["preflight"]}
    # ... and never by a computed purpose.
    for path in sorted((REPO / "kriya").rglob("*.py")):
        for call in re.findall(r"LLMClient\([^)\n]*purpose\s*=\s*([^,)\n]+)", path.read_text(encoding="utf-8")):
            assert call.strip().strip("\"'") in admission.CLIENT_PURPOSES, (path, call)


def test_the_admission_reason_table_is_closed():
    assert admission.ADMISSION_REASON_CODES == frozenset({"MODEL_BINDING_NOT_QUALIFIED"})
    assert admission.ModelAdmissionError.reason_code in admission.ADMISSION_REASON_CODES
    assert admission.CLIENT_PURPOSES == frozenset({"run", "qualification", "preflight"})
    assert UnknownModelBindingError.reason_code == "MODEL_BINDING_UNKNOWN"


# --- admission of the run -----------------------------------------------------------------------

def test_a_fully_qualified_production_run_is_admitted_with_every_digest_recorded(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _production(_cfg())
    _qualify_every_role(cfg)
    decision = mq.admit_run(cfg)
    assert decision["admitted"] and decision["all_qualified"] and decision["enforced"]
    assert decision["schema"] == admission.ADMISSION_SCHEMA and decision["reason_code"] is None
    assert decision["qualification_policy_digest"] == mq.policy_digest_for(cfg)
    assert set(decision["roles"]) == set(mq.role_models(cfg))
    assert [e["model"] for e in decision["roles"]["developer"]] == [PRIMARY, FALLBACK]
    for entries in decision["roles"].values():
        for entry in entries:
            assert entry["status"] == mq.QUALIFIED
            assert entry["fingerprint_digest"] and entry["inference_settings_digest"].startswith("sha256:")


@pytest.mark.parametrize("role, model, label", [
    ("developer", PRIMARY, "normal"),    # the primary
    ("developer", FALLBACK, "normal"),   # a chain entry
    ("reviewer", PRIMARY, "normal"),     # another role's own identity (llm.reviewer_temperature)
    ("developer", PRIMARY, "retry"),     # the Developer's retry identity
])
def test_production_refuses_the_run_for_any_one_unqualified_identity(monkeypatch, role, model, label):
    _exact_ollama(monkeypatch)
    cfg = _production(_cfg())
    cfg.llm.temperature, cfg.llm.retry_temperature, cfg.llm.reviewer_temperature = 0.7, 0.1, 0.3
    # A record is keyed by the identity, not the role: leave out every role's use of this one identity.
    target = dict(role_inference_identities(cfg, role, model))[label].digest
    sharing = {(r, m, lab) for r, models in mq.role_models(cfg).items() for m in models
               for lab, settings in role_inference_identities(cfg, r, m) if m == model and settings.digest == target}
    _qualify_every_role(cfg, skip=sharing)
    with pytest.raises(admission.ModelAdmissionError) as refused:
        mq.admit_run(cfg)
    details = refused.value.details
    assert details["reason_code"] == admission.MODEL_BINDING_NOT_QUALIFIED and not details["admitted"]
    assert {(u["role"], u["model"], u["identity"]) for u in details["unqualified"]} == sharing
    assert {(u["status"], u["inference_settings_digest"]) for u in details["unqualified"]} == {(mq.MISSING, target)}
    assert (role, model, label) in sharing
    assert f"{role}: {model} ({label} identity) is {mq.MISSING}" in str(refused.value)
    # Negative control: with that one record the same run is admitted.
    _qualify_every_role(cfg)
    assert mq.admit_run(cfg)["admitted"]


def test_outside_production_the_same_unqualified_configuration_is_recorded_and_admitted(monkeypatch):
    _exact_ollama(monkeypatch)
    decision = mq.admit_run(_cfg())
    assert decision["admitted"] and not decision["enforced"] and not decision["all_qualified"]
    assert decision["reason_code"] is None
    assert {u["status"] for u in decision["unqualified"]} == {mq.MISSING}
    assert ("developer", PRIMARY) in {(u["role"], u["model"]) for u in decision["unqualified"]}


def test_production_refuses_the_run_when_runtime_probing_is_disabled():
    """KRIYA_MODEL_RUNTIME_PROBE=0 makes every runtime non-exact: never admitted."""
    with pytest.raises(admission.ModelAdmissionError) as refused:
        mq.admit_run(_production(_cfg()))
    assert {u["status"] for u in refused.value.details["unqualified"]} == {mq.NOT_EXACT}


# The doctor and the run admission agree on one fixture: tests/test_production_doctor.py
# (test_the_doctor_and_the_run_admission_agree_on_one_fixture; a doctor run is classified there).


# --- the workflow command and the run record of it ----------------------------------------------

def test_the_workflow_command_refuses_before_the_engine_and_carries_the_decision(monkeypatch, capsys):
    from kriya.cli import _admit_model_bindings

    _exact_ollama(monkeypatch)
    cfg = _production(_cfg())
    with pytest.raises(SystemExit) as stopped:
        _admit_model_bindings(cfg, None)
    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert "[MODEL_BINDING_NOT_QUALIFIED] model admission refused this run:" in err
    assert f"developer: {PRIMARY} (normal identity) is {mq.MISSING}" in err and "kriya model qualify" in err
    _qualify_every_role(cfg)
    admitted = _admit_model_bindings(cfg, None)
    assert admitted is not cfg and admitted._model_admission["admitted"] is True
    assert admitted.model_dump() == cfg.model_dump()
    relaxed = _cfg()
    assert _admit_model_bindings(relaxed, None) is relaxed and not hasattr(relaxed, "_model_admission")


def test_the_run_opens_with_its_model_admission_event(monkeypatch):
    from test_outcome import _deps

    from kriya.workflow.run_lifecycle import open_run_state

    _exact_ollama(monkeypatch)
    cfg = _cfg()
    deps = _deps(config=cfg)
    state = asyncio.run(open_run_state(deps, error_context=None, engineering_route=None, process_profile=None))
    event = next(e for e in state.run_events if e.kind == "model.admission")
    assert event.details["schema"] == admission.ADMISSION_SCHEMA and event.details["enforced"] is False
    assert event.details["roles"]["developer"][0]["model"] == PRIMARY
    assert "recorded; enforced only under runtime_profile: production" in event.message
    deps.logger.warning.assert_not_called()
    # A decision the workflow command already made is the one recorded (not re-assessed).
    carried = cfg.model_copy(deep=True)
    carried._model_admission = {**event.details, "marker": "from-command"}
    state = asyncio.run(open_run_state(_deps(config=carried), error_context=None, engineering_route=None,
                                       process_profile=None))
    assert next(e for e in state.run_events if e.kind == "model.admission").details["marker"] == "from-command"


# --- a larger context tier is another exact runtime ---------------------------------------------

@pytest.mark.asyncio
async def test_production_never_offers_an_operator_declared_context_tier():
    import test_prd016_adaptive_budget as tiers

    from kriya.core import token_budget as tb

    with pytest.MonkeyPatch.context() as monkeypatch, tiers.per_request_window_runtime():
        tiers._exact_ollama(monkeypatch)
        cfg = tiers._cfg(declared=[65536])
        fingerprint = model_runtime.resolve_configured_model_runtime(cfg, tiers.MODEL)
        settings = role_inference_settings(cfg, "developer", tiers.MODEL)
        offer = dict(base_url=cfg.llm.base_url, api_key=cfg.llm.api_key, settings=settings)
        declared = mq.offered_context_tiers(cfg, tiers.MODEL, fingerprint, cfg.llm.context_policy, **offer)
        assert [(t.tokens, t.source) for t in declared.tiers] == [(65536, tb.TIER_SOURCE_OPERATOR_DECLARED)]
        production = mq.offered_context_tiers(_production(cfg), tiers.MODEL, fingerprint, cfg.llm.context_policy,
                                              **offer)
        assert production.tiers == () and "requires a qualification record" in production.note
        tiers._qualify_tier(cfg, 65536)
        qualified = mq.offered_context_tiers(cfg, tiers.MODEL, fingerprint, cfg.llm.context_policy, **offer)
        assert [(t.tokens, t.source) for t in qualified.tiers] == [(65536, tb.TIER_SOURCE_QUALIFICATION_RECORD)]

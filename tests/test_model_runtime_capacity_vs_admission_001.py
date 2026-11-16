"""MODEL-RUNTIME-CAPACITY-VS-ADMISSION-AUTHORITY-001.

A binding's requested window is its runtime identity: what the runtime is
asked to serve, what production requires it to serve exactly, and what a
qualification record certifies. ``context_policy.max_context_tokens`` is the
controller's admission ceiling: the most Kriya admits into that window. The
two are separate facts - a runtime served and qualified at 65536 may be
filled only to 32768 - and one rule (``provider_contract.budget_window`` /
``context_window_state``) owns the final admission limit for dispatch,
allocation, routing and transition profiles.

Measured origin (Scenario-A run 10): both models were served at 65536 while
Kriya budgeted 32768; with one value for both facts that combination could
not be expressed, so it could not be qualified.

The windows below are arbitrary sizes on a fake runtime; nothing here is
specific to a provider or model."""
import asyncio
from types import SimpleNamespace

import pytest
from _admission import qualify_dispatched
from _fake_inference_runtime import FakeRuntimeAdapter

from kriya.config import AppConfig
from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core import token_budget as tb
from kriya.core.completion import CompletionStatus
from kriya.core.inference_runtime import register_runtime_adapter, unregister_runtime_adapter
from kriya.core.inference_settings import role_inference_settings
from kriya.core.llm import LLMClient
from kriya.core.model_routing import _served_window
from kriya.core.model_runtime import admission_ceiling, resolve_configured_model_runtime
from kriya.core.provider_contract import (
    RUNTIME_CONTEXT_IDENTITY_MISMATCH,
    SERVED_CONTEXT_BELOW_REQUESTED,
    Provenance,
    ProviderContractError,
    budget_window,
    context_window_state,
)
from kriya.workflow import resume_fingerprints as rf
from kriya.workflow.context_budget import request_capacity
from kriya.workflow.model_transition import resolve_request_profile

PRIMARY = "primary:1"
FALLBACK = "fallback:1"
SERVED = 65536
CEILING = 32768
SMALL_RUNTIME = "fake-small"


@pytest.fixture
def fake(monkeypatch):
    """Two identified runtimes: ``fake`` serves 65536, ``fake-small`` 16384."""
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    adapter = FakeRuntimeAdapter(served_window=SERVED)
    small = FakeRuntimeAdapter(SMALL_RUNTIME, served_window=16384)
    register_runtime_adapter(adapter)
    register_runtime_adapter(small)
    model_runtime.clear_model_runtime_cache()
    yield adapter
    unregister_runtime_adapter(adapter.name)
    unregister_runtime_adapter(small.name)
    model_runtime.clear_model_runtime_cache()


def _config(*, ceiling=CEILING, window=SERVED, production=True, fallback=None):
    """The primary requests ``window`` (its runtime identity) under an
    admission ``ceiling``; ``fallback`` adds one llm_chain binding."""
    config = AppConfig()
    config.llm.model = PRIMARY
    config.llm.inference_runtime = "fake"
    config.llm.context_window = window
    config.llm.context_policy.mode = "strict"
    config.llm.context_policy.max_context_tokens = ceiling
    config.llm_chain = []
    if fallback is not None:
        config = AppConfig.model_validate({**config.model_dump(), "llm_chain": [fallback]})
    if production:
        config.runtime_profile = "production"
    return config


def _prompt_of(tokens):
    """A user prompt the default bound counts as about ``tokens``."""
    return "x" * int(tb.DEFAULT_BYTES_PER_TOKEN * tokens)


def _admitted(config):
    """C-09 (S1): production dispatches only a QUALIFIED identity, so a
    production case first qualifies the one it sends (tests/_admission.py)."""
    if config.runtime_profile == "production":
        qualify_dispatched(config)
    return config


def _send(config, tokens, **kwargs):
    return asyncio.run(LLMClient(_admitted(config)).complete_result("system", _prompt_of(tokens), **kwargs))


# --- the one rule ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("requested", "served", "ceiling", "expected"), [
    (65536, 65536, 32768, 32768),    # A: served and requested above the ceiling
    (65536, 65536, None, 65536),     # B: no ceiling - unchanged
    (65536, 65536, 65536, 65536),    # C: ceiling equals the capacity
    (65536, 65536, 131072, 65536),   # D: a ceiling above the capacity never enlarges
    (65536, 16384, 32768, 16384),    # the served window still bounds below the ceiling
    (16384, 65536, 32768, 16384),    # the requested window still bounds below the ceiling
    (65536, None, 32768, 32768),     # unobserved runtime: requested, bounded by the ceiling
    (None, 65536, 32768, 32768),     # undeclared window: served, bounded by the ceiling
    (None, None, 32768, None),       # a ceiling is never itself a window
    (65536, 65536, 0, 65536),        # a non-positive ceiling is no ceiling, never a zero budget
])
def test_the_admission_window_is_the_smallest_known_bound(requested, served, ceiling, expected):
    assert budget_window(requested, served, ceiling) == expected


def test_without_a_ceiling_argument_the_rule_is_what_it_was():
    assert budget_window(65536, 32768) == 32768 and budget_window(32768, 65536) == 32768
    assert budget_window(32768, None) == 32768 and budget_window(None, 32768) == 32768
    assert budget_window(None, None) is None


@pytest.mark.parametrize("exact", [True, False])
def test_the_dispatch_state_keeps_identity_and_admission_apart(exact):
    state = context_window_state(SERVED, SERVED, Provenance.SERVER_OBSERVED, exact=exact, ceiling=CEILING)
    assert (state.requested, state.served, state.budget, state.admission_ceiling) == (SERVED, SERVED, CEILING, CEILING)
    assert state.to_dict() == {
        "requested_context_window": SERVED, "served_context_window": SERVED,
        "served_context_provenance": "server_observed", "budget_context_window": CEILING,
        "admission_ceiling": CEILING,
    }


def test_the_dispatch_state_without_a_ceiling_is_unchanged():
    state = context_window_state(SERVED, SERVED, Provenance.SERVER_OBSERVED, exact=True)
    assert state.budget == SERVED and state.admission_ceiling is None
    assert "admission_ceiling" not in state.to_dict()


def test_the_ceiling_never_satisfies_or_relaxes_the_identity_checks():
    """Served 65536 with 32768 requested stays an identity mismatch however
    the ceiling is set; a ceiling equal to a too-small served window does not
    excuse it either."""
    for ceiling in (None, 32768, 65536):
        with pytest.raises(ProviderContractError) as refused:
            context_window_state(32768, 65536, Provenance.SERVER_OBSERVED, exact=True, ceiling=ceiling)
        assert refused.value.reason_code == RUNTIME_CONTEXT_IDENTITY_MISMATCH
    with pytest.raises(ProviderContractError) as below:
        context_window_state(65536, 16384, Provenance.SERVER_OBSERVED, exact=False, ceiling=16384)
    assert below.value.reason_code == SERVED_CONTEXT_BELOW_REQUESTED


def test_the_binding_ceiling_has_one_reader(fake):
    del fake
    config = _config()
    assert admission_ceiling(config.llm) == CEILING
    assert admission_ceiling(LLMClient(config)._binding(PRIMARY)) == CEILING
    assert admission_ceiling(_config(ceiling=None).llm) is None
    assert admission_ceiling(SimpleNamespace()) is None and admission_ceiling({}) is None


# --- A-D through LLMClient under production's exact identity --------------------------------------------

def test_a_prompt_under_the_ceiling_is_admitted_to_the_runtime_served_above_it(fake):
    result = _send(_config(), 20000)
    assert result.status is CompletionStatus.OK and len(fake.requests) == 1
    assert result.budget["context_window"] == CEILING and result.budget["preferred_context_window"] == CEILING
    assert result.budget["context_state"] == {
        "requested_context_window": SERVED, "served_context_window": SERVED,
        "served_context_provenance": "server_model_config", "budget_context_window": CEILING,
        "admission_ceiling": CEILING,
    }
    assert not result.budget["context_expanded"]


def test_a_prompt_over_the_ceiling_is_refused_before_dispatch(fake):
    with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
        _send(_config(), 40000)
    assert fake.requests == []
    assert refused.value.decision.context_window == CEILING
    # The same prompt fits the runtime: the ceiling, not the capacity, refused it.
    assert _send(_config(ceiling=None), 40000).status is CompletionStatus.OK


def test_without_a_ceiling_the_served_capacity_is_the_budget(fake):
    result = _send(_config(ceiling=None), 40000)
    assert result.status is CompletionStatus.OK and len(fake.requests) == 1
    assert result.budget["context_window"] == SERVED
    assert "admission_ceiling" not in result.budget["context_state"]


def test_a_ceiling_equal_to_the_capacity_is_the_exact_capacity(fake):
    result = _send(_config(ceiling=SERVED), 40000)
    assert result.status is CompletionStatus.OK and result.budget["context_window"] == SERVED


def test_a_ceiling_above_the_capacity_never_enlarges_the_budget(fake):
    config = _config(ceiling=2 * SERVED)
    assert _send(config, 40000).budget["context_window"] == SERVED
    fake.requests.clear()
    with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
        _send(config, 70000)
    assert fake.requests == [] and refused.value.decision.context_window == SERVED


def test_the_ceiling_is_not_the_runtime_identity(fake):
    """Same runtime digest with and without it, and production still refuses
    a binding that requests the ceiling's size from a runtime serving more."""
    del fake
    digests = {resolve_configured_model_runtime(_config(ceiling=ceiling), PRIMARY, fresh=True).digest
               for ceiling in (None, 16384, CEILING, SERVED)}
    assert len(digests) == 1
    assert _send(_config(), 100).runtime_fingerprint == _send(_config(ceiling=None), 100).runtime_fingerprint
    with pytest.raises(ProviderContractError) as refused:
        _send(_config(window=CEILING, ceiling=CEILING), 100)
    assert refused.value.reason_code == RUNTIME_CONTEXT_IDENTITY_MISMATCH


# --- the served window is observed (a real runtime): before and after the call -------------------------

class _ObservingRuntime(FakeRuntimeAdapter):
    """A runtime whose loaded window can be observed, as a real one's is
    (the plain fake reports no observation, so the after-call identity check
    never runs against it)."""

    loaded = SERVED

    @property
    def provider_capabilities(self):
        from kriya.core.provider_contract import ProviderCapabilities, Support

        base = super().provider_capabilities
        return ProviderCapabilities(settings=dict(base.settings),
                                    features={"served_context_observation": Support.OBSERVABLE_ONLY})

    def observe_served_context_state(self, *, base_url, model, api_key=""):
        from kriya.core.model_runtime import OBSERVED, ServedContextObservation

        return ServedContextObservation(OBSERVED, window=self.loaded)


@pytest.fixture(params=[False, True], ids=["server_configured_window", "per_request_window"])
def observing(request, monkeypatch):
    """``per_request_window`` is the native shape: the request itself carries
    the window, so the wire must carry the served identity, never the ceiling."""
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    adapter = _ObservingRuntime("observing", per_request_context_window=request.param, served_window=SERVED)
    register_runtime_adapter(adapter)
    model_runtime.clear_model_runtime_cache()
    yield adapter
    unregister_runtime_adapter(adapter.name)
    model_runtime.clear_model_runtime_cache()


def _observing_config(**kwargs):
    config = _config(**kwargs)
    config.llm.inference_runtime = "observing"
    return config


def test_an_observed_served_window_above_the_ceiling_is_not_an_identity_violation(observing):
    """Production, the loaded window observed before and after the call: the
    identity compared is requested vs served (65536 == 65536); the admission
    budget (32768) is never what the served window is held against."""
    from _fake_inference_runtime import FAKE_WINDOW_FIELD

    result = _send(_observing_config(), 20000)
    assert result.status is CompletionStatus.OK and result.error is None
    assert result.budget["context_state"]["served_context_provenance"] == "server_observed"
    assert result.budget["context_window"] == CEILING
    contract = result.protocol["provider_contract"]
    assert contract["served_context_window_after_call"] == SERVED and "violation" not in contract
    [request] = observing.requests
    if observing.capabilities.per_request_context_window:
        assert request.extra_body[FAKE_WINDOW_FIELD] == SERVED  # the wire asks for the identity, not the ceiling
    else:
        assert FAKE_WINDOW_FIELD not in (request.extra_body or {})


def test_the_tool_call_path_holds_the_served_window_against_the_identity_too(observing):
    tools = [{"type": "function", "function": {"name": "noop", "description": "does nothing",
                                               "parameters": {"type": "object", "properties": {}}}}]
    config = _observing_config()
    config.llm.capabilities.native_tool_calls = True
    result = asyncio.run(LLMClient(_admitted(config)).complete_with_tools_result(
        [{"role": "system", "content": "system"}, {"role": "user", "content": _prompt_of(20000)}], tools))
    assert result.status is CompletionStatus.OK and result.error is None
    assert result.budget["context_window"] == CEILING
    assert "violation" not in result.protocol["provider_contract"]
    assert result.protocol["provider_contract"]["served_context_window_after_call"] == SERVED


def test_a_served_window_that_is_not_the_requested_one_is_still_a_violation_after_the_call(observing):
    """Control: the after-call check still compares against the requested
    identity - a runtime found loaded at another window is refused."""
    if observing.capabilities.per_request_context_window:
        pytest.skip("a per-request runtime serves what the request carries; the mismatch control is the server-configured shape")
    config = _admitted(_observing_config())
    llm = LLMClient(config)
    calls = {"n": 0}
    original = observing.observe_served_context_state

    def drifting(**kwargs):
        calls["n"] += 1
        observing.loaded = SERVED if calls["n"] == 1 else 2 * SERVED
        return original(**kwargs)

    observing.observe_served_context_state = drifting
    result = asyncio.run(llm.complete_result("system", _prompt_of(100)))
    assert result.status is CompletionStatus.PROVIDER_CONTRACT_VIOLATION
    assert result.error.reason_code == RUNTIME_CONTEXT_IDENTITY_MISMATCH


# --- every budget consumer -----------------------------------------------------------------------------

def test_allocation_routing_and_transition_use_the_same_admission_window(fake):
    del fake
    capped, uncapped = _config(), _config(ceiling=None)
    assert request_capacity(uncapped).tokens - request_capacity(capped).tokens == SERVED - CEILING
    assert request_capacity(capped).tokens < CEILING < request_capacity(uncapped).tokens
    assert resolve_request_profile(capped).context_window == CEILING
    assert resolve_request_profile(uncapped).context_window == SERVED
    fingerprint = resolve_configured_model_runtime(capped, PRIMARY)
    assert _served_window(fingerprint, capped.llm) == CEILING
    assert _served_window(fingerprint, uncapped.llm) == SERVED
    # The profile's identity part is the runtime's, unchanged by the ceiling.
    assert resolve_request_profile(capped).runtime_digest == resolve_request_profile(uncapped).runtime_digest


def test_allocation_honours_the_ceiling_when_the_runtime_cannot_be_resolved(monkeypatch):
    """Probing off: no served window is known, the ceiling still bounds."""
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "0")
    capped, uncapped = _config(production=False), _config(ceiling=None, production=False)
    capped.llm.inference_runtime = uncapped.llm.inference_runtime = None
    assert request_capacity(uncapped).tokens - request_capacity(capped).tokens == SERVED - CEILING


# --- F: a fallback's budget is its own runtime's capacity under its own ceiling --------------------------

@pytest.mark.parametrize(("runtime", "window", "expected"), [
    ("fake", SERVED, CEILING),        # the target serves more than the ceiling
    (SMALL_RUNTIME, 16384, 16384),    # the target serves less than the source's budget
])
def test_a_fallback_transition_recomputes_the_admission_window(fake, runtime, window, expected):
    del fake
    config = _config(production=False, fallback={
        "model": FALLBACK, "base_url": "http://localhost:11434/v1", "inference_runtime": runtime,
        "context_window": window, "context_policy": {"mode": "strict", "max_context_tokens": CEILING}})
    binding = config.llm_chain[0]
    source, target = resolve_request_profile(config), resolve_request_profile(config, binding)
    assert source.context_window == CEILING and target.context_window == expected
    assert target.runtime_exact and target.runtime_digest != source.runtime_digest
    plain = _config(production=False, fallback={
        "model": FALLBACK, "base_url": "http://localhost:11434/v1", "inference_runtime": runtime,
        "context_window": window, "context_policy": {"mode": "strict"}})
    assert request_capacity(plain, plain.llm_chain[0]).tokens - request_capacity(config, binding).tokens \
        == window - expected
    result = asyncio.run(LLMClient(config).complete_result("system", "user", model_override=FALLBACK))
    assert result.budget["context_window"] == expected


def test_a_fallback_without_its_own_ceiling_does_not_inherit_the_primarys(fake):
    """The ceiling is the binding's own policy, like the rest of its
    context_policy: an operator states it on every binding it applies to."""
    del fake
    config = _config(production=False, fallback={
        "model": FALLBACK, "base_url": "http://localhost:11434/v1", "inference_runtime": "fake",
        "context_window": SERVED})
    assert resolve_request_profile(config, config.llm_chain[0]).context_window == SERVED


# --- qualification certifies the runtime, never the controller's policy --------------------------------

def test_a_qualification_run_is_not_bounded_by_the_admission_ceiling(fake):
    config = _config()
    qualifying = mq.qualification_config(config, PRIMARY, settings=role_inference_settings(config, "developer", PRIMARY))
    assert qualifying.llm.context_policy.max_context_tokens is None
    assert config.llm.context_policy.max_context_tokens == CEILING  # the original is untouched
    assert qualifying.llm.context_window == SERVED
    # A probe sized to the served window is admitted in the qualifying config only.
    assert _send(qualifying, 40000).status is CompletionStatus.OK
    fake.requests.clear()
    with pytest.raises(tb.ContextBudgetUnsatisfiableError):
        _send(config, 40000)
    assert fake.requests == []


def test_a_fallbacks_qualification_config_drops_its_ceiling_too(fake):
    del fake
    config = _config(production=False, fallback={
        "model": FALLBACK, "base_url": "http://localhost:11434/v1", "inference_runtime": "fake",
        "context_window": SERVED, "context_policy": {"max_context_tokens": CEILING}})
    qualifying = mq.qualification_config(config, FALLBACK)
    assert qualifying.llm_chain[0].context_policy.max_context_tokens is None
    assert qualifying.llm.context_policy.max_context_tokens is None


def test_changing_only_the_ceiling_keeps_the_qualification_identity(fake):
    """Runtime digest, inference-settings digest and qualification policy
    digest are what a record is keyed and checked by: none moves."""
    del fake

    def identity(ceiling):
        config = _config(ceiling=ceiling)
        return (resolve_configured_model_runtime(config, PRIMARY, fresh=True).digest,
                role_inference_settings(config, "developer", PRIMARY).digest, mq.policy_digest_for(config))

    assert identity(None) == identity(16384) == identity(CEILING) == identity(SERVED)


def test_a_record_qualified_at_the_served_window_counts_under_any_ceiling(fake):
    del fake
    config = _config()
    runtime = resolve_configured_model_runtime(config, PRIMARY, fresh=True)
    settings = role_inference_settings(config, "developer", PRIMARY)
    results = [mq.CaseResult(capability, mq.PASS) for capability in mq.CAPABILITIES]
    mq.save_record(mq.build_record(runtime, results, settings=settings))
    statuses = set()
    for ceiling in (None, 16384, CEILING):
        other = _config(ceiling=ceiling)
        statuses.add(mq.assess(resolve_configured_model_runtime(other, PRIMARY, fresh=True),
                               mq.required_capabilities(other, "developer", PRIMARY),
                               settings=role_inference_settings(other, "developer", PRIMARY),
                               policy_digest=mq.policy_digest_for(other)).status)
    assert statuses == {mq.QUALIFIED}


# --- E: the ceiling is run configuration, so a resume cannot inherit another one -------------------------

def _config_fingerprint(config):
    owned = rf.split_config_by_owner(config.model_dump())
    return owned, rf.Fingerprint(rf._digest(owned["config"]), "config-excluding-owned-fields")


@pytest.mark.parametrize("where", ["primary", "fallback"])
def test_a_changed_ceiling_changes_the_resume_config_fingerprint(where):
    fallback = {"model": FALLBACK, "base_url": "http://localhost:11434/v1", "context_window": SERVED,
                "context_policy": {"max_context_tokens": CEILING}}
    saved, current = _config(fallback=fallback), _config(fallback=fallback)
    if where == "primary":
        current.llm.context_policy.max_context_tokens = SERVED
    else:
        current.llm_chain[0].context_policy.max_context_tokens = SERVED
    (saved_owned, saved_fp), (current_owned, current_fp) = _config_fingerprint(saved), _config_fingerprint(current)
    assert saved_fp.value != current_fp.value
    # Controller policy only: the model-identity bucket of the config is the same.
    assert saved_owned["model_runtime"] == current_owned["model_runtime"]
    comparisons = {c.name: c for c in rf.compare_resume_fingerprints(
        rf.fingerprint_block({"config": saved_fp}), {"config": current_fp}, {"plan", "candidate"})}
    assert comparisons["config"].status is rf.FingerprintStatus.CHANGED
    for stage_artifact in ("knowledge_clearance", "plan", "design", "candidate", "candidate_gate_outcomes"):
        assert "config" in rf.ARTIFACT_DEPENDENCIES[stage_artifact]
    # Control: the same ceiling compares as a match.
    same = {c.name: c for c in rf.compare_resume_fingerprints(
        rf.fingerprint_block({"config": saved_fp}), {"config": _config_fingerprint(_config(fallback=fallback))[1]},
        {"plan", "candidate"})}
    assert same["config"].status is rf.FingerprintStatus.MATCH

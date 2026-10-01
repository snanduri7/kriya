"""PROVIDER-CONTRACT-001A: post-closure hardening of the provider contract.

F-1: every model call inside a run is bounded by the run's generation budget
(one root clock per run, never reset by a work unit or retry); the request
timeout is min(transport timeout, remaining budget) and a deadline stop is a
typed Kriya failure, never a provider timeout."""
import asyncio
import os
import re
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from _fake_inference_runtime import FakeRuntimeAdapter

from kriya.config import AppConfig, ModelCapabilities
from kriya.control.run_coordinator import (
    authorize_candidate_workspace,
    begin_mutating_run,
    claim_run_generation_clock,
    current_run_context,
)
from kriya.core import model_runtime
from kriya.core.completion import CompletionStatus
from kriya.core.inference_runtime import register_runtime_adapter, unregister_runtime_adapter
from kriya.core.kernel import Kernel
from kriya.core.llm import (
    DEADLINE_SOURCE_LOCAL_CAP,
    DEADLINE_SOURCE_RUN_BUDGET,
    INFERENCE_DEADLINE_EXCEEDED,
    INFERENCE_DEADLINE_EXHAUSTED,
    InferenceDeadlineError,
    LLMClient,
    inference_deadline,
)
from kriya.core.provider_contract import SERVED_CONTEXT_UNOBSERVABLE
from kriya.workflow import attempt as attempt_module
from kriya.workflow.recovery_coordinator import classify_attempt_exception
from kriya.workflow.workflow import WorkflowEngine

REPO = Path(__file__).resolve().parent.parent


class _SlowRuntime(FakeRuntimeAdapter):
    """Answers only after ``delay`` seconds and ignores the request's own
    timeout: only Kriya's total deadline can stop it."""

    delay = 0.0

    def __init__(self):
        super().__init__()
        self.started = []  # every request dispatched, answered or not

    async def complete(self, client, request):
        self.started.append(request)
        await asyncio.sleep(self.delay)
        return await super().complete(client, request)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    adapter = _SlowRuntime()
    register_runtime_adapter(adapter)
    model_runtime.clear_model_runtime_cache()
    yield adapter
    unregister_runtime_adapter(adapter.name)
    model_runtime.clear_model_runtime_cache()


def _config(*, budget=None, read=900.0):
    config = AppConfig()
    config.llm.model = "primary:1"
    config.llm.inference_runtime = "fake"
    config.llm.context_window = 8192
    config.llm_chain = []
    config.autonomy.generation_time_budget_seconds = budget
    config.llm.transport.read_timeout_seconds = read
    return config


def _in_run(workspace, call, *, already_used=0.0):
    """Run ``call()`` inside one mutating run whose generation clock started
    ``already_used`` seconds ago (an earlier unit's model work)."""
    with begin_mutating_run(str(workspace)):
        current_run_context()._lease.generation_clock = time.monotonic() - already_used  # pylint: disable=protected-access
        return asyncio.run(call())


def _deadline(llm):
    return llm.last_completion.protocol["provider_contract"]["deadline"]


# --- A/B: the effective request timeout is the smaller bound --------------------------------------------

def test_a_smaller_transport_timeout_is_the_request_timeout(fake, tmp_path):
    llm = LLMClient(_config(budget=600, read=180.0))
    _in_run(tmp_path, lambda: llm.complete_result("s", "u"))
    assert fake.requests[0].timeout.read == 180.0
    record = _deadline(llm)
    assert record["deadline_bound"] is True and record["deadline_source"] == DEADLINE_SOURCE_RUN_BUDGET
    assert record["effective_request_timeout_ms"] == 180_000 and record["configured_transport_timeout_ms"] == 180_000
    assert record["remaining_budget_at_dispatch_ms"] > 590_000
    assert llm.last_completion.protocol["provider_contract"]["inference_deadline_bound"] is True


def test_a_smaller_remaining_budget_is_the_request_timeout(fake, tmp_path):
    llm = LLMClient(_config(budget=600, read=600.0))
    _in_run(tmp_path, lambda: llm.complete_result("s", "u"), already_used=575)
    assert 24.0 < fake.requests[0].timeout.read <= 25.0
    record = _deadline(llm)
    assert 24_000 < record["effective_request_timeout_ms"] <= 25_000
    assert record["configured_transport_timeout_ms"] == 600_000


# --- C: exhausted before dispatch -----------------------------------------------------------------------

def test_an_exhausted_budget_refuses_before_any_provider_contact(fake, tmp_path):
    llm = LLMClient(_config(budget=600))
    with pytest.raises(InferenceDeadlineError) as refused:
        _in_run(tmp_path, lambda: llm.complete_result("s", "u"), already_used=601)
    assert refused.value.reason_code == INFERENCE_DEADLINE_EXHAUSTED
    assert refused.value.details["deadline_source"] == DEADLINE_SOURCE_RUN_BUDGET
    assert refused.value.details["remaining_budget_at_dispatch_ms"] == 0
    # No inference request, no runtime probe, no served-context observation.
    assert fake.requests == [] and fake.probes == []


def test_an_exhausted_budget_refuses_a_tool_call_too(fake, tmp_path):
    config = _config(budget=600)
    config.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    llm = LLMClient(config)
    with pytest.raises(InferenceDeadlineError):
        _in_run(tmp_path, lambda: llm.complete_with_tools_result(
            [{"role": "user", "content": "u"}], [{"type": "function", "function": {"name": "t", "parameters": {}}}]),
            already_used=601)
    assert fake.requests == []


# --- D: a slow provider is cut at the deadline, as one request ----------------------------------------------

def test_a_request_outliving_the_budget_is_a_typed_deadline_stop(fake, tmp_path):
    fake.delay = 30.0  # far beyond the 0.5 s left; the fake ignores its read timeout
    llm = LLMClient(_config(budget=600))
    started = time.monotonic()
    with pytest.raises(InferenceDeadlineError) as stopped:
        _in_run(tmp_path, lambda: llm.complete_result("s", "u"), already_used=599.5)
    assert time.monotonic() - started < 5.0
    assert stopped.value.reason_code == INFERENCE_DEADLINE_EXCEEDED
    assert len(fake.started) == 1 and fake.requests == []  # one request, cut: no SDK or Kriya resend
    result = llm.last_completion
    assert result.status is CompletionStatus.TIMEOUT and result.backend_status == "deadline"
    record = _deadline(llm)
    assert record["deadline_bound"] is True and record["timeout_reason"] == INFERENCE_DEADLINE_EXCEEDED
    assert record["elapsed_request_ms"] >= 400


def test_a_provider_timeout_unrelated_to_the_deadline_stays_a_provider_timeout(fake, tmp_path):
    from _fake_inference_runtime import FakeTimeout

    fake.replies = [FakeTimeout("read timed out")]
    llm = LLMClient(_config(budget=600, read=180.0))
    result = _in_run(tmp_path, lambda: llm.complete_result("s", "u"))
    assert result.status is CompletionStatus.TIMEOUT and not isinstance(result.error, InferenceDeadlineError)


# --- E: retries and later units consume the remaining budget, never a fresh one --------------------------

def test_a_later_call_in_the_same_run_gets_only_the_remaining_budget(fake, tmp_path):
    llm = LLMClient(_config(budget=600, read=900.0))

    async def two_calls():
        await llm.complete_result("s", "first")
        await asyncio.sleep(0.3)
        await llm.complete_result("s", "retry")

    _in_run(tmp_path, two_calls)
    first, second = (request.timeout.read for request in fake.requests)
    assert first <= 600.0 and second <= first - 0.25


def test_the_run_clock_starts_once_and_is_shared_by_every_claim(tmp_path):
    assert claim_run_generation_clock() is None  # outside a run: no clock
    with begin_mutating_run(str(tmp_path)):
        first = claim_run_generation_clock()
        time.sleep(0.01)
        assert claim_run_generation_clock() == first
    with begin_mutating_run(str(tmp_path)):
        assert claim_run_generation_clock() != first  # a new run is a new budget


def _successful_engine(cfg):
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code", "Design: Write math.py", "def add(a,b):\n    return a+b", "Review: Approved",
    ])
    return WorkflowEngine(Kernel(config=cfg), llm)


def _workflow_config(budget):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.generation_time_budget_seconds = budget
    return cfg


def test_a_work_unit_never_restarts_the_run_budget(tmp_path):
    # F-1 original symptom: the budget clock restarted with every
    # run_generation_workflow call (each milestone unit / enforce subtask).
    # Here an earlier unit of the same run already used 3590 s of 3600.
    engine = _successful_engine(_workflow_config(3600))
    result = _in_run(tmp_path, lambda: engine.run_generation_workflow(
        goal="Create math library", workspace_path=str(tmp_path)), already_used=3590)
    assert result["quality_gates_passed"] is False
    assert result["failure_category"] == "generation_budget_exhausted"
    assert not os.path.exists(tmp_path / "math.py")


def test_two_work_units_of_one_run_share_one_budget_clock(tmp_path, monkeypatch):
    seen = []
    real = attempt_module._ensure_generation_time_budget  # pylint: disable=protected-access

    def spy(state, ctx, **kwargs):
        seen.append(state.budget_started_monotonic)
        return real(state, ctx, **kwargs)

    monkeypatch.setattr(attempt_module, "_ensure_generation_time_budget", spy)

    async def two_units():
        for unit in ("one", "two"):
            workspace = tmp_path / unit
            workspace.mkdir()
            engine = _successful_engine(_workflow_config(3600))
            authorize_candidate_workspace(str(workspace))  # each unit in its own authorized workspace
            result = await engine.run_generation_workflow(goal="Create math library", workspace_path=str(workspace))
            assert result["quality_gates_passed"] is True

    with begin_mutating_run(str(tmp_path)):
        asyncio.run(two_units())
    assert len(seen) == 2 and seen[0] == seen[1]


# --- typed failure at the workflow boundary ------------------------------------------------------------

def test_a_deadline_stop_inside_an_attempt_is_the_time_budget_stop():
    error = InferenceDeadlineError(INFERENCE_DEADLINE_EXCEEDED, {
        "deadline_bound": True, "deadline_source": DEADLINE_SOURCE_RUN_BUDGET,
        "remaining_budget_at_dispatch_ms": 1200, "configured_transport_timeout_ms": 900_000,
        "effective_request_timeout_ms": 1200})
    classified = classify_attempt_exception(error, None, last_attempt_mode=None,
                                           ground_scope_denial=lambda exc, ctx: None)
    assert classified.failure.type == "time_budget_exhausted"
    assert classified.failure.message.startswith("GENERATION TIME BUDGET EXHAUSTED:")
    assert classified.failure.diagnostics["reason_code"] == INFERENCE_DEADLINE_EXCEEDED


# --- no budget, local caps, no leaks ----------------------------------------------------------------------

def test_without_a_run_budget_the_call_is_honestly_unbound(fake, tmp_path):
    llm = LLMClient(_config(budget=None))
    _in_run(tmp_path, lambda: llm.complete_result("s", "u"))
    record = _deadline(llm)
    assert record["deadline_bound"] is False and record["deadline_source"] is None
    assert fake.requests[0].timeout.read == 900.0


def test_a_local_cap_only_narrows_the_run_deadline(fake, tmp_path):
    llm = LLMClient(_config(budget=600))

    async def capped(cap):
        with inference_deadline(time.monotonic() + cap):
            await llm.complete_result("s", "u")
        return _deadline(llm)

    assert _in_run(tmp_path, lambda: capped(5))["deadline_source"] == DEADLINE_SOURCE_LOCAL_CAP
    assert fake.requests[-1].timeout.read <= 5
    # A cap later than the run deadline cannot extend it.
    record = _in_run(tmp_path, lambda: capped(10_000), already_used=590)
    assert record["deadline_source"] == DEADLINE_SOURCE_RUN_BUDGET and fake.requests[-1].timeout.read <= 10


def test_no_deadline_leaks_out_of_a_run_or_a_cap(fake, tmp_path):
    llm = LLMClient(_config(budget=600))
    _in_run(tmp_path, lambda: llm.complete_result("s", "u"), already_used=599)
    asyncio.run(llm.complete_result("s", "after the run"))  # e.g. qualification, outside any run
    assert _deadline(llm)["deadline_bound"] is False and fake.requests[-1].timeout.read == 900.0

    async def capped_then_free():
        with inference_deadline(time.monotonic() + 1):
            await llm.complete_result("s", "capped")
        await llm.complete_result("s", "free")

    asyncio.run(capped_then_free())
    assert fake.requests[-1].timeout.read == 900.0


# --- structure: one budget clock, one deadline owner ------------------------------------------------------

def test_the_generation_budget_is_measured_only_from_the_run_clock():
    attempt_source = (REPO / "kriya/workflow/attempt.py").read_text(encoding="utf-8")
    budget_fn = attempt_source.split("def _ensure_generation_time_budget(", 1)[1].split("\ndef ", 1)[0]
    assert "state.budget_started_monotonic" in budget_fn and "generation_started_monotonic" not in budget_fn
    workflow_source = (REPO / "kriya/workflow/workflow.py").read_text(encoding="utf-8")
    assert workflow_source.count("claim_run_generation_clock()") == 1
    # Production never sets a deadline of its own: the run budget is the owner.
    for path in (REPO / "kriya").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name != "llm.py":
            assert not re.search(r"\binference_deadline\(", text), path


# === F-3: served-context observation is typed, retried once, never blacklisted ===========================

V1 = "http://127.0.0.1:11434/v1"
LOADED = {"models": [{"name": "m:1", "context_length": 32768}]}


class _ScriptedTransport:
    """/api/ps answers in order (dicts returned, exceptions raised), per URL."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls = []

    def __call__(self, url, body, api_key):
        self.urls.append(url)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _observe(transport, base_url=V1):
    return model_runtime.observe_served_context_state(base_url=base_url, model="m:1", transport=transport)


def test_one_transient_failure_is_absorbed_by_one_bounded_retry():
    transport = _ScriptedTransport(TimeoutError("transient"), LOADED)
    observation = _observe(transport)
    assert (observation.status, observation.window) == (model_runtime.OBSERVED, 32768)
    assert transport.urls == ["http://127.0.0.1:11434/api/ps"] * 2


def test_a_failed_observation_is_never_remembered():
    # F-3 original symptom: one failure blacklisted the endpoint for the process.
    failing = _ScriptedTransport(TimeoutError("down"), TimeoutError("still down"))
    failed = _observe(failing)
    assert failed.status == model_runtime.OBSERVATION_FAILED and "TimeoutError" in failed.reason
    assert len(failing.urls) == model_runtime.SERVED_CONTEXT_OBSERVATION_ATTEMPTS == 2  # bounded, no loop
    healthy = _ScriptedTransport(LOADED)
    assert _observe(healthy).window == 32768 and len(healthy.urls) == 1


def test_endpoint_a_failing_never_affects_endpoint_b():
    assert _observe(_ScriptedTransport(OSError("a"), OSError("a"))).status == model_runtime.OBSERVATION_FAILED
    assert _observe(_ScriptedTransport(LOADED), base_url="http://localhost:11435/v1").window == 32768


@pytest.mark.parametrize("answers, status", [
    ((LOADED,), model_runtime.OBSERVED),
    (({"models": []},), model_runtime.NOT_LOADED),  # up, model not loaded: not a provider failure
    (({"models": [{"name": "m:1"}]},), model_runtime.LOADED_WITHOUT_WINDOW),
    (({"models": [{"name": "m:1", "context_length": True}]},), model_runtime.LOADED_WITHOUT_WINDOW),
    (({"unexpected": 1}, {"unexpected": 1}), model_runtime.OBSERVATION_FAILED),  # malformed, retried once
])
def test_each_observation_outcome_is_distinguished(answers, status):
    assert _observe(_ScriptedTransport(*answers)).status == status


def test_an_unobservable_configuration_is_not_applicable_never_failed(monkeypatch):
    assert _observe(_ScriptedTransport(), base_url="https://example.com/v1").status == model_runtime.NOT_APPLICABLE
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "0")
    observation = model_runtime.observe_served_context_state(base_url=V1, model="m:1")
    assert (observation.status, observation.reason) == (model_runtime.NOT_APPLICABLE, "probing_disabled")


def test_no_negative_observation_cache_exists():
    source = (REPO / "kriya/core/model_runtime.py").read_text(encoding="utf-8")
    assert "_UNOBSERVABLE_ENDPOINTS" not in source


class _ObservableRuntime(FakeRuntimeAdapter):
    """Declares served-context observation like the Ollama adapters; each
    observation is scripted (before the call, then after it)."""

    def __init__(self):
        super().__init__(name="fake-observable")
        self.observations = []

    @property
    def provider_capabilities(self):
        return model_runtime.OPENAI_COMPAT_CAPABILITIES

    def observe_served_context_state(self, **_):
        return self.observations.pop(0) if self.observations else model_runtime.ServedContextObservation(
            model_runtime.OBSERVED, window=8192)


@pytest.fixture
def observable(monkeypatch):
    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "record_fingerprint", lambda *a, **k: None)
    adapter = _ObservableRuntime()
    register_runtime_adapter(adapter)
    model_runtime.clear_model_runtime_cache()
    yield adapter
    unregister_runtime_adapter(adapter.name)
    model_runtime.clear_model_runtime_cache()


def _observable_config(production):
    config = _config()
    config.llm.inference_runtime = "fake-observable"
    if production:
        config.runtime_profile = "production"
    return config


def _obs(status, window=None, reason=None):
    return model_runtime.ServedContextObservation(status, window=window, reason=reason)


@pytest.mark.parametrize("after", [
    _obs(model_runtime.OBSERVATION_FAILED, reason="TimeoutError: /api/ps"),
    _obs(model_runtime.NOT_LOADED),  # e.g. OLLAMA_KEEP_ALIVE=0 unloaded it right after answering
    _obs(model_runtime.LOADED_WITHOUT_WINDOW, reason="no usable context_length"),
])
def test_production_refuses_a_result_whose_served_window_is_unobservable_after_the_call(observable, after):
    observable.observations = [_obs(model_runtime.OBSERVED, window=8192), after]
    result = asyncio.run(LLMClient(_observable_config(production=True)).complete_result("s", "u"))
    assert result.runtime_fingerprint_exact
    assert result.status is CompletionStatus.PROVIDER_CONTRACT_VIOLATION
    assert result.error.reason_code == SERVED_CONTEXT_UNOBSERVABLE
    contract = result.protocol["provider_contract"]
    assert contract["served_context_observation"]["status"] == after.status
    assert "served_context_window_after_call" not in contract


def test_a_model_unloaded_before_the_call_is_not_a_failure(observable):
    observable.observations = [_obs(model_runtime.NOT_LOADED), _obs(model_runtime.OBSERVED, window=8192)]
    result = asyncio.run(LLMClient(_observable_config(production=True)).complete_result("s", "u"))
    assert result.status is CompletionStatus.OK
    assert result.protocol["provider_contract"]["served_context_window_after_call"] == 8192


def test_outside_production_an_unobservable_window_is_explicitly_unverified(observable, caplog):
    observable.observations = [_obs(model_runtime.OBSERVED, window=8192),
                               _obs(model_runtime.OBSERVATION_FAILED, reason="TimeoutError: /api/ps")]
    with caplog.at_level("WARNING"):
        result = asyncio.run(LLMClient(_observable_config(production=False)).complete_result("s", "u"))
    assert result.status is CompletionStatus.OK
    contract = result.protocol["provider_contract"]
    assert contract["served_context_observation"]["status"] == model_runtime.OBSERVATION_FAILED
    assert contract["served_context_window_after_call_provenance"] == "unverified"
    assert "served_context_window_after_call" not in contract  # never claimed as observed
    assert any("unverified" in record.getMessage() for record in caplog.records)


def test_an_observation_error_raised_by_an_adapter_is_a_failed_observation(observable):
    def boom(**_):
        raise RuntimeError("adapter bug")

    observable.observe_served_context_state = boom
    result = asyncio.run(LLMClient(_observable_config(production=True)).complete_result("s", "u"))
    assert result.error.reason_code == SERVED_CONTEXT_UNOBSERVABLE
    assert result.protocol["provider_contract"]["served_context_observation"]["status"] == \
        model_runtime.OBSERVATION_FAILED


def test_a_malformed_observation_is_retried_exactly_once():
    transport = _ScriptedTransport(["not", "a", "dict"], {"models": "nope"})
    assert _observe(transport).status == model_runtime.OBSERVATION_FAILED
    assert len(transport.urls) == 2


# === F-1 at the role and terminal boundaries ==============================================================

def _deadline_error(code=INFERENCE_DEADLINE_EXHAUSTED):
    return InferenceDeadlineError(code, {"deadline_bound": True, "deadline_source": DEADLINE_SOURCE_RUN_BUDGET,
                                         "remaining_budget_at_dispatch_ms": 0,
                                         "configured_transport_timeout_ms": 900_000,
                                         "effective_request_timeout_ms": 0})


def test_a_deadline_stop_is_never_escalated_to_the_next_candidate():
    from kriya.agents.agent import _call_with_escalation
    from kriya.config import FallbackModelConfig

    llm = LLMClient(AppConfig())
    llm.complete = AsyncMock(side_effect=[_deadline_error(), "a fallback answer"])
    with pytest.raises(InferenceDeadlineError):
        asyncio.run(_call_with_escalation(llm, "s", "u", [None, FallbackModelConfig(model="fallback:1")]))
    assert llm.complete.await_count == 1


def test_a_deadline_stop_at_the_final_review_keeps_the_committed_run(tmp_path):
    cfg = _workflow_config(None)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code", "Design: Write math.py", "def add(a,b):\n    return a+b",
        _deadline_error(INFERENCE_DEADLINE_EXCEEDED),
    ])
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    result = asyncio.run(engine.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path)))
    assert result["failure_category"] == "final_review_refused"
    refusal = result["final_review_refusal"]
    assert refusal["reason_code"] == INFERENCE_DEADLINE_EXCEEDED
    assert refusal["candidate_applied"] is True and refusal["rolled_back"] is False
    assert (tmp_path / "math.py").exists()


def test_a_deadline_stop_is_never_resent_without_response_format(fake, tmp_path):
    # The reasoning-model JSON fallback resends once on a REQUEST-kind error;
    # a deadline stop is not one (the evidence would otherwise read EXHAUSTED
    # and a dropped response_format that never happened).
    fake.delay = 30.0
    llm = LLMClient(_config(budget=600))
    with pytest.raises(InferenceDeadlineError) as stopped:
        _in_run(tmp_path, lambda: llm.complete_result("s", "u", json_mode=True, reasoning_override=True),
                already_used=599.5)
    assert stopped.value.reason_code == INFERENCE_DEADLINE_EXCEEDED
    assert len(fake.started) == 1
    assert llm.last_completion.protocol["response_format_dropped"] is False

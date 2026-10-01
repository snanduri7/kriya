"""MODEL-EVIDENCE-HARDENING-001: a Developer retry at a differing
``llm.retry_temperature`` is its own inference identity. It is enumerated for
qualification, recorded as executed, and - under the production runtime
profile - refused before any request unless that exact identity is QUALIFIED.
It never runs under the normal-temperature identity's qualification."""
import json
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner
from openai.resources.chat.completions import AsyncCompletions
from test_prd017_fallback_transition import PRIMARY, _ctx, _exact_ollama, _response
from test_prd017_fallback_transition import _cfg as _prd017_cfg

from kriya.agents.agent import DeveloperAgent
from kriya.core import model_qualification as mq
from kriya.core.inference_settings import (
    retry_inference_settings,
    role_inference_identities,
    role_inference_settings,
)
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import resolve_configured_model_runtime
from kriya.workflow.attempt import _run_developer_generation
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.model_transition import RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED, resolve_request_profile
from kriya.workflow.state import GenerationState


def _cfg(*, retry=0.1, production=True):
    cfg = _prd017_cfg()
    cfg.llm.temperature = 0.7
    cfg.llm.retry_temperature = retry
    if production:
        cfg.runtime_profile = "production"
    return cfg


def _qualify(cfg, settings):
    runtime = resolve_configured_model_runtime(cfg, PRIMARY)
    mq.save_record(mq.build_record(runtime, [mq.CaseResult(c, mq.PASS) for c in mq.CAPABILITIES],
                                   settings=settings))


def _retry_kwargs(cfg):
    return dict(task_description="t", design_context="d", existing_code_context="",
                known_target_files=["A.java"], prior_error_context="A.java:3: error: ';' expected",
                implicated_files=["A.java"], retry_temperature=cfg.llm.retry_temperature)


# --- identity ----------------------------------------------------------------------------------

def test_a_retry_at_the_normal_temperature_is_the_same_identity():
    cfg = _cfg(retry=0.7)
    assert retry_inference_settings(cfg, "developer") is None
    assert [label for label, _ in role_inference_identities(cfg, "developer", PRIMARY)] == ["normal"]


def test_a_differing_retry_temperature_is_a_distinct_identity_for_the_developer_only():
    cfg = _cfg(retry=0.1)
    (_, normal), (label, retry) = role_inference_identities(cfg, "developer", PRIMARY)
    assert label == "retry" and retry.temperature == 0.1 and retry.digest != normal.digest
    assert retry.extra_body == normal.extra_body and retry.reasoning == normal.reasoning
    assert len(role_inference_identities(cfg, "reviewer", PRIMARY)) == 1


def test_no_retry_temperature_leaves_everything_as_before(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(retry=None)
    assert len(role_inference_identities(cfg, "developer", PRIMARY)) == 1
    profile = resolve_request_profile(cfg)
    assert (profile.retry_inference_settings_digest, profile.retry_qualification) == (None, None)


# --- the production gate -----------------------------------------------------------------------

async def _generate(cfg, tmp_path, create, **kwargs):
    developer = DeveloperAgent("developer", LLMClient(cfg))
    state = GenerationState()
    state.attempt_number = 2
    with patch.object(AsyncCompletions, "create", new=create):
        await _run_developer_generation(state, _ctx(str(tmp_path), cfg, developer), **kwargs)
    return developer, state


@pytest.mark.asyncio
async def test_a_production_retry_with_an_unqualified_retry_identity_is_refused_before_inference(
        tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    _qualify(cfg, role_inference_settings(cfg, "developer", PRIMARY))  # the normal identity only
    create = AsyncMock(return_value=_response("class A {}\n"))
    with pytest.raises(QualityGateFailure) as refused:
        await _generate(cfg, tmp_path, create, **_retry_kwargs(cfg))
    failure = refused.value.failure
    assert failure.type == "retry_identity_not_qualified"
    assert failure.diagnostics["reason_code"] == RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED
    assert failure.diagnostics["retry_qualification"] == mq.MISSING
    assert failure.message.startswith(f"{RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED}:")
    create.assert_not_awaited()  # nothing was sent


@pytest.mark.asyncio
async def test_with_both_identities_qualified_the_retry_executes_under_the_retry_identity(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    for _, settings in role_inference_identities(cfg, "developer", PRIMARY):
        _qualify(cfg, settings)
    create = AsyncMock(return_value=_response("class A {}\n"))
    developer, _ = await _generate(cfg, tmp_path, create, **_retry_kwargs(cfg))
    assert create.await_count == 1 and create.call_args.kwargs["temperature"] == 0.1
    retry = retry_inference_settings(cfg, "developer")
    # Completion telemetry and metrics carry the executed (retry) identity.
    assert developer.llm.last_completion.inference_settings_digest == retry.digest
    (row,) = developer.llm.role_metrics.take_unreported()
    assert (row["role"], row["inference_settings_digest"]) == ("developer", retry.digest)


@pytest.mark.asyncio
async def test_outside_production_the_retry_is_recorded_not_refused(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(production=False)
    create = AsyncMock(return_value=_response("class A {}\n"))
    developer, state = await _generate(cfg, tmp_path, create, **_retry_kwargs(cfg))
    assert create.await_count == 1
    assert state.last_developer_request_profile.retry_qualification == mq.MISSING


@pytest.mark.asyncio
async def test_a_first_attempt_without_a_retry_temperature_is_not_gated(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    _qualify(cfg, role_inference_settings(cfg, "developer", PRIMARY))
    create = AsyncMock(return_value=_response("class A {}\n"))
    await _generate(cfg, tmp_path, create, task_description="t", design_context="d", existing_code_context="",
                    known_target_files=["A.java"])
    assert create.await_count == 1 and create.call_args.kwargs["temperature"] == 0.7


def test_changing_the_retry_temperature_invalidates_the_retry_qualification(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(retry=0.1)
    for _, settings in role_inference_identities(cfg, "developer", PRIMARY):
        _qualify(cfg, settings)
    assert resolve_request_profile(cfg).retry_qualification == mq.QUALIFIED
    changed = _cfg(retry=0.3)
    assert resolve_request_profile(changed).qualification == mq.QUALIFIED
    assert resolve_request_profile(changed).retry_qualification == mq.MISSING


# --- enumeration: qualify, status, doctor -------------------------------------------------------

def test_qualify_covers_the_retry_identity_and_status_reports_it(monkeypatch, tmp_path):
    from kriya.cli import main

    _exact_ollama(monkeypatch)
    cfg = _cfg()
    cfg.llm_chain = []
    monkeypatch.setattr("kriya.cli._model_cfg", lambda ctx: cfg)
    seen = []

    async def fake_run(config, model, **kwargs):
        seen.append(kwargs["settings"])
        runtime = resolve_configured_model_runtime(cfg, PRIMARY)
        return mq.build_record(runtime, [mq.CaseResult(c, mq.PASS) for c in mq.CAPABILITIES],
                               settings=kwargs["settings"])

    monkeypatch.setattr(mq, "run_qualification", fake_run)
    out = tmp_path / "q.json"
    result = CliRunner().invoke(main, ["model", "qualify", "--model", PRIMARY, "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert sorted(s.temperature for s in seen) == [0.1, 0.7]
    assert any(role == "developer (retry)" for r in json.loads(out.read_text())["records"] for role in r["roles"])

    status = CliRunner().invoke(main, ["model", "status", "--json"])
    developer = json.loads(status.output)["developer"]
    assert {(e["identity"], e["status"]) for e in developer} == {("normal", mq.QUALIFIED), ("retry", mq.QUALIFIED)}

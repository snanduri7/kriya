"""PRD-017: capability-aware fallback model transition.

The primary and the fallback below have deliberately opposite profiles
(32K window, native tools, JSON mode, anchored edits, 16384 output tokens
versus 8K, no tools, no JSON mode, whole files only, 2048 output tokens).
The tests drive the real DeveloperAgent and LLMClient and assert on what is
actually sent to the fallback, not on mocked intermediate values.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai.resources.chat.completions import AsyncCompletions

from kriya.agents.agent import DeveloperAgent
from kriya.config import AppConfig
from kriya.config.config import DEFAULT_OUTPUT_TOKENS, FallbackModelConfig, ModelCapabilities
from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import ModelRuntimeFingerprint
from kriya.workflow.attempt import AttemptContext, _run_developer_generation
from kriya.workflow.context_budget import allocation_window, prompt_allocation_window
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.migration import resolve_migration_resolution
from kriya.workflow.model_transition import (
    FALLBACK_MODEL_INCOMPATIBLE,
    fallback_incompatibilities,
    profile_changes,
    resolve_request_profile,
)
from kriya.workflow.operations import CodeOperation
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState

PRIMARY = "primary-coder:30b"
FALLBACK = "fallback-small:8b"


def _fallback(**overrides):
    values = dict(
        model=FALLBACK,
        base_url="http://localhost:11434/v1",
        max_tokens=2048,
        context_window=8192,
        extra_body={"options": {"num_ctx": 8192}},
        capabilities=ModelCapabilities(
            native_tool_calls=False, json_mode=False, reliable_multiline_json=False,
            streaming=False, preferred_edit_protocol="full_file",
        ),
    )
    values.update(overrides)
    return FallbackModelConfig(**values)


def _cfg(**fallback_overrides):
    cfg = AppConfig()
    cfg.llm.model = PRIMARY
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {"options": {"num_ctx": 32768}}
    cfg.llm.max_tokens = 16384
    cfg.llm.capabilities = ModelCapabilities(
        native_tool_calls=True, json_mode=True, reliable_multiline_json=True,
        streaming=True, preferred_edit_protocol="small_native_tools",
    )
    cfg.llm_chain = [_fallback(**fallback_overrides)]
    return cfg


def _response(content):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].message.reasoning = None
    response.choices[0].message.tool_calls = None
    response.choices[0].finish_reason = "stop"
    response.usage = MagicMock(prompt_tokens=40, completion_tokens=20)
    return response


def _exact_ollama(monkeypatch):
    """Probe stand-in: every model is an exact Ollama runtime serving the
    num_ctx its binding sends."""
    def probe(**kw):
        return ModelRuntimeFingerprint(
            alias=kw["model"], endpoint="http://localhost:11434/v1", provider="ollama",
            provider_version="0.34.2", artifact_digest=f"sha256:{kw['model']}", tokenizer_digest="sha256:tok",
            model_context_length=262144, configured_context_window=kw["configured_context"],
            effective_context_window=kw["configured_context"], kriya_protocol=kw["kriya_protocol"],
        )

    monkeypatch.setattr(model_runtime, "probe_model_runtime", probe)


def _qualify(cfg, model, **statuses):
    runtime = model_runtime.resolve_configured_model_runtime(cfg, model)
    results = [mq.CaseResult(c, statuses.get(c, mq.PASS)) for c in mq.CAPABILITIES]
    mq.save_record(mq.build_record(runtime, results))


def _ctx(workspace, cfg, developer):
    run_verifier = AsyncMock()
    spec_compliance = AsyncMock()
    goal = "Fix the service"
    return AttemptContext(
        goal=goal, plan="Step 1", design="Design", workspace_path=workspace, worktree_path=workspace,
        architect_files=["Service.java"], resume_state=None, run_id="prd017", skills_prompt="",
        learned_rag_context="", matched_files=[], related_files=[], ecosystem_invariant_block="",
        resource_lifecycle_block="", verification_contract_block="", recovery_contract_block="",
        required_files_prompt_block="", required_dependencies_prompt_block="",
        expected_files_upfront=["Service.java"], architect_basename_to_path={"Service.java": "Service.java"},
        chain=list(cfg.llm_chain), targeted_max_retries=3, stream_callback=None, approval_callback=None,
        active_skills=[], active_skill_rules_snapshot={}, developer=developer, run_verifier=run_verifier,
        spec_compliance=spec_compliance, skill_engine=MagicMock(), kernel=Kernel(config=cfg), max_retries=4,
        web_lookup_query_callback=None, approve_web_lookup=AsyncMock(return_value=False),
        migration_resolution=resolve_migration_resolution(goal, workspace),
    )


def _fallback_kwargs(cfg, **extra):
    fallback = cfg.llm_chain[0]
    return dict(
        task_description="Fix it", design_context="Design", existing_code_context="",
        model_override=fallback.model, base_url_override=fallback.base_url,
        api_key_override=fallback.api_key, extra_body_override=fallback.extra_body, **extra,
    )


# --- the request profile ---------------------------------------------------------------------

def test_request_profile_is_resolved_for_the_model_actually_called():
    cfg = _cfg()
    primary = resolve_request_profile(cfg)
    fallback = resolve_request_profile(cfg, cfg.llm_chain[0])

    assert (primary.model, fallback.model) == (PRIMARY, FALLBACK)
    assert (primary.context_window, fallback.context_window) == (32768, 8192)
    assert (primary.output_tokens, fallback.output_tokens) == (16384, 2048)
    assert fallback.allocation_window == prompt_allocation_window(8192, 2048)
    assert (primary.native_tool_calls, fallback.native_tool_calls) == (True, False)
    assert (primary.json_mode, fallback.json_mode) == (True, False)
    assert (primary.edit_protocol, fallback.edit_protocol) == ("small_native_tools", "full_file")
    assert fallback.capability_source == "explicit_llm_chain"
    assert primary.digest != fallback.digest

    changes = profile_changes(primary, fallback)
    assert {"model", "context_window", "allocation_window", "output_tokens", "native_tool_calls",
            "json_mode", "edit_protocol", "streaming"} <= set(changes)
    assert changes["output_tokens"] == {"from": 16384, "to": 2048}
    assert profile_changes(primary, primary) == {}


def test_the_shared_output_default_is_the_packaged_llm_max_tokens():
    import os

    import yaml

    import kriya.config as config_package

    with open(os.path.join(os.path.dirname(config_package.__file__), "default_config.yaml")) as handle:
        packaged = yaml.safe_load(handle)
    assert packaged["llm"]["max_tokens"] == DEFAULT_OUTPUT_TOKENS


def test_an_unset_fallback_output_budget_is_the_shared_default_not_the_primary_override():
    cfg = _cfg(max_tokens=None)
    cfg.llm.max_tokens = 32000  # the primary binding's own override
    fallback = cfg.llm_chain[0]
    assert resolve_request_profile(cfg, fallback).output_tokens == DEFAULT_OUTPUT_TOKENS
    assert allocation_window(cfg, fallback) == prompt_allocation_window(8192, DEFAULT_OUTPUT_TOKENS)
    assert LLMClient(cfg)._binding(FALLBACK)["max_tokens"] == DEFAULT_OUTPUT_TOKENS
    assert LLMClient(cfg)._binding(PRIMARY)["max_tokens"] == 32000


def test_an_explicit_fallback_output_budget_wins():
    cfg = _cfg(max_tokens=3000)
    cfg.llm.max_tokens = 32000
    assert resolve_request_profile(cfg, cfg.llm_chain[0]).output_tokens == 3000
    assert LLMClient(cfg)._binding(FALLBACK)["max_tokens"] == 3000


@pytest.mark.asyncio
async def test_a_role_chain_entry_follows_the_same_output_budget_rule():
    from kriya.agents.agent import call_with_escalation
    from kriya.config.config import AgentModelConfig

    cfg = _cfg()
    cfg.llm.max_tokens = 32000
    cfg.agent_llms.reviewer = AgentModelConfig(llm_chain=[_fallback(
        model="role-chain:7b", max_tokens=None, context_window=65536, extra_body={"options": {"num_ctx": 65536}},
    )])
    llm = LLMClient(cfg)
    create = AsyncMock(side_effect=[_response("not json"), _response('{"ok": true}')])
    with patch.object(AsyncCompletions, "create", new=create):
        await call_with_escalation(llm, "s", "u", [None, *cfg.agent_llms.reviewer.llm_chain], json_mode=True,
                                   is_failure=lambda text: not text.strip().startswith("{"))
    sent = [call[1] for call in create.call_args_list]
    assert sent[0]["max_tokens"] == 32000  # the primary binding's own value
    assert (sent[1]["model"], sent[1]["max_tokens"]) == ("role-chain:7b", DEFAULT_OUTPUT_TOKENS)


@pytest.mark.asyncio
async def test_a_call_to_the_fallback_uses_its_own_output_budget():
    cfg = _cfg()
    llm = LLMClient(cfg)
    create = AsyncMock(return_value=_response("ok"))
    with patch.object(AsyncCompletions, "create", new=create):
        await llm.complete_result("s", "u", model_override=FALLBACK, base_url_override=cfg.llm_chain[0].base_url,
                                  extra_body_override=cfg.llm_chain[0].extra_body)
        assert create.call_args[1]["max_tokens"] == 2048
        await llm.complete_result("s", "u")
        assert create.call_args[1]["max_tokens"] == 16384


# --- what the real Developer sends on the hop -------------------------------------------------

@pytest.mark.asyncio
async def test_developer_request_on_the_fallback_matches_the_fallback_runtime(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    developer = DeveloperAgent("developer", LLMClient(cfg))
    create = AsyncMock(side_effect=[
        _response('{"files": ["App.java"]}'),
        _response("class App {}\n"),
    ])
    with patch.object(AsyncCompletions, "create", new=create):
        files = await developer.run_generation(**_fallback_kwargs(cfg))

    assert files and files[0]["filepath"] == "App.java"
    sent = [call[1] for call in create.call_args_list]
    assert all(request["model"] == FALLBACK for request in sent)
    assert all(request["extra_body"]["options"]["num_ctx"] == 8192 for request in sent)
    assert all(request["max_tokens"] == 2048 for request in sent)
    # No JSON mode, no tool schema, no streaming for a model whose profile has none.
    assert all(request.get("response_format") is None for request in sent)
    assert all("tools" not in request for request in sent)
    assert all(not request.get("stream") for request in sent)


@pytest.mark.asyncio
async def test_developer_request_on_the_primary_keeps_the_primary_protocol(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    developer = DeveloperAgent("developer", LLMClient(cfg))
    create = AsyncMock(side_effect=[_response('{"files": ["App.java"]}'), _response("class App {}\n")])
    with patch.object(AsyncCompletions, "create", new=create):
        await developer.run_generation("Fix it", "Design", "")
    file_list = create.call_args_list[0][1]
    assert file_list["model"] == PRIMARY
    assert file_list["response_format"] == {"type": "json_object"}
    assert file_list["max_tokens"] == 16384


@pytest.mark.asyncio
async def test_the_hop_is_recorded_field_by_field(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    developer = DeveloperAgent("developer", LLMClient(cfg))
    ctx = _ctx(str(tmp_path), cfg, developer)
    state = GenerationState()
    create = AsyncMock(side_effect=[_response("class A {}\n"), _response("class A {}\n")])
    with patch.object(AsyncCompletions, "create", new=create):
        state.attempt_number = 1
        await _run_developer_generation(state, ctx, task_description="t", design_context="d",
                                        existing_code_context="", known_target_files=["A.java"])
        state.attempt_number = 2
        await _run_developer_generation(state, ctx, known_target_files=["A.java"], **_fallback_kwargs(cfg))

    transitions = [event for event in state.run_events if event.kind == "model.transition"]
    assert [event.details["initial"] for event in transitions] == [True, False]
    hop = transitions[1].details
    assert hop["fallback"] is True
    assert hop["from"]["model"] == PRIMARY and hop["to"]["model"] == FALLBACK
    assert hop["changes"]["edit_protocol"] == {"from": "small_native_tools", "to": "full_file"}
    assert hop["changes"]["context_window"] == {"from": 32768, "to": 8192}
    assert hop["to"]["qualification"] == mq.MISSING  # recorded, not refused
    assert create.call_args_list[1][1]["extra_body"]["options"]["num_ctx"] == 8192


# --- explicit incompatibility ---------------------------------------------------------------

def _existing_target(tmp_path):
    (tmp_path / "Service.java").write_text("class Service {\n    void run() {}\n}\n")


@pytest.mark.asyncio
async def test_a_whole_file_only_fallback_is_refused_when_the_attempt_may_only_patch(tmp_path, monkeypatch):
    """No complete exact source of Service.java was shown, so the
    completeness gate allows only an anchored patch. A fallback that can
    only return whole files would have its answer rejected after the call;
    it is refused before any request instead."""
    _exact_ollama(monkeypatch)
    _existing_target(tmp_path)
    cfg = _cfg()
    developer = DeveloperAgent("developer", LLMClient(cfg))
    ctx = _ctx(str(tmp_path), cfg, developer)
    state = GenerationState()
    state.attempt_number = 2
    create = AsyncMock(return_value=_response("unused"))
    with patch.object(AsyncCompletions, "create", new=create), pytest.raises(QualityGateFailure) as refused:
        await _run_developer_generation(
            state, ctx, known_target_files=["Service.java"],
            operation_by_file={"Service.java": CodeOperation.REPAIR_WITH_PATCH},
            **_fallback_kwargs(cfg),
        )

    failure = refused.value.failure
    assert failure.type == "fallback_incompatible"
    assert failure.diagnostics["reason_code"] == FALLBACK_MODEL_INCOMPATIBLE
    assert any("may only patch Service.java" in reason for reason in failure.diagnostics["reasons"])
    assert failure.message.startswith(f"{FALLBACK_MODEL_INCOMPATIBLE}:")
    create.assert_not_awaited()
    assert [e.kind for e in state.run_events if e.kind == "model.fallback_incompatible"]

    # Terminal: the retry loop stops instead of sending another attempt to it.
    should_break = await handle_attempt_failure(state, ctx, refused.value)
    assert should_break is True
    assert state.environment_failure.startswith(f"{FALLBACK_MODEL_INCOMPATIBLE}:")
    assert state.last_failure.type == "fallback_incompatible"


@pytest.mark.asyncio
async def test_a_patch_capable_fallback_is_not_refused_for_the_same_attempt(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    _existing_target(tmp_path)
    cfg = _cfg(capabilities=ModelCapabilities(
        native_tool_calls=False, json_mode=False, streaming=False, preferred_edit_protocol="text_markers",
    ))
    developer = DeveloperAgent("developer", LLMClient(cfg))
    developer.run_generation = AsyncMock(return_value=[{"filepath": "Service.java", "content": "x"}])
    ctx = _ctx(str(tmp_path), cfg, developer)
    state = GenerationState()
    await _run_developer_generation(
        state, ctx, known_target_files=["Service.java"],
        operation_by_file={"Service.java": CodeOperation.REPAIR_WITH_PATCH}, **_fallback_kwargs(cfg),
    )
    developer.run_generation.assert_awaited_once()


def test_a_failed_developer_qualification_case_refuses_the_fallback(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    _qualify(cfg, FALLBACK, anchored_edit_protocol=mq.FAIL)
    profile = resolve_request_profile(cfg, cfg.llm_chain[0])
    assert profile.qualification == mq.NOT_QUALIFIED
    assert profile.failed_cases == ("anchored_edit_protocol",)
    reasons = fallback_incompatibilities(cfg, profile)
    assert reasons and "anchored_edit_protocol" in reasons[0]


def test_missing_or_passing_qualification_is_not_a_refusal(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    missing = resolve_request_profile(cfg, cfg.llm_chain[0])
    assert missing.qualification == mq.MISSING
    assert fallback_incompatibilities(cfg, missing) == []
    _qualify(cfg, FALLBACK)
    qualified = resolve_request_profile(cfg, cfg.llm_chain[0])
    assert qualified.qualification == mq.QUALIFIED
    assert fallback_incompatibilities(cfg, qualified) == []


def test_the_production_profile_requires_a_qualified_fallback(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    cfg.runtime_profile = "production"
    profile = resolve_request_profile(cfg, cfg.llm_chain[0])
    assert any("production runtime profile" in r for r in fallback_incompatibilities(cfg, profile))
    _qualify(cfg, FALLBACK)
    assert fallback_incompatibilities(cfg, resolve_request_profile(cfg, cfg.llm_chain[0])) == []


def test_a_fallback_with_no_prompt_room_is_refused():
    # The output reserve is capped at half the window, so only a window too
    # small for framing and the safety margin leaves no prompt room.
    cfg = _cfg(context_window=512, extra_body={"options": {"num_ctx": 512}})
    profile = resolve_request_profile(cfg, cfg.llm_chain[0])
    assert profile.allocation_window == 0
    assert any("leaves no room" in r for r in fallback_incompatibilities(cfg, profile))


def test_the_retry_progress_identity_changes_with_the_request_profile():
    """Two bindings differing only in window/protocol are different request
    profiles, so a hop between them is never a no-progress repeat."""
    cfg = _cfg()
    first = resolve_request_profile(cfg, cfg.llm_chain[0])
    widened = resolve_request_profile(cfg, replace_binding(cfg.llm_chain[0], context_window=16384,
                                                          extra_body={"options": {"num_ctx": 16384}}))
    assert first.model == widened.model and first.digest != widened.digest


def replace_binding(binding, **changes):
    return binding.model_copy(update=changes)


def test_the_change_record_is_json_serializable():
    import json

    cfg = _cfg()
    changes = profile_changes(resolve_request_profile(cfg), resolve_request_profile(cfg, cfg.llm_chain[0]))
    json.dumps(changes)
    json.dumps(resolve_request_profile(cfg).to_dict())


# --- skipping incompatible fallbacks in configured order ----------------------------------------

SECOND = "fallback-second:14b"


def _two_fallback_cfg(first=None, second=None):
    cfg = _cfg(**(first or {}))
    cfg.llm_chain.append(_fallback(
        model=SECOND, context_window=16384, extra_body={"options": {"num_ctx": 16384}},
        capabilities=ModelCapabilities(native_tool_calls=False, json_mode=False, streaming=False,
                                       preferred_edit_protocol="text_markers"),
        **(second or {}),
    ))
    return cfg


def test_an_incompatible_first_fallback_is_skipped_for_the_next_configured_one(tmp_path, monkeypatch):
    from kriya.workflow.attempt import _select_developer_fallback
    from kriya.workflow.attribution import resolve_fallback_model

    _exact_ollama(monkeypatch)
    cfg = _two_fallback_cfg()
    _qualify(cfg, FALLBACK, full_file_raw_content=mq.FAIL)
    ctx = _ctx(str(tmp_path), cfg, DeveloperAgent("developer", LLMClient(cfg)))
    state = GenerationState()
    state.attempt_number = 2

    selected = _select_developer_fallback(state, ctx, 1)

    assert selected.model == SECOND
    assert list(state.incompatible_fallbacks) == [FALLBACK]
    (event,) = [e for e in state.run_events if e.kind == "model.fallback_selection"]
    assert (event.details["requested"], event.details["selected"]) == (FALLBACK, SECOND)
    (rejection,) = event.details["rejected"]
    assert rejection["model"] == FALLBACK and "full_file_raw_content" in rejection["reasons"][0]
    # The configured order is kept: the ladder never goes back to the skipped one,
    # and triage (which rides the generation model) follows the same skip.
    assert _select_developer_fallback(state, ctx, 2).model == SECOND  # its own turn: nothing skipped
    assert resolve_fallback_model(1, ctx.chain, state.incompatible_fallbacks).model == SECOND
    assert len([e for e in state.run_events if e.kind == "model.fallback_selection"]) == 1
    # A later escalation that would land on the skipped one again records the skip again.
    assert _select_developer_fallback(state, ctx, 1).model == SECOND
    assert len([e for e in state.run_events if e.kind == "model.fallback_selection"]) == 2


def test_a_compatible_fallback_is_never_reordered(tmp_path, monkeypatch):
    from kriya.workflow.attempt import _select_developer_fallback

    _exact_ollama(monkeypatch)
    cfg = _two_fallback_cfg()
    _qualify(cfg, SECOND)  # a better-evidenced second fallback does not jump the queue
    ctx = _ctx(str(tmp_path), cfg, DeveloperAgent("developer", LLMClient(cfg)))
    state = GenerationState()
    assert _select_developer_fallback(state, ctx, 1).model == FALLBACK
    assert not [e for e in state.run_events if e.kind == "model.fallback_selection"]


def test_every_fallback_incompatible_is_a_typed_failure_with_every_reason(tmp_path, monkeypatch):
    from kriya.workflow.attempt import _select_developer_fallback

    _exact_ollama(monkeypatch)
    cfg = _two_fallback_cfg()
    cfg.runtime_profile = "production"
    _qualify(cfg, FALLBACK, anchored_edit_protocol=mq.FAIL)
    ctx = _ctx(str(tmp_path), cfg, DeveloperAgent("developer", LLMClient(cfg)))
    state = GenerationState()
    state.attempt_number = 2
    with pytest.raises(QualityGateFailure) as refused:
        _select_developer_fallback(state, ctx, 1)
    failure = refused.value.failure
    assert failure.type == "fallback_incompatible"
    assert failure.diagnostics["reason_code"] == FALLBACK_MODEL_INCOMPATIBLE
    rejected = {item["model"]: item["reasons"] for item in failure.diagnostics["rejected"]}
    assert set(rejected) == {FALLBACK, SECOND}
    assert any("anchored_edit_protocol" in reason for reason in rejected[FALLBACK])
    assert any("production runtime profile" in reason for reason in rejected[SECOND])
    assert FALLBACK in failure.message and SECOND in failure.message


@pytest.mark.asyncio
async def test_a_required_patch_moves_the_call_to_the_next_patch_capable_fallback(tmp_path, monkeypatch):
    """The first fallback returns whole files only and this attempt may only
    patch Service.java: the call goes to the second fallback, and the first
    receives no request at all."""
    _exact_ollama(monkeypatch)
    _existing_target(tmp_path)
    cfg = _two_fallback_cfg()
    developer = DeveloperAgent("developer", LLMClient(cfg))
    ctx = _ctx(str(tmp_path), cfg, developer)
    state = GenerationState()
    state.attempt_number = 2
    state.model_hops = [FALLBACK]
    create = AsyncMock(return_value=_response(
        "FILE: Service.java\nSEARCH:\n    void run() {}\nREPLACE:\n    void run() { }\n"
    ))
    with patch.object(AsyncCompletions, "create", new=create):
        await _run_developer_generation(
            state, ctx, known_target_files=["Service.java"],
            operation_by_file={"Service.java": CodeOperation.REPAIR_WITH_PATCH},
            **_fallback_kwargs(cfg),
        )

    sent = [call[1] for call in create.call_args_list]
    assert sent and all(request["model"] == SECOND for request in sent)
    assert all(request["extra_body"]["options"]["num_ctx"] == 16384 for request in sent)
    (event,) = [e for e in state.run_events if e.kind == "model.fallback_selection"]
    assert event.details["phase"] == "call"
    assert (event.details["requested"], event.details["selected"]) == (FALLBACK, SECOND)
    assert "may only patch Service.java" in event.details["rejected"][0]["reasons"][0]
    assert state.last_model_override == SECOND and state.model_hops == [SECOND]
    assert state.last_developer_request_profile.model == SECOND
    assert FALLBACK not in state.incompatible_fallbacks  # attempt-specific, not a run-wide verdict


@pytest.mark.asyncio
async def test_the_run_escalates_past_an_incompatible_fallback_without_calling_it(tmp_path, monkeypatch):
    """End to end through WorkflowEngine: fallback-1 failed a Developer
    qualification case, so the escalation goes to fallback-2 and
    fallback-1 is sent nothing; the skip is in the run's trace."""
    import json
    import sqlite3

    from kriya.core.state_paths import trace_db_path
    from kriya.workflow.workflow import WorkflowEngine

    _exact_ollama(monkeypatch)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model="fallback-1"), FallbackModelConfig(model="fallback-2")]
    cfg.paths.skills = str(tmp_path / "skills")
    _qualify(cfg, "fallback-1", anchored_edit_protocol=mq.FAIL)
    llm = LLMClient(cfg)
    model_overrides = []

    async def mock_complete(*args, **kwargs):
        model_overrides.append(kwargs.get("model_override"))
        n = len(model_overrides)
        if n == 1:
            return "Step 1: Write code"
        if n == 2:
            return "Design: Write math.py"
        if n == 3:
            return "def add(a,b)\n    return a+b"
        if n in (4, 5, 6, 7):
            return "FILE CONTENT:\ndef add(a,b)\n    return a+b"
        if n == 8:
            return "FILE CONTENT:\ndef add(a,b):\n    return a+b"
        return "Review: Approved"

    llm.complete = mock_complete
    workspace = tmp_path / "ws"
    workspace.mkdir()
    res = await WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(
        goal="Create math library with fallback chain", workspace_path=str(workspace),
    )

    assert res["quality_gates_passed"] is True
    assert "fallback-1" not in model_overrides
    assert model_overrides[6] == "fallback-2"  # one-shot fallback-targeted fix
    assert model_overrides[7] == "fallback-2"  # full-set escalation
    with sqlite3.connect(trace_db_path(cfg)) as db:
        (events_json,) = db.execute("SELECT run_events FROM runs").fetchone()
    selections = [e for e in json.loads(events_json) if e["kind"] == "model.fallback_selection"]
    assert selections and selections[0]["details"]["selected"] == "fallback-2"
    # Chosen at escalation, before the prompt was built for its window.
    assert {e["details"]["phase"] for e in selections} == {"escalation"}
    assert selections[0]["details"]["rejected"][0]["model"] == "fallback-1"


@pytest.mark.asyncio
async def test_triage_rides_the_same_fallback_the_generation_escalated_to():
    from kriya.workflow.attribution import _tier_triage
    from kriya.workflow.failure import Failure

    cfg = _two_fallback_cfg()
    llm = MagicMock()
    llm.complete = AsyncMock(return_value="{}")
    failure = Failure(type="test_failure", message="boom", raw_output="boom", source="tests", attempt=2)
    await _tier_triage(failure, ["A.java", "B.java"], 1, cfg.llm_chain, llm, lambda path: "class X {}",
                       skip_fallbacks=(FALLBACK,))
    assert llm.complete.call_args[1]["model_override"] == SECOND

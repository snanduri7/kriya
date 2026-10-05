"""LR-R1-P1: qualification-derived model capabilities and Option-(b)
fallback routing (handover/LR_R1_P1_IMPLEMENTATION.md).

Derivation: a capability comes only from the exact passing case(s) of the
binding's own current Developer qualification - never from QUALIFIED alone,
never inherited from a base tag; explicit operator declarations win; the
runtime fingerprint keeps the declared profile.

Routing: one resolver (model_transition.resolve_fallback_compatibility)
classifies the remaining fallbacks, in resolve_fallback_model's order,
against the next attempt's deterministic requirement. A target is patch-only
for a candidate only when whole-file representation is impossible in that
candidate's request (size lower bound). A fallback proven unable to serve a
patch-only attempt is unavailable for that transition: the primary full-set
route continues; FALLBACK_MODEL_INCOMPATIBLE is terminal only when no other
route remains. Run-wide incompatibility keeps PRD-017's semantics.
"""
import ast
import math
import os
from types import SimpleNamespace

import pytest
from test_prd017_fallback_transition import _ctx, _exact_ollama, _qualify

from kriya.agents.agent import DeveloperAgent
from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig, ModelCapabilities
from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core.llm import LLMClient
from kriya.core.model_capabilities import (
    CAPABILITY_DERIVATION_VERSION,
    KNOWN_MODEL_PROFILES,
    capabilities_for_model,
    declared_capability_profile,
    generation_protocol_for_model,
    resolve_model_capability_profile,
)
from kriya.core.provider_contract import DEFAULT_BYTES_PER_TOKEN_CEILING
from kriya.workflow.context_budget import request_capacity
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.model_transition import (
    CANDIDATE_COMPATIBLE,
    CANDIDATE_NOT_EVALUATED,
    CANDIDATE_PATCH_INCOMPATIBLE,
    CANDIDATE_RUN_WIDE_INCOMPATIBLE,
    FALLBACK_MODEL_INCOMPATIBLE,
    PATCH_ONLY_PROVEN,
    PATCH_REQUIREMENT_NOT_DETERMINED,
    fallback_routing_for_context,
    resolve_fallback_compatibility,
    resolve_request_profile,
    whole_file_minimum_tokens,
)
from kriya.workflow.operations import CodeOperation
from kriya.workflow.recovery_coordinator import conclude_attempt_failure
from kriya.workflow.retry_policy import RetryAction
from kriya.workflow.state import GenerationState

PRIMARY = "primary-coder:30b"
PINNED = "qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a"   # a derived (pinned) tag of a known base tag
WHOLE = "whole-only:8b"
PATCH = "patch-capable:14b"
TOOL_CASES = ("native_tool_calls", "multiple_tool_calls", "tool_argument_integrity")


def _binding(model, *, window=8192, capabilities=None, max_tokens=None, temperature=None):
    values = dict(model=model, base_url="http://localhost:11434/v1", context_window=window,
                  extra_body={"options": {"num_ctx": window}})
    if temperature is not None:
        values["temperature"] = temperature
    if capabilities is not None:
        values["capabilities"] = capabilities
    if max_tokens is not None:
        values["max_tokens"] = max_tokens
    return FallbackModelConfig(**values)


def _cfg(*chain):
    cfg = AppConfig()
    cfg.llm.model = PRIMARY
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {"options": {"num_ctx": 32768}}
    cfg.llm.capabilities = ModelCapabilities(native_tool_calls=True, json_mode=True, reliable_multiline_json=True,
                                             streaming=True, preferred_edit_protocol="small_native_tools")
    cfg.llm_chain = list(chain)
    return cfg


WHOLE_ONLY = ModelCapabilities(native_tool_calls=False, json_mode=False, reliable_multiline_json=False,
                               streaming=False, preferred_edit_protocol="full_file")
PATCH_CAPS = ModelCapabilities(native_tool_calls=False, json_mode=False, reliable_multiline_json=False,
                               streaming=False, preferred_edit_protocol="text_markers")


# ===================================================================================================
# Derivation
# ===================================================================================================


@pytest.mark.parametrize("status, patch", [(mq.PASS, True), (mq.FAIL, False), (mq.UNAVAILABLE, False)])
def test_the_patch_capability_comes_only_from_a_passing_anchored_edit_case(monkeypatch, status, patch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    _qualify(cfg, PINNED, anchored_edit_protocol=status)
    profile = resolve_model_capability_profile(cfg, PINNED)
    assert profile.source == "qualification_derived"
    assert (profile.capabilities.preferred_edit_protocol != "full_file") is patch
    assert profile.evidence["cases"]["anchored_edit_protocol"] == status
    assert ("patch_edit" in profile.evidence["proven"]) is patch


def test_a_patch_case_that_was_never_run_proves_nothing(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    _qualify(cfg, PINNED)
    runtime = model_runtime.resolve_configured_model_runtime(cfg, PINNED)
    from kriya.core.inference_settings import role_inference_settings

    settings = role_inference_settings(cfg, "developer", PINNED)
    record = mq.load_record(runtime.digest, settings)
    record["cases"] = [c for c in record["cases"] if c["capability"] != "anchored_edit_protocol"]
    mq.save_record(record)
    profile = resolve_model_capability_profile(cfg, PINNED)
    assert profile.source == "qualification_derived"
    assert profile.evidence["cases"]["anchored_edit_protocol"] == "NOT_RUN"
    assert profile.capabilities.preferred_edit_protocol == "full_file"


def test_whole_file_and_patch_are_derived_independently(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    no_tools = {c: mq.UNAVAILABLE for c in TOOL_CASES}
    _qualify(cfg, PINNED, full_file_raw_content=mq.FAIL, **no_tools)
    profile = resolve_model_capability_profile(cfg, PINNED)
    assert "patch_edit" in profile.evidence["proven"] and "whole_file" not in profile.evidence["proven"]
    assert profile.capabilities.preferred_edit_protocol == "text_markers"
    _qualify(cfg, PINNED, anchored_edit_protocol=mq.FAIL, **no_tools)
    profile = resolve_model_capability_profile(cfg, PINNED)
    assert "whole_file" in profile.evidence["proven"] and "patch_edit" not in profile.evidence["proven"]
    assert profile.capabilities.preferred_edit_protocol == "full_file"


@pytest.mark.parametrize("failing", [None, *TOOL_CASES])
def test_native_tools_need_every_tool_case_and_a_patch_never_implies_them(monkeypatch, failing):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    _qualify(cfg, PINNED, **({failing: mq.UNAVAILABLE} if failing else {}))
    caps = resolve_model_capability_profile(cfg, PINNED).capabilities
    assert caps.native_tool_calls is (failing is None)
    assert caps.preferred_edit_protocol == ("small_native_tools" if failing is None else "text_markers")


def test_json_multiline_and_streaming_each_follow_their_own_case(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    _qualify(cfg, PINNED, structured_json=mq.FAIL, multiline_json=mq.PASS)
    caps = resolve_model_capability_profile(cfg, PINNED).capabilities
    assert (caps.json_mode, caps.reliable_multiline_json) == (False, True)


def test_no_record_keeps_the_declared_profile(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    profile = resolve_model_capability_profile(cfg, PINNED)
    assert profile.source == "unverified_conservative_default" and profile.evidence is None


def test_a_stale_record_proves_nothing(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    _qualify(cfg, PINNED)
    cfg.model_qualification.measurement.bytes_per_token_margin = 0.8   # qualification policy drift
    assert resolve_model_capability_profile(cfg, PINNED).source == "unverified_conservative_default"


def test_a_record_of_another_runtime_identity_proves_nothing(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    _qualify(cfg, PINNED)
    cfg.llm_chain = [_binding(PINNED, window=16384)]   # another served window: another runtime digest
    assert resolve_model_capability_profile(cfg, PINNED).source == "unverified_conservative_default"


def test_a_record_of_other_inference_settings_proves_nothing(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    _qualify(cfg, PINNED)
    cfg.llm_chain = [_binding(PINNED, temperature=0.11)]
    assert resolve_model_capability_profile(cfg, PINNED).source == "unverified_conservative_default"


def test_an_explicit_declaration_wins_over_derivation(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED, capabilities=WHOLE_ONLY))
    _qualify(cfg, PINNED)
    profile = resolve_model_capability_profile(cfg, PINNED)
    assert profile.source == "explicit_llm_chain" and profile.capabilities.preferred_edit_protocol == "full_file"


def test_qualified_alone_grants_no_capability(monkeypatch):
    """QUALIFIED for the Developer (every required case PASS) while
    structured_json and the tool cases are UNAVAILABLE: no JSON mode, no
    native tools."""
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    _qualify(cfg, PINNED, structured_json=mq.UNAVAILABLE, **{c: mq.UNAVAILABLE for c in TOOL_CASES})
    runtime = model_runtime.resolve_configured_model_runtime(cfg, PINNED)
    from kriya.core.inference_settings import role_inference_settings

    assessment = mq.assess(runtime, mq.required_capabilities(cfg, "developer", PINNED),
                           settings=role_inference_settings(cfg, "developer", PINNED),
                           policy_digest=mq.policy_digest_for(cfg))
    assert assessment.status == mq.QUALIFIED
    caps = capabilities_for_model(cfg, PINNED)
    assert caps.json_mode is False and caps.native_tool_calls is False


def test_a_derived_tag_inherits_nothing_from_its_base_tag(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    base = KNOWN_MODEL_PROFILES["qwen3.6:35b-a3b-q4_k_m"]
    assert base.native_tool_calls is True
    caps = capabilities_for_model(cfg, PINNED)   # no record of its own
    assert caps.native_tool_calls is False and caps.preferred_edit_protocol == "full_file"
    _qualify(cfg, PINNED, **{c: mq.UNAVAILABLE for c in TOOL_CASES})
    assert capabilities_for_model(cfg, PINNED).native_tool_calls is False


def test_the_runtime_fingerprint_keeps_the_declared_profile(monkeypatch):
    """The record is found by the fingerprint, so derivation never feeds
    back into it: the protocol identity before and after a derivable record
    exists is the same (every existing fingerprint unchanged)."""
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    before = model_runtime.kriya_protocol_identity(cfg, PINNED)
    _qualify(cfg, PINNED)
    assert resolve_model_capability_profile(cfg, PINNED).source == "qualification_derived"
    assert model_runtime.kriya_protocol_identity(cfg, PINNED) == before
    assert declared_capability_profile(cfg, PINNED).source == "unverified_conservative_default"


def test_every_edit_protocol_consumer_reads_the_same_resolved_profile(tmp_path, monkeypatch):
    from kriya.workflow.attempt import _lower_output_protocol_retry

    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    _qualify(cfg, PINNED, **{c: mq.UNAVAILABLE for c in TOOL_CASES})
    resolved = resolve_model_capability_profile(cfg, PINNED).capabilities.preferred_edit_protocol
    assert resolved == "text_markers"
    # PRD-017 request profile (fallback incompatibility, transitions)
    assert resolve_request_profile(cfg, cfg.llm_chain[0]).edit_protocol == resolved
    # The Developer agent's generation protocol (patch -> whole-file conversion)
    assert generation_protocol_for_model(cfg, PINNED).preferred_edit_protocol == resolved
    # PRD-016 output-budget patch fallback
    (tmp_path / "Service.java").write_text("class Service {}\n")
    ctx = _ctx(str(tmp_path), cfg, DeveloperAgent("developer", LLMClient(cfg)))
    retried = _lower_output_protocol_retry(GenerationState(), ctx, {"operation_by_file": {}},
                                           SimpleNamespace(filepath="Service.java", anchored_edit=False), PINNED)
    assert retried is not None and retried["operation_by_file"]["Service.java"] == CodeOperation.REPAIR_WITH_PATCH


def test_only_the_fingerprint_reads_the_declared_profile():
    """Tripwire: every capability consumer resolves through
    resolve_model_capability_profile / capabilities_for_model /
    generation_protocol_for_model; declared_capability_profile is read only
    by the runtime fingerprint's protocol identity."""
    root = os.path.join(os.path.dirname(__file__), "..", "kriya")
    users = []
    for directory, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(directory, name)
                source = open(path, encoding="utf-8").read()
                if "declared_capability_profile" in source:
                    users.append(os.path.relpath(path, root))
    assert sorted(users) == ["core/model_capabilities.py", "core/model_runtime.py"]
    tree = ast.parse(open(os.path.join(root, "core", "model_runtime.py"), encoding="utf-8").read())
    calls = [node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "declared_capability_profile"]
    assert calls == ["declared_capability_profile"]


def test_derivation_evidence_names_its_mapping_and_record(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PINNED))
    _qualify(cfg, PINNED)
    evidence = resolve_model_capability_profile(cfg, PINNED).evidence
    runtime = model_runtime.resolve_configured_model_runtime(cfg, PINNED)
    assert evidence["mapping_version"] == CAPABILITY_DERIVATION_VERSION
    assert evidence["runtime_digest"] == runtime.digest and evidence["role"] == "developer"
    assert evidence["policy_digest"] == mq.policy_digest_for(cfg)
    assert set(evidence["cases"]) >= {"anchored_edit_protocol", "full_file_raw_content", *TOOL_CASES}


# ===================================================================================================
# The size lower bound (patch-only PROVEN vs NOT_DETERMINED)
# ===================================================================================================


def _room(cfg, binding):
    return request_capacity(cfg, binding).tokens


def _write(tmp_path, name, size):
    (tmp_path / name).write_bytes(b"x" * (size - 1) + b"\n")
    return name


def _routing(cfg, tmp_path, *names, rejections=None):
    state = GenerationState()
    state.last_implicated_files = list(names)
    state.incompatible_fallbacks = dict(rejections or {})
    ctx = SimpleNamespace(chain=list(cfg.llm_chain), kernel=SimpleNamespace(config=cfg), worktree_path=str(tmp_path))
    return fallback_routing_for_context(state, ctx)


def test_a_file_too_large_for_the_fallbacks_request_is_patch_only_proven(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    big = _write(tmp_path, "Big.java", (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))
    routing = _routing(cfg, tmp_path, big)
    assert routing.requirement == PATCH_ONLY_PROVEN
    [candidate] = routing.candidates
    assert candidate.status == CANDIDATE_PATCH_INCOMPATIBLE and candidate.patch_only_files == (big,)
    assert routing.available is False and routing.patch_excluded == (WHOLE,)


def test_a_file_that_could_fit_is_not_determined_and_nothing_is_pre_rejected(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    small = _write(tmp_path, "Small.java", 4000)
    routing = _routing(cfg, tmp_path, small)
    assert routing.requirement == PATCH_REQUIREMENT_NOT_DETERMINED
    assert [c.status for c in routing.candidates] == [CANDIDATE_NOT_EVALUATED]
    assert routing.available is True and routing.selected == WHOLE


def test_an_earlier_patch_required_attempt_is_not_proof(tmp_path, monkeypatch):
    """History says the last call on Small.java could only patch it and the
    fallback was refused at that call; the next request could still show it
    whole, so routing does not pre-reject."""
    from kriya.workflow.run_events import EventAuthority, RunEvent

    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    small = _write(tmp_path, "Small.java", 4000)
    state = GenerationState()
    state.last_implicated_files = [small]
    state.record_event(RunEvent(kind="model.fallback_selection", attempt=1, source="test",
                                authority=EventAuthority.ADVISORY, message="refused at the call",
                                details={"phase": "call", "requested": WHOLE, "selected": None,
                                         "rejected": [{"model": WHOLE, "reasons": ["may only patch Small.java"]}]}))
    ctx = SimpleNamespace(chain=list(cfg.llm_chain), kernel=SimpleNamespace(config=cfg), worktree_path=str(tmp_path))
    routing = fallback_routing_for_context(state, ctx)
    assert routing.requirement == PATCH_REQUIREMENT_NOT_DETERMINED and routing.available is True


def test_the_bound_equal_to_the_allocation_is_not_proof(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    room = _room(cfg, cfg.llm_chain[0])
    ceiling = int(DEFAULT_BYTES_PER_TOKEN_CEILING)
    exact = _write(tmp_path, "Exact.java", room * ceiling)
    assert whole_file_minimum_tokens(room * ceiling, ceiling) == room
    assert _routing(cfg, tmp_path, exact).requirement == PATCH_REQUIREMENT_NOT_DETERMINED
    over = _write(tmp_path, "Over.java", room * ceiling + 1)
    assert whole_file_minimum_tokens(room * ceiling + 1, ceiling) == room + 1
    assert _routing(cfg, tmp_path, over).requirement == PATCH_ONLY_PROVEN


def test_the_same_file_can_be_patch_only_for_one_fallback_and_not_another(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY), _binding("whole-large:30b", window=32768,
                                                                  capabilities=WHOLE_ONLY))
    size = (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING)
    assert math.ceil(size / DEFAULT_BYTES_PER_TOKEN_CEILING) <= _room(cfg, cfg.llm_chain[1])
    big = _write(tmp_path, "Big.java", size)
    routing = _routing(cfg, tmp_path, big)
    assert [(c.model, c.status) for c in routing.candidates] == [
        (WHOLE, CANDIDATE_PATCH_INCOMPATIBLE), ("whole-large:30b", CANDIDATE_COMPATIBLE)]
    assert routing.available is True and routing.selected == "whole-large:30b"


def test_the_bound_uses_the_candidates_real_allocation_not_its_nominal_window(tmp_path, monkeypatch):
    """Two fallbacks with the same 16K window; one reserves far more output.
    A file that fits beside the small reserve cannot fit beside the large
    one - patch-only for that candidate only."""
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding("small-reserve:8b", window=16384, max_tokens=1024, capabilities=WHOLE_ONLY),
               _binding("large-reserve:8b", window=16384, max_tokens=8192, capabilities=WHOLE_ONLY))
    small_room, large_room = (_room(cfg, b) for b in cfg.llm_chain)
    assert large_room < small_room < 16384
    size = (large_room + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING)
    assert math.ceil(size / DEFAULT_BYTES_PER_TOKEN_CEILING) <= small_room
    routing = _routing(cfg, tmp_path, _write(tmp_path, "Mid.java", size))
    assert {c.model: c.status for c in routing.candidates} == {
        "small-reserve:8b": CANDIDATE_COMPATIBLE, "large-reserve:8b": CANDIDATE_PATCH_INCOMPATIBLE}


@pytest.mark.asyncio
async def test_the_call_time_d1_check_stays_authoritative_when_not_proven(tmp_path, monkeypatch):
    """Routing could not prove patch-only (the file is small) and kept the
    whole-file-only fallback; the call-time check still refuses it for an
    attempt whose prompt did not show the file whole."""
    from kriya.workflow.attempt import _run_developer_generation

    _exact_ollama(monkeypatch)
    (tmp_path / "Service.java").write_text("class Service {\n    void run() {}\n}\n")
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    assert _routing(cfg, tmp_path, "Service.java").available is True
    ctx = _ctx(str(tmp_path), cfg, DeveloperAgent("developer", LLMClient(cfg)))
    state = GenerationState()
    state.attempt_number = 2
    fallback = cfg.llm_chain[0]
    with pytest.raises(QualityGateFailure) as refused:
        await _run_developer_generation(
            state, ctx, known_target_files=["Service.java"],
            operation_by_file={"Service.java": CodeOperation.REPAIR_WITH_PATCH},
            task_description="Fix it", design_context="Design", existing_code_context="",
            model_override=fallback.model, base_url_override=fallback.base_url,
            api_key_override=fallback.api_key, extra_body_override=fallback.extra_body)
    assert refused.value.failure.diagnostics["reason_code"] == FALLBACK_MODEL_INCOMPATIBLE


def test_a_run_wide_rejection_keeps_prd017_semantics_in_routing(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    big = _write(tmp_path, "Big.java", (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))
    routing = _routing(cfg, tmp_path, big, rejections={WHOLE: ["qualification failed"]})
    [candidate] = routing.candidates
    assert candidate.status == CANDIDATE_RUN_WIDE_INCOMPATIBLE
    assert routing.available is True and routing.patch_excluded == ()   # routing unchanged: PRD-017 decides


# ===================================================================================================
# Routing decisions (Option b)
# ===================================================================================================


def _decision_ctx(cfg, tmp_path, *, max_retries=4, targeted_max_retries=3):
    return SimpleNamespace(chain=list(cfg.llm_chain), kernel=SimpleNamespace(config=cfg),
                           worktree_path=str(tmp_path), workspace_path=str(tmp_path),
                           max_retries=max_retries, targeted_max_retries=targeted_max_retries)


def _failed_state(target, *, retry_count, targeted_closed=True):
    state = GenerationState()
    state.attempt_number = 5
    state.budgets.retry_count = retry_count
    state.budgets.targeted_retry_count = 3 if targeted_closed else 0
    state.last_implicated_files = [target]
    return state


@pytest.fixture
def no_sandbox_cleanup(monkeypatch):
    import kriya.workflow.recovery_coordinator as rc

    monkeypatch.setattr(rc, "_capture_final_contents_and_remove_sandbox", lambda *a, **k: None)


def test_case1_incompatible_fallback_with_primary_budget_routes_full_set(tmp_path, monkeypatch,
                                                                          no_sandbox_cleanup):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    big = _write(tmp_path, "Big.java", (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))
    state = _failed_state(big, retry_count=1)
    decision = conclude_attempt_failure(state, _decision_ctx(cfg, tmp_path))
    assert decision.action is RetryAction.FULL_SET and not decision.stop_loop
    [routing] = [e.details for e in state.run_events if e.kind == "model.fallback_routing"]
    assert routing["resulting_strategy"] == "full_set" and routing["other_route_remained"] is True
    assert state.environment_failure is None


def test_case2_the_second_compatible_fallback_is_selected_in_order(tmp_path, monkeypatch, no_sandbox_cleanup):
    from kriya.workflow.attempt import _select_developer_fallback

    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY), _binding(PATCH, capabilities=PATCH_CAPS))
    big = _write(tmp_path, "Big.java", (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))
    state = _failed_state(big, retry_count=1)
    decision = conclude_attempt_failure(state, _decision_ctx(cfg, tmp_path))
    assert decision.action is RetryAction.FALLBACK_TARGETED
    [routing] = [e.details for e in state.run_events if e.kind == "model.fallback_routing"]
    assert routing["selected"] == PATCH and routing["resulting_strategy"] == "fallback_targeted"
    assert [c["model"] for c in routing["candidates"]] == [WHOLE, PATCH]   # configured order
    ctx = _ctx(str(tmp_path), cfg, DeveloperAgent("developer", LLMClient(cfg)))
    assert _select_developer_fallback(state, ctx, 1, targets=[big]).model == PATCH
    assert WHOLE not in state.incompatible_fallbacks   # attempt-specific, never a run-wide verdict


def test_case3_all_incompatible_and_primary_exhausted_is_typed_terminal(tmp_path, monkeypatch,
                                                                         no_sandbox_cleanup):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    big = _write(tmp_path, "Big.java", (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))
    state = _failed_state(big, retry_count=4)
    llm_calls = []
    monkeypatch.setattr(LLMClient, "complete_result", lambda *a, **k: llm_calls.append(a))
    decision = conclude_attempt_failure(state, _decision_ctx(cfg, tmp_path))
    assert decision.action is RetryAction.STOP_ENVIRONMENT and decision.stop_loop
    assert state.environment_failure.startswith(f"{FALLBACK_MODEL_INCOMPATIBLE}:")
    [routing] = [e.details for e in state.run_events if e.kind == "model.fallback_routing"]
    assert routing["resulting_strategy"] == FALLBACK_MODEL_INCOMPATIBLE and routing["other_route_remained"] is False
    [terminal] = [e.details for e in state.run_events if e.kind == "model.fallback_incompatible"]
    assert terminal["phase"] == "routing" and terminal["rejected"][0]["status"] == CANDIDATE_PATCH_INCOMPATIBLE
    assert llm_calls == []


def test_case3_a_valid_primary_route_is_never_terminal(tmp_path, monkeypatch, no_sandbox_cleanup):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    big = _write(tmp_path, "Big.java", (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))
    for retry_count in (1, 2, 3):
        state = _failed_state(big, retry_count=retry_count)
        assert conclude_attempt_failure(state, _decision_ctx(cfg, tmp_path)).action is RetryAction.FULL_SET
        assert state.environment_failure is None


def test_case4_an_unknown_requirement_is_not_rejected_early(tmp_path, monkeypatch, no_sandbox_cleanup):
    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    small = _write(tmp_path, "Small.java", 4000)
    state = _failed_state(small, retry_count=1)
    decision = conclude_attempt_failure(state, _decision_ctx(cfg, tmp_path))
    assert decision.action is RetryAction.FALLBACK_TARGETED   # unchanged ordinary routing
    assert not [e for e in state.run_events if e.kind == "model.fallback_routing"]


def test_case5_the_ordinary_fallback_targeted_rule_reads_the_resolver(tmp_path, monkeypatch, no_sandbox_cleanup):
    """recovery_coordinator -> decide_for_state, no strategy transition
    involved (fallback_targeted_requested is False): the ordinary
    FALLBACK_TARGETED branch must not pick a deterministically incompatible
    fallback; with a patch-capable one it still does."""
    _exact_ollama(monkeypatch)
    big_size = None
    for caps, expected in ((WHOLE_ONLY, RetryAction.FULL_SET), (PATCH_CAPS, RetryAction.FALLBACK_TARGETED)):
        cfg = _cfg(_binding(WHOLE, capabilities=caps))
        big_size = (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING)
        big = _write(tmp_path, "Big.java", big_size)
        state = _failed_state(big, retry_count=1)
        assert state.budgets.fallback_targeted_requested is False
        assert conclude_attempt_failure(state, _decision_ctx(cfg, tmp_path)).action is expected


def test_case6_the_strategy_transition_reads_the_same_resolver(tmp_path, monkeypatch):
    """force_strategy_transition's has_fallback_model is the resolver's
    availability (retry_strategy); the end-to-end proof is the P1
    reproducer. Here: the same state gives the same answer to both."""
    from kriya.workflow.retry_policy import force_strategy_transition

    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    big = _write(tmp_path, "Big.java", (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))
    state = _failed_state(big, retry_count=1, targeted_closed=False)
    routing = fallback_routing_for_context(state, _decision_ctx(cfg, tmp_path))
    assert force_strategy_transition(state.budgets, consecutive_no_progress=2, targeted_max_retries=3,
                                     has_fallback_model=routing.available)
    assert state.budgets.fallback_targeted_requested is False and state.budgets.targeted_retry_count == 3
    source = open(os.path.join(os.path.dirname(__file__), "..", "kriya", "workflow", "retry_strategy.py"),
                  encoding="utf-8").read()
    call = source[source.index("elif force_strategy_transition("):]
    call = call[:call.index("):")]
    assert "fallback_routing_for_context(state, ctx)).available" in call and "bool(ctx.chain)" not in call


def test_case7_full_set_escalation_skips_incompatible_and_keeps_compatible(tmp_path, monkeypatch):
    from kriya.workflow.attempt import _select_developer_fallback

    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY), _binding(PATCH, capabilities=PATCH_CAPS))
    big = _write(tmp_path, "Big.java", (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))
    ctx = _ctx(str(tmp_path), cfg, DeveloperAgent("developer", LLMClient(cfg)))
    state = GenerationState()
    assert _select_developer_fallback(state, ctx, 1, targets=[big], allow_primary=True).model == PATCH
    assert _select_developer_fallback(state, ctx, 2, targets=[big], allow_primary=True).model == PATCH
    # Only the whole-file-only one: the full-set attempt stays on the primary, it does not end.
    only_whole = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    ctx = _ctx(str(tmp_path), only_whole, DeveloperAgent("developer", LLMClient(only_whole)))
    state = GenerationState()
    assert _select_developer_fallback(state, ctx, 1, targets=[big], allow_primary=True) is None
    [selection] = [e.details for e in state.run_events if e.kind == "model.fallback_selection"]
    assert selection["primary_route"] is True and selection["rejected"][0]["patch_only_files"] == [big]
    # The fallback-targeted mode has no primary route: a dead requirement ends typed (never sent).
    with pytest.raises(QualityGateFailure):
        _select_developer_fallback(GenerationState(), ctx, 1, targets=[big])
    # A small target proves nothing: no skip.
    small = _write(tmp_path, "Small.java", 4000)
    assert _select_developer_fallback(GenerationState(), ctx, 1, targets=[small], allow_primary=True).model == WHOLE


def test_a_run_wide_rejection_at_escalation_stays_terminal_even_on_the_full_set_route(tmp_path, monkeypatch):
    """PRD-017 unchanged: a fallback that failed a Developer case is never
    turned into a primary route."""
    from kriya.workflow.attempt import _select_developer_fallback

    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(PATCH, capabilities=PATCH_CAPS))
    _qualify(cfg, PATCH, full_file_raw_content=mq.FAIL)
    ctx = _ctx(str(tmp_path), cfg, DeveloperAgent("developer", LLMClient(cfg)))
    with pytest.raises(QualityGateFailure):
        _select_developer_fallback(GenerationState(), ctx, 1, targets=[], allow_primary=True)


def test_the_resolver_keeps_resolve_fallback_model_order(tmp_path, monkeypatch):
    from kriya.workflow.attribution import resolve_fallback_model

    _exact_ollama(monkeypatch)
    chain = [_binding(f"f{i}:1b", capabilities=PATCH_CAPS) for i in range(3)]
    cfg = _cfg(*chain)
    big = _write(tmp_path, "Big.java", (_room(cfg, chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))
    for retry_count in (1, 2, 3, 5):
        compat = resolve_fallback_compatibility(cfg, chain, retry_count=retry_count,
                                                target_sizes={big: os.path.getsize(tmp_path / big)})
        assert compat.selected == resolve_fallback_model(retry_count, chain).model
        assert [c.model for c in compat.candidates] == [b.model for b in chain[min(retry_count - 1, 2):]]


# ===================================================================================================
# M1: Q8/Q9 distinguish "bypassed, continued" from "bypassed, terminal"
# ===================================================================================================


def test_q8_and_q9_explain_both_routing_outcomes(tmp_path, monkeypatch, no_sandbox_cleanup):
    """Real recorder, real store, real explain: one attempt whose failure
    bypassed the fallback and continued on the full-set route, then one
    where no route remained (terminal). Q8 names each decision; Q9 names the
    last one."""
    import subprocess

    from kriya.control.run_coordinator import begin_mutating_run
    from kriya.core.attempt_evidence import scope
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR

    _exact_ollama(monkeypatch)
    cfg = _cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    big = _write(tmp_path, "Big.java", (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))
    for argv in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", *argv], cwd=str(tmp_path), check=True)
    attempt = {"n": 0}
    with begin_mutating_run(str(tmp_path)) as context:
        with scope.unit_scope(cfg, "direct", "direct"):
            for retry_count in (1, 4):
                with scope.attempt_scope(lambda: attempt["n"]):
                    attempt["n"] += 1
                    scope.attempt_opened({"mode": "full_set"})
                    conclude_attempt_failure(_failed_state(big, retry_count=retry_count),
                                             _decision_ctx(cfg, tmp_path))
    explained = explain_run(os.environ[ENV_STATE_DIR], context.run_id)
    assert explained["verification"] == "VERIFIED"
    routed = []
    for entry in explained["attempts"]:
        q8 = entry["answers"]["Q8"]
        assert q8["status"] == "RECORDED"
        [item] = [i for i in q8["items"] if i.get("phase") == "routing"]
        routed.append(item)
    assert [(i["requirement"], i["resulting_strategy"], i["other_route_remained"]) for i in routed] == [
        (PATCH_ONLY_PROVEN, "full_set", True), (PATCH_ONLY_PROVEN, FALLBACK_MODEL_INCOMPATIBLE, False)]
    for item in routed:
        [candidate] = item["candidates"]
        assert candidate["model"] == WHOLE and candidate["status"] == CANDIDATE_PATCH_INCOMPATIBLE
        assert candidate["capability_source"] == "explicit_llm_chain" and candidate["patch_only_files"] == [big]
        assert candidate["runtime_digest"] and candidate["reasons"]
        assert item["decision_point"] == "recovery.conclude_attempt_failure"
    last = explained["Q9"]["last_fallback_routing"]
    assert last["resulting_strategy"] == FALLBACK_MODEL_INCOMPATIBLE and last["other_route_remained"] is False

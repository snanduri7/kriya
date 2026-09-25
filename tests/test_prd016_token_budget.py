"""PRD-016: tokenizer-aware dispatch budgeting. The final check before a
request leaves Kriya: count the full dispatch, reduce the output budget to
what fits, or refuse with CONTEXT_BUDGET_UNSATISFIABLE before inference."""
import math
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core import token_budget as tb
from kriya.core.llm import LLMClient
from kriya.core.model_qualification import TOKENIZER_CORPORA, build_record, save_record
from kriya.core.model_runtime import ModelRuntimeFingerprint

# The comparison of the bound with a real tokenizer's exact counts on each
# content class (Java, Python, XML, JSON, Unicode, stack traces) is made
# against the real runtime: tests/test_live_prd013_016_model_runtime.py, and the
# PRD-014 tokenizer_measurement case that records the measured ratio. These
# deterministic tests pin the estimator's own properties.


def test_default_bound_counts_utf8_bytes_not_characters():
    assert tb.bound_by_bytes("abcde", 2.5) == 2
    assert tb.bound_by_bytes("東京", 2.5) == math.ceil(6 / 1.5)
    assert tb.count_tokens("", qualified_bytes_per_token=None).tokens == 0


@pytest.mark.parametrize("name", sorted(TOKENIZER_CORPORA))
def test_the_default_bound_is_never_below_len_div_4(name):
    """The dispatch check is never looser than the allocator's estimate."""
    from kriya.workflow.context_budget import estimate_tokens

    text = TOKENIZER_CORPORA[name]
    assert tb.count_tokens(text).tokens >= estimate_tokens(text)


def test_non_ascii_text_is_counted_at_its_own_denser_rate():
    """Two tokens per CJK character by default; the ASCII rate would
    undercount it (1.2 per character)."""
    assert tb.count_tokens("東京" * 50).tokens == 200
    assert tb.count_tokens("東京" * 50).tokens > tb.bound_by_bytes("東京" * 50, 2.5, 2.5)
    assert tb.count_tokens("abcde" * 50).tokens == 100


def test_qualified_ratios_apply_per_character_class():
    counted = tb.count_tokens("a" * 300 + "é" * 30, qualified_bytes_per_token=3.0,
                              qualified_non_ascii_bytes_per_token=2.0)
    assert counted == tb.TokenCount(130, False, "qualified_byte_bound")


def test_an_exact_counter_is_used_when_registered_for_the_tokenizer():
    tb.register_exact_counter("sha256:tok", lambda text: 7)
    try:
        counted = tb.count_tokens("anything", tokenizer_digest="sha256:tok")
        assert counted == tb.TokenCount(7, True, "exact_local_tokenizer")
        assert tb.count_tokens("anything", tokenizer_digest="sha256:other").method == "default_byte_bound"
    finally:
        tb.unregister_exact_counter("sha256:tok")


def test_a_broken_exact_counter_degrades_visibly_to_the_bound():
    def broken(text):
        raise RuntimeError("tokenizer crashed")

    tb.register_exact_counter("sha256:bad", broken)
    try:
        assert tb.count_tokens("abc", tokenizer_digest="sha256:bad").method == "default_byte_bound"
    finally:
        tb.unregister_exact_counter("sha256:bad")


def test_a_qualified_ratio_replaces_the_default():
    counted = tb.count_tokens("a" * 300, qualified_bytes_per_token=3.0)
    assert counted == tb.TokenCount(100, False, "qualified_byte_bound")


def test_the_full_dispatch_is_counted_messages_tool_calls_and_schemas():
    messages = [{"role": "system", "content": "S" * 10}, {"role": "user", "content": "U" * 10},
                {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "f"}}]},
                {"role": "tool", "content": "R" * 10}]
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    text = tb.dispatch_text(messages, tools)
    assert "S" * 10 in text and "R" * 10 in text and '"name": "f"' in text and '"parameters"' in text


def _plan(prompt_chars, window, max_tokens=4096, **kw):
    return tb.plan_dispatch(
        messages=[{"role": "user", "content": "x" * prompt_chars}], requested_max_tokens=max_tokens,
        context_window=window, window_source="served_num_ctx", **kw,
    )


def test_a_fitting_request_keeps_its_full_output_budget():
    decision = _plan(1000, 32768)
    assert decision.satisfiable and not decision.output_reduced and decision.max_tokens == 4096
    assert decision.approximate and decision.counting_method == "default_byte_bound"


def test_a_tight_request_gets_a_reduced_output_budget_never_below_the_minimum():
    decision = _plan(int(2.5 * 30000), 32768)
    assert decision.output_reduced
    assert tb.DEFAULT_MIN_OUTPUT_TOKENS <= decision.max_tokens < 4096
    assert decision.prompt_tokens + decision.max_tokens <= 32768


def test_an_unsatisfiable_request_is_refused_before_dispatch():
    with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
        _plan(int(2.5 * 32000), 32768)
    assert refused.value.reason_code == tb.CONTEXT_BUDGET_UNSATISFIABLE
    assert refused.value.decision.satisfiable is False
    assert "Nothing was sent" in str(refused.value)


def test_reasoning_allowance_is_reserved_on_top_of_the_minimum_output():
    fits_without = _plan(int(2.5 * 30500), 32768)
    assert fits_without.satisfiable
    with pytest.raises(tb.ContextBudgetUnsatisfiableError):
        _plan(int(2.5 * 30500), 32768, reasoning_allowance=2048)


def test_an_unknown_window_never_refuses_but_is_recorded():
    decision = tb.plan_dispatch(messages=[{"role": "user", "content": "x" * 10**6}], requested_max_tokens=512,
                                context_window=None, window_source="unknown")
    assert decision.satisfiable and decision.context_window is None and decision.max_tokens == 512


def test_under_prediction_is_detected_after_the_call():
    decision = _plan(1000, 32768).to_dict()
    assert tb.compare_with_usage(decision, decision["prompt_tokens"] + 1, model="m")["under_predicted"] is True
    assert tb.compare_with_usage(decision, 10, model="m")["under_predicted"] is False
    assert tb.compare_with_usage(decision, None, model="m") is None


# --- adaptive context/output selection (pure) ----------------------------------------------------
_TIERS_64 = (tb.ContextTier(65536, tb.TIER_SOURCE_QUALIFICATION_RECORD),)


def _prompt_for(tokens):
    """A one-message request the default bound counts as ``tokens``."""
    return int(2.5 * (tokens - tb.REQUEST_OVERHEAD_TOKENS - tb.PER_MESSAGE_OVERHEAD_TOKENS))


def test_32k_preferred_grows_to_a_qualified_64k_tier_when_the_prompt_requires_it():
    decision = _plan(_prompt_for(41000), 32768, max_tokens=16384, tiers=_TIERS_64)
    assert decision.context_window == 65536 and decision.context_expanded
    assert decision.preferred_context_window == 32768
    assert decision.selection_reason == "prompt_exceeds_preferred_window"
    assert decision.qualification_source == tb.TIER_SOURCE_QUALIFICATION_RECORD
    assert decision.max_tokens == 16384 and not decision.output_expanded
    assert decision.prompt_tokens + decision.max_tokens + decision.safety_margin <= 65536


def test_32k_stays_selected_when_it_is_sufficient():
    decision = _plan(_prompt_for(10000), 32768, max_tokens=16384, tiers=_TIERS_64)
    assert decision.context_window == 32768 and not decision.expanded
    assert decision.selection_reason == "fits_preferred"
    assert decision.qualification_source == tb.TIER_SOURCE_PREFERRED


def test_only_offered_tiers_are_ever_selected():
    """An unverified 128K window is simply not a tier: the caller offers only
    qualified or operator-declared tiers, so a prompt that needs it is
    refused rather than sent with an unverified window."""
    with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
        _plan(_prompt_for(100000), 32768, max_tokens=4096, tiers=_TIERS_64)
    assert refused.value.decision.context_window == 65536
    assert [t["tokens"] for t in refused.value.decision.considered_tiers] == [32768, 65536]


def test_the_smallest_sufficient_qualified_tier_is_selected():
    tiers = (tb.ContextTier(131072, tb.TIER_SOURCE_QUALIFICATION_RECORD),
             tb.ContextTier(65536, tb.TIER_SOURCE_OPERATOR_DECLARED))
    decision = _plan(_prompt_for(41000), 32768, max_tokens=16384, tiers=tiers)
    assert decision.context_window == 65536
    assert decision.qualification_source == tb.TIER_SOURCE_OPERATOR_DECLARED
    bigger = _plan(_prompt_for(100000), 32768, max_tokens=16384, tiers=tiers)
    assert bigger.context_window == 131072


def test_the_hard_context_ceiling_excludes_larger_tiers():
    tiers = (tb.ContextTier(65536, tb.TIER_SOURCE_QUALIFICATION_RECORD),
             tb.ContextTier(131072, tb.TIER_SOURCE_QUALIFICATION_RECORD))
    with pytest.raises(tb.ContextBudgetUnsatisfiableError):
        _plan(_prompt_for(100000), 32768, max_tokens=4096, tiers=tiers, hard_context_ceiling=65536)


def test_strict_mode_never_exceeds_the_preferred_window():
    with pytest.raises(tb.ContextBudgetUnsatisfiableError):
        _plan(_prompt_for(41000), 32768, max_tokens=4096, tiers=_TIERS_64, policy_mode=tb.POLICY_STRICT)


def test_a_grounded_large_output_exceeds_the_normal_output_budget_within_qualified_limits():
    expected = tb.OutputExpectation(24000, "full-file rewrite of Big.java (~20000 tokens)")
    decision = _plan(_prompt_for(12000), 32768, max_tokens=16384, tiers=_TIERS_64, expected_output=expected)
    # 12000 + 24000 does not fit 32K: the smallest qualified tier that holds
    # both is chosen and the output allowance grows to the expectation.
    assert decision.context_window == 65536
    assert decision.max_tokens == 24000 and decision.output_expanded
    assert decision.selection_reason == "grounded_output_exceeds_preferred_window"
    assert decision.expected_output_tokens == 24000 and "Big.java" in decision.output_grounding
    # When it fits the preferred window, only the output grows.
    small = _plan(_prompt_for(2000), 32768, max_tokens=16384, tiers=_TIERS_64,
                  expected_output=tb.OutputExpectation(20000, "rewrite"))
    assert small.context_window == 32768 and small.max_tokens == 20000
    assert small.selection_reason == "grounded_output_exceeds_preferred_output"


def test_ungrounded_output_never_grows_and_never_triggers_expansion():
    decision = _plan(_prompt_for(20000), 32768, max_tokens=16384, tiers=_TIERS_64)
    # The prompt fits 32K with the minimum output: the output is reduced,
    # the context is not expanded just to restore the full max_tokens.
    assert decision.context_window == 32768 and decision.output_reduced
    assert decision.max_tokens < 16384 and not decision.output_expanded


def test_expected_output_above_the_hard_output_ceiling_is_refused_before_dispatch():
    with pytest.raises(tb.OutputBudgetUnsatisfiableError) as refused:
        _plan(_prompt_for(2000), 32768, max_tokens=16384, tiers=_TIERS_64,
              expected_output=tb.OutputExpectation(40000, "rewrite"), hard_output_ceiling=24000)
    assert refused.value.reason_code == tb.OUTPUT_BUDGET_UNSATISFIABLE
    assert isinstance(refused.value, tb.ContextBudgetUnsatisfiableError)
    assert "hard output ceiling 24000" in str(refused.value)


def test_strict_mode_refuses_a_grounded_output_above_the_preferred_output():
    with pytest.raises(tb.OutputBudgetUnsatisfiableError) as refused:
        _plan(_prompt_for(2000), 32768, max_tokens=4096, expected_output=tb.OutputExpectation(8000, "rewrite"),
              policy_mode=tb.POLICY_STRICT)
    assert "strict" in str(refused.value)


def test_the_selection_evidence_is_complete():
    decision = _plan(_prompt_for(41000), 32768, max_tokens=16384, tiers=_TIERS_64, hard_context_ceiling=65536)
    evidence = decision.to_dict()
    for key in ("preferred_context_window", "selected_context_window", "preferred_output_tokens",
                "selected_output_tokens", "prompt_tokens", "expected_output_tokens", "selection_reason",
                "qualification_source", "hard_context_ceiling", "hard_output_ceiling", "policy_mode",
                "considered_tiers"):
        assert key in evidence, key
    assert evidence["selected_context_window"] == 65536 and evidence["hard_context_ceiling"] == 65536


# --- the LLM boundary ---------------------------------------------------------------------------

def _response(content="ok"):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].message.reasoning = None
    response.choices[0].finish_reason = "stop"
    response.usage = MagicMock(prompt_tokens=40, completion_tokens=2)
    return response


def _exact(window=8192, **changes):
    fp = ModelRuntimeFingerprint(
        alias="qwen3-coder:30b", endpoint="http://localhost:11434/v1", provider="ollama",
        provider_version="0.34.2", artifact_digest="sha256:abc", tokenizer_digest="sha256:tok",
        configured_context_window=window, effective_context_window=window,
    )
    return replace(fp, **changes)


@pytest.mark.asyncio
async def test_an_unsatisfiable_prompt_never_reaches_the_model(monkeypatch):
    monkeypatch.setattr(model_runtime, "probe_model_runtime", lambda **kw: _exact(window=4096))
    llm = LLMClient(AppConfig())
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        with pytest.raises(tb.ContextBudgetUnsatisfiableError):
            await llm.complete("system", "x" * int(2.5 * 4096))
    create.assert_not_called()


@pytest.mark.asyncio
async def test_the_output_budget_sent_is_the_reduced_one(monkeypatch):
    monkeypatch.setattr(model_runtime, "probe_model_runtime", lambda **kw: _exact(window=8192))
    llm = LLMClient(AppConfig())
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        result = await llm.complete_result("system", "x" * int(2.5 * 6000), max_tokens_override=4096)
    sent = create.call_args[1]["max_tokens"]
    assert sent < 4096 and result.budget["output_reduced"] is True
    assert result.budget["prompt_tokens"] + sent <= 8192
    assert result.budget["window_source"] == "served_num_ctx"


@pytest.mark.asyncio
async def test_without_an_exact_runtime_the_config_window_is_the_declared_assumption():
    cfg = AppConfig()
    cfg.llm.context_window = 4096
    llm = LLMClient(cfg)
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(return_value=_response())):
        result = await llm.complete_result("s", "u")
    assert result.budget["window_source"] == "config_declared" and result.budget["context_window"] == 4096
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(return_value=_response())) as create:
        with pytest.raises(tb.ContextBudgetUnsatisfiableError):
            await llm.complete("s", "x" * int(2.5 * 4096))
        create.assert_not_called()


@pytest.mark.asyncio
async def test_tool_turns_count_the_schemas(monkeypatch):
    monkeypatch.setattr(model_runtime, "probe_model_runtime", lambda **kw: _exact(window=2048))
    cfg = AppConfig()
    cfg.llm.capabilities.native_tool_calls = True
    llm = LLMClient(cfg)
    huge_schema = [{"type": "function", "function": {"name": "f", "description": "d" * 6000,
                                                     "parameters": {"type": "object"}}}]
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        with pytest.raises(tb.ContextBudgetUnsatisfiableError):
            await llm.complete_with_tools([{"role": "user", "content": "hi"}], huge_schema)
    create.assert_not_called()


@pytest.mark.asyncio
async def test_a_qualified_tokenizer_ratio_is_used_for_the_exact_runtime(monkeypatch):
    from kriya.core.model_qualification import CAPABILITIES, CaseResult

    exact = _exact(window=32768)
    monkeypatch.setattr(model_runtime, "probe_model_runtime", lambda **kw: exact)
    llm = LLMClient(AppConfig())
    # The client's kriya_protocol makes the resolved fingerprint differ from
    # `exact`; qualify whatever the client resolves.
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(return_value=_response())):
        first = await llm.complete_result("s", "u")
    resolved = model_runtime.resolve_model_runtime(
        base_url=llm.config.llm.base_url, model=llm.config.llm.model,
        configured_context=model_runtime.configured_context_window(llm.config.llm.extra_body),
        kriya_protocol=model_runtime.kriya_protocol_identity(llm.config, llm.config.llm.model),
    )
    assert first.runtime_fingerprint == resolved.digest
    record = build_record(resolved, [CaseResult(c, "PASS", measured={"bytes_per_token_floor": 3.2}
                                                if c == "tokenizer_measurement" else {}) for c in CAPABILITIES])
    save_record(record)
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(return_value=_response())):
        second = await llm.complete_result("s", "u" * 3200)
    assert second.budget["counting_method"] == "qualified_byte_bound"
    assert first.budget["counting_method"] == "default_byte_bound"


def test_a_refusal_inside_a_real_attempt_is_a_typed_failure_and_nothing_is_written(tmp_path, monkeypatch):
    """End to end: the Developer's request cannot fit its model's window; the
    attempt fails with context_budget_unsatisfiable (reason code kept), the
    Developer's model is never called, and calc.py is untouched. Every other
    role runs on a separate model binding with a normal window."""
    import asyncio
    import subprocess

    from kriya.config.config import AgentModelConfig, LLMConfig
    from kriya.core.kernel import Kernel
    from kriya.workflow.failure import Failure
    from kriya.workflow.failure_reporting import FailureCategory, categorize_failure
    from kriya.workflow.workflow import WorkflowEngine

    workspace = tmp_path / "ws"
    workspace.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    (workspace / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "s"], cwd=workspace, check=True)

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm.model = "developer-small-window"
    cfg.llm.context_window = 1200
    cfg.llm.max_tokens = 256
    cfg.llm_chain = []
    for role in ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance"):
        setattr(cfg.agent_llms, role, AgentModelConfig(llm=LLMConfig(model="helper", context_window=32768)))

    recorded, developer_requests = [], []
    original = Failure.to_gate_outcome
    monkeypatch.setattr(Failure, "to_gate_outcome", lambda self: recorded.append(self) or original(self))

    llm = LLMClient(cfg)

    async def request_once(client, model, system_prompt, user_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if model == "developer-small-window":
            developer_requests.append(user_prompt)
        content = ('["calc.py"]' if "File List Planner" in first else "Step 1: add sub to calc.py"
                   if "Planner Agent" in first else "Review: Approved")
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    llm._request_once = request_once
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    result = asyncio.run(engine.run_generation_workflow(goal="add sub(a, b) to calc.py",
                                                        workspace_path=str(workspace)))

    assert not result["quality_gates_passed"]
    assert developer_requests == []
    assert (workspace / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    typed = [f for f in recorded if f.type == "context_budget_unsatisfiable"]
    assert typed, [(f.type, f.message[:80]) for f in recorded]
    assert typed[0].diagnostics["reason_code"] == tb.CONTEXT_BUDGET_UNSATISFIABLE
    assert categorize_failure("context_budget_unsatisfiable") is FailureCategory.RESOURCE

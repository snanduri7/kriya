"""PRD-016 / CTX-001: prompt allocation agrees with the dispatch budget.

The prompt builders size context in estimate_tokens() units; the dispatch
check counts the assembled request with a conservative byte bound against the
served window and keeps the configured output budget free. These tests pin
that every builder's worst case fits its share of the call's prompt
allocation window, and that a fully allocated Developer prompt is accepted by
the dispatch check with its output budget intact (before this, a fully
allocated prompt counted to more than the whole window and was refused)."""
import asyncio
from dataclasses import replace

import pytest

from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig
from kriya.core import model_runtime
from kriya.core import token_budget as tb
from kriya.core.llm import LLMClient
from kriya.core.model_qualification import CAPABILITIES, CaseResult, build_record, save_record
from kriya.core.model_runtime import ModelRuntimeFingerprint
from kriya.workflow.context_budget import (
    REASON_BUDGET_EXHAUSTED,
    _reserve_graph_context_budget,
    _reserve_sibling_content_budget,
    allocation_window,
    build_code_context,
    build_code_context_package,
    build_known_target_context,
    estimate_tokens,
    investigation_evidence_char_budget,
    prompt_allocation_window,
    retry_evidence_char_budget,
    review_batch_budget,
    skeletonize_code,
)


def _java_class(methods: int, index: int) -> str:
    body = "".join(
        f"    public int method{j}(int value) {{\n        return value * {j} + {index};\n    }}\n\n"
        for j in range(methods)
    )
    return f"package com.example;\n\npublic class Big{index} {{\n{body}}}\n"


def _dispatch_tokens(text: str) -> int:
    return tb.count_tokens(text).tokens


# ---------------------------------------------------------------------------
# The prompt allocation window
# ---------------------------------------------------------------------------

def test_the_allocation_window_keeps_the_output_budget_framing_and_margin_free():
    window, output = 32768, 16384
    room = window - output - tb.TWO_MESSAGE_FRAMING_TOKENS - tb.DISPATCH_SAFETY_MARGIN_TOKENS
    assert prompt_allocation_window(window, output) == int(room * tb.DEFAULT_BYTES_PER_TOKEN / 4)
    # Allocator units (len // 4) converted back to dispatch tokens fit the room.
    chars = prompt_allocation_window(window, output) * 4
    assert _dispatch_tokens("x" * chars) <= room


def test_an_output_budget_above_half_the_window_reserves_half():
    assert prompt_allocation_window(16384, 16384) == prompt_allocation_window(16384, 8192)
    assert prompt_allocation_window(16384, 16384) > 0


def test_a_qualified_ratio_widens_the_allocation_window():
    assert prompt_allocation_window(32768, 16384, bytes_per_token=3.2) > prompt_allocation_window(32768, 16384)


def test_allocation_window_follows_the_served_window_and_the_output_budget():
    cfg = AppConfig()
    cfg.llm.context_window = 32768
    cfg.llm.max_tokens = 4096
    cfg.llm.extra_body = {"options": {"num_ctx": 16384}}
    # The served num_ctx, not the declared window, is what dispatch checks.
    assert allocation_window(cfg) == prompt_allocation_window(16384, 4096)
    fallback = FallbackModelConfig(model="small", context_window=8192)
    # A fallback without num_ctx is checked against its declared window; the
    # output budget is the client's max_tokens (the only one LLMClient sends).
    assert allocation_window(cfg, fallback) == prompt_allocation_window(8192, 4096)
    fallback.reasoning = True
    assert allocation_window(cfg, fallback) == prompt_allocation_window(8192, 12288)


def test_allocation_window_uses_the_qualified_ratio_of_the_exact_runtime(monkeypatch):
    cfg = AppConfig()
    cfg.llm.extra_body = {"options": {"num_ctx": 32768}}
    cfg.llm.max_tokens = 16384
    exact = ModelRuntimeFingerprint(
        alias=cfg.llm.model, endpoint="http://localhost:11434/v1", provider="ollama",
        provider_version="0.34.2", artifact_digest="sha256:abc", tokenizer_digest="sha256:tok",
    )
    monkeypatch.setattr(model_runtime, "probe_model_runtime",
                        lambda **kw: replace(exact, configured_context_window=kw["configured_context"],
                                             effective_context_window=kw["configured_context"],
                                             kriya_protocol=kw["kriya_protocol"]))
    before = allocation_window(cfg)
    assert before == prompt_allocation_window(32768, 16384)
    resolved = model_runtime.resolve_configured_model_runtime(cfg)
    save_record(build_record(resolved, [
        CaseResult(c, "PASS", measured={"bytes_per_token_floor": 3.2} if c == "tokenizer_measurement" else {})
        for c in CAPABILITIES
    ]))
    assert allocation_window(cfg) == prompt_allocation_window(32768, 16384, bytes_per_token=3.2)


# ---------------------------------------------------------------------------
# build_code_context: the budget is a bound
# ---------------------------------------------------------------------------

def _write_big_files(root, count=12, methods=400):
    names = []
    for i in range(count):
        name = f"Big{i}.java"
        (root / name).write_text(_java_class(methods, i), encoding="utf-8")
        names.append(name)
    return names


def test_graph_context_never_exceeds_its_budget_even_at_signatures(tmp_path):
    """Before PRD-016 the tier search stopped at "signatures" and rendered
    every file: twelve large classes produced ~210K characters against a
    ~4.7K-token limit."""
    names = _write_big_files(tmp_path)
    limit = 4683
    rendered, package = build_code_context_package(names[:6], names[6:], str(tmp_path), limit)
    shown = sum(estimate_tokens(item.content) for item in package.relevant_files)
    assert shown <= limit
    omitted = [entry for entry in package.omitted if entry["reason"] == REASON_BUDGET_EXHAUSTED]
    assert omitted, "files that do not fit must be recorded, never silently dropped"
    shown_paths = {item.path for item in package.relevant_files}
    for entry in omitted:
        assert entry["path"] not in shown_paths
        assert entry["path"] in rendered  # named in the prompt as left out
    # Related files are left out before any matched file is.
    omitted_paths = {e["path"] for e in omitted}
    if omitted_paths & set(names[:6]):
        assert set(names[6:]) <= omitted_paths


def test_score_aware_omission_drops_the_lowest_scored_file_first(tmp_path):
    names = _write_big_files(tmp_path, count=4)
    scores = {names[0]: 0.9, names[1]: 0.1, names[2]: 0.8, names[3]: 0.7}
    signatures = {n: estimate_tokens(skeletonize_code((tmp_path / n).read_text(), n, "signatures")) for n in names}
    # Room for three files at their smallest tier, not four.
    limit = sum(signatures.values()) - signatures[names[1]]
    _, package = build_code_context_package(names[:2], names[2:], str(tmp_path), limit, file_scores=scores)
    omitted = [e["path"] for e in package.omitted if e["reason"] == REASON_BUDGET_EXHAUSTED]
    assert omitted == [names[1]]


def test_context_within_budget_is_rendered_exactly_as_before(tmp_path):
    names = _write_big_files(tmp_path, count=2, methods=5)
    rendered = build_code_context(names[:1], names[1:], str(tmp_path), 10 ** 6)
    assert "Left out for the context budget" not in rendered
    assert f"File: {names[0]} (Tier: full)" in rendered and f"File: {names[1]} (Tier: full)" in rendered


# ---------------------------------------------------------------------------
# Each section's share
# ---------------------------------------------------------------------------

def test_every_section_budget_fits_the_dispatch_room_together():
    for window, output in ((32768, 16384), (65536, 16384), (16384, 4096)):
        prompt_window = prompt_allocation_window(window, output)
        graph = _reserve_graph_context_budget(prompt_window)
        siblings = _reserve_sibling_content_budget(prompt_window)
        retry_chars = retry_evidence_char_budget(prompt_window)
        investigation_chars = investigation_evidence_char_budget(prompt_window)
        allocator_tokens = graph + siblings + (retry_chars + investigation_chars) // 4
        assert allocator_tokens <= prompt_window, (window, output)
        room = window - output - tb.TWO_MESSAGE_FRAMING_TOKENS - tb.DISPATCH_SAFETY_MARGIN_TOKENS
        assert _dispatch_tokens("x" * (allocator_tokens * 4)) <= room, (window, output)


def test_review_batches_fit_beside_the_output_budget():
    cfg = AppConfig()
    cfg.llm.context_window = 32768
    cfg.llm.max_tokens = 16384
    cfg.llm.extra_body = {}
    budget = review_batch_budget(cfg)
    assert _dispatch_tokens("x" * budget * 4) <= 32768 - 16384


def test_investigation_evidence_keeps_whole_items_and_names_the_rest():
    from kriya.policy.trust import TrustLevel
    from kriya.workflow.context_package import make_context_item
    from kriya.workflow.investigation import render_investigation_evidence

    items = [
        make_context_item(f"f{i}.py", "x" * 1000, "investigation", "semantic_hit", TrustLevel.REPOSITORY)
        for i in range(5)
    ]
    rendered = render_investigation_evidence(items, char_budget=2500)
    assert rendered.count("=== Investigation evidence:") == 2
    assert "Left out for the context budget: f2.py, f3.py, f4.py" in rendered
    assert render_investigation_evidence(items).count("=== Investigation evidence:") == 5


# ---------------------------------------------------------------------------
# End to end: a fully allocated Developer prompt through the real producer
# (DeveloperAgent) and the real consumer (LLMClient's dispatch check)
# ---------------------------------------------------------------------------

def _developer_budgets(tmp_path, window, max_tokens):
    cfg = AppConfig()
    cfg.llm.context_window = window
    cfg.llm.max_tokens = max_tokens
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    names = _write_big_files(tmp_path)
    (tmp_path / "Target.java").write_text(_java_class(900, 99), encoding="utf-8")
    prompt_window = allocation_window(cfg)
    design = "Design: " + "The service layer delegates to the repository. " * 60
    plan = "Step: " + "Update the target method and keep the API. " * 60
    skills = "=== Active Skills ===\n" + "Skill guidance line.\n" * 40
    learned = "=== Learned Knowledge ===\n" + "Learned fact line.\n" * 40
    graph = build_code_context(names[:6], names[6:], str(tmp_path),
                               _reserve_graph_context_budget(prompt_window, skills, learned, design, plan))
    known, _ = build_known_target_context(
        ["Target.java"], str(tmp_path), None,
        _reserve_graph_context_budget(prompt_window, skills, learned, design, plan, graph),
    )
    retry = ("[retry evidence] " + _java_class(200, 7))[:retry_evidence_char_budget(prompt_window)]
    investigation = "x" * investigation_evidence_char_budget(prompt_window)
    code_context = skills + graph + learned + known + retry + investigation

    llm = LLMClient(cfg)
    budgets = []
    original = llm._dispatch_budget

    def spy(**kwargs):
        decision = original(**kwargs)
        budgets.append(decision)
        return decision

    async def request_once(client, model, system_prompt, user_prompt, *args, **kwargs):
        # Every file comes back large, so later files see full sibling sections.
        return {"content": _java_class(300, 5), "reasoning_chars": 0, "prompt_tokens": 10,
                "completion_tokens": 5, "finish_reason": "stop", "provider_metadata": {}}

    llm._dispatch_budget = spy
    llm._request_once = request_once
    from kriya.agents.agent import DeveloperAgent

    developer = DeveloperAgent("developer", llm)
    asyncio.run(developer.run_generation(
        "Goal: change Target.java\n" + plan, design, code_context,
        known_target_files=["A.java", "B.java", "C.java", "Target.java"],
        sibling_content_budget=_reserve_sibling_content_budget(prompt_window),
    ))
    assert len(budgets) == 4
    return budgets


@pytest.mark.parametrize("window,max_tokens", [(32768, 16384), (65536, 16384), (16384, 4096)])
def test_a_fully_allocated_developer_prompt_keeps_its_whole_output_budget(tmp_path, window, max_tokens):
    budgets = _developer_budgets(tmp_path, window, max_tokens)
    for decision in budgets:
        assert decision.satisfiable
        assert decision.max_tokens == max_tokens and not decision.output_reduced, decision.to_dict()


@pytest.mark.parametrize("window,max_tokens", [(16384, 16384), (8192, 4096)])
def test_a_small_window_trims_the_output_but_never_refuses(tmp_path, window, max_tokens):
    """Output above half the window, or section floors in a very small
    window, leave less than the configured output: dispatch reduces
    max_tokens (recorded) and still sends the full context."""
    budgets = _developer_budgets(tmp_path, window, max_tokens)
    for decision in budgets:
        assert decision.satisfiable
        assert decision.max_tokens >= tb.DEFAULT_MIN_OUTPUT_TOKENS

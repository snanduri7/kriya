"""DEVELOPER-PROMPT-FIT-001: a Developer request's optional context is sized
only after its mandatory text. Every per-file (and single-stage) Developer
request is fitted into the capacity of the binding it is sent to
(context_budget.fit_developer_request, applied by DeveloperAgent with the
DeveloperRequestFit the attempt's single choke point builds): the optional
sections - already-written sibling contents, graph context, investigation
evidence, untrusted learned reference - give way in reverse
DEVELOPER_SECTION_ORDER; the mandatory text (system prompt, task and
authoritative goal, design, required blocks, retry evidence, directives) is
never trimmed, so a request whose mandatory text alone cannot fit is still
refused before inference (CONTEXT_BUDGET_UNSATISFIABLE).

Before the fix the PRD-016 pools assumed 0.10 of the window for all of the
mandatory text: at 8K a repair request carried ~4,450 prompt tokens against
~3,800 of room and was sent only because the dispatch check cut the output.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from test_prd020_milestone_requirements import _probe
from test_prompt_budget_fit_001ab import REFERENCE, _run

from kriya.agents.agent import DeveloperAgent
from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig
from kriya.core import model_runtime
from kriya.core import token_budget as tb
from kriya.core.llm import LLMClient
from kriya.workflow import context_budget as budget
from kriya.workflow.untrusted_context import (
    UNTRUSTED_REFERENCE_BEGIN,
    UNTRUSTED_REFERENCE_END,
    fence_untrusted_reference,
)

COMPILE_ERROR = "[ERROR] LedgerReport.java:[3,9] cannot find symbol: total\n"
# Kriya's own mandatory sections of a repair request (see the module docstring).
MANDATORY_REPAIR_SECTIONS = (
    "=== Task ===", "=== Architecture Design ===", "=== Previous Error to Fix ===",
    "=== Ecosystem Preservation (required) ===", "=== Resource Lifecycle (required) ===",
    "=== Verification Contract (required whenever applicable) ===",
    "=== Authoritative current implementation to repair: ",
)


def _capacity(tokens):
    return budget.RequestCapacity(tokens=tokens)


def _section(kind, text, rebuilt=""):
    return budget.OptionalSection(kind, text, lambda _budget: rebuilt)


def _developer_requests(run):
    """(system, user) of every Developer generation request (not its
    file-list step)."""
    return [(system, user) for first, system, user in run.transport.requests
            if "Developer Agent" in first]


def _within(capacity, system, user):
    return capacity.count(system) + capacity.count(user) <= capacity.tokens


# --- the primitive ------------------------------------------------------------------

def test_a_request_that_fits_is_returned_byte_identical():
    prompt = "mandatory " + "graph " * 10
    fitted, details = budget.fit_developer_request(
        _capacity(10_000), "system", prompt, [_section("graph_context", "graph " * 10, "never")])
    assert fitted is prompt and details == {}


def test_optional_sections_give_way_reference_first_then_graph_then_siblings():
    mandatory = "M" * 400
    graph, siblings, reference = "G" * 400, "S" * 400, fence_untrusted_reference("R" * 400)
    prompt = mandatory + siblings + graph + reference
    sections = [
        _section("learned_reference", reference, "ref-small"),
        _section("graph_context", graph, "graph-small"),
        _section("siblings", siblings, "sib-small"),
    ]
    whole = budget.RequestCapacity(tokens=10_000).count
    # Room for the mandatory text and two whole sections: the reference goes.
    room = whole("system") + whole(mandatory) + whole(siblings) + whole(graph) + 20
    fitted, details = budget.fit_developer_request(_capacity(room), "system", prompt, sections)
    assert fitted == mandatory + siblings + graph + "ref-small" or fitted == mandatory + siblings + graph
    assert details["sections"]["learned_reference"]["reduced"]
    assert not details["sections"]["graph_context"]["reduced"] and not details["sections"]["siblings"]["reduced"]
    # Room for the mandatory text and one section: siblings are kept.
    room = whole("system") + whole(mandatory) + whole(siblings) + 5
    fitted, details = budget.fit_developer_request(_capacity(room), "system", prompt, sections)
    assert fitted.startswith(mandatory + siblings) and graph not in fitted and reference not in fitted


def test_the_mandatory_text_is_never_trimmed_even_when_it_alone_does_not_fit():
    mandatory = "M" * 4000
    prompt = mandatory + "G" * 400
    fitted, details = budget.fit_developer_request(
        _capacity(100), "system", prompt, [_section("graph_context", "G" * 400, "G")])
    assert fitted == mandatory and details["sections"]["graph_context"]["omitted"]


def test_a_section_not_found_exactly_once_is_kept_as_mandatory_and_reported():
    prompt = "M" * 400 + "dup" + "M" * 10 + "dup"
    fitted, details = budget.fit_developer_request(
        _capacity(50), "system", prompt, [_section("graph_context", "dup"), _section("siblings", "absent")])
    assert fitted == prompt and details["unlocated_sections"] == ["graph_context", "siblings"]


def test_the_learned_reference_section_keeps_whole_entries_and_its_fence():
    fenced = fence_untrusted_reference(REFERENCE)
    section = budget.fenced_reference_section(fenced)
    whole_entries = REFERENCE.count("[Source: ")
    for budget_units in (0, 50, 400, 900, 5000):
        rebuilt = section.rebuild(budget_units)
        assert rebuilt.count(UNTRUSTED_REFERENCE_BEGIN) == rebuilt.count(UNTRUSTED_REFERENCE_END) <= 1
        assert budget.estimate_tokens(rebuilt) <= max(budget_units, 0) or rebuilt == ""
        assert rebuilt.count("[Source: ") <= whole_entries
    assert budget.fenced_reference_section("") is None


def test_the_capacity_is_the_binding_actually_called_with_the_output_its_answer_needs():
    cfg = AppConfig()
    cfg.llm.context_window = 32768
    cfg.llm.max_tokens = 4096
    cfg.llm.extra_body = {}
    small = FallbackModelConfig(model="small-model", context_window=8192, max_tokens=2048)
    framing = tb.TWO_MESSAGE_FRAMING_TOKENS + tb.DISPATCH_SAFETY_MARGIN_TOKENS
    assert budget.DeveloperRequestFit(cfg, None, ()).capacity().tokens == 32768 - 4096 - framing
    fit = budget.DeveloperRequestFit(cfg, small, ())
    assert fit.capacity().tokens == 8192 - 2048 - framing
    # A full-file answer grounded to need 3000 tokens reserves them (at most half the window).
    assert fit.capacity(3000).tokens == 8192 - 3000 - framing
    assert fit.capacity(1000).tokens == 8192 - 2048 - framing


# --- end to end: the real engine and dispatch check ----------------------------------

def test_at_8k_every_developer_request_fits_its_capacity_and_keeps_its_mandatory_text(tmp_path, monkeypatch):
    """The 8K boundary. Before the fix the repair requests exceeded the
    capacity (they were sent with their output cut)."""
    run = _run(tmp_path, monkeypatch, 8192, compile_error=COMPILE_ERROR)
    capacity = budget.request_capacity(run.cfg)
    requests = _developer_requests(run)
    assert requests and run.refusals == []
    assert all(_within(capacity, system, user) for system, user in requests)
    repairs = [user for _, user in requests if "=== Previous Error to Fix ===" in user]
    assert repairs
    for user in repairs:
        assert all(marker in user for marker in MANDATORY_REPAIR_SECTIONS), user[:400]
        assert "Authoritative validator evidence:\n" in user  # the gate error being repaired
    assert any("cannot find symbol: total" in user for user in repairs)
    reductions = [e for e in run.events if e.kind == "model.optional_context_reduced"
                  and e.details.get("reason") == "request_fit"]
    assert reductions and all(
        not any(s["reduced"] for kind, s in e.details["sections"].items() if kind not in budget.DEVELOPER_SECTION_ORDER)
        for e in reductions)
    assert run.result.get("failure_category") == "quality_gates_exhausted"


def test_at_32k_the_developer_requests_are_unchanged(tmp_path, monkeypatch):
    """Control: nothing needs fitting, so no request is touched."""
    run = _run(tmp_path, monkeypatch, 32768, compile_error=COMPILE_ERROR, reference=REFERENCE)
    assert not [e for e in run.events if e.kind == "model.optional_context_reduced"]
    requests = _developer_requests(run)
    assert requests and all(fence_untrusted_reference(REFERENCE) in user for _, user in requests)
    assert all("=== Codebase Semantic Reference Context ===" in user for _, user in requests)


def test_a_large_reference_shrinks_first_and_keeps_its_fence(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, 12288, compile_error=COMPILE_ERROR, reference=REFERENCE * 3)
    capacity = budget.request_capacity(run.cfg)
    requests = _developer_requests(run)
    assert requests and run.refusals == []
    for system, user in requests:
        assert _within(capacity, system, user)
        assert user.count(UNTRUSTED_REFERENCE_BEGIN) == user.count(UNTRUSTED_REFERENCE_END) <= 1
        assert user.count("[Source: ") < (REFERENCE * 3).count("[Source: ")
    # Where the reference was cut, the graph context was kept at least as whole.
    for event in run.events:
        sections = event.details.get("sections", {}) if event.kind == "model.optional_context_reduced" else {}
        if sections.get("graph_context", {}).get("reduced"):
            assert sections.get("learned_reference", {}).get("omitted", True)


def test_a_request_whose_mandatory_text_alone_cannot_fit_is_refused_before_inference(monkeypatch):
    """Genuinely unsatisfiable: the fit leaves every optional section out,
    never trims the task, and the real dispatch check refuses the request
    (CONTEXT_BUDGET_UNSATISFIABLE) without sending it."""
    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe)
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.context_window = 8192
    cfg.llm.extra_body = {}
    developer = DeveloperAgent("developer", LLMClient(cfg))
    graph = "\n\n=== Codebase Semantic Reference Context ===\n" + "class Graph {}\n" * 300
    task = "Implement it.\n" + "A mandatory requirement line.\n" * 1200
    fit = budget.DeveloperRequestFit(cfg, None, [_section("graph_context", graph)])
    create = AsyncMock()
    with patch.object(developer.llm.client.chat.completions, "create", new=create):
        with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refusal:
            asyncio.run(developer.run_generation(
                task, "design", "skills" + graph, known_target_files=["A.java"], request_fit=fit))
    create.assert_not_called()
    assert refusal.value.reason_code == tb.CONTEXT_BUDGET_UNSATISFIABLE
    [reduction] = [r for r in developer.llm.budget_expansions if r.get("reason") == "request_fit"]
    assert reduction["sections"]["graph_context"]["omitted"] and reduction["file"] == "A.java"


def test_the_choke_point_fits_for_the_binding_actually_called_with_its_sections(monkeypatch):
    """Every Developer generation goes through _run_developer_generation:
    the request fit it hands the agent names the llm_chain binding the call
    is sent to (the primary when there is no override) and carries the
    branch's optional sections."""
    from types import SimpleNamespace

    from kriya.workflow import attempt
    from kriya.workflow.state import GenerationState

    cfg = AppConfig()
    fallback = FallbackModelConfig(model="dev-fallback", context_window=8192)
    seen = []

    async def run_generation(**kwargs):
        seen.append(kwargs["request_fit"])
        return []

    async def no_investigation(*_args):
        return None

    monkeypatch.setattr(attempt, "_enter_developer_model", lambda _state, _ctx, kwargs: kwargs)
    monkeypatch.setattr(attempt, "_require_qualified_retry_identity", lambda *_args: None)
    monkeypatch.setattr(attempt, "_ensure_generation_time_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(attempt, "_maybe_run_developer_investigation", no_investigation)
    ctx = SimpleNamespace(kernel=SimpleNamespace(config=cfg), chain=[fallback], expected_files_upfront=None,
                          developer=SimpleNamespace(run_generation=run_generation, llm=None))
    section = _section("graph_context", "graph")
    for override in ("dev-fallback", None):
        asyncio.run(attempt._run_developer_generation(
            GenerationState(), ctx, model_override=override, known_target_files=["A.java"],
            expected_output_by_file={}, optional_sections=(section,)))
    assert [fit.binding for fit in seen] == [fallback, None]
    assert all(fit.sections == (section,) and fit.config is cfg for fit in seen)

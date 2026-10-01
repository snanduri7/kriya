"""DEVELOPER-PROMPT-FIT-001, the branches and the sibling retry (Backlog 6.6
final gate): the missing-files, fallback-targeted and coordinated-repair
attempt branches each register their optional context and reach the
Developer fitting helper before inference, and the sibling-names retry after
a context refusal starts from the fitted request.

The branch tests drive the real run_attempt with a real DeveloperAgent at an
8K window; only LLMClient.complete is a recorder (the point is what reaches
inference, not what comes back). Each branch's learned reference is sized
by the PRD-016 pool, which assumes the mandatory text is small; a long
(mandatory) design makes the request too large, so only the Developer fit
brings it within the capacity of the binding it is sent to.
"""
import asyncio
import contextlib

import pytest
from test_prompt_budget_fit_001ab import REFERENCE
from test_workflow import (
    _make_two_participant_contract,
    _minimal_attempt_ctx,
    _write_prv06_fixture,
)

from kriya.agents.agent import DeveloperAgent
from kriya.config.config import FallbackModelConfig
from kriya.core import token_budget as tb
from kriya.core.llm import LLMClient
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow import context_budget as budget
from kriya.workflow.attempt import run_attempt
from kriya.workflow.context_package import make_context_item
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.state import GenerationState
from kriya.workflow.untrusted_context import (
    UNTRUSTED_REFERENCE_BEGIN,
    UNTRUSTED_REFERENCE_END,
    fence_untrusted_reference,
)

FENCED = fence_untrusted_reference(REFERENCE * 4)
# Mandatory text every branch's request carries whole: a required block. It
# is not reserved by the PRD-016 pools (which assume the mandatory text is
# small), so with it the pool-sized reference no longer fits the request.
BLOCK = "\n\n=== Ecosystem Preservation (required) ===\n" + "".join(
    f"- Rule {i}: every public method keeps its signature and documented behaviour.\n" for i in range(62))
FALLBACK = "fallback-coder"


class Recorder:
    """LLMClient.complete stand-in: records every Developer request that
    reached inference, then stops the attempt."""

    def __init__(self):
        self.requests = []

    async def __call__(self, system_prompt, prompt, **kwargs):
        if "File List Planner" not in (system_prompt or ""):
            self.requests.append((kwargs.get("model_override"), system_prompt, prompt))
        raise RuntimeError("recorded; inference not simulated")


def _ctx(tmp_path, recorder, **overrides):
    ctx = _minimal_attempt_ctx(tmp_path, learned_rag_context=FENCED, ecosystem_invariant_block=BLOCK, **overrides)
    cfg = ctx.kernel.config
    cfg.llm.context_window = 8192
    cfg.llm.extra_body = {}
    llm = LLMClient(cfg)
    llm.complete = recorder
    ctx.developer = DeveloperAgent("developer", llm)
    # The branch's own reference, sized by the pool, is present but does
    # not fit beside the mandatory text: without the fit, over capacity.
    assert "[Source: " in budget.developer_reference(budget.allocation_window(cfg), FENCED)
    return ctx


def _run(state, ctx):
    with contextlib.suppress(Exception):
        asyncio.run(run_attempt(state, ctx))


def _known_target(state, tmp_path, path):
    content = (tmp_path / path).read_text()
    state.known_target_context_items[path] = make_context_item(
        path=path, content=content, reason="known_target_full_source", source_type="named_in_request",
        trust_level="repository", tier="full", is_exact=True, revision=content_revision(content),
    )


def _missing_files(tmp_path):
    state = GenerationState()
    state.attempt_number = 1
    state.budgets.retry_count = 1
    state.last_missing_files = ["app.py"]
    state.error_context = "INCOMPLETE GENERATION: app.py was not written"
    return state, {}


def _fallback_targeted(tmp_path):
    (tmp_path / "app.py").write_text("def main():\n    return undefined_name\n")
    state = GenerationState()
    state.attempt_number = 1
    state.all_files_written = {"app.py"}
    state.last_implicated_files = ["app.py"]
    state.error_context = "app.py:2: NameError: name 'undefined_name' is not defined"
    state.budgets.fallback_targeted_requested = True
    _known_target(state, tmp_path, "app.py")
    fallback = FallbackModelConfig(model=FALLBACK, context_window=8192)
    return state, {"chain": [fallback], "allowed_write_relpaths": ["app.py"],
                   "write_scope_mode": WriteScopeMode.ALLOWLIST}


def _coordinated(tmp_path):
    app_path, test_path = _write_prv06_fixture(tmp_path)
    state = GenerationState()
    state.all_files_written = {app_path, test_path}
    state.last_implicated_files = [test_path]
    state.error_context = "TEST_PROCESS_TERMINATED: process boundary conflict"
    state.repair_contract = _make_two_participant_contract(app_path, test_path, created_attempt=1)
    for path in (app_path, test_path):
        _known_target(state, tmp_path, path)
    return state, {"architect_files": [app_path, test_path], "expected_files_upfront": [app_path, test_path],
                   "allowed_write_relpaths": [app_path, test_path], "write_scope_mode": WriteScopeMode.ALLOWLIST}


@pytest.mark.parametrize("setup, mode, sent_to", [
    (_missing_files, "missing_files", None),
    (_fallback_targeted, "fallback_targeted", FALLBACK),
    (_coordinated, "targeted", None),
], ids=["missing-files", "fallback-targeted", "coordinated-repair"])
def test_each_constrained_branch_is_fitted_before_inference(tmp_path, setup, mode, sent_to):
    state, overrides = setup(tmp_path)
    recorder = Recorder()
    ctx = _ctx(tmp_path, recorder, **overrides)
    _run(state, ctx)

    assert state.last_attempt_mode == mode
    if setup is _coordinated:
        assert any(e.kind == "developer_repair_call" for e in state.run_events)
    assert recorder.requests, "no Developer request reached inference"
    binding = next((c for c in ctx.chain if c.model == sent_to), None)
    capacity = budget.request_capacity(ctx.kernel.config, binding)
    for model, system, prompt in recorder.requests:
        assert model == sent_to
        # Within the capacity of the binding actually called ...
        assert capacity.count(system) + capacity.count(prompt) <= capacity.tokens
        # ... with the mandatory block whole and the reference cut at whole
        # entries (or left out), never at a cut fence.
        assert BLOCK.strip() in prompt
        assert prompt.count(UNTRUSTED_REFERENCE_BEGIN) == prompt.count(UNTRUSTED_REFERENCE_END) <= 1
        assert prompt.count("[Source: ") < FENCED.count("[Source: ")
    fits = [e.details for e in state.run_events
            if e.kind == "model.optional_context_reduced" and e.details.get("reason") == "request_fit"]
    assert fits and all(f["unlocated_sections"] == [] and "learned_reference" in f["sections"] for f in fits)


# --- the sibling-names retry after a refusal --------------------------------------

SIBLING = "a = 1  # the first file of the batch\n" * 40
GRAPH = "\n\n=== Codebase Semantic Reference Context ===\n" + "class Related {}\n" * 600


def _refusal():
    return tb.ContextBudgetUnsatisfiableError(tb.plan_dispatch(
        messages=[{"role": "user", "content": "x"}], requested_max_tokens=1, context_window=None,
        window_source="unknown"))


def _sibling_retry(capacity_for):
    """b.py's request (a.py already written in the batch) is refused once
    by the dispatch check; returns every prompt sent and the fit used."""
    from kriya.config import AppConfig

    llm = LLMClient(AppConfig())
    prompts = []
    replies = [SIBLING, _refusal(), "b = 2\n"]

    async def complete(system_prompt, prompt, **kwargs):
        del kwargs
        prompts.append((system_prompt, prompt))
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    llm.complete = complete
    developer = DeveloperAgent("developer", llm)
    graph = budget.OptionalSection("graph_context", GRAPH, lambda _budget: "")
    fit = budget.DeveloperRequestFit(llm.config, None, (graph,))
    fit.capacity = lambda output_tokens=None: capacity_for(prompts)
    files = asyncio.run(developer.run_generation(
        "task", "design", "skills" + GRAPH, known_target_files=["a.py", "b.py"],
        sibling_content_budget=10 ** 6, request_fit=fit))
    return files, prompts, llm


def test_the_sibling_names_retry_starts_from_the_fitted_request():
    """The fit dropped the graph context but kept a.py's contents; b.py's
    request is then refused, and the retry replaces the sibling contents
    with their names in THAT fitted request - the graph context the fit
    removed never comes back."""
    whole = budget.RequestCapacity(tokens=10 ** 6).count

    def capacity_for(prompts):
        del prompts
        # Room for everything but the graph context.
        return budget.RequestCapacity(tokens=whole(DeveloperAgent("d", None).system_prompt) + 2500)

    files, prompts, llm = _sibling_retry(capacity_for)
    assert [f["filepath"] for f in files] == ["a.py", "b.py"]
    _, refused = prompts[1]
    _, retried = prompts[2]
    assert SIBLING in refused and "Codebase Semantic Reference Context" not in refused
    assert SIBLING not in retried and "Codebase Semantic Reference Context" not in retried
    assert "contents omitted - context budget; filenames only): a.py" in retried
    reasons = [r.get("reason") for r in llm.budget_expansions]
    assert "request_fit" in reasons and "sibling_contents_omitted" in reasons


def test_a_refusal_after_the_fit_already_dropped_the_siblings_is_raised_not_retried():
    def capacity_for(prompts):
        del prompts
        return budget.RequestCapacity(tokens=10)  # nothing optional fits

    with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refusal:
        _sibling_retry(capacity_for)
    assert refusal.value.filepath == "b.py"


def test_without_a_fit_the_sibling_retry_is_unchanged():
    """Control: no request fit - the pre-existing PRD-016 sibling fallback."""
    from kriya.config import AppConfig

    llm = LLMClient(AppConfig())
    prompts = []
    replies = [SIBLING, _refusal(), "b = 2\n"]

    async def complete(system_prompt, prompt, **kwargs):
        del system_prompt, kwargs
        prompts.append(prompt)
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    llm.complete = complete
    files = asyncio.run(DeveloperAgent("developer", llm).run_generation(
        "task", "design", "skills", known_target_files=["a.py", "b.py"], sibling_content_budget=10 ** 6))
    assert len(files) == 2 and SIBLING in prompts[1] and SIBLING not in prompts[2]
    assert prompts[2] == prompts[1].replace(prompts[1][prompts[1].index("=== Already-Written File"):
                                                     prompts[1].index(SIBLING) + len(SIBLING) + 2],
                                             "=== Already-Written Files This Batch (contents omitted - context "
                                             "budget; filenames only): a.py ===\n\n", 1)



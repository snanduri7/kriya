"""The prompt-fit closure before PRD-035 (ARCHITECT-PROMPT-FIT-001,
DEVELOPER-AUX-LOOP-PROMPT-FIT-001; with them PROMPT-BUDGET-FIT-001 closes).

- ARCHITECT-PROMPT-FIT-001: the Architect request is fitted per role
  candidate like the Planner's (graph context, then the fenced untrusted
  reference get the room the mandatory text leaves). A 30-entry reference at
  16K used to be refused before inference (CONTEXT_BUDGET_UNSATISFIABLE).
- DEVELOPER-AUX-LOOP-PROMPT-FIT-001: the investigation and self-correction
  loops send only requests that fit: the investigation loop fits its first
  request's optional context, and both end with a typed reason before a turn
  whose conversation no longer fits (never a request the dispatch check must
  refuse).

The Architect cases run the real engine and dispatch check (the 001A/B
harness); the loop cases run the real LLMClient over the test runtime port.
"""
import asyncio
import json
from pathlib import Path

from _chaos_harness import ChaosRuntime, RuntimeRegistration, chaos_config, git_workspace
from test_prompt_budget_fit_001ab import REFERENCE, _run

from kriya.agents.agent import ArchitectAgent
from kriya.core.inference_runtime import ChatResponse, RawToolCall
from kriya.core.llm import LLMClient
from kriya.core.model_capabilities import ModelCapabilities
from kriya.workflow import context_budget as budget
from kriya.workflow.context_source import SourceDerivationCache
from kriya.workflow.investigation import InvestigationDependencies, run_investigation_loop
from kriya.workflow.untrusted_context import UNTRUSTED_REFERENCE_BEGIN, UNTRUSTED_REFERENCE_END

THIRTY = "".join(
    f"\n[Source: https://docs.example/{i} (Fetched: 2026-09-27)]\n" + "Ledger entry arithmetic guidance. " * 30 + "\n"
    for i in range(30)
)


def _architect(cfg):
    return ArchitectAgent("architect", None)


def test_a_30_entry_reference_at_16k_is_fitted_into_the_architect_request_not_refused(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, 16384, reference=THIRTY)
    assert run.refused("Architect Agent") == []
    [(system, user)] = run.transport.by("Architect Agent")
    [fit] = run.fits("architect")
    assert fit["reference"] is not None and fit["reference"]["used_tokens"] <= fit["reference"]["room_tokens"]
    capacity = budget.agent_request_capacity(run.cfg, _architect(run.cfg), "architect")
    assert capacity.count(system + user) <= capacity.tokens
    # The mandatory text is whole: the plan and the repository model.
    assert user.startswith("Plan:\n") and "Workspace Context:" in user
    # The fence is whole or absent; entries are cut whole, never mid-entry.
    assert user.count(UNTRUSTED_REFERENCE_BEGIN) == user.count(UNTRUSTED_REFERENCE_END) <= 1
    assert 0 < user.count("[Source: ") < 30


def test_at_32k_the_architect_request_is_unchanged(tmp_path, monkeypatch):
    from kriya.workflow.untrusted_context import fence_untrusted_reference

    run = _run(tmp_path, monkeypatch, 32768, reference=REFERENCE)
    [(_, user)] = run.transport.by("Architect Agent")
    assert fence_untrusted_reference(REFERENCE) in user
    assert run.fits("architect") == []


# --- DEVELOPER-AUX-LOOP-PROMPT-FIT-001 ---------------------------------------------

def _loop_config(window):
    cfg = chaos_config()
    cfg.llm.context_window = window
    cfg.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    return cfg


def _deps(workspace):
    async def no_hits(query):
        return []

    return InvestigationDependencies(
        workspace_path=str(workspace), worktree_path=None,
        dependency_graph_db_path=str(Path(workspace) / ".kriya" / "no-graph.db"),
        search_code=no_hits, source_cache=SourceDerivationCache(),
    )


def _measured(capacity, request):
    """What the dispatch check counts for this request beyond RequestCapacity's
    two-message framing (context_budget.conversation_tokens)."""
    return budget.conversation_tokens(capacity, request.messages, request.tools)


def test_the_investigation_loop_fits_its_first_request_and_stops_before_an_unfittable_turn(tmp_path):
    workspace = git_workspace(tmp_path, {"big.py": "".join(f"def f{i}():\n    return {i}\n" for i in range(400))})
    graph = "=== Graph context ===\n" + "context line about the ledger\n" * 3000
    replies = [ChatResponse(content="", finish_reason="tool_calls", tool_calls=[
        RawToolCall(f"c{i}", "inspect_member", json.dumps({"path": "big.py"}))]) for i in range(20)]
    queue = list(replies)
    runtime = ChaosRuntime(lambda role, request: queue.pop(0) if queue else "Ready to implement.")
    cfg = _loop_config(8192)
    sections = [budget.OptionalSection("graph_context", graph, lambda units: graph[: units * 4])]
    with RuntimeRegistration(runtime):
        fit = budget.DeveloperRequestFit(cfg, None, sections)
        capacity = fit.capacity()
        result = asyncio.run(run_investigation_loop(
            llm=LLMClient(cfg), capabilities=cfg.llm.capabilities, deps=_deps(workspace),
            task_description="add a ledger total", design_context="d", existing_code_context=graph,
            max_turns=20, known_target_files=["big.py"], request_fit=fit,
        ))
    assert runtime.requests, "the loop never sent a request"
    assert all(_measured(capacity, request) <= capacity.tokens for request in runtime.requests)
    assert len(runtime.requests[0].messages[1]["content"]) < len(graph)  # the graph context was fitted
    # The growing transcript ends the loop before an unfittable turn is sent.
    assert result.terminal_reason == "REQUEST_FULL"


def test_the_investigation_loop_without_a_fit_is_unchanged(tmp_path):
    workspace = git_workspace(tmp_path, {"a.py": "def f():\n    return 1\n"})
    runtime = ChaosRuntime(lambda role, request: "Ready to implement.")
    cfg = _loop_config(32768)
    with RuntimeRegistration(runtime):
        result = asyncio.run(run_investigation_loop(
            llm=LLMClient(cfg), capabilities=cfg.llm.capabilities, deps=_deps(workspace),
            task_description="t", design_context="d", existing_code_context="ctx", max_turns=3,
        ))
    assert result.terminal_reason == "PROPOSE" and "ctx" in runtime.requests[0].messages[1]["content"]


def test_the_self_correction_loop_never_sends_an_unfittable_turn(tmp_path):
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow.self_correction import run_self_correction_loop

    workspace = git_workspace(tmp_path, {"big.py": "".join(f"VALUE_{i} = {i}  # padding padding padding\n" for i in range(600))})
    replies = [ChatResponse(content="", finish_reason="tool_calls", tool_calls=[
        RawToolCall(f"r{i}", "read_file", json.dumps({"filepath": "big.py"}))]) for i in range(8)]
    queue = list(replies)
    runtime = ChaosRuntime(lambda role, request: queue.pop(0) if queue else "Done.")
    cfg = _loop_config(8192)
    with RuntimeRegistration(runtime):
        capacity = budget.request_capacity(cfg)
        result = asyncio.run(run_self_correction_loop(
            llm=LLMClient(cfg), worktree_path=str(workspace), validator=PolymorphicValidator(str(workspace)),
            files_in_scope=["big.py"], compile_error_output="big.py:1: error: something is wrong",
            active_code_context="", max_turns=8, request_capacity=capacity,
        ))
    assert runtime.requests
    assert all(_measured(capacity, request) <= capacity.tokens for request in runtime.requests)
    assert result.resolved is False and [i["type"] for i in result.incidents] == ["request_full"]
    assert len(runtime.requests) < 8  # stopped by the growing transcript, not by the turn budget


def test_the_self_correction_loop_without_a_capacity_is_unchanged(tmp_path):
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow.self_correction import run_self_correction_loop

    workspace = git_workspace(tmp_path, {"a.py": "x = 1\n"})
    runtime = ChaosRuntime(lambda role, request: "Done.")
    cfg = _loop_config(8192)
    with RuntimeRegistration(runtime):
        result = asyncio.run(run_self_correction_loop(
            llm=LLMClient(cfg), worktree_path=str(workspace), validator=PolymorphicValidator(str(workspace)),
            files_in_scope=["a.py"], compile_error_output="a.py:1: error", active_code_context="", max_turns=2,
        ))
    assert result.resolved is False and result.incidents == [] and len(runtime.requests) == 1

"""PROMPT-BUDGET-FIT-001A/B: every variable prompt section is sized from the
room its own request has left:

    request capacity (served window - preferred output - framing - safety
    margin, kriya/workflow/context_budget.py::request_capacity)
    - the request's mandatory fixed text (system prompt, headers, blocks)

001A: the direct Planner's graph context and reference text, and the
enforce structured Planner's reference text on every request. 001B: the
pre-approval, final and `kriya review` Reviewer file batches. The fixed text
is never trimmed: a request whose fixed text alone cannot fit is still
refused by the dispatch check (CONTEXT_BUDGET_UNSATISFIABLE; for the final
review, 001C's typed final_review_refused).

The end-to-end tests run the real WorkflowEngine and LLMClient dispatch
check (only the transport and the runtime probe are stand-ins) against a
real hashing-embedder index of the PRD-027 Java fixture, at an 8K window
with no qualification record (the default 2.5 bytes-per-token bound) and at
32K.
"""
import asyncio
import json
import os
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from test_prd020_milestone_requirements import _probe

from kriya.agents.agent import ReviewerAgent
from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core import token_budget as tb
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.memory.vector import OllamaEmbeddingClient
from kriya.workflow import context_budget as budget
from kriya.workflow.context_certification import DeterministicHashingEmbedder
from kriya.workflow.context_recall_fixtures import JAVA_SHOP
from kriya.workflow.untrusted_context import UNTRUSTED_REFERENCE_BEGIN, fence_untrusted_reference
from kriya.workflow.workflow import WorkflowEngine

EMBEDDER = DeterministicHashingEmbedder()
GOAL = "Cap the discount returned by OrderService.computeDiscount at 50 percent of the order subtotal."
LEDGER = "src/main/java/com/shop/Ledger.java"


def _ledger(methods: int, factor: int) -> str:
    body = "".join(
        f"    public long entry{i}(long amount) {{\n        return amount * {factor} + {i};\n    }}\n\n"
        for i in range(methods)
    )
    return f"package com.shop;\n\npublic class Ledger {{\n{body}}}\n"


# --- the primitive -------------------------------------------------------------

def _capacity(tokens, **kwargs):
    return budget.RequestCapacity(tokens=tokens, **kwargs)


def test_the_section_gets_capacity_minus_the_fixed_text():
    capacity = _capacity(1000)
    fixed = "f" * 1000  # 400 tokens at 2.5 bytes per token
    seen = []
    fit = budget.fit_variable_section(capacity, (fixed,), lambda units: seen.append(units) or "s" * (units * 4))
    assert fit.room_tokens == 600 and seen == [capacity.allocator_units(600)]
    assert not fit.omitted and fit.used_tokens <= 600


def test_no_room_leaves_the_section_out_without_building_it():
    fit = budget.fit_variable_section(_capacity(100), ("f" * 1000,), lambda units: pytest.fail("built"))
    assert fit.omitted and fit.value == "" and fit.room_tokens == 0 and fit.builds == 0


def test_non_ascii_text_is_rebuilt_smaller_until_the_dispatch_count_fits():
    """The builders count len//4, which undercounts non-ASCII text; the
    section is re-measured with the dispatch counter and rebuilt."""
    capacity = _capacity(400)
    fit = budget.fit_variable_section(capacity, (), lambda units: "é" * (units * 4))
    assert fit.builds > 1 and not fit.omitted
    assert capacity.count(fit.value) <= 400


def test_a_section_that_never_fits_is_left_out_after_a_bounded_number_of_builds():
    builds = []
    fit = budget.fit_variable_section(_capacity(400), (), lambda units: builds.append(units) or "x" * 100000)
    assert fit.omitted and fit.value == "" and 1 <= len(builds) <= budget._MAX_SECTION_BUILDS


def test_reference_text_is_trimmed_at_whole_entries_and_kept_byte_identical_when_it_fits():
    entries = "".join(f"\n[Source: s{i} (Fetched: d)]\n" + "fact " * 40 + "\n" for i in range(5))
    assert budget.trim_reference_text(entries, 10**6) == entries
    assert budget.trim_reference_text("no source markers\nat all", 10**6) == "no source markers\nat all"
    two = budget.trim_reference_text(entries, 2 * budget.estimate_tokens(entries) // 5 + 5)
    assert two.count("[Source: ") == 2 and entries.startswith(two)


def test_no_whole_entry_fitting_leaves_the_reference_out():
    reference = "\n[Source: s]\n" + "fact " * 400 + "\n"
    fit = budget.fit_reference_section(_capacity(2000), ("f" * 4000,), reference)
    assert fit.value == "" and fit.omitted


def test_the_fence_is_never_cut():
    reference = "".join(f"\n[Source: s{i}]\n" + "fact " * 40 + "\n" for i in range(5))
    capacity = _capacity(2000)
    whole = budget.fit_reference_section(capacity, (), reference)
    assert whole.value == fence_untrusted_reference(reference)
    tight = budget.fit_reference_section(capacity, ("f" * 4200,), reference)
    assert tight.value.startswith(f"\n\n{UNTRUSTED_REFERENCE_BEGIN}\n")
    assert 0 < tight.value.count("[Source: ") < 5 and capacity.count(tight.value) <= tight.room_tokens


def test_review_batches_are_bounded_by_the_file_count_even_with_little_room():
    capacity = _capacity(900)
    files = [(f"f{i}.py", "x = 1  # padding line\n" * 40) for i in range(6)]
    fit = budget.fit_review_batches(capacity, files, "s" * 1500)
    batches, truncated = fit.value
    assert 0 < len(batches) <= len(files)
    assert all(capacity.count("s" * 1500 + batch) <= 900 for batch in batches)
    assert truncated  # each file only as much as fits


def test_a_reviewer_with_no_room_still_sends_its_fixed_text_once():
    """The files are left out (named in the note), never forced in; the
    fixed text goes out in one request so it is reviewed, or refused by the
    dispatch check if even it does not fit."""
    cfg = AppConfig()
    cfg.llm.context_window = 8192
    cfg.llm.extra_body = {}
    reviewer = ReviewerAgent("reviewer", None)
    batches, truncated, fit = budget.review_batches_for_request(
        cfg, reviewer, [("a.py", "a = 1\n"), ("b.py", "b = 2\n")], reviewer.system_prompt, "h" * 8000)
    assert batches == [budget.REVIEW_FILES_OMITTED_NOTE] and truncated == ["a.py", "b.py"] and fit.omitted


def test_a_reviewer_binding_and_its_output_budget_size_its_own_requests():
    from kriya.config import LLMConfig

    cfg = AppConfig()
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    reviewer = ReviewerAgent("reviewer", None, role_llm=LLMConfig(model="small-reviewer", context_window=8192))
    assert budget.agent_request_capacity(cfg, reviewer, "reviewer").tokens < \
        budget.agent_request_capacity(cfg, ReviewerAgent("reviewer", None), "reviewer").tokens


def test_the_request_capacity_reserves_the_output_and_the_reasoning_floor():
    from kriya.core.llm import REASONING_MIN_MAX_TOKENS

    cfg = AppConfig()
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    framing = tb.TWO_MESSAGE_FRAMING_TOKENS + tb.DISPATCH_SAFETY_MARGIN_TOKENS
    assert budget.request_capacity(cfg, output_tokens=2048).tokens == 32768 - 2048 - framing
    cfg.llm.reasoning = True
    assert budget.request_capacity(cfg, output_tokens=2048).tokens == \
        32768 - min(REASONING_MIN_MAX_TOKENS, 16384) - framing


# --- end to end: the real engine and dispatch check ------------------------------

REPORT = "src/main/java/com/shop/LedgerReport.java"


class Transport:
    """LLMClient._request_once stand-in recording every request that passed
    the dispatch check; the Developer writes REPORT (a new file, so no
    brownfield patch mandate applies)."""

    def __init__(self, report_content):
        self.requests = []
        self.report_content = report_content

    async def __call__(self, client, model, system_prompt, user_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        self.requests.append((first, system_prompt or "", user_prompt or ""))
        if "File List Planner" in first:
            content = json.dumps({"files": [REPORT]})
        elif "Planner Agent" in first:
            content = f"Step 1: create {REPORT}"
        elif "Developer Agent" in first:
            content = self.report_content
        else:
            content = "Review: Approved"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    def by(self, *markers):
        return [(system, user) for first, system, user in self.requests if any(m in first for m in markers)]


def _workspace_with_index(tmp_path, cfg, ledger_methods):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for path, content in JAVA_SHOP.files + ((LEDGER, _ledger(ledger_methods, 3)),):
        full = workspace / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"],
                 ["add", "."], ["commit", "-qm", "seed"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    from kriya.analyzer.analyzer import RepositoryAnalyzer

    asyncio.run(RepositoryAnalyzer(str(workspace)).index_repository(
        cfg, force=True, embedding_client=EMBEDDER, generate_conventions_skill=False))
    return workspace


def _config(tmp_path, window):
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = window
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    return cfg


class Run:
    """One real direct run: every request that reached the transport, every
    dispatch refusal (by the requesting agent's system prompt) and every run
    event."""

    def __init__(self, cfg, transport, result, refusals, events):
        self.cfg, self.transport, self.result = cfg, transport, result
        self.refusals, self.events = refusals, events

    def fits(self, request):
        return [event.details for event in self.events
                if event.kind == "context.request_fit" and event.details.get("request") == request]

    def refused(self, *markers):
        return [first for first in self.refusals if any(m in first for m in markers)]


# The Reviewer's normal and rejected-candidate system prompts.
REVIEWER = ("Reviewer Agent", "REJECTED candidate")
GOAL = f"Create {REPORT}: a LedgerReport class that sums the Ledger entry methods of Ledger for an amount."


def _run(tmp_path, monkeypatch, window, *, ledger_methods=40, report_methods=40, reference="", compile_error=None):
    from kriya.workflow.state import GenerationState

    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe)
    model_runtime.clear_model_runtime_cache()
    cfg = _config(tmp_path, window)
    workspace = _workspace_with_index(tmp_path, cfg, ledger_methods)
    transport = Transport(_ledger(report_methods, 7).replace("class Ledger", "class LedgerReport"))
    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    refusals, events = [], []
    real_budget, real_record = LLMClient._dispatch_budget, GenerationState.record_event
    compiled = {"success": compile_error is None, "output": compile_error or "ok"}

    def budget_spy(client, **kwargs):
        try:
            return real_budget(client, **kwargs)
        except tb.ContextBudgetUnsatisfiableError:
            refusals.append(str(kwargs["messages"][0].get("content") or "").splitlines()[0])
            raise

    def record_spy(state, event):
        events.append(event)
        return real_record(state, event)

    async def embed(_client, text, client=None, is_query=False):
        del client, is_query
        return await EMBEDDER.get_embedding(text)

    with patch.object(OllamaEmbeddingClient, "get_embedding", new=embed), \
         patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(LLMClient, "_dispatch_budget", new=budget_spy), \
         patch.object(GenerationState, "record_event", new=record_spy), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: dict(compiled)), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               new=lambda *a, **k: {"success": True, "output": "ok"}):
        result = asyncio.run(engine.run_generation_workflow(
            goal=GOAL, workspace_path=str(workspace), reference_context=reference,
            approval_callback=AsyncMock(return_value=True)))
    return Run(cfg, transport, result, refusals, events)


REFERENCE = "".join(
    f"\n[Source: https://docs.example/{i} (Fetched: 2026-09-27)]\n" + "Ledger entry arithmetic guidance. " * 30 + "\n"
    for i in range(5)
)
# A long compile error: the final review of a failing candidate carries it in
# its mandatory header.
COMPILE_ERROR = "".join(f"[ERROR] LedgerReport.java:[{i},9] cannot find symbol: entry{i}\n" for i in range(60))
# Long enough that the header and system prompt alone exceed an 8K window.
UNFITTABLE_COMPILE_ERROR = "".join(
    f"[ERROR] LedgerReport.java:[{i},9] cannot find symbol: entry{i}\n" for i in range(160))


def _planner(cfg):
    from kriya.agents.agent import PlannerAgent

    return PlannerAgent("planner", None, max_output_tokens=cfg.llm.planner_max_tokens)


def test_the_harness_baseline_succeeds_at_8k(tmp_path, monkeypatch):
    """Positive control: without reference text or failures the 8K run
    completes, reviewed, with nothing refused."""
    run = _run(tmp_path, monkeypatch, 8192)
    assert run.refusals == [] and run.result.get("quality_gates_passed") is True
    assert run.transport.by("Planner Agent") and run.transport.by(*REVIEWER)


def test_at_8k_the_planner_leaves_its_optional_context_out_and_is_sent(tmp_path, monkeypatch):
    """001A: the Planner's own 10K-character system prompt already fills its
    preferred room at 8K (planner_max_tokens is reserved), so the graph
    context and the reference are left out, recorded - never forced in.
    Before the fix the 0.60 graph pool ignored that system prompt and the
    dispatch check refused the Planner request."""
    run = _run(tmp_path, monkeypatch, 8192, ledger_methods=120, reference=REFERENCE)
    assert run.refused("Planner Agent") == []
    [(_, user)] = run.transport.by("Planner Agent")
    [fit] = run.fits("planner")
    assert fit["graph"]["omitted"] and fit["reference"]["omitted"]
    assert UNTRUSTED_REFERENCE_BEGIN not in user and "Ledger.java" not in user.split("Workspace Context:")[0]


def test_at_16k_the_planner_graph_context_is_rebuilt_to_its_room(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, 16384, ledger_methods=160)
    assert run.refused("Planner Agent") == []
    [fit] = run.fits("planner")
    assert not fit["graph"]["omitted"] and fit["graph"]["used_tokens"] <= fit["graph"]["room_tokens"]
    [(system, user)] = run.transport.by("Planner Agent")
    capacity = budget.agent_request_capacity(run.cfg, _planner(run.cfg), "planner")
    assert capacity.count(system + user) <= capacity.tokens


def test_at_32k_the_planner_request_is_unchanged(tmp_path, monkeypatch):
    """The room binds only when the request cannot hold the section: at the
    production window the Planner prompt carries the graph context and the
    whole fenced reference, and nothing is recorded."""
    run = _run(tmp_path, monkeypatch, 32768, reference=REFERENCE)
    [(_, user)] = run.transport.by("Planner Agent")
    assert "Ledger" in user and fence_untrusted_reference(REFERENCE) in user
    assert run.fits("planner") == []


def test_at_8k_a_long_gate_error_leaves_the_review_files_out_and_the_review_still_runs(tmp_path, monkeypatch):
    """001B: the final Reviewer's batches used a fixed 0.75 of the
    Developer-shaped window beside a header (the failing candidate's diff,
    the last gate error, gate evidence) and a system prompt that were never
    reserved, so the dispatch check refused the final review
    (final_review_refused at a828a67, the same scenario)."""
    run = _run(tmp_path, monkeypatch, 8192, report_methods=65, compile_error=COMPILE_ERROR)
    assert run.refused(*REVIEWER) == []  # before the fix: refused, final_review_refused
    assert run.result.get("failure_category") == "quality_gates_exhausted"
    [fit] = run.fits("reviewer.final")
    assert fit["truncated_files"] == [REPORT]
    assert run.transport.by(*REVIEWER)


def _within_capacity(run, stage, system_prompt_marker):
    capacity = budget.agent_request_capacity(run.cfg, ReviewerAgent("reviewer", None), "reviewer")
    requests = run.transport.by(system_prompt_marker)
    assert requests
    for system, user in requests:
        assert capacity.count(system + user) <= capacity.tokens, stage


def test_at_8k_the_pre_approval_review_cuts_a_large_file_to_its_room(tmp_path, monkeypatch):
    """With room left after the system prompt and header, the file is cut
    to that room - never to a share that lets optional file content eat the
    preferred output budget. (The diff size escalates this guardrails run
    to approval, so the review is the pre-approval one.)"""
    run = _run(tmp_path, monkeypatch, 8192, report_methods=80)
    assert run.result.get("quality_gates_passed") is True and run.refusals == []
    [fit] = run.fits("reviewer.pre_approval")
    assert fit["truncated_files"] == [REPORT] and fit["room_tokens"] > 0 and not fit["omitted"]
    _within_capacity(run, "pre_approval", "Reviewer Agent")


def test_at_16k_the_final_review_of_a_failing_candidate_cuts_the_file_to_its_room(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, 16384, report_methods=200, compile_error=COMPILE_ERROR)
    assert run.refused(*REVIEWER) == []
    [fit] = run.fits("reviewer.final")
    assert fit["truncated_files"] == [REPORT] and fit["room_tokens"] > 0 and not fit["omitted"]
    _within_capacity(run, "final", "Reviewer Agent")


def test_at_32k_the_review_carries_every_file_whole(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, 32768, report_methods=45)
    reviews = run.transport.by(*REVIEWER)
    assert reviews and all(REPORT in user and "TRUNCATED" not in user for _, user in reviews)
    assert run.fits("reviewer.final") == []


def test_a_final_review_whose_fixed_text_alone_cannot_fit_ends_typed(tmp_path, monkeypatch):
    """End to end: the files are left out, and the mandatory text (system
    prompt and the gate-error header) alone is still over the window, so the
    dispatch check refuses and 001C's typed terminal result holds."""
    run = _run(tmp_path, monkeypatch, 8192, report_methods=120, compile_error=UNFITTABLE_COMPILE_ERROR)
    [fit] = run.fits("reviewer.final")
    assert fit["omitted"] and fit["room_tokens"] == 0
    assert run.result.get("failure_category") == "final_review_refused"
    assert run.result["final_review_refusal"]["reason_code"] == tb.CONTEXT_BUDGET_UNSATISFIABLE


def test_a_review_whose_fixed_text_alone_cannot_fit_is_still_refused(monkeypatch):
    """Genuinely unsatisfiable: the fixed text alone is over the window. It
    is never trimmed to pass - the request built by the primitive goes out
    and the real dispatch check refuses it before inference
    (CONTEXT_BUDGET_UNSATISFIABLE; the final review turns that into 001C's
    typed final_review_refused, tests/test_prompt_budget_fit_001c.py)."""
    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe)
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.context_window = 8192
    cfg.llm.extra_body = {}
    reviewer = ReviewerAgent("reviewer", LLMClient(cfg))
    header = "Goal: x\n" + UNFITTABLE_COMPILE_ERROR * 2
    batches, _, fit = budget.review_batches_for_request(cfg, reviewer, [("a.py", "a = 1\n")],
                                                        reviewer.system_prompt, header)
    assert fit.omitted and batches == [budget.REVIEW_FILES_OMITTED_NOTE]
    create = AsyncMock()
    with patch.object(reviewer.llm.client.chat.completions, "create", new=create):
        with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refusal:
            asyncio.run(reviewer.llm.complete(reviewer.system_prompt, header + batches[0]))
    create.assert_not_called()
    assert refusal.value.reason_code == tb.CONTEXT_BUDGET_UNSATISFIABLE


# --- the Developer's learned reference (Fix of a314d45) -------------------------

def test_learned_reference_never_makes_a_developer_request_fail_at_8k(tmp_path, monkeypatch):
    """a314d45 routed reference_context into the Developer's reserved slot,
    but a reservation never trims: at 8K the reference pushed the larger
    REPAIR-mode Developer requests past the window (4 refusals at 8892e3c in
    this scenario). It now takes at most what the graph pool leaves, so the
    run ends exactly as the same run without it."""
    reference = "".join(
        f"\n[Source: https://docs.example/{i} (Fetched: 2026-09-27)]\n" + "Ledger entry arithmetic guidance. " * 30 + "\n"
        for i in range(10)
    )
    gate_error = "[ERROR] LedgerReport.java:[3,9] cannot find symbol: total\n"
    (tmp_path / "plain").mkdir()
    (tmp_path / "learned").mkdir()
    plain = _run(tmp_path / "plain", monkeypatch, 8192, compile_error=gate_error)
    learned = _run(tmp_path / "learned", monkeypatch, 8192, compile_error=gate_error, reference=reference)
    # No request of any role is refused: the Planner refits the reference,
    # the Developer trims it, and the Architect carries it within its window.
    assert plain.refusals == [] and learned.refusals == []
    assert learned.result.get("failure_category") == plain.result.get("failure_category") == "quality_gates_exhausted"
    assert all(user.count(UNTRUSTED_REFERENCE_BEGIN) == 1 for _, user in learned.transport.by("Developer Agent"))


def test_at_32k_the_developer_carries_the_whole_reference(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, 32768, reference=REFERENCE)
    developer_prompts = [user for _, user in run.transport.by("Developer Agent")]
    assert developer_prompts and all(fence_untrusted_reference(REFERENCE) in user for user in developer_prompts)


def test_developer_reference_keeps_whole_entries_inside_the_pool():
    fenced = fence_untrusted_reference(REFERENCE)
    assert budget.developer_reference(10**6, fenced) is fenced
    trimmed = budget.developer_reference(2385, fenced, "p" * 1000)
    assert trimmed.startswith(f"\n\n{UNTRUSTED_REFERENCE_BEGIN}\n") and 0 < trimmed.count("[Source: ") < 5
    pool = int(2385 * 0.60) - 250 - min(1000, int(2385 * 0.15))
    assert budget.estimate_tokens(trimmed) <= pool
    assert budget.developer_reference(600, fenced, "p" * 1000) == ""
    assert budget.developer_reference(2385, "") == ""


# --- enforce: the structured Planner's reference, on every request --------------

def _enforce(tmp_path, window, reference):
    from test_auth_goal_contamination_001 import _plan_copying
    from test_workflow_controller import _workflow_engine

    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.workflow_controller import WorkflowController

    engine = _workflow_engine()
    engine.kernel.config.llm.context_window = window
    engine.kernel.config.llm.extra_body = {}
    engine.planner.role_llm = None
    engine.planner.run = AsyncMock(return_value="structured plan")
    plan = _plan_copying("ok")

    async def generation(**kwargs):
        with open(os.path.join(kwargs["workspace_path"], "a.py"), "w", encoding="utf-8") as handle:
            handle.write("# generated\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}

    engine.run_generation_workflow = AsyncMock(side_effect=generation)
    verdicts = [PlanValidationResult(valid=False, errors=["unknown invariant id"], reason_codes=["UNKNOWN_INVARIANT"])] \
        + [PlanValidationResult(valid=True)] * 3
    workspace = tmp_path / "enforce"
    workspace.mkdir()
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(side_effect=verdicts)):
        result = asyncio.run(WorkflowController(engine).execute(
            "Update a.py.", str(workspace), migration_mode="enforce", reference_context=reference))
    capacity = budget.agent_request_capacity(engine.kernel.config, engine.planner, "planner",
                                             output_tokens=engine.kernel.config.llm.planner_max_tokens)
    return [call.args[0] for call in engine.planner.run.await_args_list], capacity, result


def test_the_enforce_planner_fits_its_reference_on_the_first_and_every_repair_request(tmp_path):
    from kriya.workflow.workflow_controller import AUTHORITATIVE_PLANNER_SYSTEM_PROMPT

    reference = REFERENCE * 8
    requests, capacity, result = _enforce(tmp_path, 12288, reference)
    assert len(requests) == 2  # the first request and one repair round
    for request in requests:
        assert UNTRUSTED_REFERENCE_BEGIN in request
        assert 0 < request.count("[Source: ") < reference.count("[Source: ")
        assert capacity.count(AUTHORITATIVE_PLANNER_SYSTEM_PROMPT + request) <= capacity.tokens
    assert result.legacy_result.get("quality_gates_passed") is True


def test_the_enforce_planner_leaves_a_reference_with_no_room_out(tmp_path):
    requests, _, result = _enforce(tmp_path, 4096, REFERENCE)
    assert requests and all(UNTRUSTED_REFERENCE_BEGIN not in request for request in requests)
    assert result.legacy_result.get("quality_gates_passed") is True

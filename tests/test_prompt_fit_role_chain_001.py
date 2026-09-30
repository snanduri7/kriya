"""PROMPT-FIT-ROLE-CHAIN-001: a Planner or Reviewer request is fitted for the
model that actually receives it. When a role's first model fails and its
role chain escalates, the fallback's request is rebuilt (context_budget.
CandidatePrompts) from THAT model's own RequestCapacity - its served
window, the output that exact call asks for (agent.candidate_output_tokens,
the rule call_with_escalation itself uses) and its token counter - never
the package fitted for the first model. A fallback may carry less optional
context (graph context, reference text, review file contents); its
mandatory text (system prompt, goal, requirements, review header) is never
trimmed, so a fallback that cannot hold it is refused before inference
(CONTEXT_BUDGET_UNSATISFIABLE) exactly as before.

End to end through the real WorkflowEngine, role escalation and LLMClient
dispatch check (transport and runtime probe are stand-ins) on the
hashing-embedder index of the PRD-027 Java fixture.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from _provider_usage import plausible_prompt_tokens
from click.testing import CliRunner
from test_prd020_milestone_requirements import _probe
from test_prompt_budget_fit_001ab import GOAL, REFERENCE, REPORT, _config, _ledger, _workspace_with_index

from kriya.agents.agent import PlannerAgent, ReviewerAgent, call_with_escalation, candidate_output_tokens
from kriya.config import AppConfig
from kriya.config.config import AgentModelConfig, FallbackModelConfig
from kriya.core import model_runtime
from kriya.core import token_budget as tb
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.memory.vector import OllamaEmbeddingClient
from kriya.workflow import context_budget as budget
from kriya.workflow.context_certification import DeterministicHashingEmbedder
from kriya.workflow.state import GenerationState
from kriya.workflow.untrusted_context import UNTRUSTED_REFERENCE_BEGIN
from kriya.workflow.workflow import WorkflowEngine

EMBEDDER = DeterministicHashingEmbedder()
PRIMARY = "dev-model"
FALLBACK = "small-model"


class Transport:
    """LLMClient._request_once stand-in: records (model, agent, system, user)
    of every request that passed the dispatch check; the role's first model
    fails with a server error for the agents named in ``fail``."""

    def __init__(self, report_content, fail=()):
        self.requests = []
        self.report_content = report_content
        self.fail = fail

    async def __call__(self, client, model, system_prompt, user_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        self.requests.append((model, first, system_prompt or "", user_prompt or ""))
        if model == PRIMARY and any(marker in first for marker in self.fail):
            raise RuntimeError("inference server error")
        if "File List Planner" in first:
            content = json.dumps({"files": [REPORT]})
        elif "Planner Agent" in first:
            content = f"Step 1: create {REPORT}"
        elif "Developer Agent" in first:
            content = self.report_content
        else:
            content = "Review: Approved"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": plausible_prompt_tokens(system_prompt, user_prompt), "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    def by(self, model, *markers):
        return [(system, user) for m, first, system, user in self.requests
                if m == model and any(marker in first for marker in markers)]


class Run:
    def __init__(self, cfg, transport, result, error, refusals, events):
        self.cfg, self.transport, self.result, self.error = cfg, transport, result, error
        self.refusals, self.events = refusals, events

    def fits(self, request, model):
        return [event.details for event in self.events if event.kind == "context.request_fit"
                and str(event.details.get("request", "")).startswith(request)
                and event.details.get("model") == model]


def _run(tmp_path, monkeypatch, *, primary_window, role, fallback_window, fail=(), report_methods=40,
         reference="", compile_error=None, risk_threshold_lines=None):
    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe)
    model_runtime.clear_model_runtime_cache()
    cfg = _config(tmp_path, primary_window)
    if risk_threshold_lines is not None:
        cfg.autonomy.risk_threshold_lines = risk_threshold_lines
    setattr(cfg.agent_llms, role, AgentModelConfig(llm_chain=[
        FallbackModelConfig(model=FALLBACK, context_window=fallback_window)]))
    workspace = _workspace_with_index(tmp_path, cfg, 40)
    transport = Transport(_ledger(report_methods, 7).replace("class Ledger", "class LedgerReport"), fail)
    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    refusals, events = [], []
    real_budget, real_record = LLMClient._dispatch_budget, GenerationState.record_event
    compiled = {"success": compile_error is None, "output": compile_error or "ok"}

    def budget_spy(client, **kwargs):
        try:
            return real_budget(client, **kwargs)
        except tb.ContextBudgetUnsatisfiableError:
            refusals.append((kwargs["model"], str(kwargs["messages"][0].get("content") or "").splitlines()[0]))
            raise

    def record_spy(state, event):
        events.append(event)
        return real_record(state, event)

    async def embed(_client, text, client=None, is_query=False):
        del client, is_query
        return await EMBEDDER.get_embedding(text)

    result, error = None, None
    with patch.object(OllamaEmbeddingClient, "get_embedding", new=embed), \
         patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(LLMClient, "_dispatch_budget", new=budget_spy), \
         patch.object(GenerationState, "record_event", new=record_spy), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: dict(compiled)), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               new=lambda *a, **k: {"success": True, "output": "ok"}):
        try:
            result = asyncio.run(engine.run_generation_workflow(
                goal=GOAL, workspace_path=str(workspace), reference_context=reference,
                approval_callback=AsyncMock(return_value=True)))
        except tb.ContextBudgetUnsatisfiableError as refusal:
            error = refusal
    return Run(cfg, transport, result, error, refusals, events)


def _capacity(cfg, role, model, override=None):
    candidate = next((c for c in getattr(cfg.agent_llms, role).llm_chain if c.model == model), None)
    return budget.candidate_request_capacity(cfg, candidate, role, max_tokens_override=override)


def _within(capacity, system, user):
    return capacity.count(system) + capacity.count(user) <= capacity.tokens


PLANNER = ("Planner Agent",)
REVIEWER = ("Reviewer Agent", "REJECTED candidate")


# --- the output rule: the sizing reserves what the call asks for ------------------

def test_the_output_a_candidate_is_sized_for_is_the_output_its_call_asks_for():
    cfg = AppConfig()
    cfg.llm.max_tokens = 3000
    small = FallbackModelConfig(model=FALLBACK, max_tokens=2000)
    large = FallbackModelConfig(model="large", max_tokens=12000)
    sent = []

    class LLM:
        config = cfg
        model = PRIMARY

        async def complete(self, system, prompt, **kwargs):
            sent.append((kwargs.get("model_override"), kwargs.get("max_tokens_override"), prompt))
            raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        asyncio.run(call_with_escalation(LLM(), "s", lambda c: f"for {c.model if c else PRIMARY}",
                                         [None, small, large], max_tokens_override=8192))
    assert sent == [(None, 8192, f"for {PRIMARY}"), (FALLBACK, 2000, f"for {FALLBACK}"), ("large", 8192, "for large")]
    for candidate, (_, asked, _) in zip([None, small, large], sent, strict=True):
        assert candidate_output_tokens(cfg, candidate, 8192) == asked
    assert candidate_output_tokens(cfg, None, None) == 3000


def test_candidate_prompts_are_built_lazily_once_per_candidate_for_its_own_capacity():
    cfg = AppConfig()
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    small = FallbackModelConfig(model=FALLBACK, context_window=8192, max_tokens=6000)
    planner = PlannerAgent("planner", None, role_chain=[small], max_output_tokens=2048)
    seen = []

    def build(capacity, candidate):
        seen.append((candidate_model_name(candidate), capacity.tokens))
        return f"{capacity.tokens}"

    prompts = budget.CandidatePrompts(cfg, planner, "planner", build)
    assert prompts.first() == prompts(None) and len(seen) == 1  # memoized; fallback not built yet
    assert int(prompts(small)) < int(prompts(None)) and len(seen) == 2
    # The fallback's own window, with the 2048 the call asks it for (its
    # own 6000 clamped by the Planner's ceiling), not its default budget.
    framing = tb.TWO_MESSAGE_FRAMING_TOKENS + tb.DISPATCH_SAFETY_MARGIN_TOKENS
    assert seen == [(PRIMARY, 32768 - 2048 - framing), (FALLBACK, 8192 - 2048 - framing)]


def candidate_model_name(candidate):
    return candidate.model if candidate is not None else PRIMARY


# --- Planner ------------------------------------------------------------------------

def test_a_smaller_planner_fallback_gets_a_request_refitted_to_its_own_room(tmp_path, monkeypatch):
    """Primary 32K fails; the 16K fallback's request is rebuilt for 16K
    (reference trimmed to its room) and reaches the model. Before the fix
    the 32K package went to the fallback and the dispatch check refused
    it, so the run raised."""
    run = _run(tmp_path, monkeypatch, primary_window=32768, role="planner", fallback_window=12288,
               fail=PLANNER, reference=REFERENCE * 6)
    assert run.error is None and run.refusals == []
    (primary_system, primary_user), = run.transport.by(PRIMARY, *PLANNER)
    (system, user), = run.transport.by(FALLBACK, *PLANNER)
    override = run.cfg.llm.planner_max_tokens
    assert not _within(_capacity(run.cfg, "planner", FALLBACK, override), primary_system, primary_user)
    assert _within(_capacity(run.cfg, "planner", FALLBACK, override), system, user)
    # Optional context shrank; the mandatory goal and requirement text did not.
    assert user.count("[Source: ") < primary_user.count("[Source: ")
    assert GOAL in user and "REQ-1" in user
    assert run.fits("planner", FALLBACK) and not run.fits("planner", PRIMARY)
    assert run.result.get("quality_gates_passed") is True


def test_a_larger_planner_fallback_gets_a_request_fitted_to_its_own_larger_room(tmp_path, monkeypatch):
    """Each candidate's request is fitted to its own room: a larger fallback
    carries more of the reference than the first model had room for (the
    graph context was retrieved for the first model and fits both)."""
    run = _run(tmp_path, monkeypatch, primary_window=12288, role="planner", fallback_window=32768,
               fail=PLANNER, reference=REFERENCE * 2)
    (_, primary_user), = run.transport.by(PRIMARY, *PLANNER)
    (system, user), = run.transport.by(FALLBACK, *PLANNER)
    assert primary_user.count("[Source: ") < user.count("[Source: ") == (REFERENCE * 2).count("[Source: ")
    assert _within(_capacity(run.cfg, "planner", FALLBACK, run.cfg.llm.planner_max_tokens), system, user)
    assert run.error is None and run.refusals == []


def test_equal_planner_windows_send_byte_identical_requests(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, primary_window=12288, role="planner", fallback_window=12288,
               fail=PLANNER, reference=REFERENCE * 2)
    assert run.transport.by(PRIMARY, *PLANNER) == run.transport.by(FALLBACK, *PLANNER)
    assert run.error is None


def test_a_planner_fallback_that_cannot_hold_the_mandatory_text_is_refused_typed(tmp_path, monkeypatch):
    """At 4K the Planner system prompt alone exceeds the fallback's window:
    it is never trimmed, the dispatch check refuses the fallback before
    inference and, as the last candidate, the refusal is what the run
    raises (the first model's server error does not mask it)."""
    run = _run(tmp_path, monkeypatch, primary_window=32768, role="planner", fallback_window=4096, fail=PLANNER)
    assert run.transport.by(FALLBACK, *PLANNER) == []
    assert run.refusals and run.refusals[-1][0] == FALLBACK
    assert run.error is not None and run.error.reason_code == tb.CONTEXT_BUDGET_UNSATISFIABLE


# --- Reviewer -----------------------------------------------------------------------

def test_a_smaller_reviewer_fallback_reviews_what_fits_and_names_the_rest(tmp_path, monkeypatch):
    """The first reviewer (32K) holds the 120-method report whole and fails;
    the 8K fallback's review request (the pre-approval review: the diff
    size requires approval) carries what fits of that file, marked
    TRUNCATED, within its own room. Before the fix it was sent the 32K
    batch and refused."""
    run = _run(tmp_path, monkeypatch, primary_window=32768, role="reviewer", fallback_window=8192,
               fail=REVIEWER, report_methods=120)
    [(primary_system, primary_user)] = run.transport.by(PRIMARY, *REVIEWER)
    [(system, user)] = run.transport.by(FALLBACK, *REVIEWER)
    capacity = _capacity(run.cfg, "reviewer", FALLBACK)
    assert not _within(capacity, primary_system, primary_user)
    assert _within(capacity, system, user)
    assert f"Goal: {GOAL}" in user  # the mandatory header is whole
    assert "TRUNCATED" in user or "Not shown in this request" in user
    [refit] = run.fits("reviewer", FALLBACK)
    assert refit["files"] == [REPORT] and refit["truncated_files"] == [REPORT] and refit["batch"] == 1
    assert run.refusals == [] and run.result.get("quality_gates_passed") is True
    assert not run.fits("reviewer", PRIMARY)


def test_the_final_review_is_refitted_for_a_smaller_fallback(tmp_path, monkeypatch):
    """Without an approval step (the diff is under the risk threshold) the
    final review does the work. Its mandatory header (goal, the candidate's
    full diff, evidence) already fills the 8K fallback's room, so the
    fallback's request keeps that header whole and leaves the optional file
    batch out (the dispatch check then reduces its output) - instead of
    being sent the 32K batch and refused (final_review_refused)."""
    run = _run(tmp_path, monkeypatch, primary_window=32768, role="reviewer", fallback_window=8192,
               fail=REVIEWER, report_methods=120, risk_threshold_lines=100000)
    [(_, primary_user)] = run.transport.by(PRIMARY, *REVIEWER)
    [(_, user)] = run.transport.by(FALLBACK, *REVIEWER)
    header = primary_user[:primary_user.index("\n=== File: ")]
    assert user == header + budget.REVIEW_FILES_OMITTED_NOTE
    [refit] = run.fits("reviewer.final", FALLBACK)
    assert refit["omitted"] and refit["not_shown_files"] == [REPORT]
    assert run.refusals == [] and run.result.get("quality_gates_passed") is True
    assert run.result.get("failure_category") != "final_review_refused"


def test_kriya_review_refits_each_batch_for_a_smaller_fallback(tmp_path, monkeypatch):
    from kriya.cli import main

    cfg = AppConfig()
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    small = FallbackModelConfig(model=FALLBACK, context_window=8192)
    cfg.agent_llms.reviewer = AgentModelConfig(llm_chain=[small])
    (tmp_path / "a.py").write_text("a = 1  # first file\n" * 70)
    (tmp_path / "b.py").write_text("b = 2  # second file\n" * 70)
    monkeypatch.chdir(tmp_path)
    sent = []

    async def complete(_llm, system, prompt, **kwargs):
        sent.append((kwargs.get("model_override"), system, prompt))
        if kwargs.get("model_override") is None:
            raise RuntimeError("inference server error")
        return "Looks fine."

    with patch("kriya.cli.load_config", return_value=cfg), patch.object(LLMClient, "complete", new=complete):
        result = CliRunner().invoke(main, ["review", str(tmp_path)])
    assert result.exit_code == 0, result.output
    [(_, _, first)] = [entry for entry in sent if entry[0] is None]
    [(_, system, fallback)] = [entry for entry in sent if entry[0] == FALLBACK]
    assert "b = 2" in first and "b = 2" not in fallback
    assert fallback.endswith(budget.REVIEW_FILES_NOT_SHOWN_NOTE.format(paths="b.py"))
    assert _within(budget.candidate_request_capacity(cfg, small, "reviewer"), system, fallback)


def test_equal_reviewer_windows_send_byte_identical_requests(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, primary_window=32768, role="reviewer", fallback_window=32768,
               fail=REVIEWER, report_methods=120)
    assert run.transport.by(PRIMARY, *REVIEWER) == run.transport.by(FALLBACK, *REVIEWER)
    assert not run.fits("reviewer", FALLBACK)


def test_a_reviewer_fallback_that_cannot_hold_the_header_ends_final_review_refused(tmp_path, monkeypatch):
    """The failing candidate's final review carries its compile error in the
    mandatory header: a 4K fallback cannot hold the header, is refused
    before inference, and 001C's typed final_review_refused ends the run."""
    compile_error = "".join(f"[ERROR] LedgerReport.java:[{i},9] cannot find symbol: entry{i}\n" for i in range(60))
    run = _run(tmp_path, monkeypatch, primary_window=32768, role="reviewer", fallback_window=4096,
               fail=REVIEWER, compile_error=compile_error)
    assert run.transport.by(FALLBACK, *REVIEWER) == []
    assert run.refusals and run.refusals[-1][0] == FALLBACK
    assert run.result["failure_category"] == "final_review_refused"
    assert run.result["final_review_refusal"]["reason_code"] == tb.CONTEXT_BUDGET_UNSATISFIABLE


def test_the_review_fixed_text_is_kept_whole_for_every_candidate():
    """Unit: a candidate with no room for any file of its batch still gets
    the whole header, with the files-omitted note; the first candidate's
    request is unchanged."""
    cfg = AppConfig()
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    small = FallbackModelConfig(model=FALLBACK, context_window=6144)
    reviewer = ReviewerAgent("reviewer", None, role_chain=[small])
    header = "Goal: g\n" + "h" * 4000
    files = [("a.py", "a = 1\n" * 900)]
    [request], truncated, _ = budget.review_requests(cfg, reviewer, files, reviewer.system_prompt, header)
    assert truncated == [] and request.first().startswith(header) and "a = 1" in request.first()
    assert request(small) == header + budget.REVIEW_FILES_OMITTED_NOTE


def test_a_fallback_review_request_names_the_files_of_its_batch_it_could_not_carry():
    cfg = AppConfig()
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    small = FallbackModelConfig(model=FALLBACK, context_window=8192)
    reviewer = ReviewerAgent("reviewer", None, role_chain=[small])
    files = [("a.py", "a = 1  # first file\n" * 200), ("b.py", "b = 2  # second file\n" * 200)]
    [request], _, _ = budget.review_requests(cfg, reviewer, files, reviewer.system_prompt, "Goal: g\n")
    assert "a = 1" in request.first() and "b = 2" in request.first()  # one batch for the first model
    fallback = request(small)
    assert "a = 1" in fallback and "b = 2" not in fallback
    assert fallback.endswith(budget.REVIEW_FILES_NOT_SHOWN_NOTE.format(paths="b.py"))
    capacity = budget.candidate_request_capacity(cfg, small, "reviewer")
    assert _within(capacity, reviewer.system_prompt, fallback)


# --- enforce ---------------------------------------------------------------------------

def test_the_enforce_planner_refits_its_reference_for_each_candidate(tmp_path):
    """The first request and the repair round each carry a CandidatePrompts:
    a 12K fallback's request is refitted to its own room."""
    from kriya.workflow.workflow_controller import AUTHORITATIVE_PLANNER_SYSTEM_PROMPT

    small = FallbackModelConfig(model=FALLBACK, context_window=12288)
    calls, cfg, result = _enforce_calls(tmp_path, 32768, REFERENCE * 8)
    assert result.legacy_result.get("quality_gates_passed") is True and len(calls) == 2
    capacity = budget.candidate_request_capacity(cfg, small, "planner", max_tokens_override=cfg.llm.planner_max_tokens)
    for prompt, prompts in calls:
        assert prompts.first() == prompt
        fallback_prompt = prompts(small)
        assert capacity.count(AUTHORITATIVE_PLANNER_SYSTEM_PROMPT + fallback_prompt) <= capacity.tokens
        assert UNTRUSTED_REFERENCE_BEGIN in fallback_prompt
        assert 0 < fallback_prompt.count("[Source: ") < prompt.count("[Source: ")


def _enforce_calls(tmp_path, window, reference):
    """test_prompt_budget_fit_001ab._enforce, keeping each Planner call's
    first-candidate prompt and its CandidatePrompts."""
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
        with open(f"{kwargs['workspace_path']}/a.py", "w", encoding="utf-8") as handle:
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
    calls = engine.planner.run.await_args_list
    return [(call.args[0], call.kwargs["candidate_prompt"]) for call in calls], engine.kernel.config, result


# --- milestone ----------------------------------------------------------------------------

def test_a_milestone_unit_review_is_refitted_for_a_smaller_reviewer_fallback(tmp_path, monkeypatch):
    """Each milestone unit runs the same per-candidate review: the first
    reviewer (32K) fails on M2's large file, the 8K fallback reviews what
    fits within its own room, and M2 verifies (before the fix M2's review
    was refused: final_review_refused)."""
    from test_prompt_budget_fit_001c import _workspace

    from kriya.cli import main

    workspace = _workspace(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"group_id": "rolechain", "original_goal": "Build m1.py and m2.py.", "milestones": [
        {"id": "M1", "goal": "build M1: create m1.py", "success_criterion": "m1.py exists", "depends_on": []},
        {"id": "M2", "goal": "build M2: create m2.py", "success_criterion": "m2.py exists", "depends_on": ["M1"]},
    ]}))
    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe)
    model_runtime.clear_model_runtime_cache()
    monkeypatch.chdir(workspace)
    cfg = AppConfig()
    cfg.llm.model = PRIMARY
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.agent_llms.reviewer = AgentModelConfig(llm_chain=[FallbackModelConfig(model=FALLBACK, context_window=8192)])
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    large = "".join(f"def entry{i}(amount):\n    return amount * 7 + {i}\n\n\n" for i in range(700))
    requests = []

    async def transport(_llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del client  # patched in as a method: the LLMClient comes first
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        prompt = user_prompt or ""
        target = "m2.py" if "build M2" in prompt or "m2.py" in prompt else "m1.py"
        requests.append((model, first, system_prompt or "", prompt, target))
        if "Reviewer Agent" in first:
            if model == PRIMARY and target == "m2.py":
                raise RuntimeError("inference server error")
            content = "Review: Approved"
        elif "File List Planner" in first:
            content = json.dumps({"files": [target]})
        elif "Planner Agent" in first:
            content = f"Step 1: create {target}"
        else:
            content = large if target == "m2.py" else f"VALUE = '{target}'\n"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": plausible_prompt_tokens(system_prompt, user_prompt), "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    with patch("kriya.cli.load_config", return_value=cfg), patch.object(LLMClient, "_request_once", new=transport):
        cli = CliRunner().invoke(main, ["generate", "--from-milestones", str(plan), "-y"])
    reviews = [(system, prompt) for model, first, system, prompt, target in requests
               if model == FALLBACK and "Reviewer Agent" in first and target == "m2.py"]
    assert reviews, cli.output
    capacity = budget.candidate_request_capacity(cfg, cfg.agent_llms.reviewer.llm_chain[0], "reviewer")
    assert all(_within(capacity, system, prompt) for system, prompt in reviews)
    payload = json.loads(cli.output[cli.output.index("{", cli.output.index("=== Milestone sequence")):])
    # M2's review went to the fallback and M2 verified (before the fix its
    # review was refused: final_review_refused, M2 not VERIFIED).
    assert payload["work_unit_states"]["M2"]["status"] == "VERIFIED", cli.output

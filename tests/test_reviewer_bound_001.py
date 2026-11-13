"""REVIEWER-RUNAWAY-UNBOUNDED-OUTPUT-001: one Reviewer call must not be able to consume a quarter of a run.

MEASURED (run 20261110T064040-fcf2b210, sealed): nine Reviewer reports took 359-856 output tokens (10-32 s); one
repetition loop (`### Code Review` x80) ran to the 16384-token ceiling the Reviewer INHERITED from the Developer binding and
took 597 s, and the provider's `finish_reason=length` fragment was handed on as if it were a finished review (the text API
`LLMClient.complete` returned it with a log line).

Controls here are model-free: the scripted runtime returns a repeating `length` completion exactly as the provider did.
"""
import asyncio
from types import SimpleNamespace

import pytest
from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    plausible_message_tokens,
    run_direct,
)
from _strict_doubles import strict_config

from kriya.agents.agent import (
    REVIEWER_DEFAULT_MAX_OUTPUT_TOKENS,
    ArchitectAgent,
    ReviewerAgent,
    RunVerifierAgent,
    SpecComplianceAgent,
    call_with_escalation,
)
from kriya.core.completion import CompletionProtocolError, CompletionResult, CompletionStatus
from kriya.core.inference_runtime import ChatResponse
from kriya.core.state_paths import trace_db_path
from kriya.metrics.evidence import load_trace_runs

GOAL = "add sub to calc.py"
LOOP = "### Code Review\n\n#### Summary of Findings\n\n* The tests cover standard cases.\n\n" * 400


def _truncated(request, content=LOOP):
    # prompt_tokens must be plausible for the real request, or the provider contract (not the control) refuses it.
    return ChatResponse(content=content, prompt_tokens=plausible_message_tokens(request.messages, request.tools),
                        completion_tokens=REVIEWER_DEFAULT_MAX_OUTPUT_TOKENS, finish_reason="length")


class _SpyLLM:
    """Records exactly the keyword arguments an agent sends to ``complete``."""

    def __init__(self, reply="ok"):
        self.calls = []
        self.model = "m"
        self.config = strict_config()
        self._reply = reply

    async def complete(self, system_prompt, prompt, **kwargs):
        self.calls.append(kwargs)
        reply = self._reply
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _incomplete(status=CompletionStatus.OUTPUT_TRUNCATED):
    return CompletionProtocolError(CompletionResult(status=status, content=LOOP, model="m", finish_reason="length"))


# -- the role's own bound ------------------------------------------------------------------------------------------------

def test_the_reviewer_has_its_own_output_ceiling_far_below_the_developer_budget():
    reviewer = ReviewerAgent("reviewer", _SpyLLM())
    assert reviewer.max_output_tokens == REVIEWER_DEFAULT_MAX_OUTPUT_TOKENS == 4096


def test_a_configured_reviewer_ceiling_wins_over_the_default():
    assert ReviewerAgent("reviewer", _SpyLLM(), max_output_tokens=1024).max_output_tokens == 1024


def test_an_explicit_ceiling_is_taken_as_written_even_above_the_default():
    """The owner's own configured value is never second-guessed; only the built-in default is clamped."""
    assert ReviewerAgent("reviewer", _SpyLLM(), max_output_tokens=9000).max_output_tokens == 9000


def test_the_default_is_a_ceiling_and_never_raises_a_smaller_configured_budget():
    """MEASURED by the focused gate: a 512-token deployment asked the Reviewer for 4096 when the default was an unclamped override."""
    small = _SpyLLM()
    small.config.llm.max_tokens = 512
    assert ReviewerAgent("reviewer", small).max_output_tokens == 512
    large = _SpyLLM()
    large.config.llm.max_tokens = 16384
    assert ReviewerAgent("reviewer", large).max_output_tokens == 4096


def test_the_default_is_clamped_to_the_role_binding_when_the_role_has_its_own_model():
    llm = _SpyLLM()
    llm.config.llm.max_tokens = 16384
    assert ReviewerAgent("reviewer", llm, SimpleNamespace(max_tokens=1000)).max_output_tokens == 1000
    assert ReviewerAgent("reviewer", llm, SimpleNamespace(max_tokens=20000)).max_output_tokens == 4096


def test_no_other_role_inherits_the_reviewers_bound_or_the_completeness_requirement():
    for agent_class in (ArchitectAgent, RunVerifierAgent, SpecComplianceAgent):
        agent = agent_class("x", _SpyLLM())
        assert agent.max_output_tokens is None and agent.REQUIRE_COMPLETE_OUTPUT is False
    assert ReviewerAgent.REQUIRE_COMPLETE_OUTPUT is True


def test_the_reviewer_call_carries_the_ceiling_and_the_completeness_requirement():
    llm = _SpyLLM("Review: fine")
    asyncio.run(ReviewerAgent("reviewer", llm).run("review this"))
    [kwargs] = llm.calls
    assert kwargs["max_tokens_override"] == 4096 and kwargs["require_complete"] is True


def test_another_role_call_keeps_its_exact_shape_without_the_new_keyword():
    llm = _SpyLLM("plan")
    asyncio.run(ArchitectAgent("architect", llm).run("design this"))
    [kwargs] = llm.calls
    assert "require_complete" not in kwargs and "max_tokens_override" not in kwargs


# -- a cut-off answer is never a finished one ----------------------------------------------------------------------------

@pytest.mark.parametrize("status", [CompletionStatus.OUTPUT_TRUNCATED, CompletionStatus.EMPTY_CONTENT])
def test_complete_refuses_an_incomplete_completion_only_when_asked(status):
    from kriya.core.llm import LLMClient

    client = LLMClient.__new__(LLMClient)
    result = CompletionResult(status=status, content=LOOP, model="m", finish_reason="length")

    async def fake_complete_result(*_a, **_k):
        return result

    client.complete_result = fake_complete_result
    with pytest.raises(CompletionProtocolError) as refused:
        asyncio.run(client.complete("s", "u", require_complete=True))
    assert refused.value.reason_code == status.value
    # The compatibility behaviour every other caller relies on is unchanged.
    assert asyncio.run(client.complete("s", "u")) == LOOP


def test_complete_returns_a_complete_completion_normally_when_required():
    from kriya.core.llm import LLMClient

    client = LLMClient.__new__(LLMClient)

    async def fake_complete_result(*_a, **_k):
        return CompletionResult(status=CompletionStatus.OK, content="Review: approved", model="m", finish_reason="stop")

    client.complete_result = fake_complete_result
    assert asyncio.run(client.complete("s", "u", require_complete=True)) == "Review: approved"


def test_a_truncated_primary_falls_through_to_the_role_chain_and_a_fully_truncated_chain_raises():
    class _Candidate(SimpleNamespace):
        pass

    def cand(name):
        return _Candidate(model=name, base_url=None, api_key=None, temperature=None, reasoning=None, extra_body=None,
                          max_tokens=None)

    class Llm(_SpyLLM):
        async def complete(self, system_prompt, prompt, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("model_override") == "good":
                return "Review: fine"
            raise _incomplete()

    llm = Llm()
    assert asyncio.run(call_with_escalation(llm, "s", "p", [None, cand("good")], require_complete=True)) == "Review: fine"
    assert len(llm.calls) == 2 and all(call.get("require_complete") is True for call in llm.calls)   # primary AND chain candidate
    with pytest.raises(CompletionProtocolError) as refused:
        asyncio.run(call_with_escalation(Llm(), "s", "p", [None, cand("bad")], require_complete=True))
    assert refused.value.reason_code == "OUTPUT_TRUNCATED"   # raised, never returned as text


# -- end to end through the real pipeline, model-free -----------------------------------------------------------------

def _runtime(reviewer):
    def responder(role, request):
        if role == "developer":
            return CALC_WITH_SUB
        if role == "reviewer":
            return reviewer(request)
        return benign_roles(role, request)
    return ChaosRuntime(responder)


def _events(cfg, kind):
    return [d for run in load_trace_runs(trace_db_path(cfg)) for d in run.events_of(kind)]


def _approval_run(tmp_path, reviewer):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    cfg = chaos_config(mode="human-in-the-loop")
    seen = []

    def callback(diffs, reason):
        seen.append(reason)
        return True

    runtime = _runtime(reviewer)
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg), GOAL, workspace, approval_callback=callback)
    return cfg, result, seen, runtime


def test_a_repeating_length_stopped_review_is_not_attached_and_is_typed_evidence(tmp_path):
    reviewer_calls = []

    def reviewer(request):
        reviewer_calls.append(request)
        return _truncated(request) if len(reviewer_calls) == 1 else "Review: Approved"

    cfg, result, seen, runtime = _approval_run(tmp_path, reviewer)
    [event] = _events(cfg, "review.pre_approval_unattached")
    assert event["reason_code"] == "OUTPUT_TRUNCATED"
    assert result["review_included_in_approval"] is False
    assert "NOT ATTACHED" in seen[0] and "OUTPUT_TRUNCATED" in seen[0]
    assert "### Code Review" not in seen[0]            # the fragment is never shown as the review
    # The request itself was bounded by the Reviewer's own ceiling, not the Developer binding's.
    reviewer_requests = [r for role, r in zip(runtime.roles, runtime.requests, strict=True) if role == "reviewer"]
    assert reviewer_requests and all(r.max_tokens <= REVIEWER_DEFAULT_MAX_OUTPUT_TOKENS for r in reviewer_requests)


def test_a_normal_review_is_attached_unchanged(tmp_path):
    cfg, result, seen, _ = _approval_run(tmp_path, lambda request: "Review: Approved, looks correct")
    assert result["review_included_in_approval"] is True
    assert "Review: Approved, looks correct" in seen[0] and "NOT ATTACHED" not in seen[0]
    assert _events(cfg, "review.pre_approval_unattached") == []


def test_a_long_valid_review_below_the_ceiling_is_not_truncated(tmp_path):
    long_valid = "\n".join(f"* Finding {i}: file section {i} was checked against the supplied evidence." for i in range(120))
    cfg, result, seen, _ = _approval_run(
        tmp_path, lambda request: ChatResponse(content=long_valid, prompt_tokens=plausible_message_tokens(request.messages, request.tools),
                                               completion_tokens=2500, finish_reason="stop"))
    assert result["review_included_in_approval"] is True and long_valid in seen[0]
    assert _events(cfg, "review.pre_approval_unattached") == []


def test_a_non_repetitive_review_that_hits_the_role_ceiling_is_also_refused(tmp_path):
    """The ceiling governs, not a repetition guess: distinct text cut off at the ceiling is still an unfinished review."""
    distinct = "\n".join(f"* Observation {i}: {'x' * (i % 17)} item {i * 7919}" for i in range(500))
    cfg, result, seen, _ = _approval_run(tmp_path, lambda request: _truncated(request, distinct))
    [event] = _events(cfg, "review.pre_approval_unattached")
    assert event["reason_code"] == "OUTPUT_TRUNCATED" and "Observation 3:" not in seen[0]


def test_a_truncated_final_review_is_the_existing_typed_refusal_not_a_successful_review(tmp_path):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    cfg = chaos_config()
    with RuntimeRegistration(_runtime(lambda request: _truncated(request))):
        result = run_direct(chaos_engine(cfg), GOAL, workspace)
    assert result["quality_gates_passed"] is False and result["failure_category"] == "final_review_refused"
    assert result["final_review_refusal"]["reason_code"] == "OUTPUT_TRUNCATED"
    assert result["candidate_gates_passed"] is True       # what was built is kept and reported, as for any refused review

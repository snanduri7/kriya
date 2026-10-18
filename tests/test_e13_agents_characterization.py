"""E13 characterization (full modularization W4): the agents' decisions, pinned through their public methods
BEFORE kriya/agents/agent.py is split into one module per agent (handover/MODULARIZATION/E13-DECISION-TO-KILLER.md).

Every test drives a real agent over a real AppConfig and a real LLMClient whose ``complete`` is scripted (no model,
no network): the escalation loop and its call shapes, the role output budget, the base agent's forwarding, the
boolean/input-channel coercions, the Planner/Milestone/Architect output handling, the Developer's per-file request
assembly, response-protocol parsing and refusals, the step-1 file list and the single-stage fallback, the
RunVerifier judge/grade verdicts, the SpecCompliance verdicts, the SkillGap extraction and conflict check, and the
Reviewer's structured and rejected-candidate paths. Each decision is pinned with its INVERSE case next to the
positive one. The prompt digests pin the exact text every agent sends (a verbatim move must not change one byte).
The names are imported from the facade ``kriya.agents.agent``: after the split they must be the owners' objects.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from kriya.agents.agent import (
    OUTPUT_TRUNCATED,
    ArchitectAgent,
    DeveloperAgent,
    MilestonePlannerAgent,
    PlannerAgent,
    ReviewerAgent,
    RunVerifierAgent,
    SkillGapAgent,
    SpecComplianceAgent,
    _call_with_escalation,
    _infer_input_channel_from_text,
    call_with_escalation,
    candidate_output_tokens,
    role_output_override,
)
from kriya.agents.contracts import AUTHORITATIVE_GOAL_SECTION_HEADER
from kriya.config import AppConfig, FallbackModelConfig
from kriya.core.completion import CompletionResult, CompletionStatus
from kriya.core.llm import InferenceDeadlineError, LLMClient
from kriya.core.model_runtime import binding_output_tokens
from kriya.core.role_metrics import current_model_role
from kriya.core.token_budget import ContextBudgetUnsatisfiableError, OutputBudgetUnsatisfiableError


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _llm(responses=None, config: Optional[AppConfig] = None) -> LLMClient:
    llm = LLMClient(config or AppConfig())
    llm.complete = AsyncMock(side_effect=list(responses) if responses is not None else None)
    return llm


def _calls(llm: LLMClient) -> List[Any]:
    return list(llm.complete.await_args_list)


class _Metrics:
    def __init__(self):
        self.schema_failures: List[str] = []

    def record_schema_failure(self, model: str) -> None:
        self.schema_failures.append(model)


def _cand(model: str, **kw) -> FallbackModelConfig:
    return FallbackModelConfig(model=model, **kw)


# ---------------------------------------------------------------------------------------------------------------
# Escalation loop
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_run_deadline_is_raised_and_never_escalated_to_the_next_candidate():
    llm = _llm([InferenceDeadlineError("deadline", {}), "fallback answer"])
    with pytest.raises(InferenceDeadlineError):
        await call_with_escalation(llm, "sys", "p", [None, _cand("fb")])
    assert llm.complete.await_count == 1
    # inverse: any other failure escalates to the next candidate
    llm = _llm([ConnectionError("down"), "fallback answer"])
    assert await call_with_escalation(llm, "sys", "p", [None, _cand("fb")]) == "fallback answer"
    assert llm.complete.await_count == 2


@pytest.mark.asyncio
async def test_an_unusable_answer_moves_to_the_next_candidate_and_a_usable_one_stops():
    llm = _llm(["bad", "good"])
    out = await call_with_escalation(llm, "sys", "p", [None, _cand("fb")], is_failure=lambda r: r == "bad")
    assert out == "good" and llm.complete.await_count == 2
    llm = _llm(["good", "unused"])
    out = await call_with_escalation(llm, "sys", "p", [None, _cand("fb")], is_failure=lambda r: r == "bad")
    assert out == "good" and llm.complete.await_count == 1


@pytest.mark.asyncio
async def test_a_schema_failure_is_recorded_against_each_candidates_own_model():
    llm = _llm(["bad", "bad"])
    llm.role_metrics = _Metrics()
    out = await call_with_escalation(llm, "sys", "p", [None, _cand("fb-model")], is_failure=lambda r: True)
    assert out == "bad"
    assert llm.role_metrics.schema_failures == [llm.model, "fb-model"]


@pytest.mark.asyncio
async def test_the_last_answer_wins_over_an_earlier_raise_and_a_later_raise_wins_over_an_earlier_answer():
    llm = _llm([ConnectionError("first"), "unusable"])
    assert await call_with_escalation(llm, "sys", "p", [None, _cand("fb")], is_failure=lambda r: True) == "unusable"
    llm = _llm(["unusable", ConnectionError("second")])
    with pytest.raises(ConnectionError, match="second"):
        await call_with_escalation(llm, "sys", "p", [None, _cand("fb")], is_failure=lambda r: True)


@pytest.mark.asyncio
async def test_exhaustion_by_exception_raises_the_last_exception():
    llm = _llm([ConnectionError("first"), TimeoutError("last")])
    with pytest.raises(TimeoutError, match="last"):
        await call_with_escalation(llm, "sys", "p", [None, _cand("fb")])


@pytest.mark.asyncio
async def test_a_callable_prompt_is_built_for_each_candidate():
    llm = _llm([ConnectionError("x"), "ok"])
    fb = _cand("fb")
    await call_with_escalation(llm, "sys", lambda c: f"prompt-for-{getattr(c, 'model', 'primary')}", [None, fb])
    assert [c.args[1] for c in _calls(llm)] == ["prompt-for-primary", "prompt-for-fb"]


@pytest.mark.asyncio
async def test_the_primary_call_shape_carries_max_tokens_only_when_an_override_is_set():
    llm = _llm(["a", "b"])
    await call_with_escalation(llm, "sys", "p", [None])
    await call_with_escalation(llm, "sys", "p", [None], max_tokens_override=1600, temperature_override=0.3,
                               json_mode=True)
    first, second = _calls(llm)
    assert first.kwargs == {"stream_callback": None, "json_mode": False, "temperature_override": None}
    assert second.kwargs == {"stream_callback": None, "json_mode": True, "temperature_override": 0.3,
                             "max_tokens_override": 1600}


@pytest.mark.asyncio
async def test_an_explicit_candidate_sends_its_own_identity_temperature_and_clamped_budget():
    llm = _llm(["ok"])
    cand = _cand("fb-model", base_url="http://localhost:9/v1", api_key="k", temperature=0.9, max_tokens=4096,
                 reasoning=True, extra_body={"x": 1})
    await call_with_escalation(llm, "sys", "p", [cand], temperature_override=0.1, max_tokens_override=1600)
    kwargs = _calls(llm)[0].kwargs
    assert kwargs["model_override"] == "fb-model"
    assert kwargs["base_url_override"] == "http://localhost:9/v1"
    assert kwargs["api_key_override"] == "k"
    assert kwargs["temperature_override"] == 0.9          # the candidate's own temperature wins
    assert kwargs["max_tokens_override"] == 1600          # clamped by the role override
    assert kwargs["reasoning_override"] is True
    assert kwargs["extra_body_override"] == {"x": 1}


def test_candidate_output_tokens_clamps_never_raises_and_the_primary_takes_the_override():
    cfg = AppConfig()
    cand = _cand("fb", max_tokens=4096)
    assert candidate_output_tokens(cfg, cand, 1600) == 1600
    assert candidate_output_tokens(cfg, cand, 8000) == 4096   # never raised above the candidate's own budget
    assert candidate_output_tokens(cfg, cand, None) == 4096
    assert candidate_output_tokens(cfg, None, 1600) == 1600
    assert candidate_output_tokens(cfg, None, None) == binding_output_tokens(cfg, None)


def test_role_output_override_prefers_the_call_then_the_agent_and_drops_non_ints():
    class _Agent:
        max_output_tokens = 500

    assert role_output_override(_Agent(), 300) == 300
    assert role_output_override(_Agent(), None) == 500
    _Agent.max_output_tokens = "500"
    assert role_output_override(_Agent(), None) is None
    assert role_output_override(object(), None) is None


@pytest.mark.asyncio
async def test_every_call_is_attributed_to_the_agent_role():
    seen = []

    async def complete(*_a, **_k):
        seen.append(current_model_role())
        return "ok"

    llm = _llm()
    llm.complete = complete
    await call_with_escalation(llm, "sys", "p", [None], role="planner")
    assert seen == ["planner"]


def test_only_a_truncated_normalized_completion_is_a_truncation():
    from kriya.agents.agent import _truncated_completion_error

    class _Double:
        truncated = True
        finish_reason = "length"
        max_tokens = 10

    class _Holder:
        last_completion = None

    holder = _Holder()
    holder.last_completion = CompletionResult(status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length",
                                              max_tokens=128)
    message = _truncated_completion_error(holder, "a.py")
    assert message.startswith(f"{OUTPUT_TRUNCATED}: the model stopped at its output budget")
    assert "finish_reason='length', max_tokens=128" in message and "while writing a.py" in message
    holder.last_completion = CompletionResult(status=CompletionStatus.OK, finish_reason="stop")
    assert _truncated_completion_error(holder, "a.py") is None
    holder.last_completion = _Double()                     # a test double is never a truncation
    assert _truncated_completion_error(holder, "a.py") is None


@pytest.mark.asyncio
async def test_the_private_loop_carries_no_role_attribution_of_its_own():
    seen = []

    async def complete(*_a, **_k):
        seen.append(current_model_role())
        return "ok"

    llm = _llm()
    llm.complete = complete
    assert await _call_with_escalation(llm, "sys", "p", [None]) == "ok"
    assert seen == ["unattributed"]


# ---------------------------------------------------------------------------------------------------------------
# BaseAgent.run forwarding
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_base_run_tries_the_role_model_then_its_own_chain():
    llm = _llm([ConnectionError("primary"), "from chain"])
    agent = PlannerAgent("planner", llm, role_llm=None, role_chain=[_cand("chain-1")])
    assert await agent.run("plan") == "from chain"
    assert _calls(llm)[1].kwargs["model_override"] == "chain-1"


@pytest.mark.asyncio
async def test_base_run_forwards_its_overrides_exactly():
    seen = []

    async def complete(system, prompt, **kw):
        seen.append((system, prompt, kw, current_model_role()))
        return "ok"

    llm = _llm()
    llm.complete = complete
    agent = ArchitectAgent("architect", llm, max_output_tokens=700)
    await agent.run("p", system_prompt_override="SYS", json_mode=True, metrics_role="planner_shadow",
                    temperature_override=0.2, candidate_prompt=lambda c: "built")
    system, prompt, kw, role = seen[0]
    assert (system, prompt, role) == ("SYS", "built", "planner_shadow")
    assert kw == {"stream_callback": None, "json_mode": True, "temperature_override": 0.2, "max_tokens_override": 700}
    seen.clear()
    await agent.run("p", max_tokens_override=300)
    system, prompt, kw, role = seen[0]
    assert (system, prompt, role) == (agent.system_prompt, "p", "architect")
    assert kw == {"stream_callback": None, "json_mode": False, "temperature_override": None, "max_tokens_override": 300}


# ---------------------------------------------------------------------------------------------------------------
# Coercions (RunVerifier/SpecCompliance read them)
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False), ("true", True), (" YES ", True), ("1", True), ("false", False), ("no", False),
    ("0", False), ("", False), (1, False), (0, False), (None, False), ("maybe", False), ([True], False),
])
def test_coerce_bool_field_reads_only_real_booleans_and_known_spellings(value, expected):
    from kriya.agents.agent import _coerce_bool_field
    assert _coerce_bool_field(value, "f", "ctx") is expected


def test_an_unrecognised_boolean_is_logged_under_the_agents_logger_name(caplog):
    from kriya.agents.agent import _coerce_bool_field
    with caplog.at_level(logging.WARNING, logger="kriya.agents.agent"):
        assert _coerce_bool_field(7, "should_run", "judge") is False
    assert [r.name for r in caplog.records] == ["kriya.agents.agent"]
    assert "'should_run' was 7 (type int)" in caplog.records[0].getMessage()


@pytest.mark.parametrize("criteria,reasoning,expected", [
    ("reads the command line argument", "", "argv"),
    ("pass it as a CLI ARGUMENT", "", "argv"),
    ("uses argv[1]", "", "argv"),
    ("reads from standard input", "", "stdin"),
    ("", "pipe it to STDIN", "stdin"),
    ("the command-line argument or stdin", "", "argv"),
    ("prints hello", "nothing else", "none"),
])
def test_the_input_channel_backstop(criteria, reasoning, expected):
    assert _infer_input_channel_from_text(criteria, reasoning) == expected


# ---------------------------------------------------------------------------------------------------------------
# Prompt text (a verbatim move must not change one byte of what any agent sends)
# ---------------------------------------------------------------------------------------------------------------

def _prompt_texts() -> Dict[str, str]:
    from kriya.agents.agent import PLANNER_CLOSED_VOCABULARIES, REQUIREMENT_VERDICTS_SCHEMA
    llm = _llm()
    reviewer = ReviewerAgent("reviewer", llm)
    return {
        "planner": PlannerAgent("p", llm).system_prompt,
        "milestone_planner": MilestonePlannerAgent("m", llm).system_prompt,
        "architect": ArchitectAgent("a", llm).system_prompt,
        "developer": DeveloperAgent("d", llm).system_prompt,
        "run_verifier": RunVerifierAgent("r", llm).system_prompt,
        "spec_compliance": SpecComplianceAgent("s", llm).system_prompt,
        "skill_gap": SkillGapAgent("g", llm).system_prompt,
        "reviewer": reviewer.system_prompt,
        "reviewer_structured": reviewer.structured_system_prompt,
        "reviewer_rejected": reviewer.rejected_candidate_system_prompt("quality gates exhausted"),
        "planner_closed_vocabularies": PLANNER_CLOSED_VOCABULARIES,
        "requirement_verdicts_schema": REQUIREMENT_VERDICTS_SCHEMA,
        "self_consistency_nudge": DeveloperAgent.SELF_CONSISTENCY_NUDGE,
        "rejected_markers": ReviewerAgent.REJECTED_DIAGNOSTIC_START + "|" + ReviewerAgent.REJECTED_DIAGNOSTIC_END,
    }


PROMPT_DIGESTS = {
    'architect': 'b700554a5f27eb26',
    'developer': '0cf65910968d7ddc',
    'milestone_planner': 'cb527719618db602',
    'planner': '6e6b6128feb3ac84',
    'planner_closed_vocabularies': '4080a03cff893701',
    'rejected_markers': '25e6823c7da3b148',
    'requirement_verdicts_schema': '46ede08410a070df',
    'reviewer': '0647c06a6f0b1a1a',
    'reviewer_rejected': '0acd1d023dc7a7c3',
    'reviewer_structured': 'b31940fad88e5a22',
    'run_verifier': '95a1b2c17b4df7ca',
    'self_consistency_nudge': 'c341c332e56ca2e7',
    'skill_gap': '3979e4547ddfda56',
    'spec_compliance': 'd98fa75854e867b4',
}  # sha256[:16] at 886a46d


def test_every_agent_prompt_text_is_byte_identical_to_the_base():
    assert {name: _sha(text) for name, text in _prompt_texts().items()} == PROMPT_DIGESTS
    assert DeveloperAgent.DEFAULT_SIBLING_CONTENT_BUDGET == 3000


# ---------------------------------------------------------------------------------------------------------------
# Milestone planner / Architect output handling
# ---------------------------------------------------------------------------------------------------------------

_V2 = ('{"milestones": [{"id": "M1", "goal": "start the node", "depends_on": [], '
       '"acceptance": [{"id": "M1-A1", "description": "prints started"}]}]}')
_V1 = '{"milestones": [{"goal": "start the node", "success_criterion": "prints started", "depends_on_previous": false}]}'


@pytest.mark.asyncio
async def test_the_milestone_planner_takes_v2_then_normalized_v1_then_nothing():
    agent = MilestonePlannerAgent("milestone_planner", _llm([f"```json\n{_V2}\n```"]))
    raw, milestones = await agent.run_with_milestone_list("goal")
    assert [(m.id, m.goal) for m in milestones] == [("M1", "start the node")]
    agent = MilestonePlannerAgent("milestone_planner", _llm([f"```json\n{_V1}\n```"]))
    raw, milestones = await agent.run_with_milestone_list("goal")
    assert milestones is not None and type(milestones[0]).__name__ == "MilestoneV2"
    assert [(m.id, m.goal) for m in milestones] == [("M1", "start the node")]
    agent = MilestonePlannerAgent("milestone_planner", _llm(["no json here"]))
    assert await agent.run_with_milestone_list("goal") == ("no json here", None)


@pytest.mark.asyncio
async def test_the_architect_returns_its_validated_file_list_or_none():
    design = 'Design.\n```json\n{"files": ["src/App.java", "pom.xml"]}\n```'
    agent = ArchitectAgent("architect", _llm([design]))
    assert await agent.run_with_file_list("goal") == (design, ["src/App.java", "pom.xml"])
    agent = ArchitectAgent("architect", _llm(["prose only"]))
    assert await agent.run_with_file_list("goal") == ("prose only", None)
    llm = _llm(["x"])
    await ArchitectAgent("architect", llm).run_with_file_list("goal", candidate_prompt=lambda c: "per-candidate")
    assert _calls(llm)[0].args[1] == "per-candidate"


# ---------------------------------------------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------------------------------------------

def test_the_rejected_diagnostic_keeps_only_the_text_between_ordered_markers():
    agent = ReviewerAgent("reviewer", _llm())
    start, end = agent.REJECTED_DIAGNOSTIC_START, agent.REJECTED_DIAGNOSTIC_END
    assert agent.extract_rejected_candidate_diagnostic(f"How to run\n{start}\n  why it failed \n{end}\nrun it") == \
        "why it failed"
    withheld = agent.extract_rejected_candidate_diagnostic(f"{end}\nx\n{start}")
    assert withheld.startswith("[Reviewer output did not follow the required rejected-candidate report structure")
    assert agent.extract_rejected_candidate_diagnostic(f"{start} only") == withheld
    assert agent.extract_rejected_candidate_diagnostic("nothing") == withheld


@pytest.mark.asyncio
async def test_the_structured_review_returns_the_object_or_a_typed_error():
    llm = _llm(['```json\n{"summary": "s", "findings": []}\n```'])
    agent = ReviewerAgent("reviewer", llm)
    assert await agent.run_structured_review("p") == {"summary": "s", "findings": []}
    call = _calls(llm)[0]
    assert call.args[0] == agent.structured_system_prompt and call.kwargs["json_mode"] is True
    agent = ReviewerAgent("reviewer", _llm([ConnectionError("down")]))
    assert await agent.run_structured_review("p") == {"_error": "structured review call failed: down"}
    agent = ReviewerAgent("reviewer", _llm(["[1, 2]"]))
    assert await agent.run_structured_review("p") == {"_error": "structured review response was not a JSON object"}


@pytest.mark.asyncio
async def test_the_structured_review_escalates_past_an_unparseable_answer():
    llm = _llm(["not json", '{"summary": "fb"}'])
    agent = ReviewerAgent("reviewer", llm, role_chain=[_cand("fb")])
    assert await agent.run_structured_review("p") == {"summary": "fb"}
    llm = _llm(["not json"])
    out = await ReviewerAgent("reviewer", llm).run_structured_review("p")
    assert out["_error"].startswith("structured review response was unparseable JSON: ")


# ---------------------------------------------------------------------------------------------------------------
# SkillGap
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skill_gap_extraction_filters_items_and_keeps_conflicts_out_of_rules():
    answer = json.dumps({
        "rules": ["Use port 5672", "  ", 7, "Pin version 2"],
        "examples": {"a.xml": "<a/>", " ": "x", "b.xml": {"nested": 1}},
        "conflicts": ["text", {"candidate_rule": " Pin version 2 ", "conflicts_with": " v1 ", "reason": 3},
                      {"candidate_rule": "  "}],
    })
    llm = _llm([answer])
    out = await SkillGapAgent("skill_gap", llm).extract_skill_update("R" * 30000, "the gap", [])
    assert out == {"rules": ["Use port 5672"], "examples": {"a.xml": "<a/>"},
                   "conflicts": [{"candidate_rule": "Pin version 2", "conflicts_with": "v1", "reason": ""}]}
    prompt = _calls(llm)[0].args[1]
    assert prompt.count("R") - prompt.replace("R" * 20000, "").count("R") == 20000 and "R" * 20001 not in prompt
    assert "=== Existing Rules For This Skill ===\n(none yet)\n" in prompt
    for failure in ([ConnectionError("x")], ["not json"], ["[1]"]):
        assert await SkillGapAgent("g", _llm(failure)).extract_skill_update("r", "g", ["a"]) == \
            {"rules": [], "examples": {}, "conflicts": []}


@pytest.mark.asyncio
async def test_skill_gap_extraction_escalates_past_an_unparseable_answer():
    llm = _llm(["nope", '{"rules": ["R1"]}'])
    out = await SkillGapAgent("g", llm, role_chain=[_cand("fb")]).extract_skill_update("r", "g", ["a", "b"])
    assert out == {"rules": ["R1"], "examples": {}, "conflicts": []}
    assert "=== Existing Rules For This Skill ===\na\nb\n" in _calls(llm)[0].args[1]


@pytest.mark.asyncio
async def test_the_skill_conflict_check_resolves_1_based_indexes_from_the_real_lists():
    answer = json.dumps({"conflicts": [
        {"rule_a_index": 1, "rule_b_index": 2, "explanation": "same port"},
        {"rule_a_index": 0, "rule_b_index": 1}, {"rule_a_index": 3, "rule_b_index": 1},
        {"rule_a_index": 1, "rule_b_index": 3}, {"rule_a_index": "1", "rule_b_index": 1}, "x",
        {"rule_a_index": 2, "rule_b_index": 1},
    ]})
    llm = _llm([answer])
    out = await SkillGapAgent("g", llm).check_skill_conflicts("A", ["a1", "a2"], "B", ["b1", "b2"])
    assert out == [{"rule_a": "a1", "rule_b": "b2", "explanation": "same port"},
                   {"rule_a": "a2", "rule_b": "b1", "explanation": ""}]
    prompt = _calls(llm)[0].args[1]
    assert "=== Skill A: A ===\n[1] a1\n[2] a2\n\n=== Skill B: B ===\n[1] b1\n[2] b2\n\n" in prompt
    assert _sha(_calls(llm)[0].args[0]) == CONFLICT_CHECKER_DIGEST
    assert await SkillGapAgent("g", _llm(["unused"])).check_skill_conflicts("A", ["a"], "B", []) == []
    assert await SkillGapAgent("g", _llm(["unused"])).check_skill_conflicts("A", [], "B", ["b"]) == []
    for failure in ([ConnectionError("x")], ["not json"], ["[1]"], ['{"conflicts": {"rule_a_index": 1}}']):
        assert await SkillGapAgent("g", _llm(failure)).check_skill_conflicts("A", ["a"], "B", ["b"]) == []


@pytest.mark.asyncio
async def test_the_skill_conflict_check_escalates_past_an_unparseable_answer():
    llm = _llm(["nope", '{"conflicts": [{"rule_a_index": 1, "rule_b_index": 1}]}'])
    out = await SkillGapAgent("g", llm, role_chain=[_cand("fb")]).check_skill_conflicts("A", ["a"], "B", ["b"])
    assert out == [{"rule_a": "a", "rule_b": "b", "explanation": ""}]


CONFLICT_CHECKER_DIGEST = '535b3bf13fe111eb'  # sha256[:16] at 886a46d


# ---------------------------------------------------------------------------------------------------------------
# RunVerifier.judge
# ---------------------------------------------------------------------------------------------------------------

def _judge(answer: Dict[str, Any]) -> str:
    return json.dumps({"should_run": True, "run_commands": [["java", "App"]], "success_criteria": "prints hi",
                       "reasoning": "r", **answer})


@pytest.mark.asyncio
async def test_judge_shows_the_build_file_or_states_its_absence_for_java_only():
    llm = _llm([_judge({})] * 3)
    agent = RunVerifierAgent("run_verifier", llm)
    await agent.judge("g", "d", ["App.java"], build_file_content="<project/>")
    await agent.judge("g", "d", ["App.java"])
    await agent.judge("g", "d", ["app.py"])
    with_pom, java_no_pom, python = (c.args[1] for c in _calls(llm))
    assert "=== Actual pom.xml content (ground truth for how to invoke this app) ===\n<project/>\n\n" in with_pom
    assert "=== Build System ===" not in with_pom
    assert "=== Build System ===\nNo pom.xml or build.gradle was found" in java_no_pom
    assert "=== Build System ===" not in python and "pom.xml content" not in python
    assert all(c.kwargs["json_mode"] is True for c in _calls(llm))


@pytest.mark.asyncio
async def test_judge_failure_paths_never_run_and_name_the_infrastructure_error():
    base = {"should_run": False, "execution_mode": "finite_command", "run_commands": None, "managed_service": None,
            "command_source": "inferred", "success_criteria": ""}
    out = await RunVerifierAgent("v", _llm([ConnectionError("boom")])).judge("g", "d", ["a.py"])
    assert out == {**base, "reasoning": "judge() call failed entirely: boom", "infrastructure_error": "boom"}
    out = await RunVerifierAgent("v", _llm(["[1]"])).judge("g", "d", ["a.py"])
    assert out == {**base, "reasoning": "judge() response was not a JSON object",
                   "infrastructure_error": "response was not a JSON object"}
    out = await RunVerifierAgent("v", _llm(["nope"])).judge("g", "d", ["a.py"])
    assert out["should_run"] is False and out["infrastructure_error"].startswith("unparseable response: ")
    assert out["reasoning"].startswith("judge() response was unparseable JSON: ")
    assert set(out) == set(base) | {"reasoning", "infrastructure_error"}


@pytest.mark.asyncio
async def test_judge_escalates_past_an_unparseable_answer():
    llm = _llm(["nope", _judge({})])
    out = await RunVerifierAgent("v", llm, role_chain=[_cand("fb")]).judge("g", "d", ["a.py"])
    assert out["should_run"] is True and llm.complete.await_count == 2


@pytest.mark.asyncio
async def test_judge_commands_shape_javac_backstop_and_should_run():
    async def judge(answer, files=("b/B.java", "a/A.java"), build=None):
        return await RunVerifierAgent("v", _llm([_judge(answer)])).judge("g", "d", list(files), build_file_content=build)

    out = await judge({"run_commands": ["java", "App"]})            # the old single-command shape
    assert out["run_commands"] == [["javac", "a/A.java", "b/B.java"], ["java", "App"]] and out["should_run"] is True
    out = await judge({}, build="<project/>")                          # a build file: no javac backstop
    assert out["run_commands"] == [["java", "App"]]
    out = await judge({"run_commands": [["JAVAC", "A.java"], ["java", "App"]]})   # already compiles: untouched
    assert out["run_commands"] == [["JAVAC", "A.java"], ["java", "App"]]
    out = await judge({}, files=("app.py",))                           # not Java: untouched
    assert out["run_commands"] == [["java", "App"]]
    out = await judge({"run_commands": [["java", "App"], "x"]})         # partially invalid: refused whole
    assert out["run_commands"] is None and out["should_run"] is False
    out = await judge({"run_commands": []})
    assert out["run_commands"] is None and out["should_run"] is False
    out = await judge({"should_run": "false"})
    assert out["should_run"] is False
    out = await judge({"should_run": "yes"})
    assert out["should_run"] is True
    out = await judge({"should_run": 1})
    assert out["should_run"] is False


@pytest.mark.asyncio
async def test_judge_execution_mode_managed_service_command_source_and_input_channel():
    async def judge(answer):
        return await RunVerifierAgent("v", _llm([_judge(answer)])).judge("g", "d", ["a.py"])

    out = await judge({})
    assert (out["execution_mode"], out["managed_service"], out["command_source"]) == ("finite_command", None, "inferred")
    out = await judge({"execution_mode": "service"})                   # explicit unknown value is preserved
    assert out["execution_mode"] == "service" and out["should_run"] is True
    out = await judge({"execution_mode": "managed_service", "run_commands": None,
                       "managed_service": {"service_command": ["x"]}})
    assert out["should_run"] is True and out["managed_service"] == {"service_command": ["x"]}
    out = await judge({"execution_mode": "managed_service", "managed_service": ["x"]})
    assert out["should_run"] is False and out["managed_service"] is None
    out = await judge({"command_source": "goal_explicit"})
    assert out["command_source"] == "goal_explicit"
    out = await judge({"command_source": "model"})
    assert out["command_source"] == "inferred"
    out = await judge({"input_channel": "stdin", "success_criteria": "reads the command line argument"})
    assert out["input_channel"] == "stdin"
    out = await judge({"input_channel": "file", "success_criteria": "reads the command line argument"})
    assert out["input_channel"] == "argv"
    out = await judge({"success_criteria": None, "reasoning": None})
    assert (out["success_criteria"], out["reasoning"], out["input_channel"]) == ("", "", "none")


# ---------------------------------------------------------------------------------------------------------------
# RunVerifier.grade
# ---------------------------------------------------------------------------------------------------------------

def _grade_answer(**kw) -> str:
    return json.dumps({"verdict": "PASS", "passed": True, "reasoning": "saw it", "likely_files": [], **kw})


@pytest.mark.asyncio
async def test_grade_pass_fail_unknown_and_likely_files_limited_to_known():
    agent = RunVerifierAgent("v", _llm([_grade_answer(likely_files=["a.py", "ghost.py", 3])]))
    out = await agent.grade("g", "prints hi", "hi\n", 0, files_written=["a.py"])
    assert (out["passed"], out["verdict"], out["reason_code"], out["reasoning"], out["likely_files"]) == \
        (True, "PASS", "VERIFIER_CONFIRMED", "saw it", ["a.py"])
    assert [p["answered"] for p in out["evidence_packages"]] == [True]
    out = await RunVerifierAgent("v", _llm([_grade_answer(verdict="FAIL", passed=False)])).grade("g", "c", "x", 1)
    assert (out["passed"], out["verdict"], out["reason_code"]) == (False, "FAIL", "VERIFIER_REPORTED_FAILURE")
    out = await RunVerifierAgent("v", _llm([_grade_answer(verdict="UNKNOWN")])).grade("g", "c", "x", 0)
    assert (out["passed"], out["verdict"], out["reason_code"]) == (False, "UNKNOWN", "VERIFIER_REPORTED_UNKNOWN")
    out = await RunVerifierAgent("v", _llm([json.dumps({"passed": "false"})])).grade("g", "c", "x", 0)
    assert (out["passed"], out["verdict"]) == (False, "FAIL")
    out = await RunVerifierAgent("v", _llm([json.dumps({"passed": "true", "likely_files": "a.py"})])).grade(
        "g", "c", "x", 0, files_written=["a.py"])
    assert (out["passed"], out["verdict"], out["likely_files"]) == (True, "PASS", [])


@pytest.mark.asyncio
async def test_grade_failure_paths_are_unknown_with_their_reason_and_answered_flag():
    out = await RunVerifierAgent("v", _llm([ConnectionError("down")])).grade("g", "c", "x", 0)
    assert (out["passed"], out["verdict"], out["reason_code"], out["reasoning"], out["likely_files"]) == \
        (False, "UNKNOWN", "VERIFIER_CALL_FAILED", "Grader call failed: down", [])
    assert [p["answered"] for p in out["evidence_packages"]] == [False]
    out = await RunVerifierAgent("v", _llm(["[1]"])).grade("g", "c", "x", 0)
    assert (out["verdict"], out["reason_code"], out["reasoning"]) == \
        ("UNKNOWN", "VERIFIER_RESULT_MALFORMED", "Grader response was not a JSON object.")
    assert [p["answered"] for p in out["evidence_packages"]] == [True]
    out = await RunVerifierAgent("v", _llm(["nope"])).grade("g", "c", "x", 0)
    assert (out["verdict"], out["reason_code"]) == ("UNKNOWN", "VERIFIER_RESULT_MALFORMED")
    assert out["reasoning"].startswith("Grader response could not be parsed: ")


@pytest.mark.asyncio
async def test_grade_builds_one_package_per_candidate_and_only_the_last_is_answered():
    cfg = AppConfig()
    llm = _llm(["nope", _grade_answer()], config=cfg)
    fb = _cand("fallback-grader", max_tokens=2048)
    out = await RunVerifierAgent("v", llm, role_chain=[fb]).grade("g", "c", "hi", 0)
    assert out["verdict"] == "PASS"
    packages = out["evidence_packages"]
    assert [p["answered"] for p in packages] == [False, True]
    assert [p["model"] for p in packages] == [cfg.llm.model, "fallback-grader"]
    assert all(call.kwargs["json_mode"] is True for call in _calls(llm))


@pytest.mark.asyncio
async def test_grade_trusted_notice_timeout_note_and_the_retained_evidence():
    from kriya.workflow.verifier_evidence import RetainedRuntimeEvidence
    llm = _llm([_grade_answer()] * 3)
    agent = RunVerifierAgent("v", llm)
    await agent.grade("g", "c", "plain output", 0, distrust_notice="the PASS marker is ungrounded", timed_out=True)
    await agent.grade("g", "c", "plain output", 0)
    evidence = RetainedRuntimeEvidence.from_output("evidence output", 0)
    await agent.grade("g", "c", "ignored output", 0, evidence=evidence)
    noticed, plain, retained = (c.args[1] for c in _calls(llm))
    assert ("=== Deterministic Distrust Notice (TRUSTED, from Kriya) ===\nthe PASS marker is ungrounded\n"
            "=== End Deterministic Distrust Notice ===\n") in noticed
    assert "NOTE: this process was forcibly killed after exceeding its execution timeout" in noticed
    assert "Distrust Notice (TRUSTED" not in plain and "forcibly killed" not in plain
    assert "evidence output" in retained and "ignored output" not in retained
    assert _sha(_calls(llm)[0].args[0]) == GRADER_SYSTEM_DIGEST


@pytest.mark.asyncio
async def test_a_reported_pass_without_the_decisive_evidence_is_demoted_and_explained():
    from kriya.workflow.verifier_evidence import RetainedRuntimeEvidence
    evidence = RetainedRuntimeEvidence.from_run_result({"returncode": 0, "steps": [
        {"command": ["java", "App"], "exit_code": 0, "stdout": "partial", "stdout_lost_chars": 500}]})
    out = await RunVerifierAgent("v", _llm([_grade_answer(reasoning="looks fine")])).grade(
        "g", "c", "", 0, evidence=evidence)
    assert (out["passed"], out["verdict"], out["reason_code"]) == (False, "UNKNOWN", "CAPTURE_LOSS_UNRESOLVED")
    assert out["reasoning"] == ("CAPTURE_LOSS_UNRESOLVED: the grader reported PASS without seeing all decisive "
                                "evidence (evidence it did not see cannot support a PASS). Grader's own reasoning: "
                                "looks fine")
    out = await RunVerifierAgent("v", _llm([_grade_answer(verdict="FAIL", reasoning="no")])).grade(
        "g", "c", "", 0, evidence=evidence)
    assert (out["verdict"], out["reasoning"]) == ("FAIL", "no")


GRADER_SYSTEM_DIGEST = '4a2b574438724689'  # sha256[:16] at 886a46d


# ---------------------------------------------------------------------------------------------------------------
# SpecCompliance.check
# ---------------------------------------------------------------------------------------------------------------

class _Req:
    def __init__(self, rid, text):
        self.id, self.text = rid, text


class _Reqs:
    def __init__(self, *items):
        self.requirements = list(items)


@pytest.mark.asyncio
async def test_the_check_prompt_context_schema_and_requirements_block():
    llm = _llm([json.dumps({"compliant": True})] * 2)
    agent = SpecComplianceAgent("spec_compliance", llm)
    await agent.check("goal", ["a.py"], {"a.py": "x = 1\n"})
    reqs = _Reqs(_Req("REQ-1", "field `displayName`"))
    await agent.check("goal", ["a.py"], {"a.py": "x = 1\n"}, authoritative_context="SETTLED FACTS", requirements=reqs)
    plain, full = _calls(llm)
    assert plain.args[0] == agent.system_prompt and full.args[0].startswith(agent.system_prompt)
    assert full.args[0].endswith(_req_schema()) and not plain.args[0].endswith(_req_schema())
    assert plain.args[1].startswith("=== Goal ===\ngoal\n\n=== Files Generated ===\n")
    assert full.args[1].startswith("SETTLED FACTS\n\n=== Goal ===\ngoal\n\n")
    assert "REQ-1" in full.args[1] and "requirement_verdicts" in full.args[1] and "REQ-1" not in plain.args[1]
    assert all(c.kwargs["json_mode"] is True for c in _calls(llm))


def _req_schema() -> str:
    from kriya.agents.agent import REQUIREMENT_VERDICTS_SCHEMA
    return REQUIREMENT_VERDICTS_SCHEMA


@pytest.mark.asyncio
async def test_the_check_failure_paths_fail_open_with_a_reason_and_the_verifier():
    llm = _llm([ConnectionError("down")])
    out = await SpecComplianceAgent("s", llm).check("g", ["a.py"], {"a.py": "x"})
    verifier = {"model": None, "runtime_fingerprint": None, "runtime_exact": False}
    assert out == {"compliant": True, "status": "unknown", "reasoning": "Check call failed: down",
                   "missing_requirements": [], "likely_files": [], "failure_reason_code": "VERIFIER_CALL_FAILED",
                   "verifier": verifier}
    refusal = ContextBudgetUnsatisfiableError.__new__(ContextBudgetUnsatisfiableError)
    Exception.__init__(refusal, "too big")
    out = await SpecComplianceAgent("s", _llm([refusal])).check("g", ["a.py"], {"a.py": "x"})
    assert out["failure_reason_code"] == "VERIFIER_REQUEST_REFUSED" and out["compliant"] is True
    out = await SpecComplianceAgent("s", _llm(["nope"])).check("g", ["a.py"], {"a.py": "x"})
    assert (out["status"], out["failure_reason_code"], out["compliant"]) == ("unknown", "MALFORMED_VERIFIER_RESULT", True)
    out = await SpecComplianceAgent("s", _llm(["[1]"])).check("g", ["a.py"], {"a.py": "x"})
    assert out == {"compliant": True, "status": "unknown", "reasoning": "Response was not a JSON object.",
                   "missing_requirements": [], "likely_files": [], "failure_reason_code": "MALFORMED_VERIFIER_RESULT",
                   "verifier": verifier}


@pytest.mark.asyncio
async def test_the_check_names_the_verifier_that_judged():
    llm = _llm([json.dumps({"compliant": True})])
    llm.last_completion = CompletionResult(status=CompletionStatus.OK, model="judge-model",
                                           runtime_fingerprint="sha256:abc", runtime_fingerprint_exact=True)
    out = await SpecComplianceAgent("s", llm).check("g", ["a.py"], {"a.py": "x"})
    assert out["verifier"] == {"model": "judge-model", "runtime_fingerprint": "sha256:abc", "runtime_exact": True}

    class _Loose:
        model = ""
        runtime_fingerprint = 5
        runtime_fingerprint_exact = "yes"

    llm = _llm([ConnectionError("x")])
    llm.last_completion = _Loose()
    out = await SpecComplianceAgent("s", llm).check("g", ["a.py"], {"a.py": "x"})
    assert out["verifier"] == {"model": None, "runtime_fingerprint": None, "runtime_exact": False}


@pytest.mark.asyncio
async def test_the_check_verdict_fields_and_the_indeterminate_shape():
    async def check(answer, requirements=None):
        return await SpecComplianceAgent("s", _llm([json.dumps(answer)])).check(
            "g", ["a.py", "b.py"], {"a.py": "x", "b.py": "y"}, requirements=requirements)

    out = await check({"compliant": False, "missing_requirements": ["name", 3], "likely_files": ["a.py", "z.py"],
                       "reasoning": "r", "requirement_verdicts": [{"id": "REQ-1"}]})
    assert out == {"compliant": False, "reasoning": "r", "missing_requirements": ["name"], "likely_files": ["a.py"],
                   "requirement_verdicts": None, "verifier": out["verifier"]}
    out = await check({"compliant": "false", "missing_requirements": [], "likely_files": ["b.py"], "reasoning": "why"})
    assert out == {"compliant": False, "status": "indeterminate", "reasoning": "why", "missing_requirements": [],
                   "likely_files": ["b.py"], "requirement_verdicts": None, "verifier": out["verifier"]}
    out = await check({"compliant": "TRUE"})
    assert out["compliant"] is True and "status" not in out and out["missing_requirements"] == []
    out = await check({"compliant": 1, "missing_requirements": ["x"]})
    assert out["compliant"] is False and out["missing_requirements"] == ["x"]


@pytest.mark.asyncio
async def test_a_missing_concrete_requirement_is_named_once_and_fails_the_check():
    reqs = _Reqs(_Req("REQ-1", "the class `PersonService`"), _Req("REQ-2", "it should be fast and robust"))
    verdicts = [{"id": "REQ-1", "verdict": " Missing "}, {"id": "REQ-2", "verdict": "missing"},
                {"id": "REQ-9", "verdict": "missing"}, "x", {"id": "REQ-1", "verdict": "missing"}]
    out = await SpecComplianceAgent("s", _llm([json.dumps({"compliant": True, "requirement_verdicts": verdicts,
                                                           "missing_requirements": []})])).check(
        "g", ["a.py"], {"a.py": "x"}, requirements=reqs)
    assert out["compliant"] is False and out["requirement_verdicts"] == verdicts
    assert out["missing_requirements"] == ["REQ-1: the class `PersonService`"]
    satisfied = [{"id": "REQ-1", "verdict": "satisfied"}]
    out = await SpecComplianceAgent("s", _llm([json.dumps({"compliant": True, "requirement_verdicts": satisfied})])).check(
        "g", ["a.py"], {"a.py": "x"}, requirements=reqs)
    assert out["compliant"] is True and out["missing_requirements"] == [] and out["requirement_verdicts"] == satisfied
    out = await SpecComplianceAgent("s", _llm([json.dumps({"compliant": True, "requirement_verdicts": "x"})])).check(
        "g", ["a.py"], {"a.py": "x"}, requirements=reqs)
    assert out["compliant"] is True and out["requirement_verdicts"] == "x"


@pytest.mark.asyncio
async def test_the_check_escalates_past_an_unparseable_answer():
    llm = _llm(["nope", json.dumps({"compliant": True})])
    out = await SpecComplianceAgent("s", llm, role_chain=[_cand("fb")]).check("g", ["a.py"], {"a.py": "x"})
    assert out["compliant"] is True and "failure_reason_code" not in out and llm.complete.await_count == 2


@pytest.mark.asyncio
async def test_the_check_shows_changed_regions_against_the_baseline_when_files_do_not_fit():
    from unittest.mock import patch
    seen = {}

    def fit(capacity, fixed, files_written, file_contents, baseline_contents):
        seen["baseline"] = baseline_contents
        return type("R", (), {"value": "FILES"})()

    with patch("kriya.workflow.context_budget.fit_spec_compliance_files", side_effect=fit):
        llm = _llm([json.dumps({"compliant": True})])
        await SpecComplianceAgent("s", llm).check("g", ["a.py"], {"a.py": "new"}, baseline_contents={"a.py": "old"})
    assert seen["baseline"] == {"a.py": "old"}
    assert _calls(llm)[0].args[1].endswith("=== Files Generated ===\nFILES\n\nDoes this code satisfy every concrete, "
                                           "literally-named requirement in the goal, per the rules above?")


# ---------------------------------------------------------------------------------------------------------------
# Developer: per-file request assembly, response parsing and refusals
# ---------------------------------------------------------------------------------------------------------------

from _protocol_responses import sentinel  # noqa: E402 - tests/ is on sys.path (the shared scripted-answer helper)

LEGACY = AppConfig.model_validate({"autonomy": {"developer_response_protocol": "legacy_strict"}})
_INCOMPATIBLE = "App.java:[3,5] incompatible types: Object cannot be converted to Person\n"
_OVERFLOW = "Exception in thread main java.nio.BufferOverflowException\n"


class _Recorder:
    """A scripted per-file completion that records (system, prompt, kwargs) of every call."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, system, prompt, **kwargs):
        self.calls.append({"system": system, "prompt": prompt, **kwargs})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer(system) if callable(answer) else answer


def _developer(answers, config: Optional[AppConfig] = None):
    llm = LLMClient(config or AppConfig())
    recorder = _Recorder(answers)
    llm.complete = recorder
    return DeveloperAgent("developer", llm), llm, recorder


class _Protocol:
    def __init__(self, preferred="small_native_tools", streaming=True, json_mode=True):
        self.preferred_edit_protocol, self.streaming, self.json_mode = preferred, streaming, json_mode
        self.reliable_multiline_json = True


async def _fill(agent, files, **kw):
    entries = [{"filepath": f, "content": None, "edits": None} for f in files]
    args = dict(task_description=kw.pop("task", "the task"), design_context="the design",
                existing_code_context=kw.pop("context", "the context"), stream_callback=kw.pop("stream", None),
                model_override=kw.pop("model_override", None), base_url_override=None, api_key_override=None)
    args.update(kw)
    return await agent._fill_missing_content(entries, **args)


def _prompt_scenarios():
    """(name, config, files, kwargs, answer) - every per-file request shape the Developer assembles."""
    retry = dict(prior_error_context=_INCOMPATIBLE + _OVERFLOW, error_source_context={"src/App.java": "SRC LINE 3\n"},
                 extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE)
    rich = dict(task=f"{AUTHORITATIVE_GOAL_SECTION_HEADER}\ngoal", context="== Engineering Skill Conventions ==\nrule")
    return [
        ("create_structured", None, ["src/App.java"], {}, "x"),
        ("create_legacy", LEGACY, ["src/App.java"], {}, "x"),
        ("create_rich_legacy", LEGACY, ["src/App.java"], dict(rich), "x"),
        ("create_rich_structured", None, ["src/App.java"], dict(rich), "x"),
        ("retry_anchored_legacy", LEGACY, ["src/App.java"], dict(retry), "FIX ANALYSIS: a\nNO CHANGE NEEDED: b"),
        ("retry_anchored_structured", None, ["src/App.java"], dict(retry), "x"),
        ("retry_full_legacy", LEGACY, ["src/App.java"], dict(prior_error_context=_INCOMPATIBLE),
         "FIX ANALYSIS: a\nNO CHANGE NEEDED: b"),
        ("retry_current_content_legacy", LEGACY, ["src/Util.java"],
         dict(prior_error_context="[VERIFICATION] FAIL", files_with_current_content=["src/Util.java"]),
         "FIX ANALYSIS: a\nNO CHANGE NEEDED: b"),
        ("repair_full_no_failure_legacy", LEGACY, ["src/Util.java"], dict(default_operation="repair_with_full_file"),
         "x"),
        ("repair_full_no_failure_structured", None, ["src/Util.java"], dict(default_operation="repair_with_full_file"),
         "x"),
        ("first_attempt_patch_legacy", LEGACY, ["src/Util.java"], dict(default_operation="repair_with_patch"),
         "FIX ANALYSIS: a\nNO CHANGE NEEDED: b"),
        ("patch_only_legacy", LEGACY, ["src/Util.java"],
         dict(prior_error_context="boom", edit_operations={"src/Util.java": ["anchored_edit"]}),
         "FIX ANALYSIS: a\nNO CHANGE NEEDED: b"),
        ("patch_only_structured", None, ["src/Util.java"],
         dict(prior_error_context="boom", edit_operations={"src/Util.java": ["anchored_edit"]}), "x"),
        ("not_implicated_legacy", LEGACY, ["src/Util.java"],
         dict(prior_error_context=_INCOMPATIBLE, implicated_files=["src/Other.java"]), "x"),
        ("siblings_legacy", LEGACY, ["src/A.java", "src/B.java", "src/C.java"], {}, "class A {}"),
    ]


async def _scenario_requests(name, config, files, kwargs, answer):
    agent, _llm_, recorder = _developer([answer] * len(files), config)
    await _fill(agent, files, **dict(kwargs))
    return [(call["system"], call["prompt"]) for call in recorder.calls]


DEVELOPER_REQUEST_DIGESTS: Dict[str, List[str]] = {
    'create_legacy': ['0e60a4b795768fb4:f025486678761cca'],
    'create_rich_legacy': ['0e60a4b795768fb4:5b99df43f3ba4902'],
    'create_rich_structured': ['cc46cd1f35d82753:66ee17a167a1de54'],
    'create_structured': ['cc46cd1f35d82753:9e05ee1ec7453ef1'],
    'first_attempt_patch_legacy': ['0f9d15e75e160c21:52eeaac7154011fd'],
    'not_implicated_legacy': ['0e60a4b795768fb4:eb490c7e51286855'],
    'patch_only_legacy': ['4571cf2455edeac9:ad3f2fb4d88f9b1d'],
    'patch_only_structured': ['df3aea2e7b0221f3:f30a2e3f0e399e01'],
    'repair_full_no_failure_legacy': ['e56adf18557e36c4:73237869722b251b'],
    'repair_full_no_failure_structured': ['f1ea42ad48a97231:f30a2e3f0e399e01'],
    'retry_anchored_legacy': ['2fbf142e38f98e8c:5e8a2c253ad9ecf9'],
    'retry_anchored_structured': ['fc6420ae08fe441f:e0a4e171a56d6dda'],
    'retry_current_content_legacy': ['2fbf142e38f98e8c:d25a0ebb468a7a27'],
    'retry_full_legacy': ['e83ed54ca16a44f5:b2f79ad7e3d4950c'],
    'siblings_legacy': ['0e60a4b795768fb4:04c7fc9da870ddb2', '0e60a4b795768fb4:e8af1374aaa762cd', '0e60a4b795768fb4:a6c6294f6b795010'],
}  # sha256[:16] at 886a46d


@pytest.mark.asyncio
async def test_every_developer_request_shape_is_byte_identical_to_the_base():
    found = {}
    for name, config, files, kwargs, answer in _prompt_scenarios():
        found[name] = [_sha(system) + ":" + _sha(prompt)
                       for system, prompt in await _scenario_requests(name, config, files, kwargs, answer)]
    assert found == DEVELOPER_REQUEST_DIGESTS


@pytest.mark.asyncio
async def test_the_mode_and_reminders_follow_the_request():
    reqs = {}
    for name, config, files, kwargs, answer in _prompt_scenarios():
        reqs[name] = await _scenario_requests(name, config, files, kwargs, answer)
    system, prompt = reqs["create_legacy"][0]
    assert "MODE: CREATE_FULL_FILE" in system and "FIX ANALYSIS" not in prompt
    assert "[VERIFICATION] PASS" in prompt                     # src/App.java is an entrypoint
    assert "Engineering Skill Conventions in the Existing" not in prompt and "Authoritative Goal section" not in prompt
    system, prompt = reqs["create_rich_legacy"][0]
    assert "re-check the Engineering Skill Conventions" in prompt and "Authoritative Goal section" in prompt
    system, prompt = reqs["retry_anchored_legacy"][0]
    assert "MODE: REPAIR" in system and '"SEARCH:"' in system and "FILE CONTENT:" in system
    assert "This is a RETRY" in prompt and "SRC LINE 3" in prompt
    assert '"Object" cannot be converted to "Person"' in prompt and "BufferOverflowException - writing" in prompt
    assert "re-read your own FIX ANALYSIS" in prompt
    system, prompt = reqs["retry_full_legacy"][0]
    assert "MODE: REPAIR" in system and '"SEARCH:"' not in system and "SRC LINE" not in prompt
    system, prompt = reqs["retry_current_content_legacy"][0]
    assert '"SEARCH:"' in system and "[VERIFICATION] PASS" not in prompt   # Util.java is not an entrypoint
    system, prompt = reqs["repair_full_no_failure_legacy"][0]
    assert "MODE: REPAIR_WITH_FULL_FILE" in system and "FIX ANALYSIS" not in prompt
    system, prompt = reqs["first_attempt_patch_legacy"][0]
    assert "the requested change in this file - the complete current content" in system
    assert "This file's complete current content was not available to you this attempt" in prompt
    system, prompt = reqs["patch_only_legacy"][0]
    assert "FILE CONTENT:" not in system and "A whole-file replacement is not available for this file" in prompt
    system, prompt = reqs["not_implicated_legacy"][0]
    assert "MODE: CREATE_FULL_FILE" in system and "incompatible types" not in prompt.split("=== Task ===")[1][30:]
    for name in ("create_structured", "retry_anchored_structured", "repair_full_no_failure_structured",
                 "patch_only_structured"):
        assert "<<<KRIYA:" in reqs[name][0][0], name
    assert "MODE: REPAIR.\nRepair exactly one existing file, 'src/App.java'" in reqs["retry_anchored_structured"][0][0]
    assert "BufferOverflowException" in reqs["retry_anchored_structured"][0][1]
    assert "BufferOverflowException" not in reqs["create_structured"][0][1]


@pytest.mark.asyncio
async def test_the_legacy_create_payload_is_unwrapped_only_as_a_whole():
    agent, _l, _r = _developer(["```java\n    class A {}\n```", '{"files": [{"path": "src/A.java", "content": "class A {}"}]}',
                                "intro\n```java\nclass A {}\n```\noutro"], LEGACY)
    first = await _fill(agent, ["src/A.java"])
    second = await _fill(agent, ["src/A.java"])
    third = await _fill(agent, ["src/A.java"])
    assert first == [{"filepath": "src/A.java", "content": "    class A {}"}]
    assert second == [{"filepath": "src/A.java", "content": "class A {}"}]
    assert third[0]["content"] is None and third[0]["protocol_reason_code"] == "AMBIGUOUS_FILE_RESPONSE_PROTOCOL"


@pytest.mark.asyncio
async def test_a_file_list_answer_is_refused_as_content_except_for_json_documents():
    for path, answer in (("src/calc.py", "[]"), ("src/calc.py", "{}"), ("src/calc.py", '["a.py"]'),
                         ("src/calc.py", '{"path": "a.py"}'), ("src/calc.py", '{"files": []}')):
        agent, _l, _r = _developer([answer], LEGACY)
        entry = (await _fill(agent, [path]))[0]
        assert entry["content"] is None, answer
        assert entry["protocol_reason_code"] == "FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT", answer
        assert entry["protocol_error"].startswith(f"FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT: the response for '{path}' is ")
    for path, answer in (("data.json", "[]"), ("conf/app.YAML", "{}"), (".babelrc", "{}"), ("src/calc.py", '{"a": 1}'),
                         ("src/calc.py", "[1, 2"), ("src/calc.py", '"text"')):
        agent, _l, _r = _developer([answer], LEGACY)
        entry = (await _fill(agent, [path]))[0]
        assert entry == {"filepath": path, "content": answer}, (path, answer)


@pytest.mark.asyncio
async def test_the_envelope_unwrap_matches_exact_then_basename_then_a_single_entry():
    unwrap = DeveloperAgent._unwrap_file_content_envelope
    two = json.dumps({"files": [{"path": "a/x.py", "content": "A"}, {"filepath": "b/x.py", "content": "B"}]})
    assert unwrap(two, "b/x.py") == "B" and unwrap(two, "c/x.py") == "A" and unwrap(two, "c/y.py") is None
    assert unwrap(json.dumps({"files": [{"path": "q.py", "content": "Q"}]}), "z.py") == "Q"
    assert unwrap(json.dumps({"files": [{"path": "z.py", "content": ""}, {"path": "q.py", "content": "Q"}]}), "z.py") == "Q"
    assert unwrap(json.dumps({"path": "z.py", "content": "Z"}), "z.py") == "Z"
    assert unwrap(json.dumps({"path": "z.py", "content": 3}), "z.py") is None
    assert unwrap("not json", "z.py") is None and unwrap(json.dumps({"files": [{"path": "z.py"}]}), "z.py") is None


def test_json_protocol_fences_and_value_recovery():
    strip = DeveloperAgent._strip_json_protocol_fences
    assert strip("```json\n    {\"a\": 1}\n```\n") == '    {"a": 1}'
    assert strip("```\n{}") == "{}"
    assert strip("pre\n```json\n{\"short\": 1}\n```\nmid\n```json\n{\"much longer\": 1}\n```\npost") == '{"much longer": 1}'
    assert strip("  plain text  \n") == "  plain text  \n"
    extract = DeveloperAgent._extract_json_value
    assert extract('here: [1, {"a": 2}] done') == [1, {"a": 2}]
    assert extract('x {"a": [1]} y') == {"a": [1]}
    with pytest.raises(json.JSONDecodeError):
        extract("nothing")


def test_file_entry_normalization():
    norm = DeveloperAgent._normalize_file_entries
    assert norm(["a.py", "b.py"]) == [{"filepath": "a.py", "content": None, "edits": None},
                                      {"filepath": "b.py", "content": None, "edits": None}]
    assert norm({"empty": [], "files": [{"path": "a.py", "content": "", "edits": []}, {"content": "x"}]}) == \
        [{"filepath": "a.py", "content": None, "edits": None}]
    assert norm([{"x": 1}]) is None and norm(["a", 1]) is None and norm({"files": []}) is None and norm(3) is None


def test_the_unparseable_json_failure_signal():
    from kriya.agents.agent import _is_unparseable_json
    assert _is_unparseable_json("not json") is True
    assert _is_unparseable_json("[1, 2]") is True
    assert _is_unparseable_json('```json\n{"a": 1}\n```') is False


@pytest.mark.asyncio
async def test_reused_entries_are_passed_through_without_a_call_and_their_payload_is_parsed():
    agent, _l, recorder = _developer([], LEGACY)
    entries = [{"filepath": "a.py", "content": "```python\nx = 1\n```", "edits": None},
               {"filepath": "b.py", "content": None, "edits": [{"search": "a", "replace": "b"}]},
               {"filepath": "c.py", "content": "pre\n```\nx\n```\npost", "edits": None}]
    out = await agent._fill_missing_content(entries, "t", "d", "c", None, None, None, None)
    assert recorder.calls == []
    assert out[0] == {"filepath": "a.py", "content": "x = 1", "edits": []}
    assert out[1] == {"filepath": "b.py", "content": None, "edits": [{"search": "a", "replace": "b"}]}
    assert out[2]["content"] is None and out[2]["protocol_reason_code"] == "AMBIGUOUS_FILE_RESPONSE_PROTOCOL"


@pytest.mark.asyncio
async def test_sibling_contents_follow_the_budget_and_name_the_omitted_ones():
    big = "class A { " + "x " * 4000 + "}"
    agent, _l, recorder = _developer([big, "class B {}", "class C {}"], LEGACY)
    await _fill(agent, ["src/A.java", "src/B.java", "src/C.java"], sibling_content_budget=50)
    third = recorder.calls[2]["prompt"]
    assert "=== Already-Written File This Batch (for cross-file consistency" in third and "class B {}" in third
    assert "Additional Already-Written Files This Batch (contents omitted - cross-file reference budget reached; " \
           "filenames only): src/A.java ===" in third and big not in third
    first = recorder.calls[0]["prompt"]
    assert "Not Yet Written (context only - do NOT output their content here) ===\nsrc/B.java, src/C.java" in first
    agent, _l, recorder = _developer([big, "class B {}"], LEGACY)
    await _fill(agent, ["src/A.java", "src/B.java"])                      # default budget 3000 tokens
    assert big in recorder.calls[1]["prompt"]


@pytest.mark.asyncio
async def test_the_operation_and_capability_decide_the_requested_mode():
    async def mode(files, **kw):
        agent, _l, recorder = _developer(["FIX ANALYSIS: a\nNO CHANGE NEEDED: b"] * len(files), LEGACY)
        await _fill(agent, files, **kw)
        return [call["system"].split("\n")[0] for call in recorder.calls]

    full = "You are the Kriya Developer Agent. MODE: REPAIR_WITH_FULL_FILE."
    repair = "You are the Kriya Developer Agent. MODE: REPAIR."
    create = "You are the Kriya Developer Agent. MODE: CREATE_FULL_FILE."
    assert await mode(["a.py", "b.py"], default_operation="repair_with_full_file",
                      operation_by_file={"b.py": "create_full_file"}) == [full, create]
    assert await mode(["a.py"], default_operation="repair_with_patch",
                      generation_protocol=_Protocol("full_file_text")) == [full]
    assert await mode(["a.py"], default_operation="repair_with_patch",
                      generation_protocol=_Protocol("full_file")) == [full]
    assert await mode(["a.py"], default_operation="repair_with_patch",
                      generation_protocol=_Protocol("small_native_tools")) == [repair]
    assert await mode(["a.py"], default_operation="repair_with_full_file",
                      edit_operations={"a.py": ["anchored_edit"]}) == [repair]
    assert await mode(["a.py"], default_operation="repair_with_patch",
                      edit_operations={"a.py": ["full_file_replacement"]}) == [full]
    assert await mode(["a.py"], default_operation="repair_with_patch",
                      edit_operations={"a.py": ["full_file_replacement", "anchored_edit"]}) == [repair]


@pytest.mark.asyncio
async def test_the_anchored_preference_on_a_retry_needs_a_locator_or_current_content():
    async def system(**kw):
        agent, _l, recorder = _developer(["FIX ANALYSIS: a\nNO CHANGE NEEDED: b"], LEGACY)
        await _fill(agent, ["src/Util.java"], prior_error_context="boom", **kw)
        return recorder.calls[0]["system"], recorder.calls[0]["prompt"]

    anchored, plain = '"SEARCH:"', "Then either:"
    s, p = await system(error_source_context={"src/Util.java": "LOCATED LINE"})
    assert anchored in s and "LOCATED LINE" in p
    s, p = await system(error_source_context={"src/Other.java": "LOCATED LINE"})
    assert plain in s and "LOCATED LINE" not in p
    s, _p = await system(files_with_current_content=["src/Util.java"])
    assert anchored in s
    s, _p = await system(files_with_current_content=["src/Other.java"])
    assert plain in s
    s, _p = await system(edit_operations={"src/Util.java": ["anchored_edit", "full_file_replacement"]})
    assert plain in s                                         # edit operations alone decide; no patch requested
    agent, _l, recorder = _developer(["x"], LEGACY)
    await _fill(agent, ["src/Util.java"], error_source_context={"src/Util.java": "LOCATED LINE"})
    assert "LOCATED LINE" not in recorder.calls[0]["prompt"]   # never without a prior error


@pytest.mark.asyncio
async def test_completion_options_follow_the_mode_capability_and_retry():
    agent, _l, recorder = _developer(["FIX ANALYSIS: a\nNO CHANGE NEEDED: b", "x"], LEGACY)
    stream = lambda _t: None  # noqa: E731 - a stream callback double
    await _fill(agent, ["src/A.java", "src/B.java"], prior_error_context="boom", implicated_files=["src/A.java"],
                retry_temperature=0.05, stream=stream, model_override="m1",
                expected_output_by_file={"src/A.java": "EXP-A", "src/B.java": "EXP-B"},
                error_source_context={"src/A.java": "L"}, generation_protocol=_Protocol(streaming=False))
    a, b = recorder.calls
    assert (a["temperature_override"], b["temperature_override"]) == (0.05, None)
    assert (a["expected_output"], b["expected_output"]) == (None, "EXP-B")      # anchored A: no expectation
    assert a["stream_callback"] is None and a["model_override"] == "m1" and a["json_mode"] is False
    agent, _l, recorder = _developer(["x"], LEGACY)
    await _fill(agent, ["src/A.java"], stream=stream, generation_protocol=_Protocol(streaming=True))
    assert recorder.calls[0]["stream_callback"] is stream
    agent, _l, recorder = _developer(["x"], LEGACY)
    await _fill(agent, ["src/A.java"], stream=stream)
    assert recorder.calls[0]["stream_callback"] is stream


@pytest.mark.asyncio
async def test_the_stream_callback_announces_each_generated_file():
    seen = []
    agent, _l, _r = _developer(["x"], LEGACY)
    await _fill(agent, ["src/A.java"], stream=seen.append)
    assert seen == ["\n[Implementing file: src/A.java]"]


class _Fit:
    def __init__(self, details=None, use_reduced=False):
        self.details, self.use_reduced, self.calls = details, use_reduced, []

    def fit(self, system, prompt, *optional, output_tokens=None):
        self.calls.append({"optional": optional, "output_tokens": output_tokens})
        if self.use_reduced and optional:
            section = optional[0]
            return prompt.replace(section.text, section.rebuild(10_000)), self.details
        return "FITTED:" + prompt, self.details


@pytest.mark.asyncio
async def test_a_request_fit_shrinks_only_the_siblings_and_records_the_reduction():
    agent, llm, recorder = _developer(["class A {}", "class B {}"], LEGACY)
    llm.budget_expansions = []
    fit = _Fit(details={"removed_tokens": 5}, use_reduced=True)
    await _fill(agent, ["src/A.java", "src/B.java"], request_fit=fit, model_override="m",
                expected_output_by_file={"src/B.java": type("E", (), {"tokens": 321})()})
    assert [c["optional"][0].kind for c in fit.calls] == ["siblings", "siblings"]
    assert fit.calls[0]["optional"][0].text.startswith("=== Other Files In This Batch, Not Yet Written")
    assert fit.calls[1]["optional"][0].text.startswith("=== Already-Written File This Batch")
    assert fit.calls[1]["output_tokens"] == 321 and fit.calls[0]["output_tokens"] is None
    assert "Already-Written Files This Batch (contents omitted - context budget; filenames only): src/A.java" in \
        recorder.calls[1]["prompt"]
    assert llm.budget_expansions == [
        {"event_kind": "model.optional_context_reduced", "model": "m", "reason": "request_fit", "file": f,
         "removed_tokens": 5} for f in ("src/A.java", "src/B.java")]
    agent, llm, recorder = _developer(["x"], LEGACY)
    llm.budget_expansions = []
    await _fill(agent, ["src/A.java"], request_fit=_Fit(details=None))
    assert recorder.calls[0]["prompt"].startswith("FITTED:") and llm.budget_expansions == []


def _refusal(output=False):
    cls = OutputBudgetUnsatisfiableError if output else ContextBudgetUnsatisfiableError
    refusal = cls.__new__(cls)
    Exception.__init__(refusal, "does not fit")
    refusal.decision = type("D", (), {"prompt_tokens": 9000, "context_window": 4096})()
    return refusal


@pytest.mark.asyncio
async def test_a_context_refusal_is_retried_once_with_sibling_names_only():
    agent, llm, recorder = _developer(["class A {}", _refusal(), "class B {}"], LEGACY)
    llm.budget_expansions = []
    out = await _fill(agent, ["src/A.java", "src/B.java"], model_override="m")
    assert out[1]["content"] == "class B {}" and len(recorder.calls) == 3
    assert "class A {}" in recorder.calls[1]["prompt"] and "class A {}" not in recorder.calls[2]["prompt"]
    assert "(contents omitted - context budget; filenames only): src/A.java" in recorder.calls[2]["prompt"]
    assert llm.budget_expansions == [{"event_kind": "model.optional_context_reduced", "model": "m",
                                      "reason": "sibling_contents_omitted", "file": "src/B.java",
                                      "required_prompt_tokens": 9000, "selected_context_window": 4096}]


@pytest.mark.asyncio
async def test_an_output_refusal_or_a_request_without_siblings_is_raised_naming_the_file():
    agent, _l, recorder = _developer(["class A {}", _refusal(output=True)], LEGACY)
    with pytest.raises(OutputBudgetUnsatisfiableError) as raised:
        await _fill(agent, ["src/A.java", "src/B.java"])
    assert (raised.value.filepath, raised.value.anchored_edit, len(recorder.calls)) == ("src/B.java", False, 2)
    agent, _l, recorder = _developer([_refusal()], LEGACY)
    with pytest.raises(ContextBudgetUnsatisfiableError) as raised:
        await _fill(agent, ["src/A.java"], prior_error_context="boom", error_source_context={"src/A.java": "L"})
    assert (raised.value.filepath, raised.value.anchored_edit, len(recorder.calls)) == ("src/A.java", True, 1)
    agent, _l, recorder = _developer(["class A {}", _refusal()], LEGACY)
    with pytest.raises(ContextBudgetUnsatisfiableError):        # siblings already removed by the fit: not retried
        await _fill(agent, ["src/A.java", "src/B.java"], request_fit=_Fit(use_reduced=True))
    assert len(recorder.calls) == 2


@pytest.mark.asyncio
async def test_the_response_protocol_decides_the_parser_and_the_patch_permission():
    edit = "FIX ANALYSIS: the cause\nSEARCH:\nold\nREPLACE:\nnew\n"
    agent, _l, _r = _developer([edit], LEGACY)
    out = await _fill(agent, ["src/A.java"], prior_error_context="boom", error_source_context={"src/A.java": "L"})
    assert out == [{"filepath": "src/A.java", "content": None, "edits": [{"search": "old", "replace": "new"}],
                    "analysis": "the cause"}]
    agent, _l, _r = _developer([edit], LEGACY)                   # a patch where none is allowed is refused
    out = await _fill(agent, ["src/A.java"], prior_error_context="boom")
    assert out[0]["content"] is None and "edits" not in out[0] and out[0]["protocol_reason_code"]
    agent, _l, _r = _developer(["FIX ANALYSIS: why\nFILE CONTENT:\n" +
                                json.dumps({"files": [{"path": "src/A.java", "content": "class A {}"}]})], LEGACY)
    out = await _fill(agent, ["src/A.java"], prior_error_context="boom")
    assert out == [{"filepath": "src/A.java", "content": "class A {}", "analysis": "why"}]
    s_edit = sentinel("src/A.java", analysis="the cause", edits=[("old", "new")])
    agent, _l, _r = _developer([s_edit])
    out = await _fill(agent, ["src/A.java"], prior_error_context="boom", error_source_context={"src/A.java": "L"})
    assert out == [{"filepath": "src/A.java", "content": None, "edits": [{"search": "old", "replace": "new"}],
                    "analysis": "the cause"}]
    agent, _l, _r = _developer([s_edit])
    out = await _fill(agent, ["src/A.java"], prior_error_context="boom")
    assert out[0]["content"] is None and "edits" not in out[0] and out[0]["protocol_reason_code"]
    agent, _l, _r = _developer([sentinel("src/A.java", content="class A {}\n")])
    assert await _fill(agent, ["src/A.java"]) == [{"filepath": "src/A.java", "content": "class A {}\n"}]
    agent, _l, _r = _developer(["FIX ANALYSIS: ignored on a create\nclass A {}"], LEGACY)
    out = await _fill(agent, ["src/A.java"])
    assert out == [{"filepath": "src/A.java", "content": "FIX ANALYSIS: ignored on a create\nclass A {}"}]


@pytest.mark.asyncio
async def test_the_parse_evidence_names_the_protocol_and_the_source():
    from unittest.mock import patch
    recorded = []
    with patch("kriya.core.attempt_evidence.scope.record_developer_parse",
               side_effect=lambda parsed, filepath, **kw: recorded.append((filepath, kw))):
        agent, _l, _r = _developer(["x"], LEGACY)
        await _fill(agent, ["src/A.java"])
        await agent._fill_missing_content([{"filepath": "b.py", "content": "y", "edits": None}], "t", "d", "c",
                                          None, None, None, None)
    assert recorded == [("src/A.java", {"selected_protocol": "legacy_strict", "source": "developer_response"}),
                        ("b.py", {"selected_protocol": None, "source": "reused_batch_entry"})]


@pytest.mark.asyncio
async def test_a_truncated_completion_is_never_a_file_or_an_edit():
    truncated = CompletionResult(status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length", max_tokens=64)
    agent, llm, _r = _developer(["class A {"], LEGACY)
    llm.last_completion = truncated
    out = await _fill(agent, ["src/A.java"])
    assert out[0]["content"] is None and out[0]["protocol_reason_code"] == OUTPUT_TRUNCATED
    assert out[0]["protocol_error"].startswith(f"{OUTPUT_TRUNCATED}: the model stopped at its output budget")
    agent, llm, _r = _developer(["FIX ANALYSIS: c\nSEARCH:\nold\nREPLACE:\nnew\n"], LEGACY)
    llm.last_completion = truncated
    out = await _fill(agent, ["src/A.java"], prior_error_context="boom", error_source_context={"src/A.java": "L"})
    assert out == [{"filepath": "src/A.java", "content": None, "analysis": "c",
                    "protocol_error": out[0]["protocol_error"], "protocol_reason_code": OUTPUT_TRUNCATED}]
    agent, llm, _r = _developer(["class A {}"], LEGACY)
    llm.last_completion = CompletionResult(status=CompletionStatus.OK, finish_reason="stop")
    assert await _fill(agent, ["src/A.java"]) == [{"filepath": "src/A.java", "content": "class A {}"}]


# ---------------------------------------------------------------------------------------------------------------
# Developer: step 1 (which files), run_generation routing and the single-stage fallback
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_one_sources_contract_fallback_none_and_records_a_contract_miss():
    agent, llm, recorder = _developer(['{"files": ["a.py"]}'], LEGACY)
    llm.role_metrics = _Metrics()
    assert await agent._resolve_step1_file_list("t", "d", json_mode=False) == (
        [{"filepath": "a.py", "content": None, "edits": None}], "contract")
    assert llm.role_metrics.schema_failures == [] and recorder.calls[0]["json_mode"] is False
    agent, llm, recorder = _developer(['["a.py", "b.py"]'], LEGACY)
    llm.role_metrics = _Metrics()
    entries, source = await agent._resolve_step1_file_list("t", "d", model_override="m2", json_mode=True)
    assert source == "fallback" and [e["filepath"] for e in entries] == ["a.py", "b.py"]
    assert llm.role_metrics.schema_failures == ["m2"] and recorder.calls[0]["json_mode"] is True
    agent, llm, _r = _developer(["no json at all"], LEGACY)
    assert await agent._resolve_step1_file_list("t", "d", json_mode=True) == (None, "none")
    agent, llm, _r = _developer(['{"files": []}'], LEGACY)
    assert await agent._resolve_step1_file_list("t", "d", json_mode=True) == (None, "none")


@pytest.mark.asyncio
async def test_step_one_takes_json_mode_from_the_models_capability_when_unset():
    from unittest.mock import patch
    seen = []

    def protocol_for(_config, model):
        seen.append(model)
        return _Protocol(json_mode=False)

    with patch("kriya.core.model_capabilities.generation_protocol_for_model", side_effect=protocol_for):
        agent, llm, recorder = _developer(['{"files": ["a.py"]}'] * 2, LEGACY)
        await agent._resolve_step1_file_list("t", "d", model_override="fb-model")
        await agent._resolve_step1_file_list("t", "d")
    assert seen == ["fb-model", llm.model] and [c["json_mode"] for c in recorder.calls] == [False, False]
    assert _sha(recorder.calls[0]["system"]) + ":" + _sha(recorder.calls[0]["prompt"]) == STEP1_REQUEST_DIGEST


STEP1_REQUEST_DIGEST = '215f3cddcab89bf8:af45e412658e7c7f'  # sha256[:16] at 886a46d


@pytest.mark.asyncio
async def test_run_generation_skips_step_one_for_known_targets_and_uses_the_capability_profile():
    from unittest.mock import patch
    with patch("kriya.core.model_capabilities.generation_protocol_for_model",
               return_value=_Protocol(json_mode=False, streaming=False)):
        agent, _l, recorder = _developer(["class A {}"], LEGACY)
        out = await agent.run_generation("t", "d", "c", known_target_files=["src/A.java"], stream_callback=print)
        assert out == [{"filepath": "src/A.java", "content": "class A {}"}]
        assert len(recorder.calls) == 1 and recorder.calls[0]["stream_callback"] is None
        agent, _l, recorder = _developer(['{"files": ["src/A.java"]}', "class A {}"], LEGACY)
        out = await agent.run_generation("t", "d", "c")
        assert out == [{"filepath": "src/A.java", "content": "class A {}"}]
        assert recorder.calls[0]["json_mode"] is False                     # step 1 received the profile's json_mode


@pytest.mark.asyncio
async def test_a_budget_refusal_in_step_one_is_raised_not_retried_single_stage():
    agent, _l, recorder = _developer([_refusal()], LEGACY)
    with pytest.raises(ContextBudgetUnsatisfiableError):
        await agent.run_generation("t", "d", "c")
    assert len(recorder.calls) == 1
    agent, _l, recorder = _developer([ConnectionError("down"), '[{"path": "a.py", "content": "x"}]'], LEGACY)
    assert await agent.run_generation("t", "d", "c") == [{"path": "a.py", "content": "x"}]   # a list: kept as-is


@pytest.mark.asyncio
async def test_the_single_stage_fallback_prompt_and_result_shapes():
    agent, _l, recorder = _developer(["nothing", '{"files": [{"path": "a.py", "content": "x"}]}'], LEGACY)
    out = await agent.run_generation("t", "d", "c", prior_error_context="E1", extra_fix_instruction="NUDGE",
                                     retry_temperature=0.07)
    assert out == [{"path": "a.py", "content": "x", "filepath": "a.py"}]
    single = recorder.calls[1]
    assert "=== Prior Attempt Failed - Fix This Error ===\nE1\n" in single["prompt"] and "\nNUDGE\n\n" in single["prompt"]
    assert single["temperature_override"] == 0.07 and single["system"] == agent.system_prompt
    assert _sha(single["prompt"]) == SINGLE_STAGE_PROMPT_DIGEST
    agent, _l, recorder = _developer(["nothing", '{"files": [{"filepath": "a.py", "path": "z", "content": "x"}]}'],
                                     LEGACY)
    assert await agent.run_generation("t", "d", "c", extra_fix_instruction="NUDGE") == \
        [{"filepath": "a.py", "path": "z", "content": "x"}]
    assert "Prior Attempt Failed" not in recorder.calls[1]["prompt"] and "NUDGE" not in recorder.calls[1]["prompt"]
    agent, _l, _r = _developer(["nothing", '{"a": 1}'], LEGACY)
    assert await agent.run_generation("t", "d", "c") == [{"a": 1}]
    agent, _l, _r = _developer(["nothing", '[{"filepath": "a.py"}, 3, "x"]'], LEGACY)
    assert await agent.run_generation("t", "d", "c") == [{"filepath": "a.py"}]
    agent, _l, _r = _developer(["nothing", "still nothing"], LEGACY)
    with pytest.raises(ValueError, match="Failed to parse Developer Agent response as JSON"):
        await agent.run_generation("t", "d", "c")


@pytest.mark.asyncio
async def test_a_truncated_single_stage_list_is_refused():
    agent, llm, _r = _developer(["nothing", '[{"filepath": "a.py", "content": "x"}'], LEGACY)
    llm.last_completion = CompletionResult(status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length",
                                           max_tokens=99)
    with pytest.raises(ValueError, match=f"^{OUTPUT_TRUNCATED}: .*while writing the file list"):
        await agent.run_generation("t", "d", "c")


@pytest.mark.asyncio
async def test_the_single_stage_request_is_fitted_and_streams_by_capability():
    from unittest.mock import patch
    fit = _Fit()
    seen = []
    with patch("kriya.core.model_capabilities.generation_protocol_for_model", return_value=_Protocol(streaming=True)):
        agent, _l, recorder = _developer(["nothing", "[]"], LEGACY)
        await agent.run_generation("t", "d", "c", request_fit=fit, stream_callback=seen.append)
    assert recorder.calls[1]["prompt"].startswith("FITTED:") and recorder.calls[1]["stream_callback"] == seen.append
    with patch("kriya.core.model_capabilities.generation_protocol_for_model", return_value=_Protocol(streaming=False)):
        agent, _l, recorder = _developer(["nothing", "[]"], LEGACY)
        await agent.run_generation("t", "d", "c", stream_callback=seen.append)
    assert recorder.calls[1]["stream_callback"] is None


SINGLE_STAGE_PROMPT_DIGEST = '600cb3d222cf3dea'  # sha256[:16] at 886a46d

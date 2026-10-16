"""PLAN-GLOBAL-INVARIANT-PROJECTION-001: a multi-subtask plan whose subtasks do not ALL reference a declared global invariant is rejected (SUBTASK_GLOBAL_INVARIANTS_MISSING), but the Planner was
never told that rule, and the bounded repair feedback named the subtasks without saying what a valid repair is.

Live evidence (scenario 04, run 20261016T085640-13e8ec80): the Planner declared ONE invariant, referenced it from 2 of 7 subtasks, and returned the byte-identical plan for the first answer and both
repairs. The recorded answer is replayed here through the SAME parse -> build -> normalise -> ``validate_plan`` chain ``WorkflowController`` runs in enforce mode. No model, no network.

Decision log (handover/SPRING-IGNITE-PROTOCOL/LIVE-FAILURE-ANALYSIS.md): the validator is CORRECT and unchanged; the fix is the contract sentence (one shared constant in every Planner contract) and typed repair
feedback. Nothing here names a technology, a scenario or a goal text.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from kriya.agents.agent import PlannerAgent
from kriya.agents.contracts import GLOBAL_INVARIANT_PROJECTION_RULE, parse_planner_structured_output
from kriya.workflow.acceptance import goal_requires_runtime_behavior
from kriya.workflow.plan_normalization import (
    coalesce_same_file_owners,
    derive_verification_contracts,
    redirect_immutable_test_edits,
    scope_verification_to_requirements,
)
from kriya.workflow.plan_schema import build_engineering_plan_from_planner_output
from kriya.workflow.plan_validation import canonicalize_planned_file_actions, validate_plan
from kriya.workflow.planner_repair import (
    build_structured_plan_repair_prompt,
    format_missing_invariant_projection_feedback,
    missing_invariant_projection_facts,
)
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_controller import AUTHORITATIVE_PLANNER_SYSTEM_PROMPT, build_authoritative_planner_request

DATA = json.loads((Path(__file__).parent / "fixtures" / "plan_global_invariant_projection_001.json").read_text(encoding="utf-8"))
RESPONSE: str = DATA["planner_response"]
GOAL: str = DATA["goal"]
CODE = "SUBTASK_GLOBAL_INVARIANTS_MISSING"
TECHNOLOGY_WORDS = re.compile(r"ignite|spring|maven|java|junit|protocol|qpid|python|node", re.IGNORECASE)


def _validate_plan_dict(plan_dict: Dict[str, Any], workspace: str) -> Tuple[bool, List[str], List[str]]:
    """The controller's chain, in its order, for one Planner answer."""
    output, issue = parse_planner_structured_output(json.dumps(plan_dict))
    assert output is not None, issue
    raw = build_engineering_plan_from_planner_output(output, plan_id="repro", kind=ChangeKind.MILESTONE)
    plan, _ = canonicalize_planned_file_actions(raw, workspace)
    plan, _ = coalesce_same_file_owners(plan)
    plan, _ = redirect_immutable_test_edits(plan, (), lambda rel: os.path.exists(os.path.join(workspace, rel)))
    plan, _ = derive_verification_contracts(plan)
    plan, _ = scope_verification_to_requirements(plan, goal_requires_runtime_behavior(GOAL))
    result = asyncio.run(validate_plan(
        plan, workspace_path=workspace, available_tool_names=None, require_model_planned_files=True, require_semantic_contracts=True,
        runtime_verification_required=goal_requires_runtime_behavior(GOAL)))
    return result.valid, list(result.reason_codes), list(result.errors)


@pytest.fixture
def recorded() -> Dict[str, Any]:
    return json.loads(RESPONSE)


def _repair_prompt(plan_text: str, codes: List[str], errors: List[str]) -> str:
    return build_structured_plan_repair_prompt(GOAL, plan_text, errors, codes, 1, route_kind=ChangeKind.MILESTONE)


# ---- 1. the recorded live failure, characterised ----

def test_the_recorded_live_answer_is_rejected_with_exactly_the_live_code_and_error(tmp_path: Path, recorded) -> None:
    valid, codes, errors = _validate_plan_dict(recorded, str(tmp_path))
    assert valid is False
    assert codes == DATA["live_reason_codes"] == [CODE]
    assert errors == DATA["live_errors"]


def test_the_recorded_plan_shape_five_of_seven_subtasks_have_no_invariant_and_one_invariant_is_declared(recorded) -> None:
    assert [g["id"] for g in recorded["global_invariants"]] == ["gi1"]
    empty = [s["id"] for s in recorded["subtasks"] if not s["relevant_global_invariant_ids"]]
    assert empty == ["s1", "s2", "s3", "s4", "s6"] and len(recorded["subtasks"]) == 7


def test_the_validator_is_correct_and_unchanged_it_still_refuses_an_empty_projection_in_a_multi_subtask_plan(tmp_path: Path, recorded) -> None:
    # Characterization of the rule the fix must NOT weaken: empty projection is refused with >1 subtask; a single-subtask plan is not subject to it.
    single = copy.deepcopy(recorded)
    single["subtasks"] = [single["subtasks"][4]]
    single["subtasks"][0]["depends_on"], single["subtasks"][0]["requires"] = [], []
    single["subtasks"][0]["relevant_global_invariant_ids"] = []
    _valid, codes, _errors = _validate_plan_dict(single, str(tmp_path))
    assert CODE not in codes  # one subtask: the rule does not apply
    multi = copy.deepcopy(recorded)
    assert _validate_plan_dict(multi, str(tmp_path))[1] == [CODE]


def test_discrimination_giving_every_subtask_a_declared_id_makes_the_recorded_plan_valid(tmp_path: Path, recorded) -> None:
    repaired = copy.deepcopy(recorded)
    for subtask in repaired["subtasks"]:
        if not subtask["relevant_global_invariant_ids"]:
            subtask["relevant_global_invariant_ids"] = ["gi1"]
    valid, codes, errors = _validate_plan_dict(repaired, str(tmp_path))
    assert (valid, codes, errors) == (True, [], [])  # the ONLY defect of the recorded plan is the empty projection


def test_an_undeclared_id_is_still_refused_the_repair_may_not_invent_one(tmp_path: Path, recorded) -> None:
    repaired = copy.deepcopy(recorded)
    repaired["subtasks"][0]["relevant_global_invariant_ids"] = ["gi99"]
    _valid, codes, _errors = _validate_plan_dict(repaired, str(tmp_path))
    assert "UNKNOWN_GLOBAL_INVARIANT" in codes


# ---- 2. the repair feedback, before and after ----

def test_the_recorded_first_repair_prompt_carried_no_instruction_about_this_rule() -> None:
    old = DATA["first_repair_prompt_as_recorded"]
    assert "receive no relevant_global_invariants" in old  # it named the subtasks...
    assert "empty list" not in old and "at least one declared" not in old and "SUBTASK_GLOBAL_INVARIANTS_MISSING:" not in old  # ...and said nothing about what a valid repair is


def test_the_facts_are_read_from_the_previous_plan_not_from_error_text() -> None:
    facts = missing_invariant_projection_facts(RESPONSE)
    assert facts["subtask_ids"] == ["s1", "s2", "s3", "s4", "s6"]
    assert facts["declared"] == [{"id": "gi1", "statement": "All application resources including Spring and Ignite must be properly closed to ensure normal application exit"}]


def test_the_repair_prompt_now_states_the_rule_names_the_subtasks_and_lists_the_valid_repairs() -> None:
    prompt = _repair_prompt(RESPONSE, [CODE], DATA["live_errors"])
    assert "SUBTASK_GLOBAL_INVARIANTS_MISSING:" in prompt
    assert "s1, s2, s3, s4, s6" in prompt
    assert "gi1" in prompt
    assert "at least one declared global invariant id" in prompt and "empty list" in prompt
    assert "1. " in prompt.split("SUBTASK_GLOBAL_INVARIANTS_MISSING:")[1] and "2. " in prompt.split("SUBTASK_GLOBAL_INVARIANTS_MISSING:")[1]
    assert "Do not invent" in prompt.split("SUBTASK_GLOBAL_INVARIANTS_MISSING:")[1]
    assert "do not change anything else" in prompt.split("SUBTASK_GLOBAL_INVARIANTS_MISSING:")[1].lower()
    assert "leaving the list empty is not a repair" in prompt.lower()


def test_negative_control_no_block_when_the_reason_code_is_absent_or_nothing_is_missing(recorded) -> None:
    other = _repair_prompt(RESPONSE, ["SUBTASK_SEMANTIC_CONTRACT_MISSING"], ["x"])
    assert "SUBTASK_GLOBAL_INVARIANTS_MISSING:" not in other
    fixed = copy.deepcopy(recorded)
    for subtask in fixed["subtasks"]:
        subtask["relevant_global_invariant_ids"] = ["gi1"]
    assert missing_invariant_projection_facts(json.dumps(fixed))["subtask_ids"] == []
    assert format_missing_invariant_projection_feedback(missing_invariant_projection_facts(json.dumps(fixed))) == ""
    assert missing_invariant_projection_facts("not json at all") == {"subtask_ids": [], "declared": []}
    assert missing_invariant_projection_facts("") == {"subtask_ids": [], "declared": []}


def test_a_single_subtask_plan_has_no_missing_projection_to_report(recorded) -> None:
    single = copy.deepcopy(recorded)
    single["subtasks"] = [single["subtasks"][4]]
    single["subtasks"][0]["relevant_global_invariant_ids"] = []
    assert missing_invariant_projection_facts(json.dumps(single))["subtask_ids"] == []  # the rule needs more than one subtask; no feedback for a case the validator accepts


def test_when_no_invariant_is_declared_the_feedback_says_so_and_asks_for_a_request_stated_constraint(recorded) -> None:
    bare = copy.deepcopy(recorded)
    bare["global_invariants"] = []
    for subtask in bare["subtasks"]:
        subtask["relevant_global_invariant_ids"] = []
    prompt = _repair_prompt(json.dumps(bare), [CODE], ["x"])
    block = prompt.split("SUBTASK_GLOBAL_INVARIANTS_MISSING:")[1]
    assert "no global invariant is declared" in block
    assert "the original request states" in block


def test_the_feedback_block_is_generic_it_names_no_technology_scenario_or_goal_text() -> None:
    template_lines = format_missing_invariant_projection_feedback({"subtask_ids": ["sX"], "declared": [{"id": "giX", "statement": "stmt"}]})
    assert TECHNOLOGY_WORDS.search(template_lines) is None
    assert GOAL.split(".")[0] not in template_lines


# ---- 3. the contract, wherever invariants are taught ----

def test_the_rule_is_one_concise_generic_sentence_present_in_every_planner_contract() -> None:
    assert len(GLOBAL_INVARIANT_PROJECTION_RULE) < 700
    assert TECHNOLOGY_WORDS.search(GLOBAL_INVARIANT_PROJECTION_RULE) is None
    assert GLOBAL_INVARIANT_PROJECTION_RULE in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert AUTHORITATIVE_PLANNER_SYSTEM_PROMPT.count(GLOBAL_INVARIANT_PROJECTION_RULE) == 1
    request = build_authoritative_planner_request("Make a thing.")
    assert GLOBAL_INVARIANT_PROJECTION_RULE in request and request.count(GLOBAL_INVARIANT_PROJECTION_RULE) == 1
    legacy = PlannerAgent.__new__(PlannerAgent).system_prompt
    assert GLOBAL_INVARIANT_PROJECTION_RULE in legacy and legacy.count(GLOBAL_INVARIANT_PROJECTION_RULE) == 1


def test_the_rule_states_the_requirement_the_validator_enforces() -> None:
    rule = GLOBAL_INVARIANT_PROJECTION_RULE
    assert "more than one subtask" in rule and "at least one declared global invariant id" in rule and "empty list" in rule
    assert "relevant_global_invariant_ids" in rule
    assert "never invent" in rule.lower() or "do not invent" in rule.lower()


def test_the_new_text_does_not_contradict_the_existing_unknown_invariant_guidance() -> None:
    # Existing guidance: a subtask relevant to only PART of a compound invariant references the whole id; never invent an undeclared id.
    assert "Never reference an id that isn't declared" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT and "a subtask relevant to only PART of a compound invariant" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "split" not in GLOBAL_INVARIANT_PROJECTION_RULE.lower()  # no per-clause splitting is invited
